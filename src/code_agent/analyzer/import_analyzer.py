import ast
from dataclasses import dataclass
from pathlib import Path
from code_agent.analyzer.project_scanner import ProjectStructure, scan_project

@dataclass(frozen=True)
class ImportEdge:
    """
       单条已解析的模块显式导入依赖边。
       Attributes:
           source_module: 发起导入的当前模块全限定名（如 "code_agent.core.query_engine"）。
           file_path: 发起导入的源码文件相对路径（相对于工程根目录）。
           target_module: 解析后的目标模块绝对路径（相对导入会被提前还原，如 "code_agent.core.schemas"）。
           imported_name: `from X import Y` 中具体导入的成员名；
               若语句是 `import X` 或 `Y` 本身已被识别为工程内部子模块，则为 None。
           alias: 通过 `as` 指定的局部别名（如 `import numpy as np` 中的 "np"）；无别名时为 None。
           is_relative: 是否为相对导入语法（如 `from . import x` 或 `from ..core import y` 为 True）。
           is_internal: 目标模块是否属于当前工程内部源码模块；
               True 表示项目内跨模块依赖，False 表示标准库或外部第三方依赖。
           line_number: 该 import 语句在源文件中的物理起始行号（1-based）。
    """
    source_module: str
    file_path: Path
    target_module: str
    imported_name: str | None
    alias: str | None
    is_relative: bool
    is_internal: bool
    line_number: int

@dataclass(frozen=True)
class UnresolvedImport:
    """
        超出静态解析范围或存在导入异常的未解析记录。
        Attributes:
            file_path: 出现该导入的源码文件相对路径。
            source_module: 该文件对应的点分模块名。
            reason_code: 机器可读的未解析原因码（如 star_import / dynamic_import /
                relative_import_overflow / read_error / syntax_error）。
            detail: 面向人工或下游 Agent 的可读说明。
            line_number: 语句所在物理行号；文件读取失败时可为 None。
            raw_statement: 还原出的原始语句文本（便于排查定位），无具体语句时为 None。
    """
    file_path: Path
    source_module: str
    reason_code: str
    detail: str
    line_number: int | None = None
    raw_statement: str | None = None

@dataclass(frozen=True)
class ModuleDependencyGraph:
    """
    工程级有向模块依赖图与未解析导入清单。
    Attributes:
        project_root: 工程根目录绝对路径。
        modules: 工程内部所有已识别的源码模块名清单（已去重并按字典序排序）。
        edges: 所有成功解析的显式导入边（按文件路径、行号、目标模块确定性排序）。
        unresolved: 超出静态支持范围的导入记录清单。
    """
    project_root: Path
    modules: tuple[str,...]
    edges: tuple[ImportEdge,...]
    unresolved: tuple[UnresolvedImport,...]

    def dependencies_of(self,
                        module_name: str,
                        *,
                        internal_only: bool = False,
                        ) -> tuple[str,...]:
        """
        正向查询：返回指定模块所依赖的目标模块列表（去重且保序）。
        Args:
            module_name: 待查询的内部源模块名（如 "minisite.cli"）。
            internal_only: 若为 True，仅保留工程内部模块依赖，过滤掉标准库与第三方库。
        Returns:
            该模块显式导入的目标模块名元组。
        """
        targets: list[str] = []
        seen: set[str] = set()
        for edge in self.edges:
            if edge.source_module != module_name:
                continue
            if internal_only and not edge.is_internal:
                continue
            if edge.target_module not in seen:
                seen.add(edge.target_module)
                targets.append(edge.target_module)

        return tuple(targets)

    def dependents_of(self, module_name: str) -> tuple[str, ...]:
        """
        反向查询：返回所有显式导入了指定模块的工程内部模块列表（去重且保序）。
        Args:
            module_name: 被依赖的目标模块名（如 "minisite.exceptions"）。
        Returns:
            所有直接引用该模块的内部源模块名元组。
        """
        sources: list[str] = []
        seen: set[str] = set()
        for edge in self.edges:
            if edge.target_module == module_name and edge.source_module not in seen:
                seen.add(edge.source_module)
                sources.append(edge.source_module)
        return tuple(sources)


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
    """还原源码文件的点分模块名，并标识其是否为包入口 (__init__.py)。"""
    rel_path = _strip_source_root(file_path, source_roots).with_suffix("")
    parts = rel_path.parts
    if not parts:
        return "", False
    if parts[-1] == "__init__" and len(parts) > 1:
        return ".".join(parts[:-1]), True
    return ".".join(parts), False

def _resolve_relative_base(
    source_module: str,
    is_package: bool,
    level: int,
    node_module: str | None,
) -> str | None:
    """
    将相对导入还原为绝对基础模块路径；若层级越界则按契约返回 None。
    示例（假设顶层包为 code_agent）：
    - code_agent/core/query_engine.py (is_package=False, package_parts=['code_agent', 'core'])
      - level=1, module='schemas' -> 'code_agent.core.schemas'
      - level=2, module='index'   -> 'code_agent.index'
      - level=3                   -> None（超出顶层包边界）
    - code_agent/core/__init__.py (is_package=True, package_parts=['code_agent', 'core'])
      - level=1, module='model'   -> 'code_agent.core.model'
      - level=2, module='index'   -> 'code_agent.index'
    """
    if not source_module:
        return None
    module_parts = source_module.split(".")
    package_parts = module_parts if is_package else module_parts[:-1]
    steps_up = level - 1
    if steps_up >= len(package_parts):
        return None
    anchor_parts = package_parts[: len(package_parts) - steps_up]
    if node_module:
        return ".".join([*anchor_parts, node_module])
    return ".".join(anchor_parts)

def _is_internal_module(target_module: str, known_modules: set[str]) -> bool:
    """
        判断目标模块是否属于当前工程内部源码模块。
        Args:
            target_module: 待判定的点分模块名。
            known_modules: 工程内部全部已知模块名集合。
        Returns:
            若精确命中已知模块，或作为已知模块的上级命名空间包前缀存在，则返回 True。
    """
    if target_module in known_modules:
        return True
    prefix = f"{target_module}."
    return any(mod.startswith(prefix) for mod in known_modules)

def _collect_importlib_aliases(
    tree: ast.AST,
) -> tuple[set[str], set[str]]:
    """
    收集文件内引入 importlib 模块及 import_module 函数的局部名称/别名。
    Args:
        tree: 已解析的模块 AST 根节点。
    Returns:
        (importlib_aliases, import_module_aliases) 二元组，用于辅助识别动态导入调用。
    """
    importlib_aliases: set[str] = set()
    import_module_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "importlib":
                    importlib_aliases.add(alias.asname or "importlib")
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module == "importlib":
            for alias in node.names:
                if alias.name == "import_module":
                    import_module_aliases.add(alias.asname or "import_module")
    return importlib_aliases, import_module_aliases


def _analyze_file_imports(
    project_root: Path,
    file_path: Path,
    source_module: str,
    is_package: bool,
    known_modules: set[str],
) -> tuple[list[ImportEdge], list[UnresolvedImport]]:
    """
    从单个 Python 源码文件中抽取显式导入边与 unresolved 记录。
    Args:
        project_root: 工程根目录绝对路径。
        file_path: 待解析文件相对于 project_root 的路径。
        source_module: 该文件对应的点分模块名。
        is_package: 该文件是否为包入口文件 (__init__.py)。
        known_modules: 工程内部全部已知模块名集合，用于区分内部模块与外部库。
    Returns:
        (edges, unresolved) 二元组，分别表示解析成功的依赖边和未解析记录。
    """
    edges: list[ImportEdge] = []
    unresolved: list[UnresolvedImport] = []
    full_path = project_root / file_path

    try:
        source_text = full_path.read_text(encoding="utf-8")
    except OSError as exc:
        unresolved.append(
            UnresolvedImport(
                file_path=file_path,
                source_module=source_module,
                reason_code="read_error",
                detail=f"读取源码文件失败: {exc}",
            )
        )
        return edges, unresolved

    try:
        tree = ast.parse(source_text, filename=file_path.as_posix())
    except SyntaxError as exc:
        unresolved.append(
            UnresolvedImport(
                file_path=file_path,
                source_module=source_module,
                reason_code="syntax_error",
                detail=f"AST 语法解析失败: {exc.msg}",
                line_number=exc.lineno,
            )
        )
        return edges, unresolved

    importlib_aliases, import_module_aliases = _collect_importlib_aliases(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                edges.append(
                    ImportEdge(
                        source_module=source_module,
                        file_path=file_path,
                        target_module=alias.name,
                        imported_name=None,
                        alias=alias.asname,
                        is_relative=False,
                        is_internal=_is_internal_module(alias.name, known_modules),
                        line_number=node.lineno,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            from_edges, from_unresolved = _extract_from_import(
                node=node,
                file_path=file_path,
                source_module=source_module,
                is_package=is_package,
                known_modules=known_modules,
            )
            edges.extend(from_edges)
            unresolved.extend(from_unresolved)
        elif isinstance(node, ast.Call):
            dyn_item = _check_dynamic_import_call(
                node=node,
                file_path=file_path,
                source_module=source_module,
                importlib_aliases=importlib_aliases,
                import_module_aliases=import_module_aliases,
            )
            if dyn_item is not None:
                unresolved.append(dyn_item)
    return edges, unresolved


def analyze_imports(
        project_or_root: ProjectStructure | str | Path,
) -> ModuleDependencyGraph:
    """
    分析工程源码的显式 import 拓扑并构建有向依赖图。
    Args:
        project_or_root: 已扫描的 ProjectStructure 对象，或工程根目录路径。
    Returns:
        ModuleDependencyGraph: 包含内部模块清单、确定性排序的依赖边与未解析导入列表。
    """
    structure = (
        project_or_root
        if isinstance(project_or_root, ProjectStructure)
        else scan_project(project_or_root)
    )
    file_identities: list[tuple[Path, str, bool]] = []
    known_modules: set[str] = set()
    for file_path in structure.source_files:
        module_name, is_package = _resolve_module_identity(
            file_path, structure.source_roots
        )
        file_identities.append((file_path, module_name, is_package))
        if module_name:
            known_modules.add(module_name)
    all_edges: list[ImportEdge] = []
    all_unresolved: list[UnresolvedImport] = []
    for file_path, module_name, is_package in file_identities:
        file_edges, file_unresolved = _analyze_file_imports(
            project_root=structure.project_root,
            file_path=file_path,
            source_module=module_name,
            is_package=is_package,
            known_modules=known_modules,
        )
        all_edges.extend(file_edges)
        all_unresolved.extend(file_unresolved)
    sorted_modules = tuple(sorted(known_modules))
    sorted_edges = tuple(
        sorted(
            all_edges,
            key=lambda edge: (
                edge.file_path.as_posix(),
                edge.line_number,
                edge.target_module,
                edge.imported_name or "",
                edge.alias or "",
            ),
        )
    )
    sorted_unresolved = tuple(
        sorted(
            all_unresolved,
            key=lambda item: (
                item.file_path.as_posix(),
                item.line_number or 0,
                item.reason_code,
                item.raw_statement or "",
            ),
        )
    )
    return ModuleDependencyGraph(
        project_root=structure.project_root,
        modules=sorted_modules,
        edges=sorted_edges,
        unresolved=sorted_unresolved,
    )

def _extract_from_import(
    node: ast.ImportFrom,
    file_path: Path,
    source_module: str,
    is_package: bool,
    known_modules: set[str],
) -> tuple[list[ImportEdge], list[UnresolvedImport]]:
    """专门解析单条 from ... import ... 语句，返回依赖边或未解析记录。"""
    edges: list[ImportEdge] = []
    unresolved: list[UnresolvedImport] = []
    is_relative = node.level > 0

    if is_relative:
        resolved_base = _resolve_relative_base(
            source_module=source_module,
            is_package=is_package,
            level=node.level,
            node_module=node.module,
        )
        if resolved_base is None:
            unresolved.append(
                UnresolvedImport(
                    file_path=file_path,
                    source_module=source_module,
                    reason_code="relative_import_overflow",
                    detail=f"相对导入层级 (level={node.level}) 超出当前模块包边界。",
                    line_number=node.lineno,
                    raw_statement=ast.unparse(node),
                )
            )
            return edges, unresolved
    else:
        resolved_base = node.module or ""

    if not resolved_base:
        unresolved.append(
            UnresolvedImport(
                file_path=file_path,
                source_module=source_module,
                reason_code="empty_import_module",
                detail="无法解析空模块路径的 from-import 语句。",
                line_number=node.lineno,
                raw_statement=ast.unparse(node),
            )
        )
        return edges, unresolved

    for alias in node.names:
        if alias.name == "*":
            unresolved.append(
                UnresolvedImport(
                    file_path=file_path,
                    source_module=source_module,
                    reason_code="star_import",
                    detail=f"星号导入 from {resolved_base} import * 无法静态确定导入符号清单。",
                    line_number=node.lineno,
                    raw_statement=ast.unparse(node),
                )
            )
            continue

        candidate_submodule = f"{resolved_base}.{alias.name}"
        if _is_internal_module(candidate_submodule, known_modules):
            target_module = candidate_submodule
            imported_name: str | None = None
        else:
            target_module = resolved_base
            imported_name = alias.name

        edges.append(
            ImportEdge(
                source_module=source_module,
                file_path=file_path,
                target_module=target_module,
                imported_name=imported_name,
                alias=alias.asname,
                is_relative=is_relative,
                is_internal=_is_internal_module(target_module, known_modules),
                line_number=node.lineno,
            )
        )
    return edges, unresolved


def _check_dynamic_import_call(
    node: ast.Call,
    file_path: Path,
    source_module: str,
    importlib_aliases: set[str],
    import_module_aliases: set[str],
) -> UnresolvedImport | None:
    """检查函数调用是否为 __import__ 或 importlib.import_module 动态导入。"""
    func = node.func
    is_builtin_dynamic = isinstance(func, ast.Name) and (
        func.id == "__import__" or func.id in import_module_aliases
    )
    is_importlib_dynamic = (
        isinstance(func, ast.Attribute)
        and func.attr == "import_module"
        and isinstance(func.value, ast.Name)
        and func.value.id in importlib_aliases
    )
    if not (is_builtin_dynamic or is_importlib_dynamic):
        return None
    return UnresolvedImport(
        file_path=file_path,
        source_module=source_module,
        reason_code="dynamic_import",
        detail=f"检测到动态导入调用: {ast.unparse(node)}",
        line_number=node.lineno,
        raw_statement=ast.unparse(node),
    )