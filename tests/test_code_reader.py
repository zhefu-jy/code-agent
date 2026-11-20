"""代码片段分页读取工具 (code_reader) 单元测试。"""

from pathlib import Path
from code_agent.tools.code_reader import read_file_snippet


def test_read_file_snippet_full_within_budget(tmp_path: Path) -> None:
    """验证预算充足时一次性完整返回文件及物理行号前缀。"""
    target = tmp_path / "src" / "demo.py"
    target.parent.mkdir(parents=True)
    target.write_text("alpha = 1\nbeta = 2\ngamma = 3\n", encoding="utf-8")

    res = read_file_snippet(tmp_path, "src/demo.py")

    assert res.ok is True
    assert res.file_path == "src/demo.py"
    assert res.total_lines == 3
    assert res.requested_range == (1, 3)
    assert res.returned_range == (1, 3)
    assert res.is_truncated is False
    assert res.remaining_lines == 0
    assert res.next_cursor is None
    assert res.content == "alpha = 1\nbeta = 2\ngamma = 3"
    assert "  1 | alpha = 1" in res.numbered_content
    assert "  3 | gamma = 3" in res.numbered_content


def test_read_file_snippet_pagination_by_max_lines(tmp_path: Path) -> None:
    """验证超出 max_lines 时触发截断，并通过 next_cursor 连续续读完整还原。"""
    lines = [f"line_{i} = {i}" for i in range(1, 11)]
    (tmp_path / "long.py").write_text("\n".join(lines) + "\n", encoding="utf-8")

    collected_parts: list[str] = []
    start = 1
    end: int | None = 9
    pages = 0

    while True:
        res = read_file_snippet(
            tmp_path,
            "long.py",
            start_line=start,
            end_line=end,
            max_lines=4,
            max_chars=5000,
        )
        assert res.ok is True
        collected_parts.append(res.content)
        pages += 1
        if not res.is_truncated:
            assert res.remaining_lines == 0
            assert res.next_cursor is None
            break
        assert res.next_cursor is not None
        assert res.remaining_lines > 0
        start = res.next_cursor["start_line"]
        end = res.next_cursor["end_line"]

    # 第 1~9 行按每页 4 行分 3 页：4 + 4 + 1
    assert pages == 3
    assert "\n".join(collected_parts) == "\n".join(lines[:9])


def test_read_file_snippet_pagination_by_max_chars_and_long_first_line(tmp_path: Path) -> None:
    """验证字符数预算截断，以及首行超长时至少推进 1 行防止死循环。"""
    long_line = "x" * 50
    (tmp_path / "wide.py").write_text(
        f"{long_line}\nshort_1\nshort_2\n",
        encoding="utf-8",
    )

    # 场景 A：首行本身超过 max_chars=20，必须截断该行但游标推进到第 2 行
    first = read_file_snippet(tmp_path, "wide.py", start_line=1, max_lines=10, max_chars=20)
    assert first.ok is True
    assert first.line_truncated is True
    assert first.returned_range == (1, 1)
    assert first.content == "x" * 20
    assert first.is_truncated is True
    assert first.remaining_lines == 2
    assert first.next_cursor == {"start_line": 2, "end_line": 3}

    # 场景 B：从第 2 行续读，后续两行在 max_chars=20 内一次读完
    assert first.next_cursor is not None
    second = read_file_snippet(
        tmp_path,
        "wide.py",
        start_line=first.next_cursor["start_line"],
        end_line=first.next_cursor["end_line"],
        max_lines=10,
        max_chars=20,
    )
    assert second.ok is True
    assert second.line_truncated is False
    assert second.returned_range == (2, 3)
    assert second.is_truncated is False
    assert second.content == "short_1\nshort_2"


def test_read_file_snippet_clamp_end_line_and_empty_file(tmp_path: Path) -> None:
    """验证 end_line 超出文件总行数的夹断行为与空文件边界。"""
    (tmp_path / "short.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    clamped = read_file_snippet(tmp_path, "short.py", start_line=1, end_line=999)
    assert clamped.ok is True
    assert clamped.requested_range == (1, 2)
    assert clamped.returned_range == (1, 2)
    assert clamped.is_truncated is False

    (tmp_path / "empty.py").write_text("", encoding="utf-8")
    empty_ok = read_file_snippet(tmp_path, "empty.py", start_line=1)
    assert empty_ok.ok is True
    assert empty_ok.total_lines == 0
    assert empty_ok.returned_range is None
    assert empty_ok.content == ""

    empty_err = read_file_snippet(tmp_path, "empty.py", start_line=2)
    assert empty_err.ok is False
    assert empty_err.error_code == "start_line_out_of_range"


def test_read_file_snippet_path_sandbox_and_missing(tmp_path: Path) -> None:
    """验证目录逃逸、外部绝对路径、文件不存在与目录误传入的拦截。"""
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text("top_secret", encoding="utf-8")
    (repo / "sub").mkdir()

    escape_rel = read_file_snippet(repo, "../secret.txt")
    assert escape_rel.ok is False
    assert escape_rel.error_code == "path_outside_root"

    escape_abs = read_file_snippet(repo, secret.resolve())
    assert escape_abs.ok is False
    assert escape_abs.error_code == "path_outside_root"

    missing = read_file_snippet(repo, "not_exists.py")
    assert missing.ok is False
    assert missing.error_code == "file_not_found"

    is_dir = read_file_snippet(repo, "sub")
    assert is_dir.ok is False
    assert is_dir.error_code == "not_a_file"


def test_read_file_snippet_invalid_ranges_and_decode_error(tmp_path: Path) -> None:
    """验证非法行号区间、越界起点与非 UTF-8 文件的结构化报错。"""
    (tmp_path / "sample.py").write_text("one\ntwo\n", encoding="utf-8")
    (tmp_path / "binary.dat").write_bytes(b"\xff\xfe\x00\x01")

    bad_start = read_file_snippet(tmp_path, "sample.py", start_line=0)
    assert bad_start.ok is False
    assert bad_start.error_code == "invalid_line_range"

    bad_order = read_file_snippet(tmp_path, "sample.py", start_line=2, end_line=1)
    assert bad_order.ok is False
    assert bad_order.error_code == "invalid_line_range"

    out_of_range = read_file_snippet(tmp_path, "sample.py", start_line=5)
    assert out_of_range.ok is False
    assert out_of_range.error_code == "start_line_out_of_range"

    decode_err = read_file_snippet(tmp_path, "binary.dat")
    assert decode_err.ok is False
    assert decode_err.error_code == "decode_error"