from pathlib import Path
import pytest

from code_agent.index.bm25_index import CodeChunk
from code_agent.index.vector_index import VectorCodeIndex


def test_vector_index_build_and_search(tmp_path: Path) -> None:
    # 构造临时测试源码
    code_file = tmp_path / "sample_service.py"
    code_file.write_text(
        "class TokenUsageTracker:\n"
        "    \"\"\"负责记录和累计调用耗费的输入输出Token总量。\"\"\"\n"
        "    def __init__(self) -> None:\n"
        "        self.total_tokens = 0\n"
        "\n"
        "    def add_usage(self, prompt_tokens: int, completion_tokens: int) -> int:\n"
        "        self.total_tokens += prompt_tokens + completion_tokens\n"
        "        return self.total_tokens\n"
        "\n"
        "def compute_cosine_distance(vec_a: list[float], vec_b: list[float]) -> float:\n"
        "    \"\"\"计算两个多维浮点向量之间的余弦距离。\"\"\"\n"
        "    return 0.0\n",
        encoding="utf-8",
    )

    indexer = VectorCodeIndex(collection_name="test_vector_collection")
    total_chunks = indexer.build_from_directory(tmp_path)
    assert total_chunks >= 2

    # 1. 语义查询：Token 消耗统计（源码正文中并无“消耗统计”这四个字）
    query = "统计消耗了多少Token"
    results = indexer.search(query, top_k=2)
    assert len(results) > 0
    top_chunk, score = results[0]
    assert score > 0.0
    assert "TokenUsageTracker" in top_chunk.content or "add_usage" in top_chunk.content

    # 2. 语义查询：向量相似度与距离
    query_dist = "计算多维向量相似度与距离"
    results_dist = indexer.search(query_dist, top_k=2)
    assert len(results_dist) > 0
    top_dist_chunk, dist_score = results_dist[0]
    assert dist_score > 0.0
    assert "compute_cosine_distance" in top_dist_chunk.content


def test_vector_index_empty_cases(tmp_path: Path) -> None:
    indexer = VectorCodeIndex(collection_name="test_empty_collection")

    # 针对空索引库搜索
    assert indexer.search("任意测试查询", top_k=3) == []

    # 针对空文本搜索
    assert indexer.search("", top_k=3) == []
    assert indexer.search("   ", top_k=3) == []


def test_vector_index_build_from_chunks_multi_source() -> None:
    """测试 VectorCodeIndex 接收代码与文档多源切块并完整还原元数据标签（契约测试）"""
    # 纯净代码块：无人工修饰的注释
    code_chunk = CodeChunk(
        file_path="src/auth.py",
        start_line=10,
        end_line=25,
        content="def verify_jwt_token(token: str) -> bool:\n    return token.startswith('bearer_')",
        tokens=["def", "verify", "jwt", "token"],
        symbol_name="verify_jwt_token",
        source_type="code",
    )
    # 纯净文档块：技术决策描述
    doc_chunk = CodeChunk(
        file_path="docs/ADR-002.md",
        start_line=1,
        end_line=18,
        content="本项目采用 JWT Bearer Token 作为分布式微服务统一鉴权凭据与方案选型。",
        tokens=["jwt", "token", "鉴", "权"],
        source_type="doc",
        title_path="架构选型 > 鉴权方案",
    )

    indexer = VectorCodeIndex(collection_name="test_multi_source_collection")
    total = indexer.build_from_chunks([code_chunk, doc_chunk])
    assert total == 2

    # 1. 验证文档召回与元数据完整性
    res_doc = indexer.search("微服务统一鉴权方案与架构选型", top_k=1)
    assert len(res_doc) == 1
    hit_doc, score_doc = res_doc[0]
    assert score_doc > 0.0
    assert hit_doc.file_path == "docs/ADR-002.md"
    assert hit_doc.source_type == "doc"
    assert hit_doc.title_path == "架构选型 > 鉴权方案"
    assert hit_doc.symbol_name == ""

    # 2. 验证代码符号召回与元数据完整性
    res_code = indexer.search("verify_jwt_token", top_k=1)
    assert len(res_code) == 1
    hit_code, score_code = res_code[0]
    assert score_code > 0.0
    assert hit_code.file_path == "src/auth.py"
    assert hit_code.source_type == "code"
    assert hit_code.symbol_name == "verify_jwt_token"
    assert hit_code.title_path == ""