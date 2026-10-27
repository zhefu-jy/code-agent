from pathlib import Path
import sys

from code_agent.index.bm25_index import BM25CodeIndex
from code_agent.index.vector_index import VectorCodeIndex


def run_comparison() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    src_dir = Path(__file__).resolve().parent.parent / "src"
    print("=" * 65)
    print("📊 [W5 S2] 词法检索 (BM25) vs 语义向量检索 (ChromaDB) 对照实验")
    print(f"目标源码目录: {src_dir}")
    print("=" * 65 + "\n")

    # 1. 构建 BM25 词法索引
    bm25 = BM25CodeIndex()
    bm25_count = bm25.build_from_directory(src_dir)
    print(f"[BM25] 构建完成，共生成 {bm25_count} 个滑动窗口代码切块")

    # 2. 构建向量语义索引
    vec = VectorCodeIndex(collection_name="eval_compare_vector")
    vec_count = vec.build_from_directory(src_dir)
    print(f"[Vector] 构建完成，共存入 {vec_count} 个 AST 语法感知代码切块\n")

    queries = [
        (
            "Query 1: 概念意图（纯中文自然语言）",
            "如何校验引用合法性与出处行号",
        ),
        (
            "Query 2: 架构模式（概念关联，无直接命名）",
            "大模型如何做结构化输出与代码审查报告",
        ),
        (
            "Query 3: 语法解析（混合术语）",
            "提取AST语法树的类和函数切块实现",
        ),
        (
            "Query 4: 精确符号定位（纯代码符号）",
            "validate_citations",
        ),
    ]

    for title, q in queries:
        print(f"\n{'=' * 70}")
        print(f"🔍 【{title}】")
        print(f"   查询语句: \"{q}\"")
        print(f"{'=' * 70}")

        # BM25 检索
        bm25_hits = bm25.search(q, top_k=2)
        print(f"\n--- [BM25 词法检索结果] --- (命中数: {len(bm25_hits)})")
        if not bm25_hits:
            print("  ❌ 未检索到任何相关切块 (Score = 0.0，因英文分词器过滤导致 Query Token 蒸发)")
        else:
            for rank, (chunk, score) in enumerate(bm25_hits, 1):
                clean_path = Path(chunk.file_path).name
                preview = chunk.content.splitlines()[0] if chunk.content else ""
                print(
                    f"  Top {rank} | 得分: {score:.2f} | 文件: {clean_path} "
                    f"(L{chunk.start_line}~L{chunk.end_line}) | 内容: {preview[:60]}..."
                )

        # Vector 向量检索
        vec_hits = vec.search(q, top_k=2)
        print(f"\n--- [Vector 语义检索结果] --- (命中数: {len(vec_hits)})")
        if not vec_hits:
            print("  ❌ 未检索到任何相关切块")
        else:
            for rank, (chunk, score) in enumerate(vec_hits, 1):
                clean_path = Path(chunk.file_path).name
                symbol = f" [{chunk.symbol_name}]" if chunk.symbol_name else ""
                preview = chunk.content.splitlines()[0] if chunk.content else ""
                print(
                    f"  Top {rank} | 相似度: {score:.4f} | 文件: {clean_path}{symbol} "
                    f"(L{chunk.start_line}~L{chunk.end_line}) | 内容: {preview[:60]}..."
                )


if __name__ == "__main__":
    run_comparison()