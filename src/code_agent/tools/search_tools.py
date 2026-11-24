"""支持多维输出预算守卫、分页游标与文件过滤的受控文本搜索工具。"""

from collections.abc import Sequence
from dataclasses import dataclass
import fnmatch
import os
from pathlib import Path, PurePosixPath
import re

from code_agent.analyzer.project_scanner import IGNORED_DIRS
from code_agent.tools.base import ToolResult, tool_error

DEFAULT_MAX_RESULTS = 30
DEFAULT_MAX_PER_FILE = 5
DEFAULT_MAX_CHARS = 3000
DEFAULT_MAX_LINE_CHARS = 200
DEFAULT_MAX_FILE_BYTES = 1_048_576  # 1 MB 上限，防止误扫超大日志/数据文件
DEFAULT_EXCLUDE_PATTERNS: tuple[str, ...] = (
    "**/traces/**",
    "**/logs/**",
    "**/eval/runs/**",
    "**/.pytest_tmp/**",
    "**/.pytmp/**",
)
_TOOL_NAME = "search_text"
_BINARY_PROBE_BYTES = 8192


@dataclass(frozen=True)
class TextMatchItem:
    """
    单条文本搜索命中记录。

    Attributes:
        file_path: 相对于 project_root 的 POSIX 风格路径（可直接传给 read_file_snippet）。
        line_number: 命中所在物理行号（1-based）。
        line_text: 经单行长度预算裁剪后的行内容（不含尾部换行符）。
        line_truncated: 该行原始文本是否因超出 max_line_chars 而被截断。
    """

    file_path: str
    line_number: int
    line_text: str
    line_truncated: bool = False


@dataclass(frozen=True)
class SearchTextData:
    """
    search_text 工具成功执行后的载荷数据。

    Attributes:
        pattern: 实际执行的搜索模式字符串。
        is_regex: 是否按正则表达式模式执行匹配。
        case_sensitive: 是否区分大小写。
        file_pattern: 调用方传入的文件过滤模式（未传时为 None）。
        matches: 本页实际返回的命中条目元组。
        formatted_preview: 按 "路径:行号 | 内容" 排版好的紧凑文本视图。
        scanned_files: 本次实际扫描内容的 UTF-8 文本文件数。
        skipped_files: 因二进制、非 UTF-8、超大体积或读取错误而跳过的文件数。
        files_with_matches: 至少命中 1 次的文件总数。
        per_file_capped_files: 命中数超出 max_per_file 而被单文件截断的文件路径元组。
    """

    pattern: str
    is_regex: bool
    case_sensitive: bool
    file_pattern: str | None
    matches: tuple[TextMatchItem, ...]
    formatted_preview: str
    scanned_files: int
    skipped_files: int
    files_with_matches: int
    per_file_capped_files: tuple[str, ...]


def _validate_glob_pattern(raw_pattern: str) -> str | None:
    """校验并规范化路径过滤模式；若检测到路径逃逸或空串则返回 None。"""
    cleaned = raw_pattern.strip().replace("\\", "/")
    if not cleaned:
        return None
    if cleaned.startswith("/") or ":" in cleaned:
        return None
    parts = [part for part in cleaned.split("/") if part]
    if ".." in parts:
        return None
    return cleaned


def _glob_to_regex(glob_pattern: str) -> re.Pattern[str]:
    """将含 ** 的 POSIX glob 转为跨 Python 版本一致的正则表达式。"""
    escaped = re.escape(glob_pattern)
    # 顺序不能乱：先替换 "**/"（支持零层或多层目录），再替换剩余 "**"、"*"、"?"
    regex_body = (
        escaped.replace(r"\*\*/", "(?:.*/)?")
        .replace(r"\*\*", ".*")
        .replace(r"\*", "[^/]*")
        .replace(r"\?", "[^/]")
    )
    return re.compile(f"^{regex_body}$")


def _matches_file_pattern(rel_posix: str, norm_pattern: str) -> bool:
    """判断相对 POSIX 路径是否命中过滤模式（支持文件名 glob、** 目录通配与目录前缀）。"""
    has_wildcard = any(ch in norm_pattern for ch in "*?[")
    if not has_wildcard:
        if norm_pattern.endswith("/"):
            return rel_posix.startswith(norm_pattern)
        return rel_posix == norm_pattern or rel_posix.startswith(f"{norm_pattern}/")

    if "/" not in norm_pattern and "**" not in norm_pattern:
        return fnmatch.fnmatchcase(PurePosixPath(rel_posix).name, norm_pattern)

    return _glob_to_regex(norm_pattern).match(rel_posix) is not None


def _compile_matcher(
        pattern: str,
        is_regex: bool,
        case_sensitive: bool,
) -> re.Pattern[str]:
    """将字面量或正则表达式统一编译为正则对象，简化后续逐行匹配分支。"""
    flags = 0 if case_sensitive else re.IGNORECASE
    expr = pattern if is_regex else re.escape(pattern)
    return re.compile(expr, flags)


def _iter_candidate_files(
        root: Path,
        norm_file_pattern: str | None,
        norm_excludes: tuple[str, ...],
) -> list[tuple[Path, str]]:
    """确定性遍历工程内候选文件，剪枝忽略目录与符号链接并应用路径过滤。"""
    candidates: list[tuple[Path, str]] = []
    for current_dir, dir_names, file_names in os.walk(
            root,
            topdown=True,
            followlinks=False,
    ):
        current_path = Path(current_dir)
        kept_dirs: list[str] = []
        for name in sorted(dir_names):
            if name in IGNORED_DIRS:
                continue
            sub_dir = current_path / name
            if sub_dir.is_symlink():
                continue
            kept_dirs.append(name)
        dir_names[:] = kept_dirs

        for file_name in sorted(file_names):
            abs_file = current_path / file_name
            if abs_file.is_symlink():
                continue
            rel_posix = abs_file.relative_to(root).as_posix()
            if norm_file_pattern is not None and not _matches_file_pattern(
                    rel_posix, norm_file_pattern
            ):
                continue
            if any(_matches_file_pattern(rel_posix, exc) for exc in norm_excludes):
                continue
            candidates.append((abs_file, rel_posix))

    candidates.sort(key=lambda item: item[1])
    return candidates


def _read_utf8_lines(file_path: Path, max_file_bytes: int) -> list[str] | None:
    """安全读取 UTF-8 文本行；遇到超大文件、二进制或编码异常时返回 None。"""
    try:
        if file_path.stat().st_size > max_file_bytes:
            return None
        raw_bytes = file_path.read_bytes()
    except OSError:
        return None

    if b"\x00" in raw_bytes[:_BINARY_PROBE_BYTES]:
        return None

    try:
        return raw_bytes.decode("utf-8").splitlines()
    except UnicodeDecodeError:
        return None


def _scan_files(
        candidates: list[tuple[Path, str]],
        matcher: re.Pattern[str],
        max_per_file: int,
        max_line_chars: int,
        max_file_bytes: int,
) -> tuple[list[TextMatchItem], int, int, int, tuple[str, ...]]:
    """逐文件扫描命中行并施加单文件条数上限与单行字符上限。"""
    collected: list[TextMatchItem] = []
    scanned_files = 0
    skipped_files = 0
    files_with_matches = 0
    capped_files: list[str] = []

    for abs_file, rel_posix in candidates:
        lines = _read_utf8_lines(abs_file, max_file_bytes=max_file_bytes)
        if lines is None:
            skipped_files += 1
            continue

        scanned_files += 1
        file_match_count = 0
        file_capped = False

        for line_no, raw_line in enumerate(lines, start=1):
            if matcher.search(raw_line) is None:
                continue

            if file_match_count >= max_per_file:
                file_capped = True
                break

            line_truncated = len(raw_line) > max_line_chars
            preview_line = raw_line[:max_line_chars] if line_truncated else raw_line
            collected.append(
                TextMatchItem(
                    file_path=rel_posix,
                    line_number=line_no,
                    line_text=preview_line,
                    line_truncated=line_truncated,
                )
            )
            file_match_count += 1

        if file_match_count > 0:
            files_with_matches += 1
        if file_capped:
            capped_files.append(rel_posix)

    return (
        collected,
        scanned_files,
        skipped_files,
        files_with_matches,
        tuple(capped_files),
    )


def _format_match_line(item: TextMatchItem) -> str:
    """将单条命中记录格式化为紧凑可读的单行摘要。"""
    suffix = " [行内截断]" if item.line_truncated else ""
    return f"{item.file_path}:{item.line_number} | {item.line_text}{suffix}"


def _paginate_by_budget(
        collected: list[TextMatchItem],
        offset: int,
        max_results: int,
        max_chars: int,
) -> tuple[tuple[TextMatchItem, ...], str, int, dict[str, int] | None]:
    """从 offset 起按条数与字符双预算切片，确保至少推进 1 条防止死循环。"""
    page_items: list[TextMatchItem] = []
    preview_lines: list[str] = []
    used_chars = 0

    for item in collected[offset : offset + max_results]:
        formatted = _format_match_line(item)
        added_chars = len(formatted) + (1 if preview_lines else 0)
        if used_chars + added_chars > max_chars:
            if not page_items:
                # 第 1 条即超出总字符预算时强制裁剪保留，保证游标向前推进
                clipped_text = formatted[:max_chars]
                page_items.append(item)
                preview_lines.append(clipped_text)
            break
        page_items.append(item)
        preview_lines.append(formatted)
        used_chars += added_chars

    remaining_count = max(0, len(collected) - (offset + len(page_items)))
    next_cursor = (
        {"offset": offset + len(page_items)} if remaining_count > 0 else None
    )
    return tuple(page_items), "\n".join(preview_lines), remaining_count, next_cursor


def search_text(
        project_root: str | Path,
        pattern: str,
        file_pattern: str | None = None,
        *,
        is_regex: bool = False,
        case_sensitive: bool = True,
        exclude_patterns: Sequence[str] | None = None,
        max_results: int = DEFAULT_MAX_RESULTS,
        max_per_file: int = DEFAULT_MAX_PER_FILE,
        max_chars: int = DEFAULT_MAX_CHARS,
        max_line_chars: int = DEFAULT_MAX_LINE_CHARS,
        max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
        offset: int = 0,
) -> ToolResult[SearchTextData]:
    """
    在工程目录内执行受控的文本或正则表达式搜索（覆盖代码、Markdown 文档与配置）。

    Args:
        project_root: 工程根目录路径。
        pattern: 待搜索的文本关键字或正则表达式。
        file_pattern: 可选的路径/文件名过滤模式（如 "*.py"、"docs/*.md"、"src/**/*.py"）。
        is_regex: 是否启用正则匹配，默认按字面量精确匹配。
        case_sensitive: 是否区分大小写，默认区分。
        exclude_patterns: 可选的排除路径模式序列。
        max_results: 单页最多返回的命中条目数。
        max_per_file: 单个文件最多采集的命中条目数，防止单文件刷屏。
        max_chars: 单页格式化预览的总字符数预算上限。
        max_line_chars: 单行命中内容的最大预览字符数。
        max_file_bytes: 跳过扫描的单个文件体积上限（字节）。
        offset: 分页偏移量，用于配合 next_cursor 续读下一页。

    Returns:
        ToolResult[SearchTextData]: 统一返回契约，含命中条目、截断信息、续读游标或错误说明。
    """
    if not pattern:
        return tool_error(_TOOL_NAME, "empty_pattern", "搜索模式 pattern 不能为空。")
    if (
            max_results < 1
            or max_per_file < 1
            or max_chars < 1
            or max_line_chars < 1
            or max_file_bytes < 1
    ):
        return tool_error(
            _TOOL_NAME,
            "invalid_budget",
            "预算参数（max_results / max_per_file / max_chars / max_line_chars / max_file_bytes）必须 >= 1。",
        )
    if offset < 0:
        return tool_error(
            _TOOL_NAME,
            "invalid_offset",
            f"offset 不能小于 0，当前为 {offset}。",
        )

    norm_file_pattern: str | None = None
    if file_pattern is not None:
        norm_file_pattern = _validate_glob_pattern(file_pattern)
        if norm_file_pattern is None:
            return tool_error(
                _TOOL_NAME,
                "invalid_file_pattern",
                f"非法的 file_pattern（不允许为空、绝对路径或包含 ..）：{file_pattern}",
            )

    effective_excludes = (
        DEFAULT_EXCLUDE_PATTERNS if exclude_patterns is None else exclude_patterns
    )
    norm_excludes: list[str] = []
    if effective_excludes:
        for raw_exc in effective_excludes:
            norm_exc = _validate_glob_pattern(raw_exc)
            if norm_exc is None:
                return tool_error(
                    _TOOL_NAME,
                    "invalid_exclude_pattern",
                    f"非法的 exclude_patterns 项：{raw_exc}",
                )
            norm_excludes.append(norm_exc)

    try:
        matcher = _compile_matcher(
            pattern, is_regex=is_regex, case_sensitive=case_sensitive
        )
    except re.error as exc:
        return tool_error(_TOOL_NAME, "invalid_regex", f"无效的正则表达式：{exc}")

    root = Path(project_root).resolve()
    if not root.exists() or not root.is_dir():
        return tool_error(
            _TOOL_NAME,
            "invalid_project_root",
            f"工程根目录不存在或不是目录：{root}",
        )

    candidates = _iter_candidate_files(root, norm_file_pattern, tuple(norm_excludes))
    collected, scanned_files, skipped_files, files_with_matches, capped_files = (
        _scan_files(
            candidates,
            matcher=matcher,
            max_per_file=max_per_file,
            max_line_chars=max_line_chars,
            max_file_bytes=max_file_bytes,
        )
    )

    total_matches = len(collected)
    if offset > 0 and offset >= total_matches:
        return tool_error(
            _TOOL_NAME,
            "offset_out_of_range",
            f"offset ({offset}) 超出总命中条数 ({total_matches})。",
        )

    page_items, formatted_preview, remaining_count, next_cursor = _paginate_by_budget(
        collected,
        offset=offset,
        max_results=max_results,
        max_chars=max_chars,
    )

    notes: list[str] = []
    if total_matches == 0:
        notes.append("未找到匹配内容。")
    else:
        notes.append("搜索结果仅提供局部命中行，确认完整语义请结合 read_file_snippet。")
    if capped_files:
        notes.append(
            f"以下文件命中数达到单文件上限 ({max_per_file} 条) 已截断："
            f"{', '.join(capped_files[:5])}"
            + (" 等" if len(capped_files) > 5 else "")
            + "。"
        )
    if remaining_count > 0:
        notes.append(f"尚有 {remaining_count} 条命中未返回，可传入 next_cursor 续读。")

    data = SearchTextData(
        pattern=pattern,
        is_regex=is_regex,
        case_sensitive=case_sensitive,
        file_pattern=norm_file_pattern,
        matches=page_items,
        formatted_preview=formatted_preview,
        scanned_files=scanned_files,
        skipped_files=skipped_files,
        files_with_matches=files_with_matches,
        per_file_capped_files=capped_files,
    )
    return ToolResult(
        ok=True,
        tool_name=_TOOL_NAME,
        data=data,
        is_truncated=remaining_count > 0,
        total_matches=total_matches,
        returned_count=len(page_items),
        remaining_count=remaining_count,
        next_cursor=next_cursor,
        note=" ".join(notes),
    )