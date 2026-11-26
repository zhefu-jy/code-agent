"""W11 W /verify-spec 全链路与关键边界契约离线单元测试。"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
import pytest

from code_agent.cli import _parse_verify_spec_args, handle_verify_spec
from code_agent.spec import AuditVerdict

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_SPEC = REPO_ROOT / "eval" / "fixtures" / "sample_spec.md"
MINI_REPO = REPO_ROOT / "eval" / "fixtures" / "mini_repo"


class _FakeStructuredRunnable:
    """模拟 with_structured_output 返回的调用器。"""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> dict[str, Any]:
        return self._payload


class FakeParseModel:
    """S1 规范拆解离线替身。"""

    def __init__(self, rules_payload: list[dict[str, Any]]) -> None:
        self._payload = {"rules": rules_payload}

    def with_structured_output(
        self, schema: type[Any], **kwargs: Any
    ) -> _FakeStructuredRunnable:
        return _FakeStructuredRunnable(self._payload)


class _BoundFakeAuditModel:
    """模拟 model.bind_tools(...) 返回对象。"""

    def __init__(self, parent: "FakeAuditModel") -> None:
        self._parent = parent

    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> AIMessage:
        return self._parent._next()


class FakeAuditModel:
    """S3 审计循环离线替身，按队列顺序回放工具调用与四态判决。"""

    def __init__(self, script: Sequence[AIMessage]) -> None:
        self._script = list(script)

    def bind_tools(
        self, tools: Sequence[dict[str, Any]], **kwargs: Any
    ) -> _BoundFakeAuditModel:
        return _BoundFakeAuditModel(self)

    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> AIMessage:
        return self._next()

    def _next(self) -> AIMessage:
        if not self._script:
            raise AssertionError("FakeAuditModel 响应队列已耗尽")
        return self._script.pop(0)


def _ai_tool(call_id: str, name: str, args: dict[str, Any]) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": name, "args": args, "type": "tool_call"}],
        usage_metadata={"input_tokens": 100, "output_tokens": 30, "total_tokens": 130},
    )


def _ai_text(content: str) -> AIMessage:
    return AIMessage(
        content=content,
        usage_metadata={"input_tokens": 180, "output_tokens": 70, "total_tokens": 250},
    )


def test_parse_verify_spec_args_windows_paths_and_errors() -> None:
    """兼容 Windows 反斜杠与带引号路径，非法参数仅抛 ValueError 不退 REPL。"""
    parsed = _parse_verify_spec_args(
        r'eval\fixtures\sample_spec.md --repo eval\fixtures\mini_repo --out eval\runs\out.md --yes'
    )
    assert parsed.spec_file == r"eval\fixtures\sample_spec.md"
    assert parsed.repo_override == r"eval\fixtures\mini_repo"
    assert parsed.out_path == r"eval\runs\out.md"
    assert parsed.auto_confirm is True

    quoted = _parse_verify_spec_args(r'"dir with space\spec.md" -r "my repo\app" -o out.md -y')
    assert quoted.spec_file == r"dir with space\spec.md"
    assert quoted.repo_override == r"my repo\app"
    assert quoted.out_path == "out.md"
    assert quoted.auto_confirm is True

    for bad_arg in ("", "--repo", "--out", "--unknown spec.md", "a.md b.md"):
        with pytest.raises(ValueError):
            _parse_verify_spec_args(bad_arg)


def test_handle_verify_spec_full_pipeline_and_markdown_export(tmp_path: Path) -> None:
    """验证 S1 确认 -> S2 人工增补 -> S3 多态审计（含上游守卫缺失判 VIOLATED）-> Markdown 落盘。"""
    parse_model = FakeParseModel(
        [
            {
                "title": "max_posts 默认值与校验上限",
                "requirement": "SiteConfig.max_posts 默认值 <= 100，validate_config 拒绝 <= 0 并抛 ConfigError",
                "target_scope": "SiteConfig 与 validate_config",
                "conditions": ["默认上限 100", "必须为正整数"],
                "chunk_index": 1,
            },
            {
                "title": "渲染异常必须记录 warning 日志",
                "requirement": "BuildEngine.build 或上游 run_cli 捕获渲染异常时必须调用 logging.warning",
                "target_scope": "BuildEngine.build 及直接上游 run_cli",
                "conditions": ["若上游已记录 warning 可视为满足"],
                "chunk_index": 2,
            },
            {
                "title": "禁用 os.system",
                "requirement": "全仓严禁直接使用 os.system，必须使用 subprocess",
                "target_scope": "全仓源码",
                "conditions": [],
                "chunk_index": 3,
            },
            {
                "title": "生产配置 minisite.json 上限",
                "requirement": "若仓库存在 minisite.json，则 max_posts 不得超过 50",
                "target_scope": "生产配置文件 minisite.json",
                "conditions": ["缺少文件时不得臆断"],
                "chunk_index": 4,
            },
        ]
    )

    audit_model = FakeAuditModel(
        [
            # R1 (COMPLIANT): 读 config.py 正文确认 max_posts=100 且 <=0 抛 ConfigError
            _ai_tool(
                "r1_c1",
                "read_file_snippet",
                {"file_path": "minisite/config.py", "start_line": 14, "end_line": 60},
            ),
            _ai_text(
                "VERDICT: COMPLIANT\n"
                "【判决结论】\n- SiteConfig.max_posts 默认值为 100 且 validate_config 拒绝 <= 0 并抛出 ConfigError。\n"
                "【代码证据与事实】\n- [E1] [minisite/config.py:14-60] 定义 max_posts=100 并在 <= 0 时抛出 ConfigError。\n"
                "【已调查范围与缺失线索】\n- 已读取 minisite/config.py:14-60，无明显缺口。\n"
                "【整改建议/说明】\n- 符合规范。"
            ),
            # R2 (VIOLATED): 读取 engine.py 与上游 cli.py 正文，核实均无 logging.warning，允许放行 VIOLATED
            _ai_tool(
                "r2_c1",
                "read_file_snippet",
                {"file_path": "minisite/engine.py", "start_line": 80, "end_line": 102},
            ),
            _ai_tool(
                "r2_c2",
                "read_file_snippet",
                {"file_path": "minisite/cli.py", "start_line": 48, "end_line": 77},
            ),
            _ai_text(
                "VERDICT: VIOLATED\n"
                "【判决结论】\n- 已核实 BuildEngine.build 及上游 run_cli，两者捕获异常时均未调用 logging.warning。\n"
                "【代码证据与事实】\n"
                "- [E1] [minisite/engine.py:80-102] 捕获 Exception 后直接 raise RenderError，未记录 warning 日志。\n"
                "- [E2] [minisite/cli.py:48-77] 上游 run_cli 仅 print 到 stderr，未调用 logging.warning。\n"
                "【已调查范围与缺失线索】\n- 已核实 engine.py 与上游 cli.py，适用范围清晰。\n"
                "【整改建议/说明】\n- 在 BuildEngine.build 或 run_cli 异常分支补充 logging.warning。"
            ),
            # R3 (COMPLIANT via zero-hit search): 全仓搜索 os.system 为 0 命中
            _ai_tool("r3_c1", "search_text", {"pattern": "os.system"}),
            _ai_text(
                "VERDICT: COMPLIANT\n"
                "【判决结论】\n- 全仓搜索 os.system 为 0 命中，未发现违规调用。\n"
                "【代码证据与事实】\n- 未采集到直接源码正文证据（全仓 search_text 零命中）。\n"
                "【已调查范围与缺失线索】\n- 已在全仓执行 search_text('os.system') 返回 0 命中。\n"
                "【整改建议/说明】\n- 符合禁用规范。"
            ),
            # R4 (INSUFFICIENT_EVIDENCE): 搜索 *.json 无 minisite.json，不编造行号
            _ai_tool("r4_c1", "search_text", {"pattern": "max_posts", "file_pattern": "*.json"}),
            _ai_text(
                "VERDICT: INSUFFICIENT_EVIDENCE\n"
                "【判决结论】\n- 仓库中未找到 minisite.json 生产配置文件，无法核验其 max_posts 上限。\n"
                "【代码证据与事实】\n- 未采集到直接源码正文证据。\n"
                "【已调查范围与缺失线索】\n- 已检索 *.json 中的 max_posts（0 命中），缺失实际部署的 minisite.json。\n"
                "【整改建议/说明】\n- 请在提供实际生产配置文件后复核。"
            ),
        ]
    )

    # 模拟双人工确认环：
    # S1: all 确认全部草稿 -> n 不补录
    # S2: R1 回车确认 -> R2 按 a 手动补充上游 minisite/cli.py 后回车确认 -> R3 按 all 放行剩余
    scripted_inputs = iter(
        [
            "all",
            "n",
            "",
            "a",
            "minisite/cli.py",
            "run_cli",
            "48-77",
            "补充上游调用入口核查守卫",
            "",
            "all",
        ]
    )
    outputs: list[str] = []
    out_md = tmp_path / "spec_verify_001.md"

    report = handle_verify_spec(
        f'"{SAMPLE_SPEC}" --repo "{MINI_REPO}" --out "{out_md}"',
        repo_root=REPO_ROOT,
        retriever_type="bm25",
        parse_model=parse_model,
        audit_model=audit_model,
        input_fn=lambda _prompt: next(scripted_inputs),
        print_fn=outputs.append,
    )

    assert report is not None
    summary = report.summary()
    assert summary.total_rules == 4
    assert summary.compliant_count == 2
    assert summary.violated_count == 1
    assert summary.insufficient_evidence_count == 1
    assert summary.downgraded_count == 0

    verdicts = {res.rule.rule_id: res.verdict for res in report.results}
    assert verdicts == {
        "R1": AuditVerdict.COMPLIANT,
        "R2": AuditVerdict.VIOLATED,
        "R3": AuditVerdict.COMPLIANT,
        "R4": AuditVerdict.INSUFFICIENT_EVIDENCE,
    }
    assert report.results[1].had_human_candidate_intervention is True
    assert report.results[3].investigated_scope

    assert out_md.is_file()
    saved_md = out_md.read_text(encoding="utf-8")
    assert "## 四态审计矩阵对比表" in saved_md
    assert "## 核查配置与 S2 定位分离统计" in saved_md
    assert "+1 新增 / -0 排除" in saved_md


def test_handle_verify_spec_zero_hit_search_cannot_claim_violated() -> None:
    """关键防线：搜索 0 命中时即便模型硬判 VIOLATED，程序也必须强制降级为 INSUFFICIENT_EVIDENCE。"""
    parse_model = FakeParseModel(
        [
            {
                "title": "生产配置 minisite.json 上限",
                "requirement": "若仓库存在 minisite.json，则 max_posts 不得超过 50",
                "target_scope": "生产配置文件 minisite.json",
                "conditions": [],
                "chunk_index": 4,
            }
        ]
    )
    # 故意让模型在 0 命中后胡乱下 VIOLATED 判决
    audit_model = FakeAuditModel(
        [
            _ai_tool("c1", "search_text", {"pattern": "max_posts", "file_pattern": "*.json"}),
            _ai_text(
                "VERDICT: VIOLATED\n"
                "【判决结论】\n- 未搜索到 minisite.json，直接判定违规。\n"
                "【代码证据与事实】\n- 未采集到直接源码正文证据。\n"
                "【已调查范围与缺失线索】\n- 搜索 *.json 返回 0 命中。\n"
                "【整改建议/说明】\n- 补充配置文件。"
            ),
        ]
    )
    outputs: list[str] = []

    report = handle_verify_spec(
        f'"{SAMPLE_SPEC}" --repo "{MINI_REPO}" --yes',
        repo_root=REPO_ROOT,
        retriever_type="bm25",
        parse_model=parse_model,
        audit_model=audit_model,
        print_fn=outputs.append,
    )

    assert report is not None
    assert len(report.results) == 1
    res = report.results[0]
    assert res.raw_model_verdict == AuditVerdict.VIOLATED
    assert res.verdict == AuditVerdict.INSUFFICIENT_EVIDENCE
    assert res.was_downgraded is True
    assert "VERIFIED_BODY" in res.downgrade_reason
    assert any("search:max_posts (*.json) -> 0 hits" in item for item in res.investigated_scope)


def test_handle_verify_spec_missing_paths_graceful_return(tmp_path: Path) -> None:
    """不存在的规约文件或 --repo 目录应打印友好提示并返回 None，不抛未捕获异常。"""
    outputs_1: list[str] = []
    res_1 = handle_verify_spec(
        "nonexistent_spec.md",
        repo_root=tmp_path,
        print_fn=outputs_1.append,
    )
    assert res_1 is None
    assert any("未找到规约文件" in msg for msg in outputs_1)

    outputs_2: list[str] = []
    res_2 = handle_verify_spec(
        f'"{SAMPLE_SPEC}" --repo "{tmp_path / "missing_repo"}"',
        repo_root=REPO_ROOT,
        print_fn=outputs_2.append,
    )
    assert res_2 is None
    assert any("目标工程目录不存在" in msg for msg in outputs_2)