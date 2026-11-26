"""W11 S3 单条规则多步调查与四态审计判定单元测试（纯离线 Fake 模型）。"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage

from code_agent.analyzer.symbol_index import SymbolIndex, build_symbol_index
from code_agent.orchestration import InvestigationBudget, InvestigationStatus
from code_agent.spec import (
    AuditVerdict,
    CandidateSource,
    CodeCandidate,
    RuleCandidateEntry,
    RuleCandidateMap,
    RuleConfirmationStatus,
    RuleSourceLocation,
    SpecRule,
    audit_candidate_map,
    audit_rule,
    format_audit_report_markdown,
)


class _BoundFakeModel:
    """模拟 model.bind_tools(...) 返回的可调用对象。"""

    def __init__(self, parent: "FakeChatModel") -> None:
        self._parent = parent

    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> Any:
        return self._parent._handle_invoke(input, is_bound=True)


class FakeChatModel:
    """按预设响应队列回放模型工具调用与审计判决的离线替身。"""

    def __init__(self, script: Sequence[AIMessage | Exception]) -> None:
        self._script = list(script)
        self.invoke_history: list[tuple[bool, list[BaseMessage]]] = []

    def bind_tools(
        self, tools: Sequence[dict[str, Any]], **kwargs: Any
    ) -> _BoundFakeModel:
        return _BoundFakeModel(self)

    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> Any:
        return self._handle_invoke(input, is_bound=False)

    def _handle_invoke(self, messages: list[BaseMessage], is_bound: bool) -> Any:
        self.invoke_history.append((is_bound, list(messages)))
        if not self._script:
            raise AssertionError("FakeChatModel 响应队列已空，发生预期之外的模型请求")
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _ai_tool_call(
    call_id: str,
    name: str,
    args: dict[str, Any],
) -> AIMessage:
    """构造带工具调用与固定 Token 统计的 AIMessage。"""
    return AIMessage(
        content="",
        tool_calls=[{"id": call_id, "name": name, "args": args, "type": "tool_call"}],
        usage_metadata={"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
    )


def _ai_text(content: str) -> AIMessage:
    """构造纯文本终结回答 AIMessage。"""
    return AIMessage(
        content=content,
        usage_metadata={"input_tokens": 200, "output_tokens": 80, "total_tokens": 280},
    )


def _build_demo_repo(tmp_path: Path, *, include_os_system: bool = True) -> tuple[Path, SymbolIndex]:
    """构造测试用微型工程并构建 SymbolIndex。"""
    pkg = tmp_path / "src" / "demo"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "client.py").write_text(
        "\n".join(
            [
                "DEFAULT_TIMEOUT = 10",
                "",
                "def fetch_data(timeout: int = DEFAULT_TIMEOUT) -> str:",
                "    return f'timeout={timeout}'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    runner_body = (
        [
            "import os",
            "",
            "def run_shell(cmd: str) -> int:",
            "    return os.system(cmd)",
        ]
        if include_os_system
        else [
            "import subprocess",
            "",
            "def run_shell(cmd: list[str]) -> int:",
            "    return subprocess.run(cmd, check=False).returncode",
        ]
    )
    (pkg / "runner.py").write_text("\n".join(runner_body) + "\n", encoding="utf-8")
    return tmp_path, build_symbol_index(tmp_path)


def _make_entry(
    rule_id: str,
    *,
    title: str,
    requirement: str,
    target_scope: str,
    candidates: list[CodeCandidate] | None = None,
) -> RuleCandidateEntry:
    """构造单条规则的 S2 候选映射条目。"""
    rule = SpecRule(
        rule_id=rule_id,
        title=title,
        requirement=requirement,
        target_scope=target_scope,
        source_location=RuleSourceLocation(
            file_path="docs/spec.md",
            start_line=1,
            end_line=4,
            section_path="规范",
        ),
        status=RuleConfirmationStatus.CONFIRMED,
    )
    return RuleCandidateEntry(
        rule=rule,
        query_used=f"{title} | {requirement}",
        candidates=candidates or [],
    )


def test_audit_rule_compliant_with_verified_body(tmp_path: Path) -> None:
    """验证读取源码正文并正确绑定 [E1] 时放行 COMPLIANT。"""
    root, sym_idx = _build_demo_repo(tmp_path)
    entry = _make_entry(
        "R1",
        title="请求超时上限",
        requirement="默认超时上限必须为 10 秒",
        target_scope="client.fetch_data",
        candidates=[
            CodeCandidate(
                candidate_id="C1",
                file_path="src/demo/client.py",
                start_line=1,
                end_line=4,
                symbol_name="demo.client.fetch_data",
                sources=[CandidateSource.SYMBOL_INDEX],
                score=0.85,
            )
        ],
    )
    fake_model = FakeChatModel(
        [
            _ai_tool_call(
                "call_1",
                "read_file_snippet",
                {"file_path": "src/demo/client.py", "start_line": 1, "end_line": 4},
            ),
            _ai_text(
                "VERDICT: COMPLIANT\n"
                "【判决结论】\n"
                "- 默认超时常量与函数默认参数均为 10 秒，符合规约。\n"
                "【代码证据与事实】\n"
                "- DEFAULT_TIMEOUT = 10 且 fetch_data 使用该默认值 [E1] [src/demo/client.py:1-4]。\n"
                "【已调查范围与缺失线索】\n"
                "- 已完整读取 src/demo/client.py，无明显缺口。\n"
                "【整改建议/说明】\n"
                "- 无需整改。"
            ),
        ]
    )

    result = audit_rule(
        entry,
        project_root=root,
        model=fake_model,
        symbol_index=sym_idx,
        budget=InvestigationBudget(max_turns=4, max_tool_calls=4),
    )

    assert result.verdict == AuditVerdict.COMPLIANT
    assert result.raw_model_verdict == AuditVerdict.COMPLIANT
    assert result.was_downgraded is False
    assert result.verified_citation_count == 1
    assert result.bound_evidence_ids == ["E1"]
    assert result.cost.tool_calls_used == 1
    assert result.cost.total_tokens == 430
    assert any("read:src/demo/client.py:1-4" in s for s in result.investigated_scope)


def test_audit_rule_violated_with_verified_body(tmp_path: Path) -> None:
    """验证搜索命中后再读取正文核实，放行 VIOLATED 且兼容加粗 VERDICT 标签。"""
    root, sym_idx = _build_demo_repo(tmp_path, include_os_system=True)
    entry = _make_entry(
        "R2",
        title="禁止调用 os.system",
        requirement="严禁直接调用 os.system，必须使用 subprocess",
        target_scope="全仓代码",
    )
    fake_model = FakeChatModel(
        [
            _ai_tool_call("call_1", "search_text", {"pattern": "os.system"}),
            _ai_tool_call(
                "call_2",
                "read_file_snippet",
                {"file_path": "src/demo/runner.py", "start_line": 1, "end_line": 4},
            ),
            _ai_text(
                "**VERDICT**: **VIOLATED**\n"
                "【判决结论】\n"
                "- runner.py 中的 run_shell 直接调用了被禁用的 os.system。\n"
                "【代码证据与事实】\n"
                "- 第 4 行存在实际可执行调用 `return os.system(cmd)` [E2] [src/demo/runner.py:3-4]。\n"
                "【已调查范围与缺失线索】\n"
                "- 已全仓搜索 os.system 并读取 src/demo/runner.py 正文。\n"
                "【整改建议/说明】\n"
                "- 替换为 subprocess.run 并显式传参列表。"
            ),
        ]
    )

    result = audit_rule(entry, project_root=root, model=fake_model, symbol_index=sym_idx)

    assert result.verdict == AuditVerdict.VIOLATED
    assert result.was_downgraded is False
    assert result.verified_citation_count == 1
    assert result.cost.tool_calls_used == 2


def test_violated_without_body_read_is_downgraded(tmp_path: Path) -> None:
    """验证仅凭 search_text 单行预览（未读正文）判 VIOLATED 会被程序强制降级。"""
    root, sym_idx = _build_demo_repo(tmp_path, include_os_system=True)
    entry = _make_entry(
        "R2",
        title="禁止调用 os.system",
        requirement="严禁直接调用 os.system",
        target_scope="全仓代码",
    )
    fake_model = FakeChatModel(
        [
            _ai_tool_call("call_1", "search_text", {"pattern": "os.system"}),
            _ai_text(
                "VERDICT: VIOLATED\n"
                "【判决结论】\n"
                "- 搜索命中 os.system，判定违规。\n"
                "【代码证据与事实】\n"
                "- 搜索单行命中 [E1] [src/demo/runner.py:4-4]。"
            ),
        ]
    )

    result = audit_rule(entry, project_root=root, model=fake_model, symbol_index=sym_idx)

    assert result.verdict == AuditVerdict.INSUFFICIENT_EVIDENCE
    assert result.raw_model_verdict == AuditVerdict.VIOLATED
    assert result.was_downgraded is True
    assert "VERIFIED_BODY" in result.downgrade_reason
    assert result.coordinate_only_citation_count == 1


def test_fabricated_evidence_id_forces_downgrade(tmp_path: Path) -> None:
    """验证模型伪造不存在的 [E99] 时被账本拦下并强制降级。"""
    root, sym_idx = _build_demo_repo(tmp_path)
    entry = _make_entry(
        "R1",
        title="请求超时上限",
        requirement="默认超时上限必须为 10 秒",
        target_scope="client",
    )
    fake_model = FakeChatModel(
        [
            _ai_tool_call(
                "call_1",
                "read_file_snippet",
                {"file_path": "src/demo/client.py", "start_line": 1, "end_line": 4},
            ),
            _ai_text(
                "VERDICT: COMPLIANT\n"
                "【判决结论】\n"
                "- 超时合规。\n"
                "【代码证据与事实】\n"
                "- 见 [E99] [src/demo/client.py:1-4]。"
            ),
        ]
    )

    result = audit_rule(entry, project_root=root, model=fake_model, symbol_index=sym_idx)

    assert result.verdict == AuditVerdict.INSUFFICIENT_EVIDENCE
    assert result.was_downgraded is True
    assert "E99" in result.downgrade_reason


def test_zero_hit_prohibition_scan_allows_compliant_with_notice(tmp_path: Path) -> None:
    """验证全仓禁用类规则经 search_text 零命中扫描可合理判 COMPLIANT 并附告警提示。"""
    root, sym_idx = _build_demo_repo(tmp_path, include_os_system=False)
    entry = _make_entry(
        "R2",
        title="禁止调用 os.system",
        requirement="严禁全仓调用 os.system",
        target_scope="全仓代码",
    )
    fake_model = FakeChatModel(
        [
            _ai_tool_call("call_1", "search_text", {"pattern": "os.system"}),
            _ai_text(
                "VERDICT: COMPLIANT\n"
                "【判决结论】\n"
                "- 全仓搜索 os.system 返回 0 命中，未发现违规调用。\n"
                "【代码证据与事实】\n"
                "- 未采集到直接源码正文证据（全仓检索 0 命中）。\n"
                "【已调查范围与缺失线索】\n"
                "- 已执行全仓 search_text(os.system)，无明显缺口。"
            ),
        ]
    )

    result = audit_rule(entry, project_root=root, model=fake_model, symbol_index=sym_idx)

    assert result.verdict == AuditVerdict.COMPLIANT
    assert result.was_downgraded is False
    assert any("零命中" in w for w in result.verification_warnings)


def test_not_applicable_requires_probe_and_empty_skip_mode(tmp_path: Path) -> None:
    """验证 NOT_APPLICABLE 必须先探查仓库，以及 skip_empty_candidates 零开销短路模式。"""
    root, sym_idx = _build_demo_repo(tmp_path)
    entry = _make_entry(
        "R3",
        title="S3 上传器加密约束",
        requirement="S3Uploader 必须启用 KMS 加密",
        target_scope="部署上传模块",
    )

    # 1) 零工具调用直接宣称 NOT_APPLICABLE -> 必须降级
    no_probe_model = FakeChatModel(
        [_ai_text("VERDICT: NOT_APPLICABLE\n【判决结论】\n- 猜测本项目没有上传模块。")]
    )
    res_no_probe = audit_rule(
        entry, project_root=root, model=no_probe_model, symbol_index=sym_idx
    )
    assert res_no_probe.verdict == AuditVerdict.INSUFFICIENT_EVIDENCE
    assert res_no_probe.was_downgraded is True

    # 2) 先 search_text 核实后再判 NOT_APPLICABLE -> 放行
    probed_model = FakeChatModel(
        [
            _ai_tool_call("call_1", "search_text", {"pattern": "S3Uploader"}),
            _ai_text("VERDICT: NOT_APPLICABLE\n【判决结论】\n- 已全仓检索，工程无 S3 上传模块。"),
        ]
    )
    res_probed = audit_rule(
        entry, project_root=root, model=probed_model, symbol_index=sym_idx
    )
    assert res_probed.verdict == AuditVerdict.NOT_APPLICABLE
    assert res_probed.was_downgraded is False

    # 3) skip_empty_candidates=True 直接短路，不消耗任何模型请求
    unused_model = FakeChatModel([])
    res_skipped = audit_rule(
        entry,
        project_root=root,
        model=unused_model,
        symbol_index=sym_idx,
        skip_empty_candidates=True,
    )
    assert res_skipped.verdict == AuditVerdict.INSUFFICIENT_EVIDENCE
    assert res_skipped.exit_reason == "empty_candidates_skipped"
    assert unused_model.invoke_history == []


def test_audit_candidate_map_and_markdown_report_with_model_error(tmp_path: Path) -> None:
    """验证批量审计汇总、模型异常降级、JSON 序列化及 Markdown 矩阵报告渲染。"""
    root, sym_idx = _build_demo_repo(tmp_path)
    e1 = _make_entry(
        "R1",
        title="超时控制",
        requirement="超时必须为 10 秒",
        target_scope="client",
    )
    e2 = _make_entry(
        "R2",
        title="日志要求",
        requirement="异常必须打 warning",
        target_scope="runner",
    )
    cmap = RuleCandidateMap(project_root=root.as_posix(), entries=[e1, e2])

    fake_model = FakeChatModel(
        [
            _ai_tool_call(
                "call_1",
                "read_file_snippet",
                {"file_path": "src/demo/client.py", "start_line": 1, "end_line": 4},
            ),
            _ai_text(
                "VERDICT: COMPLIANT\n"
                "【判决结论】\n- 超时为 10 秒，合规。\n"
                "【代码证据与事实】\n- DEFAULT_TIMEOUT = 10 [E1] [src/demo/client.py:1-4]。"
            ),
            RuntimeError("upstream timeout"),
        ]
    )

    report = audit_candidate_map(cmap, model=fake_model, symbol_index=sym_idx)
    summary = report.summary()

    assert summary.total_rules == 2
    assert summary.compliant_count == 1
    assert summary.insufficient_evidence_count == 1
    assert summary.downgraded_count == 1
    assert report.results[1].investigation_status == InvestigationStatus.MODEL_ERROR
    assert report.results[1].was_downgraded is True

    # 可完整 JSON 持久化
    dumped = report.model_dump_json()
    assert '"COMPLIANT"' in dumped and '"INSUFFICIENT_EVIDENCE"' in dumped

    md = format_audit_report_markdown(report)
    assert "## 四态审计矩阵对比表" in md
    assert "程序防幻觉降级" in md
    assert "E1(src/demo/client.py:1-4 [snippet_body])" in md


def test_bound_snippet_body_id_with_backtick_path_is_accepted(tmp_path: Path) -> None:
    """验证已读取正文并绑定合法 SNIPPET_BODY 编号 [E1] 时，即使路径用反引号书写也不误降级。"""
    root, sym_idx = _build_demo_repo(tmp_path, include_os_system=True)
    entry = _make_entry(
        "R2",
        title="禁止调用 os.system",
        requirement="严禁直接调用 os.system",
        target_scope="runner",
    )
    fake_model = FakeChatModel(
        [
            _ai_tool_call(
                "call_1",
                "read_file_snippet",
                {"file_path": "src/demo/runner.py", "start_line": 1, "end_line": 4},
            ),
            _ai_text(
                "VERDICT: VIOLATED\n"
                "【判决结论】\n- runner.py 直接调用了 os.system。\n"
                "【代码证据与事实】\n- [E1] `src/demo/runner.py:3-4` 直接调用 `os.system(cmd)`。"
            ),
        ]
    )

    result = audit_rule(entry, project_root=root, model=fake_model, symbol_index=sym_idx)

    assert result.verdict == AuditVerdict.VIOLATED
    assert result.was_downgraded is False
    assert result.bound_evidence_ids == ["E1"]
    assert any("已读正文证据编号 [E#]" in w for w in result.verification_warnings)
