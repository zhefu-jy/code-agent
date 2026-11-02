from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple
from rank_bm25 import BM25Okapi
from code_agent.index.bm25_index import BM25CodeIndex, CodeChunk
from code_agent.index.vector_index import VectorCodeIndex
SourceType = Literal["BM25", "VECTOR", "BOTH"]


@dataclass
class HybridSearchResult:
    """混合检索返回的统一结果契约"""
    chunk: CodeChunk
    score: float  # 加权融合最终分 [0.0, 1.0]
    source: SourceType  # 命中来源标签
    bm25_norm_score: float = 0.0  # 归一化后的 BM25 分
    vec_norm_score: float = 0.0  # 归一化后的 Vector 分
    raw_bm25_score: float = 0.0  # 原始 BM25 分
    raw_vec_score: float = 0.0  # 原始向量余弦相似度分


def _chunk_key(chunk:CodeChunk)->Tuple[str,int,int]:
    """生成能够唯一标识物理代码块的元组键"""
    return (chunk.file_path, chunk.start_line, chunk.end_line)

def _min_max_normalize(
        scored_items: List[Tuple[CodeChunk, float]]
)->Dict[Tuple[str,int,int],float]:
    """
       将一组 (CodeChunk, score) 的分数使用 Min-Max 归一化映射到 [0.0, 1.0]。

       防御性边界处理：
       1. 列表为空 -> 返回空字典
       2. 最大值等于最小值且 > 0 -> 统一赋予 1.0
       3. 最大值等于最小值且 <= 0 -> 统一赋予 0.0
       4. 正常区间 -> (s - min) / (max - min)
    """
    if not scored_items:
        return {}
    scores = [score for _,score in scored_items]
    max_score = max(scores)
    min_score = min(scores)

    norm_map: Dict[Tuple[str, int, int], float] = {}

    if max_score == min_score:
        default_val = 1.0 if max_score > 0 else 0.0
        for chunk,_ in scored_items:
            norm_map[_chunk_key(chunk)] = default_val
        return norm_map

    diff = max_score - min_score
    for chunk,score in scored_items:
        norm_val = max(0.0,(score - min_score) / diff)
        norm_map[_chunk_key(chunk)] = norm_val

    return norm_map

class HybridRetriever:
    """
       基于 BM25 词法与 ChromaDB 向量语义的双路召回混合检索器。

       核心特性：
       1. 底座对齐：统一使用 ASTChunker 语法切块构建 BM25 与向量双路索引；
       2. 量纲归一：Min-Max 动态映射抹平 BM25 无界得分与向量余弦相似度的尺度鸿沟；
       3. 加权融合：支持 alpha 权重微调与来源实体对齐去重。
    """
    def __init__(self,
                 bm25_index: Optional[BM25CodeIndex] =None,
                 vector_index: Optional[VectorCodeIndex] =None,
                 alpha = 0.5,
                 )->None:
        self.bm25_index = bm25_index or BM25CodeIndex()
        self.vector_index = vector_index or VectorCodeIndex()
        # 严格限制权重在 [0.0, 1.0] 合法区间
        self.alpha = max(0.0, min(1.0, alpha))

    def build_from_directory(
            self,
            root_dir: str | Path,
            extensions: Tuple[str, ...] = (".py",),
    ) -> int:
        """
        扫描目录并构建双路索引。
        先由向量索引器生成 AST 切块入库，再把完全一致的切块语料同步给 BM25。
        """
        # 1. 向量索引器生成 AST 语法切块并存入本地 ChromaDB
        count = self.vector_index.build_from_directory(root_dir,extensions)

        # 2. 将同一份 AST chunks 同步喂给 BM25
        if hasattr(self.bm25_index,"build_from_chunks"):
            self.bm25_index.build_from_chunks(self.vector_index.chunks)
        else:
            self.bm25_index.chunks = list(self.vector_index.chunks)
            if self.bm25_index.chunks:
                corpus = [c.tokens for c in self.bm25_index.chunks]
                self.bm25_index.bm25 = BM25Okapi(corpus)

        return count


    def search(
            self,
            query: str,
            top_k: int = 3,
    ) -> List[HybridSearchResult]:
        """
        双路召回 -> Min-Max 独立归一化 -> 实体对齐加权打分 -> 降序截断 Top-K
        """
        if not query.strip():
            return []

        # 双路召回池适度放大，保证去重融合后有充足的候选（取 2 * top_k，至少 5 个）
        fetch_k = max(top_k * 2, 5)

        bm25_hits = self.bm25_index.search(query, top_k=fetch_k)
        vec_hits = self.vector_index.search(query, top_k=fetch_k)

        if not bm25_hits and not vec_hits:
            return []

        # 各自独立完成 Min-Max 归一化
        bm25_norm_map = _min_max_normalize(bm25_hits)
        vec_norm_map = _min_max_normalize(vec_hits)

        # 收集块实体与原始打分字典
        chunk_map: Dict[Tuple[str, int, int], CodeChunk] = {}
        raw_bm25_map: Dict[Tuple[str, int, int], float] = {}
        raw_vec_map: Dict[Tuple[str, int, int], float] = {}

        for chunk, score in bm25_hits:
            k = _chunk_key(chunk)
            chunk_map[k] = chunk
            raw_bm25_map[k] = score

        for chunk, score in vec_hits:
            k = _chunk_key(chunk)
            chunk_map[k] = chunk
            raw_vec_map[k] = score

        # 候选实体求并集
        all_keys = set(chunk_map.keys())
        results: List[HybridSearchResult] = []

        for k in all_keys:
            chunk = chunk_map[k]
            in_bm25 = k in bm25_norm_map
            in_vec = k in vec_norm_map

            #判定命中来源
            if in_bm25 and in_vec:
                source: SourceType = "BOTH"
            elif in_bm25:
                source = "BM25"
            else:
                source = "VECTOR"

            norm_b = bm25_norm_map.get(k, 0.0)
            norm_v = vec_norm_map.get(k, 0.0)
            # 加权打分公式
            final_score = self.alpha * norm_b + (1.0 - self.alpha) * norm_v
            results.append(
                HybridSearchResult(
                    chunk=chunk,
                    score=round(final_score, 4),
                    source=source,
                    bm25_norm_score=round(norm_b, 4),
                    vec_norm_score=round(norm_v, 4),
                    raw_bm25_score=round(raw_bm25_map.get(k, 0.0), 4),
                    raw_vec_score=round(raw_vec_map.get(k, 0.0), 4),
                )
            )

        # 按加权融合得分降序排序，截取最终 Top-K
        results.sort(key=lambda item: item.score, reverse=True)
        return results[:top_k]

