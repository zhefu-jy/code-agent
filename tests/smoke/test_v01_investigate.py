"""v0.1 工程模块理解助手端到端流程冒烟测试（固定模型响应 + 真实 mini_repo 工具链）。"""

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
import pytest

from code_agent.analyzer.symbol_index import build_symbol_index
from code_agent.cli import handle_investigate
from code_agent.orchestration import (
    InvestigationBudget,
    InvestigationStatus,
    audit_report_sections,
    investigate,
)

MINI_REPO = Path(__file__).resolve().parents[2] / "eval" / "fixtures" / "mini_repo"


class _BoundFakeModel:
    """区分带工具探查阶段（is_bound=True）与无工具总结阶段（is_bound=False）。"""

    def __init__(self, parent: "FakeChatModel") -> None:
        self._parent = parent

    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> Any:
        return self._parent._next(is_bound=True)


class FakeChatModel:
    """按预设响应序列确定性驱动端到端调查流程的离线替身。"""

    def __init__(self, script: list[AIMessage]) -> None:
        self._script = list(script)
        self.invoke_modes: list[bool] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> _BoundFakeModel:
        return _BoundFakeModel(self)

    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> Any:
        return self._next(is_bound=False)

    def _next(self, is_bound: bool) -> AIMessage:
        self.invoke_modes.append(is_bound)
        if not self._script:
            raise AssertionError("FakeChatModel 响应队列已耗尽")
        return self._script.pop(0)


def _ai(
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> AIMessage:
    msg = AIMessage(content=content, tool_calls=tool_calls or [])
    msg.usage_metadata = {"input_tokens": 120, "output_tokens": 60, "total_tokens": 180}
    return msg


def test_smoke_v01_cli_investigate_happy_path(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """端到端冒烟：SymbolIndex + handle_investigate + 真实 ToolBox + 账本核验 + 三板块输出。"""
    symbol_index = build_symbol_index(MINI_REPO)
    final_answer = (
        "【已确认代码事实】\n"
        "- `BuildEngine.build` 先调用 `discover_documents()` 再写盘 HTML [E2] [minisite/engine.py:80-102]。\n"
        "【架构推论与说明】\n"
        "- 构建链路遵循先发现后渲染的两段式编排。\n"
        "【未核实缺口/盲区】\n"
        "- 未读取 `models.py` 内部 `render_html` 实现。"
    )
    model = FakeChatModel(
        [
            _ai(
                tool_calls=[
                    {"id": "c1", "name": "lookup_symbol", "args": {"name": "BuildEngine"}}
                ]
            ),
            _ai(
                tool_calls=[
                    {
                        "id": "c2",
                        "name": "read_file_snippet",
                        "args": {"file_path": "minisite/engine.py", "start_line": 80, "end_line": 102},
                    }
                ]
            ),
            _ai(content=final_answer),
        ]
    )

    handle_investigate(
        "BuildEngine.build 是如何生成页面的？",
        repo_root=MINI_REPO,
        symbol_index=symbol_index,
        model=model,
    )
    captured = capsys.readouterr().out

    assert "状态: completed" in captured
    assert "【已确认代码事实】" in captured
    assert "【架构推论与说明】" in captured
    assert "【未核实缺口/盲区】" in captured
    assert "绑定引用全部通过账本核验" in captured
    assert "[E2] (snippet_body) minisite/engine.py:80-102" in captured


def test_smoke_v01_budget_exhausted_uses_reserved_summary() -> None:
    """端到端冒烟：探查轮数耗尽后停止工具调用，切换无工具总结并正常收口。"""
    symbol_index = build_symbol_index(MINI_REPO)
    summary_answer = (
        "【已确认代码事实】\n"
        "- `run_cli` 入口位于 `minisite/cli.py` [E1] [minisite/cli.py:48-76]。\n"
        "【架构推论与说明】\n"
        "- 暂无。\n"
        "【未核实缺口/盲区】\n"
        "- 预算耗尽，尚未读取 `run_cli` 函数体正文。"
    )
    model = FakeChatModel(
        [
            _ai(
                tool_calls=[
                    {"id": "c1", "name": "lookup_symbol", "args": {"name": "run_cli"}}
                ]
            ),
            _ai(content=summary_answer),
        ]
    )

    result = investigate(
        project_root=MINI_REPO,
        question="run_cli 如何处理异常？",
        model=model,
        symbol_index=symbol_index,
        budget=InvestigationBudget(max_turns=2, reserve_summary_turns=1),
    )

    assert result.status == InvestigationStatus.BUDGET_EXHAUSTED
    assert result.exit_reason.startswith("explore_turns_exhausted")
    assert result.used_summary_fallback is False
    assert model.invoke_modes == [True, False]
    assert audit_report_sections(result.answer).is_compliant is True


def test_smoke_v01_loop_guard_and_hallucination_rejected() -> None:
    """端到端冒烟：重复调用被防死锁拦截，伪造 E# 与未读行号被程序核验拦截。"""
    symbol_index = build_symbol_index(MINI_REPO)
    hallucinated_answer = (
        "【已确认代码事实】\n"
        "- `Page.render_html` 在第 1~20 行渲染模板 [E99] [minisite/models.py:1-20]。\n"
        "【架构推论与说明】\n"
        "- 暂无。\n"
        "【未核实缺口/盲区】\n"
        "- 无。"
    )
    model = FakeChatModel(
        [
            _ai(
                tool_calls=[
                    {"id": "c1", "name": "lookup_symbol", "args": {"name": "BuildEngine"}}
                ]
            ),
            _ai(
                tool_calls=[
                    {"id": "c2", "name": "lookup_symbol", "args": {"name": "BuildEngine"}}
                ]
            ),
            _ai(content=hallucinated_answer),
        ]
    )

    result = investigate(
        project_root=MINI_REPO,
        question="Page 如何渲染？",
        model=model,
        symbol_index=symbol_index,
    )

    assert any(rec.error_code == "duplicate_tool_call" for rec in result.tool_records)
    assert result.evidence_verification is not None
    assert result.evidence_verification.has_unverified_claims is True
    assert "E99" in result.evidence_verification.unknown_evidence_ids
    assert result.evidence_verification.unverified_count == 1