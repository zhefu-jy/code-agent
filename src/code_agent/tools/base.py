"""代码探查工具的统一返回契约、错误构造与 W9 S1 结果适配器。"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Generic, TypeVar

from code_agent.tools.code_reader import CodeSnippetResult
from code_agent.tools.symbol_tools import SymbolLookupToolResult
T = TypeVar("T")

@dataclass(frozen=True)
class ToolResult(Generic[T]):
    """
    所有代码探查工具的统一返回信封契约。
    Attributes:
        ok: 工具执行是否成功；参数校验失败、路径越界或运行时异常时为 False。
        tool_name: 工具名称标识（如 "read_file_snippet" / "lookup_symbol" / "search_text"）。
        data: 工具成功时的强类型业务载荷；失败时为 None。
        is_truncated: 本次输出是否因预算上限（条数、行数或字符数）而发生截断。
        total_matches: 总体命中条数或请求区间总行数；不适用或失败时为 None。
        returned_count: 本次实际返回的条目数或行数。
        remaining_count: 尚未返回的剩余条目数或行数。
        next_cursor: 发生截断时的下一页续读入参建议；未截断时为 None。
        note: 面向调用方/模型的补充提示（如证据边界说明、续读指引）。
        error_code: 失败时的机器可读错误码。
        error_message: 失败时的清晰错误说明。
    """
    ok: bool
    tool_name: str
    data: T | None = None
    is_truncated: bool = False
    total_matches: int | None = None
    returned_count: int = 0
    remaining_count: int = 0
    next_cursor: Mapping[str, str | int] | None = None
    note: str = ""
    error_code: str | None = None
    error_message: str | None = None

def tool_error(
        tool_name: str,
        error_code: str,
        error_message: str,
        note: str = "",
) -> ToolResult[T]:
    """构造统一的结构化工具失败响应，严禁向调度层抛出未捕获异常。"""
    return ToolResult(
        ok=False,
        tool_name=tool_name,
        data=None,
        is_truncated=False,
        total_matches=None,
        returned_count=0,
        remaining_count=0,
        next_cursor=None,
        note=note,
        error_code=error_code,
        error_message=error_message,
    )

def adapt_code_snippet(result: CodeSnippetResult) -> ToolResult[CodeSnippetResult]:
    """将 W9 S1 的代码片段读取结果无损适配为统一 ToolResult 契约。"""
    if not result.ok:
        return tool_error(
            tool_name="read_file_snippet",
            error_code=result.error_code or "read_failed",
            error_message=result.error_message or "读取代码片段失败。",
        )
    returned_lines = (
        result.returned_range[1] - result.returned_range[0] + 1
        if result.returned_range is not None
        else 0
    )
    requested_lines = (
        result.requested_range[1] - result.requested_range[0] + 1
        if result.requested_range is not None
        else 0
    )
    notes: list[str] = []
    if result.line_truncated:
        notes.append("首行内容超出单次字符预算，已执行行内截断。")
    if result.is_truncated and result.next_cursor is not None:
        notes.append(
            f"尚有 {result.remaining_lines} 行未返回，可传入 next_cursor 继续读取。"
        )
    return ToolResult(
        ok=True,
        tool_name="read_file_snippet",
        data=result,
        is_truncated=result.is_truncated,
        total_matches=requested_lines,
        returned_count=returned_lines,
        remaining_count=result.remaining_lines,
        next_cursor=result.next_cursor,
        note=" ".join(notes),
    )


def adapt_symbol_lookup(
        result: SymbolLookupToolResult,
) -> ToolResult[SymbolLookupToolResult]:
    """将 W9 S1 的符号查找结果无损适配为统一 ToolResult 契约。"""
    if not result.ok:
        return tool_error(
            tool_name="lookup_symbol",
            error_code=result.error_code or "lookup_failed",
            error_message=result.error_message or "符号查找失败。",
        )
    return ToolResult(
        ok=True,
        tool_name="lookup_symbol",
        data=result,
        is_truncated=result.is_truncated,
        total_matches=result.total_matches,
        returned_count=len(result.items),
        remaining_count=result.remaining_count,
        next_cursor=None,
        note=result.note,
    )