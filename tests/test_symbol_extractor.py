"""AST 符号抽取器与 unresolved 边界单元测试。"""

from pathlib import Path
import pytest
from code_agent.analyzer.symbol_extractor import extract_symbols
REPO_ROOT = Path(__file__).resolve().parent.parent

def test_extracts_module_class_method_function_and_constant(
        tmp_path: Path,
) -> None:
    """验证常规模块、类、异步方法、顶层函数与常量的提取及行号签名。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "service.py").write_text(
        "\n".join(
            [
                "DEFAULT_TIMEOUT: int = 30",
                "API_VERSION = 'v1'",
                "",
                "def build_name(prefix: str, suffix: str = 'ok') -> str:",
                "    return f'{prefix}_{suffix}'",
                "",
                "class UserService(BaseService):",
                "    async def fetch_user(self, user_id: int) -> dict[str, str]:",
                "        return {'id': str(user_id)}",
            ]
        ),
        encoding="utf-8",
    )
    table = extract_symbols(tmp_path)
    mod_sym = table.get_by_fqn("app.service")
    assert mod_sym is not None
    assert mod_sym.kind == "module"
    assert mod_sym.start_line == 1
    assert mod_sym.end_line == 9
    timeout_sym = table.get_by_fqn("app.service.DEFAULT_TIMEOUT")
    assert timeout_sym is not None
    assert timeout_sym.kind == "constant"
    assert timeout_sym.start_line == 1
    version_sym = table.get_by_fqn("app.service.API_VERSION")
    assert version_sym is not None
    assert version_sym.kind == "constant"
    func_sym = table.get_by_fqn("app.service.build_name")
    assert func_sym is not None
    assert func_sym.kind == "function"
    assert func_sym.signature == "(prefix: str, suffix: str='ok') -> str"
    assert func_sym.start_line == 4
    assert func_sym.end_line == 5
    cls_sym = table.get_by_fqn("app.service.UserService")
    assert cls_sym is not None
    assert cls_sym.kind == "class"
    assert cls_sym.bases == ("BaseService",)
    assert cls_sym.parent_fqn == "app.service"
    method_sym = table.get_by_fqn("app.service.UserService.fetch_user")
    assert method_sym is not None
    assert method_sym.kind == "method"
    assert method_sym.parent_fqn == "app.service.UserService"
    assert method_sym.signature == "async (self, user_id: int) -> dict[str, str]"
    assert len(table.symbols_in_file("src/app/service.py")) == 6
    assert [s.fqn for s in table.find_by_name("fetch_user")] == [
        "app.service.UserService.fetch_user"
    ]
    assert table.unresolved == ()

def test_init_module_maps_to_package_fqn(tmp_path: Path) -> None:
    """验证 __init__.py 映射到所属包名而非 pkg.__init__。"""
    pkg_dir = tmp_path / "src" / "pkg"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("VERSION = '1.0'\n", encoding="utf-8")
    table = extract_symbols(tmp_path)
    assert table.get_by_fqn("pkg") is not None
    assert table.get_by_fqn("pkg.__init__") is None
    assert table.get_by_fqn("pkg.VERSION") is not None

def test_marks_namespace_package_as_unresolved(tmp_path: Path) -> None:
    """验证缺少 __init__.py 的包目录会标记为 namespace_package。"""
    pkg_dir = tmp_path / "src" / "ns_pkg"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "helper.py").write_text("def run() -> None:\n    pass\n", encoding="utf-8")
    table = extract_symbols(tmp_path)
    assert any(
        item.reason_code == "namespace_package"
        and item.module_name == "ns_pkg"
        for item in table.unresolved
    )

@pytest.mark.parametrize(
    "class_header",
    [
        "class Target(MixinA, MixinB):",
        "class Target(make_base()):",
        "class Target(Generic[T]):",
    ],
)

def test_marks_complex_inheritance_without_dropping_class(
        tmp_path: Path,
        class_header: str,
) -> None:
    """验证多重继承或动态/泛型基类会记录 unresolved，同时不丢失类本身符号。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "models.py").write_text(
        f"{class_header}\n    def save(self) -> None:\n        pass\n",
        encoding="utf-8",
    )
    table = extract_symbols(tmp_path)
    assert table.get_by_fqn("app.models.Target") is not None
    assert table.get_by_fqn("app.models.Target.save") is not None
    complex_items = [
        item for item in table.unresolved if item.reason_code == "complex_inheritance"
    ]
    assert len(complex_items) == 1
    assert complex_items[0].symbol_name == "app.models.Target"

@pytest.mark.parametrize(
    "all_statement",
    [
        "__all__ = ['A'] + ['B']",
        "__all__: list[str] = ['A'] + ['B']",
        "__all__ += ['B']",
        "__all__.append('C')",
    ],
)

def test_marks_dynamic_all_variants_as_unresolved(
        tmp_path: Path,
        all_statement: str,
) -> None:
    """验证各类动态 __all__ 构造与修改均被标记为 dynamic_all_export。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "exports.py").write_text(f"{all_statement}\n", encoding="utf-8")
    table = extract_symbols(tmp_path)
    assert any(
        item.reason_code == "dynamic_all_export" and item.symbol_name == "__all__"
        for item in table.unresolved
    )

def test_marks_all_reexport_as_unresolved(tmp_path: Path) -> None:
    """验证 __all__ 导出非本模块定义符号时标记为 all_reexport。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "reexport.py").write_text(
        "\n".join(
            [
                "class LocalItem:",
                "    pass",
                "",
                "__all__ = ['LocalItem', 'ExternalItem']",
            ]
        ),
        encoding="utf-8",
    )
    table = extract_symbols(tmp_path)
    reexport_items = [
        item for item in table.unresolved if item.reason_code == "all_reexport"
    ]
    assert len(reexport_items) == 1
    assert reexport_items[0].symbol_name == "ExternalItem"

def test_marks_dynamic_setattr_nested_class_and_conditional_def(
        tmp_path: Path,
) -> None:
    """验证动态 setattr、嵌套类、条件定义被标记，且放过常规 main 守卫。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "tricky.py").write_text(
        "\n".join(
            [
                "class Outer:",
                "    class Meta:",
                "        pass",
                "",
                "    def bind(self, name: str, val: object) -> None:",
                "        setattr(self, name, val)",
                "",
                "if True:",
                "    def conditional_fn() -> None:",
                "        pass",
                "",
                "def main() -> None:",
                "    pass",
                "",
                "if __name__ == '__main__':",
                "    main()",
            ]
        ),
        encoding="utf-8",
    )
    table = extract_symbols(tmp_path)
    reasons = [item.reason_code for item in table.unresolved]
    assert "nested_class" in reasons
    assert "dynamic_setattr" in reasons
    assert reasons.count("conditional_definition") == 1

def test_marks_syntax_error_without_crashing(tmp_path: Path) -> None:
    """验证个别文件存在语法错误时记录 syntax_error，不中断其余文件解析。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "broken.py").write_text("def bad(:\n    pass\n", encoding="utf-8")
    (pkg_dir / "good.py").write_text("def ok() -> int:\n    return 1\n", encoding="utf-8")
    table = extract_symbols(tmp_path)
    assert table.get_by_fqn("app.good.ok") is not None
    syntax_items = [
        item for item in table.unresolved if item.reason_code == "syntax_error"
    ]
    assert len(syntax_items) == 1
    assert syntax_items[0].start_line == 1

def test_missing_project_directory_raises(tmp_path: Path) -> None:
    """验证传入不存在的根目录时按契约抛出 NotADirectoryError。"""
    with pytest.raises(NotADirectoryError):
        extract_symbols(tmp_path / "missing")

def test_repeated_extraction_is_deterministic(tmp_path: Path) -> None:
    """验证多次运行抽取结果完全一致，顺序稳定。"""
    pkg_dir = tmp_path / "src" / "app"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
    (pkg_dir / "b.py").write_text("B = 2\n", encoding="utf-8")
    (pkg_dir / "a.py").write_text("A = 1\n", encoding="utf-8")
    first = extract_symbols(tmp_path)
    second = extract_symbols(tmp_path)
    assert first == second
    assert [s.fqn for s in first.symbols] == [
        "app",
        "app.a",
        "app.a.A",
        "app.b",
        "app.b.B",
    ]

def test_extracts_external_mini_repo_symbols() -> None:
    """验证外部独立工程 mini_repo 的核心符号与模块命名。"""
    mini_repo = REPO_ROOT / "eval" / "fixtures" / "mini_repo"
    table = extract_symbols(mini_repo)
    assert table.get_by_fqn("minisite.engine") is not None
    assert table.get_by_fqn("minisite.engine.BuildEngine") is not None
    assert table.get_by_fqn("minisite.engine.BuildEngine.build") is not None
    assert table.get_by_fqn("minisite.cli.main") is not None
    assert table.unresolved == ()