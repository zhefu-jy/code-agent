from pathlib import  Path
from code_agent.index.bm25_index import  CodeChunk

def to_repo_relative(file_path:str | Path,base_dir: Path | str | None = None) -> str:
    """把物理路径转成相对 base_dir 的 POSIX 风格路径（消除 Windows 盘符冒号）。"""
    raw = Path(file_path)
    if base_dir is not None:
        try:
            return raw.resolve().relative_to(Path(base_dir).resolve()).as_posix()
        except ValueError:
            pass  # 不在仓库内（比如临时文件），退回原样
    return raw.as_posix()

def build_snippets_text(
        hits: list[tuple[CodeChunk, float]],
        base_dir: Path | str | None = None,
) -> str:
    """把 BM25 命中切片拼成带相对路径与行号的上下文标尺。"""
    parts: list[str] = []
    for index,(chunk,_score) in  enumerate(hits, 1):
        clean_path = to_repo_relative(chunk.file_path, base_dir=base_dir)
        parts.append(
            f"\n--- 片段 {index}: {clean_path} "
            f"(第 {chunk.start_line}~{chunk.end_line} 行) ---\n"
            f"{chunk.content}\n"
        )
    return "".join(parts)
