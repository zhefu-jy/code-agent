from pathlib import Path
import pytest
from code_agent.index.bm25_index import BM25CodeIndex, CodeChunk, tokenize_code
from code_agent.index.markdown_chunker import chunk_markdown_file

def test_tokenize_code_english_and_code() -> None:
    """测试纯英文与代码符号分词（向前兼容）"""
    tokens = tokenize_code("def streamAsk(user_query: str):")
    assert "stream" in tokens
    assert "ask" in tokens
    assert "user" in tokens
    assert "query" in tokens
    assert "str" in tokens



def test_tokenize_code_chinese_and_mixed() -> None:
    """测试中文与中英混合分词（1-gram 与 2-gram 提取）"""
    # 纯中文
    tokens_zh = tokenize_code("技术选型决策")
    # 包含单字 1-gram
    assert "技" in tokens_zh and "术" in tokens_zh and "选" in tokens_zh and "型" in tokens_zh
    # 包含双字 2-gram
    assert "技术" in tokens_zh and "术选" in tokens_zh and "选型" in tokens_zh and "型决" in tokens_zh and "决策" in tokens_zh
    # 中英混合
    tokens_mixed = tokenize_code("ADR-001 为什么阶段 1-2 不上向量库？")
    assert "adr" in tokens_mixed
    assert "001" in tokens_mixed
    assert "阶段" in tokens_mixed
    assert "向量" in tokens_mixed
    assert "量库" in tokens_mixed


def test_bm25_chinese_doc_search() -> None:
    """测试 BM25 对中文技术文档切块的精准命中能力（避开 N<=2 极小语料 IDF 负值停用效应）"""
    chunk1 = CodeChunk(
        file_path="docs/storage.md",
        start_line=1,
        end_line=10,
        content="本项目本地轻量数据库选型采用 SQLite，免服务部署。",
        tokens=tokenize_code("本项目本地轻量数据库选型采用 SQLite，免服务部署。"),
        source_type="doc",
        title_path="架构 > 存储选型",
    )
    chunk2 = CodeChunk(
        file_path="docs/vector.md",
        start_line=1,
        end_line=15,
        content="在阶段1-2不上向量库，优先使用 BM25 关键词倒排索引保证确定性。",
        tokens=tokenize_code("在阶段1-2不上向量库，优先使用 BM25 关键词倒排索引保证确定性。"),
        source_type="doc",
        title_path="架构 > 检索决策",
    )
    chunk3 = CodeChunk(
        file_path="docs/api.md",
        start_line=1,
        end_line=20,
        content="API 接口规约与鉴权机制，统一使用 Token 验签。",
        tokens=tokenize_code("API 接口规约与鉴权机制，统一使用 Token 验签。"),
        source_type="doc",
        title_path="规范 > 接口鉴权",
    )
    chunk4 = CodeChunk(
        file_path="src/cli.py",
        start_line=1,
        end_line=30,
        content="CLI 终端交互主循环与流式响应渲染控制台。",
        tokens=tokenize_code("CLI 终端交互主循环与流式响应渲染控制台。"),
        source_type="code",
        symbol_name="main_loop",
    )
    indexer = BM25CodeIndex()
    indexer.build_from_chunks([chunk1, chunk2, chunk3, chunk4])
    # 1. 检索阶段不上向量库
    results = indexer.search("阶段不上向量库的原因", top_k=1)
    assert len(results) == 1
    hit_chunk, score = results[0]
    assert hit_chunk.file_path == "docs/vector.md"
    assert score > 0.0
    # 2. 检索 SQLite 存储
    results_storage = indexer.search("本地轻量数据库存储选型", top_k=1)
    assert len(results_storage) == 1
    hit_storage, score_storage = results_storage[0]
    assert hit_storage.file_path == "docs/storage.md"
    assert score_storage > 0.0


def test_bm25_real_adr_search() -> None:
    """在真实 ADR-001 技术文档切块上验证 BM25 中文检索"""
    adr_path = Path("docs/ADR-001-技术选型.md")
    assert adr_path.exists()
    chunks = chunk_markdown_file(adr_path)
    assert len(chunks) >= 5
    indexer = BM25CodeIndex()
    indexer.build_from_chunks(chunks)
    # 检索不上向量库
    res = indexer.search("为什么阶段1-2不上向量库", top_k=1)
    assert len(res) == 1
    top_chunk, score = res[0]
    assert "向量库" in top_chunk.content
    assert top_chunk.start_line == 38
    assert score > 0.0