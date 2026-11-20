"""W9 W 手动组合探查实验：人工编排工具链追踪跨模块上下文并验证 Schema 与异常防御。"""

from collections import Counter
from pathlib import Path
import sys
from typing import Any

# 允许直接通过 `python scripts/manual_investigate.py` 运行
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC_DIR = _REPO_ROOT / "src"
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))

from code_agent.tools import (  # noqa: E402
    ToolBox,
    ToolResult,
    get_tool_schemas,
    serialize_tool_result,
)


def _configure_utf8_stdio() -> None:
    """确保 Windows 控制台下的 UTF-8 中文输出不发生编码报错。"""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


class InvestigationRecorder:
    """记录人工探查过程中的每一步工具调用、耗时与截断续读轨迹。"""

    def __init__(self, box: ToolBox) -> None:
        self.box = box
        self.counts: Counter[str] = Counter()
        self.logs: list[str] = []

    def call(self, tool_name: str, arguments: dict[str, Any]) -> ToolResult[Any]:
        """执行工具调用并登记调用次数与摘要日志。"""
        result = self.box.dispatch(tool_name, arguments)
        self.counts[tool_name] += 1
        step_no = sum(self.counts.values())
        status = "OK" if result.ok else f"ERR({result.error_code})"
        self.logs.append(
            f"[Step {step_no}] {tool_name}({arguments}) -> {status} | "
            f"returned={result.returned_count}, remaining={result.remaining_count}, "
            f"truncated={result.is_truncated}"
        )
        return result


def run_case_035_investigation(mini_repo: Path) -> None:
    """
    针对外部工程 mini_repo 的 Case 035 执行人工编排探查：
    “BuildEngine 在扫描文档时，是如何依据 SiteConfig.include_drafts 和 is_published 过滤草稿的？”
    """
    print("=" * 72)
    print("实验一：Case 035 跨模块草稿过滤链路人工编排探查 (mini_repo)")
    print("=" * 72)

    box = ToolBox(mini_repo)
    rec = InvestigationRecorder(box)

    # 1. search_text：全局文本定位 include_drafts 涉及哪些文件（源码 + 配置 + CLI）
    r_search = rec.call("search_text", {"pattern": "include_drafts"})
    if r_search.data is not None:
        print("\n[1] search_text('include_drafts') 命中预览:")
        print(r_search.data.formatted_preview)

    # 2. lookup_symbol：精确查 BuildEngine.discover_documents 与 is_published 的定义坐标
    r_lookup_discover = rec.call(
        "lookup_symbol", {"name": "BuildEngine.discover_documents"}
    )
    r_lookup_published = rec.call("lookup_symbol", {"name": "is_published"})
    print("\n[2] lookup_symbol 定位坐标:")
    for label, res in (
            ("discover_documents", r_lookup_discover),
            ("is_published", r_lookup_published),
    ):
        if res.ok and res.data is not None:
            for item in res.data.items:
                print(
                    f"  - {label}: {item.file_path}:{item.start_line}-{item.end_line} "
                    f"(fqn={item.fqn})"
                )

    # 3. find_references：核查谁调用了 discover_documents（含同文件自调与跨文件调用）
    r_refs = rec.call(
        "find_references", {"symbol_name": "BuildEngine.discover_documents"}
    )
    if r_refs.ok and r_refs.data is not None:
        print("\n[3] find_references('BuildEngine.discover_documents'):")
        print(r_refs.data.formatted_preview)

    # 4. 分页 read_file_snippet：刻意设置较紧预算 (max_lines=14, max_chars=500)，演练游标续读
    print("\n[4] 分页读取 minisite/engine.py 第 20~58 行 (discover_documents 实现):")
    cursor: dict[str, Any] = {
        "file_path": "minisite/engine.py",
        "start_line": 20,
        "end_line": 58,
        "max_lines": 14,
        "max_chars": 500,
    }
    engine_chunks: list[str] = []
    while True:
        r_read = rec.call("read_file_snippet", cursor)
        assert r_read.ok and r_read.data is not None
        engine_chunks.append(r_read.data.numbered_content)
        if not r_read.is_truncated or r_read.next_cursor is None:
            break
        cursor = {
            "file_path": "minisite/engine.py",
            "start_line": r_read.next_cursor["start_line"],
            "end_line": r_read.next_cursor["end_line"],
            "max_lines": 14,
            "max_chars": 500,
        }
    print(f"  共分页读取 {len(engine_chunks)} 次拼接完整函数体，关键第 54~58 行已核实:")
    print("\n".join(engine_chunks[-1].splitlines()[-5:]))

    # 点读 models.py (is_published) 与 config.py (SiteConfig 默认值)
    r_model = rec.call(
        "read_file_snippet",
        {"file_path": "minisite/models.py", "start_line": 20, "end_line": 26},
    )
    r_config = rec.call(
        "read_file_snippet",
        {"file_path": "minisite/config.py", "start_line": 14, "end_line": 26},
    )
    print("\n[5] 已核实辅助模块核心片段:")
    if r_model.data is not None:
        print("  --- minisite/models.py:20-26 ---")
        print(r_model.data.numbered_content)
    if r_config.data is not None:
        print("  --- minisite/config.py:14-26 ---")
        print(r_config.data.numbered_content)

    # 输出调用轨迹、已核实结论与未查明盲区
    print("\n[6] 人工探查调度统计与轨迹:")
    for line in rec.logs:
        print(" ", line)
    print(f"  总调用次数: {sum(rec.counts.values())} | 分项统计: {dict(rec.counts)}")

    print("\n[7] 实验结论与仍未查明部分（诚实划界）:")
    print(
        "  已确认事实: SiteConfig.include_drafts 默认为 False (minisite/config.py:23)；\n"
        "  BaseItem.is_published 读取 metadata['status']（默认 'published'），不等于 'draft' 即视为已发布 (minisite/models.py:21-24)；\n"
        "  BuildEngine.discover_documents 在第 55 行按 `if item.is_published or self.config.include_drafts:` 过滤留存 (minisite/engine.py:55-56)。"
    )
    print(
        "  仍未查明部分: \n"
        "  - SiteConfig.max_posts 字段是否也在后续扫描/构建阶段起过滤作用（尚未完整读取 load_config 与 build 全文）；\n"
        "  - cli.py 第 57 行之后对 include_drafts 的覆盖传参上下文尚未全文核实。"
    )


def run_schema_and_error_checks(mini_repo: Path) -> None:
    """验证标准 Tool Calling JSON Schema 与异常输入下的结构化错误防御。"""
    print("\n" + "=" * 72)
    print("实验二：Tool Calling Schema 核验与结构化错误防御验证")
    print("=" * 72)

    schemas = get_tool_schemas()
    print(f"\n已导出标准 Tool Calling Schema 共 {len(schemas)} 个:")
    for s in schemas:
        fn = s["function"]
        props = list(fn["parameters"]["properties"].keys())
        req = fn["parameters"]["required"]
        print(f"  - {fn['name']}: required={req}, properties={props}")

    box = ToolBox(mini_repo)
    error_cases: list[tuple[str, str, dict[str, Any]]] = [
        ("不存在的文件", "read_file_snippet", {"file_path": "minisite/missing.py"}),
        (
            "起始行号越界",
            "read_file_snippet",
            {"file_path": "minisite/engine.py", "start_line": 9999},
        ),
        ("路径逃逸防御", "read_file_snippet", {"file_path": "../secret.py"}),
        (
            "bool 冒充 int 类型拦截",
            "read_file_snippet",
            {"file_path": "minisite/engine.py", "start_line": True},
        ),
        ("空正则/非法正则", "search_text", {"pattern": "[bad", "is_regex": True}),
        ("未知工具调度", "drop_database", {"table": "users"}),
    ]

    print("\n异常输入防御测试:")
    for label, tool_name, args in error_cases:
        res = box.dispatch(tool_name, args)
        assert res.ok is False, f"期望失败但成功了: {label}"
        assert res.error_code and res.error_message
        print(f"  - {label}: ok={res.ok}, code={res.error_code}, msg={res.error_message}")

    # 抽样展示一条序列化后的完整 ToolMessage JSON（用于后续 W10 喂给大模型）
    sample_res = box.dispatch("lookup_symbol", {"name": "SiteConfig"})
    serialized = serialize_tool_result(sample_res)
    print(f"\nToolResult JSON 序列化样本长度: {len(serialized)} 字符")


def main() -> None:
    _configure_utf8_stdio()
    mini_repo = _REPO_ROOT / "eval" / "fixtures" / "mini_repo"
    run_case_035_investigation(mini_repo)
    run_schema_and_error_checks(mini_repo)


if __name__ == "__main__":
    main()