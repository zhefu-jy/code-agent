from pathlib import Path
from typing import List, Tuple, TypedDict  # <-- 引入 TypedDict

from markdown_it import MarkdownIt
from code_agent.index.bm25_index import CodeChunk, tokenize_code


class HeadingInfo(TypedDict):
    level: int
    title: str
    start_0: int
    h_end_0: int

class MarkdownDocChunker:
    """基于 markdown-it-py 的技术文档层级感知分块器"""

    def __init__(self) -> None:
        self.md = MarkdownIt()

    def chunk_file(self,file_path: Path | str) -> List[CodeChunk]:
        """
        读取 Markdown 文档并按标题层级（H1/H2/H3）切分为独立的语义块。
        自动提取面包屑标题路径并映射精准的物理行号。
        """
        path = Path(file_path)

        #1. 非markdown文件或者不存在，直接返回空
        if not path.is_file() or path.suffix.lower() not in [".md",".markdown"]:
            return []

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return []

        if not content.strip():
            return []

        lines = content.splitlines()
        total_lines = len(lines)
        tokens = self.md.parse(content)

        # 2. 提取所有标题的层级、标题文本与起止行号 (0-indexed)
        headings: List[HeadingInfo] = []
        for idx,t in enumerate(tokens):
            if t.type == "heading_open" and t.map:
                level = int(t.tag[1:])
                inline = tokens[idx + 1]
                title = inline.content.strip()
                headings.append({
                    "level": level,
                    "title": title,
                    "start_0": t.map[0],
                    "h_end_0": t.map[1],
                })

        # 3. 边界处理：若文档完全无标题，作为单个大块整体返回
        path_str = str(path)
        if not headings:
            chunk_tokens = tokenize_code(content)
            title_path = path.stem
            chunk_tokens.extend(tokenize_code(title_path))
            return [
                CodeChunk(
                    file_path=path_str,
                    start_line=1,
                    end_line=total_lines,
                    content=content,
                    tokens=chunk_tokens,
                    symbol_name="",
                    source_type="doc",
                    title_path=title_path,
                )
            ]

        chunks: List[CodeChunk] = []
        stack: List[Tuple[int,str]] = []

        # 4. 提取首个标题之前的前置引言文本（Prelude）
        first_h_start = headings[0]["start_0"]
        if first_h_start > 0:
            prelude_text = "\n".join(lines[:first_h_start])
            if prelude_text.strip():
                title_path = f"{path.stem} > 引言"
                chunk_tokens = tokenize_code(prelude_text)
                chunk_tokens.extend(tokenize_code(title_path))
                chunks.append(
                    CodeChunk(
                        file_path=path_str,
                        start_line=1,
                        end_line=first_h_start,
                        content=prelude_text,
                        tokens=chunk_tokens,
                        symbol_name="",
                        source_type="doc",
                        title_path=title_path,
                    )
                )

        # 5. 逐个切分各级标题段落并维护层级栈
        for i, h in enumerate(headings):
            level = h["level"]
            title = h["title"]
            start_0 = h["start_0"]
            h_end_0 = h["h_end_0"]
            next_start_0 = headings[i + 1]["start_0"] if i + 1 < len(headings) else total_lines
            # 维护标题层级栈（弹出所有同级及更深级的旧标题）
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            title_path = " > ".join(t for _, t in stack)
            # 判断标题与下个标题之间是否有实质正文
            body_text = "\n".join(lines[h_end_0:next_start_0]).strip()
            is_parent_container = (
                    i + 1 < len(headings)
                    and headings[i + 1]["level"] > level
                    and not body_text
            )
            # 纯父级容器标题（紧跟子标题且无任何正文）不产生切块，仅作为下级标题的面包屑来源
            if not is_parent_container:
                chunk_text = "\n".join(lines[start_0:next_start_0])
                if chunk_text.strip():
                    chunk_tokens = tokenize_code(chunk_text)
                    chunk_tokens.extend(tokenize_code(title_path))
                    chunks.append(
                        CodeChunk(
                            file_path=path_str,
                            start_line=start_0 + 1,
                            end_line=next_start_0,
                            content=chunk_text,
                            tokens=chunk_tokens,
                            symbol_name="",
                            source_type="doc",
                            title_path=title_path,
                        )
                    )
        return chunks

def chunk_markdown_file(file_path: Path | str) -> List[CodeChunk]:
    """便捷工具函数：切分单个 Markdown 文件"""
    return MarkdownDocChunker().chunk_file(file_path)


