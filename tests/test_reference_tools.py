"""候选引用查找工具 (reference_tools) 单元测试。"""

from pathlib import Path

from code_agent.tools import ReferenceConfidence, find_references


def test_find_references_same_file_and_class_isolation(tmp_path: Path) -> None:
    """验证同文件直接调用、目标类 self 调用，以及同文件异类同名方法隔离。"""
    src_dir = tmp_path / "pkg"
    src_dir.mkdir()
    (src_dir / "__init__.py").write_text("", encoding="utf-8")
    (src_dir / "worker.py").write_text(
        "\n".join(
            [
                "def helper(x: int) -> int:",
                "    return x + 1",
                "",
                "def main() -> int:",
                "    return helper(10)",
                "",
                "class TargetRunner:",
                "    def run(self) -> int:",
                "        return helper(1)",
                "",
                "    def start(self) -> int:",
                "        return self.run()",
                "",
                "class OtherRunner:",
                "    def run(self) -> int:",
                "        return 0",
                "",
                "    def start(self) -> int:",
                "        return self.run()",
            ]
        )
        + "\n",
        encoding="utf-8",
        )

    helper_res = find_references("helper", project_root=tmp_path)
    assert helper_res.ok is True
    assert helper_res.total_matches == 2
    assert helper_res.data is not None
    assert all(
        ref.confidence == ReferenceConfidence.SAME_FILE_CALL
        for ref in helper_res.data.references
    )
    assert [ref.line_number for ref in helper_res.data.references] == [5, 9]

    run_res = find_references("TargetRunner.run", project_root=tmp_path)
    assert run_res.ok is True
    assert run_res.total_matches == 2
    assert run_res.data is not None
    by_line = {ref.line_number: ref for ref in run_res.data.references}
    assert by_line[12].confidence == ReferenceConfidence.SAME_FILE_CALL
    assert by_line[19].confidence == ReferenceConfidence.ATTRIBUTE_ACCESS


def test_find_references_explicit_imports_and_no_false_positive(tmp_path: Path) -> None:
    """验证跨文件别名/模块前缀/类前缀导入绑定，且不误报无关文件的同名函数。"""
    pkg = tmp_path / "app"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "core.py").write_text(
        "\n".join(
            [
                "def compute(val: int) -> int:",
                "    return val * 2",
                "",
                "class Engine:",
                "    @classmethod",
                "    def build(cls) -> str:",
                "        return 'ok'",
            ]
        )
        + "\n",
        encoding="utf-8",
        )
    (pkg / "caller_alias.py").write_text(
        "from app.core import compute as calc, Engine as CoreEngine\n"
        "x = calc(3)\n"
        "y = CoreEngine.build()\n",
        encoding="utf-8",
    )
    (pkg / "caller_mod.py").write_text(
        "import app.core as c\n"
        "import app.core\n"
        "a = c.compute(4)\n"
        "b = app.core.compute(5)\n",
        encoding="utf-8",
    )
    (pkg / "unrelated.py").write_text(
        "def compute(n: int) -> int:\n    return n\n\nz = compute(9)\n",
        encoding="utf-8",
    )

    comp_res = find_references("app.core.compute", project_root=tmp_path)
    assert comp_res.ok is True
    assert comp_res.total_matches == 3
    assert comp_res.data is not None
    assert all(
        ref.confidence == ReferenceConfidence.EXPLICIT_IMPORT_CALL
        for ref in comp_res.data.references
    )
    assert {ref.file_path for ref in comp_res.data.references} == {
        "app/caller_alias.py",
        "app/caller_mod.py",
    }

    build_res = find_references("app.core.Engine.build", project_root=tmp_path)
    assert build_res.ok is True
    assert build_res.total_matches == 1
    assert build_res.data is not None
    assert (
            build_res.data.references[0].confidence
            == ReferenceConfidence.EXPLICIT_IMPORT_CALL
    )
    assert build_res.data.references[0].file_path == "app/caller_alias.py"


def test_find_references_attribute_access_and_scope_filters(tmp_path: Path) -> None:
    """验证源码与测试范围分离标签，以及 include_tests / include_attribute_access 开关。"""
    src_pkg = tmp_path / "src" / "demo"
    test_dir = tmp_path / "tests"
    src_pkg.mkdir(parents=True)
    test_dir.mkdir()
    (src_pkg / "__init__.py").write_text("", encoding="utf-8")
    (src_pkg / "service.py").write_text(
        "class Service:\n    def execute(self) -> None:\n        pass\n",
        encoding="utf-8",
    )
    (src_pkg / "handler.py").write_text(
        "def handle(svc) -> None:\n    svc.execute()\n",
        encoding="utf-8",
    )
    (test_dir / "test_service.py").write_text(
        "from demo.service import Service\n"
        "def test_ok() -> None:\n"
        "    svc = Service()\n"
        "    svc.execute()\n",
        encoding="utf-8",
    )

    full = find_references("Service.execute", project_root=tmp_path)
    assert full.ok is True
    assert full.total_matches == 2
    assert full.data is not None
    assert [(r.scope, r.file_path) for r in full.data.references] == [
        ("source", "src/demo/handler.py"),
        ("test", "tests/test_service.py"),
    ]

    no_tests = find_references(
        "Service.execute", project_root=tmp_path, include_tests=False
    )
    assert no_tests.ok is True
    assert no_tests.total_matches == 1

    no_attr = find_references(
        "Service.execute", project_root=tmp_path, include_attribute_access=False
    )
    assert no_attr.ok is True
    assert no_attr.total_matches == 0


def test_find_references_unresolved_star_and_dynamic_calls(tmp_path: Path) -> None:
    """验证星号导入与 getattr/setattr 动态操作会被诚实标记为 UNRESOLVED。"""
    pkg = tmp_path / "modpkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "defs.py").write_text("def target_op() -> None:\n    pass\n", encoding="utf-8")
    (pkg / "star_user.py").write_text(
        "from modpkg.defs import *\n", encoding="utf-8"
    )
    (pkg / "dyn_user.py").write_text(
        "import modpkg.defs as d\n"
        "fn = getattr(d, 'target_op')\n"
        "setattr(d, 'target_op', fn)\n",
        encoding="utf-8",
    )

    res = find_references("target_op", project_root=tmp_path)
    assert res.ok is True
    assert res.total_matches == 3
    assert res.data is not None
    assert res.data.confidence_counts["UNRESOLVED"] == 3
    assert "UNRESOLVED" in res.note


def test_find_references_unindexed_and_substring_guard(tmp_path: Path) -> None:
    """验证未定义符号与宽泛子串不会误触发全仓扫描。"""
    (tmp_path / "m.py").write_text(
        "def build_report() -> None:\n    pass\n", encoding="utf-8"
    )

    missing = find_references("nonexistent_fn", project_root=tmp_path)
    assert missing.ok is True
    assert missing.total_matches == 0
    assert "未在静态符号表中精确找到" in missing.note

    partial = find_references("build", project_root=tmp_path)
    assert partial.ok is True
    assert partial.total_matches == 0
    assert "build_report" in partial.note


def test_find_references_pagination_and_structured_errors(tmp_path: Path) -> None:
    """验证分页续读闭合性与非法入参的结构化错误返回。"""
    (tmp_path / "p.py").write_text(
        "\n".join(
            [
                "def ping() -> int:",
                "    return 1",
                "a = ping()",
                "b = ping()",
                "c = ping()",
            ]
        )
        + "\n",
        encoding="utf-8",
        )

    p1 = find_references("ping", project_root=tmp_path, max_results=2, offset=0)
    assert p1.ok is True
    assert p1.total_matches == 3
    assert p1.returned_count == 2
    assert p1.remaining_count == 1
    assert p1.is_truncated is True
    assert p1.next_cursor == {"offset": 2}

    p2 = find_references("ping", project_root=tmp_path, max_results=2, offset=2)
    assert p2.ok is True
    assert p2.returned_count == 1
    assert p2.remaining_count == 0
    assert p2.is_truncated is False
    assert p2.next_cursor is None

    assert find_references("   ", project_root=tmp_path).error_code == "empty_symbol_name"
    assert (
            find_references("ping", project_root=tmp_path, max_results=0).error_code
            == "invalid_budget"
    )
    assert (
            find_references("ping", project_root=tmp_path, offset=-1).error_code
            == "invalid_offset"
    )
    assert find_references("ping").error_code == "missing_project_root"
    assert (
            find_references("ping", project_root=tmp_path / "no_dir").error_code
            == "invalid_project_root"
    )
    assert (
            find_references("ping", project_root=tmp_path, offset=9).error_code
            == "offset_out_of_range"
    )


def test_find_references_on_mini_repo() -> None:
    """验证在真实微型工程 eval/fixtures/mini_repo 上准确区分同文件与跨文件属性调用。"""
    mini_repo = Path("eval/fixtures/mini_repo")
    res = find_references("discover_documents", project_root=mini_repo)

    assert res.ok is True
    assert res.total_matches == 2
    assert res.data is not None
    assert res.data.confidence_counts[ReferenceConfidence.SAME_FILE_CALL.value] == 1
    assert res.data.confidence_counts[ReferenceConfidence.ATTRIBUTE_ACCESS.value] == 1