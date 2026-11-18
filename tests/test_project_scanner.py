# tests/test_project_scanner.py
from pathlib import Path

from code_agent.analyzer.project_scanner import scan_project


def test_scans_src_layout_and_ignores_cache(tmp_path: Path) -> None:
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()
    (tmp_path / ".venv" / "Lib").mkdir(parents=True)

    (tmp_path / "src" / "app" / "main.py").write_text("main = 1")
    (tmp_path / "tests" / "test_main.py").write_text("def test_main(): pass")
    (tmp_path / "docs" / "guide.md").write_text("# Guide")
    (tmp_path / ".venv" / "Lib" / "ignored.py").write_text("ignored = 1")
    (tmp_path / "pyproject.toml").write_text("[project]")

    result = scan_project(tmp_path)

    assert result.layout == "src"
    assert result.source_roots == (Path("src"),)
    assert Path("src/app/main.py") in result.source_files
    assert Path("tests/test_main.py") in result.test_files
    assert Path("docs/guide.md") in result.doc_files
    assert Path("pyproject.toml") in result.config_files
    assert all(".venv" not in path.parts for path in result.source_files)


def test_scans_flat_layout_without_mixing_tests_and_docs(
    tmp_path: Path,
) -> None:
    (tmp_path / "minisite").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "docs").mkdir()

    (tmp_path / "minisite" / "engine.py").write_text("class Engine: pass")
    (tmp_path / "tests" / "test_engine.py").write_text("def test_engine(): pass")
    (tmp_path / "docs" / "example.py").write_text("not application source")

    result = scan_project(tmp_path)

    assert result.layout == "flat"
    assert result.source_roots == (Path("."),)
    assert result.source_files == (Path("minisite/engine.py"),)
    assert result.test_files == (Path("tests/test_engine.py"),)


def test_unknown_layout_is_reported(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("# Empty project")

    result = scan_project(tmp_path)

    assert result.layout == "unknown"
    assert result.source_roots == ()
    assert result.warnings


def test_missing_project_directory_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    try:
        scan_project(missing)
    except NotADirectoryError:
        pass
    else:
        raise AssertionError("expected NotADirectoryError")

def test_src_directory_without_python_files_reports_warning(
    tmp_path: Path,
) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "app.py").write_text(
        "value = 1",
        encoding="utf-8",
    )

    result = scan_project(tmp_path)

    assert result.layout == "flat"
    assert result.source_files == (Path("app.py"),)
    assert any("没有 Python 源码" in item for item in result.warnings)


def test_mixed_src_and_flat_layout_prefers_src(
    tmp_path: Path,
) -> None:
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "main.py").write_text(
        "value = 1",
        encoding="utf-8",
    )
    (tmp_path / "root_module.py").write_text(
        "value = 2",
        encoding="utf-8",
    )

    result = scan_project(tmp_path)

    assert result.layout == "src"
    assert result.source_files == (
        Path("src/app/main.py"),
    )
    assert any("同时发现 src 布局" in item for item in result.warnings)


def test_nested_cache_directories_are_excluded(
    tmp_path: Path,
) -> None:
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "main.py").write_text(
        "value = 1",
        encoding="utf-8",
    )

    cache_dir = tmp_path / "src" / "app" / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "main.cpython-313.py").write_text(
        "ignored = 1",
        encoding="utf-8",
    )

    result = scan_project(tmp_path)

    assert result.source_files == (
        Path("src/app/main.py"),
    )


def test_repeated_scan_is_deterministic(
    tmp_path: Path,
) -> None:
    (tmp_path / "src" / "b").mkdir(parents=True)
    (tmp_path / "src" / "a").mkdir(parents=True)

    (tmp_path / "src" / "b" / "b.py").write_text(
        "b = 1",
        encoding="utf-8",
    )
    (tmp_path / "src" / "a" / "a.py").write_text(
        "a = 1",
        encoding="utf-8",
    )

    first = scan_project(tmp_path)
    second = scan_project(tmp_path)

    assert first == second
    assert first.source_files == (
        Path("src/a/a.py"),
        Path("src/b/b.py"),
    )