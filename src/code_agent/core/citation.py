from dataclasses import  dataclass
from enum import Enum
from pathlib import Path
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




#定义状态枚举
class CitationStatus(str,Enum):
    """引用校验的判决状态"""
    VALID = "VALID" # 物理文件存在且行号完全合法
    FILE_NOT_FOUND = "FILE_NOT_FOUND"   #文件不存在
    LINE_OUT_OF_BOUNDS = "LINE_OUT_OF_BOUNDS" # 行号超出范围
    INVALID_RANGE = "INVALID_RANGE" #行号逻辑异常（如 start > end）

@dataclass
class CitationValidationResult:
    citation:Citation #原始引用对象
    status:CitationStatus #判决状态
    total_lines:int|None = None #物理文件实际总行数
    error_message:str="" #详细的失败诊断信息

    @property
    def is_valid(self) -> bool:
        """快捷布尔判断：是否通过校验"""
        return self.status == CitationStatus.VALID

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


def _resolve_file_path(file_path:str,base_dir:Path | str | None = None) ->Path:
    """辅助函数：根据base_dir或工程根目录解析文件真实路径"""
    path = Path(file_path)

    # 1. 如果已经是绝对路径，直接返回
    if path.is_absolute():
        return path

    # 2. 如果显式指定了 base_dir，直接以此为基准拼接
    if base_dir is not None:
        return Path(base_dir).joinpath(path).resolve()

    # 3. 未指定 base_dir 时的防御：优先当前工作目录
    candidate = (Path.cwd() / path).resolve()
    if candidate.exists():
        return candidate

    # 4. 回退机制：如果运行目录发生偏移（如在 tests 目录下运行），自动向上寻找仓库根目录
    repo_root = Path(__file__).resolve().parents[3]
    repo_candidate = (repo_root / path).resolve()
    if repo_candidate.exists():
        return repo_candidate
    return candidate


def validate_citation(citation:Citation,
                      base_dir:Path|str|None=None
                      )->CitationValidationResult:
    """
       校验单个引用在物理磁盘上的真实性与行号边界：
       1. 检查物理文件是否存在；
       2. 计算文件物理总行数；
       3. 校验 start_line 与 end_line 是否落在 [1, total_lines] 内。
    """
    #1.寻址定位真实文件
    resolved_path = _resolve_file_path(citation.file_path,base_dir=base_dir)
    #2.检查文件物理存在性
    if not resolved_path.is_file():
        return CitationValidationResult(
            citation=citation,
            status=CitationStatus.FILE_NOT_FOUND,
            error_message=f"物理文件不存在: {citation.file_path}"
        )
    #3.读取物理文件并计算真实总行数（带编码容错）
    try:
        content = resolved_path.read_text(encoding="utf-8",errors="replace")
    except Exception as e:
        return CitationValidationResult(
            citation=citation,
            status=CitationStatus.FILE_NOT_FOUND,
            error_message=f"读取文件失败: {e}"
        )

    lines = content.splitlines()
    total_lines = len(lines)

    # 4. 行号边界防御校验
    # 防御 A：行号小于 1（代码行号从 1 开始算）
    if citation.start_line < 1:
        return CitationValidationResult(
            citation=citation,
            status=CitationStatus.LINE_OUT_OF_BOUNDS,
            total_lines=total_lines,
            error_message=f"起始行号必须大于等于 1，实际为 {citation.start_line}"
        )
    # 防御 B：起始行号大于结束行号
    if citation.start_line > citation.end_line:
        return CitationValidationResult(
            citation=citation,
            status=CitationStatus.INVALID_RANGE,
            total_lines=total_lines,
            error_message=f"起始行号({citation.start_line})大于结束行号({citation.end_line})"
        )
    #防御 C：行号越过文件尾部（大模型最常见的计算行号幻觉）
    if citation.end_line > total_lines:
        return CitationValidationResult(
            citation=citation,
            status=CitationStatus.LINE_OUT_OF_BOUNDS,
            total_lines=total_lines,
            error_message=f"引用行号超出文件总行数 (文件共 {total_lines} 行，引用到第 {citation.end_line} 行)"
        )
        # 5. 校验全部通过
    return CitationValidationResult(
        citation=citation,
        status=CitationStatus.VALID,
        total_lines=total_lines
    )

def validate_citations(
    citations: list[Citation],
    base_dir: Path | str | None = None
) -> list[CitationValidationResult]:
    """批量校验一组引用"""
    return [validate_citation(c, base_dir=base_dir) for c in citations]