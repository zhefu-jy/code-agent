from dataclasses import  dataclass
import re

@dataclass
class Citation:
    """
    定义模型回答中提取出代码片段的物理位置引用
    """
    file_path:str
    start_line:int
    end_line:int
    raw_text:str

CITATION_PROMPT_REQUIREMENT = """
【出处与依据引用铁律】
1. 每一个事实、结论或分析，都必须严格在句末标注代码出处，格式为: [文件路径:起始行-结束行]（例如: [src/code_agent/core/model.py:50-64]）。若只有单行则为: [文件路径:行号]（例如: [src/code_agent/core/model.py:50]）。
2. 出处中的文件路径和行号必须完全以提供的参考代码片段为准，严禁凭空记忆或编造行号！
3. 若根据所给的代码片段无法得出答案，或者库中根本没有相关实现，必须显式回答“无依据”，严禁推测或臆造任何虚构代码和行号。
""".strip()

# 正则分解说明：
# 1. \[`?                  匹配开头的 [ 以及可能存在的反引号 `
# 2. ([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9_\-]+) 捕获组1：文件路径（必须含有点号后缀，允许字母数字下划线及斜杠）
# 3. `?:\s*               匹配可能闭合的反引号 `，冒号 : 以及可能的空格
# 4. (\d+)                捕获组2：起始行号（纯数字）
# 5. (?:\s*-\s*(\d+))?    可选的非捕获组，内部捕获组3：结束行号（纯数字）
# 6. \]                   匹配结尾的 ]
_CITATION_PATTERN = re.compile(
    r"`?\[`?([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9_\-]+)`?:\s*(\d+)(?:\s*-\s*(\d+))?`?\]`?"
)

def extract_citations(text:str)-> list[Citation]:
    """
    从文本中提取所有引用，并做路径标准化与行号容错
    """
    citations: list[Citation] = []

    #使用finditer编辑文本中所有匹配项
    for match in _CITATION_PATTERN.finditer(text):
        raw_text = match.group(0)
        raw_path = match.group(1).strip()
        start_str = match.group(2)
        end_str = match.group(3)

        # 1. 路径标准化：把 Windows 的反斜杠 \ 统一替换为正斜杠 /，并去除多余反引号
        clean_path = raw_path.replace("\\", "/").strip("`' ")

        # 2. 单行容错：若未捕获到结束行，则 end_line = start_line
        start_line = int(start_str)
        end_line = int(end_str) if end_str is not None else start_line

        if start_line > end_line:
            start_line, end_line = end_line, start_line

        citations.append(
            Citation(
            file_path=clean_path,
            start_line=start_line,
            end_line=end_line,
            raw_text=raw_text
        ))

    return citations



_NO_EVIDENCE_PATTERN = re.compile(
    r"(无依据|未找到.*依据|没有找到.*依据|不存在.*依据|无法从.*代码中找到|代码库中未实现|未在代码中找到)",
    re.IGNORECASE,
)


def has_no_evidence(text: str) -> bool:
  """检测回答是否明确声明了无依据（拒答判定）"""
  return bool(_NO_EVIDENCE_PATTERN.search(text))
