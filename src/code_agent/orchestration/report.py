"""调查循环三板块报告规范校验与高信噪比渲染器（W10 S3）。"""
from __future__ import annotations
from dataclasses import dataclass
import re
from code_agent.orchestration.investigator import InvestigationResult, InvestigationStatus

REQUIRED_REPORT_SECTIONS: tuple[str, ...] = (
    "【已确认代码事实】",
    "【架构推论与说明】",
    "【未核实缺口/盲区】",
)
_SECTION_LINE_PATTERN = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:\*{1,2}\s*)?"
    r"(【(?:已确认代码事实|架构推论与说明|未核实缺口/盲区)】)"
    r"(?:\s*\*{1,2})?\s*$"
)

@dataclass(frozen=True)
class ReportSectionAudit:
    """
    三互斥板块格式自检结果。
    Attributes:
        present_sections: 回答中按规范出现的板块标题元组。
        missing_sections: 回答中缺失的板块标题元组。
        is_compliant: 是否同时具备全部三个规定板块。
    """
    present_sections: tuple[str, ...]
    missing_sections: tuple[str, ...]
    @property
    def is_compliant(self) -> bool:
        """是否完整包含全部三个互斥板块。"""
        return len(self.missing_sections) == 0


def audit_report_sections(answer: str) -> ReportSectionAudit:
    """检查模型回答是否按规定包含了三个互斥板块标题。"""
    found: set[str] = set()
    for line in answer.splitlines():
        match = _SECTION_LINE_PATTERN.match(line)
        if match:
            found.add(match.group(1))
        else:
            # 容忍标题后紧跟冒号或简短说明的常见变体（如 "【已确认代码事实】："）
            stripped = line.lstrip("#* \t")
            for sec in REQUIRED_REPORT_SECTIONS:
                if stripped.startswith(sec):
                    found.add(sec)
    present = tuple(sec for sec in REQUIRED_REPORT_SECTIONS if sec in found)
    missing = tuple(sec for sec in REQUIRED_REPORT_SECTIONS if sec not in found)
    return ReportSectionAudit(present_sections=present, missing_sections=missing)



def format_investigation_report(
    result: InvestigationResult,
    *,
    show_evidence_ledger: bool = True,
) -> str:
    """
    将 InvestigationResult 格式化为高信噪比的终端/文本报告。
    仅展示调查状态、预算消耗、工具调用日志、回答正文与证据核验摘要，不输出模型思考链。
    """
    status_icon = {
        InvestigationStatus.COMPLETED: "✅",
        InvestigationStatus.BUDGET_EXHAUSTED: "⚠️",
        InvestigationStatus.MODEL_ERROR: "🛑",
    }.get(result.status, "•")
    lines: list[str] = [
        "=" * 64,
        f"🕵️  [代码调查报告] {status_icon} 状态: {result.status.value} ｜ 退出原因: {result.exit_reason}",
        f"❓ 问题: {result.question}",
        (
            f"📊 预算消耗: 轮数 {result.budget.turns_used} ｜ "
            f"工具 {result.budget.tool_calls_used} 次 ｜ "
            f"Tokens {result.budget.total_tokens} "
            f"(in {result.budget.input_tokens} / out {result.budget.output_tokens}) ｜ "
            f"耗时 {result.budget.elapsed_seconds:.2f}s"
        ),
    ]
    if result.used_summary_fallback:
        lines.append("ℹ️  说明: 本次触发了程序级确定性兜底输出（未依赖模型生成最终总结）。")
    if result.error_message:
        lines.append(f"🛑 异常信息: {result.error_message}")
    lines.append("-" * 64)
    lines.append(f"🛠️  [工具调用日志]（共 {len(result.tool_records)} 步）")
    if not result.tool_records:
        lines.append("   (未执行任何工具调用)")
    else:
        for rec in result.tool_records:
            flag = "✓" if rec.ok else "✗"
            lines.append(
                f"   [{flag}] T{rec.turn}/S{rec.step_index} {rec.summary} ({rec.duration_ms:.1f} ms)"
            )
    lines.append("-" * 64)
    lines.append("📝 [调查结论]")
    if not result.used_summary_fallback:
        sec_audit = audit_report_sections(result.answer)
        if not sec_audit.is_compliant:
            lines.append(
                f"⚠️  [板块规范提示] 模型回答缺少规定板块: {', '.join(sec_audit.missing_sections)}"
            )
    lines.append(result.answer or "(空回答)")
    lines.append("-" * 64)
    lines.append("🔎 [证据核验与底账]")
    ver = result.evidence_verification
    if ver is None:
        lines.append("   • 程序核验: 不适用（程序兜底或无模型结论）")
    else:
        verdict_tag = "⚠️ 存在未验证项/告警" if ver.has_unverified_claims else "✅ 绑定引用全部通过账本核验"
        bound_text = ", ".join(ver.bound_evidence_ids) if ver.bound_evidence_ids else "(无)"
        lines.append(f"   • 核验结论: {verdict_tag}")
        lines.append(
            f"   • 引用统计: 绑定 ID [{bound_text}] ｜ 正文覆盖 {ver.verified_count} ｜ "
            f"仅坐标/单行 {ver.coordinate_only_count} ｜ 未验证 {ver.unverified_count}"
        )
        if ver.warnings:
            lines.append("   • 核验告警:")
            for warn in ver.warnings:
                lines.append(f"      - {warn}")
    if show_evidence_ledger:
        lines.append(f"   • 证据底账（共 {len(result.evidence_items)} 条，供人工复核）:")
        if not result.evidence_items:
            lines.append("      (无已登记证据)")
        else:
            for item in result.evidence_items:
                trunc_tag = " [截断]" if item.is_truncated else ""
                note_tag = f" ｜ {item.note}" if item.note else ""
                lines.append(
                    f"      - [{item.id}] ({item.kind.value}) "
                    f"{item.file_path}:{item.line_range[0]}-{item.line_range[1]}"
                    f"{trunc_tag} [sha256:{item.content_hash[:8]}]{note_tag}"
                )
    lines.append("=" * 64)
    return "\n".join(lines)