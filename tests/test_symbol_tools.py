"""符号查找工具 (symbol_tools) 单元测试。"""

from pathlib import Path
from code_agent.analyzer.symbol_index import build_symbol_index
from code_agent.tools.code_reader import read_file_snippet
from code_agent.tools.symbol_tools import lookup_symbol

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_lookup_symbol_levels_and_reader_handoff(tmp_path: Path) -> None:
    """验证四级查询精度、元数据字段完整透出，以及与 read_file_snippet 的行号闭环。"""
    pkg = tmp_path / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "service.py").write_text(
        "\n".join(
            [
                "MAX_CONN = 10",
                "",
                "class BaseRunner:",
                "    pass",
                "",
                "class TaskRunner(BaseRunner):",
                "    def run(self, task_id: int) -> str:",
                "        return str(task_id)",
            ]
        )
        + "\n",
        encoding="utf-8",
        )

    # 1. exact_fqn 命中方法，校验签名与父级 FQN
    fqn_res = lookup_symbol("app.service.TaskRunner.run", project_root=tmp_path)
    assert fqn_res.ok is True
    assert fqn_res.match_mode == "exact_fqn"
    assert fqn_res.total_matches == 1
    item = fqn_res.items[0]
    assert item.kind == "method"
    assert item.file_path == "src/app/service.py"
    assert item.signature == "(self, task_id: int) -> str"
    assert item.parent_fqn == "app.service.TaskRunner"

    # 2. 用返回的 file_path 与行号区间直接联动 read_file_snippet 读取实现体
    snippet = read_file_snippet(
        tmp_path,
        item.file_path,
        start_line=item.start_line,
        end_line=item.end_line,
    )
    assert snippet.ok is True
    assert "def run(self, task_id: int) -> str:" in snippet.content

    # 3. exact_name 命中类，校验基类元组
    cls_res = lookup_symbol("TaskRunner", project_root=tmp_path)
    assert cls_res.match_mode == "exact_name"
    assert cls_res.items[0].bases == ("BaseRunner",)

    # 4. substring 命中常量
    sub_res = lookup_symbol("conn", project_root=tmp_path)
    assert sub_res.match_mode == "substring"
    assert [m.name for m in sub_res.items] == ["MAX_CONN"]

    # 5. none 未命中
    none_res = lookup_symbol("nonexistent_symbol_xyz", project_root=tmp_path)
    assert none_res.ok is True
    assert none_res.match_mode == "none"
    assert none_res.total_matches == 0


def test_lookup_symbol_truncation(tmp_path: Path) -> None:
    """验证匹配条数超过 max_results 时的截断与剩余计数。"""
    pkg = tmp_path / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    defs = "\n".join(f"def worker_{i}() -> None:\n    pass" for i in range(5))
    (pkg / "workers.py").write_text(defs + "\n", encoding="utf-8")

    res = lookup_symbol("worker_", project_root=tmp_path, max_results=2)

    assert res.ok is True
    assert res.match_mode == "substring"
    assert res.total_matches == 5
    assert len(res.items) == 2
    assert res.is_truncated is True
    assert res.remaining_count == 3


def test_lookup_symbol_unresolved_hints(tmp_path: Path) -> None:
    """验证存在相关静态分析未解析项时，能在结果中诚实带出提示。"""
    pkg = tmp_path / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "dyn.py").write_text(
        "def patch(obj: object) -> None:\n    setattr(obj, 'TargetHook', 1)\n",
        encoding="utf-8",
    )

    res = lookup_symbol("TargetHook", project_root=tmp_path)

    assert res.ok is True
    assert res.total_matches == 0
    assert any("dynamic_setattr" in hint for hint in res.unresolved_hints)


def test_lookup_symbol_reuse_index_and_errors(tmp_path: Path) -> None:
    """验证复用已有 SymbolIndex 及各类非法入参的结构化错误返回。"""
    pkg = tmp_path / "src" / "app"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "mod.py").write_text("FLAG = True\n", encoding="utf-8")

    cached_index = build_symbol_index(tmp_path)
    reused = lookup_symbol("FLAG", symbol_index=cached_index)
    assert reused.ok is True
    assert reused.items[0].fqn == "app.mod.FLAG"

    empty_q = lookup_symbol("   ", symbol_index=cached_index)
    assert empty_q.ok is False
    assert empty_q.error_code == "empty_query"

    bad_budget = lookup_symbol("FLAG", symbol_index=cached_index, max_results=0)
    assert bad_budget.ok is False
    assert bad_budget.error_code == "invalid_budget"

    no_root = lookup_symbol("FLAG")
    assert no_root.ok is False
    assert no_root.error_code == "missing_project_root"

    bad_root = lookup_symbol("FLAG", project_root=tmp_path / "not_exist_dir")
    assert bad_root.ok is False
    assert bad_root.error_code == "invalid_project_root"


def test_lookup_symbol_and_read_on_mini_repo() -> None:
    """在外部独立工程 mini_repo 上验证符号定位与分页代码读取联动。"""
    mini_repo = REPO_ROOT / "eval" / "fixtures" / "mini_repo"
    res = lookup_symbol("BuildEngine.build", project_root=mini_repo)

    assert res.ok is True
    assert res.match_mode == "substring" or res.match_mode in ("exact_fqn", "exact_name")
    assert any(item.fqn == "minisite.engine.BuildEngine.build" for item in res.items)

    target = next(item for item in res.items if item.fqn == "minisite.engine.BuildEngine.build")
    page1 = read_file_snippet(
        mini_repo,
        target.file_path,
        start_line=target.start_line,
        end_line=target.end_line,
        max_lines=8,
    )
    assert page1.ok is True
    assert "def build(" in page1.content