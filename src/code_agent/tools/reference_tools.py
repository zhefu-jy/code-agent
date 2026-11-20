"""结合 W8 符号索引与 AST 扫描的候选引用查找工具（带可信度分级）。"""

import ast
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Literal

from code_agent.analyzer.symbol_extractor import SymbolInfo
from code_agent.analyzer.symbol_index import SymbolIndex, build_symbol_index
from code_agent.tools.base import ToolResult, tool_error

DEFAULT_MAX_RESULTS = 30
DEFAULT_MAX_CHARS = 3000
DEFAULT_MAX_LINE_CHARS = 200
_TOOL_NAME = "find_references"
_DYNAMIC_ATTR_FUNCS = frozenset({"getattr", "setattr", "hasattr", "delattr"})


class ReferenceConfidence(str, Enum):
    """候选引用的静态可信度分级枚举。"""

    SAME_FILE_CALL = "SAME_FILE_CALL"
    EXPLICIT_IMPORT_CALL = "EXPLICIT_IMPORT_CALL"
    ATTRIBUTE_ACCESS = "ATTRIBUTE_ACCESS"
    UNRESOLVED = "UNRESOLVED"


_CONFIDENCE_RANK: dict[ReferenceConfidence, int] = {
    ReferenceConfidence.SAME_FILE_CALL: 0,
    ReferenceConfidence.EXPLICIT_IMPORT_CALL: 1,
    ReferenceConfidence.ATTRIBUTE_ACCESS: 2,
    ReferenceConfidence.UNRESOLVED: 3,
}


@dataclass(frozen=True)
class ReferenceItem:
    """
    单条候选引用或未解析风险线索。

    Attributes:
        file_path: 引用所在文件相对于工程根目录的 POSIX 路径。
        line_number: 物理行号（1-based）。
        col_offset: 列偏移量（0-based）。
        confidence: 可信度分级（同文件 / 显式导入 / 属性访问 / 未解析）。
        scope: 所属代码范围（"source" 业务源码 或 "test" 测试代码）。
        target_fqn: 关联的目标符号全限定名。
        line_text: 所在源码行预览（超长按 max_line_chars 截断）。
        detail: 命中依据或未解析原因说明。
        line_truncated: 所在行预览是否被截断。
    """

    file_path: str
    line_number: int
    col_offset: int
    confidence: ReferenceConfidence
    scope: Literal["source", "test"]
    target_fqn: str
    line_text: str
    detail: str
    line_truncated: bool = False


@dataclass(frozen=True)
class FindReferencesData:
    """
    find_references 工具成功执行后的载荷数据。

    Attributes:
        query: 原始查询符号名或全限定名。
        target_definitions: 在静态符号表中精确匹配到的目标定义列表。
        references: 本页返回的候选引用条目元组。
        formatted_preview: 便于模型与人眼阅读的格式化候选清单。
        confidence_counts: 全部命中中各可信度等级的统计计数。
        scanned_files: 本次成功解析 AST 的文件数。
        skipped_files: 因读取或语法解析失败而跳过的文件数。
    """

    query: str
    target_definitions: tuple[SymbolInfo, ...]
    references: tuple[ReferenceItem, ...]
    formatted_preview: str
    confidence_counts: dict[str, int]
    scanned_files: int
    skipped_files: int


@dataclass(frozen=True)
class _TargetSpec:
    """内部查询规格：拆分顶层符号名与类方法信息，便于 AST 匹配。"""

    symbol: SymbolInfo
    short_name: str
    owner_class_name: str | None


@dataclass
class _LocalImportBindings:
    """单个文件内的局部导入绑定表。"""

    # 局部符号名 -> (源模块全名, 原始导出名)
    symbol_aliases: dict[str, tuple[str, str]]
    # 局部模块别名或点分路径前缀 -> 绝对模块名
    module_aliases: dict[str, str]
    # 星号导入清单: (行号, 源模块名)
    star_imports: list[tuple[int, str]]


def _strip_source_root(file_path: Path, source_roots: tuple[Path, ...]) -> Path:
    """移除路径所属的源码根前缀。"""
    for root in source_roots:
        if root == Path("."):
            return file_path
        if file_path == root or root in file_path.parents:
            return file_path.relative_to(root)
    return file_path


def _resolve_module_identity(
        file_path: Path,
        source_roots: tuple[Path, ...],
) -> tuple[str, bool]:
    """还原源码文件的点分模块名，并标识是否为包入口 (__init__.py)。"""
    rel_path = _strip_source_root(file_path, source_roots).with_suffix("")
    parts = rel_path.parts
    if not parts:
        return "", False
    if parts[-1] == "__init__" and len(parts) > 1:
        return ".".join(parts[:-1]), True
    return ".".join(parts), False


def _resolve_relative_module(
        source_module: str,
        is_package: bool,
        level: int,
        node_module: str | None,
) -> str | None:
    """将相对导入还原为绝对模块路径；若超出包边界则返回 None。"""
    if not source_module:
        return None
    parts = source_module.split(".")
    pkg_parts = parts if is_package else parts[:-1]
    steps_up = level - 1
    if steps_up >= len(pkg_parts):
        return None
    anchor = pkg_parts[: len(pkg_parts) - steps_up]
    if node_module:
        return ".".join([*anchor, node_module])
    return ".".join(anchor)


def _extract_local_bindings(
        tree: ast.AST,
        source_module: str,
        is_package: bool,
        known_modules: set[str],
) -> _LocalImportBindings:
    """从单文件 AST 提取局部符号/模块导入绑定与星号导入。"""
    bindings = _LocalImportBindings(
        symbol_aliases={},
        module_aliases={},
        star_imports=[],
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bindings.module_aliases[alias.asname] = alias.name
                else:
                    bindings.module_aliases[alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                base_mod = _resolve_relative_module(
                    source_module, is_package, node.level, node.module
                )
            else:
                base_mod = node.module or ""
            if not base_mod:
                continue

            for alias in node.names:
                if alias.name == "*":
                    bindings.star_imports.append((node.lineno, base_mod))
                    continue
                local_name = alias.asname or alias.name
                sub_mod = f"{base_mod}.{alias.name}"
                # 若引入项本身对应工程内部模块，则按模块别名登记
                if sub_mod in known_modules or any(
                        m.startswith(f"{sub_mod}.") for m in known_modules
                ):
                    bindings.module_aliases[local_name] = sub_mod
                else:
                    bindings.symbol_aliases[local_name] = (base_mod, alias.name)
    return bindings


def _dotted_expr(node: ast.AST) -> str | None:
    """将属性访问左侧表达式还原为点分名称（如 a.b -> 'a.b'）；非纯属性链返回 None。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _dotted_expr(node.value)
        if prefix is not None:
            return f"{prefix}.{node.attr}"
    return None


class _ReferenceVisitor(ast.NodeVisitor):
    """基于单文件 AST 与导入绑定的候选引用扫描器。"""

    def __init__(
            self,
            *,
            file_posix: str,
            scope: Literal["source", "test"],
            source_lines: list[str],
            bindings: _LocalImportBindings,
            targets: tuple[_TargetSpec, ...],
            include_attribute_access: bool,
            max_line_chars: int,
    ) -> None:
        self.file_posix = file_posix
        self.scope: Literal["source", "test"] = scope
        self.source_lines = source_lines
        self.bindings = bindings
        self.targets = targets
        self.include_attribute_access = include_attribute_access
        self.max_line_chars = max_line_chars
        self.class_stack: list[str] = []
        self.items: list[ReferenceItem] = []

    def _add_item(
            self,
            *,
            lineno: int,
            col_offset: int,
            confidence: ReferenceConfidence,
            target_fqn: str,
            detail: str,
    ) -> None:
        """构造单条引用记录并自动附带截断后的源码行文本。"""
        raw_line = (
            self.source_lines[lineno - 1]
            if 1 <= lineno <= len(self.source_lines)
            else ""
        )
        truncated = len(raw_line) > self.max_line_chars
        preview = raw_line[: self.max_line_chars] if truncated else raw_line
        self.items.append(
            ReferenceItem(
                file_path=self.file_posix,
                line_number=lineno,
                col_offset=col_offset,
                confidence=confidence,
                scope=self.scope,
                target_fqn=target_fqn,
                line_text=preview,
                detail=detail,
                line_truncated=truncated,
            )
        )

    def visit_Import(self, node: ast.Import) -> None:
        """跳过 import 语句自身节点，避免把导入声明误记为使用点。"""
        return

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """跳过 from-import 语句自身节点；星号导入由 bindings 统一产出 UNRESOLVED。"""
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """先检查基类与装饰器中的外部引用，再压入当前类作用域扫描类体。"""
        for base in node.bases:
            self.visit(base)
        for dec in node.decorator_list:
            self.visit(dec)
        self.class_stack.append(node.name)
        for stmt in node.body:
            self.visit(stmt)
        self.class_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        """识别针对目标短名的动态属性调用（getattr / setattr / hasattr / delattr）。"""
        if (
                isinstance(node.func, ast.Name)
                and node.func.id in _DYNAMIC_ATTR_FUNCS
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and isinstance(node.args[1].value, str)
        ):
            attr_literal = node.args[1].value
            for target in self.targets:
                if attr_literal == target.short_name:
                    self._add_item(
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                        confidence=ReferenceConfidence.UNRESOLVED,
                        target_fqn=target.symbol.fqn,
                        detail=f"动态属性操作 {node.func.id}(..., {attr_literal!r}) 无法静态绑定目标实体。",
                    )
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        """识别同文件直接引用与跨文件显式导入符号引用。"""
        if not isinstance(node.ctx, ast.Load):
            return

        for target in self.targets:
            sym = target.symbol
            is_same_file = self.file_posix == sym.file_path.as_posix()

            # 1. 同文件直接名称引用（适用于顶层函数/类/常量）
            if (
                    is_same_file
                    and sym.kind in ("function", "class", "constant")
                    and node.id == sym.name
            ):
                self._add_item(
                    lineno=node.lineno,
                    col_offset=node.col_offset,
                    confidence=ReferenceConfidence.SAME_FILE_CALL,
                    target_fqn=sym.fqn,
                    detail=f"同文件内直接引用 {sym.name}。",
                )
                continue

            # 2. 跨文件通过 from mod import sym [as alias] 显式绑定
            if not is_same_file and node.id in self.bindings.symbol_aliases:
                bound_mod, orig_name = self.bindings.symbol_aliases[node.id]
                if bound_mod == sym.module_name and orig_name == sym.name:
                    self._add_item(
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                        confidence=ReferenceConfidence.EXPLICIT_IMPORT_CALL,
                        target_fqn=sym.fqn,
                        detail=f"通过显式导入 {bound_mod}.{orig_name}（局部名 {node.id}）引用。",
                    )
                    continue

            # 3. 若查询目标为模块，命中模块局部别名
            if sym.kind == "module" and node.id in self.bindings.module_aliases:
                if self.bindings.module_aliases[node.id] == sym.module_name:
                    self._add_item(
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                        confidence=ReferenceConfidence.EXPLICIT_IMPORT_CALL,
                        target_fqn=sym.fqn,
                        detail=f"通过显式导入模块别名 {node.id} 引用模块 {sym.module_name}。",
                    )

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """识别 self/cls 同类调用、显式模块/类前缀引用以及未绑定对象的同名属性访问。"""
        if isinstance(node.ctx, ast.Load):
            owner_expr = _dotted_expr(node.value)
            for target in self.targets:
                sym = target.symbol
                if node.attr != target.short_name:
                    continue
                is_same_file = self.file_posix == sym.file_path.as_posix()

                # 1. 同文件且位于目标类体内的 self.method / cls.method
                if (
                        is_same_file
                        and sym.kind == "method"
                        and target.owner_class_name is not None
                        and owner_expr in ("self", "cls")
                        and target.owner_class_name in self.class_stack
                ):
                    self._add_item(
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                        confidence=ReferenceConfidence.SAME_FILE_CALL,
                        target_fqn=sym.fqn,
                        detail=f"目标类 {target.owner_class_name} 内部通过 {owner_expr}.{node.attr} 引用。",
                    )
                    continue

                # 2. 通过模块别名访问（如 engine.BuildEngine / m.execute_query）
                if (
                        owner_expr is not None
                        and owner_expr in self.bindings.module_aliases
                        and self.bindings.module_aliases[owner_expr] == sym.module_name
                        and sym.kind in ("function", "class", "constant")
                ):
                    self._add_item(
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                        confidence=ReferenceConfidence.EXPLICIT_IMPORT_CALL,
                        target_fqn=sym.fqn,
                        detail=f"通过显式模块前缀 {owner_expr}.{node.attr} 引用。",
                    )
                    continue

                # 3. 通过显式类符号调用类方法/静态属性（如 BuildEngine.build）
                if (
                        sym.kind == "method"
                        and target.owner_class_name is not None
                        and owner_expr is not None
                ):
                    if is_same_file and owner_expr == target.owner_class_name:
                        self._add_item(
                            lineno=node.lineno,
                            col_offset=node.col_offset,
                            confidence=ReferenceConfidence.SAME_FILE_CALL,
                            target_fqn=sym.fqn,
                            detail=f"同文件内通过类名 {owner_expr}.{node.attr} 引用。",
                        )
                        continue
                    bound = self.bindings.symbol_aliases.get(owner_expr)
                    if bound == (sym.module_name, target.owner_class_name):
                        self._add_item(
                            lineno=node.lineno,
                            col_offset=node.col_offset,
                            confidence=ReferenceConfidence.EXPLICIT_IMPORT_CALL,
                            target_fqn=sym.fqn,
                            detail=f"通过显式导入类 {owner_expr}.{node.attr} 引用。",
                        )
                        continue

                # 4. 其余未知接收者对象的同名属性访问（如 instance.build()）
                if self.include_attribute_access:
                    self._add_item(
                        lineno=node.lineno,
                        col_offset=node.col_offset,
                        confidence=ReferenceConfidence.ATTRIBUTE_ACCESS,
                        target_fqn=sym.fqn,
                        detail=f"同名属性访问 .{node.attr}（静态无法确定接收对象类型，需结合上下文核实）。",
                    )

        self.generic_visit(node)


def _scan_single_file(
        *,
        project_root: Path,
        rel_path: Path,
        scope: Literal["source", "test"],
        source_roots: tuple[Path, ...],
        known_modules: set[str],
        targets: tuple[_TargetSpec, ...],
        include_attribute_access: bool,
        max_line_chars: int,
) -> list[ReferenceItem] | None:
    """扫描单个 Python 文件的候选引用；读取或语法错误时返回 None。"""
    full_path = project_root / rel_path
    try:
        source_text = full_path.read_text(encoding="utf-8")
        tree = ast.parse(source_text, filename=rel_path.as_posix())
    except (OSError, UnicodeDecodeError, SyntaxError):
        return None

    source_module, is_package = _resolve_module_identity(rel_path, source_roots)
    bindings = _extract_local_bindings(tree, source_module, is_package, known_modules)
    source_lines = source_text.splitlines()
    file_posix = rel_path.as_posix()

    visitor = _ReferenceVisitor(
        file_posix=file_posix,
        scope=scope,
        source_lines=source_lines,
        bindings=bindings,
        targets=targets,
        include_attribute_access=include_attribute_access,
        max_line_chars=max_line_chars,
    )
    visitor.visit(tree)

    # 若该文件对目标定义所在模块执行了星号导入，记录一条 UNRESOLVED 线索
    target_modules = {t.symbol.module_name: t.symbol.fqn for t in targets}
    for star_line, star_mod in bindings.star_imports:
        if star_mod in target_modules:
            raw_line = (
                source_lines[star_line - 1]
                if 1 <= star_line <= len(source_lines)
                else ""
            )
            truncated = len(raw_line) > max_line_chars
            visitor.items.append(
                ReferenceItem(
                    file_path=file_posix,
                    line_number=star_line,
                    col_offset=0,
                    confidence=ReferenceConfidence.UNRESOLVED,
                    scope=scope,
                    target_fqn=target_modules[star_mod],
                    line_text=raw_line[:max_line_chars] if truncated else raw_line,
                    detail=f"存在星号导入 from {star_mod} import *，无法静态跟踪潜在引用。",
                    line_truncated=truncated,
                )
            )

    return visitor.items


def _dedup_and_sort_items(items: list[ReferenceItem]) -> list[ReferenceItem]:
    """按 (文件路径, 行号, 列号, 目标FQN, 可信度) 去重，并按可信度优先级确定性排序。"""
    seen: set[tuple[str, int, int, str, ReferenceConfidence]] = set()
    unique: list[ReferenceItem] = []
    for item in items:
        key = (
            item.file_path,
            item.line_number,
            item.col_offset,
            item.target_fqn,
            item.confidence,
        )
        if key not in seen:
            seen.add(key)
            unique.append(item)

    unique.sort(
        key=lambda item: (
            _CONFIDENCE_RANK[item.confidence],
            0 if item.scope == "source" else 1,
            item.file_path,
            item.line_number,
            item.col_offset,
            item.target_fqn,
        )
    )
    return unique


def _format_ref_line(item: ReferenceItem) -> str:
    """将单条候选引用格式化为可读摘要行。"""
    scope_tag = "TEST" if item.scope == "test" else "SRC"
    return (
        f"[{item.confidence.value}][{scope_tag}] "
        f"{item.file_path}:{item.line_number} | {item.line_text.strip()}"
    )


def _paginate_references(
        items: list[ReferenceItem],
        offset: int,
        max_results: int,
        max_chars: int,
) -> tuple[tuple[ReferenceItem, ...], str, int, dict[str, int] | None]:
    """按条数与字符双预算切片候选引用，保证至少推进 1 条防止死循环。"""
    page: list[ReferenceItem] = []
    lines: list[str] = []
    used_chars = 0

    for item in items[offset : offset + max_results]:
        formatted = _format_ref_line(item)
        added = len(formatted) + (1 if lines else 0)
        if used_chars + added > max_chars:
            if not page:
                page.append(item)
                lines.append(formatted[:max_chars])
            break
        page.append(item)
        lines.append(formatted)
        used_chars += added

    remaining = max(0, len(items) - (offset + len(page)))
    next_cursor = {"offset": offset + len(page)} if remaining > 0 else None
    return tuple(page), "\n".join(lines), remaining, next_cursor


def _resolve_target_symbols(
        symbol_index: SymbolIndex,
        cleaned_query: str,
) -> tuple[tuple[SymbolInfo, ...], tuple[SymbolInfo, ...]]:
    """
    解析待查找的目标符号定义：支持 FQN、精确短名以及 Class.method 点分后缀精确匹配。

    Returns:
        (精确匹配到的目标符号元组, 未精确命中时的相似候选元组)
    """
    lookup_res = symbol_index.lookup_symbol(cleaned_query)
    if lookup_res.match_mode in ("exact_fqn", "exact_name"):
        return lookup_res.matches, ()

    if "." in cleaned_query:
        suffix = f".{cleaned_query}"
        suffix_matches = tuple(
            sym
            for sym in symbol_index.symbol_table.symbols
            if sym.fqn.endswith(suffix)
        )
        if suffix_matches:
            return suffix_matches, ()

    return (), lookup_res.matches

def find_references(
        symbol_name: str,
        project_root: str | Path | None = None,
        symbol_index: SymbolIndex | None = None,
        *,
        include_tests: bool = True,
        include_attribute_access: bool = True,
        max_results: int = DEFAULT_MAX_RESULTS,
        max_chars: int = DEFAULT_MAX_CHARS,
        max_line_chars: int = DEFAULT_MAX_LINE_CHARS,
        offset: int = 0,
) -> ToolResult[FindReferencesData]:
    """
    结合静态符号索引与 AST 扫描查找指定符号的候选引用（含同文件、跨文件与未解析线索）。

    Args:
        symbol_name: 待查找引用的符号短名或全限定名（FQN，如 "BuildEngine.build"）。
        project_root: 工程根目录路径（未传入 symbol_index 时必填）。
        symbol_index: 可选的已构建 SymbolIndex 实例，用于多步探查时免重复建库。
        include_tests: 是否同时扫描测试文件中的引用，默认 True。
        include_attribute_access: 是否包含未绑定类型的同名属性访问候选，默认 True。
        max_results: 单页最多返回的引用条目数。
        max_chars: 单页格式化预览的最大字符数预算。
        max_line_chars: 单行代码预览的最大字符数。
        offset: 分页偏移量，用于配合 next_cursor 续读。

    Returns:
        ToolResult[FindReferencesData]: 带可信度分级的候选引用结果与诚实边界提示。
    """
    cleaned = symbol_name.strip()
    if not cleaned:
        return tool_error(_TOOL_NAME, "empty_symbol_name", "待查找的符号名称不能为空。")
    if max_results < 1 or max_chars < 1 or max_line_chars < 1:
        return tool_error(
            _TOOL_NAME,
            "invalid_budget",
            "预算参数（max_results / max_chars / max_line_chars）必须 >= 1。",
        )
    if offset < 0:
        return tool_error(
            _TOOL_NAME,
            "invalid_offset",
            f"offset 不能小于 0，当前为 {offset}。",
        )

    active_index = symbol_index
    if active_index is None:
        if project_root is None:
            return tool_error(
                _TOOL_NAME,
                "missing_project_root",
                "未传入 symbol_index 时必须提供 project_root。",
            )
        try:
            active_index = build_symbol_index(project_root)
        except (NotADirectoryError, OSError) as exc:
            return tool_error(_TOOL_NAME, "invalid_project_root", str(exc))

    matched_symbols, suggestions = _resolve_target_symbols(active_index, cleaned)
    if not matched_symbols:
        suggestion_text = (
            "；相似符号候选：" + ", ".join(sym.fqn for sym in suggestions[:5])
            if suggestions
            else ""
        )
        data = FindReferencesData(
            query=cleaned,
            target_definitions=(),
            references=(),
            formatted_preview="",
            confidence_counts={c.value: 0 for c in ReferenceConfidence},
            scanned_files=0,
            skipped_files=0,
        )
        return ToolResult(
            ok=True,
            tool_name=_TOOL_NAME,
            data=data,
            total_matches=0,
            returned_count=0,
            remaining_count=0,
            note=(
                f"未在静态符号表中精确找到 {cleaned!r} 的定义"
                f"{suggestion_text}。若怀疑存在动态定义，请结合 search_text 核实。"
            ),
        )

    targets = tuple(
        _TargetSpec(
            symbol=sym,
            short_name=sym.name,
            owner_class_name=(
                sym.parent_fqn.rsplit(".", 1)[-1]
                if sym.kind == "method" and sym.parent_fqn
                else None
            ),
        )
        for sym in matched_symbols
    )

    structure = active_index.structure
    known_modules = set(active_index.dependency_graph.modules)
    file_jobs: list[tuple[Path, Literal["source", "test"]]] = [
        (path, "source") for path in structure.source_files
    ]
    if include_tests:
        file_jobs.extend((path, "test") for path in structure.test_files)

    all_items: list[ReferenceItem] = []
    scanned_files = 0
    skipped_files = 0
    for rel_path, scope in file_jobs:
        file_items = _scan_single_file(
            project_root=structure.project_root,
            rel_path=rel_path,
            scope=scope,
            source_roots=structure.source_roots,
            known_modules=known_modules,
            targets=targets,
            include_attribute_access=include_attribute_access,
            max_line_chars=max_line_chars,
        )
        if file_items is None:
            skipped_files += 1
            continue
        scanned_files += 1
        all_items.extend(file_items)

    sorted_items = _dedup_and_sort_items(all_items)
    total_matches = len(sorted_items)
    if offset > 0 and offset >= total_matches:
        return tool_error(
            _TOOL_NAME,
            "offset_out_of_range",
            f"offset ({offset}) 超出候选引用总数 ({total_matches})。",
        )

    counts = {c.value: 0 for c in ReferenceConfidence}
    for item in sorted_items:
        counts[item.confidence.value] += 1

    page_items, preview, remaining, next_cursor = _paginate_references(
        sorted_items,
        offset=offset,
        max_results=max_results,
        max_chars=max_chars,
    )

    notes = [
        "仅包含静态 AST 支持范围内的候选引用（按可信度分级），受限于 Python 动态特性不承诺已找出全部调用点。"
    ]
    if counts[ReferenceConfidence.ATTRIBUTE_ACCESS.value] > 0:
        notes.append("ATTRIBUTE_ACCESS 为同名属性访问候选，请结合 read_file_snippet 确认接收对象类型。")
    if counts[ReferenceConfidence.UNRESOLVED.value] > 0:
        notes.append("存在 UNRESOLVED 动态或星号导入线索，建议人工/工具进一步核实。")
    if remaining > 0:
        notes.append(f"尚有 {remaining} 条候选未返回，可传入 next_cursor 续读。")

    data = FindReferencesData(
        query=cleaned,
        target_definitions=matched_symbols,
        references=page_items,
        formatted_preview=preview,
        confidence_counts=counts,
        scanned_files=scanned_files,
        skipped_files=skipped_files,
    )
    return ToolResult(
        ok=True,
        tool_name=_TOOL_NAME,
        data=data,
        is_truncated=remaining > 0,
        total_matches=total_matches,
        returned_count=len(page_items),
        remaining_count=remaining,
        next_cursor=next_cursor,
        note=" ".join(notes),
    )