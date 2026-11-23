"""W10 S1 调查循环引擎与多维预算守卫单元测试（纯离线 Fake 模型）。"""

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
import pytest

from code_agent.orchestration import (
    InvestigationBudget,
    InvestigationStatus,
    Investigator,
    investigate,
)


class _BoundFakeModel:
    """模拟 model.bind_tools(...) 返回的可调用对象，用于区分带工具调用与无工具总结。"""

    def __init__(self, parent: "FakeChatModel") -> None:
        self._parent = parent

    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> Any:
        return self._parent._handle_invoke(input, is_bound=True)


class FakeChatModel:
    """按预设响应队列确定性回放模型行为的离线测试替身。"""

    def __init__(self, script: list[AIMessage | Exception]) -> None:
        self._script = list(script)
        self.bind_calls: list[list[dict[str, Any]]] = []
        self.invoke_history: list[tuple[bool, list[BaseMessage]]] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> _BoundFakeModel:
        self.bind_calls.append(list(tools))
        return _BoundFakeModel(self)

    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> Any:
        return self._handle_invoke(input, is_bound=False)

    def _handle_invoke(self, messages: list[BaseMessage], is_bound: bool) -> Any:
        self.invoke_history.append((is_bound, list(messages)))
        if not self._script:
            raise AssertionError("FakeChatModel 响应队列已空，发生了预期之外的模型请求")
        next_item = self._script.pop(0)
        if isinstance(next_item, Exception):
            raise next_item
        return next_item


class FakeClock:
    """可控单调时钟，用于在 0ms 内确定性测试软超时与硬超时边界。"""

    def __init__(self, initial: float = 0.0) -> None:
        self.now = initial

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _make_ai_msg(
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
    input_tokens: int = 100,
    output_tokens: int = 40,
) -> AIMessage:
    msg = AIMessage(content=content, tool_calls=tool_calls or [])
    msg.usage_metadata = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
    }
    return msg


def _setup_demo_repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "\n".join(
            [
                "def compute_fee(amount: int) -> int:",
                "    return amount + 5",
                "",
                "def checkout(amount: int) -> int:",
                "    return compute_fee(amount)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def test_investigator_completes_multi_step_loop(tmp_path: Path) -> None:
    """验证预算内多步探查（lookup -> read -> 结论）能正常收敛为 COMPLETED。"""
    repo = _setup_demo_repo(tmp_path)
    model = FakeChatModel(
        [
            _make_ai_msg(
                tool_calls=[
                    {
                        "id": "c1",
                        "name": "lookup_symbol",
                        "args": {"name": "compute_fee"},
                    }
                ]
            ),
            _make_ai_msg(
                tool_calls=[
                    {
                        "id": "c2",
                        "name": "read_file_snippet",
                        "args": {"file_path": "app.py", "start_line": 1, "end_line": 2},
                    }
                ]
            ),
            _make_ai_msg(content="compute_fee 在 app.py:1-2 定义，逻辑为 amount + 5。"),
        ]
    )

    result = investigate(repo, "compute_fee 做了什么？", model=model)

    assert result.status == InvestigationStatus.COMPLETED
    assert result.used_summary_fallback is False
    assert result.exit_reason == "model_completed"
    assert "amount + 5" in result.answer
    assert result.budget.turns_used == 3
    assert result.budget.tool_calls_used == 2
    assert result.budget.total_tokens == 420
    assert len(result.tool_records) == 2
    assert all(rec.ok for rec in result.tool_records)
    assert len(model.bind_calls) == 1
    assert len(model.bind_calls[0]) == 4


def test_turn_budget_exhausted_uses_reserved_unbound_summary(tmp_path: Path) -> None:
    """验证探查轮数耗尽时，利用预留 1 轮调用无工具模型完成总结且状态为 BUDGET_EXHAUSTED。"""
    repo = _setup_demo_repo(tmp_path)
    budget = InvestigationBudget(max_turns=4, reserve_summary_turns=1)
    model = FakeChatModel(
        [
            _make_ai_msg(
                tool_calls=[{"id": "t1", "name": "lookup_symbol", "args": {"name": "compute_fee"}}]
            ),
            _make_ai_msg(
                tool_calls=[{"id": "t2", "name": "lookup_symbol", "args": {"name": "checkout"}}]
            ),
            _make_ai_msg(
                tool_calls=[{"id": "t3", "name": "search_text", "args": {"pattern": "compute_fee"}}]
            ),
            _make_ai_msg(content="已查明两处符号定义位置，函数体实现尚未读完。"),
        ]
    )

    result = investigate(repo, "分析结账流程", model=model, budget=budget)

    assert result.status == InvestigationStatus.BUDGET_EXHAUSTED
    assert result.used_summary_fallback is False
    assert "explore_turns_exhausted(3/3)" in result.exit_reason
    assert result.budget.turns_used == 4
    assert result.budget.tool_calls_used == 3
    assert len(model.invoke_history) == 4
    # 前 3 轮为绑定工具调用，第 4 轮总结必须走未绑定工具的裸模型调用
    assert [is_bound for is_bound, _ in model.invoke_history] == [True, True, True, False]


def test_tool_call_budget_caps_single_turn_batch_safely(tmp_path: Path) -> None:
    """验证单轮并发多 tool_calls 超出上限时拦截多余调用并补齐 ToolMessage 协议。"""
    repo = _setup_demo_repo(tmp_path)
    budget = InvestigationBudget(max_turns=4, max_tool_calls=2, reserve_summary_turns=1)
    model = FakeChatModel(
        [
            _make_ai_msg(
                tool_calls=[
                    {"id": "b1", "name": "lookup_symbol", "args": {"name": "compute_fee"}},
                    {"id": "b2", "name": "lookup_symbol", "args": {"name": "checkout"}},
                    {"id": "b3", "name": "search_text", "args": {"pattern": "amount"}},
                ]
            ),
            _make_ai_msg(content="仅完成前两个符号定位，第 3 个调用受工具预算限制未执行。"),
        ]
    )

    result = investigate(repo, "批量查询", model=model, budget=budget)

    assert result.status == InvestigationStatus.BUDGET_EXHAUSTED
    assert result.used_summary_fallback is False
    assert result.budget.tool_calls_used == 2
    assert len(result.tool_records) == 2

    # 总结请求发送的消息里，3 个 tool_call_id 必须都有对应的 ToolMessage
    _, summary_msgs = model.invoke_history[-1]
    tool_msgs = [m for m in summary_msgs if isinstance(m, ToolMessage)]
    assert [m.tool_call_id for m in tool_msgs] == ["b1", "b2", "b3"]
    assert "budget_exhausted" in str(tool_msgs[2].content)


def test_token_hard_cap_skips_model_and_uses_programmatic_fallback(tmp_path: Path) -> None:
    """验证单轮直接吃穿总 Token 上限时，不再请求模型而由程序直接输出兜底报告。"""
    repo = _setup_demo_repo(tmp_path)
    budget = InvestigationBudget(
        max_turns=4,
        max_total_tokens=500,
        reserve_summary_tokens=150,
    )
    model = FakeChatModel(
        [
            _make_ai_msg(
                tool_calls=[{"id": "k1", "name": "lookup_symbol", "args": {"name": "compute_fee"}}],
                input_tokens=400,
                output_tokens=150,  # 累计 550 >= max_total_tokens(500)
            ),
        ]
    )

    result = investigate(repo, "Token 爆表测试", model=model, budget=budget)

    assert result.status == InvestigationStatus.BUDGET_EXHAUSTED
    assert result.used_summary_fallback is True
    assert "total_tokens_exhausted" in result.exit_reason
    assert len(model.invoke_history) == 1
    assert "lookup_symbol" in result.answer
    assert "【未核实缺口】" in result.answer


def test_soft_timeout_triggers_reserved_summary_or_hard_fallback(tmp_path: Path) -> None:
    """验证注入时钟触及软超时预留线时进入总结，彻底超时时跳过模型直接兜底。"""
    repo = _setup_demo_repo(tmp_path)

    # 场景 A：仅触及探查软超时阈值 (10 - 3 = 7s)，仍可消耗预留时间做总结
    clock_a = FakeClock()

    class AdvancingModel(FakeChatModel):
        def _handle_invoke(self, messages: list[BaseMessage], is_bound: bool) -> Any:
            if is_bound:
                clock_a.advance(7.5)
            return super()._handle_invoke(messages, is_bound)

    model_a = AdvancingModel(
        [
            _make_ai_msg(
                tool_calls=[{"id": "s1", "name": "lookup_symbol", "args": {"name": "compute_fee"}}]
            ),
            _make_ai_msg(content="时间紧张，已定位 compute_fee 定义位置。"),
        ]
    )
    budget = InvestigationBudget(soft_timeout_seconds=10.0, reserve_summary_seconds=3.0)
    res_a = Investigator(repo, model=model_a, budget=budget, clock=clock_a).run("超时测试 A")
    assert res_a.status == InvestigationStatus.BUDGET_EXHAUSTED
    assert res_a.used_summary_fallback is False
    assert "soft_timeout_reached" in res_a.exit_reason

    # 场景 B：直接超出总软超时上限 (10.0s)，跳过总结请求由程序兜底
    clock_b = FakeClock()

    class HardTimeoutModel(FakeChatModel):
        def _handle_invoke(self, messages: list[BaseMessage], is_bound: bool) -> Any:
            clock_b.advance(11.0)
            return super()._handle_invoke(messages, is_bound)

    model_b = HardTimeoutModel(
        [
            _make_ai_msg(
                tool_calls=[{"id": "s2", "name": "lookup_symbol", "args": {"name": "checkout"}}]
            ),
        ]
    )
    res_b = Investigator(repo, model=model_b, budget=budget, clock=clock_b).run("超时测试 B")
    assert res_b.status == InvestigationStatus.BUDGET_EXHAUSTED
    assert res_b.used_summary_fallback is True
    assert "hard_timeout_reached" in res_b.exit_reason
    assert len(model_b.invoke_history) == 1


def test_model_error_preserves_prior_observations_without_retry(tmp_path: Path) -> None:
    """验证探查中途模型抛异常时，不额外请求模型，输出 MODEL_ERROR 与已有工具轨迹。"""
    repo = _setup_demo_repo(tmp_path)
    model = FakeChatModel(
        [
            _make_ai_msg(
                tool_calls=[{"id": "e1", "name": "lookup_symbol", "args": {"name": "compute_fee"}}]
            ),
            RuntimeError("DeepSeek API 503"),
        ]
    )

    result = investigate(repo, "异常恢复测试", model=model)

    assert result.status == InvestigationStatus.MODEL_ERROR
    assert result.used_summary_fallback is True
    assert result.error_message == "DeepSeek API 503"
    assert len(result.tool_records) == 1
    assert "compute_fee" in result.answer
    assert "DeepSeek API 503" in result.answer


def test_budget_validation_rejects_invalid_values() -> None:
    """验证 InvestigationBudget 非法参数会在构造期立即报错。"""
    with pytest.raises(ValueError):
        InvestigationBudget(max_turns=0)
    with pytest.raises(ValueError):
        InvestigationBudget(max_turns=2, reserve_summary_turns=2)
    with pytest.raises(ValueError):
        InvestigationBudget(soft_timeout_seconds=0.0)