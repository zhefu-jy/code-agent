from pathlib import Path
import pytest
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