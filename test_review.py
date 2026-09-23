import sys
from pathlib import Path

#测试结构化输出
# 确保 src 目录在 Python 模块搜索路径中
src_dir = Path(__file__).resolve().parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from code_agent.core.model import review_code

# 一段存在典型问题（未检查负数折扣、缺少类型注解、命名与异常处理缺失）的代码片段
sample_code = """
def calculate_discount(price, discount):
    # 折扣率如果大于 1 或者小于 0 会有严重问题
    if discount > 1:
        return price
    total = price * (1 - discount)
    return total
"""

print("正在调用 DeepSeek 进行代码结构化审查，请稍候...\n")
report = review_code(code=sample_code, file_path="discount_calculator.py")

print("=" * 60)
print(f"【代码审查报告】综合评分: {report.score} / 100 分")
print(f"【审查总评】: {report.summary}")
print("=" * 60)

print(f"\n共发现 {len(report.issues)} 处问题:\n")
for idx, issue in enumerate(report.issues, 1):
    severity_tag = issue.severity.value.upper()
    print(f"[{idx}] 级别: {severity_tag} | 行号: 第 {issue.line_number} 行 | 类型: {issue.issue_type}")
    print(f"    问题描述: {issue.message}")
    print(f"    修改建议: {issue.suggestion}")
    print("-" * 60)
