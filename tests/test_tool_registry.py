"""Tool Calling Schema 与统一工具分发器 (registry) 单元测试。"""

import json
from pathlib import Path

from code_agent.tools import (
    ToolBox,
    ToolResult,
    get_tool_schemas,
    serialize_tool_result,
)


def test_get_tool_schemas_structure() -> None:
    """验证导出的 JSON Schema 符合严格 Function Calling 规范且不暴露宿主内部参数。"""
    schemas = get_tool_schemas()
    names = [s["function"]["name"] for s in schemas]
    assert names == [
        "read_file_snippet",
        "lookup_symbol",
        "search_text",
        "find_references",
    ]

    for schema in schemas:
        assert schema["type"] == "function"
        fn = schema["function"]
        assert isinstance(fn["description"], str) and fn["description"]
        params = fn["parameters"]
        assert params["type"] == "object"
        assert params["additionalProperties"] is False
        props = params["properties"]
        assert "project_root" not in props
        assert "symbol_index" not in props
        for req_name in params["required"]:
            assert req_name in props


def test_toolbox_dispatch_all_tools_unified(tmp_path: Path) -> None:
    """验证 ToolBox 能统一调度 4 个工具、复用同一 SymbolIndex 且返回 ToolResult。"""
    (tmp_path / "demo.py").write_text(
        "\n".join(
            [
                "def greet(name: str) -> str:",
                "    return f'hi {name}'",
                "",
                "msg = greet('world')",
            ]
        )
        + "\n",
        encoding="utf-8",
        )

    box = ToolBox(tmp_path)
    idx_first = box.get_symbol_index()
    idx_second = box.get_symbol_index()
    assert idx_first is idx_second

    r_search = box.dispatch("search_text", {"pattern": "greet"})
    r_lookup = box.dispatch("lookup_symbol", {"name": "greet"})
    r_refs = box.dispatch("find_references", {"symbol_name": "greet"})
    r_read = box.dispatch(
        "read_file_snippet",
        {"file_path": "demo.py", "start_line": 1, "end_line": 2},
    )

    for res in (r_search, r_lookup, r_refs, r_read):
        assert isinstance(res, ToolResult)
        assert res.ok is True
    assert r_refs.total_matches == 1


def test_toolbox_argument_validation_and_error_isolation(tmp_path: Path) -> None:
    """验证未知工具、缺参、多余参数、bool 冒充 int 及路径逃逸均被结构化拦截。"""
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    box = ToolBox(tmp_path)

    assert box.dispatch("no_such_tool", {}).error_code == "unknown_tool"
    assert box.dispatch("read_file_snippet", {}).error_code == "invalid_arguments"
    assert (
            box.dispatch(
                "lookup_symbol", {"name": "x", "extra_field": 123}
            ).error_code
            == "invalid_arguments"
    )
    assert (
            box.dispatch(
                "read_file_snippet", {"file_path": "a.py", "start_line": True}
            ).error_code
            == "invalid_arguments"
    )
    assert (
            box.dispatch(
                "search_text", {"pattern": "x", "offset": -1}
            ).error_code
            == "invalid_arguments"
    )
    assert (
            box.dispatch(
                "read_file_snippet", {"file_path": "../outside.py"}
            ).error_code
            == "path_outside_root"
    )


def test_serialize_tool_result_json_roundtrip(tmp_path: Path) -> None:
    """验证含 Enum / Path / dataclass 的 ToolResult 能无损序列化为合法 JSON。"""
    (tmp_path / "m.py").write_text(
        "def run() -> int:\n    return 1\n\nval = run()\n",
        encoding="utf-8",
    )
    box = ToolBox(tmp_path)
    res = box.dispatch("find_references", {"symbol_name": "run"})

    raw_json = serialize_tool_result(res)
    parsed = json.loads(raw_json)

    assert parsed["ok"] is True
    assert parsed["tool_name"] == "find_references"
    assert parsed["total_matches"] == 1
    assert parsed["data"]["references"][0]["confidence"] == "SAME_FILE_CALL"
    assert parsed["data"]["target_definitions"][0]["file_path"] == "m.py"