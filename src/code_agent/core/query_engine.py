"""统一业务问答内核：消除仓库硬编码，CLI 与 Eval 共用一套业务流水线。"""

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from code_agent.core.citation import (
    CITATION_PROMPT_REQUIREMENT,
    extract_citations,
    validate_citation,
)
from code_agent.core.context import build_snippets_text
from code_agent.core.judge import CaseSpec, Verdict, judge_answer, path_matches
from code_agent.core.model import get_chat_model
from code_agent.core.schemas import (
    AnswerVerdict,
    AnswerVerdictKind,
    EvidenceValidity,
    SystemStatus,
)
from code_agent.core.trace import (
    SnippetSnapshot,
    TraceRecord,
    generate_trace_id,
    get_git_info,
    save_trace,
)
from code_agent.index.bm25_index import BM25CodeIndex, CodeChunk
from code_agent.index.hybrid_retriever import HybridRetriever, HybridSearchResult
from code_agent.index.vector_index import VectorCodeIndex

@dataclass
class QueryConfig:
    """问答执行配置"""
    retriever_type: str = "hybrid"  # "hybrid" | "bm25" | "vector"
    top_k: int = 3
    alpha: float=0.5
    temperature: float = 0.0
    code_dir: Optional[Path | str] = None
    doc_dir: Optional[Path | str] = None
    enable_trace: bool = True
    trace_dir: Optional[Path | str] = None

@dataclass
class QueryResult:
    """单次问答执行的完整业务产物"""
    query: str
    answer: str
    hits: List[Tuple[CodeChunk,float]]
    snippets_text: str
    system_status: SystemStatus
    evidence_validity: EvidenceValidity
    answer_verdict: AnswerVerdict
    latency: float
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    full_prompt: str
    verdict: Optional[Verdict] = None
    trace_id: Optional[str] = None
    trace_path: Optional[Path] = None


def build_retriever_for_project(
    project_root: Path | str,
    retriever_type: str = "hybrid",
    alpha: float = 0.5,
    code_dir: Optional[Path | str] = None,
    doc_dir: Optional[Path | str] = None,
) -> HybridRetriever | BM25CodeIndex | VectorCodeIndex:
    """动态为任意工程目录构建索引底座，消除当前仓库路径硬编码。"""
    root = Path(project_root).resolve()

    # 动态探测代码与文档目录：优先尊重自定义配置；默认探测 src/ 与 docs/，若无 src 则平铺使用根目录
    c_dir = Path(code_dir).resolve() if code_dir else (root / "src" if (root / "src").exists() else root)
    d_dir = Path(doc_dir).resolve() if doc_dir else (root / "docs" if (root / "docs").exists() else None)

    if retriever_type == "hybrid":
        retriever = HybridRetriever(alpha=alpha)
        retriever.build_from_sources(code_dir=c_dir, doc_dir=d_dir)
        return retriever
    elif retriever_type == "bm25":
        bm25 = BM25CodeIndex()
        bm25.build_from_directory(c_dir)
        return bm25
    elif retriever_type == "vector":
        vec = VectorCodeIndex()
        vec.build_from_directory(c_dir)
        return vec
    else:
        raise ValueError(f"不支持的检索策略: {retriever_type}")

def execute_query(
    project_root: Path | str,
    query: str,
    indexer: Optional[Any] = None,
    model: Optional[Any] = None,
    config: Optional[QueryConfig] = None,
    spec: Optional[CaseSpec] = None,
    system_prompt_extra: str = "",
) -> QueryResult:
    """统一问答执行内核：执行一次无状态问答并返回三维度度量实体。"""
    start_time = time.time()
    cfg = config or QueryConfig()
    root = Path(project_root).resolve()

    # 1. 索引就绪保障：若外部传入现成索引（如评测大盘批量循环）则直接复用；否则就地动态构建
    if indexer is None:
        active_indexer = build_retriever_for_project(
            project_root=root,
            retriever_type=cfg.retriever_type,
            alpha=cfg.alpha,
            code_dir=cfg.code_dir,
            doc_dir=cfg.doc_dir,
        )
    else:
        active_indexer = indexer

    # 2. 检索相关切块并对齐为 (CodeChunk, score) 契约
    raw_results = active_indexer.search(query, top_k=cfg.top_k)
    hits: List[Tuple[CodeChunk, float]] = []
    for item in raw_results:
        if isinstance(item, HybridSearchResult):
            hits.append((item.chunk, item.score))
        else:
            hits.append(item)

    # 3. 拼装 Prompt 上下文与出处铁律
    snippets_text = build_snippets_text(hits, base_dir=root)
    system_prompt = (
        "你是一个严谨的代码工程与技术规范助手。\n"
        f"{CITATION_PROMPT_REQUIREMENT}"
    )
    if system_prompt_extra:
        system_prompt += f"\n{system_prompt_extra}"
    user_prompt = (
        f"【参考上下文片段（包含源码与技术文档）】:\n{snippets_text}\n\n"
        f"【用户问题】: {query}\n"
        "请根据参考资料回答，并严格标注每一处出处引用。"
    )
    full_prompt = (
        f"=== [System Message] ===\n{system_prompt}\n\n"
        f"=== [User Message] ===\n{user_prompt}"
    )

    # 4. 调用大模型
    active_model = model or get_chat_model(temperature=cfg.temperature)
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    system_status = SystemStatus.SUCCESS
    try:
        response = active_model.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
        raw_answer = response.content
        answer = raw_answer if isinstance(raw_answer, str) else str(raw_answer)
        if not answer.strip():
            system_status = SystemStatus.MODEL_ERROR
        usage = getattr(response, "usage_metadata", {}) or {}
        if not usage and hasattr(response, "response_metadata"):
            usage = response.response_metadata.get("token_usage", {}) or {}
        prompt_tokens = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
    except Exception as exc:
        answer = f"调用模型异常: {exc}"
        system_status = SystemStatus.MODEL_ERROR
    latency = time.time() - start_time

    # 5. 判卷内核调用（若无规格，则默认使用通用 adhoc 规则）
    active_spec = spec or CaseSpec(id="adhoc", match_mode="any")
    verdict = judge_answer(active_spec, answer, base_dir=root)

    # 计算切片上下文覆盖度 (citations_in_context)
    citations_in_context = 0
    valid_citations = [
        res.citation
        for res in (validate_citation(c, base_dir=root) for c in extract_citations(answer))
        if res.is_valid
    ]
    for vc in valid_citations:
        for chunk, _ in hits:
            if path_matches(chunk.file_path, vc.file_path):
                if max(chunk.start_line, vc.start_line) <= min(chunk.end_line, vc.end_line):
                    citations_in_context += 1
                    break
    verdict.evidence.citations_in_context = citations_in_context
    if verdict.evidence.citations_valid > 0:
        verdict.evidence.context_covered = (citations_in_context == verdict.evidence.citations_valid)
    else:
        verdict.evidence.context_covered = None
    if system_status != SystemStatus.SUCCESS:
        verdict.answer_verdict.verdict = AnswerVerdictKind.FACTUAL_ERROR
        if "模型调用异常" not in verdict.answer_verdict.fail_reasons:
            verdict.answer_verdict.fail_reasons.insert(0, "模型调用异常")

    # 6. 全链路 Trace 审计记账落盘
    trace_id: Optional[str] = None
    trace_path: Optional[Path] = None
    if cfg.enable_trace:
        trace_id = generate_trace_id()
        target_trace_dir = Path(cfg.trace_dir).resolve() if cfg.trace_dir else (root / "traces")
        git_revision, is_git_dirty = get_git_info(root)
        snippet_snapshots = [
            SnippetSnapshot.from_chunk(chunk, score)
            for chunk, score in hits
        ]
        trace_record = TraceRecord(
            trace_id=trace_id,
            agent_version="0.1.0",
            repo_path=str(root),
            git_revision=git_revision,
            is_git_dirty=is_git_dirty,
            query=query,
            config={
                "retriever_type": cfg.retriever_type,
                "top_k": cfg.top_k,
                "alpha": cfg.alpha,
                "temperature": cfg.temperature,
            },
            spec_id=active_spec.id if active_spec and active_spec.id != "adhoc" else None,
            snippets=snippet_snapshots,
            full_prompt=full_prompt,
            raw_answer=answer,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            latency=latency,
            system_status=system_status,
            evidence_validity=verdict.evidence,
            answer_verdict=verdict.answer_verdict,
        )
        trace_path = save_trace(trace_record, target_trace_dir)

    return QueryResult(
        query=query,
        answer=answer,
        hits=hits,
        snippets_text=snippets_text,
        system_status=system_status,
        evidence_validity=verdict.evidence,
        answer_verdict=verdict.answer_verdict,
        latency=latency,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        full_prompt=full_prompt,
        verdict=verdict,
        trace_id=trace_id,
        trace_path=trace_path,
    )