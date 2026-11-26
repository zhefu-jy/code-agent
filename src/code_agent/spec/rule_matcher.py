"""规范条款与代码候选实现的语义绑定与人工复核机制（W11 S2）。"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field

from code_agent.analyzer.symbol_index import SymbolIndex
from code_agent.index.bm25_index import CodeChunk
from code_agent.index.hybrid_retriever import HybridSearchResult
from code_agent.spec.rule_parser import SpecRule
from code_agent.analyzer.symbol_extractor import SymbolInfo
import re
from dataclasses import dataclass, field

class CodeRetrieverLike(Protocol):
    """支持按查询返回候选切块的最小检索器协议（兼容 HybridRetriever、BM25CodeIndex 与测试替身）。"""

    def search(self, query: str, top_k: int = 3) -> Sequence[Any]: ...


class CandidateSource(str, Enum):
    """候选代码位置的召回/引入来源。"""

    SYMBOL_INDEX = "symbol_index"          # 静态符号索引命中
    HYBRID_RETRIEVAL = "hybrid_retrieval"  # 混合检索（BM25 + Vector）命中
    HUMAN_ADDED = "human_added"            # 人工补充加入


class InterventionAction(str, Enum):
    """人工对候选代码清单的介入操作类型。"""

    ADD = "add"        # 人工补充遗漏候选
    REMOVE = "remove"  # 人工排除噪音候选（软删除，保留自动定位基线）


class HumanCandidateIntervention(BaseModel):
    """单次人工候选增删操作记录。"""

    rule_id: str
    action: InterventionAction
    file_path: str
    symbol_name: str = ""
    start_line: int | None = None
    end_line: int | None = None
    reason: str = ""


class CodeCandidate(BaseModel):
    """单条规则关联的一个候选代码位置。"""

    candidate_id: str = Field(description="单条规则内的候选编号（如 C1、C2）")
    file_path: str = Field(description="相对工程根目录的 POSIX 路径")
    start_line: int = Field(ge=1, description="候选起始行号（1-based）")
    end_line: int = Field(ge=1, description="候选结束行号（1-based）")
    symbol_name: str = Field(default="", description="关联的类/函数/常量名（如有）")
    sources: list[CandidateSource] = Field(
        default_factory=list,
        description="命中来源列表（多路同时命中时一并保留）",
    )
    score: float = Field(default=0.0, ge=0.0, description="综合粗筛排序得分")
    match_reasons: list[str] = Field(
        default_factory=list,
        description="可解释的命中依据列表（如符号精确命中、BM25/向量召回分）",
    )
    excluded_by_human: bool = Field(
        default=False,
        description="是否被人工排除（软删除，不送入下游审计但保留在自动定位记录中）",
    )
    human_note: str = Field(default="", description="人工增删备注")

    @property
    def is_auto_discovered(self) -> bool:
        """是否由系统自动定位产出（不含人工新增）。"""
        return CandidateSource.HUMAN_ADDED not in self.sources

    @property
    def is_human_added(self) -> bool:
        """是否由人工手动补充。"""
        return CandidateSource.HUMAN_ADDED in self.sources

    @property
    def is_effective(self) -> bool:
        """是否最终生效并传递给下游审计 Agent。"""
        return not self.excluded_by_human


class RuleCandidateEntry(BaseModel):
    """单条规范条款对应的候选代码映射条目。"""

    rule: SpecRule
    query_used: str = Field(default="", description="自动粗筛时构造的检索查询文本")
    extracted_identifiers: list[str] = Field(
        default_factory=list,
        description="从规则中抽取的代码标识符/关键符号词",
    )
    candidates: list[CodeCandidate] = Field(default_factory=list)
    interventions: list[HumanCandidateIntervention] = Field(default_factory=list)

    @property
    def auto_candidates(self) -> list[CodeCandidate]:
        """纯自动定位产出的候选列表（含后续被人工排除项，用于真实统计自主定位成绩）。"""
        return [c for c in self.candidates if c.is_auto_discovered]

    @property
    def effective_candidates(self) -> list[CodeCandidate]:
        """人工复核后最终生效、将送入 S3 审计的候选列表。"""
        return [c for c in self.candidates if c.is_effective]

    @property
    def human_added_candidates(self) -> list[CodeCandidate]:
        """人工手动新增且当前仍生效的候选列表。"""
        return [c for c in self.candidates if c.is_human_added and c.is_effective]

    @property
    def human_removed_candidates(self) -> list[CodeCandidate]:
        """系统自动定位出但被人工排除的噪音候选列表。"""
        return [c for c in self.candidates if c.is_auto_discovered and c.excluded_by_human]

    @property
    def has_human_intervention(self) -> bool:
        """该规则是否发生过人工候选增删。"""
        return bool(self.interventions)


class CandidateMatchMetrics(BaseModel):
    """自动定位与人工辅助双口径分离统计指标。"""

    total_rules: int
    auto_matched_rules: int          # 纯自动定位至少找到 1 个候选的规则数
    auto_empty_rules: int            # 纯自动定位 0 命中的规则数
    auto_total_candidates: int       # 纯自动定位候选总数（不含人工新增）
    human_intervened_rules: int      # 发生过人工介入的规则数
    human_added_total: int           # 人工新增候选总数
    human_removed_total: int         # 人工排除自动候选总数
    effective_matched_rules: int     # 最终生效集至少有 1 个候选的规则数
    effective_total_candidates: int  # 最终送审候选总数


class RuleCandidateMap(BaseModel):
    """整份规约的规则 → 候选代码映射总表。"""

    project_root: str
    entries: list[RuleCandidateEntry] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)



    def get_entry(self, rule_id: str) -> RuleCandidateEntry | None:
        """按 rule_id 查找对应映射条目（大小写不敏感）。"""
        target = rule_id.strip().upper()
        for entry in self.entries:
            if entry.rule.rule_id.upper() == target:
                return entry
        return None

    def summary_metrics(self) -> CandidateMatchMetrics:
        """分别统计纯自动定位结果与人工辅助后的最终生效结果，不混淆两类成绩。"""
        auto_matched = sum(1 for e in self.entries if e.auto_candidates)
        effective_matched = sum(1 for e in self.entries if e.effective_candidates)
        return CandidateMatchMetrics(
            total_rules=len(self.entries),
            auto_matched_rules=auto_matched,
            auto_empty_rules=len(self.entries) - auto_matched,
            auto_total_candidates=sum(len(e.auto_candidates) for e in self.entries),
            human_intervened_rules=sum(1 for e in self.entries if e.has_human_intervention),
            human_added_total=sum(len(e.human_added_candidates) for e in self.entries),
            human_removed_total=sum(len(e.human_removed_candidates) for e in self.entries),
            effective_matched_rules=effective_matched,
            effective_total_candidates=sum(len(e.effective_candidates) for e in self.entries),
        )

_IDENTIFIER_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")

_ENGLISH_STOPWORDS: frozenset[str] = frozenset(
    {
        "the", "and", "for", "with", "from", "that", "this", "must",
        "should", "not", "all", "any", "into", "when", "only", "use",
        "using", "used", "need", "needs", "have", "has", "are", "was",
        "were", "can", "may", "will", "not", "none", "true", "false",
    }
)


@dataclass
class _CandidateDraft:
    """双路粗筛合并阶段的内部可变草稿。"""

    file_path: str
    start_line: int
    end_line: int
    symbol_name: str = ""
    is_module_level: bool = False
    sources: list[CandidateSource] = field(default_factory=list)
    symbol_score: float = 0.0
    retriever_score: float = 0.0
    match_reasons: list[str] = field(default_factory=list)

    @property
    def total_score(self) -> float:
        return round(self.symbol_score + self.retriever_score, 4)


def _normalize_candidate_path(raw_path: str | Path, project_root: Path) -> str:
    """将候选文件路径统一归一化为相对 project_root 的 POSIX 路径。"""
    root = project_root.resolve()
    raw_str = str(raw_path).strip()
    candidate = Path(raw_str)
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(root).as_posix()
        except ValueError:
            return candidate.as_posix()
    return Path(raw_str.replace("\\", "/")).as_posix()


def extract_rule_identifiers(rule: SpecRule, *, max_terms: int = 12) -> list[str]:
    """从规范条款中抽取可用于静态符号索引查询的代码标识符与点分表达式。"""
    combined = " ".join(
        [
            rule.title,
            rule.requirement,
            rule.target_scope,
            *rule.conditions,
        ]
    )
    seen: set[str] = set()
    terms: list[str] = []

    def _push(token: str) -> None:
        cleaned = token.strip(".")
        if len(cleaned) < 3:
            return
        low = cleaned.lower()
        if low in _ENGLISH_STOPWORDS or low in seen:
            return
        seen.add(low)
        terms.append(cleaned)

    for match in _IDENTIFIER_PATTERN.finditer(combined):
        raw = match.group(0)
        _push(raw)
        if "." in raw:
            for part in raw.split("."):
                _push(part)
        if len(terms) >= max_terms:
            break

    return terms[:max_terms]


def build_rule_query(rule: SpecRule) -> str:
    """将规范条款拼装为面向混合检索（BM25 + 向量）的紧凑查询文本。"""
    parts = [rule.title.strip(), rule.requirement.strip(), rule.target_scope.strip()]
    if rule.conditions:
        parts.append(" ".join(c.strip() for c in rule.conditions if c.strip()))
    return " | ".join(part for part in parts if part)


def _symbol_tail(symbol_name: str) -> str:
    """提取符号名尾段（去除类/模块前缀与 (header) 标记），用于合并对齐。"""
    cleaned = symbol_name.replace("(header)", "").strip()
    if "," in cleaned:
        cleaned = cleaned.split(",", 1)[0].strip()
    if "." in cleaned:
        return cleaned.rsplit(".", 1)[-1].strip().lower()
    return cleaned.lower()


def _should_merge_drafts(existing: _CandidateDraft, incoming: _CandidateDraft) -> bool:
    """判断两条候选草稿是否指向同一代码实现位置，决定是否合并去重。"""
    if existing.file_path != incoming.file_path:
        return False
    # 1. 物理行号区间完全一致，直接合并
    if existing.start_line == incoming.start_line and existing.end_line == incoming.end_line:
        return True
    # 2. 模块级全文件范围不吞并具体函数/类候选
    if existing.is_module_level or incoming.is_module_level:
        return False
    # 3. 区间重叠且共享相同符号尾名（兼容 ASTChunker 与 SymbolExtractor 装饰器/类头 1~2 行偏差）
    overlaps = (
        max(existing.start_line, incoming.start_line)
        <= min(existing.end_line, incoming.end_line)
    )
    if not overlaps:
        return False
    existing_tail = _symbol_tail(existing.symbol_name)
    incoming_tail = _symbol_tail(incoming.symbol_name)
    return bool(existing_tail and incoming_tail and existing_tail == incoming_tail)


def _merge_into_drafts(
    drafts: list[_CandidateDraft],
    incoming: _CandidateDraft,
) -> None:
    """将新召回的候选合并进草稿池；命中同一位置时合并来源、依据与得分。"""
    for existing in drafts:
        if _should_merge_drafts(existing, incoming):
            existing.start_line = min(existing.start_line, incoming.start_line)
            existing.end_line = max(existing.end_line, incoming.end_line)
            if not existing.symbol_name and incoming.symbol_name:
                existing.symbol_name = incoming.symbol_name
            for src in incoming.sources:
                if src not in existing.sources:
                    existing.sources.append(src)
            existing.symbol_score = max(existing.symbol_score, incoming.symbol_score)
            existing.retriever_score = max(existing.retriever_score, incoming.retriever_score)
            for reason in incoming.match_reasons:
                if reason not in existing.match_reasons:
                    existing.match_reasons.append(reason)
            return
    drafts.append(incoming)


def _collect_symbol_candidates(
    identifiers: list[str],
    symbol_index: SymbolIndex,
    project_root: Path,
    drafts: list[_CandidateDraft],
    *,
    max_hits_per_term: int = 5,
) -> None:
    """路线 A：基于静态符号索引，按精确 FQN / 精确短名 / 受控子串召回候选实现。"""
    mode_scores: dict[str, float] = {
        "exact_fqn": 1.0,
        "exact_name": 0.85,
        "substring": 0.55,
    }
    for ident in identifiers:
        lookup = symbol_index.lookup_symbol(ident)
        if lookup.match_mode == "none" or not lookup.matches:
            continue
        # 短词降级为子串匹配容易引入噪音，要求词长 >= 4 或包含点分结构
        if lookup.match_mode == "substring" and len(ident) < 4 and "." not in ident:
            continue
        base_score = mode_scores.get(lookup.match_mode, 0.5)
        for sym in lookup.matches[:max_hits_per_term]:
            _append_symbol_draft(
                sym=sym,
                ident=ident,
                match_mode=lookup.match_mode,
                base_score=base_score,
                project_root=project_root,
                drafts=drafts,
            )


def _append_symbol_draft(
    *,
    sym: SymbolInfo,
    ident: str,
    match_mode: str,
    base_score: float,
    project_root: Path,
    drafts: list[_CandidateDraft],
) -> None:
    """将单条 SymbolInfo 转换为候选草稿并合入候选池。"""
    is_module = sym.kind == "module"
    # 模块级实体粒度较粗，略微降权让位于具体类/函数/常量
    adjusted_score = round(base_score * (0.75 if is_module else 1.0), 4)
    file_path = _normalize_candidate_path(sym.file_path, project_root)
    reason = f"symbol_index:{match_mode}({ident} -> {sym.fqn})"
    _merge_into_drafts(
        drafts,
        _CandidateDraft(
            file_path=file_path,
            start_line=max(1, sym.start_line),
            end_line=max(max(1, sym.start_line), sym.end_line),
            symbol_name=sym.fqn,
            is_module_level=is_module,
            sources=[CandidateSource.SYMBOL_INDEX],
            symbol_score=adjusted_score,
            match_reasons=[reason],
        ),
    )


def _unpack_retriever_item(item: Any) -> tuple[CodeChunk, float, str] | None:
    """兼容解包 HybridSearchResult 或 (CodeChunk, score) 二元组。"""
    if isinstance(item, HybridSearchResult):
        return item.chunk, float(item.score), item.source
    if (
        isinstance(item, tuple)
        and len(item) == 2
        and isinstance(item[0], CodeChunk)
        and isinstance(item[1], (int, float))
    ):
        return item[0], float(item[1]), "RAW"
    return None


def _collect_retriever_candidates(
    query: str,
    retriever: CodeRetrieverLike,
    project_root: Path,
    drafts: list[_CandidateDraft],
    *,
    top_k: int = 5,
    min_retriever_score: float = 0.35,
) -> None:
    """路线 B：基于混合检索召回相关代码切块（剔除 Markdown 文档切块与低信噪比尾噪）。"""
    if not query.strip():
        return
    # 适当放大抓取池，避免部分名额被 docs/*.md 文档切块占用
    fetch_k = max(top_k * 2, top_k + 4)
    raw_items = retriever.search(query, top_k=fetch_k)

    unpacked: list[tuple[CodeChunk, float, str]] = []
    for item in raw_items:
        parsed = _unpack_retriever_item(item)
        if parsed is None:
            continue
        chunk, score, source_tag = parsed
        # 严格只召回代码实现，防止把规范 Markdown 自己召回回来
        if chunk.source_type != "code":
            continue
        if score <= 0.0:
            continue
        unpacked.append((chunk, score, source_tag))

    if not unpacked:
        return

    # 若接入纯 BM25 等无界分检索器，按批内最大值归一至 [0, 1] 与符号路对齐量纲
    max_score = max(score for _, score, _ in unpacked)
    scale = max_score if max_score > 1.0 else 1.0

    for chunk, raw_score, source_tag in unpacked[:top_k]:
        norm_score = round(min(1.0, raw_score / scale), 4)
        # 过滤低相关性尾噪（防中文查询下 BM25=0 时纯向量 Min-Max 拉伸出 <=0.30 噪音）
        if norm_score < min_retriever_score:
            continue
        file_path = _normalize_candidate_path(chunk.file_path, project_root)
        symbols = chunk.all_symbols()
        symbol_label = ", ".join(symbols) if symbols else ""
        reason = f"hybrid_retrieval:{source_tag}({norm_score:.3f})"
        _merge_into_drafts(
            drafts,
            _CandidateDraft(
                file_path=file_path,
                start_line=max(1, chunk.start_line),
                end_line=max(max(1, chunk.start_line), chunk.end_line),
                symbol_name=symbol_label,
                is_module_level=False,
                sources=[CandidateSource.HYBRID_RETRIEVAL],
                retriever_score=norm_score,
                match_reasons=[reason],
            ),
        )


def match_rule_to_candidates(
    rule: SpecRule,
    *,
    project_root: str | Path,
    symbol_index: SymbolIndex,
    retriever: CodeRetrieverLike | None = None,
    top_k: int = 5,
    min_retriever_score: float = 0.35,
) -> RuleCandidateEntry:
    """为单条已确认规范条款执行双路（符号索引 + 混合检索）候选代码粗筛。"""
    root = Path(project_root).resolve()
    identifiers = extract_rule_identifiers(rule)
    query = build_rule_query(rule)
    drafts: list[_CandidateDraft] = []

    _collect_symbol_candidates(
        identifiers=identifiers,
        symbol_index=symbol_index,
        project_root=root,
        drafts=drafts,
    )
    if retriever is not None:
        _collect_retriever_candidates(
            query=query,
            retriever=retriever,
            project_root=root,
            drafts=drafts,
            top_k=top_k,
            min_retriever_score=min_retriever_score,
        )

    # 确定性排序：总分降序 -> 多路命中优先 -> 文件路径升序 -> 起始行号升序
    drafts.sort(
        key=lambda d: (
            -d.total_score,
            -len(d.sources),
            d.file_path,
            d.start_line,
            d.end_line,
        )
    )

    candidates = [
        CodeCandidate(
            candidate_id=f"C{idx}",
            file_path=d.file_path,
            start_line=d.start_line,
            end_line=d.end_line,
            symbol_name=d.symbol_name,
            sources=list(d.sources),
            score=d.total_score,
            match_reasons=list(d.match_reasons),
        )
        for idx, d in enumerate(drafts[: max(1, top_k)], start=1)
    ]

    return RuleCandidateEntry(
        rule=rule,
        query_used=query,
        extracted_identifiers=identifiers,
        candidates=candidates,
    )


def match_rules_to_candidates(
    rules: Sequence[SpecRule],
    *,
    project_root: str | Path,
    symbol_index: SymbolIndex,
    retriever: CodeRetrieverLike | None = None,
    top_k: int = 5,
    active_only: bool = True,
    min_retriever_score: float = 0.35,
) -> RuleCandidateMap:
    """
    批量为规范条款列表构建 RuleCandidateMap。
    默认只放行 S1 已确认/已修正的 active 规则，跳过未确认草稿与已跳过条款。
    """
    root = Path(project_root).resolve()
    entries: list[RuleCandidateEntry] = []
    warnings: list[str] = []

    for rule in rules:
        if active_only and not rule.is_active:
            warnings.append(
                f"{rule.rule_id}: 当前状态为 {rule.status.value}（未激活），已跳过候选定位。"
            )
            continue
        entry = match_rule_to_candidates(
            rule,
            project_root=root,
            symbol_index=symbol_index,
            retriever=retriever,
            top_k=top_k,
            min_retriever_score=min_retriever_score,
        )
        if not entry.candidates:
            warnings.append(
                f"{rule.rule_id}: 自动定位未找到候选代码，可人工补充或交由审计阶段按证据不足处理。"
            )
        entries.append(entry)

    return RuleCandidateMap(
        project_root=root.as_posix(),
        entries=entries,
        warnings=warnings,
    )

def _require_entry(
    candidate_map: RuleCandidateMap,
    rule_id: str,
) -> RuleCandidateEntry:
    """按 rule_id 获取规则候选条目，不存在时抛出 ValueError。"""
    entry = candidate_map.get_entry(rule_id)
    if entry is None:
        raise ValueError(f"未找到规则候选映射: {rule_id}")
    return entry


def _next_candidate_id(candidates: Sequence[CodeCandidate]) -> str:
    """生成单条规则内下一个递增候选编号（C1、C2...）。"""
    existing_nums = [
        int(c.candidate_id[1:])
        for c in candidates
        if c.candidate_id.upper().startswith("C") and c.candidate_id[1:].isdigit()
    ]
    return f"C{max(existing_nums, default=len(candidates)) + 1}"


def _resolve_manual_line_range(
    project_root: Path,
    norm_path: str,
    symbol_name: str,
    start_line: int | None,
    end_line: int | None,
    symbol_index: SymbolIndex | None,
) -> tuple[int, int]:
    """为人工新增的候选推断合法物理行号区间。"""
    if start_line is not None:
        start = max(1, start_line)
        end = max(start, end_line if end_line is not None else start)
        return start, end

    cleaned_symbol = symbol_name.strip()
    if symbol_index is not None and cleaned_symbol:
        lookup = symbol_index.lookup_symbol(cleaned_symbol)
        for sym in lookup.matches:
            if _normalize_candidate_path(sym.file_path, project_root) == norm_path:
                start = max(1, sym.start_line)
                return start, max(start, sym.end_line)

    target_file = project_root / norm_path
    if target_file.is_file():
        try:
            line_count = len(target_file.read_text(encoding="utf-8").splitlines())
            return 1, max(1, line_count)
        except (OSError, UnicodeDecodeError):
            pass

    return 1, 1


def add_candidate(
    candidate_map: RuleCandidateMap,
    rule_id: str,
    *,
    file_path: str | Path,
    symbol_name: str = "",
    start_line: int | None = None,
    end_line: int | None = None,
    reason: str = "人工补充遗漏候选",
    symbol_index: SymbolIndex | None = None,
) -> CodeCandidate:
    """人工为指定规则补充一条候选代码位置，并单独记入介入明细（不计入自动定位成绩）。"""
    entry = _require_entry(candidate_map, rule_id)
    root = Path(candidate_map.project_root).resolve()
    norm_path = _normalize_candidate_path(file_path, root)
    if not norm_path:
        raise ValueError("候选文件路径不能为空")

    resolved_start, resolved_end = _resolve_manual_line_range(
        project_root=root,
        norm_path=norm_path,
        symbol_name=symbol_name,
        start_line=start_line,
        end_line=end_line,
        symbol_index=symbol_index,
    )
    cleaned_symbol = symbol_name.strip()
    cleaned_reason = reason.strip() or "人工补充遗漏候选"

    created = CodeCandidate(
        candidate_id=_next_candidate_id(entry.candidates),
        file_path=norm_path,
        start_line=resolved_start,
        end_line=resolved_end,
        symbol_name=cleaned_symbol,
        sources=[CandidateSource.HUMAN_ADDED],
        score=0.0,
        match_reasons=[f"human_added:{cleaned_reason}"],
        excluded_by_human=False,
        human_note=cleaned_reason,
    )
    entry.candidates.append(created)
    entry.interventions.append(
        HumanCandidateIntervention(
            rule_id=entry.rule.rule_id,
            action=InterventionAction.ADD,
            file_path=norm_path,
            symbol_name=cleaned_symbol,
            start_line=resolved_start,
            end_line=resolved_end,
            reason=cleaned_reason,
        )
    )
    return created


def remove_candidate(
    candidate_map: RuleCandidateMap,
    rule_id: str,
    candidate_id: str,
    *,
    reason: str = "人工排除无关候选",
) -> CodeCandidate:
    """人工软删除指定候选（从生效集移出，但保留在自动定位记录中供双口径统计）。"""
    entry = _require_entry(candidate_map, rule_id)
    target_id = candidate_id.strip().upper()
    cleaned_reason = reason.strip() or "人工排除无关候选"

    for idx, cand in enumerate(entry.candidates):
        if cand.candidate_id.upper() == target_id:
            if cand.excluded_by_human:
                return cand
            updated = cand.model_copy(
                update={
                    "excluded_by_human": True,
                    "human_note": cleaned_reason,
                }
            )
            entry.candidates[idx] = updated
            entry.interventions.append(
                HumanCandidateIntervention(
                    rule_id=entry.rule.rule_id,
                    action=InterventionAction.REMOVE,
                    file_path=updated.file_path,
                    symbol_name=updated.symbol_name,
                    start_line=updated.start_line,
                    end_line=updated.end_line,
                    reason=cleaned_reason,
                )
            )
            return updated

    raise ValueError(f"规则 {entry.rule.rule_id} 中未找到候选编号: {candidate_id}")


def _format_source_tag(sources: Sequence[CandidateSource]) -> str:
    """将候选来源列表渲染为高可读性标签。"""
    source_set = set(sources)
    if CandidateSource.HUMAN_ADDED in source_set:
        return "👤 人工新增"
    has_sym = CandidateSource.SYMBOL_INDEX in source_set
    has_ret = CandidateSource.HYBRID_RETRIEVAL in source_set
    if has_sym and has_ret:
        return "🔀 双路命中"
    if has_sym:
        return "🔎 符号索引"
    if has_ret:
        return "🧭 混合检索"
    return "❓ 未知来源"


def format_candidate_map_report(candidate_map: RuleCandidateMap) -> str:
    """渲染规则 → 候选代码映射表，严格分离展示“自动定位结果”与“人工辅助结果”。"""
    if not candidate_map.entries:
        return "（当前无已激活规则的候选代码映射）"

    lines: list[str] = [
        "=" * 76,
        "🗺️ 【规则 → 候选代码映射清单（自动定位 vs 人工辅助分离报告）】",
        "=" * 76,
    ]

    for entry in candidate_map.entries:
        rule = entry.rule
        id_text = ", ".join(entry.extracted_identifiers) if entry.extracted_identifiers else "无"
        lines.append(f"[{rule.rule_id}] {rule.title}")
        lines.append(f"  - 适用范围: {rule.target_scope}")
        lines.append(f"  - 抽取标识符: {id_text}")

        # 1) 纯自动定位结果
        auto_list = entry.auto_candidates
        if auto_list:
            lines.append(f"  - 自动定位候选 ({len(auto_list)} 处):")
            for cand in auto_list:
                tag = _format_source_tag(cand.sources)
                sym_part = f" | 符号: {cand.symbol_name}" if cand.symbol_name else ""
                removed_part = (
                    f" ❌ [已人工排除: {cand.human_note}]"
                    if cand.excluded_by_human
                    else ""
                )
                lines.append(
                    f"      [{cand.candidate_id}] {tag} (score={cand.score:.3f}) "
                    f"{cand.file_path}:{cand.start_line}-{cand.end_line}{sym_part}{removed_part}"
                )
        else:
            lines.append("  - 自动定位候选 (0 处): ⚠️ 未自动召回匹配实现")

        # 2) 人工介入明细
        if entry.interventions:
            lines.append(f"  - 人工介入记录 ({len(entry.interventions)} 次):")
            for item in entry.interventions:
                action_tag = "➕ 新增" if item.action == InterventionAction.ADD else "➖ 排除"
                range_part = (
                    f":{item.start_line}-{item.end_line}"
                    if item.start_line is not None and item.end_line is not None
                    else ""
                )
                sym_part = f" ({item.symbol_name})" if item.symbol_name else ""
                lines.append(
                    f"      {action_tag} {item.file_path}{range_part}{sym_part} — {item.reason}"
                )
        else:
            lines.append("  - 人工介入记录: 无")

        # 3) 最终生效集（供 S3 审计）
        eff_list = entry.effective_candidates
        if eff_list:
            eff_brief = ", ".join(
                f"{c.candidate_id}({c.file_path}:{c.start_line}-{c.end_line})"
                for c in eff_list
            )
            lines.append(f"  - 最终生效送审候选 ({len(eff_list)} 处): {eff_brief}")
        else:
            lines.append("  - 最终生效送审候选 (0 处): ⚠️ 无可用候选（下游审计将按证据不足处理）")
        lines.append("-" * 76)

    metrics = candidate_map.summary_metrics()
    lines.append(
        f"📊 【自主定位成绩（纯自动，不含人工补充）】"
        f"覆盖规则 {metrics.auto_matched_rules}/{metrics.total_rules} 条"
        f"（空命中 {metrics.auto_empty_rules} 条），自动候选共 {metrics.auto_total_candidates} 处。"
    )
    lines.append(
        f"🤝 【人工辅助与最终生效（单独计列，不混入自主成绩）】"
        f"人工介入 {metrics.human_intervened_rules} 条规则"
        f"（+{metrics.human_added_total} 新增 / -{metrics.human_removed_total} 排除），"
        f"最终生效覆盖 {metrics.effective_matched_rules}/{metrics.total_rules} 条，"
        f"送审候选共 {metrics.effective_total_candidates} 处。"
    )
    return "\n".join(lines)


def _parse_line_range_input(raw: str) -> tuple[int | None, int | None, bool]:
    """解析终端输入的行号区间（支持留空、单行 '25' 或区间 '10-40'）。"""
    text = raw.strip()
    if not text:
        return None, None, True
    if "-" in text:
        parts = [p.strip() for p in text.split("-", 1)]
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            start = max(1, int(parts[0]))
            end = max(start, int(parts[1]))
            return start, end, True
        return None, None, False
    if text.isdigit():
        line_no = max(1, int(text))
        return line_no, line_no, True
    return None, None, False


def interactive_review_candidates(
    candidate_map: RuleCandidateMap,
    *,
    symbol_index: SymbolIndex | None = None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> RuleCandidateMap:
    """
    终端人工确认环 ②：逐条展示规则对应的候选代码，支持人工补充遗漏或排除噪音。
    所有人工增删均单独记录，绝不污染自动定位基线。
    """
    for warn in candidate_map.warnings:
        print_fn(f"⚠️ {warn}")
    print_fn(format_candidate_map_report(candidate_map))

    if not candidate_map.entries:
        return candidate_map

    print_fn(
        "\n操作指令: [Enter/c] 确认本条候选 | [a] 新增候选 | [r] 排除候选 | "
        "[all] 放行全部剩余规则 | [q] 结束复核"
    )

    stop_all = False
    for entry in candidate_map.entries:
        if stop_all:
            break
        rule_id = entry.rule.rule_id
        while True:
            choice = (
                input_fn(
                    f"👉 复核 [{rule_id}] {entry.rule.title} "
                    f"(当前生效 {len(entry.effective_candidates)} 处) [Enter/c/a/r/all/q]: "
                )
                .strip()
                .lower()
            )
            if choice in ("", "c", "y"):
                print_fn(f"  ✅ {rule_id} 候选清单已确认。")
                break
            if choice == "a":
                file_path = input_fn("  文件路径（相对工程根）: ").strip()
                if not file_path:
                    print_fn("  ⚠️ 文件路径不能为空，已取消新增。")
                    continue
                symbol_name = input_fn("  符号名（可选）: ").strip()
                line_raw = input_fn("  行号区间（如 10-40，留空自动推断）: ")
                start_line, end_line, ok = _parse_line_range_input(line_raw)
                if not ok:
                    print_fn("  ⚠️ 行号格式无效（应如 10-40 或 25），已按自动推断处理。")
                reason = input_fn("  补充原因（可选）: ").strip() or "人工补充遗漏候选"
                added = add_candidate(
                    candidate_map,
                    rule_id,
                    file_path=file_path,
                    symbol_name=symbol_name,
                    start_line=start_line,
                    end_line=end_line,
                    reason=reason,
                    symbol_index=symbol_index,
                )
                print_fn(
                    f"  ➕ 已新增 [{added.candidate_id}] "
                    f"{added.file_path}:{added.start_line}-{added.end_line}"
                )
            elif choice == "r":
                cand_id = input_fn("  要排除的候选编号（如 C1）: ").strip()
                if not cand_id:
                    print_fn("  ⚠️ 候选编号不能为空，已取消排除。")
                    continue
                reason = input_fn("  排除原因（可选）: ").strip() or "人工排除无关候选"
                try:
                    removed = remove_candidate(
                        candidate_map,
                        rule_id,
                        cand_id,
                        reason=reason,
                    )
                    print_fn(
                        f"  ➖ 已排除 [{removed.candidate_id}] "
                        f"{removed.file_path}:{removed.start_line}-{removed.end_line}"
                    )
                except ValueError as exc:
                    print_fn(f"  ⚠️ {exc}")
            elif choice == "all":
                print_fn("  ✅ 已放行全部剩余规则的当前候选清单。")
                stop_all = True
                break
            elif choice == "q":
                print_fn("  🛑 已提前结束候选复核。")
                stop_all = True
                break
            else:
                print_fn(f"  ⚠️ 未识别指令 '{choice}'，请输入 Enter/c/a/r/all/q。")

    print_fn("\n" + format_candidate_map_report(candidate_map))
    return candidate_map