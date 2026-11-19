import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


# 工程目录布局类型：
# - "src":     标准 src 布局（业务源码集中在 <root>/src/ 下，如 src/code_agent/...）
# - "flat":    平铺布局（业务源码直接放在工程根目录或根目录子包下，如 minisite/...）
# - "unknown": 未识别布局（工程中未发现 src/ 下的 Python 文件，也无根目录有效源码）
Layout = Literal["src", "flat", "unknown"]


IGNORED_DIRS = {
    ".git",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}

TEST_DIR_NAMES = {"test", "tests"}
DOC_DIR_NAMES = {"doc", "docs", "documentation"}

CONFIG_FILE_NAMES = {
    "pyproject.toml",
    "setup.py",
    "requirements.txt",
}

PYTHON_EXTENSIONS = {".py"}
DOCUMENT_EXTENSIONS = {".md", ".rst", ".txt"}


@dataclass(frozen=True)
class ProjectStructure:
    """
    工程目录扫描结果。

    project_root 使用绝对路径。
    其他路径均为相对于 project_root 的路径。

    source_roots 表示源码扫描起点，不代表其中所有文件都是业务源码。
    source_files 是经过目录分类和过滤后的最终源码文件清单。
    """

    project_root: Path
    layout: Layout
    source_roots: tuple[Path, ...]
    test_roots: tuple[Path, ...]
    doc_roots: tuple[Path, ...]
    config_files: tuple[Path, ...]
    source_files: tuple[Path, ...]
    test_files: tuple[Path, ...]
    doc_files: tuple[Path, ...]
    warnings: tuple[str, ...]


def _is_under(path: Path, root: Path) -> bool:
    """判断 path 是否等于 root，或位于 root 目录下。"""
    if root == Path("."):
        return True
    return path == root or root in path.parents


def scan_project(project_root: str | Path) -> ProjectStructure:
    """扫描工程目录并识别源码、测试、文档和配置文件。"""
    root = Path(project_root).expanduser().resolve()

    if not root.is_dir():
        raise NotADirectoryError(
            f"工程根目录不存在或不是目录: {root}"
        )

    warnings: list[str] = []

    test_roots = tuple(
        sorted(
            (
                Path(name)
                for name in TEST_DIR_NAMES
                if (root / name).is_dir()
            ),
            key=lambda path: path.as_posix(),
        )
    )

    doc_roots = tuple(
        sorted(
            (
                Path(name)
                for name in DOC_DIR_NAMES
                if (root / name).is_dir()
            ),
            key=lambda path: path.as_posix(),
        )
    )

    files: list[Path] = []

    def on_walk_error(error: OSError) -> None:
        """记录无法访问的目录，避免扫描遗漏被静默隐藏。"""
        location = error.filename or "<unknown>"
        warnings.append(f"扫描目录失败: {location}: {error}")

    for current_dir, dir_names, file_names in os.walk(
        root,
        topdown=True,
        followlinks=False,
        onerror=on_walk_error,
    ):
        current_path = Path(current_dir)

        kept_dirs: list[str] = []
        for name in sorted(dir_names):
            if name in IGNORED_DIRS:
                continue

            candidate = current_path / name
            if candidate.is_symlink():
                warnings.append(
                    f"跳过符号链接目录: "
                    f"{candidate.relative_to(root).as_posix()}"
                )
                continue

            kept_dirs.append(name)

        # 剪枝：后续不会进入缓存、虚拟环境和符号链接目录。
        dir_names[:] = kept_dirs

        for file_name in sorted(file_names):
            absolute_path = current_path / file_name

            if absolute_path.is_symlink():
                warnings.append(
                    f"跳过符号链接文件: "
                    f"{absolute_path.relative_to(root).as_posix()}"
                )
                continue

            files.append(absolute_path.relative_to(root))

    files.sort(key=lambda path: path.as_posix())

    src_dir = Path("src")
    src_exists = (root / src_dir).is_dir()

    has_src_python = any(
        path.suffix.lower() in PYTHON_EXTENSIONS
        and _is_under(path, src_dir)
        for path in files
    )

    test_root_set = set(test_roots)
    doc_root_set = set(doc_roots)

    has_flat_python = any(
        path.suffix.lower() in PYTHON_EXTENSIONS
        and not any(_is_under(path, item) for item in test_root_set)
        and not any(_is_under(path, item) for item in doc_root_set)
        for path in files
    )

    layout: Layout
    source_roots: tuple[Path, ...]
    if has_src_python:
        layout= "src"
        source_roots = (src_dir,)

        if has_flat_python:
            warnings.append(
                "同时发现 src 布局和根目录源码，"
                "当前优先使用 src 作为源码布局。"
            )
    elif has_flat_python:
        layout = "flat"
        source_roots = (Path("."),)

        if src_exists:
            warnings.append(
                "发现 src 目录，但其中没有 Python 源码，"
                "当前按 flat 布局处理。"
            )
    else:
        layout = "unknown"
        source_roots = ()

        if src_exists:
            warnings.append(
                "发现 src 目录，但未识别到 Python 源码。"
            )
        else:
            warnings.append(
                "未识别到 src 布局或普通 Python 源码布局。"
            )

    config_files = tuple(
        path
        for path in files
        if len(path.parts) == 1
        and path.name in CONFIG_FILE_NAMES
    )

    test_files = tuple(
        path
        for path in files
        if path.suffix.lower() in PYTHON_EXTENSIONS
        and any(_is_under(path, root_path) for root_path in test_roots)
    )

    doc_files = tuple(
        path
        for path in files
        if path.suffix.lower() in DOCUMENT_EXTENSIONS
        and any(_is_under(path, root_path) for root_path in doc_roots)
    )

    if layout == "src":
        source_files = tuple(
            path
            for path in files
            if path.suffix.lower() in PYTHON_EXTENSIONS
            and _is_under(path, src_dir)
        )
    elif layout == "flat":
        source_files = tuple(
            path
            for path in files
            if path.suffix.lower() in PYTHON_EXTENSIONS
            and not any(
                _is_under(path, root_path)
                for root_path in test_roots
            )
            and not any(
                _is_under(path, root_path)
                for root_path in doc_roots
            )
        )
    else:
        source_files = ()

    return ProjectStructure(
        project_root=root,
        layout=layout,
        source_roots=source_roots,
        test_roots=test_roots,
        doc_roots=doc_roots,
        config_files=tuple(
            sorted(config_files, key=lambda path: path.as_posix())
        ),
        source_files=source_files,
        test_files=test_files,
        doc_files=doc_files,
        warnings=tuple(sorted(set(warnings))),
    )


