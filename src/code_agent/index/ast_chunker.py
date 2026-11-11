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
        except Exception:
            # 语法错误或读取异常，安全回退到滑动窗口切块
            return chunk_file(path)

        lines = content.splitlines()
        chunks: List[CodeChunk] = []
        path_str = str(path)

        # 遍历顶层节点：对连续的非类/非函数节点（导入、全局常量、模块赋值等）聚合成顶层块，
        # 并提取被赋值的符号名（如 _CITATION_PATTERN, DEFAULT_IGNORE_DIRS），防止全局常量成为检索盲区
        current_misc: List[ast.stmt] = []

        def flush_misc() -> None:
            if not current_misc:
                return
            first_line = current_misc[0].lineno
            last_line = max(n.end_lineno or n.lineno for n in current_misc)
            misc_text = "\n".join(lines[first_line - 1 : last_line])
            tokens = tokenize_code(misc_text)
            if tokens:
                assigned: List[str] = []
                for n in current_misc:
                    if isinstance(n, ast.Assign):
                        for t in n.targets:
                            if isinstance(t, ast.Name):
                                assigned.append(t.id)
                    elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
                        assigned.append(n.target.id)
                sym = assigned[0] if assigned else ""
                chunks.append(
                    CodeChunk(
                        file_path=path_str,
                        start_line=first_line,
                        end_line=last_line,
                        content=misc_text,
                        tokens=tokens,
                        symbol_name=sym,
                        symbol_names=assigned,
                    )
                )
            current_misc.clear()

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                flush_misc()
                chunks.extend(self._chunk_function(node, lines, path_str))
            elif isinstance(node, ast.ClassDef):
                flush_misc()
                chunks.extend(self._chunk_class(node, lines, path_str))
            else:
                current_misc.append(node)
        flush_misc()

        # 3. 极端空文件兜底：若 AST 未切出任何块但文件有内容，使用滑动窗口保底
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
        full_name = f"{prefix}{node.name}"

        if prefix:
            tokens.extend(tokenize_code(full_name))

        if not tokens:
            return []
        return [
            CodeChunk(
                file_path=file_path,
                start_line=start,
                end_line=end,
                content=func_text,
                tokens=tokens,
                symbol_name=full_name,
                symbol_names=[full_name],
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
                        symbol_name=node.name,
                        symbol_names=[node.name],
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
                            symbol_name=f"{node.name} (header)",
                            symbol_names=[],
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

