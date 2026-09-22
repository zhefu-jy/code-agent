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