from pathlib import Path
import pytest

from code_agent.index.bm25_index import CodeChunk
from code_agent.index.hybrid_retriever import (
    HybridRetriever,
    _min_max_normalize,
    _chunk_key,
)


def test_min_max_normalize_edge_cases() -> None:
    """测试分值归一化的边界防御机制"""
    # 1. 空列表边界
    assert _min_max_normalize([]) == {}

    chunk1 = CodeChunk("a.py", 1, 10, "code1", ["code1"])
    chunk2 = CodeChunk("b.py", 5, 15, "code2", ["code2"])

    # 2. 所有候选块分数完全相等且 > 0 -> 应统一归一化为 1.0
    res_equal = _min_max_normalize([(chunk1, 15.0), (chunk2, 15.0)])
    assert res_equal[_chunk_key(chunk1)] == 1.0
    assert res_equal[_chunk_key(chunk2)] == 1.0

    # 3. 所有候选块分数全为 0.0 -> 应统一保持 0.0
    res_zero = _min_max_normalize([(chunk1, 0.0), (chunk2, 0.0)])
    assert res_zero[_chunk_key(chunk1)] == 0.0
    assert res_zero[_chunk_key(chunk2)] == 0.0

    # 4. 阶梯差值归一化验证
    chunk3 = CodeChunk("c.py", 1, 5, "code3", ["code3"])
    res_ladder = _min_max_normalize([(chunk1, 10.0), (chunk2, 20.0), (chunk3, 30.0)])
    assert res_ladder[_chunk_key(chunk1)] == 0.0
    assert res_ladder[_chunk_key(chunk2)] == 0.5
    assert res_ladder[_chunk_key(chunk3)] == 1.0


def test_hybrid_retriever_build_and_search(tmp_path: Path) -> None:
    """端到端验证构建索引、双路召回与来源标注"""
    test_file = tmp_path / "billing.py"
    test_file.write_text(
        "class TokenBillingEngine:\n"
        "    \"\"\"负责计算模型调用所消耗的美元费用和账单记录。\"\"\"\n"
        "    def calculate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:\n"
        "        rate = 0.002\n"
        "        return (prompt_tokens + completion_tokens) * rate\n"
        "\n"
        "def audit_api_quota(user_id: str) -> bool:\n"
        "    \"\"\"检查用户调用配额是否用尽。\"\"\"\n"
        "    return True\n"
        "\n"
        "def get_system_uptime() -> float:\n"
        "    \"\"\"获取系统运行时间。\"\"\"\n"
        "    return 3600.0\n",
        encoding="utf-8",
    )

    retriever = HybridRetriever(alpha=0.5)
    total_chunks = retriever.build_from_directory(tmp_path)
    assert total_chunks >= 2

    # 1. 精确符号查询：词法与语义共同召回
    results_exact = retriever.search("calculate_cost", top_k=2)
    assert len(results_exact) > 0
    top_hit = results_exact[0]
    assert "calculate_cost" in top_hit.chunk.content
    assert top_hit.source == "BOTH"
    assert top_hit.score > 0.0

    # 2. 纯自然语言抽象概念意图（源码无“账单开销核算”字样）：向量挽救 BM25
    # 2. 纯自然语言抽象概念意图（源码无对应英文字号）：向量挽救 BM25
    results_semantic = retriever.search("计算模型调用费用与美元账单", top_k=2)
    assert len(results_semantic) > 0
    sem_hit = results_semantic[0]
    assert "TokenBillingEngine" in sem_hit.chunk.content or "calculate_cost" in sem_hit.chunk.content
    # 词法可能未命中，此时 source 应为 VECTOR 或 BOTH
    assert sem_hit.source in ("VECTOR", "BOTH")

    # 3. 空查询防御
    assert retriever.search("") == []
    assert retriever.search("   ") == []


def test_hybrid_retriever_alpha_weight(tmp_path: Path) -> None:
    """测试权重 alpha 对融合打分的调节作用"""
    test_file = tmp_path / "demo.py"
    test_file.write_text(
        "def format_commit_message(msg: str) -> str:\n"
        "    return msg.strip().capitalize()\n"
        "\n"
        "def parse_git_branch(ref: str) -> str:\n"
        "    return ref.split('/')[-1]\n"
        "\n"
        "def check_git_status() -> bool:\n"
        "    return True\n",
        encoding="utf-8",
    )

    # 纯词法倾向 (alpha = 1.0)
    retriever_bm25_only = HybridRetriever(alpha=1.0)
    retriever_bm25_only.build_from_directory(tmp_path)
    hits = retriever_bm25_only.search("format_commit_message", top_k=1)
    assert len(hits) == 1
    # 最终分应当等于 BM25 归一化分
    assert hits[0].score == hits[0].bm25_norm_score