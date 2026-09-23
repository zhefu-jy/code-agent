import sys
import time
from pathlib import Path

# 确保能正确导入 src 下的模块
src_dir = Path(__file__).resolve().parent.parent / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from langchain_core.messages import HumanMessage, SystemMessage
from code_agent.core.model import get_chat_model


def generate_code_context(target_lines: int) -> tuple[str, int]:
    """
    生成一段逼真的 Python 工程业务代码，并在最容易被忽略的“正中间（50%位置）”埋入逻辑覆写。
    返回: (生成的完整代码, 关键覆写所在的实际行号)
    """
    lines = [
        "# ================= 系统配置与全局常量 =================",
        "import os",
        "import logging",
        "from typing import Optional, Dict, Any",
        "",
        "# 默认基础重试次数",
        "DEFAULT_RETRY_COUNT = 3",
        "DEFAULT_TIMEOUT_SECONDS = 30",
        "",
        "# ================= 业务工具函数 =================",
    ]

    # 生成前半部分干扰业务函数
    half_lines = (target_lines - 30) // 2
    for i in range(half_lines // 6):
        lines.extend([
            f"def helper_service_action_{i}(user_id: int, payload: Dict[str, Any]) -> bool:",
            f"    '''辅助业务处理逻辑 #{i}'''",
            f"    logging.info(f'Processing action {i} for user {{user_id}}')",
            f"    if not payload or 'token' not in payload:",
            "        return False",
            "    return True",
            ""
        ])

    # 关键“针”：埋在整个代码的正中间（Lost in the Middle 盲区）
    needle_line_num = len(lines) + 3
    lines.extend([
        "# ---------------- 核心运行时补丁（请特别关注） ----------------",
        "def apply_production_patch(env: str, is_high_concurrency: bool) -> int:",
        "    global DEFAULT_RETRY_COUNT",
        "    # 生产高并发场景下，重试次数必须从 3 强制覆写为 7，以避免瞬时抖动造成雪崩",
        "    if env == 'production' and is_high_concurrency:",
        "        DEFAULT_RETRY_COUNT = 7",
        "    return DEFAULT_RETRY_COUNT",
        "# ----------------------------------------------------------------",
        ""
    ])

    # 生成后半部分干扰业务函数
    remaining = target_lines - len(lines)
    for i in range(remaining // 6):
        idx = i + 1000
        lines.extend([
            f"def audit_log_handler_{idx}(event_name: str, status_code: int) -> None:",
            f"    '''审计日志处理逻辑 #{idx}'''",
            f"    timestamp = os.getenv('SERVER_START_TIME', '0')",
            "    if status_code >= 400:",
            f"        logging.warning(f'Event {{event_name}} failed with code {{status_code}} at {{timestamp}}')",
            ""
        ])

    code_content = "\n".join(lines)
    actual_line_count = len(lines)
    return code_content, needle_line_num


def run_experiment(scale_name: str, target_lines: int) -> dict:
    print(f"\n>>> 正在运行实验组: 【{scale_name}】(目标行数: ~{target_lines} 行)...")
    
    # 1. 构造代码
    code, needle_line = generate_code_context(target_lines)
    actual_lines = code.count("\n") + 1
    
    # 2. 构造具有推理要求的 Prompt
    question = (
        "仔细阅读以下 Python 源代码，回答两个问题：\n"
        "1. 在生产高并发环境下（env='production', is_high_concurrency=True），DEFAULT_RETRY_COUNT 最终生效的值是多少？\n"
        "2. 这个值最初是在哪里定义的？后来在哪个函数内部被覆写成了多少？大约在第几行？\n\n"
        f"```python\n{code}\n```"
    )

    # 3. 调用模型并严格统计
    model = get_chat_model(temperature=0.0)
    messages = [
        SystemMessage(content="你是一名严格的代码分析专家，只能根据提供的源码内容进行事实回答。"),
        HumanMessage(content=question)
    ]

    start_time = time.perf_counter()
    resp = model.invoke(messages)
    elapsed = time.perf_counter() - start_time

    # 提取实际 Token 消耗
    usage = getattr(resp, "usage_metadata", {}) or {}
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    total_tokens = usage.get("total_tokens", 0)
    
    answer_text = str(resp.content)
    
    # 快速检验：是否找出了被修改后的正确数值 7
    is_correct = "7" in answer_text

    print(f"    - 实际代码行数: {actual_lines} 行 (埋点在第 {needle_line} 行)")
    print(f"    - 耗时: {elapsed:.2f} 秒")
    print(f"    - 输入 Token: {input_tokens} | 输出 Token: {output_tokens} | 总 Token: {total_tokens}")
    print(f"    - 是否正确识别覆写后的值 7: {'✅ 正确' if is_correct else '❌ 失败/未识别'}")
    print(f"    - 回答摘要:\n{answer_text[:200]}...")

    return {
        "scale": scale_name,
        "lines": actual_lines,
        "needle_line": needle_line,
        "elapsed": elapsed,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "is_correct": is_correct,
        "answer": answer_text
    }


def main():
    print("=" * 70)
    print("      Token 边界与长上下文代价实验 (Needle In A Haystack & Latency)")
    print("=" * 70)

    # 分别测试：小片段(基准) vs 中规模(~800行) vs 大规模(~2500行)
    test_cases = [
        ("基准组 (50行)", 50),
        ("中规模组 (800行)", 800),
        ("大规模组 (2500行)", 2500),
    ]

    results = []
    for name, lines in test_cases:
        res = run_experiment(name, lines)
        results.append(res)
        time.sleep(1)  # 稍微停顿一下避免触发速率限制

    print("\n" + "=" * 70)
    print("                   📊 最终实验对比报告")
    print("=" * 70)
    print(f"{'测试组':<16} | {'代码行数':<8} | {'输入 Token':<10} | {'耗时 (秒)':<10} | {'判定结果'}")
    print("-" * 70)
    for r in results:
        status = "✅ 正确" if r["is_correct"] else "❌ 错误"
        print(f"{r['scale']:<16} | {r['lines']:<10} | {r['input_tokens']:<12} | {r['elapsed']:<12.2f} | {status}")
    print("=" * 70)


if __name__ == "__main__":
    main()
