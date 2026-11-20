"""代码探查工具箱公共出口。"""

from code_agent.tools.base import (
    ToolResult,
    adapt_code_snippet,
    adapt_symbol_lookup,
    tool_error,
)
from code_agent.tools.code_reader import CodeSnippetResult, read_file_snippet
from code_agent.tools.search_tools import SearchTextData, TextMatchItem, search_text
from code_agent.tools.symbol_tools import (
    SymbolLookupToolResult,
    SymbolMatchItem,
    lookup_symbol,
)
from code_agent.tools.reference_tools import (
    FindReferencesData,
    ReferenceConfidence,
    ReferenceItem,
    find_references,
)

from code_agent.tools.registry import (
    ToolBox,
    ToolSpec,
    get_tool_schemas,
    serialize_tool_result,
)

__all__ = [
    "CodeSnippetResult",
    "SearchTextData",
    "SymbolLookupToolResult",
    "SymbolMatchItem",
    "TextMatchItem",
    "ToolResult",
    "adapt_code_snippet",
    "adapt_symbol_lookup",
    "lookup_symbol",
    "read_file_snippet",
    "search_text",
    "tool_error",
    "FindReferencesData",
    "ReferenceConfidence",
    "ReferenceItem",
    "find_references",
    "ToolBox",
    "ToolSpec",
    "get_tool_schemas",
    "serialize_tool_result",
]