"""支持安全边界校验与分页续读的代码片段读取工具。"""
from dataclasses import dataclass
from pathlib import Path
DEFAULT_MAX_LINES = 80
DEFAULT_MAX_CHARS = 1500

@dataclass(frozen=True)
class CodeSnippetResult:
    """
    代码片段读取结果契约。
    Attributes:
        ok: 本次读取是否成功（参数越界、路径逃逸、IO/编码异常时为 False）。
        file_path: 相对于 project_root 的 POSIX 风格文件路径（异常时保留输入值）。
        total_lines: 目标文件的物理总行数（读取失败时为 0）。
        requested_range: 经过边界夹断后的目标请求区间 (start_line, effective_end)。
        returned_range: 本次实际返回的 1-based 行号区间 (actual_start, actual_end)；空文件或失败时为 None。
        is_truncated: 是否因超过单次预算（行数或字符数）而发生截断。
        remaining_lines: 在目标请求区间内尚未返回的剩余行数。
        next_cursor: 发生截断时的下一页建议参数，如 {"start_line": 81, "end_line": 120}；未截断时为 None。
        content: 实际返回区间的原始文本内容（不含行号前缀）。
        numbered_content: 带 1-based 物理行号前缀的可读文本，便于模型准确引用行号。
        line_truncated: 首行超过 max_chars 时是否发生了行内字符截断。
        error_code: 失败时的机器可读错误码（如 path_outside_root / file_not_found / invalid_line_range）。
        error_message: 面向调用方或模型的清晰错误说明。
    """
    ok: bool
    file_path: str
    total_lines: int
    requested_range: tuple[int, int] | None
    returned_range: tuple[int, int] | None
    is_truncated: bool
    remaining_lines: int
    next_cursor: dict[str, int] | None
    content: str
    numbered_content: str
    line_truncated: bool = False
    error_code: str | None = None
    error_message: str | None = None

def _error_result(file_path: str, error_code: str, error_message: str, total_lines: int = 0) -> CodeSnippetResult:
    """构造统一的结构化错误响应，避免在工具层向外抛出未捕获异常。"""
    return CodeSnippetResult(
        ok=False,
        file_path=file_path,
        total_lines=total_lines,
        requested_range=None,
        returned_range=None,
        is_truncated=False,
        remaining_lines=0,
        next_cursor=None,
        content="",
        numbered_content="",
        error_code=error_code,
        error_message=error_message,
    )

def _format_numbered_lines(lines: list[str], start_line: int, total_lines: int) -> str:
    """为代码行添加右对齐的 1-based 物理行号前缀。"""
    width = max(len(str(total_lines)), 3)
    return "\n".join(
        f"{start_line + idx:>{width}} | {line}"
        for idx, line in enumerate(lines)
    )

def read_file_snippet(
        project_root: str | Path,
        file_path: str | Path,
        start_line: int = 1,
        end_line: int | None = None,
        max_lines: int = DEFAULT_MAX_LINES,
        max_chars: int = DEFAULT_MAX_CHARS,
) -> CodeSnippetResult:
    """
    安全读取工程内指定文件的行号区间，超出预算时返回分页续读游标。
    Args:
        project_root: 工程根目录路径。
        file_path: 待读取文件相对路径（或位于 project_root 内的路径）。
        start_line: 起始物理行号（1-based，默认 1）。
        end_line: 结束物理行号（1-based，含边界；为 None 时默认读到文件末尾）。
        max_lines: 单次最多返回行数预算。
        max_chars: 单次最多返回字符数预算。
    Returns:
        CodeSnippetResult: 包含源码内容、截断状态、剩余行数与续读游标的结构化结果。
    """

    raw_file_str = str(file_path)

    # 1. 基础预算与行号入参校验
    if max_lines < 1 or max_chars < 1:
        return _error_result(raw_file_str, "invalid_budget", "max_lines 和 max_chars 必须大于等于 1。")
    if start_line < 1:
        return _error_result(raw_file_str, "invalid_line_range", f"start_line 必须 >= 1，当前为 {start_line}。")
    if end_line is not None and end_line < start_line:
        return _error_result(
            raw_file_str,
            "invalid_line_range",
            f"end_line ({end_line}) 不能小于 start_line ({start_line})。",
        )

    # 2. 路径沙箱解析与文件可用性检查
    root = Path(project_root).resolve()
    if not root.exists() or not root.is_dir():
        return _error_result(raw_file_str, "invalid_project_root", f"工程根目录不存在或不是目录：{root}")

    candidate = Path(file_path)
    target = candidate.resolve() if candidate.is_absolute() else (root / candidate).resolve()
    if not target.is_relative_to(root):
        return _error_result(
            raw_file_str,
            "path_outside_root",
            f"拒绝访问工程根目录之外的路径：{raw_file_str}",
        )

    rel_posix = target.relative_to(root).as_posix()
    if not target.exists():
        return _error_result(rel_posix, "file_not_found", f"文件不存在：{rel_posix}")
    if not target.is_file():
        return _error_result(rel_posix, "not_a_file", f"目标路径不是普通文件：{rel_posix}")

    try:
        raw_text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return _error_result(rel_posix, "decode_error", f"文件不是有效的 UTF-8 文本，无法读取：{rel_posix}")
    except OSError as exc:
        return _error_result(rel_posix, "read_error", f"读取文件失败 ({rel_posix})：{exc}")

    # 3. 切分物理行并处理空文件与上界越界
    all_lines = raw_text.splitlines()
    total_lines = len(all_lines)
    if total_lines == 0:
        if start_line > 1:
            return _error_result(
                rel_posix,
                "start_line_out_of_range",
                f"文件 {rel_posix} 为空（0 行），无法从第 {start_line} 行读取。",
                total_lines=0,
            )
        return CodeSnippetResult(
            ok=True,
            file_path=rel_posix,
            total_lines=0,
            requested_range=None,
            returned_range=None,
            is_truncated=False,
            remaining_lines=0,
            next_cursor=None,
            content="",
            numbered_content="",
        )

    if start_line > total_lines:
        return _error_result(
            rel_posix,
            "start_line_out_of_range",
            f"起始行 {start_line} 超出文件 {rel_posix} 总行数 ({total_lines})。",
            total_lines=total_lines,
        )
    effective_end = total_lines if end_line is None else min(end_line, total_lines)

    # 4. 按行数与字符数双预算截取，确保至少推进 1 行防止死循环
    selected_lines: list[str] = []
    used_chars = 0
    line_truncated = False

    for line_no in range(start_line, effective_end + 1):
        if len(selected_lines) >= max_lines:
            break
        line_text = all_lines[line_no - 1]
        # 计算加入本行（含换行符分隔）后的总字符数
        added_chars = len(line_text) + (1 if selected_lines else 0)
        if used_chars + added_chars > max_chars:
            if not selected_lines:
                # 首行本身超出 max_chars：截断保留首行前 max_chars 字符，确保游标推进
                selected_lines.append(line_text[:max_chars])
                line_truncated = True
            break
        selected_lines.append(line_text)
        used_chars += added_chars
    actual_end = start_line + len(selected_lines) - 1
    remaining_lines = effective_end - actual_end
    is_truncated = remaining_lines > 0
    next_cursor = (
        {"start_line": actual_end + 1, "end_line": effective_end}
        if is_truncated
        else None
    )
    content = "\n".join(selected_lines)
    numbered_content = _format_numbered_lines(selected_lines, start_line, total_lines)
    return CodeSnippetResult(
        ok=True,
        file_path=rel_posix,
        total_lines=total_lines,
        requested_range=(start_line, effective_end),
        returned_range=(start_line, actual_end),
        is_truncated=is_truncated,
        remaining_lines=remaining_lines,
        next_cursor=next_cursor,
        content=content,
        numbered_content=numbered_content,
        line_truncated=line_truncated,
    )