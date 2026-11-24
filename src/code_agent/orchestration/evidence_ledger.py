"""调查循环的证据记录表（Evidence Ledger）与引用程序核验模块（W10 S2）。"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import re
from typing import Any

from code_agent.core.citation import Citation, extract_citations
from code_agent.core.trace import compute_content_hash, get_git_info
from code_agent.tools.base import ToolResult
from code_agent.tools.code_reader import CodeSnippetResult
from code_agent.tools.reference_tools import FindReferencesData
from code_agent.tools.search_tools import SearchTextData
from code_agent.tools.symbol_tools import SymbolLookupToolResult

_EVIDENCE_ID_PATTERN = re.compile(r"(?<![A-Za-z0-9_])E(\d+)(?![A-Za-z0-9_])")

class EvidenceKind(str, Enum):
    """证据支撑级别，区分“已读实现正文”与“仅获知坐标/单行命中”。"""

    SNIPPET_BODY = "snippet_body" #（read_file_snippet）：真正读到了源码正文，可支撑逻辑与实现结论；
    SYMBOL_SIGNATURE = "symbol_signature" #（lookup_symbol）：只拿到了定义行号与参数签名，不能支撑函数体内部实现结论；
    TEXT_MATCH = "text_match" #（search_text）只拿到了单行预览，只能证明该行文本/调用点出现过，不代表核实了上下文逻辑
    REFERENCE_MATCH = "reference_match" #（find_references）只拿到了单行预览，只能证明该行文本/调用点出现过，不代表核实了上下文逻辑

class CitationCheckStatus(str, Enum):
    """单条物理引用经过账本核验后的判定状态。"""
    VERIFIED_BODY = "verified_body"  # 完整落在已读取源码正文（SNIPPET_BODY）区间内
    COORDINATE_ONLY = "coordinate_only"  # 仅命中单行搜索/引用或符号坐标，未读完整实现
    UNVERIFIED = "unverified"  # 未在账本覆盖范围内，或版本不一致


@dataclass(frozen=True)
class EvidenceItem:
    """
    单条不可变工具证据记录。
    Attributes:
        id: 账本内唯一证据编号（如 "E1"、"E2"）。
        kind: 证据支撑级别。
        tool_name: 产生该证据的工具名称。
        file_path: 相对工程根目录的 POSIX 路径。
        revision: 采集该证据时的仓库版本标识（含 dirty 状态）。
        line_range: 工具实际返回的 1-based 行号闭区间 (start_line, end_line)。
        content_hash: 实际返回内容的 SHA-256 哈希。
        content: 工具实际返回给模型的文本内容（非整文件原文）。
        is_truncated: 本次工具调用是否发生截断。
        note: 证据使用边界补充说明（如“仅含定义坐标与签名”）。
    """
    id: str
    kind: EvidenceKind
    tool_name: str
    file_path: str
    revision: str
    line_range: tuple[int, int]
    content_hash: str
    content: str
    is_truncated: bool = False
    note: str = ""

@dataclass(frozen=True)
class CitationVerificationItem:
    """
    单条物理引用在账本中的核验明细（供程序判定与人工复核）。
    Attributes:
        citation: 从模型回答中提取出的原始引用对象（含路径、起止行号与原文片段）。
        status: 账本核验判定状态（正文完整覆盖 / 仅坐标或单行命中 / 未验证）。
        matched_evidence_ids: 能够支撑或关联到该引用的账本证据编号元组（如 ("E1", "E2")）。
        detail: 本条引用的核验结论与原因说明（用于展示或告警）。
    """
    citation: Citation
    status: CitationCheckStatus
    matched_evidence_ids: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class EvidenceVerification:
    """
    最终回答的整体证据核验报告。
    Attributes:
        bound_evidence_ids: 回答中提及且确实存在于账本中的合法证据编号（如 ("E1", "E3")）。
        unknown_evidence_ids: 回答中提及但账本里不存在的伪造/无效证据编号（如 ("E99",)）。
        citation_checks: 回答中每一条 [file:line] 物理引用的逐条核验明细。
        verified_count: 已被已读源码正文（SNIPPET_BODY）完整覆盖的引用条数。
        coordinate_only_count: 仅命中符号坐标或搜索/引用单行（未读正文）的引用条数。
        unverified_count: 未在账本中出现或超出已返回行号区间的无效引用条数。
        warnings: 汇总的全部核验告警信息（含伪造 ID、仅坐标告警、未绑定证据等）。
        missing_evidence_binding: 账本已采集证据但回答未绑定任何 E# 或物理行号引用时为 True。
    """
    bound_evidence_ids: tuple[str, ...]
    unknown_evidence_ids: tuple[str, ...]
    citation_checks: tuple[CitationVerificationItem, ...]
    verified_count: int
    coordinate_only_count: int
    unverified_count: int
    warnings: tuple[str, ...]
    missing_evidence_binding: bool = False

    @property
    def has_unverified_claims(self) -> bool:
        """是否存在未知证据 ID、无效引用或未绑定任何证据的裸回答。"""
        return (
                bool(self.unknown_evidence_ids)
                or self.unverified_count > 0
                or self.missing_evidence_binding
        )

def resolve_project_revision(project_root: str | Path) -> str:
    """探测工程当前修订标识；若工作区有未提交修改则显式附加 dirty 标记。"""
    rev, is_dirty = get_git_info(project_root)
    if rev == "non-git":
        return rev
    return f"{rev}(dirty)" if is_dirty else rev

def _merge_line_ranges(ranges: list[tuple[int, int]]) -> list[tuple[int, int]]:
    """合并同一文件内重叠或首尾紧邻的已读行号区间（兼容分页续读）。"""
    if not ranges:
        return []
    ordered = sorted(ranges, key=lambda r: (r[0], r[1]))
    merged: list[tuple[int, int]] = [ordered[0]]
    for start, end in ordered[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end + 1:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged

def _extract_evidence_ids(text: str) -> tuple[str, ...]:
    """按出现顺序去重提取回答中引用的证据编号（如 E1、E2）。"""
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _EVIDENCE_ID_PATTERN.finditer(text):
        eid = f"E{match.group(1)}"
        if eid not in seen:
            seen.add(eid)
            ordered.append(eid)
    return tuple(ordered)

class EvidenceLedger:
    """
    维护单次调查过程中实际交付给模型的全部证据，并核验最终回答的依据真实性。
    """
    def __init__(self, revision: str) -> None:
        self.revision = revision
        self._items: list[EvidenceItem] = []
        self._by_id: dict[str, EvidenceItem] = {}

    @property
    def items(self) -> tuple[EvidenceItem, ...]:
        """返回当前已登记的全部不可变证据快照。"""
        return tuple(self._items)

    def _add_item(
        self,
        *,
        kind: EvidenceKind,
        tool_name: str,
        file_path: str,
        line_range: tuple[int, int],
        content: str,
        is_truncated: bool,
        note: str = "",
    ) -> EvidenceItem:
        eid = f"E{len(self._items) + 1}"
        norm_path = file_path.replace("\\", "/").strip()
        item = EvidenceItem(
            id=eid,
            kind=kind,
            tool_name=tool_name,
            file_path=norm_path,
            revision=self.revision,
            line_range=line_range,
            content_hash=compute_content_hash(content),
            content=content,
            is_truncated=is_truncated,
            note=note,
        )
        self._items.append(item)
        self._by_id[eid] = item
        return item

    def record_tool_result(self, result: ToolResult[Any]) -> tuple[EvidenceItem, ...]:
        """
        将单次成功的工具返回登记为一条或多条 EvidenceItem。
        仅登记工具实际返回给模型的内容与区间，失败或空结果不产生虚假证据。
        """
        if not result.ok or result.data is None:
            return ()
        data = result.data
        created: list[EvidenceItem] = []
        if isinstance(data, CodeSnippetResult):
            if data.returned_range is not None:
                note = "截断片段，仅已返回区间可作正文依据。" if data.is_truncated else ""
                created.append(
                    self._add_item(
                        kind=EvidenceKind.SNIPPET_BODY,
                        tool_name=result.tool_name,
                        file_path=data.file_path,
                        line_range=data.returned_range,
                        content=data.content,
                        is_truncated=data.is_truncated,
                        note=note,
                    )
                )
        elif isinstance(data, SymbolLookupToolResult):
            for sym in data.items:
                sig_text = sym.signature or ""
                summary = (
                    f"{sym.kind} {sym.fqn}{sig_text} "
                    f"@ {sym.file_path}:{sym.start_line}-{sym.end_line}"
                )
                created.append(
                    self._add_item(
                        kind=EvidenceKind.SYMBOL_SIGNATURE,
                        tool_name=result.tool_name,
                        file_path=sym.file_path,
                        line_range=(sym.start_line, sym.end_line),
                        content=summary,
                        is_truncated=data.is_truncated,
                        note="仅包含符号定义位置与签名，未读取函数体实现。",
                    )
                )
        elif isinstance(data, SearchTextData):
            for hit in data.matches:
                created.append(
                    self._add_item(
                        kind=EvidenceKind.TEXT_MATCH,
                        tool_name=result.tool_name,
                        file_path=hit.file_path,
                        line_range=(hit.line_number, hit.line_number),
                        content=hit.line_text,
                        is_truncated=result.is_truncated or hit.line_truncated,
                        note="仅命中单行文本预览，不代表已核实所在函数完整逻辑。",
                    )
                )
        elif isinstance(data, FindReferencesData):
            for ref in data.references:
                ref_content = f"[{ref.confidence.value}/{ref.scope}] {ref.target_fqn}: {ref.line_text}"
                created.append(
                    self._add_item(
                        kind=EvidenceKind.REFERENCE_MATCH,
                        tool_name=result.tool_name,
                        file_path=ref.file_path,
                        line_range=(ref.line_number, ref.line_number),
                        content=ref_content,
                        is_truncated=result.is_truncated or ref.line_truncated,
                        note="仅命中候选调用/引用所在单行，需结合代码正文核实语义。",
                    )
                )
        return tuple(created)


    def verify_answer(
        self,
        answer: str,
        expected_revision: str | None = None,
    ) -> EvidenceVerification:
        """
        核验模型最终回答中绑定的证据 ID 与物理行号引用是否真实受账本支撑。
        """
        target_rev = expected_revision if expected_revision is not None else self.revision
        raw_ids = _extract_evidence_ids(answer)
        bound_ids: list[str] = []
        unknown_ids: list[str] = []
        for eid in raw_ids:
            if eid in self._by_id:
                bound_ids.append(eid)
            else:
                unknown_ids.append(eid)
        citations = extract_citations(answer)
        checks: list[CitationVerificationItem] = []
        verified_count = 0
        coordinate_only_count = 0
        unverified_count = 0
        warnings: list[str] = []
        if unknown_ids:
            warnings.append(f"回答引用了账本中不存在的证据 ID：{', '.join(unknown_ids)}")
        for cit in citations:
            check_item = self._verify_single_citation(cit, target_rev)
            checks.append(check_item)
            if check_item.status == CitationCheckStatus.VERIFIED_BODY:
                verified_count += 1
            elif check_item.status == CitationCheckStatus.COORDINATE_ONLY:
                coordinate_only_count += 1
                warnings.append(check_item.detail)
            else:
                unverified_count += 1
                warnings.append(check_item.detail)
        missing_binding = bool(self._items and not bound_ids and not citations)
        if missing_binding:
            warnings.append("账本已采集证据，但回答未绑定任何证据 ID（如 [E1]）或物理行号引用。")

        return EvidenceVerification(
            bound_evidence_ids=tuple(bound_ids),
            unknown_evidence_ids=tuple(unknown_ids),
            citation_checks=tuple(checks),
            verified_count=verified_count,
            coordinate_only_count=coordinate_only_count,
            unverified_count=unverified_count,
            warnings=tuple(warnings),
            missing_evidence_binding=missing_binding,
        )

    def _verify_single_citation(
        self,
        cit: Citation,
        target_revision: str,
    ) -> CitationVerificationItem:
        """按文件、版本、证据级别与合并区间核验单条物理引用。"""
        same_file_items = [
            item
            for item in self._items
            if item.file_path == cit.file_path and item.revision == target_revision
        ]
        if not same_file_items:
            return CitationVerificationItem(
                citation=cit,
                status=CitationCheckStatus.UNVERIFIED,
                matched_evidence_ids=(),
                detail=f"引用 {cit.raw_text} 未在当前版本({target_revision})的已采证记录中出现。",
            )
        body_items = [
            item for item in same_file_items if item.kind == EvidenceKind.SNIPPET_BODY
        ]
        merged_body_ranges = _merge_line_ranges([item.line_range for item in body_items])
        for start, end in merged_body_ranges:
            if start <= cit.start_line and cit.end_line <= end:
                matched_ids = tuple(
                    item.id
                    for item in body_items
                    if not (item.line_range[1] < cit.start_line or item.line_range[0] > cit.end_line)
                )
                return CitationVerificationItem(
                    citation=cit,
                    status=CitationCheckStatus.VERIFIED_BODY,
                    matched_evidence_ids=matched_ids,
                    detail=f"引用 {cit.raw_text} 已被已读正文区间 [{start}, {end}] 完整覆盖。",
                )
        # 未被正文覆盖时，检查是否仅落在单行命中或符号坐标中
        coord_items = [
            item
            for item in same_file_items
            if item.kind != EvidenceKind.SNIPPET_BODY
            and item.line_range[0] <= cit.start_line
            and cit.end_line <= item.line_range[1]
        ]
        if coord_items:
            matched_ids = tuple(item.id for item in coord_items)
            kinds = ", ".join(sorted({item.kind.value for item in coord_items}))
            return CitationVerificationItem(
                citation=cit,
                status=CitationCheckStatus.COORDINATE_ONLY,
                matched_evidence_ids=matched_ids,
                detail=(
                    f"引用 {cit.raw_text} 仅命中 {kinds}（{', '.join(matched_ids)}），"
                    "尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。"
                ),
            )
        return CitationVerificationItem(
            citation=cit,
            status=CitationCheckStatus.UNVERIFIED,
            matched_evidence_ids=(),
            detail=f"引用 {cit.raw_text} 超出账本实际返回行号区间，已标为未验证。",
        )