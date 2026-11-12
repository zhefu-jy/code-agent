from enum import Enum
from pydantic import  BaseModel,Field

class Severity(str,Enum):
    FATAL = "fatal"
    WARNING = "warning"
    INFO = "info"

class ReviewIssue(BaseModel):
    """
    代码评审后的问题总结
    """
    file_path:str = Field(description="问题所在文件")
    line_number: int = Field(description="哪一行代码有问题（必须是整数）")
    severity: Severity = Field(description="严重级别（强制匹配枚举)")
    issue_type:str = Field(description="问题类型（如：代码规范, 安全风险, 逻辑缺陷）")
    message:str = Field(description="问题描述")
    suggestion:str=Field(description="具体的修改建议或修复示例")

class ReviewReport(BaseModel):
    """
    问题的审查报告
    """
    summary:str = Field(description="对整段代码的总体评价")
    score: int = Field(
        ge=0,
        le=100,
        description="代码整体评分（0 到 100 分）"
    )
    issues:list[ReviewIssue]=Field(description="所有具体问题的列表")

class SystemStatus(str,Enum):
    """系统执行状态：衡量程序与资源运行是否正常"""
    SUCCESS = "SUCCESS"  # 正常闭环（正常获取输出）
    BUDGET_EXHAUSTED = "BUDGET_EXHAUSTED"  # 预算耗尽（Token/调用轮数/时间上限）
    MODEL_ERROR = "MODEL_ERROR"  # 模型层异常（API报错、JSON格式无法解析等）
    TOOL_ERROR = "TOOL_ERROR"  # 工具调用层异常未恢复


class EvidenceValidity(BaseModel):
    """举证真实性：衡量回答引用的真实度与上下文边界"""
    citations_total: int = 0 # 答案中提取到的引用标记总数
    citations_valid: int = 0 # 经物理文件存在性与行号区间校验合法的引用数
    citations_in_context: int = 0 # 引用是否真正命中本次实际读取/喂给模型的切片范围（防凭记忆编造）
    is_valid: bool = False # 物理有效率是否达标（至少存在 1 处有效物理引用，或无依据用例中无伪造引用）
    evidence_supported: bool | None = None # 结论是否真实被证据支撑（None: 待人工复核, True: 已证实, False: 证据不足/伪造）

class AnswerVerdictKind(str, Enum):
    """回答结论状态枚举"""
    CORRECT = "CORRECT"  # 事实正确且证据确凿
    FACTUAL_ERROR = "FACTUAL_ERROR"  # 事实错误（答非所问、核心符号捏造、结论相反）
    UNRESOLVED_REFUSAL = "UNRESOLVED_REFUSAL"  # 诚实拒答/无依据，但任务未解决（合规但未通关）
    EXPECTED_REFUSAL = "EXPECTED_REFUSAL"  # 符合预期的拒答（针对虚构功能或边界探测用例的正确拒答）
    NEEDS_HUMAN_REVIEW = "NEEDS_HUMAN_REVIEW"  # 争议项或复杂推论，需要人工介入标定


class JudgeSource(str,Enum):
    """判卷来源"""
    AUTO_RULE = "AUTO_RULE"  # 自动化规则断言（文件、符号、正则等）
    HUMAN_REVIEW = "HUMAN_REVIEW"  # 人工审查标定
    LLM_JUDGE = "LLM_JUDGE"  # 独立的模型裁判（后续扩展）


class AnswerVerdict(BaseModel):
    """回答结论判定：衡量业务答案的正确性与完成度"""
    verdict: AnswerVerdictKind  # 最终判定结论
    judge_source: JudgeSource = JudgeSource.AUTO_RULE  # 判卷裁决来源
    missing_files: list[str] = Field(default_factory=list)  # 未命中的期望文件
    missing_symbols: list[str] = Field(default_factory=list)  # 未命中的关键符号
    fail_reasons: list[str] = Field(default_factory=list)  # 不达标的具体原因
    human_note: str = ""  # 人工复核备注