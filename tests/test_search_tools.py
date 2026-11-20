"""统一工具返回契约 (base) 与文本搜索工具 (search_tools) 单元测试。"""

from pathlib import Path

from code_agent.tools import (
    ToolResult,
    adapt_code_snippet,
    adapt_symbol_lookup,
    lookup_symbol,
    read_file_snippet,
    search_text,
)


def test_search_text_literal_and_case_sensitivity(tmp_path: Path) -> None:
    """验证默认覆盖代码与 Markdown 文档的字面量搜索，以及大小写开关。"""
    (tmp_path / "src").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "TIMEOUT_SEC = 10\ndef run_task():\n    return TIMEOUT_SEC\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "spec.md").write_text(
        "# 超时规范\n默认 timeout_sec 约束为 10 秒。\n",
        encoding="utf-8",
    )

    sensitive = search_text(tmp_path, "TIMEOUT_SEC")
    assert sensitive.ok is True
    assert sensitive.tool_name == "search_text"
    assert sensitive.total_matches == 2
    assert sensitive.returned_count == 2
    assert sensitive.is_truncated is False
    assert sensitive.data is not None
    assert [m.file_path for m in sensitive.data.matches] == [
        "src/app.py",
        "src/app.py",
    ]

    insensitive = search_text(tmp_path, "TIMEOUT_SEC", case_sensitive=False)
    assert insensitive.ok is True
    assert insensitive.total_matches == 3
    assert insensitive.data is not None
    assert "docs/spec.md" in [m.file_path for m in insensitive.data.matches]


def test_search_text_regex_and_file_pattern_filtering(tmp_path: Path) -> None:
    """验证正则表达式搜索、** 零层/多层目录通配以及 exclude_patterns 过滤。"""
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "src" / "root_mod.py").write_text(
        "def init_v1(): pass\n", encoding="utf-8"
    )
    (tmp_path / "src" / "pkg" / "sub.py").write_text(
        "def init_v2(): pass\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "guide.md").write_text(
        "参考 init_v3 初始化流程\n", encoding="utf-8"
    )

    res_py = search_text(
        tmp_path,
        r"init_v\d+",
        file_pattern="src/**/*.py",
        is_regex=True,
    )
    assert res_py.ok is True
    assert res_py.total_matches == 2
    assert res_py.data is not None
    assert [m.file_path for m in res_py.data.matches] == [
        "src/pkg/sub.py",
        "src/root_mod.py",
    ]

    res_excluded = search_text(
        tmp_path,
        r"init_v\d+",
        is_regex=True,
        exclude_patterns=("docs", "*sub.py"),
    )
    assert res_excluded.ok is True
    assert res_excluded.total_matches == 1
    assert res_excluded.data is not None
    assert res_excluded.data.matches[0].file_path == "src/root_mod.py"


def test_search_text_ignores_dirs_binary_and_non_utf8(tmp_path: Path) -> None:
    """验证自动跳过忽略目录、二进制文件与非 UTF-8 文件且不抛异常。"""
    (tmp_path / ".git").mkdir()
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("target_token\n", encoding="utf-8")
    (tmp_path / "__pycache__" / "mod.pyc").write_text(
        "target_token\n", encoding="utf-8"
    )
    (tmp_path / "blob.bin").write_bytes(b"\x00\x01target_token\x02")
    (tmp_path / "gbk.txt").write_bytes("中文内容 target_token".encode("gbk"))
    (tmp_path / "valid.py").write_text("TOKEN = 'target_token'\n", encoding="utf-8")

    res = search_text(tmp_path, "target_token")
    assert res.ok is True
    assert res.total_matches == 1
    assert res.data is not None
    assert res.data.scanned_files == 1
    assert res.data.skipped_files == 2
    assert res.data.matches[0].file_path == "valid.py"


def test_search_text_per_file_cap_prevents_flooding(tmp_path: Path) -> None:
    """验证 max_per_file 能防止单个高频文件挤占全部返回预算。"""
    noisy_lines = [f"needle_{i} = {i}" for i in range(1, 10)]
    (tmp_path / "a_noisy.py").write_text(
        "\n".join(noisy_lines) + "\n", encoding="utf-8"
    )
    (tmp_path / "b_clean.py").write_text("x = 'needle_final'\n", encoding="utf-8")

    res = search_text(tmp_path, "needle", max_per_file=2, max_results=10)
    assert res.ok is True
    assert res.total_matches == 3
    assert res.data is not None
    assert res.data.files_with_matches == 2
    assert res.data.per_file_capped_files == ("a_noisy.py",)
    assert [m.file_path for m in res.data.matches] == [
        "a_noisy.py",
        "a_noisy.py",
        "b_clean.py",
    ]
    assert "a_noisy.py" in res.note


def test_search_text_pagination_by_results_and_chars(tmp_path: Path) -> None:
    """验证触发 max_results 或 max_chars 截断时可通过 next_cursor 无遗漏续读。"""
    (tmp_path / "a.py").write_text(
        "\n".join(f"item_val = {i:02d}" for i in range(1, 6)),
        encoding="utf-8",
    )
    (tmp_path / "b.py").write_text(
        "\n".join(f"item_val = {i:02d}" for i in range(6, 10)),
        encoding="utf-8",
    )

    all_collected: list[tuple[str, int]] = []
    offset = 0
    pages = 0

    while True:
        res = search_text(
            tmp_path,
            "item_val",
            max_per_file=10,
            max_results=3,
            max_chars=45,
            offset=offset,
        )
        assert res.ok is True
        assert res.data is not None
        assert res.returned_count == len(res.data.matches)
        assert res.returned_count >= 1
        all_collected.extend(
            (m.file_path, m.line_number) for m in res.data.matches
        )
        pages += 1
        if not res.is_truncated:
            assert res.remaining_count == 0
            assert res.next_cursor is None
            break
        assert res.next_cursor is not None
        assert res.remaining_count > 0
        offset = int(res.next_cursor["offset"])

    assert pages >= 3
    assert len(all_collected) == 9
    assert len(set(all_collected)) == 9


def test_search_text_single_long_line_and_progress_guarantee(tmp_path: Path) -> None:
    """验证超长单行触发行内截断，且单条超出 max_chars 时仍保底推进游标。"""
    long_line = "prefix_hit_" + ("x" * 120)
    (tmp_path / "one.py").write_text(
        f"{long_line}\nprefix_hit_2\n", encoding="utf-8"
    )

    page1 = search_text(
        tmp_path,
        "prefix_hit",
        max_line_chars=30,
        max_chars=25,
        offset=0,
    )
    assert page1.ok is True
    assert page1.returned_count == 1
    assert page1.is_truncated is True
    assert page1.remaining_count == 1
    assert page1.next_cursor == {"offset": 1}
    assert page1.data is not None
    assert page1.data.matches[0].line_truncated is True
    assert len(page1.data.matches[0].line_text) == 30
    assert len(page1.data.formatted_preview) <= 25

    page2 = search_text(
        tmp_path,
        "prefix_hit",
        max_line_chars=30,
        max_chars=25,
        offset=1,
    )
    assert page2.ok is True
    assert page2.is_truncated is False
    assert page2.data is not None
    assert page2.data.matches[0].line_number == 2


def test_search_text_structured_errors(tmp_path: Path) -> None:
    """验证异常输入均返回结构化 ToolResult(ok=False) 而非抛出异常。"""
    (tmp_path / "demo.py").write_text("hello = 1\n", encoding="utf-8")

    assert search_text(tmp_path, "").error_code == "empty_pattern"
    assert search_text(tmp_path, "[unclosed", is_regex=True).error_code == "invalid_regex"
    assert search_text(tmp_path, "hello", max_results=0).error_code == "invalid_budget"
    assert search_text(tmp_path, "hello", offset=-1).error_code == "invalid_offset"
    assert (
            search_text(tmp_path, "hello", file_pattern="../secret/*.py").error_code
            == "invalid_file_pattern"
    )
    assert (
            search_text(tmp_path, "hello", file_pattern="/abs/*.py").error_code
            == "invalid_file_pattern"
    )
    assert (
            search_text(tmp_path / "missing_dir", "hello").error_code
            == "invalid_project_root"
    )
    assert search_text(tmp_path, "hello", offset=5).error_code == "offset_out_of_range"


def test_adapt_w9_s1_results_to_tool_result(tmp_path: Path) -> None:
    """验证 W9 S1 读取与符号查找结果可无损适配为统一 ToolResult 契约。"""
    (tmp_path / "mod.py").write_text(
        "def alpha() -> int:\n    return 1\n\ndef beta() -> int:\n    return 2\n",
        encoding="utf-8",
    )

    snippet_ok = adapt_code_snippet(
        read_file_snippet(tmp_path, "mod.py", start_line=1, end_line=5, max_lines=2)
    )
    assert isinstance(snippet_ok, ToolResult)
    assert snippet_ok.ok is True
    assert snippet_ok.tool_name == "read_file_snippet"
    assert snippet_ok.is_truncated is True
    assert snippet_ok.returned_count == 2
    assert snippet_ok.remaining_count == 3
    assert snippet_ok.next_cursor == {"start_line": 3, "end_line": 5}

    snippet_err = adapt_code_snippet(read_file_snippet(tmp_path, "no_such.py"))
    assert snippet_err.ok is False
    assert snippet_err.error_code == "file_not_found"

    sym_ok = adapt_symbol_lookup(lookup_symbol("alpha", project_root=tmp_path))
    assert sym_ok.ok is True
    assert sym_ok.tool_name == "lookup_symbol"
    assert sym_ok.total_matches == 1
    assert sym_ok.returned_count == 1

    sym_err = adapt_symbol_lookup(lookup_symbol("   ", project_root=tmp_path))
    assert sym_err.ok is False
    assert sym_err.error_code == "empty_query"