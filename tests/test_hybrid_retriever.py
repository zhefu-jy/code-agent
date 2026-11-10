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


def test_hybrid_retriever_dual_sources(tmp_path: Path) -> None:
    """测试代码与 Markdown 文档双源联合扫描、多态契约还原与混合检索"""
    # 1. 构造源码目录与代码文件
    code_dir = tmp_path / "src"
    code_dir.mkdir()
    py_file = code_dir / "auth.py"
    py_file.write_text(
        "class TokenAuthenticator:\n"
        "    \"\"\"负责用户身份令牌的鉴权与合法性校验。\"\"\"\n"
        "    def verify_jwt_token(self, token: str) -> bool:\n"
        "        return len(token) > 10\n",
        encoding="utf-8",
    )

    # 2. 构造技术文档目录与规范文件
    doc_dir = tmp_path / "docs"
    doc_dir.mkdir()
    md_file = doc_dir / "architecture.md"
    md_file.write_text(
        "# 系统架构总览\n\n"
        "## 鉴权设计决策 ADR-002\n\n"
        "本项目统一采用基于 JWT 令牌的对称加密签名鉴权规范。\n",
        encoding="utf-8",
    )

    # 3. 双源构建
    retriever = HybridRetriever(alpha=0.5)
    total = retriever.build_from_sources(code_dir=code_dir, doc_dir=doc_dir)
    assert total >= 2

    # 4. 代码意图检索断言
    code_hits = retriever.search("verify_jwt_token", top_k=1)
    assert len(code_hits) == 1
    c_hit = code_hits[0]
    assert c_hit.chunk.source_type == "code"
    assert c_hit.chunk.symbol_name == "TokenAuthenticator"  # 小类保留整体，符号为类名
    assert "verify_jwt_token" in c_hit.chunk.content

    # 5. 规范意图检索断言
    doc_hits = retriever.search("JWT 令牌对称加密签名", top_k=1)
    assert len(doc_hits) == 1
    d_hit = doc_hits[0]
    assert d_hit.chunk.source_type == "doc"
    assert "鉴权设计决策 ADR-002" in d_hit.chunk.title_path
    assert "JWT" in d_hit.chunk.content

    # 6. 打分契约与来源有效性
    assert 0.0 <= c_hit.score <= 1.0
    assert 0.0 <= d_hit.score <= 1.0
    assert c_hit.source in ("BM25", "VECTOR", "BOTH")
    assert d_hit.source in ("BM25", "VECTOR", "BOTH")