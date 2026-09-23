from dataclasses import  dataclass
from pathlib import  Path
import re
from turtledemo.penrose import start

from rank_bm25 import BM25Okapi

@dataclass
class CodeChunk:
    file_path:str
    start_line:int
    end_line:int
    content:str
    tokens:list[str]


def tokenize_code(text:str) ->list[str]:
    """
    将代码文本拆分为小写单词列表，支持驼峰和下划线拆分
    """
    spaced_camel = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    words = re.findall(r'[a-zA-Z0-9]+', spaced_camel)
    tokens = [w.lower() for w in words if len(w) > 1]  # 过滤单字符无意义符号
    return tokens

def chunk_file(file_path:Path,chunk_size:int = 40,overlap: int = 10)->list[CodeChunk]:
    """
    读取单个文件并按滑动窗口行数切分
    """
    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []

    lines = content.splitlines();
    total_lines = len(lines)
    chunks = []

    start = 0
    step = chunk_size - overlap
    while start < total_lines:
        end = min(start + chunk_size, total_lines)
        chunk_lines = lines[start:end]
        chunk_text = "\n".join(chunk_lines)
        tokens = tokenize_code(chunk_text)

        if tokens:
            chunks.append(CodeChunk(
                file_path=str(file_path),
                start_line=start + 1,
                end_line=end,
                content=chunk_text,
                tokens=tokens
            ))

        if end >= total_lines:
            break
        start += step
    return chunks

class BM25CodeIndex:
    def __init__(self):
        self.chunks : list[CodeChunk] = []
        self.bm25 : BM25Okapi | None = None

    def build_from_directory(self, root_dir: str | Path,extensions: tuple = (".py", ".java")) -> int:
        """
         递归扫描目录并构建倒排索引，返回总切块数
        """
        path = Path(root_dir)
        ignore_dirs = {".git", ".idea", "__pycache__", ".venv", "venv", ".mypy_cache"}
        self.chunks.clear()

        for p in path.rglob("*"):
            if any(part in ignore_dirs for part in p.parts):
                continue
            if p.is_file() and p.suffix.lower() in extensions:
                self.chunks.extend(chunk_file(p))

        if self.chunks:
            corpus = [c.tokens for c in self.chunks]
            self.bm25 = BM25Okapi(corpus)

        return len(self.chunks)

    def search(self, query: str, top_k: int = 3) -> list[tuple[CodeChunk, float]]:
        """根据查询词检索最相关的代码块"""
        if not self.bm25 or not self.chunks:
            return []

        query_tokens = tokenize_code(query)
        if not query_tokens:
            return []

        scores = self.bm25.get_scores(query_tokens)

        # 挑选得分最高的前 top_k
        ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]

        # 过滤掉得分为 0 的完全不相关项
        return [(self.chunks[idx], score) for idx, score in ranked if score > 0]


if __name__ == "__main__":
    # bm25_index.py (当前文件)
    # .parent 是 index/
    # .parent.parent 是 code_agent/
    # .parent.parent.parent 是 src/
    src_dir = Path(__file__).resolve().parent.parent.parent

    print(f"📁 正在索引目录: {src_dir}")

    indexer = BM25CodeIndex()
    # 扫描真正的 src 目录
    total_chunks = indexer.build_from_directory(src_dir)
    print(f"✅ 成功扫描构建索引，共生成 {total_chunks} 个代码切块！\n")

    # 模拟搜索一个我们前天写的函数名
    search_query = "stream ask"
    print(f"🔍 正在检索关键词: '{search_query}' ...\n")

    results = indexer.search(search_query, top_k=2)
    for rank, (chunk, score) in enumerate(results, 1):
        print(f"【TOP {rank}】得分: {score:.2f} | 文件: {chunk.file_path} (第 {chunk.start_line}~{chunk.end_line} 行)")
        print("代码片段预览:")
        print("-" * 50)
        preview = "\n".join(chunk.content.splitlines()[:6])
        print(preview)
        print("-" * 50 + "\n")