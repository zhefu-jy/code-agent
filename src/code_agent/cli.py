import sys
from pathlib import Path

# 确保在任意工作目录下直接执行此脚本都能正确找到包
src_dir = str(Path(__file__).resolve().parent.parent)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from collections import Counter

from code_agent.core.schemas import ReviewReport, Severity
from code_agent.core.model import review_code
from code_agent.core.context import to_repo_relative

# 仓库根：cli.py → code_agent → src → agent-project
REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_REVIEW_LINES = 800

from code_agent.index.bm25_index import BM25CodeIndex
from code_agent.core.model import stream_ask
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage


_SEVERITY_TAG: dict[Severity, str] = {
    Severity.FATAL: "🛑 FATAL  ",
    Severity.WARNING: "⚠️  WARNING",
    Severity.INFO: "ℹ️  INFO   ",
}


def _render_review(report: ReviewReport, total_lines: int, display_path: str) -> None:
    print("=" * 62)
    print(f"📋 审查对象: {display_path}（{total_lines} 行）")
    print(f"🏆 综合评分: {report.score}/100  {'█' * (report.score // 5)}")
    print(f"📝 总评: {report.summary}")
    counts = Counter(issue.severity for issue in report.issues)
    print(f"📊 问题分布: FATAL {counts[Severity.FATAL]} / WARNING {counts[Severity.WARNING]} / INFO {counts[Severity.INFO]}")
    print("=" * 62)

    if not report.issues:
        print("✅ 未发现问题。\n")
        return

    for idx, issue in enumerate(report.issues, 1):
        tag = _SEVERITY_TAG.get(issue.severity, "•")
        # 行号物理校验：复用 W2 的边界思想，审查报告的行号同样是幻觉高发区
        line_note = ""
        if not 1 <= issue.line_number <= total_lines:
            line_note = f"  ⚠️ 行号越界(疑幻觉，文件仅 {total_lines} 行)"
        print(f"[{idx}] {tag} | L{issue.line_number}{line_note} | {issue.issue_type}")
        print(f"    问题: {issue.message}")
        print(f"    建议: {issue.suggestion}")
        print("-" * 62)
    print()


def handle_review(raw_arg: str) -> None:
    if not raw_arg:
        print("⚠️ 用法: /review <文件路径>，例如 /review src/code_agent/cli.py\n")
        return

    target = Path(raw_arg)
    if not target.is_absolute():
        target = REPO_ROOT / target

    if not target.is_file():
        print(f"⚠️ 文件不存在: {target}\n")
        return

    try:
        code = target.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"⚠️ 读取失败: {exc}\n")
        return

    total_lines = len(code.splitlines())
    if total_lines > MAX_REVIEW_LINES:
        print(f"⚠️ 该文件 {total_lines} 行，超过单次审查预算 {MAX_REVIEW_LINES} 行，请先切分\n")
        return

    display_path = to_repo_relative(target, base_dir=REPO_ROOT)
    print(f"🔎 正在审查 {display_path}（{total_lines} 行）...\n")
    try:
        report = review_code(code=code, file_path=display_path)
    except Exception as exc:                  # 网络/结构化解析失败都不该炸掉 CLI
        print(f"⚠️ 审查失败: {exc}\n")
        return

    _render_review(report, total_lines, display_path)


def main() -> None:

    indexer = BM25CodeIndex()
    # 自动索引本地 src 目录下的代码
    chunk_count = indexer.build_from_directory(Path(src_dir))
    print("=" * 60)
    print(f"🚀 Code-Agent 已启动！(已索引 {chunk_count} 个本地代码切块)")
    print("  - 普通对话：直接输入问题与模型对话")
    print("  - 检索代码：输入 /search <关键词> (例如: /search stream ask)")
    print("  - 审查文件：输入 /review <文件路径> (例如: /review src/code_agent/cli.py)")
    print("  - 退出程序：输入 exit")
    print("=" * 60 + "\n")

    messages: list[BaseMessage] = [SystemMessage(content="你是一个专业的代码工程助手，回答简洁专业。")]
    while True:
        input_question = input("Question: ")
        if input_question == 'exit':
            break
        if not input_question.strip():
            continue

        # 拦截 /search 命令
        if input_question.startswith("/search"):
            query = input_question.replace("/search", "").strip()
            if not query:
                print("⚠️ 请提供要搜索的关键词，例如: /search review_code\n")
                continue

            results = indexer.search(query, top_k=3)
            if not results:
                print(f"🔍 未在代码库中找到与 '{query}' 相关的代码片段。\n")
            else:
                print(f"\n🔍 找到 {len(results)} 处高度相关的代码片段:")
                for rank, (chunk, score) in enumerate(results, 1):
                    print(
                        f"【TOP {rank}】得分: {score:.2f} | 文件: {chunk.file_path} (第 {chunk.start_line}~{chunk.end_line} 行)")
                    print("-" * 50)
                    print(chunk.content)
                    print("-" * 50 + "\n")
            continue

        if input_question.startswith("/review"):
            handle_review(input_question[len("/review"):].strip())
            continue

        messages.append(HumanMessage(content=input_question))
        full_answer = ""
        print("Agent: ", end="", flush=True)
        for piece in stream_ask(messages):
            full_answer += piece
            print(piece, end="", flush=True)

        messages.append(AIMessage(content=full_answer))
        print("-" * 60)




if __name__ == "__main__":
    main()