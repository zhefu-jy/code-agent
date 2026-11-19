"""AST 导入分析器与 ModuleDependencyGraph 单元测试。"""

from pathlib import Path
import pytest
from code_agent.analyzer.import_analyzer import analyze_imports

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_extracts_standard_aliased_and_submodule_imports(tmp_path: Path) -> None:
    """验证普通 import、别名 as、内部子模块与成员导入，以及正反向查询。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "models.py").write_text("class User:\n    pass\n", encoding="utf-8")
    (pkg_dir / "utils.py").write_text("def fmt() -> str:\n    return 'ok'\n", encoding="utf-8")
    (pkg_dir / "service.py").write_text(
        "\n".join(
            [
                "import json",
                "import pathlib as pl",
                "from app import utils",
                "from app.models import User as AppUser",
            ]
        ),
        encoding="utf-8",
    )

    graph = analyze_imports(tmp_path)

    assert graph.dependencies_of("app.service") == (
        "json",
        "pathlib",
        "app.utils",
        "app.models",
    )
    assert graph.dependencies_of("app.service", internal_only=True) == (
        "app.utils",
        "app.models",
    )
    assert graph.dependents_of("app.models") == ("app.service",)
    assert graph.dependents_of("app.utils") == ("app.service",)

    edge_by_target = {
        (e.target_module, e.imported_name): e
        for e in graph.edges
        if e.source_module == "app.service"
    }
    assert edge_by_target[("pathlib", None)].alias == "pl"
    assert edge_by_target[("pathlib", None)].is_internal is False
    # from app import utils 中 utils 本身是内部模块，imported_name 归并为 None
    assert edge_by_target[("app.utils", None)].is_internal is True
    assert edge_by_target[("app.models", "User")].alias == "AppUser"
    assert edge_by_target[("app.models", "User")].is_internal is True
    assert graph.unresolved == ()


def test_resolves_relative_imports_for_module_and_init_package(tmp_path: Path) -> None:
    """验证普通模块与 __init__.py 包入口在 . 与 .. 相对导入下的路径还原。"""
    app_dir = tmp_path / "src" / "app"
    core_dir = app_dir / "core"
    core_dir.mkdir(parents=True)
    (app_dir / "__init__.py").write_text("", encoding="utf-8")
    (app_dir / "config.py").write_text("DEBUG = True\n", encoding="utf-8")
    (core_dir / "__init__.py").write_text(
        "\n".join(
            [
                "from . import model",
                "from ..config import DEBUG",
            ]
        ),
        encoding="utf-8",
    )
    (core_dir / "model.py").write_text("class Item:\n    pass\n", encoding="utf-8")
    (core_dir / "service.py").write_text(
        "\n".join(
            [
                "from .model import Item",
                "from .. import config",
            ]
        ),
        encoding="utf-8",
    )

    graph = analyze_imports(tmp_path)

    assert graph.dependencies_of("app.core", internal_only=True) == (
        "app.core.model",
        "app.config",
    )
    assert graph.dependencies_of("app.core.service", internal_only=True) == (
        "app.core.model",
        "app.config",
    )
    assert all(e.is_relative for e in graph.edges)
    assert graph.unresolved == ()


def test_marks_relative_import_overflow_as_unresolved(tmp_path: Path) -> None:
    """验证相对导入层级越界时记为 relative_import_overflow 而不静默丢弃。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "broken.py").write_text("from ..outside import x\n", encoding="utf-8")

    graph = analyze_imports(tmp_path)

    assert graph.edges == ()
    assert len(graph.unresolved) == 1
    assert graph.unresolved[0].reason_code == "relative_import_overflow"
    assert graph.unresolved[0].line_number == 1


def test_marks_star_and_dynamic_imports_as_unresolved(tmp_path: Path) -> None:
    """验证星号导入与各类动态导入（含 importlib 别名）均记录为 unresolved。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "dynamic.py").write_text(
        "\n".join(
            [
                "import importlib as il",
                "from importlib import import_module as im",
                "from math import *",
                "m1 = __import__('os')",
                "m2 = il.import_module('json')",
                "m3 = im('sys')",
            ]
        ),
        encoding="utf-8",
    )

    graph = analyze_imports(tmp_path)
    reasons = [item.reason_code for item in graph.unresolved]

    assert reasons.count("star_import") == 1
    assert reasons.count("dynamic_import") == 3


def test_handles_syntax_error_without_crashing(tmp_path: Path) -> None:
    """验证单文件语法错误记录 syntax_error，不阻塞其余模块导入分析。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "bad.py").write_text("from x import (\n", encoding="utf-8")
    (pkg_dir / "good.py").write_text("import json\n", encoding="utf-8")

    graph = analyze_imports(tmp_path)

    assert graph.dependencies_of("app.good") == ("json",)
    assert len(graph.unresolved) == 1
    assert graph.unresolved[0].reason_code == "syntax_error"


def test_missing_project_directory_raises(tmp_path: Path) -> None:
    """验证工程目录不存在时抛出 NotADirectoryError。"""
    with pytest.raises(NotADirectoryError):
        analyze_imports(tmp_path / "missing")


def test_repeated_analysis_is_deterministic(tmp_path: Path) -> None:
    """验证多次分析输出完全一致，顺序确定。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "b.py").write_text("import sys\n", encoding="utf-8")
    (pkg_dir / "a.py").write_text("import os\n", encoding="utf-8")

    first = analyze_imports(tmp_path)
    second = analyze_imports(tmp_path)

    assert first == second


def test_analyzes_external_mini_repo_dependencies() -> None:
    """验证外部独立工程 mini_repo 的跨模块正反向依赖图。"""
    mini_repo = REPO_ROOT / "eval" / "fixtures" / "mini_repo"
    graph = analyze_imports(mini_repo)

    assert set(graph.dependencies_of("minisite.cli", internal_only=True)) == {
        "minisite.config",
        "minisite.engine",
        "minisite.exceptions",
    }
    assert set(graph.dependents_of("minisite.exceptions")) == {
        "minisite.cli",
        "minisite.config",
        "minisite.engine",
    }
    assert graph.unresolved == ()