"""基于 W8 静态符号索引的代码符号定义与签名查找工具。"""
from dataclasses import dataclass
from pathlib import Path
from code_agent.analyzer.symbol_index import SymbolIndex, build_symbol_index
DEFAULT_MAX_RESULTS = 20
DEFAULT_MAX_UNRESOLVED_HINTS = 5

@dataclass(frozen=True)
class SymbolMatchItem:
    """
    面向下游调用方/Agent 的单条符号匹配摘要。
    Attributes:
        fqn: 符号全限定名（如 "minisite.engine.BuildEngine.build"）。
        name: 符号短名称。
        kind: 符号类别（module / class / function / method / constant）。
        module_name: 所属模块点分全名。
        file_path: 相对于工程根目录的 POSIX 路径（可直接传给 read_file_snippet）。
        start_line: 定义起始物理行号（1-based）。
        end_line: 定义结束物理行号（1-based）。
        signature: 函数或方法的参数与返回值签名；非函数/方法时为 None。
        bases: 类的基类表达式文本元组；非类符号为空元组 ()。
        parent_fqn: 直接父级作用域全限定名；顶层模块自身为 None。
    """
    fqn: str
    name: str
    kind: str
    module_name: str
    file_path: str
    start_line: int
    end_line: int
    signature: str | None
    bases: tuple[str, ...]
    parent_fqn: str | None

@dataclass(frozen=True)
class SymbolLookupToolResult:
    """
    符号查找工具的统一结构化返回契约。
    Attributes:
        ok: 工具执行是否成功（查询词为空或工程目录无效时为 False）。
        query: 去除首尾空白后的查询字符串。
        match_mode: 命中精度（exact_fqn / exact_name / substring / none）。
        total_matches: 本次查询在索引中命中的总记录数。
        items: 截断后实际返回的符号摘要元组。
        is_truncated: 命中数量是否超过 max_results 而发生截断。
        remaining_count: 未返回的剩余匹配条数。
        unresolved_hints: 与查询词相关的静态分析未解析边界提示（如动态绑定、语法错误等）。
        note: 证据使用边界提示（仅位置与签名，查看实现请调用 read_file_snippet）。
        error_code: 失败时的机器可读错误码。
        error_message: 失败时的详细错误说明。
    """
    ok: bool
    query: str
    match_mode: str
    total_matches: int
    items: tuple[SymbolMatchItem, ...]
    is_truncated: bool
    remaining_count: int
    unresolved_hints: tuple[str, ...]
    note: str
    error_code: str | None = None
    error_message: str | None = None

def _error_result(query: str, error_code: str, error_message: str) -> SymbolLookupToolResult:
    """构造符号查找失败时的结构化错误响应。"""
    return SymbolLookupToolResult(
        ok=False,
        query=query,
        match_mode="none",
        total_matches=0,
        items=(),
        is_truncated=False,
        remaining_count=0,
        unresolved_hints=(),
        note="",
        error_code=error_code,
        error_message=error_message,
    )

def _collect_unresolved_hints(index: SymbolIndex, query: str) -> tuple[str, ...]:
    """收集与查询词相关的静态分析 unresolved 记录，诚实提示分析盲区。"""
    needle = query.lower()
    # 若查询类似 "a.b.foo"，同时检查末段短名以防漏掉动态导出或语法错提示
    short_needle = needle.rsplit(".", 1)[-1]
    hints: list[str] = []
    for item in index.symbol_table.unresolved:
        haystack = " ".join(
            part for part in (item.symbol_name or "", item.module_name, item.detail) if part
        ).lower()
        if needle in haystack or (short_needle and short_needle in haystack):
            loc = item.file_path.as_posix()
            if item.start_line is not None:
                loc = f"{loc}:{item.start_line}"
            hints.append(f"[{item.reason_code}] {loc} - {item.detail}")
            if len(hints) >= DEFAULT_MAX_UNRESOLVED_HINTS:
                break
    return tuple(hints)


def lookup_symbol(
        name: str,
        project_root: str | Path | None = None,
        symbol_index: SymbolIndex | None = None,
        max_results: int = DEFAULT_MAX_RESULTS,
) -> SymbolLookupToolResult:
    """
    在静态符号索引中查找指定符号的定义位置与签名。
    优先复用传入的 symbol_index；若未传入，则基于 project_root 现场构建索引。
    Args:
        name: 待查询的符号名、全限定名（FQN）或名称关键字片段。
        project_root: 工程根目录路径（未传入 symbol_index 时必填）。
        symbol_index: 可选的已构建 SymbolIndex 实例，用于多步探查时免重复扫描。
        max_results: 单次最多返回匹配条数，防止宽泛子串查询刷屏。
    Returns:
        SymbolLookupToolResult: 包含命中等级、位置签名摘要、截断信息及未解析盲区提示。
    """
    cleaned = name.strip()
    if not cleaned:
        return _error_result(name, "empty_query", "符号查询名称不能为空。")
    if max_results < 1:
        return _error_result(cleaned, "invalid_budget", "max_results 必须大于等于 1。")
    active_index = symbol_index
    if active_index is None:
        if project_root is None:
            return _error_result(
                cleaned,
                "missing_project_root",
                "未传入 symbol_index 时必须提供 project_root。",
            )
        try:
            active_index = build_symbol_index(project_root)
        except (NotADirectoryError, OSError) as exc:
            return _error_result(cleaned, "invalid_project_root", str(exc))
    lookup_res = active_index.lookup_symbol(cleaned)
    total_matches = len(lookup_res.matches)
    sliced = lookup_res.matches[:max_results]
    is_truncated = total_matches > max_results
    remaining_count = max(0, total_matches - len(sliced))
    items = tuple(
        SymbolMatchItem(
            fqn=sym.fqn,
            name=sym.name,
            kind=sym.kind,
            module_name=sym.module_name,
            file_path=sym.file_path.as_posix(),
            start_line=sym.start_line,
            end_line=sym.end_line,
            signature=sym.signature,
            bases=sym.bases,
            parent_fqn=sym.parent_fqn,
        )
        for sym in sliced
    )
    unresolved_hints = _collect_unresolved_hints(active_index, cleaned)
    if total_matches == 0:
        note = "未在静态符号表中找到匹配定义；若怀疑存在动态定义或导入重导出，请结合文本搜索或直接读取文件核实。"
    else:
        note = "仅包含符号定义位置与签名，不能替代函数体实现核实；如需查看实现细节请调用 read_file_snippet。"
    return SymbolLookupToolResult(
        ok=True,
        query=cleaned,
        match_mode=lookup_res.match_mode,
        total_matches=total_matches,
        items=items,
        is_truncated=is_truncated,
        remaining_count=remaining_count,
        unresolved_hints=unresolved_hints,
        note=note,
    )