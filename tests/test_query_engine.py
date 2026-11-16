"""统一问答内核单元测试（纯离线：Mock 模型、零 Token 消耗）。"""

from pathlib import Path
from typing import Any
import pytest
from langchain_core.messages import AIMessage, BaseMessage

from code_agent.core.judge import CaseSpec
from code_agent.core.query_engine import (
    QueryConfig,
    build_retriever_for_project,
    execute_query,
)
from code_agent.core.schemas import AnswerVerdictKind, SystemStatus
from code_agent.index.hybrid_retriever import HybridRetriever

REPO_ROOT = Path(__file__).resolve().parent.parent


class MockChatModel:
    """轻量离线 Mock 模型，用于确定性验证业务流转"""

    def __init__(self, response_text: str = "", should_raise: bool = False):
        self.response_text = response_text
        self.should_raise = should_raise

    def invoke(self, messages: list[BaseMessage], **kwargs: Any) -> AIMessage:
        if self.should_raise:
            raise RuntimeError("API 连接超时 (Mock 异常)")
        msg = AIMessage(content=self.response_text)
        # 模拟 Token 统计元数据
        msg.usage_metadata = {"input_tokens": 120, "output_tokens": 35, "total_tokens": 155}
        return msg


def test_build_retriever_auto_detects_project_layout() -> None:
    """验证索引构建器能自动探测工程目录结构"""
    retriever = build_retriever_for_project(REPO_ROOT, retriever_type="hybrid")
    assert isinstance(retriever, HybridRetriever)
    assert len(retriever.chunks) > 0
    # 验证同时包含源码块与文档块
    sources = {c.source_type for c in retriever.chunks}
    assert "code" in sources
    assert "doc" in sources


def test_execute_query_successful_flow() -> None:
    """验证正常问答场景下，三维度模型正确流转且切片完整覆盖"""
    # 模拟模型给出了包含有效引用的正确回答
    mock_answer = (
        "`stream_ask` 函数定义在 `src/code_agent/core/model.py` 中，具体位于第 24~47 行。"
        "[src/code_agent/core/model.py:24-47]"
    )
    mock_model = MockChatModel(response_text=mock_answer)
    spec = CaseSpec(
        id="test_001",
        expected_files=("src/code_agent/core/model.py",),
        expected_symbols=("stream_ask",),
    )

    result = execute_query(
        project_root=REPO_ROOT,
        query="stream_ask 函数定义在哪个文件哪几行？",
        model=mock_model,  # 传入 Mock 模型，不耗费 API
        config=QueryConfig(top_k=3),
        spec=spec,
    )

    # 1. 验证系统执行状态
    assert result.system_status == SystemStatus.SUCCESS
    assert result.prompt_tokens == 120
    assert result.completion_tokens == 35

    # 2. 验证举证有效性与切片覆盖
    assert result.evidence_validity.citations_total == 1
    assert result.evidence_validity.citations_valid == 1
    assert result.evidence_validity.citations_in_context == 1
    assert result.evidence_validity.context_covered is True
    assert result.evidence_validity.is_valid is True

    # 3. 验证业务结论裁决
    assert result.answer_verdict.verdict == AnswerVerdictKind.CORRECT
    assert not result.answer_verdict.fail_reasons
    assert result.verdict is not None and result.verdict.passed


def test_execute_query_model_error_resilience() -> None:
    """验证模型抛出异常时，内核能优雅降级记录 MODEL_ERROR 而不抛出未捕获异常"""
    mock_model = MockChatModel(should_raise=True)
    result = execute_query(
        project_root=REPO_ROOT,
        query="测试异常捕获",
        model=mock_model,
        config=QueryConfig(top_k=1),
    )

    assert result.system_status == SystemStatus.MODEL_ERROR
    assert "调用模型异常" in result.answer
    assert result.answer_verdict.verdict == AnswerVerdictKind.FACTUAL_ERROR
    assert any("模型调用异常" in r for r in result.answer_verdict.fail_reasons)