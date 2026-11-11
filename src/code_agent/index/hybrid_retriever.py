import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple
from rank_bm25 import BM25Okapi
from code_agent.index.bm25_index import BM25CodeIndex, CodeChunk, DEFAULT_IGNORE_DIRS
from code_agent.index.vector_index import VectorCodeIndex
from code_agent.index.ast_chunker import ASTChunker
from code_agent.index.markdown_chunker import MarkdownDocChunker
SourceType = Literal["BM25", "VECTOR", "BOTH", "CONTEXT"]


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
    is_definition: bool = False  # 是否为被查标识符的物理定义块（排序优先级最高）
    definition_strength: int = 0  # 定义匹配置信度：符号命中了查询中的几个标识符
    is_context: bool = False  # 是否为"依赖补全"块（被选中的定义块所引用的同文件常量）


def _chunk_key(chunk:CodeChunk)->Tuple[str,int,int]:
    """生成能够唯一标识物理代码块的元组键"""
    return (chunk.file_path, chunk.start_line, chunk.end_line)


def matches_identity(symbols: List[str], identifiers: set[str]) -> bool:
    """判断切块声明的符号（含类.方法 / 类 (header) 形式）是否命中查询中的标识符。

    多符号块（顶层聚合块）必须逐个比较，不能把逗号拼接后的整串拿去精确匹配，
    否则 `_CITATION_PATTERN` 这类"定义在聚合块里"的符号永远无法被定义提权命中。
    """
    return definition_strength(symbols, identifiers) > 0


def definition_strength(symbols: List[str], identifiers: set[str]) -> int:
    """定义匹配置信度 = 符号里命中了查询中的几个标识符。

    必要性：`BM25CodeIndex.build_from_directory` 与 `VectorCodeIndex.build_from_directory`
    的方法名相同，只按"方法名命中"会让三个类的同名方法并列为定义块，
    真正被问的那个类反而无法排到最前。命中标识符越多（类名 + 方法名都命中）越可信。

    最高分为 2：只命中方法名得 1，类名与方法名都命中得 2。
    裸函数（如 `tokenize_code`）只命中一次，与"仅方法名命中"同分，
    由后续的融合分与路径字典序做确定性 tie-break。
    """
    best = 0
    for symbol in symbols:
        low = symbol.lower().strip()
        if not low:
            continue
        base = low.replace("(header)", "").strip()
        tail = base.rsplit(".", 1)[-1].strip()
        # 类名与方法名都命中才算 2 分；否则只要任一形式命中即得 1 分
        if base == low and "." in low and base in identifiers and tail in identifiers and tail != base:
            best = max(best, 2)
        elif tail in identifiers or base in identifiers:
            best = max(best, 1)
    return best

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
        self.chunks: List[CodeChunk] = []


    def build_from_chunks(self,chunks: List[CodeChunk])-> int:
        """
        直接加载预先切好的多源语义切块（代码 AST 块与 Markdown 文档块）。
        保证 BM25 与向量库底层共享完全相同的切块实体与元数据契约。
        """
        self.chunks = list(chunks)
        # 1. 写入向量库底座
        self.vector_index.build_from_chunks(self.chunks,clear_existing=True)
        # 2.同步写入BM25倒排索引
        self.bm25_index.build_from_chunks(self.chunks)
        return len(self.chunks)


    def build_from_sources(
            self,
            code_dir: Optional[str|Path]=None,
            doc_dir: Optional[str|Path]=None,
            code_extensions: Tuple[str,...] = (".py",),
            doc_exclude_patterns: Tuple[str,...] = ("*安排*", "*progress*", "*notes*"),
    )->int:
        """
        跨源统一构建：分别对代码与文档应用专业切块器，汇流后联合构建双路索引。
        - code_dir: Python 代码目录（使用 ASTChunker 解析类与函数）
        - doc_dir: Markdown 文档目录（使用 MarkdownDocChunker 解析标题树与面包屑）
        - doc_exclude_patterns: 排除过程性描述文档（如日程安排、日志与备忘），避免词法污染
        """
        all_chunks: List[CodeChunk] = []
        # 1. 扫描与切分代码源
        if code_dir:
            code_path = Path(code_dir)
            if code_path.exists():
                code_chunker = ASTChunker()
                for p in code_path.rglob("*"):
                    if any(part in DEFAULT_IGNORE_DIRS for part in p.parts):
                        continue
                    if p.is_file() and p.suffix.lower() in code_extensions:
                        all_chunks.extend(code_chunker.chunk_file(p))

        # 2. 扫描与切分权威技术文档源（排除过程性演进文档）
        if doc_dir:
            doc_path = Path(doc_dir)
            if doc_path.exists():
                doc_chunker = MarkdownDocChunker()
                for p in doc_path.rglob("*.md"):
                    if any(part in DEFAULT_IGNORE_DIRS for part in p.parts):
                        continue
                    if any(p.match(pattern) for pattern in doc_exclude_patterns):
                        continue
                    if p.is_file():
                        all_chunks.extend(doc_chunker.chunk_file(p))

        return self.build_from_chunks(all_chunks)




    def build_from_directory(
            self,
            root_dir: str | Path,
            extensions: Tuple[str, ...] = (".py",),
            doc_dir: Optional[str | Path] = None,
    ) -> int:
        """
        扫描目录并构建双路索引。
         向后兼容既有单目录接口，同时支持传入 doc_dir 实现一键双源联合索引构建。
        """
        return self.build_from_sources(
            code_dir=root_dir,
            doc_dir=doc_dir,
            code_extensions=extensions,
        )


    def search(
            self,
            query: str,
            top_k: int = 3,
    ) -> List[HybridSearchResult]:
        """
        双路召回 -> Min-Max 独立归一化 -> 实体对齐加权打分 -> 定义提权与源配额 -> 确定性 Tie-Break 降序截断 Top-K
        """
        if not query.strip():
            return []

        # 双路召回池适度放大，保证候选丰富度
        fetch_k = max(top_k * 5, 20)

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

        # 提取查询中的代码标识符（用于定义块精准命中，过滤通用无意义单词）
        GENERIC_WORDS = {
            "search", "main", "get", "set", "run", "chunk", "test",
            "demo", "file", "text", "line", "code", "index", "doc", "docs"
        }
        query_identifiers = {w.lower() for w in re.findall(r'[a-zA-Z_][a-zA-Z0-9_]*', query) if len(w) > 1}
        specific_identifiers = query_identifiers - GENERIC_WORDS

        # 针对库中直接定义了被查符号的块，进行保底候选注入
        for c in self.chunks:
            if matches_identity(c.all_symbols(), specific_identifiers):
                k = _chunk_key(c)
                if k not in chunk_map:
                    chunk_map[k] = c
                    raw_bm25_map[k] = 0.0
                    raw_vec_map[k] = 0.0

        # 确定性排序迭代 keys（消除 set 随机哈希带来的评测不可复现）
        deterministic_keys = sorted(chunk_map.keys(), key=lambda x: (x[0], x[1], x[2]))
        all_candidates: List[HybridSearchResult] = []

        # 语言感知融合：当查询包含中文时，由于当前向量模型为英文单语模型 (all-MiniLM-L6-v2)，
        # 跨语言余弦相似度信噪比较低；动态提升具备高精度 CJK 1/2-gram 倒排索引的 BM25 权重
        effective_alpha = self.alpha
        if re.search(r'[\u4e00-\u9fff]', query):
            effective_alpha = max(self.alpha, 0.7)

        for k in deterministic_keys:
            chunk = chunk_map[k]
            in_bm25 = k in bm25_norm_map
            in_vec = k in vec_norm_map

            # 判定命中来源
            if in_bm25 and in_vec:
                source: SourceType = "BOTH"
            elif in_bm25:
                source = "BM25"
            else:
                source = "VECTOR"

            norm_b = bm25_norm_map.get(k, 0.0)
            norm_v = vec_norm_map.get(k, 0.0)
            # 加权打分公式保持在 [0.0, 1.0] 量纲
            final_score = effective_alpha * norm_b + (1.0 - effective_alpha) * norm_v
            is_def = matches_identity(chunk.all_symbols(), specific_identifiers)
            strength = definition_strength(chunk.all_symbols(), specific_identifiers)

            all_candidates.append(
                HybridSearchResult(
                    chunk=chunk,
                    score=round(final_score, 4),
                    source=source,
                    bm25_norm_score=round(norm_b, 4),
                    vec_norm_score=round(norm_v, 4),
                    raw_bm25_score=round(raw_bm25_map.get(k, 0.0), 4),
                    raw_vec_score=round(raw_vec_map.get(k, 0.0), 4),
                    is_definition=is_def,
                    definition_strength=strength,
                )
            )

        def _is_definition_match(item: HybridSearchResult) -> int:
            """判断是否为被查询标识符的物理定义块（专治 mention-vs-definition 倒置）"""
            return 1 if item.is_definition else 0

        # 严格确定性 Tie-Break 排序：(定义置信度降序, 融合分降序, file_path 字典序, start_line 升序)
        all_candidates.sort(
            key=lambda item: (
                -item.definition_strength,
                -item.score,
                item.chunk.file_path,
                item.chunk.start_line,
            )
        )

        # 跨源意图配额保障：当查询明确涉及跨源协同（同时涉及规约与代码）时，确保两路各至少占一席
        has_doc_intent = any(w in query for w in ["文档", "ADR", "选型", "方案", "规约", "决策"])
        has_code_intent = bool(specific_identifiers) or any(w in query for w in ["源码", "代码", "函数", "类", "实现", "定义"])

        code_cands = [item for item in all_candidates if item.chunk.source_type == "code"]
        doc_cands = [item for item in all_candidates if item.chunk.source_type == "doc"]

        # 显式提及文件名提取（例如 cli.py, model.py 等）
        mentioned_files = [m.lower() for m in re.findall(r'[\w\-]+\.(?:py|md)', query)]

        selected: List[HybridSearchResult] = []
        seen_files: set[str] = set()
        selected_keys: set[Tuple[str, int, int]] = set()
        # 每个文件最多占用的席位。旧实现是"每文件严格 1 块"，会让「同一文件内需要
        # 跨块取证」的问题（如 014 要正则定义 + 抽取实现、017 要常量 + 方法）在结构上
        # 永远凑不齐证据；这里改为软配额：先保证文件多样性，再允许同文件补第二块。
        max_chunks_per_file = 2
        file_quota: Dict[str, int] = {}

        def _take(item: HybridSearchResult) -> None:
            key = _chunk_key(item.chunk)
            if key in selected_keys:
                return
            selected.append(item)
            selected_keys.add(key)
            seen_files.add(item.chunk.file_path)
            file_quota[key[0]] = file_quota.get(key[0], 0) + 1

        # 阶段 1：跨源配额保底（若意图同时覆盖文档与源码）
        if has_doc_intent and has_code_intent and code_cands and doc_cands and top_k >= 2:
            _take(code_cands[0])
            _take(doc_cands[0])

        # 阶段 2：显式提及文件保底（专治跨文件联合问答如 Case 021）
        for mf in mentioned_files:
            if len(selected) >= top_k:
                break
            for item in all_candidates:
                fp_norm = item.chunk.file_path.replace("\\", "/").lower()
                if (fp_norm.endswith("/" + mf) or fp_norm == mf) and item.chunk.file_path not in seen_files:
                    _take(item)
                    break

        # 阶段 3：定义块优先（命中查询标识符定义处的块，允许与阶段 1 同文件，
        # 只要该文件尚未用满配额，保证"定义处"不会被同文件的其他块挤掉）
        for item in all_candidates:
            if len(selected) >= top_k:
                break
            if _is_definition_match(item) and file_quota.get(item.chunk.file_path, 0) < max_chunks_per_file:
                _take(item)

        # 阶段 3.5：同名定义块的同文件保底
        # `chunk_file` 既是 `ASTChunker.chunk_file`（归一后只命中方法名，得 1 分）
        # 也是 `bm25_index.py` 里的裸函数（同样得 1 分），而前者的融合分更高。
        # 这类"同名但不同文件"的定义块必须让真正被问的那个文件也进上下文，
        # 否则模型只能看到 ast_chunker 的版本，对 bm25_index 的实现回答"无依据"
        # （Case 003 / 016）。这里对低置信度定义块补一个同文件席位。
        ambiguous_files: List[str] = []
        for item in all_candidates:
            if (
                item.is_definition
                and item.definition_strength < 2
                and item.chunk.file_path not in ambiguous_files
                and any(
                    other.is_definition
                    and other.definition_strength < 2
                    and other.chunk.file_path != item.chunk.file_path
                    for other in all_candidates
                )
            ):
                ambiguous_files.append(item.chunk.file_path)
        for file_path in ambiguous_files:
            if len(selected) >= top_k:
                break
            if file_quota.get(file_path, 0) > 0:
                continue
            # 优先在该文件里取"同名定义块"，其次取该文件排名最高的块
            same_name = next(
                (
                    item for item in all_candidates
                    if item.chunk.file_path == file_path and item.is_definition
                ),
                None,
            )
            best = same_name or next(
                (item for item in all_candidates if item.chunk.file_path == file_path), None
            )
            if best is not None:
                _take(best)
            # 同名定义的真正定义块可能还没进候选池（BM25/向量池只取前 N 条），
            # 直接在全量切块里补上
            if len(selected) < top_k:
                for chunk in self.chunks:
                    if chunk.file_path != file_path or _chunk_key(chunk) in selected_keys:
                        continue
                    if matches_identity(chunk.all_symbols(), specific_identifiers):
                        _take(
                            HybridSearchResult(
                                chunk=chunk,
                                score=0.0,
                                source="CONTEXT",
                                is_definition=True,
                                is_context=True,
                            )
                        )
                        break

        # 阶段 4：文件级多样性补足（避免同文件次优块垄断剩余席位）
        for item in all_candidates:
            if len(selected) >= top_k:
                break
            if item.chunk.file_path not in seen_files:
                _take(item)

        # 阶段 5：配额未满时，用同文件的高分块补齐（跨块取证场景）
        for item in all_candidates:
            if len(selected) >= top_k:
                break
            if file_quota.get(item.chunk.file_path, 0) < max_chunks_per_file:
                _take(item)

        # 阶段 6：仍未满（top_k 大于可用文件数 * 配额）时放宽限制
        for item in all_candidates:
            if len(selected) >= top_k:
                break
            _take(item)

        # 阶段 7：依赖补全（一次静态引用跳转）
        # 用户问「X 函数怎么做的」，答案却可能落在 X 引用的模块级常量里：
        #   build_from_directory → DEFAULT_IGNORE_DIRS
        #   has_no_evidence      → _NO_EVIDENCE_PATTERN
        # 检索只能按查询词命中"函数"，永远命不中"它用到的那个常量"（向量与 BM25 都无词面重叠）。
        # 只补「被主定义块引用的同文件模块级常量」：
        # - 不补被引用的函数/类：它们通常已有自己的词面命中，补进来只会挤掉真正的定义块
        #   （Case 003/016 的 chunk_file 就曾被同文件的 tokenize_code / CodeChunk 挤掉）；
        # - 只针对主定义块，避免多个定义块各自补全把上下文塞满。
        primary_def = next((item for item in selected if item.is_definition), None)
        completion: List[HybridSearchResult] = []
        completed_keys: set[Tuple[str, int, int]] = set()
        if primary_def is not None:
            for other in self.chunks:
                if other.source_type != "code" or other.file_path != primary_def.chunk.file_path:
                    continue
                other_key = _chunk_key(other)
                if other_key in selected_keys or other_key in completed_keys:
                    continue
                if not self._looks_like_constant(other):
                    continue
                if self._references_symbol(primary_def.chunk.content, other.all_symbols()):
                    completion.append(
                        HybridSearchResult(
                            chunk=other,
                            score=0.0,
                            source="CONTEXT",
                            is_context=True,
                        )
                    )
                    completed_keys.add(other_key)

        if completion:
            # 顺序 = 主定义块 → 依赖补全块 → 其余已选块（保持原相对顺序）。
            # 关键约束（W6 复盘踩到的坑）：补全块只能挤占**非定义块**的席位，
            # 否则会出现"为了补一个常量，把另一个同名定义块挤掉"的情况
            # （Case 003/016：bm25_index 的 chunk_file 被同文件 CodeChunk 顶替）。
            primary = next((item for item in selected if item is primary_def), None)
            completion.sort(key=lambda item: item.chunk.start_line)
            head: List[HybridSearchResult] = [primary] if primary is not None else []
            rest = [item for item in selected if item is not primary]

            ordered: List[HybridSearchResult] = []
            seen_keys: set[Tuple[str, int, int]] = set()

            def _push(item: HybridSearchResult) -> None:
                key = _chunk_key(item.chunk)
                if key in seen_keys:
                    return
                seen_keys.add(key)
                ordered.append(item)

            for item in head:
                _push(item)

            # 补全块按需插入：只有在"还有非定义块可让位"时才占用席位
            for extra in completion:
                if len(ordered) >= top_k:
                    victim = next(
                        (idx for idx in range(len(ordered) - 1, -1, -1) if not ordered[idx].is_definition),
                        None,
                    )
                    if victim is None:
                        break
                    ordered.pop(victim)
                _push(extra)

            for item in rest:
                if len(ordered) >= top_k:
                    break
                _push(item)
            # 定义块若因补全被挤掉，回补（宁可少一个补全块）
            for item in rest:
                if len(ordered) >= top_k:
                    break
                _push(item)

            return ordered[:top_k]

        return selected[:top_k]

    @staticmethod
    def _looks_like_constant(chunk: CodeChunk) -> bool:
        """判断切块是否是模块级常量/配置（而非函数、类或数据模型）"""
        if re.fullmatch(r"[A-Z_][A-Z0-9_]*", chunk.symbol_name or ""):
            return True
        head = chunk.content.lstrip()
        if head.startswith(("def ", "class ", "async def ")):
            return False
        return True

    @staticmethod
    def _references_symbol(content: str, symbols: List[str]) -> bool:
        """判断代码块正文是否引用了给定符号之一（用词边界避免子串误命中）"""
        for symbol in symbols:
            if len(symbol) < 4:
                continue
            if re.search(rf"(?<![\w.]){re.escape(symbol)}(?![\w])", content):
                return True
        return False

