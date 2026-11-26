"""单条规范条款的多步调查与四态审计判定模块（W11 S3）。"""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from pathlib import Path
import re
import time

from pydantic import BaseModel, Field

from code_agent.analyzer.symbol_index import SymbolIndex
from code_agent.orchestration.evidence_ledger import (
    EvidenceItem,
    EvidenceKind,
    EvidenceVerification,
    resolve_project_revision,
)
from code_agent.orchestration.investigator import (
    ChatModelLike,
    InvestigationBudget,
    InvestigationResult,
    InvestigationStatus,
    Investigator,
    ToolCallRecord,
)
from code_agent.spec.rule_matcher import RuleCandidateEntry, RuleCandidateMap
from code_agent.spec.rule_parser import SpecRule


class AuditVerdict(str, Enum):
    """规范一致性四态审计判决枚举。"""

    COMPLIANT = "COMPLIANT"                        # 符合规约（有代码实现或全仓扫描佐证）
    VIOLATED = "VIOLATED"                          # 违反规约（明确指出违背位置与事实）
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"  # 证据不足（列出已查范围与缺失线索，不编造行号）
    NOT_APPLICABLE = "NOT_APPLICABLE"              # 不适用（明确说明不适用理由）


class AuditEvidenceRef(BaseModel):
    """绑定到单条规则审计结果的不可变证据索引快照（便于 JSON 持久化）。"""

    evidence_id: str = Field(description="账本证据编号（如 E1、E2）")
    kind: str = Field(description="证据级别（snippet_body / symbol_signature / text_match / reference_match）")
    file_path: str = Field(description="相对工程根目录的 POSIX 路径")
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    revision: str = Field(default="", description="取证时的代码修订版本")
    content_hash: str = Field(default="", description="实际返回片段的 SHA-256 哈希")


class AuditCostSummary(BaseModel):
    """单条规则审计的预算与成本统计。"""

    turns_used: int = 0
    tool_calls_used: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    elapsed_seconds: float = 0.0


class RuleAuditResult(BaseModel):
    """单条规范条款的完整四态审计结果契约。"""

    rule: SpecRule
    verdict: AuditVerdict = Field(description="经程序防幻觉降级闸门核验后的最终四态判决")
    raw_model_verdict: AuditVerdict | None = Field(
        default=None,
        description="模型原始宣称的四态判决（解析失败或未调模型时为 None）",
    )
    was_downgraded: bool = Field(
        default=False,
        description="是否因缺少正文证据、伪造引用或运行异常被程序强制降级为 INSUFFICIENT_EVIDENCE",
    )
    downgrade_reason: str = Field(default="", description="程序降级原因说明")
    summary: str = Field(default="", description="精炼判决结论")
    evidence_facts: str = Field(default="", description="绑定 [E#] 与行号的代码事实正文")
    investigated_scope: list[str] = Field(
        default_factory=list,
        description="程序确定性汇总的已调查文件、符号与搜索词范围",
    )
    missing_clues: str = Field(default="", description="缺失的关键线索或尚未核实的盲区")
    recommendation: str = Field(default="", description="整改建议或不适用/核查说明")
    raw_answer: str = Field(default="", description="Investigator 返回的完整原始回答文本")
    investigation_status: InvestigationStatus = Field(
        default=InvestigationStatus.COMPLETED,
        description="底层 W10 调查引擎终态",
    )
    exit_reason: str = Field(default="", description="调查退出原因")
    candidate_count: int = Field(default=0, description="S2 传入的生效候选数量")
    had_human_candidate_intervention: bool = Field(
        default=False,
        description="该规则在 S2 候选阶段是否经过人工增删辅助",
    )
    evidence_refs: list[AuditEvidenceRef] = Field(
        default_factory=list,
        description="本次审计采集并登记进账本的全部证据索引",
    )
    bound_evidence_ids: list[str] = Field(
        default_factory=list,
        description="回答中实际绑定且账本合法的证据编号列表",
    )
    verified_citation_count: int = 0
    coordinate_only_citation_count: int = 0
    unverified_citation_count: int = 0
    verification_warnings: list[str] = Field(default_factory=list)
    cost: AuditCostSummary = Field(default_factory=AuditCostSummary)


class SpecAuditSummary(BaseModel):
    """整份规约批量审计的四态分布与成本汇总。"""

    total_rules: int                  # 参与本次审计的规则总数
    compliant_count: int              # 最终判定为符合规约（COMPLIANT）的条数
    violated_count: int               # 最终判定为违反规约（VIOLATED）的条数
    insufficient_evidence_count: int  # 最终判定为证据不足（INSUFFICIENT_EVIDENCE）的条数
    not_applicable_count: int         # 最终判定为不适用（NOT_APPLICABLE）的条数
    downgraded_count: int             # 被程序防幻觉闸门强制降级为证据不足的条数
    total_tool_calls: int             # 全部规则累计执行的工具调用总次数
    total_tokens: int                 # 全部规则累计消耗的 Token 总量
    total_elapsed_seconds: float      # 全部规则审计累计耗时（秒）


class SpecAuditReport(BaseModel):
    """整份规约的规范一致性审计总报告。"""

    project_root: str
    revision: str = ""
    results: list[RuleAuditResult] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def summary(self) -> SpecAuditSummary:
        """汇总四态判决分布、降级条数与总成本。"""
        return SpecAuditSummary(
            total_rules=len(self.results),
            compliant_count=sum(1 for r in self.results if r.verdict == AuditVerdict.COMPLIANT),
            violated_count=sum(1 for r in self.results if r.verdict == AuditVerdict.VIOLATED),
            insufficient_evidence_count=sum(
                1 for r in self.results if r.verdict == AuditVerdict.INSUFFICIENT_EVIDENCE
            ),
            not_applicable_count=sum(
                1 for r in self.results if r.verdict == AuditVerdict.NOT_APPLICABLE
            ),
            downgraded_count=sum(1 for r in self.results if r.was_downgraded),
            total_tool_calls=sum(r.cost.tool_calls_used for r in self.results),
            total_tokens=sum(r.cost.total_tokens for r in self.results),
            total_elapsed_seconds=round(
                sum(r.cost.elapsed_seconds for r in self.results), 3
            ),
        )

_AUDIT_SYSTEM_PROMPT = (
    "你是一个严谨的软件工程规范一致性审计助手。"
    "你的任务是基于给定的一条规范条款与候选代码线索，主动调用工具核实本地工程的实际实现，"
    "并给出严格的四态审计判决，禁止猜测或编造任何未读取到的文件、符号或行号。\n"
    "审计纪律：\n"
    "1. 候选代码仅是粗筛起点：请优先使用 read_file_snippet 读取候选区间正文；"
    "必要时使用 search_text、lookup_symbol、find_references 追踪上游守卫、委托实现或全仓调用点；\n"
    "2. 仅通过 lookup_symbol 拿到的签名坐标或 search_text 拿到的单行预览，"
    "不代表已核实函数实现逻辑（可能是注释、字符串或死代码），"
    "下 VIOLATED 判决前必须调用 read_file_snippet 读取对应代码正文；\n"
    "3. 严禁仅凭搜索未找到候选就判 VIOLATED：\n"
    "   - COMPLIANT（符合规约）：代码正文证实满足约束；或针对“全仓禁用某接口”类条款，"
    "已通过 search_text 全仓核实无任何有效调用；\n"
    "   - VIOLATED（违反规约）：代码正文明确违背条款，或已核实规则适用范围及可能的上游守卫/委托实现后，"
    "有正文证据表明必需行为缺失；\n"
    "   - INSUFFICIENT_EVIDENCE（证据不足）：未能找到足够实现线索、存在动态调用/外部配置盲区或预算受限时，"
    "必须诚实判证据不足并列出已调查范围与缺失线索，严禁编造行号；\n"
    "   - NOT_APPLICABLE（不适用）：已核实工程确无该条款对应的业务场景或组件，说明理由；\n"
    "4. 每次工具成功返回都会在 evidence_items_meta 中附带证据编号（如 E1、E2），"
    "所有代码事实必须绑定 [E#] 与带方括号的物理行号 [file_path:start-end]（务必使用英文方括号 []，不要用反引号或省略方括号）；\n"
    "5. 最终回答必须严格按以下格式输出（第一行必须是 VERDICT）：\n"
    "VERDICT: COMPLIANT | VIOLATED | INSUFFICIENT_EVIDENCE | NOT_APPLICABLE\n"
    "【判决结论】\n"
    "- 用 1~3 句话明确说明最终判定及核心依据。\n"
    "【代码证据与事实】\n"
    "- 逐条列出通过工具核实的源码事实，每条必须写成 [E#] [文件路径:起始行-结束行] 格式（若无实证请写“未采集到直接源码正文证据”）。\n"
    "【已调查范围与缺失线索】\n"
    "- 列出本次实际检索/读取过的文件、符号与关键词，以及尚未核实或缺失的关键线索（若无缺失请写“无明显缺口”）。\n"
    "【整改建议/说明】\n"
    "- 违规时给出具体整改建议；合规/不适用/证据不足时给出简短补充说明。"
)

_VERDICT_PATTERN = re.compile(
    r"(?im)^\s*(?:\*\*)?VERDICT(?:\*\*)?\s*[:：]\s*(?:\*\*)?\[?"
    r"(COMPLIANT|VIOLATED|INSUFFICIENT_EVIDENCE|NOT_APPLICABLE)"
    r"\]?(?:\*\*)?"
)

_SECTION_HEADERS: tuple[tuple[str, str], ...] = (
    ("summary", "【判决结论】"),
    ("evidence_facts", "【代码证据与事实】"),
    ("missing_clues", "【已调查范围与缺失线索】"),
    ("recommendation", "【整改建议/说明】"),
)


def build_audit_question(entry: RuleCandidateEntry) -> str:
    """将单条已确认规则与 S2 生效候选打包为交给 Investigator 的审计任务描述。"""
    rule = entry.rule
    loc = rule.source_location
    cond_text = "；".join(rule.conditions) if rule.conditions else "无特殊前提或例外"
    lines: list[str] = [
        f"请审计规范条款 [{rule.rule_id}] 是否与当前工程代码实现一致：",
        f"- 规则标题: {rule.title}",
        f"- 核心要求: {rule.requirement}",
        f"- 适用范围: {rule.target_scope}",
        f"- 前提/单位/例外: {cond_text}",
        f"- 规约出处: {loc.file_path}:{loc.start_line}-{loc.end_line}（仅供溯源，条款内容已完整给出，无需调用工具读取规约文件本身）",
    ]
    if rule.human_note:
        lines.append(f"- 人工补充说明: {rule.human_note}")

    effective = entry.effective_candidates
    lines.append("")
    if effective:
        lines.append(
            f"【S2 粗筛生效候选代码（共 {len(effective)} 处，优先核查起点，仍需调工具读取正文核实）】"
        )
        for cand in effective:
            sym_part = f" | 符号: {cand.symbol_name}" if cand.symbol_name else ""
            lines.append(
                f"- [{cand.candidate_id}] {cand.file_path}:{cand.start_line}-{cand.end_line}{sym_part}"
            )
    else:
        lines.append(
            "【S2 粗筛生效候选代码（0 处）】\n"
            "- 粗筛阶段未命中候选代码。请先主动调用 search_text / lookup_symbol 核查工程是否存在相关实现或禁用接口调用；"
            "严禁仅凭未搜到就判 VIOLATED。"
        )

    lines.append("")
    lines.append("请调工具取证后，严格按 VERDICT + 四板块格式输出最终审计判决。")
    return "\n".join(lines)


def _parse_audit_answer(answer: str) -> tuple[AuditVerdict | None, dict[str, str]]:
    """从 Investigator 原始回答中提取四态 VERDICT 标签与四个固定板块正文。"""
    verdict: AuditVerdict | None = None
    match = _VERDICT_PATTERN.search(answer)
    if match is not None:
        verdict = AuditVerdict(match.group(1).upper())

    positions: list[tuple[int, int, str]] = []
    for key, header in _SECTION_HEADERS:
        idx = answer.find(header)
        if idx != -1:
            positions.append((idx, idx + len(header), key))
    positions.sort(key=lambda item: item[0])

    sections: dict[str, str] = {key: "" for key, _ in _SECTION_HEADERS}
    for i, (_, body_start, key) in enumerate(positions):
        body_end = positions[i + 1][0] if i + 1 < len(positions) else len(answer)
        sections[key] = answer[body_start:body_end].strip()

    # 若模型未严格输出【判决结论】标题，取去除 VERDICT 行后的首段非空文本作兜底摘要
    if not sections["summary"]:
        cleaned = _VERDICT_PATTERN.sub("", answer).strip()
        first_line = next((ln.strip() for ln in cleaned.splitlines() if ln.strip()), "")
        sections["summary"] = first_line[:300]

    return verdict, sections


def _collect_investigated_scope(
    entry: RuleCandidateEntry,
    tool_records: tuple[ToolCallRecord, ...],
) -> list[str]:
    """程序确定性汇总本次审计实际触达的候选位置、文件区间、符号与搜索关键词。"""
    scope: list[str] = []
    seen: set[str] = set()

    def _add(item: str) -> None:
        if item and item not in seen:
            seen.add(item)
            scope.append(item)

    for cand in entry.effective_candidates:
        _add(f"candidate:{cand.file_path}:{cand.start_line}-{cand.end_line}")

    for rec in tool_records:
        args = rec.arguments
        if rec.tool_name == "read_file_snippet":
            fp = str(args.get("file_path", "")).strip()
            s_line = args.get("start_line")
            e_line = args.get("end_line")
            _add(f"read:{fp}:{s_line}-{e_line}")
        elif rec.tool_name == "search_text":
            pat = str(args.get("pattern", "")).strip()
            fpat = args.get("file_pattern")
            suffix = f" ({fpat})" if fpat else ""
            _add(f"search:{pat}{suffix} -> {rec.returned_count} hits")
        elif rec.tool_name == "lookup_symbol":
            name = str(args.get("symbol_name") or args.get("name") or "").strip()
            _add(f"symbol:{name} -> {rec.returned_count} hits")
        elif rec.tool_name == "find_references":
            name = str(args.get("symbol_name", "")).strip()
            _add(f"refs:{name} -> {rec.returned_count} hits")

    return scope


def _convert_evidence_refs(items: tuple[EvidenceItem, ...]) -> list[AuditEvidenceRef]:
    """将 W10 账本的 EvidenceItem 元组转换为可 JSON 序列化的 AuditEvidenceRef 列表。"""
    return [
        AuditEvidenceRef(
            evidence_id=item.id,
            kind=item.kind.value,
            file_path=item.file_path,
            start_line=max(1, item.line_range[0]),
            end_line=max(max(1, item.line_range[0]), item.line_range[1]),
            revision=item.revision,
            content_hash=item.content_hash,
        )
        for item in items
    ]


def _apply_verdict_guard(
    *,
    raw_verdict: AuditVerdict | None,
    inv_result: InvestigationResult,
    verification: EvidenceVerification | None,
) -> tuple[AuditVerdict, bool, str, list[str]]:
    """
    程序级防幻觉降级闸门：结合 W10 证据账本核验与运行状态裁决最终四态。
    返回: (最终判决, 是否降级, 降级原因, 附加核验告警列表)。
    """
    extra_warnings: list[str] = []

    if inv_result.status == InvestigationStatus.MODEL_ERROR:
        return (
            AuditVerdict.INSUFFICIENT_EVIDENCE,
            True,
            f"底层调查引擎触发模型异常（{inv_result.exit_reason}）",
            extra_warnings,
        )

    if inv_result.used_summary_fallback:
        return (
            AuditVerdict.INSUFFICIENT_EVIDENCE,
            True,
            f"触发程序级预算兜底，未由模型完成有效审计判决（{inv_result.exit_reason}）",
            extra_warnings,
        )

    if raw_verdict is None:
        return (
            AuditVerdict.INSUFFICIENT_EVIDENCE,
            True,
            "模型回答中未解析到合法四态判决标签（VERDICT）",
            extra_warnings,
        )

    if inv_result.status == InvestigationStatus.BUDGET_EXHAUSTED:
        extra_warnings.append(
            f"探查阶段触及预算上限后由预留总结收口（{inv_result.exit_reason}），建议人工复核完整度"
        )

    if verification is not None:
        if verification.unknown_evidence_ids:
            bad_ids = ", ".join(verification.unknown_evidence_ids)
            return (
                AuditVerdict.INSUFFICIENT_EVIDENCE,
                True,
                f"回答引用了账本中不存在的伪造证据编号: {bad_ids}",
                extra_warnings,
            )
        if verification.unverified_count > 0:
            return (
                AuditVerdict.INSUFFICIENT_EVIDENCE,
                True,
                f"回答包含 {verification.unverified_count} 处未在已读账本中核验通过的物理行号引用",
                extra_warnings,
            )
        if verification.missing_evidence_binding and raw_verdict in (
            AuditVerdict.COMPLIANT,
            AuditVerdict.VIOLATED,
        ):
            return (
                AuditVerdict.INSUFFICIENT_EVIDENCE,
                True,
                "账本已采集证据，但回答未绑定任何 [E#] 或物理行号引用",
                extra_warnings,
            )

    verified_body = verification.verified_count if verification is not None else 0
    body_id_set = {
        item.id
        for item in inv_result.evidence_items
        if item.kind == EvidenceKind.SNIPPET_BODY
    }
    bound_body_count = (
        sum(1 for eid in verification.bound_evidence_ids if eid in body_id_set)
        if verification is not None
        else 0
    )
    has_verified_body_support = verified_body >= 1 or bound_body_count >= 1
    successful_tools = [r for r in inv_result.tool_records if r.ok]
    has_zero_hit_search = any(
        r.ok and r.tool_name == "search_text" and r.returned_count == 0
        for r in inv_result.tool_records
    )

    if raw_verdict == AuditVerdict.VIOLATED:
        if not has_verified_body_support:
            return (
                AuditVerdict.INSUFFICIENT_EVIDENCE,
                True,
                "判定 VIOLATED 必须至少包含 1 处经账本核验的源码正文引用（VERIFIED_BODY），"
                "禁止仅凭搜索单行预览、符号坐标或猜测判违规",
                extra_warnings,
            )
        if verified_body < 1 and bound_body_count >= 1:
            extra_warnings.append(
                f"已通过 {bound_body_count} 个已读正文证据编号 [E#] 核验实现正文"
                "（提示：部分路径未按 [file:start-end] 方括号格式书写）"
            )
    elif raw_verdict == AuditVerdict.COMPLIANT:
        if not has_verified_body_support:
            if has_zero_hit_search and not inv_result.evidence_items:
                extra_warnings.append(
                    "本条 COMPLIANT 基于全仓 search_text 零命中扫描得出（未读取具体实现正文），"
                    "适用于接口禁用类检查"
                )
            else:
                return (
                    AuditVerdict.INSUFFICIENT_EVIDENCE,
                    True,
                    "判定 COMPLIANT 缺少经账本核验的源码正文引用（VERIFIED_BODY），"
                    "仅凭符号坐标或未读源码不能放行合规结论",
                    extra_warnings,
                )
        elif verified_body < 1 and bound_body_count >= 1:
            extra_warnings.append(
                f"已通过 {bound_body_count} 个已读正文证据编号 [E#] 核验实现正文"
                "（提示：部分路径未按 [file:start-end] 方括号格式书写）"
            )
    elif raw_verdict == AuditVerdict.NOT_APPLICABLE:
        if not successful_tools:
            return (
                AuditVerdict.INSUFFICIENT_EVIDENCE,
                True,
                "未成功执行任何代码探查工具，不能直接判定规则不适用（NOT_APPLICABLE）",
                extra_warnings,
            )

    return raw_verdict, False, "", extra_warnings


def audit_rule(
    entry: RuleCandidateEntry,
    *,
    project_root: str | Path,
    model: ChatModelLike | None = None,
    symbol_index: SymbolIndex | None = None,
    budget: InvestigationBudget | None = None,
    skip_empty_candidates: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> RuleAuditResult:
    """围绕单条规范条款驱动 W10 Investigator 执行多步取证，并产出经程序闸门核验的四态判决。"""
    effective_candidates = entry.effective_candidates
    if skip_empty_candidates and not effective_candidates:
        scope = [f"candidate_query:{entry.query_used}"] if entry.query_used else []
        return RuleAuditResult(
            rule=entry.rule,
            verdict=AuditVerdict.INSUFFICIENT_EVIDENCE,
            raw_model_verdict=None,
            was_downgraded=False,
            summary="S2 粗筛未命中生效候选且启用了空候选跳过模式，未发起模型调查。",
            evidence_facts="未采集到直接源码正文证据。",
            investigated_scope=scope,
            missing_clues=(
                "自动粗筛未定位到候选代码实现；可人工补充候选文件，"
                "或关闭 skip_empty_candidates 允许调查 Agent 全仓检索。"
            ),
            recommendation="建议人工确认适用范围或补充候选实现后再审。",
            investigation_status=InvestigationStatus.COMPLETED,
            exit_reason="empty_candidates_skipped",
            candidate_count=0,
            had_human_candidate_intervention=entry.has_human_intervention,
        )

    question = build_audit_question(entry)
    engine = Investigator(
        project_root=project_root,
        model=model,
        symbol_index=symbol_index,
        budget=budget if budget is not None else InvestigationBudget(),
        system_prompt=_AUDIT_SYSTEM_PROMPT,
        clock=clock,
    )
    inv_result = engine.run(question)

    raw_verdict, sections = _parse_audit_answer(inv_result.answer)
    verification = inv_result.evidence_verification
    final_verdict, was_downgraded, downgrade_reason, extra_warnings = _apply_verdict_guard(
        raw_verdict=raw_verdict,
        inv_result=inv_result,
        verification=verification,
    )

    warnings: list[str] = []
    if verification is not None:
        warnings.extend(verification.warnings)
    warnings.extend(extra_warnings)

    investigated_scope = _collect_investigated_scope(entry, inv_result.tool_records)
    evidence_refs = _convert_evidence_refs(inv_result.evidence_items)

    return RuleAuditResult(
        rule=entry.rule,
        verdict=final_verdict,
        raw_model_verdict=raw_verdict,
        was_downgraded=was_downgraded,
        downgrade_reason=downgrade_reason,
        summary=sections["summary"],
        evidence_facts=sections["evidence_facts"],
        investigated_scope=investigated_scope,
        missing_clues=sections["missing_clues"],
        recommendation=sections["recommendation"],
        raw_answer=inv_result.answer,
        investigation_status=inv_result.status,
        exit_reason=inv_result.exit_reason,
        candidate_count=len(effective_candidates),
        had_human_candidate_intervention=entry.has_human_intervention,
        evidence_refs=evidence_refs,
        bound_evidence_ids=(
            list(verification.bound_evidence_ids) if verification is not None else []
        ),
        verified_citation_count=(
            verification.verified_count if verification is not None else 0
        ),
        coordinate_only_citation_count=(
            verification.coordinate_only_count if verification is not None else 0
        ),
        unverified_citation_count=(
            verification.unverified_count if verification is not None else 0
        ),
        verification_warnings=warnings,
        cost=AuditCostSummary(
            turns_used=inv_result.budget.turns_used,
            tool_calls_used=inv_result.budget.tool_calls_used,
            input_tokens=inv_result.budget.input_tokens,
            output_tokens=inv_result.budget.output_tokens,
            total_tokens=inv_result.budget.total_tokens,
            elapsed_seconds=round(inv_result.budget.elapsed_seconds, 3),
        ),
    )


def audit_candidate_map(
    candidate_map: RuleCandidateMap,
    *,
    model: ChatModelLike | None = None,
    symbol_index: SymbolIndex | None = None,
    budget: InvestigationBudget | None = None,
    skip_empty_candidates: bool = False,
    clock: Callable[[], float] = time.monotonic,
) -> SpecAuditReport:
    """按 RuleCandidateMap 逐条执行规范一致性审计并汇总成 SpecAuditReport。"""
    root = Path(candidate_map.project_root).resolve()
    revision = resolve_project_revision(root)
    results: list[RuleAuditResult] = []
    warnings: list[str] = list(candidate_map.warnings)

    for entry in candidate_map.entries:
        result = audit_rule(
            entry,
            project_root=root,
            model=model,
            symbol_index=symbol_index,
            budget=budget,
            skip_empty_candidates=skip_empty_candidates,
            clock=clock,
        )
        if result.was_downgraded:
            warnings.append(
                f"{result.rule.rule_id}: 已触发程序降级 -> INSUFFICIENT_EVIDENCE（{result.downgrade_reason}）"
            )
        results.append(result)

    return SpecAuditReport(
        project_root=root.as_posix(),
        revision=revision,
        results=results,
        warnings=warnings,
    )


_VERDICT_LABELS: dict[AuditVerdict, str] = {
    AuditVerdict.COMPLIANT: "✅ COMPLIANT（符合）",
    AuditVerdict.VIOLATED: "❌ VIOLATED（违规）",
    AuditVerdict.INSUFFICIENT_EVIDENCE: "⚠️ INSUFFICIENT_EVIDENCE（证据不足）",
    AuditVerdict.NOT_APPLICABLE: "➖ NOT_APPLICABLE（不适用）",
}


def _md_cell(text: str, *, max_len: int = 80) -> str:
    """清洗 Markdown 表格单元格内容（去除换行并转义竖线）。"""
    compact = " ".join(text.split()).replace("|", "\\|")
    if len(compact) > max_len:
        return compact[: max_len - 1] + "…"
    return compact or "-"


def format_audit_report_markdown(report: SpecAuditReport) -> str:
    """将 SpecAuditReport 渲染为包含四态矩阵对比表与逐条证据明细的 Markdown 报告。"""
    summary = report.summary()
    lines: list[str] = [
        "# 规范一致性审计报告",
        "",
        f"- **目标工程**: `{report.project_root}`",
        f"- **代码修订版本**: `{report.revision or 'unknown'}`",
        (
            f"- **四态汇总**: 共 {summary.total_rules} 条 | "
            f"✅ 符合 {summary.compliant_count} | "
            f"❌ 违规 {summary.violated_count} | "
            f"⚠️ 证据不足 {summary.insufficient_evidence_count} | "
            f"➖ 不适用 {summary.not_applicable_count} | "
            f"🛡️ 程序降级 {summary.downgraded_count}"
        ),
        (
            f"- **成本基线**: 工具调用 {summary.total_tool_calls} 次 | "
            f"总 Token {summary.total_tokens:,} | "
            f"总耗时 {summary.total_elapsed_seconds:.2f}s"
        ),
        "",
    ]

    if report.warnings:
        lines.append("## 审计告警")
        for warn in report.warnings:
            lines.append(f"- ⚠️ {warn}")
        lines.append("")

    if not report.results:
        lines.append("（本次无待审计的已激活规则。）")
        return "\n".join(lines)

    lines.extend(
        [
            "## 四态审计矩阵对比表",
            "",
            "| 规则 | 标题 | 最终判决 | 模型原判 | S2候选(人工介入) | 引用核验(正文/坐标/未验) | 工具/Token/耗时 | 核心结论 |",
            "|---|---|---|---|---|---|---|---|",
        ]
    )

    for res in report.results:
        verdict_tag = _VERDICT_LABELS[res.verdict]
        raw_tag = res.raw_model_verdict.value if res.raw_model_verdict is not None else "NONE"
        if res.was_downgraded:
            raw_tag = f"{raw_tag} → 降级"
        cand_cell = (
            f"{res.candidate_count} ({'有' if res.had_human_candidate_intervention else '无'})"
        )
        cite_cell = (
            f"{res.verified_citation_count}/"
            f"{res.coordinate_only_citation_count}/"
            f"{res.unverified_citation_count}"
        )
        cost_cell = (
            f"{res.cost.tool_calls_used}次 / {res.cost.total_tokens:,} / {res.cost.elapsed_seconds:.2f}s"
        )
        lines.append(
            f"| {res.rule.rule_id} | {_md_cell(res.rule.title, max_len=28)} | "
            f"{verdict_tag} | {raw_tag} | {cand_cell} | {cite_cell} | "
            f"{cost_cell} | {_md_cell(res.summary, max_len=60)} |"
        )

    lines.extend(["", "## 逐条规则审计明细", ""])
    for res in report.results:
        loc = res.rule.source_location
        lines.append(f"### [{res.rule.rule_id}] {res.rule.title} — {_VERDICT_LABELS[res.verdict]}")
        lines.append(
            f"- **规约要求**: {res.rule.requirement} "
            f"（出处: `{loc.file_path}:{loc.start_line}-{loc.end_line}`）"
        )
        lines.append(f"- **适用范围**: {res.rule.target_scope}")
        if res.was_downgraded:
            raw_name = res.raw_model_verdict.value if res.raw_model_verdict else "NONE"
            lines.append(
                f"- **🛡️ 程序防幻觉降级**: 模型原判 `{raw_name}` → 强制降级为 `INSUFFICIENT_EVIDENCE`"
                f"（原因：{res.downgrade_reason}）"
            )
        lines.append(f"- **判决结论**: {res.summary or '无'}")
        if res.evidence_facts:
            lines.append("")
            lines.append("**代码证据与事实**:")
            lines.append(res.evidence_facts)
        lines.append("")
        scope_text = "；".join(res.investigated_scope) if res.investigated_scope else "无工具探查记录"
        lines.append(f"- **已调查范围（程序实录）**: {scope_text}")
        if res.missing_clues:
            lines.append(f"- **缺失线索/盲区**: {res.missing_clues}")
        if res.recommendation:
            lines.append(f"- **整改建议/说明**: {res.recommendation}")
        if res.evidence_refs:
            ref_brief = ", ".join(
                f"{ref.evidence_id}({ref.file_path}:{ref.start_line}-{ref.end_line} [{ref.kind}])"
                for ref in res.evidence_refs
            )
            lines.append(f"- **账本证据索引**: {ref_brief}")
        if res.verification_warnings:
            for warn in res.verification_warnings:
                lines.append(f"- **核验提示**: ⚠️ {warn}")
        lines.append("")

    return "\n".join(lines)

