"""统一内存符号索引器 (SymbolIndex) 单元测试。"""

from pathlib import Path
import pytest
from code_agent.analyzer.symbol_index import build_symbol_index

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_lookup_symbol_levels(tmp_path: Path) -> None:
    """验证 exact_fqn -> exact_name -> substring -> none 四级查询策略。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "a.py").write_text("def run() -> None:\n    pass\n", encoding="utf-8")
    (pkg_dir / "b.py").write_text(
        "class TaskRunner:\n    def run(self) -> None:\n        pass\n",
        encoding="utf-8",
    )

    index = build_symbol_index(tmp_path)

    # 1. exact_fqn：仅精确返回唯一一条
    fqn_res = index.lookup_symbol("app.b.TaskRunner.run")
    assert fqn_res.match_mode == "exact_fqn"
    assert len(fqn_res.matches) == 1
    assert fqn_res.matches[0].kind == "method"

    # 2. exact_name：返回所有同名符号
    name_res = index.lookup_symbol("run")
    assert name_res.match_mode == "exact_name"
    assert {m.fqn for m in name_res.matches} == {"app.a.run", "app.b.TaskRunner.run"}

    # 3. substring：不区分大小写子串兜底
    sub_res = index.lookup_symbol("runner")
    assert sub_res.match_mode == "substring"
    assert [m.fqn for m in sub_res.matches] == ["app.b.TaskRunner"]

    # 4. none：空字符串或无匹配
    assert index.lookup_symbol("   ").match_mode == "none"
    assert index.lookup_symbol("not_exist_xyz").match_mode == "none"


def test_symbols_in_module_and_all_unresolved(tmp_path: Path) -> None:
    """验证模块内符号过滤及符号/导入两类 unresolved 汇总。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "worker.py").write_text(
        "\n".join(
            [
                "from math import *",
                "MAX_RETRY = 3",
                "def bind(obj: object) -> None:",
                "    setattr(obj, 'x', 1)",
            ]
        ),
        encoding="utf-8",
    )

    index = build_symbol_index(tmp_path)
    mod_syms = index.symbols_in_module("app.worker")
    assert [s.name for s in mod_syms] == ["worker", "MAX_RETRY", "bind"]

    sym_unresolved, imp_unresolved = index.all_unresolved()
    assert [u.reason_code for u in sym_unresolved] == ["dynamic_setattr"]
    assert [u.reason_code for u in imp_unresolved] == ["star_import"]


def test_inspect_module_deps_internal_external_and_missing(tmp_path: Path) -> None:
    """验证单模块出入向依赖区分内部/外部、含未解析记录及未知模块降级。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    (pkg_dir / "cli.py").write_text(
        "\n".join(
            [
                "import json",
                "from math import *",
                "from app.core import VALUE",
            ]
        ),
        encoding="utf-8",
    )

    index = build_symbol_index(tmp_path)

    cli_deps = index.inspect_module_deps("app.cli")
    assert cli_deps.exists is True
    assert cli_deps.internal_dependencies == ("app.core",)
    assert cli_deps.external_dependencies == ("json",)
    assert cli_deps.dependents == ()
    assert len(cli_deps.outgoing_edges) == 2
    assert [u.reason_code for u in cli_deps.unresolved_in_module] == ["star_import"]

    core_deps = index.inspect_module_deps("app.core")
    assert core_deps.dependents == ("app.cli",)

    missing_deps = index.inspect_module_deps("app.unknown")
    assert missing_deps.exists is False
    assert missing_deps.internal_dependencies == ()


def test_missing_project_directory_raises(tmp_path: Path) -> None:
    """验证根目录不存在时抛出 NotADirectoryError。"""
    with pytest.raises(NotADirectoryError):
        build_symbol_index(tmp_path / "missing")


def test_build_symbol_index_on_external_mini_repo() -> None:
    """验证外部独立工程 mini_repo 的统一索引构建与耗时记录。"""
    mini_repo = REPO_ROOT / "eval" / "fixtures" / "mini_repo"
    index = build_symbol_index(mini_repo)

    assert index.structure.layout == "flat"
    assert index.build_duration_ms > 0.0
    assert index.lookup_symbol("BuildEngine").match_mode == "exact_name"
    assert index.lookup_symbol("minisite.engine.BuildEngine.build").match_mode == "exact_fqn"
    assert set(index.inspect_module_deps("minisite.cli").internal_dependencies) == {
        "minisite.config",
        "minisite.engine",
        "minisite.exceptions",
    }
    sym_u, imp_u = index.all_unresolved()
    assert sym_u == ()
    assert imp_u == ()