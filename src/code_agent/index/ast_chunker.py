import ast
from pathlib import Path
from typing import List

from code_agent.index.bm25_index import CodeChunk,chunk_file,tokenize_code

class ASTChunker:
    """基于Python标准库AST的语法感知代码切块器"""

    def __init__(self,max_class_lines: int = 50)-> None:
        self.max_class_lines = max_class_lines

    def chunk_file(self,file_path: Path | str) -> List[CodeChunk]:
        """
        将单个 Python 文件按函数与类边界解析为独立的语义代码块。
        若非 Python 文件或解析异常，则降级为滑动窗口切块。
        """
        path = Path(file_path)

        #非Python文件降级
        if path.suffix.lower() != ".py":
            return chunk_file(path)

        try:
            content = path.read_text(encoding ="utf-8",errors = "ignore")
            tree = ast.parse(content,filename = str(path))
        except (SyntaxError,UnicodeDecodeError,Exception):
            # 语法错误或读取异常，安全回退到滑动窗口切块
            return chunk_file(path)

        lines = content.splitlines()
        chunks: List[CodeChunk] = []
        path_str = str(path)

        #1.提取模块级头部（扫描至第一个类或函数出现前）
        header_nodes = []

        for n in tree.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                break
            header_nodes.append(n)

        if header_nodes:
            first_line = header_nodes[0].lineno
            last_line = max(n.end_lineno or n.lineno for n in header_nodes)
            header_text = "\n".join(lines[first_line - 1 : last_line])
            tokens = tokenize_code(header_text)
            if tokens:
                chunks.append(
                    CodeChunk(
                        file_path = path_str,
                        start_line = first_line,
                        end_line = last_line,
                        content = header_text,
                        tokens = tokens,
                    )
                )


        for node in tree.body:
            if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)):
                chunks.extend(self._chunk_function(node,lines,path_str))
            elif isinstance(node,(ast.ClassDef)):
                chunks.extend(self._chunk_class(node,lines,path_str))

        #3.极端空文件兜底： 若AST未切除任何块但文件有内容，使用滑动窗口保底
        if not chunks and content.strip():
          return chunk_file(path)

        return chunks

    def _chunk_function(
            self,
            node: ast.FunctionDef | ast.AsyncFunctionDef,
            lines: List[str],
            file_path: str,
            prefix: str = "",
    ) -> List[CodeChunk]:
        """切分独立函数或者类方法"""
        start = node.lineno
        end = node.end_lineno or node.lineno
        func_text = "\n".join(lines[start - 1 : end])
        tokens = tokenize_code(func_text)

        if prefix:
            tokens.extend(tokenize_code(f"{prefix}{node.name}"))

        if not tokens:
            return []
        return [
            CodeChunk(
                file_path=file_path,
                start_line=start,
                end_line=end,
                content=func_text,
                tokens=tokens,
            )
        ]

    def _chunk_class(
            self,
            node: ast.ClassDef,
            lines: List[str],
            file_path: str,
    )->List[CodeChunk]:
        """智能切分类：小类保持整体；大类拆分方法并附带类前缀"""
        start = node.lineno
        end = node.end_lineno or node.lineno
        total_class_lines = end - start + 1

        #小类整体作为一个块
        if total_class_lines < self.max_class_lines:
            class_text = "\n".join(lines[start - 1 : end])
            tokens = tokenize_code(class_text)
            if tokens:
                return [
                    CodeChunk(
                        file_path=file_path,
                        start_line=start,
                        end_line=end,
                        content=class_text,
                        tokens=tokens,
                    )
                ]

        #大类拆分：类头部作为一个块，各个方法独立成块
        chunks: List[CodeChunk] = []

        #提取类头（类签名与 docstring/类属性，截取至第一个方法前）
        methods = [
            n for n in node.body
            if isinstance(n,(ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        if methods:
            first_method_line = methods[0].lineno
            header_end = first_method_line - 1
            if header_end >= start:
                header_text = "\n".join(lines[start - 1 : header_end])
                tokens = tokenize_code(header_text)
                if tokens:
                    chunks.append(
                        CodeChunk(
                            file_path=file_path,
                            start_line=start,
                            end_line=header_end,
                            content=header_text,
                            tokens=tokens,
                        )
                    )
        #遍历类内每个方法进行切块
        for member in node.body:
            if isinstance(member,(ast.FunctionDef, ast.AsyncFunctionDef)):
                chunks.extend(
                    self._chunk_function(
                        member, lines, file_path, prefix=f"{node.name}."
                    )
                )

        return chunks

