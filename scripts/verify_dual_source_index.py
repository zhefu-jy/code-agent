from pathlib import Path
import sys
from code_agent.index.ast_chunker import ASTChunker
from code_agent.index.bm25_index import BM25CodeIndex, CodeChunk
from code_agent.index.markdown_chunker import MarkdownDocChunker
from code_agent.index.vector_index import VectorCodeIndex

def main()->None:
    # 保证 Windows 控制台 UTF-8 输出正常
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print("=" * 60)
    print("🚀 开始构建代码与文档双源联合检索底座...")
    print("=" * 60)
    # 1. 提取真实源码切块 (src/)
    code_chunker = ASTChunker()
    code_chunks: list[CodeChunk] = []
    src_dir = Path("src")
    for py_file in src_dir.rglob("*.py"):
        if "__pycache__" in py_file.parts:
            continue
        code_chunks.extend(code_chunker.chunk_file(py_file))

    #2. 提取真实技术文档切块（docs）
    doc_chunker = MarkdownDocChunker()
    doc_chunks: list[CodeChunk] = []
    docs_dir = Path("docs")
    for md_file in docs_dir.rglob("*.md"):
        doc_chunks.extend(doc_chunker.chunk_file(md_file))

    # 3. 汇流双源语料
    all_chunks = code_chunks + doc_chunks
    print(f"📦 语料汇流完成：总切块数 = {len(all_chunks)} (代码块: {len(code_chunks)} | 文档块: {len(doc_chunks)})")

    # 4. 灌入 BM25 与 Vector 向量库
    print("\n🔨 正在构建 BM25 倒排索引...")
    bm25 = BM25CodeIndex()
    bm25.build_from_chunks(all_chunks)
    print("🔨 正在写入 ChromaDB 向量库...")
    vec = VectorCodeIndex(collection_name="dual_source_verify_collection")
    vec.build_from_chunks(all_chunks, clear_existing=True)
    print("✅ 双源索引构建完成！\n")

    # 5. 跨源真实检索测试集
    test_queries = [
        ("代码事实意图", "stream_ask"),
        ("架构决策意图", "阶段1-2不上向量库的原因"),
        ("技术选型意图", "本地轻量数据库存储选型 SQLite"),
    ]

    for category, query in test_queries:
        print("-" * 60)
        print(f"🔍 【{category}】查询词: '{query}'")
        print("-" * 60)

        # BM25 检索
        b_hits = bm25.search(query, top_k=1)
        if b_hits:
            c, s = b_hits[0]
            tag = "💻 [代码]" if c.source_type == "code" else "📄 [文档]"
            info = c.symbol_name if c.source_type == "code" else c.title_path
            print(f"  [BM25 得分: {s:.2f}] {tag} {c.file_path}:{c.start_line}~{c.end_line} 行")
            print(f"    🏷️ 标识: {info}")
            preview = c.content.splitlines()[0] if c.content else ""
            print(f"    📖 首行预览: {preview[:70]}...")
        else:
            print("  [BM25] 无命中")

            # 向量检索
        v_hits = vec.search(query, top_k=1)
        if v_hits:
            c, s = v_hits[0]
            tag = "💻 [代码]" if c.source_type == "code" else "📄 [文档]"
            info = c.symbol_name if c.source_type == "code" else c.title_path
            print(f"  [Vector 相似度: {s:.2f}] {tag} {c.file_path}:{c.start_line}~{c.end_line} 行")
            print(f"    🏷️ 标识: {info}")
            preview = c.content.splitlines()[0] if c.content else ""
            print(f"    📖 首行预览: {preview[:70]}...")
        else:
            print("  [Vector] 无命中")
            print()


if __name__ == "__main__":
    main()

