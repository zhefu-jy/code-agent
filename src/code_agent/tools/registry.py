"""代码探查工具的 Tool Calling JSON Schema 定义、受控分发与序列化层。"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields, is_dataclass
from enum import Enum
import json
from pathlib import Path
from typing import Any

from code_agent.analyzer.symbol_index import SymbolIndex, build_symbol_index
from code_agent.tools.base import (
    ToolResult,
    adapt_code_snippet,
    adapt_symbol_lookup,
    tool_error,
)
from code_agent.tools.code_reader import read_file_snippet
from code_agent.tools.reference_tools import find_references
from code_agent.tools.search_tools import search_text
from code_agent.tools.symbol_tools import lookup_symbol


@dataclass(frozen=True)
class ToolSpec:
    """单个工具的元数据契约与 JSON Schema 声明。"""

    name: str
    description: str
    parameters: dict[str, Any]
    required: tuple[str, ...]

    def to_openai_schema(self) -> dict[str, Any]:
        """导出兼容 OpenAI / DeepSeek / LangChain bind_tools 的标准 Function Schema。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": list(self.required),
                    "additionalProperties": False,
                },
            },
        }


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="read_file_snippet",
        description=(
            "安全读取工程内文本文件的指定行号区间（1-based，含起止行）。"
            "当内容超出单次行数或字符预算时会返回 is_truncated=True 与 next_cursor，"
            "调用方可传入 next_cursor 继续分页读取，用于核实完整函数实现或文档内容。"
        ),
        parameters={
            "file_path": {
                "type": "string",
                "description": "相对于工程根目录的 POSIX 路径（如 'src/app.py' 或 'README.md'）。",
            },
            "start_line": {
                "type": "integer",
                "minimum": 1,
                "description": "起始物理行号（1-based，默认为 1）。",
            },
            "end_line": {
                "type": "integer",
                "minimum": 1,
                "description": "结束物理行号（1-based，可选；不传则默认尝试读到文件末尾并受预算控制）。",
            },
            "max_lines": {
                "type": "integer",
                "minimum": 1,
                "description": "单次最多返回的行数预算（默认 80）。",
            },
            "max_chars": {
                "type": "integer",
                "minimum": 1,
                "description": "单次最多返回的字符数预算（默认 1500）。",
            },
        },
        required=("file_path",),
    ),
    ToolSpec(
        name="lookup_symbol",
        description=(
            "在静态 AST 符号索引中查找 Python 类、函数、方法或常量的定义位置、参数签名与基类信息。"
            "支持全限定名（FQN）、精确短名与关键字子串查找；注意结果仅含定义坐标与签名，不含函数体实现。"
        ),
        parameters={
            "name": {
                "type": "string",
                "description": "待查找的符号名、全限定名（如 'BuildEngine.build'）或名称片段。",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "description": "单次最多返回的符号条目数（默认 20）。",
            },
        },
        required=("name",),
    ),
    ToolSpec(
        name="search_text",
        description=(
            "在工程内所有 UTF-8 文本文件（含源码、Markdown 设计文档与配置）中执行受控字面量或正则搜索。"
            "内置单文件条数上限、总条数与总字符预算，截断时提供 next_cursor(offset) 供续读。"
        ),
        parameters={
            "pattern": {
                "type": "string",
                "description": "待搜索的关键字或正则表达式，不能为空。",
            },
            "file_pattern": {
                "type": "string",
                "description": "可选的路径/文件名过滤模式（如 '*.py'、'docs/*.md'、'src/**/*.py'）。",
            },
            "is_regex": {
                "type": "boolean",
                "description": "是否将 pattern 作为正则表达式匹配，默认 false（字面量匹配）。",
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "是否区分大小写，默认 true。",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "description": "单页最多返回的匹配条目数（默认 30）。",
            },
            "max_per_file": {
                "type": "integer",
                "minimum": 1,
                "description": "单个文件最多采集的匹配条数，防止单文件刷屏（默认 5）。",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "分页续读偏移量（默认 0，翻页时传入上次返回的 next_cursor.offset）。",
            },
        },
        required=("pattern",),
    ),
    ToolSpec(
        name="find_references",
        description=(
            "结合静态符号索引与 AST 扫描查找指定 Python 符号的候选引用点，"
            "按可信度分级为 SAME_FILE_CALL / EXPLICIT_IMPORT_CALL / ATTRIBUTE_ACCESS / UNRESOLVED，"
            "并区分源码（source）与测试（test）范围。注意：受限于 Python 动态特性，不承诺找全所有调用。"
        ),
        parameters={
            "symbol_name": {
                "type": "string",
                "description": "待查找引用的符号短名、Class.method（如 'BuildEngine.discover_documents'）或完整 FQN。",
            },
            "include_tests": {
                "type": "boolean",
                "description": "是否同时扫描测试目录中的引用，默认 true。",
            },
            "include_attribute_access": {
                "type": "boolean",
                "description": "是否保留未绑定类型的同名属性访问候选（ATTRIBUTE_ACCESS），默认 true。",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "description": "单页最多返回的引用条目数（默认 30）。",
            },
            "offset": {
                "type": "integer",
                "minimum": 0,
                "description": "分页续读偏移量（默认 0）。",
            },
        },
        required=("symbol_name",),
    ),
)

_SPEC_BY_NAME: dict[str, ToolSpec] = {spec.name: spec for spec in TOOL_SPECS}


def get_tool_schemas() -> list[dict[str, Any]]:
    """返回全部代码探查工具的标准 Tool Calling JSON Schema 列表。"""
    return [spec.to_openai_schema() for spec in TOOL_SPECS]


def _check_arg_type(value: Any, expected_type: str) -> bool:
    """校验 JSON 参数值是否符合 Schema 类型声明（显式排除 bool 冒充 int）。"""
    if expected_type == "string":
        return isinstance(value, str)
    if expected_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected_type == "boolean":
        return isinstance(value, bool)
    return False


def _validate_arguments(spec: ToolSpec, raw_args: Mapping[str, Any]) -> str | None:
    """校验入参必填项、多余字段与基础类型，全部通过返回 None，否则返回错误信息。"""
    for req_key in spec.required:
        if req_key not in raw_args:
            return f"缺少必填参数: {req_key}"

    for key, val in raw_args.items():
        if key not in spec.parameters:
            return f"存在未声明的额外参数: {key}"
        prop_schema = spec.parameters[key]
        expected_type = str(prop_schema["type"])
        if not _check_arg_type(val, expected_type):
            return f"参数 {key} 类型错误，期望 {expected_type}。"
        if expected_type == "integer" and "minimum" in prop_schema:
            min_val = int(prop_schema["minimum"])
            if int(val) < min_val:
                return f"参数 {key} 不能小于 {min_val}，当前为 {val}。"

    return None


def _to_json_compatible(value: Any) -> Any:
    """将 dataclass / Path / Enum 等内部对象递归转为可 JSON 序列化结构。"""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return value.as_posix()
    if is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: _to_json_compatible(getattr(value, f.name))
            for f in fields(value)
        }
    if isinstance(value, Mapping):
        return {str(k): _to_json_compatible(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_json_compatible(item) for item in value]
    return value


def serialize_tool_result(result: ToolResult[Any]) -> str:
    """将统一 ToolResult 序列化为紧凑清晰的 JSON 字符串，便于作为 ToolMessage 返回模型。"""
    payload = _to_json_compatible(result)
    return json.dumps(payload, ensure_ascii=False, indent=2)


class ToolBox:
    """
    面向调查循环与人工实验的统一工具执行器。

    负责安全绑定 project_root、懒加载并复用 SymbolIndex、校验模型传参，
    并将全部工具的返回统一收敛为 ToolResult 契约。
    """

    def __init__(
            self,
            project_root: str | Path,
            symbol_index: SymbolIndex | None = None,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self._symbol_index = symbol_index
        self._handlers: dict[str, Callable[[dict[str, Any]], ToolResult[Any]]] = {
            "read_file_snippet": self._exec_read_file_snippet,
            "lookup_symbol": self._exec_lookup_symbol,
            "search_text": self._exec_search_text,
            "find_references": self._exec_find_references,
        }

    def get_symbol_index(self) -> SymbolIndex:
        """懒加载并缓存工程静态符号索引，避免多步探查重复扫描。"""
        if self._symbol_index is None:
            self._symbol_index = build_symbol_index(self.project_root)
        return self._symbol_index

    def _exec_read_file_snippet(self, args: dict[str, Any]) -> ToolResult[Any]:
        res = read_file_snippet(self.project_root, **args)
        return adapt_code_snippet(res)

    def _exec_lookup_symbol(self, args: dict[str, Any]) -> ToolResult[Any]:
        res = lookup_symbol(
            project_root=self.project_root,
            symbol_index=self.get_symbol_index(),
            **args,
        )
        return adapt_symbol_lookup(res)

    def _exec_search_text(self, args: dict[str, Any]) -> ToolResult[Any]:
        return search_text(self.project_root, **args)

    def _exec_find_references(self, args: dict[str, Any]) -> ToolResult[Any]:
        return find_references(
            project_root=self.project_root,
            symbol_index=self.get_symbol_index(),
            **args,
        )

    def dispatch(
            self,
            tool_name: str,
            arguments: Mapping[str, Any] | None = None,
    ) -> ToolResult[Any]:
        """校验参数并调度执行指定工具，任何异常均转为结构化 ToolResult 错误。"""
        spec = _SPEC_BY_NAME.get(tool_name)
        if spec is None:
            return tool_error(
                tool_name,
                "unknown_tool",
                f"未知工具 {tool_name!r}，可用工具：{', '.join(_SPEC_BY_NAME)}",
            )

        raw_args = dict(arguments) if arguments is not None else {}
        err_msg = _validate_arguments(spec, raw_args)
        if err_msg is not None:
            return tool_error(tool_name, "invalid_arguments", err_msg)

        handler = self._handlers[tool_name]
        try:
            return handler(raw_args)
        except Exception as exc:
            return tool_error(
                tool_name,
                "tool_execution_error",
                f"工具执行异常: {exc}",
            )