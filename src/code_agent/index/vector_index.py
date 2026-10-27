from pathlib import Path
from typing import List, Tuple
import chromadb
from langchain_core import documents

from code_agent.index.ast_chunker import ASTChunker
from code_agent.index.bm25_index import CodeChunk, tokenize_code
from chromadb.api.types import Metadata

class VectorCodeIndex:
    """基于轻量级ChromaDB与AST语法感知切块的语义向量索引器"""

    def __init__(
            self,
            collection_name: str = "code_agent_vector",
            persist_path: str | Path | None = None,
    )->None:
        self.collection_name = collection_name
        self.persist_path = Path(persist_path) if persist_path else None
        self.chunker = ASTChunker()
        self.chunks:List[CodeChunk] = []

        if self.persist_path:
            self.persist_path.mkdir(parents=True, exist_ok=True)
            self.client = chromadb.PersistentClient(path=str(self.persist_path))
        else:
            self.client = chromadb.Client()

        # 显式指定余弦空间: cosine
        self.collection = self.client.get_or_create_collection(
            name=self.collection_name,
            metadata={"hnsw:space": "cosine"},
        )


    def build_from_directory(
            self,
            root_dir: str | Path,
            extensions: Tuple[str,...] = (".py",),
            clear_existing: bool = True,
    ) -> int:
        """
        递归扫描目标目录，利用 ASTChunker 提取语义切块并写入向量库。
        返回总构建切块数。
        """
        path = Path(root_dir)
        ignore_dirs = {
            ".git",
            ".idea",
            "__pycache__",
            ".venv",
            "venv",
            ".mypy_cache",
            ".pytest_cache",
            ".workbuddy",
        }
        self.chunks.clear()

        for p in path.rglob("*"):
            if any(part in ignore_dirs for part in p.parts):
                continue
            if p.is_file() and p.suffix.lower() in extensions:
                self.chunks.extend(self.chunker.chunk_file(p))

        if clear_existing:
            try:
                self.client.delete_collection(name=self.collection_name)
            except Exception:
                pass

            self.collection = self.client.create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )

        if not self.chunks:
            return 0

        documents:List[str] = [c.content for c in self.chunks]
        ids: List[str] = [
            f"chunk_{idx}_{abs(hash(c.file_path))}_{c.start_line}_{c.end_line}"
            for idx, c in enumerate(self.chunks)
        ]
        metadatas: List[Metadata] = [
            {
                "file_path": c.file_path,
                "start_line": c.start_line,
                "end_line": c.end_line,
                "symbol_name": c.symbol_name,
            }
            for c in self.chunks
        ]

        # 分批写入向量库，防御大批量调用超限
        batch_size = 100
        for i in range(0, len(self.chunks), batch_size):
            self.collection.add(
                documents=documents[i: i + batch_size],
                ids=ids[i: i + batch_size],
                metadatas=metadatas[i: i + batch_size],
            )
        return len(self.chunks)

    def search(self,query:str,top_k: int = 3)->List[Tuple[CodeChunk,float]]:
        """
              根据自然语言查询语义检索最相关的代码块。
              返回按余弦相似度降序排列的 (chunk, similarity_score) 元组列表。
        """
        if not query.strip() or self.collection.count() == 0:
            return []

        n_results = min(top_k, self.collection.count())
        res = self.collection.query(query_texts=[query], n_results=n_results)

        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        dists = (res.get("distances") or [[]])[0]

        results: List[Tuple[CodeChunk, float]] = []
        for doc, meta, dist in zip(docs, metas, dists):
            meta_dict = meta if isinstance(meta,dict) else {}
            # 余弦空间下: 相似度 = 1.0 - 距离
            similarity = max(0.0, 1.0 - float(dist))

            chunk = CodeChunk(
                file_path=str(meta_dict.get("file_path", "")),
                start_line=int(meta_dict.get("start_line", 1)),
                end_line=int(meta_dict.get("end_line", 1)),
                content=str(doc or ""),
                tokens=tokenize_code(str(doc or "")),
                symbol_name=str(meta_dict.get("symbol_name", "")),
            )

            results.append((chunk, similarity))
        return results


VectorIndex = VectorCodeIndex