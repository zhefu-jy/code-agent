import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from code_agent.analyzer.project_scanner import ProjectStructure, scan_project

SymbolKind = Literal["module", "class", "function", "method", "constant"]

@dataclass(frozen=True)
class SymbolInfo:
    """单个代码实体的结构化符号信息。"""
    fqn: str
    name: str
    kind: SymbolKind
    module_name: str
    file_path: Path
    start_line: int
    end_line: int
    signature: str | None = None
    bases: tuple[str, ...] = ()
    parent_fqn: str | None = None

@dataclass(frozen=True)
class UnresolvedItem:
    """超出静态 AST 解析范围或无法确定归属的边界记录。"""
    file_path: Path
    module_name: str
    reason_code: str
    detail: str
    start_line: int | None = None
    end_line: int | None = None
    symbol_name: str | None = None

@dataclass(frozen=True)
class SymbolTable:
    """工程级确定性符号表与未解析清单。"""
    project_root: Path
    symbols: tuple[SymbolInfo, ...]
    unresolved: tuple[UnresolvedItem, ...]

    def get_by_fqn(self, fqn: str) -> SymbolInfo | None:
        """按全限定名精确查找符号。"""
        for symbol in self.symbols:
            if symbol.fqn == fqn:
                return symbol
        return None

    def find_by_name(self, name: str) -> tuple[SymbolInfo, ...]:
        """按短名称查找所有同名符号。"""
        return tuple(symbol for symbol in self.symbols if symbol.name == name)

    def symbols_in_file(self, file_path: Path | str) -> tuple[SymbolInfo, ...]:
        """获取指定相对路径文件内的全部符号。"""
        target = Path(file_path).as_posix()
        return tuple(
            symbol for symbol in self.symbols if symbol.file_path.as_posix() == target
        )


def _strip_source_root(file_path: Path, source_roots: tuple[Path, ...]) -> Path:
    """移除路径所属的源码根前缀，便于后续还原模块名。"""
    for root in source_roots:
        if root == Path("."):
            return file_path
        if file_path == root or root in file_path.parents:
            return file_path.relative_to(root)
    return file_path


def _module_name_from_path(file_path: Path, source_roots: tuple[Path, ...]) -> str:
    """将源码相对路径转换为标准 Python 点分模块名。"""
    rel_path = _strip_source_root(file_path, source_roots).with_suffix("")
    parts = rel_path.parts
    if not parts:
        return ""
    # 包入口文件 __init__.py 对应的模块名是所在包名本身
    if parts[-1] == "__init__" and len(parts) > 1:
        return ".".join(parts[:-1])
    return ".".join(parts)

def _format_signature(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    """利用 ast.unparse 还原函数或方法的参数及返回值签名。"""
    args_text = ast.unparse(node.args)
    returns_text = f" -> {ast.unparse(node.returns)}" if node.returns is not None else ""
    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
    return f"{prefix}({args_text}){returns_text}"

def _is_main_guard(node: ast.stmt) -> bool:
    """判断是否为标准的 if __name__ == '__main__': 入口守卫。"""
    if not isinstance(node, ast.If):
        return False
    test = node.test
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or len(test.comparators) != 1:
        return False
    if not isinstance(test.ops[0], ast.Eq):
        return False
    left, right = test.left, test.comparators[0]
    return (
            isinstance(left, ast.Name)
            and left.id == "__name__"
            and isinstance(right, ast.Constant)
            and right.value == "__main__"
    )


def _check_namespace_packages(structure: ProjectStructure) -> list[UnresolvedItem]:
    """检查源码包目录是否缺少 __init__.py（PEP 420 命名空间包）。"""
    items: list[UnresolvedItem] = []
    seen_dirs: set[Path] = set()
    for file_path in structure.source_files:
        rel_to_root = _strip_source_root(file_path, structure.source_roots)
        # 顶层单文件（如 app.py）不在子包内，跳过检查
        if len(rel_to_root.parts) <= 1:
            continue
        # 逐层检查所属包目录（注意拼接原 source_root 还原物理相对路径）
        source_root = Path(".")
        for root in structure.source_roots:
            if root == Path(".") or root in file_path.parents:
                source_root = root
                break
        for parent in rel_to_root.parents:
            if parent == Path("."):
                continue
            pkg_rel_dir = parent if source_root == Path(".") else source_root / parent
            if pkg_rel_dir in seen_dirs:
                continue
            seen_dirs.add(pkg_rel_dir)
            if not (structure.project_root / pkg_rel_dir / "__init__.py").is_file():
                mod_name = ".".join(parent.parts)
                items.append(
                    UnresolvedItem(
                        file_path=pkg_rel_dir,
                        module_name=mod_name,
                        reason_code="namespace_package",
                        detail=f"包目录 {pkg_rel_dir.as_posix()} 缺少 __init__.py（命名空间包），模块解析可能存在歧义。",
                    )
                )
    return items

def _find_dynamic_setattr(
        tree: ast.AST,
        file_path: Path,
        module_name: str,
) -> list[UnresolvedItem]:
    """扫描文件内所有动态 setattr 调用，防止运行时注入属性被静默忽略。"""
    items: list[UnresolvedItem] = []
    for node in ast.walk(tree):
        if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "setattr"
        ):
            items.append(
                UnresolvedItem(
                    file_path=file_path,
                    module_name=module_name,
                    reason_code="dynamic_setattr",
                    detail=f"检测到动态 setattr 调用: {ast.unparse(node)}",
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                )
            )
    return items

def _extract_static_all_names(
        value_node: ast.expr,
) -> list[str] | None:
    """若 __all__ 右值为静态字符串列表/元组字面量则返回成员列表，否则返回 None。"""
    if not isinstance(value_node, (ast.List, ast.Tuple, ast.Set)):
        return None
    names: list[str] = []
    for elt in value_node.elts:
        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
            names.append(elt.value)
        else:
            return None
    return names

def _extract_file_symbols(
        project_root: Path,
        file_path: Path,
        module_name: str,
) -> tuple[list[SymbolInfo], list[UnresolvedItem]]:
    """从单个 Python 源码文件中提取确定性符号及 unresolved 记录。"""
    symbols: list[SymbolInfo] = []
    unresolved: list[UnresolvedItem] = []
    full_path = project_root / file_path
    try:
        source_text = full_path.read_text(encoding="utf-8")
    except OSError as exc:
        unresolved.append(
            UnresolvedItem(
                file_path=file_path,
                module_name=module_name,
                reason_code="read_error",
                detail=f"读取源码文件失败: {exc}",
            )
        )
        return symbols, unresolved
    try:
        tree = ast.parse(source_text, filename=file_path.as_posix())
    except SyntaxError as exc:
        unresolved.append(
            UnresolvedItem(
                file_path=file_path,
                module_name=module_name,
                reason_code="syntax_error",
                detail=f"AST 语法解析失败: {exc.msg}",
                start_line=exc.lineno,
                end_line=exc.lineno,
            )
        )
        return symbols, unresolved
    total_lines = max(1, len(source_text.splitlines()))
    module_short_name = module_name.rsplit(".", 1)[-1] if module_name else file_path.stem
    symbols.append(
        SymbolInfo(
            fqn=module_name,
            name=module_short_name,
            kind="module",
            module_name=module_name,
            file_path=file_path,
            start_line=1,
            end_line=total_lines,
        )
    )
    local_defined_names: set[str] = set()
    declared_all_exports: list[tuple[str, int, int]] = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_fqn = f"{module_name}.{node.name}" if module_name else node.name
            bases = tuple(ast.unparse(base) for base in node.bases)
            symbols.append(
                SymbolInfo(
                    fqn=class_fqn,
                    name=node.name,
                    kind="class",
                    module_name=module_name,
                    file_path=file_path,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    bases=bases,
                    parent_fqn=module_name,
                )
            )
            local_defined_names.add(node.name)
            # 边界检测：多重继承或非简单 Name/Attribute 的动态基类
            has_complex_base = len(node.bases) > 1 or any(
                not isinstance(base, (ast.Name, ast.Attribute)) for base in node.bases
            )
            if has_complex_base:
                unresolved.append(
                    UnresolvedItem(
                        file_path=file_path,
                        module_name=module_name,
                        reason_code="complex_inheritance",
                        detail=f"类 {node.name} 存在多重继承或动态基类表达式 ({', '.join(bases)})，未做完整 MRO 展开。",
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        symbol_name=class_fqn,
                    )
                )
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    method_fqn = f"{class_fqn}.{item.name}"
                    symbols.append(
                        SymbolInfo(
                            fqn=method_fqn,
                            name=item.name,
                            kind="method",
                            module_name=module_name,
                            file_path=file_path,
                            start_line=item.lineno,
                            end_line=item.end_lineno or item.lineno,
                            signature=_format_signature(item),
                            parent_fqn=class_fqn,
                        )
                    )
                elif isinstance(item, ast.ClassDef):
                    unresolved.append(
                        UnresolvedItem(
                            file_path=file_path,
                            module_name=module_name,
                            reason_code="nested_class",
                            detail=f"类 {node.name} 内部存在嵌套类 {item.name}，当前版本未展开嵌套类符号。",
                            start_line=item.lineno,
                            end_line=item.end_lineno or item.lineno,
                            symbol_name=f"{class_fqn}.{item.name}",
                        )
                    )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            func_fqn = f"{module_name}.{node.name}" if module_name else node.name
            symbols.append(
                SymbolInfo(
                    fqn=func_fqn,
                    name=node.name,
                    kind="function",
                    module_name=module_name,
                    file_path=file_path,
                    start_line=node.lineno,
                    end_line=node.end_lineno or node.lineno,
                    signature=_format_signature(node),
                    parent_fqn=module_name,
                )
            )
            local_defined_names.add(node.name)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if target.id == "__all__":
                        static_all = _extract_static_all_names(node.value)
                        if static_all is None:
                            unresolved.append(
                                UnresolvedItem(
                                    file_path=file_path,
                                    module_name=module_name,
                                    reason_code="dynamic_all_export",
                                    detail="__all__ 不是静态字符串序列，无法静态确定模块导出清单。",
                                    start_line=node.lineno,
                                    end_line=node.end_lineno or node.lineno,
                                    symbol_name="__all__",
                                )
                            )
                        else:
                            for export_name in static_all:
                                declared_all_exports.append(
                                    (export_name, node.lineno, node.end_lineno or node.lineno)
                                )
                        continue
                    const_fqn = f"{module_name}.{target.id}" if module_name else target.id
                    symbols.append(
                        SymbolInfo(
                            fqn=const_fqn,
                            name=target.id,
                            kind="constant",
                            module_name=module_name,
                            file_path=file_path,
                            start_line=node.lineno,
                            end_line=node.end_lineno or node.lineno,
                            parent_fqn=module_name,
                        )
                    )
                    local_defined_names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_name = node.target.id
            if target_name == "__all__":
                static_all = (
                    _extract_static_all_names(node.value)
                    if node.value is not None
                    else None
                )
                if static_all is None:
                    unresolved.append(
                        UnresolvedItem(
                            file_path=file_path,
                            module_name=module_name,
                            reason_code="dynamic_all_export",
                            detail="带类型注解的 __all__ 不是静态字符串序列，无法静态确定导出清单。",
                            start_line=node.lineno,
                            end_line=node.end_lineno or node.lineno,
                            symbol_name="__all__",
                        )
                    )
                else:
                    for export_name in static_all:
                        declared_all_exports.append(
                            (export_name, node.lineno, node.end_lineno or node.lineno)
                        )
            else:
                const_fqn = f"{module_name}.{target_name}" if module_name else target_name
                symbols.append(
                    SymbolInfo(
                        fqn=const_fqn,
                        name=target_name,
                        kind="constant",
                        module_name=module_name,
                        file_path=file_path,
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        parent_fqn=module_name,
                    )
                )
                local_defined_names.add(target_name)
        elif isinstance(node, ast.AugAssign) and isinstance(node.target, ast.Name):
            if node.target.id == "__all__":
                unresolved.append(
                    UnresolvedItem(
                        file_path=file_path,
                        module_name=module_name,
                        reason_code="dynamic_all_export",
                        detail="检测到对 __all__ 的增量修改，无法静态确定完整导出符号。",
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        symbol_name="__all__",
                    )
                )
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            func = node.value.func
            if (
                    isinstance(func, ast.Attribute)
                    and isinstance(func.value, ast.Name)
                    and func.value.id == "__all__"
            ):
                unresolved.append(
                    UnresolvedItem(
                        file_path=file_path,
                        module_name=module_name,
                        reason_code="dynamic_all_export",
                        detail=f"检测到动态修改 __all__ ({ast.unparse(node.value)})。",
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                        symbol_name="__all__",
                    )
                )
        elif isinstance(node, (ast.If, ast.Try, ast.For, ast.While, ast.With)):
            if _is_main_guard(node):
                continue
            has_nested_defs = any(
                isinstance(sub, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                for sub in ast.walk(node)
            )
            if has_nested_defs:
                unresolved.append(
                    UnresolvedItem(
                        file_path=file_path,
                        module_name=module_name,
                        reason_code="conditional_definition",
                        detail="顶层控制流（if/try 等）内部包含类或函数定义，静态分析无法确定运行时生效版本。",
                        start_line=node.lineno,
                        end_line=node.end_lineno or node.lineno,
                    )
                )
    # 核对 __all__ 中是否包含非本模块直接定义的符号（即跨模块重导出）
    for export_name, start_line, end_line in declared_all_exports:
        if export_name not in local_defined_names:
            unresolved.append(
                UnresolvedItem(
                    file_path=file_path,
                    module_name=module_name,
                    reason_code="all_reexport",
                    detail=f"__all__ 导出符号 '{export_name}' 非本模块直接定义，可能为跨模块重导出。",
                    start_line=start_line,
                    end_line=end_line,
                    symbol_name=export_name,
                )
            )
    unresolved.extend(_find_dynamic_setattr(tree, file_path, module_name))
    return symbols, unresolved


def extract_symbols(project_or_root: ProjectStructure | str | Path) -> SymbolTable:
    """从工程结构或工程根目录提取全部符号与 unresolved 边界记录。"""
    structure = (
        project_or_root
        if isinstance(project_or_root, ProjectStructure)
        else scan_project(project_or_root)
    )
    all_symbols: list[SymbolInfo] = []
    all_unresolved: list[UnresolvedItem] = _check_namespace_packages(structure)
    for file_path in structure.source_files:
        module_name = _module_name_from_path(file_path, structure.source_roots)
        file_symbols, file_unresolved = _extract_file_symbols(
            project_root=structure.project_root,
            file_path=file_path,
            module_name=module_name,
        )
        all_symbols.extend(file_symbols)
        all_unresolved.extend(file_unresolved)
    # 按统一排序键保持输出确定性，消除文件遍历或检查顺序干扰
    sorted_symbols = tuple(
        sorted(
            all_symbols,
            key=lambda item: (
                item.file_path.as_posix(),
                item.start_line,
                item.fqn,
            ),
        )
    )
    sorted_unresolved = tuple(
        sorted(
            all_unresolved,
            key=lambda item: (
                item.file_path.as_posix(),
                item.start_line or 0,
                item.reason_code,
                item.symbol_name or "",
            ),
        )
    )
    return SymbolTable(
        project_root=structure.project_root,
        symbols=sorted_symbols,
        unresolved=sorted_unresolved,
    )