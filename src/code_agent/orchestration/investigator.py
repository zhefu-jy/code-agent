"""基于原生 ReAct 循环与多维预算守卫的代码调查引擎（W10 S1）。"""
from __future__ import annotations
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
import json
from pathlib import Path
import time
from typing import Any, Protocol
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from loguru import logger
from code_agent.analyzer.symbol_index import SymbolIndex
from code_agent.core.model import get_chat_model
from code_agent.tools.registry import (
    ToolBox,
    get_tool_schemas,
    serialize_tool_result,
)

_DEFAULT_SYSTEM_PROMPT = (
    "你是一个严谨的本地工程代码调查助手。你必须通过调用给定工具核实代码实现，"
    "禁止猜测或编造未读取到的文件路径、符号名与行号。\n"
    "工作策略：\n"
    "1. 优先使用 lookup_symbol 或 search_text 定位目标模块与行号范围；\n"
    "2. 需要核实具体逻辑时调用 read_file_snippet 读取关键行号区间，遇到截断可用 next_cursor 续读；\n"
    "3. 需要追踪调用方时调用 find_references；\n"
    "4. 证据足够回答问题时直接输出清晰结论，并标明哪些部分尚未核实。"
)
_BUDGET_SUMMARY_INSTRUCTION = (
    "【系统预算通知】探查阶段预算已耗尽，工具调用已停止。"
    "请勿再请求调用任何工具，仅依据当前对话历史中已经通过工具实际获得的代码观测结果，"
    "输出一份简明清晰的阶段性总结，必须明确区分：\n"
    "1. 已核实的代码事实（仅写已读取/查到的文件、符号与行号）；\n"
    "2. 尚未核实的缺口与盲区。"
)

class ChatModelLike(Protocol):
    """兼容 LangChain BaseChatModel 与测试替身的最小模型协议。"""
    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> Any: ...
    def bind_tools(
        self, tools: Sequence[dict[str, Any]], **kwargs: Any
    ) -> Any: ...

class InvestigationStatus(str, Enum):
    """调查任务终态。"""
    COMPLETED = "completed"
    BUDGET_EXHAUSTED = "budget_exhausted"
    MODEL_ERROR = "model_error"

@dataclass(frozen=True)
class InvestigationBudget:
    """
    调查循环的多维预算配置与总结预留策略。
    Attributes:
        max_turns: 单次调查允许的对话总轮数上限（含预留总结轮次）。
        max_tool_calls: 允许执行的工具调用总次数上限。
        max_total_tokens: 单次会话允许累计的 Token 上限。
        soft_timeout_seconds: 单次调查的总耗时软超时（秒）。
        reserve_summary_turns: 为探查预算触顶后做最终总结预留的模型轮数。
        reserve_summary_tokens: 为最终总结预留的 Token 缓冲区。
        reserve_summary_seconds: 为最终总结预留的时间缓冲区（秒）。
    """
    max_turns: int = 4
    max_tool_calls: int = 8
    max_total_tokens: int = 16000
    soft_timeout_seconds: float = 45.0
    reserve_summary_turns: int = 1
    reserve_summary_tokens: int = 1200
    reserve_summary_seconds: float = 8.0

    def __post_init__(self) -> None:
        if self.max_turns < 1:
            raise ValueError("max_turns 必须 >= 1")
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls 不能为负数")
        if self.max_total_tokens < 1:
            raise ValueError("max_total_tokens 必须 >= 1")
        if self.soft_timeout_seconds <= 0:
            raise ValueError("soft_timeout_seconds 必须 > 0")
        if self.reserve_summary_turns < 0 or self.reserve_summary_turns >= self.max_turns:
            raise ValueError("reserve_summary_turns 必须满足 0 <= reserve < max_turns")
        if self.reserve_summary_tokens < 0:
            raise ValueError("reserve_summary_tokens 不能为负数")
        if self.reserve_summary_seconds < 0:
            raise ValueError("reserve_summary_seconds 不能为负数")

    @property
    def explore_max_turns(self) -> int:
        """探查阶段允许使用的最大轮数（扣除总结预留）。"""
        return self.max_turns - self.reserve_summary_turns

    @property
    def explore_token_limit(self) -> int:
        """探查阶段触及预留总结水位线的 Token 阈值。"""
        if self.reserve_summary_tokens >= self.max_total_tokens:
            return max(1, self.max_total_tokens // 2)
        return self.max_total_tokens - self.reserve_summary_tokens

    @property
    def explore_timeout_seconds(self) -> float:
        """探查阶段触及预留总结水位线的时间阈值（秒）。"""
        if self.reserve_summary_seconds >= self.soft_timeout_seconds:
            return self.soft_timeout_seconds * 0.75
        return self.soft_timeout_seconds - self.reserve_summary_seconds

@dataclass(frozen=True)
class BudgetSnapshot:
    """某时刻的多维预算消耗快照。"""
    turns_used: int
    tool_calls_used: int
    input_tokens: int
    output_tokens: int
    total_tokens: int
    elapsed_seconds: float
    exhausted_reason: str | None = None


@dataclass(frozen=True)
class ToolCallRecord:
    """单次工具调用的执行审计记录。"""
    turn: int
    step_index: int
    tool_call_id: str
    tool_name: str
    arguments: dict[str, Any]
    ok: bool
    is_truncated: bool
    returned_count: int
    remaining_count: int
    error_code: str | None
    summary: str
    duration_ms: float



@dataclass(frozen=True)
class InvestigationResult:
    """调查引擎执行完毕后的结构化输出契约。"""
    question: str
    status: InvestigationStatus
    answer: str
    exit_reason: str
    used_summary_fallback: bool
    budget: BudgetSnapshot
    tool_records: tuple[ToolCallRecord, ...]
    observations: tuple[str, ...]
    error_message: str | None = None

@dataclass(frozen=True)
class _ParsedToolCall:
    call_id: str
    name: str
    args: dict[str, Any]

class BudgetTracker:
    """多维预算追踪器，统一裁决“能否继续探查”与“是否还有余量做预留总结”。"""
    def __init__(
        self,
        budget: InvestigationBudget,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.budget = budget
        self._clock = clock
        self._start_time = clock()
        self.turns_used = 0
        self.tool_calls_used = 0
        self.input_tokens = 0
        self.output_tokens = 0
        self.total_tokens = 0
        self.exhausted_reason: str | None = None
    def elapsed_seconds(self) -> float:
        """返回自本次调查开始至今的耗时（秒）。"""
        return max(0.0, self._clock() - self._start_time)

    def record_model_turn(
        self, input_tokens: int, output_tokens: int, total_tokens: int
    ) -> None:
        """记录一轮模型调用消耗的轮数与 Token。"""
        self.turns_used += 1
        in_tok = max(0, input_tokens)
        out_tok = max(0, output_tokens)
        tot_tok = max(0, total_tokens) if total_tokens > 0 else (in_tok + out_tok)
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        self.total_tokens += tot_tok

    def record_tool_call(self) -> None:
        """记录一次实际工具执行次数。"""
        self.tool_calls_used += 1

    def check_explore_budget(self) -> str | None:
        """检查探查阶段预算是否仍有余量；触顶返回原因标识，否则返回 None。"""
        if self.turns_used >= self.budget.explore_max_turns:
            reason = f"explore_turns_exhausted({self.turns_used}/{self.budget.explore_max_turns})"
        elif self.tool_calls_used >= self.budget.max_tool_calls:
            reason = f"max_tool_calls_reached({self.tool_calls_used}/{self.budget.max_tool_calls})"
        elif self.total_tokens >= self.budget.explore_token_limit:
            reason = f"token_budget_reached({self.total_tokens}/{self.budget.explore_token_limit})"
        elif self.elapsed_seconds() >= self.budget.explore_timeout_seconds:
            reason = (
                f"soft_timeout_reached({self.elapsed_seconds():.2f}s/"
                f"{self.budget.explore_timeout_seconds:.2f}s)"
            )
        else:
            return None
        if self.exhausted_reason is None:
            self.exhausted_reason = reason
        return reason

    def can_dispatch_tool(self) -> str | None:
        """在单轮工具批处理中逐个校验是否还能执行下一个工具。"""
        if self.tool_calls_used >= self.budget.max_tool_calls:
            reason = f"max_tool_calls_reached({self.tool_calls_used}/{self.budget.max_tool_calls})"
        elif self.elapsed_seconds() >= self.budget.explore_timeout_seconds:
            reason = (
                f"soft_timeout_reached({self.elapsed_seconds():.2f}s/"
                f"{self.budget.explore_timeout_seconds:.2f}s)"
            )
        else:
            return None
        if self.exhausted_reason is None:
            self.exhausted_reason = reason
        return reason

    def can_run_reserved_summary(self) -> tuple[bool, str | None]:
        """判断是否还保留有足够的总预算执行最后一次“无工具总结”调用。"""
        if self.budget.reserve_summary_turns <= 0:
            return False, "no_summary_turn_reserved"
        if self.turns_used >= self.budget.max_turns:
            return False, f"total_turns_exhausted({self.turns_used}/{self.budget.max_turns})"
        if self.total_tokens >= self.budget.max_total_tokens:
            return (
                False,
                f"total_tokens_exhausted({self.total_tokens}/{self.budget.max_total_tokens})",
            )
        elapsed = self.elapsed_seconds()
        if elapsed >= self.budget.soft_timeout_seconds:
            return (
                False,
                f"hard_timeout_reached({elapsed:.2f}s/{self.budget.soft_timeout_seconds:.2f}s)",
            )
        return True, None

    def snapshot(self) -> BudgetSnapshot:
        """生成当前预算消耗快照。"""
        return BudgetSnapshot(
            turns_used=self.turns_used,
            tool_calls_used=self.tool_calls_used,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            total_tokens=self.total_tokens,
            elapsed_seconds=round(self.elapsed_seconds(), 4),
            exhausted_reason=self.exhausted_reason,
        )

def _extract_text_content(content: Any) -> str:
    """安全提取 AIMessage.content 文本，兼容 str 与多段 content block 列表。"""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text_val = item.get("text")
                if isinstance(text_val, str):
                    parts.append(text_val)
        return "\n".join(p for p in parts if p).strip()
    return str(content).strip() if content is not None else ""

def _to_int(val: Any) -> int:
    if isinstance(val, bool):
        return 0
    if isinstance(val, int):
        return val
    return 0

def _extract_token_usage(response: Any) -> tuple[int, int, int]:
    """从 LangChain AIMessage 提取 (input_tokens, output_tokens, total_tokens)。"""
    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, Mapping):
        in_tok = _to_int(usage.get("input_tokens"))
        out_tok = _to_int(usage.get("output_tokens"))
        tot_tok = _to_int(usage.get("total_tokens"))
        return in_tok, out_tok, tot_tok
    resp_meta = getattr(response, "response_metadata", None)
    if isinstance(resp_meta, Mapping):
        tok_usage = resp_meta.get("token_usage")
        if isinstance(tok_usage, Mapping):
            in_tok = _to_int(tok_usage.get("prompt_tokens"))
            out_tok = _to_int(tok_usage.get("completion_tokens"))
            tot_tok = _to_int(tok_usage.get("total_tokens"))
            return in_tok, out_tok, tot_tok
    return 0, 0, 0

def _parse_tool_args(raw_args: Any) -> dict[str, Any]:
    """将模型返回的工具参数规整为 dict；若为 JSON 字符串则尝试解码。"""
    if isinstance(raw_args, Mapping):
        return {str(k): v for k, v in raw_args.items()}
    if isinstance(raw_args, str):
        try:
            loaded = json.loads(raw_args)
            if isinstance(loaded, Mapping):
                return {str(k): v for k, v in loaded.items()}
        except json.JSONDecodeError:
            return {"_raw_args": raw_args}
    return {}

def _extract_tool_calls(response: Any, turn: int) -> list[_ParsedToolCall]:
    """从模型响应中提取标准 tool_calls 列表。"""
    raw_calls = getattr(response, "tool_calls", None)
    if not isinstance(raw_calls, Sequence) or isinstance(raw_calls, (str, bytes)):
        return []
    parsed: list[_ParsedToolCall] = []
    for idx, call in enumerate(raw_calls, start=1):
        if not isinstance(call, Mapping):
            continue
        name = str(call.get("name") or "").strip()
        if not name:
            continue
        call_id = str(call.get("id") or f"call_t{turn}_{idx}")
        args = _parse_tool_args(call.get("args"))
        parsed.append(_ParsedToolCall(call_id=call_id, name=name, args=args))
    return parsed

def _summarize_tool_observation(
    tool_name: str,
    args: Mapping[str, Any],
    ok: bool,
    returned_count: int,
    remaining_count: int,
    is_truncated: bool,
    error_code: str | None,
    error_message: str | None,
) -> str:
    """生成高信噪比的单步观测摘要，用于日志记录与程序级兜底输出。"""
    compact_args = ", ".join(f"{k}={v!r}" for k, v in args.items())
    if not ok:
        return f"{tool_name}({compact_args}) -> 失败 [{error_code}]: {error_message}"
    trunc_note = f", truncated=True, remaining={remaining_count}" if is_truncated else ""
    return f"{tool_name}({compact_args}) -> 成功 (returned={returned_count}{trunc_note})"

def _build_programmatic_fallback(
    question: str,
    status: InvestigationStatus,
    exit_reason: str,
    tool_records: Sequence[ToolCallRecord],
    observations: Sequence[str],
    error_message: str | None = None,
) -> str:
    """当模型不可用或总预算彻底耗尽时，由程序确定性输出已有证据摘要、缺口与退出原因。"""
    lines: list[str] = [
        f"调查已提前终止（状态：{status.value}，原因：{exit_reason}）。",
        f"原始问题：{question}",
    ]
    if error_message:
        lines.append(f"异常信息：{error_message}")
    lines.append("")
    lines.append("【已收集工具观测轨迹】")
    if not tool_records:
        lines.append("- 尚未成功执行任何工具调用。")
    else:
        for rec in tool_records:
            lines.append(f"- [Turn {rec.turn} / Step {rec.step_index}] {rec.summary}")
    if observations:
        lines.append("")
        lines.append("【已采集原始证据片段索引】")
        for idx, obs in enumerate(observations, start=1):
            lines.append(f"- 证据#{idx}: {obs}")
    lines.append("")
    lines.append("【未核实缺口】")
    if not tool_records:
        lines.append("- 尚未完成任何代码文件或符号核验，当前无法给出基于源码的结论。")
    else:
        lines.append(
            "- 受预算或模型可用性限制，未能继续追踪剩余调用链或由模型完成最终归纳，"
            "请结合上述已采集位置继续人工或分段核查。"
        )
    return "\n".join(lines)

class Investigator:
    """
    手写原生 ReAct 调查循环引擎。
    在多维预算（轮数、工具次数、Token、耗时）约束下调用 ToolBox 探查工程代码，
    并在预算耗尽时优先使用预留预算生成阶段性总结，或退回程序级确定性兜底。
    """
    def __init__(
        self,
        project_root: str | Path,
        model: ChatModelLike | None = None,
        symbol_index: SymbolIndex | None = None,
        budget: InvestigationBudget | None = None,
        system_prompt: str = _DEFAULT_SYSTEM_PROMPT,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.project_root = Path(project_root).resolve()
        self.model: ChatModelLike = (
            model if model is not None else get_chat_model(temperature=0.0)
        )
        self.toolbox = ToolBox(self.project_root, symbol_index=symbol_index)
        self.budget = budget if budget is not None else InvestigationBudget()
        self.system_prompt = system_prompt
        self._clock = clock

    def run(self, question: str) -> InvestigationResult:
        """围绕单个代码调查问题执行受控多步调查循环。"""
        cleaned_question = question.strip()
        tracker = BudgetTracker(self.budget, clock=self._clock)
        if not cleaned_question:
            return InvestigationResult(
                question=question,
                status=InvestigationStatus.MODEL_ERROR,
                answer="调查问题不能为空。",
                exit_reason="empty_question",
                used_summary_fallback=True,
                budget=tracker.snapshot(),
                tool_records=(),
                observations=(),
                error_message="empty_question",
            )
        schemas = get_tool_schemas()
        bound_model = self.model.bind_tools(schemas)
        messages: list[BaseMessage] = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=cleaned_question),
        ]
        tool_records: list[ToolCallRecord] = []
        observations: list[str] = []
        step_counter = 0
        while True:
            exhausted_reason = tracker.check_explore_budget()
            if exhausted_reason is not None:
                return self._finish_on_budget_exhausted(
                    question=cleaned_question,
                    exit_reason=exhausted_reason,
                    messages=messages,
                    tracker=tracker,
                    tool_records=tool_records,
                    observations=observations,
                )
            current_turn = tracker.turns_used + 1
            try:
                raw_response = bound_model.invoke(messages)
            except Exception as exc:
                logger.warning(f"[Investigator] Turn {current_turn} 模型调用异常: {exc}")
                return self._finish_on_model_error(
                    question=cleaned_question,
                    exit_reason="model_invoke_failed",
                    error_message=str(exc),
                    tracker=tracker,
                    tool_records=tool_records,
                    observations=observations,
                )
            in_tok, out_tok, tot_tok = _extract_token_usage(raw_response)
            tracker.record_model_turn(in_tok, out_tok, tot_tok)
            ai_msg = (
                raw_response
                if isinstance(raw_response, AIMessage)
                else AIMessage(content=_extract_text_content(raw_response))
            )
            messages.append(ai_msg)
            parsed_calls = _extract_tool_calls(ai_msg, current_turn)
            if not parsed_calls:
                final_answer = _extract_text_content(ai_msg.content)
                return InvestigationResult(
                    question=cleaned_question,
                    status=InvestigationStatus.COMPLETED,
                    answer=final_answer,
                    exit_reason="model_completed",
                    used_summary_fallback=False,
                    budget=tracker.snapshot(),
                    tool_records=tuple(tool_records),
                    observations=tuple(observations),
                )
            batch_stopped_reason: str | None = None
            for call in parsed_calls:
                if batch_stopped_reason is None:
                    batch_stopped_reason = tracker.can_dispatch_tool()
                if batch_stopped_reason is not None:
                    blocked_payload = json.dumps(
                        {
                            "ok": False,
                            "tool_name": call.name,
                            "error_code": "budget_exhausted",
                            "error_message": f"预算已耗尽（{batch_stopped_reason}），本次工具调用未执行。",
                        },
                        ensure_ascii=False,
                    )
                    messages.append(
                        ToolMessage(
                            content=blocked_payload,
                            tool_call_id=call.call_id,
                            name=call.name,
                        )
                    )
                    continue
                step_counter += 1
                t0 = self._clock()
                res = self.toolbox.dispatch(call.name, call.args)
                duration_ms = round(max(0.0, (self._clock() - t0) * 1000.0), 2)
                tracker.record_tool_call()
                summary = _summarize_tool_observation(
                    tool_name=call.name,
                    args=call.args,
                    ok=res.ok,
                    returned_count=res.returned_count,
                    remaining_count=res.remaining_count,
                    is_truncated=res.is_truncated,
                    error_code=res.error_code,
                    error_message=res.error_message,
                )
                tool_records.append(
                    ToolCallRecord(
                        turn=current_turn,
                        step_index=step_counter,
                        tool_call_id=call.call_id,
                        tool_name=call.name,
                        arguments=call.args,
                        ok=res.ok,
                        is_truncated=res.is_truncated,
                        returned_count=res.returned_count,
                        remaining_count=res.remaining_count,
                        error_code=res.error_code,
                        summary=summary,
                        duration_ms=duration_ms,
                    )
                )
                if res.ok:
                    observations.append(summary)
                messages.append(
                    ToolMessage(
                        content=serialize_tool_result(res),
                        tool_call_id=call.call_id,
                        name=call.name,
                    )
                )
            if batch_stopped_reason is not None:
                return self._finish_on_budget_exhausted(
                    question=cleaned_question,
                    exit_reason=batch_stopped_reason,
                    messages=messages,
                    tracker=tracker,
                    tool_records=tool_records,
                    observations=observations,
                )

    def _finish_on_budget_exhausted(
        self,
        question: str,
        exit_reason: str,
        messages: list[BaseMessage],
        tracker: BudgetTracker,
        tool_records: list[ToolCallRecord],
        observations: list[str],
    ) -> InvestigationResult:
        """预算耗尽时优先用预留预算请模型总结；若总预算已耗尽或总结失败，改由程序直接兜底。"""
        can_summarize, block_reason = tracker.can_run_reserved_summary()
        if not can_summarize:
            fallback_reason = f"{exit_reason}; summary_skipped={block_reason}"
            answer = _build_programmatic_fallback(
                question=question,
                status=InvestigationStatus.BUDGET_EXHAUSTED,
                exit_reason=fallback_reason,
                tool_records=tool_records,
                observations=observations,
            )
            return InvestigationResult(
                question=question,
                status=InvestigationStatus.BUDGET_EXHAUSTED,
                answer=answer,
                exit_reason=fallback_reason,
                used_summary_fallback=True,
                budget=tracker.snapshot(),
                tool_records=tuple(tool_records),
                observations=tuple(observations),
            )
        summary_messages = list(messages) + [HumanMessage(content=_BUDGET_SUMMARY_INSTRUCTION)]
        try:
            summary_response = self.model.invoke(summary_messages)
        except Exception as exc:
            logger.warning(f"[Investigator] 预留总结模型调用失败: {exc}")
            fallback_reason = f"{exit_reason}; summary_model_failed"
            answer = _build_programmatic_fallback(
                question=question,
                status=InvestigationStatus.BUDGET_EXHAUSTED,
                exit_reason=fallback_reason,
                tool_records=tool_records,
                observations=observations,
                error_message=str(exc),
            )
            return InvestigationResult(
                question=question,
                status=InvestigationStatus.BUDGET_EXHAUSTED,
                answer=answer,
                exit_reason=fallback_reason,
                used_summary_fallback=True,
                budget=tracker.snapshot(),
                tool_records=tuple(tool_records),
                observations=tuple(observations),
                error_message=str(exc),
            )
        in_tok, out_tok, tot_tok = _extract_token_usage(summary_response)
        tracker.record_model_turn(in_tok, out_tok, tot_tok)
        summary_text = _extract_text_content(getattr(summary_response, "content", summary_response))
        if not summary_text:
            summary_text = _build_programmatic_fallback(
                question=question,
                status=InvestigationStatus.BUDGET_EXHAUSTED,
                exit_reason=f"{exit_reason}; empty_summary_response",
                tool_records=tool_records,
                observations=observations,
            )
            used_fallback = True
        else:
            used_fallback = False
        return InvestigationResult(
            question=question,
            status=InvestigationStatus.BUDGET_EXHAUSTED,
            answer=summary_text,
            exit_reason=exit_reason,
            used_summary_fallback=used_fallback,
            budget=tracker.snapshot(),
            tool_records=tuple(tool_records),
            observations=tuple(observations),
        )

    def _finish_on_model_error(
        self,
        question: str,
        exit_reason: str,
        error_message: str,
        tracker: BudgetTracker,
        tool_records: list[ToolCallRecord],
        observations: list[str],
    ) -> InvestigationResult:
        """模型不可用时不再发起任何额外请求，直接由程序输出已有观测、缺口与退出原因。"""
        answer = _build_programmatic_fallback(
            question=question,
            status=InvestigationStatus.MODEL_ERROR,
            exit_reason=exit_reason,
            tool_records=tool_records,
            observations=observations,
            error_message=error_message,
        )
        return InvestigationResult(
            question=question,
            status=InvestigationStatus.MODEL_ERROR,
            answer=answer,
            exit_reason=exit_reason,
            used_summary_fallback=True,
            budget=tracker.snapshot(),
            tool_records=tuple(tool_records),
            observations=tuple(observations),
            error_message=error_message,
        )

def investigate(
    project_root: str | Path,
    question: str,
    model: ChatModelLike | None = None,
    symbol_index: SymbolIndex | None = None,
    budget: InvestigationBudget | None = None,
) -> InvestigationResult:
    """便捷入口：对指定工程执行一次受控多步代码调查。"""
    engine = Investigator(
        project_root=project_root,
        model=model,
        symbol_index=symbol_index,
        budget=budget,
    )
    return engine.run(question)