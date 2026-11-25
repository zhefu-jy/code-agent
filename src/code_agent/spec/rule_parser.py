"""Markdown 规范文档条款拆解与人工确认机制（W11 S1）。"""
from __future__ import annotations

from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from code_agent.core.model import get_chat_model
from code_agent.index.bm25_index import CodeChunk
from code_agent.index.markdown_chunker import MarkdownDocChunker

class StructuredRunnableLike(Protocol):
    """支持 invoke 的结构化输出调用器协议（兼容 LangChain 与测试替身）。"""
    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> Any: ...


class StructuredModelLike(Protocol):
    """支持 with_structured_output 的最小模型协议。"""
    def with_structured_output(
        self, schema: type[BaseModel], **kwargs: Any
    ) -> StructuredRunnableLike: ...

class RuleConfirmationStatus(str, Enum):
    """规范规则草稿的人工确认状态。"""
    DRAFT = "draft"          # 模型刚提取的初始草稿，尚未人工核对
    CONFIRMED = "confirmed"  # 人工原样确认有效
    EDITED = "edited"        # 经人工修正适用范围、单位或例外后确认
    SKIPPED = "skipped"      # 人工判定为非约束/背景描述，不进入后续核查


class RuleSourceLocation(BaseModel):
    """规范条款在原始 Markdown 文档中的物理溯源位置。"""
    file_path: str = Field(description="规约文件的相对路径或展示路径")
    start_line: int = Field(ge=1, description="条款起始行号（1-based）")
    end_line: int = Field(ge=1, description="条款结束行号（1-based）")
    section_path: str = Field(default="", description="所属 Markdown 标题面包屑路径")


class SpecRule(BaseModel):
    """单条结构化规范条款契约。"""
    rule_id: str = Field(description="规则编号（如 R1、R2）")
    title: str = Field(description="简短清晰的规则标题")
    requirement: str = Field(description="核心可核验约束要求")
    target_scope: str = Field(description="规则适用的代码模块、函数或场景范围")
    conditions: list[str] = Field(
        default_factory=list,
        description="前提条件、数值单位换算与例外情况列表",
    )
    source_location: RuleSourceLocation = Field(description="规约文档物理出处")
    status: RuleConfirmationStatus = Field(
        default=RuleConfirmationStatus.DRAFT,
        description="人工确认状态",
    )
    human_note: str = Field(default="", description="人工确认或修正说明")
    @property
    def is_active(self) -> bool:
        """是否已激活并可进入下游候选定位与审计。"""
        return self.status in (
            RuleConfirmationStatus.CONFIRMED,
            RuleConfirmationStatus.EDITED,
        )

class _ExtractedRuleDraft(BaseModel):
    """供 LLM 结构化输出使用的单条规则草稿中间结构。"""
    title: str = Field(description="简短规则标题")
    requirement: str = Field(description="清晰、可代码核验的具体要求")
    target_scope: str = Field(description="适用对象或代码范围（如 HTTP 客户端、所有 except ConnectionError 分支）")
    conditions: list[str] = Field(
        default_factory=list,
        description="触发条件、数值单位要求（如 10秒=10000毫秒）或明确的例外情况",
    )
    chunk_index: int = Field(
        ge=0,
        description="该规则出处对应的输入片段编号 [Chunk N] 中的整数 N",
    )
    start_line: int | None = Field(
        default=None,
        description="可选：条款起始物理行号，需位于对应 Chunk 范围内",
    )
    end_line: int | None = Field(
        default=None,
        description="可选：条款结束物理行号，需位于对应 Chunk 范围内",
    )

class _ExtractedRuleBatch(BaseModel):
    """模型单次返回的结构化规则草稿集合。"""
    rules: list[_ExtractedRuleDraft] = Field(default_factory=list)


class RuleParseResult(BaseModel):
    """规范文档拆解输出容器。"""
    spec_path: str
    rules: list[SpecRule] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    def active_rules(self) -> list[SpecRule]:
        """返回已确认或已修正（排除草稿与跳过）的待核查规则。"""
        return [rule for rule in self.rules if rule.is_active]


_RULE_EXTRACTION_SYSTEM_PROMPT = (
    "你是一个严谨的软件工程规范拆解助手。"
    "你的任务是从给定的 Markdown 技术规范片段中，提取出**可被代码实现核验**的具体约束条款草稿。\n"
    "拆解原则：\n"
    "1. 只提取明确的行为约束、数值上限/下限、异常处理要求或接口禁用/替代要求；"
    "纯背景介绍、目录说明或空泛愿景不要拆成规则；\n"
    "2. 若同一小节包含多条独立约束，拆分为多条独立规则；\n"
    "3. target_scope 必须写明规则作用对象（如“所有外部 HTTP 请求”、“捕获 ConnectionError 的分支”、“全仓代码”）；\n"
    "4. conditions 必须保留前置条件、单位口径（如“10 秒（即 10000 ms）”）与例外情况（如“健康检查接口除外”）；\n"
    "5. chunk_index 必须严格填写对应片段头部标注的 [Chunk N] 编号，不得编造不存在的编号或行号。"
)

def _format_chunks_for_prompt(chunks: list[CodeChunk]) -> str:
    """将带物理行号的文档切块格式化为可锚定的提示词上下文。"""
    blocks: list[str] = []
    for idx, chunk in enumerate(chunks):
        section = chunk.title_path or "正文"
        header = (
            f"[Chunk {idx}] 章节: {section} | "
            f"物理行号: {chunk.start_line}-{chunk.end_line}"
        )
        blocks.append(f"{header}\n{chunk.content}")
    return "\n\n---\n\n".join(blocks)

def _resolve_spec_display_path(
    spec_path: Path, project_root: Path | None
) -> str:
    """生成规范的 POSIX 展示路径（优先相对 project_root）。"""
    if project_root is not None:
        try:
            return spec_path.resolve().relative_to(project_root.resolve()).as_posix()
        except ValueError:
            pass
    return spec_path.as_posix()

def _ground_source_location(
    draft: _ExtractedRuleDraft,
    chunks: list[CodeChunk],
    display_path: str,
    warnings: list[str],
    rule_id: str,
) -> RuleSourceLocation:
    """将模型返回的 chunk_index 与行号锚定到真实切片物理边界，防止行号幻觉。"""
    if draft.chunk_index < 0 or draft.chunk_index >= len(chunks):
        fallback_chunk = chunks[0]
        warnings.append(
            f"{rule_id}: 模型返回越界 chunk_index={draft.chunk_index}，已回退锚定至 Chunk 0 "
            f"({fallback_chunk.start_line}-{fallback_chunk.end_line})"
        )
        return RuleSourceLocation(
            file_path=display_path,
            start_line=fallback_chunk.start_line,
            end_line=fallback_chunk.end_line,
            section_path=fallback_chunk.title_path,
        )
    chunk = chunks[draft.chunk_index]
    start = draft.start_line
    end = draft.end_line
    # 模型给出的行号必须落在所属切片的真实物理边界内，否则回退并告警
    if (
        start is None
        or end is None
        or start > end
        or start < chunk.start_line
        or end > chunk.end_line
    ):
        if start is not None or end is not None:
            warnings.append(
                f"{rule_id}: 模型给出的行号 ({start}-{end}) 超出所属切片 "
                f"({chunk.start_line}-{chunk.end_line})，已自动夹紧至切片真实区间"
            )
        start = chunk.start_line
        end = chunk.end_line
    return RuleSourceLocation(
        file_path=display_path,
        start_line=start,
        end_line=end,
        section_path=chunk.title_path,
    )

def parse_spec_rules(
    spec_path: str | Path,
    *,
    project_root: str | Path | None = None,
    model: StructuredModelLike | None = None,
    chunker: MarkdownDocChunker | None = None,
) -> RuleParseResult:
    """
    解析指定 Markdown 规约文档，提取待人工确认的结构化规则草稿列表。
    Args:
        spec_path: Markdown 规约文件路径。
        project_root: 可选工程根目录，用于将出处路径归一化为相对路径。
        model: 可选模型实例（默认使用 get_chat_model(temperature=0.0)）。
        chunker: 可选 Markdown 分块器实例。
    """
    path = Path(spec_path)
    root_path = Path(project_root) if project_root is not None else None
    display_path = _resolve_spec_display_path(path, root_path)
    if not path.is_file():
        return RuleParseResult(
            spec_path=display_path,
            warnings=[f"规约文件不存在或不是常规文件: {display_path}"],
        )
    doc_chunker = chunker if chunker is not None else MarkdownDocChunker()
    chunks = doc_chunker.chunk_file(path)
    if not chunks:
        return RuleParseResult(
            spec_path=display_path,
            warnings=[f"规约文件为空或非 Markdown 格式，未切分出有效段落: {display_path}"],
        )
    actual_model: StructuredModelLike = (
        model if model is not None else get_chat_model(temperature=0.0)
    )
    structured_llm = actual_model.with_structured_output(_ExtractedRuleBatch)
    prompt_body = _format_chunks_for_prompt(chunks)
    messages: list[BaseMessage] = [
        SystemMessage(content=_RULE_EXTRACTION_SYSTEM_PROMPT),
        HumanMessage(
            content=(
                f"规约文件：{display_path}\n"
                f"请基于以下切片提取所有需要代码核验的规范条款草稿：\n\n{prompt_body}"
            )
        ),
    ]
    warnings: list[str] = []
    try:
        raw_batch = structured_llm.invoke(messages)
        batch = (
            raw_batch
            if isinstance(raw_batch, _ExtractedRuleBatch)
            else _ExtractedRuleBatch.model_validate(raw_batch)
        )
    except Exception as exc:
        return RuleParseResult(
            spec_path=display_path,
            warnings=[f"调用模型拆解规范失败: {exc}"],
        )
    rules: list[SpecRule] = []
    for idx, draft in enumerate(batch.rules, start=1):
        rule_id = f"R{idx}"
        location = _ground_source_location(
            draft=draft,
            chunks=chunks,
            display_path=display_path,
            warnings=warnings,
            rule_id=rule_id,
        )
        rules.append(
            SpecRule(
                rule_id=rule_id,
                title=draft.title.strip() or f"未命名规则 {rule_id}",
                requirement=draft.requirement.strip(),
                target_scope=draft.target_scope.strip() or "未指定范围（请人工补充）",
                conditions=[c.strip() for c in draft.conditions if c.strip()],
                source_location=location,
                status=RuleConfirmationStatus.DRAFT,
            )
        )
    if not rules:
        warnings.append("模型未从当前规约中识别出需要代码核验的约束条款。")
    return RuleParseResult(
        spec_path=display_path,
        rules=rules,
        warnings=warnings,
    )

def _find_rule_index(rules: list[SpecRule], rule_id: str) -> int:
    """按 rule_id 查找规则索引，找不到时抛出 ValueError。"""
    target = rule_id.strip().upper()
    for idx, rule in enumerate(rules):
        if rule.rule_id.upper() == target:
            return idx
    raise ValueError(f"未找到规则编号: {rule_id}")
def confirm_rule(
    rules: list[SpecRule],
    rule_id: str,
    *,
    note: str = "",
) -> SpecRule:
    """将指定草稿规则标记为已确认（CONFIRMED）。"""
    idx = _find_rule_index(rules, rule_id)
    updated = rules[idx].model_copy(
        update={
            "status": RuleConfirmationStatus.CONFIRMED,
            "human_note": note if note else rules[idx].human_note,
        }
    )
    rules[idx] = updated
    return updated
def edit_rule(
    rules: list[SpecRule],
    rule_id: str,
    *,
    title: str | None = None,
    requirement: str | None = None,
    target_scope: str | None = None,
    conditions: list[str] | None = None,
    note: str = "",
) -> SpecRule:
    """人工修正规则字段并自动将状态更新为 EDITED。"""
    idx = _find_rule_index(rules, rule_id)
    current = rules[idx]
    updates: dict[str, Any] = {"status": RuleConfirmationStatus.EDITED}
    if title is not None:
        updates["title"] = title.strip()
    if requirement is not None:
        updates["requirement"] = requirement.strip()
    if target_scope is not None:
        updates["target_scope"] = target_scope.strip()
    if conditions is not None:
        updates["conditions"] = [c.strip() for c in conditions if c.strip()]
    if note:
        updates["human_note"] = note
    updated = current.model_copy(update=updates)
    rules[idx] = updated
    return updated
def skip_rule(
    rules: list[SpecRule],
    rule_id: str,
    *,
    reason: str = "",
) -> SpecRule:
    """将不需要核查的规则标记为跳过（SKIPPED）。"""
    idx = _find_rule_index(rules, rule_id)
    updated = rules[idx].model_copy(
        update={
            "status": RuleConfirmationStatus.SKIPPED,
            "human_note": reason if reason else rules[idx].human_note,
        }
    )
    rules[idx] = updated
    return updated
def add_rule(
    rules: list[SpecRule],
    *,
    title: str,
    requirement: str,
    target_scope: str,
    conditions: list[str] | None = None,
    spec_path: str = "manual",
    start_line: int = 1,
    end_line: int = 1,
    section_path: str = "人工补充",
    note: str = "人工补充遗漏条款",
) -> SpecRule:
    """人工补充一条模型遗漏的规范条款（直接标记为 EDITED）。"""
    existing_nums = [
        int(r.rule_id[1:])
        for r in rules
        if r.rule_id.startswith("R") and r.rule_id[1:].isdigit()
    ]
    next_num = max(existing_nums, default=len(rules)) + 1
    new_rule = SpecRule(
        rule_id=f"R{next_num}",
        title=title.strip(),
        requirement=requirement.strip(),
        target_scope=target_scope.strip() or "全仓代码",
        conditions=[c.strip() for c in (conditions or []) if c.strip()],
        source_location=RuleSourceLocation(
            file_path=spec_path,
            start_line=max(1, start_line),
            end_line=max(max(1, start_line), end_line),
            section_path=section_path,
        ),
        status=RuleConfirmationStatus.EDITED,
        human_note=note,
    )
    rules.append(new_rule)
    return new_rule
def confirm_all_drafts(rules: list[SpecRule]) -> int:
    """批量将所有仍处于 DRAFT 状态的规则确认为 CONFIRMED，返回本次确认条数。"""
    count = 0
    for idx, rule in enumerate(rules):
        if rule.status == RuleConfirmationStatus.DRAFT:
            rules[idx] = rule.model_copy(
                update={"status": RuleConfirmationStatus.CONFIRMED}
            )
            count += 1
    return count
_STATUS_LABELS: dict[RuleConfirmationStatus, str] = {
    RuleConfirmationStatus.DRAFT: "⏳ 草稿",
    RuleConfirmationStatus.CONFIRMED: "✅ 已确认",
    RuleConfirmationStatus.EDITED: "✏️ 已修正",
    RuleConfirmationStatus.SKIPPED: "⏭️ 已跳过",
}
def format_rule_drafts_table(rules: list[SpecRule]) -> str:
    """渲染高可读性的规则草稿审查表，用于人工确认前透视全部条款。"""
    if not rules:
        return "（当前无可审查的规范条款草稿）"
    lines: list[str] = ["=" * 72, "📜 【规范条款草稿清单】", "=" * 72]
    for rule in rules:
        loc = rule.source_location
        status_tag = _STATUS_LABELS.get(rule.status, rule.status.value)
        section = f" ({loc.section_path})" if loc.section_path else ""
        cond_text = "；".join(rule.conditions) if rule.conditions else "无特殊条件/例外"
        lines.extend(
            [
                f"[{rule.rule_id}] {status_tag} | {rule.title}",
                f"  - 核心要求: {rule.requirement}",
                f"  - 适用范围: {rule.target_scope}",
                f"  - 条件/单位/例外: {cond_text}",
                f"  - 规约出处: {loc.file_path}:{loc.start_line}-{loc.end_line}{section}",
            ]
        )
        if rule.human_note:
            lines.append(f"  - 人工备注: {rule.human_note}")
        lines.append("-" * 72)
    active_count = sum(1 for r in rules if r.is_active)
    lines.append(
        f"汇总: 共 {len(rules)} 条规则，当前已激活待核查 {active_count} 条。"
    )
    return "\n".join(lines)
def _prompt_edit_fields(
    rule: SpecRule,
    rules: list[SpecRule],
    input_fn: Callable[[str], str],
    print_fn: Callable[[str], None],
) -> None:
    """交互式修改单条规则的核心字段（直接回车保留原值）。"""
    print_fn("  （直接按回车保留当前原值；conditions 用分号 `;` 分隔多条）")
    new_req = input_fn(f"  核心要求 [{rule.requirement}]: ").strip()
    new_scope = input_fn(f"  适用范围 [{rule.target_scope}]: ").strip()
    old_cond_str = "; ".join(rule.conditions)
    new_cond_str = input_fn(f"  条件/单位/例外 [{old_cond_str or '无'}]: ").strip()
    note = input_fn("  修正备注（可选）: ").strip()
    parsed_conditions: list[str] | None = None
    if new_cond_str:
        if new_cond_str in ("无", "none", "clear"):
            parsed_conditions = []
        else:
            parsed_conditions = [
                part.strip()
                for part in new_cond_str.replace("；", ";").split(";")
                if part.strip()
            ]
    edit_rule(
        rules,
        rule.rule_id,
        requirement=new_req if new_req else None,
        target_scope=new_scope if new_scope else None,
        conditions=parsed_conditions,
        note=note,
    )
    print_fn(f"  ✏️ {rule.rule_id} 已更新并确认。\n")
def interactive_confirm_rules(
    parse_result: RuleParseResult,
    *,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> list[SpecRule]:
    """
    终端人工确认环：逐条展示模型提取的规则草稿，允许确认、修改、跳过或补录。
    返回用户确认后的激活规则列表（CONFIRMED / EDITED）。
    """
    rules = list(parse_result.rules)
    for warn in parse_result.warnings:
        print_fn(f"⚠️ {warn}")
    print_fn(format_rule_drafts_table(rules))
    if rules:
        print_fn(
            "\n操作指令: [Enter/c] 确认 | [e] 修正字段 | [s] 跳过 | "
            "[all] 确认全部剩余草稿 | [q] 结束确认"
        )
    for idx in range(len(rules)):
        rule = rules[idx]
        if rule.status != RuleConfirmationStatus.DRAFT:
            continue
        choice = (
            input_fn(f"👉 审核 [{rule.rule_id}] {rule.title} [Enter/c/e/s/all/q]: ")
            .strip()
            .lower()
        )
        if choice in ("", "c", "y"):
            confirm_rule(rules, rule.rule_id)
            print_fn(f"  ✅ {rule.rule_id} 已确认。")
        elif choice == "e":
            _prompt_edit_fields(rule, rules, input_fn, print_fn)
        elif choice == "s":
            reason = input_fn("  跳过原因（可选）: ").strip()
            skip_rule(rules, rule.rule_id, reason=reason)
            print_fn(f"  ⏭️ {rule.rule_id} 已跳过。")
        elif choice == "all":
            count = confirm_all_drafts(rules)
            print_fn(f"  ✅ 已批量确认剩余 {count} 条草稿。")
            break
        elif choice == "q":
            print_fn("  🛑 已提前结束逐条确认（未确认的草稿不会进入核查）。")
            break
        else:
            print_fn(f"  ⚠️ 未识别指令 '{choice}'，默认保留为草稿（不进入核查）。")
    while True:
        add_more = input_fn("\n是否需要人工补录遗漏条款？[y/N]: ").strip().lower()
        if add_more != "y":
            break
        title = input_fn("  规则标题: ").strip()
        requirement = input_fn("  核心要求: ").strip()
        if not title or not requirement:
            print_fn("  ⚠️ 标题和核心要求不能为空，已取消本次补录。")
            continue
        target_scope = input_fn("  适用范围（默认 全仓代码）: ").strip() or "全仓代码"
        cond_raw = input_fn("  条件/单位/例外（分号分隔，可选）: ").strip()
        conditions = [
            c.strip()
            for c in cond_raw.replace("；", ";").split(";")
            if c.strip()
        ]
        created = add_rule(
            rules,
            title=title,
            requirement=requirement,
            target_scope=target_scope,
            conditions=conditions,
            spec_path=parse_result.spec_path,
        )
        print_fn(f"  ➕ 已补录规则 [{created.rule_id}] {created.title}")
    parse_result.rules = rules
    print_fn("\n" + format_rule_drafts_table(rules))
    return parse_result.active_rules()