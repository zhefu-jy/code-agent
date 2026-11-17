"""全链路 Trace 审计契约单元测试（纯离线、零 Token 消耗）。"""

from pathlib import Path
from typing import Any
import pytest
from langchain_core.messages import AIMessage, BaseMessage

from code_agent.core.query_engine import QueryConfig, execute_query
from code_agent.core.schemas import (
    AnswerVerdict,
    AnswerVerdictKind,
    EvidenceValidity,
    JudgeSource,
    SystemStatus,
)
from code_agent.core.trace import (
    SnippetSnapshot,
    TraceRecord,
    compute_content_hash,
    generate_trace_id,
    get_git_info,
    load_trace,
    save_trace,
)
from code_agent.index.bm25_index import CodeChunk

REPO_ROOT = Path(__file__).resolve().parent.parent


class MockChatModel:
    """轻量离线 Mock 模型"""

    def __init__(self, response_text: str = ""):
        self.response_text = response_text

    def invoke(self, messages: list[BaseMessage], **kwargs: Any) -> AIMessage:
        msg = AIMessage(content=self.response_text)
        msg.usage_metadata = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
        return msg


def test_get_git_info_on_current_repo() -> None:
    """验证能够正确识别当前工程的 Git revision 与修改状态"""
    revision, is_dirty = get_git_info(REPO_ROOT)
    assert revision != "non-git"
    assert len(revision) >= 7  # 至少是 7 位短 hash 或完整 40 位 commit hash
    assert isinstance(is_dirty, bool)


def test_get_git_info_on_non_git_dir(tmp_path: Path) -> None:
    """验证在非 Git 目录下能够安全降级返回 ('non-git', False) 而不抛出异常"""
    revision, is_dirty = get_git_info(tmp_path)
    assert revision == "non-git"
    assert is_dirty is False


def test_snippet_snapshot_hash_consistency() -> None:
    """验证代码切片快照能正确计算 SHA-256 哈希且变动敏感"""
    chunk = CodeChunk(
        file_path="src/code_agent/core/model.py",
        start_line=10,
        end_line=20,
        content="def hello_world():\n    return 'ok'",
        tokens=["hello", "world"],
        symbol_name="hello_world",
    )
    snapshot = SnippetSnapshot.from_chunk(chunk, score=0.95)

    expected_hash = compute_content_hash(chunk.content)
    assert snapshot.content_hash == expected_hash
    assert snapshot.symbol_name == "hello_world"
    assert snapshot.score == 0.95

    # 内容微调后哈希必然改变
    altered_hash = compute_content_hash(chunk.content + " ")
    assert snapshot.content_hash != altered_hash


def test_trace_save_and_load_roundtrip(tmp_path: Path) -> None:
    """验证 TraceRecord 序列化落盘与反序列化还原无损"""
    trace_id = generate_trace_id()
    record = TraceRecord(
        trace_id=trace_id,
        agent_version="0.1.0",
        repo_path=str(REPO_ROOT),
        git_revision="a1b2c3d4",
        is_git_dirty=False,
        query="测试 Trace 序列化",
        config={"retriever_type": "hybrid", "top_k": 3},
        snippets=[],
        full_prompt="System: ... User: ...",
        raw_answer="测试回答内容",
        prompt_tokens=100,
        completion_tokens=20,
        total_tokens=120,
        latency=0.85,
        system_status=SystemStatus.SUCCESS,
        evidence_validity=EvidenceValidity(
            citations_total=1,
            citations_valid=1,
            citations_in_context=1,
            context_covered=True,
            is_valid=True,
        ),
        answer_verdict=AnswerVerdict(
            verdict=AnswerVerdictKind.CORRECT,
            judge_source=JudgeSource.HUMAN_REVIEW,
            human_note="离线单测确认无误",
        ),
    )

    out_file = save_trace(record, trace_dir=tmp_path)
    assert out_file.exists()

    loaded = load_trace(out_file)
    assert loaded.trace_id == trace_id
    assert loaded.system_status == SystemStatus.SUCCESS
    assert loaded.evidence_validity.context_covered is True
    assert loaded.answer_verdict.judge_source == JudgeSource.HUMAN_REVIEW
    assert loaded.answer_verdict.human_note == "离线单测确认无误"


def test_execute_query_trace_integration(tmp_path: Path) -> None:
    """验证统一问答内核在启用 Trace 时自动完成审计快照落盘"""
    mock_model = MockChatModel(response_text="这是一个带引用的测试回答 [src/code_agent/core/model.py:24-47]")
    cfg = QueryConfig(top_k=2, enable_trace=True, trace_dir=tmp_path)

    result = execute_query(
        project_root=REPO_ROOT,
        query="测试内核 Trace 落盘联动",
        model=mock_model,
        config=cfg,
    )

    assert result.trace_id is not None
    assert result.trace_path is not None
    assert result.trace_path.exists()

    saved_trace = load_trace(result.trace_path)
    assert saved_trace.trace_id == result.trace_id
    assert saved_trace.query == "测试内核 Trace 落盘联动"
    assert len(saved_trace.snippets) > 0
    # 验证快照里的每个切片均已计算好 sha256 哈希
    for snip in saved_trace.snippets:
        assert len(snip.content_hash) == 64