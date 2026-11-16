import argparse
import sys
from pathlib import Path
from collections import Counter
# 确保在任意工作目录下直接执行此脚本都能正确找到包
src_dir = str(Path(__file__).resolve().parent.parent)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
from code_agent.core.schemas import ReviewReport, Severity, SystemStatus
from code_agent.core.model import review_code
from code_agent.core.context import to_repo_relative
from code_agent.core.query_engine import (
    QueryConfig,
    build_retriever_for_project,
    execute_query,
)
from typing import Any
from code_agent.index.hybrid_retriever import HybridSearchResult
MAX_REVIEW_LINES = 800
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


def handle_review(raw_arg: str,repo_root: Path) -> None:
    if not raw_arg:
        print("⚠️ 用法: /review <文件路径>，例如 /review src/code_agent/cli.py\n")
        return

    target = Path(raw_arg)
    if not target.is_absolute():
        target = repo_root  / target

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

    display_path = to_repo_relative(target, base_dir=repo_root)
    print(f"🔎 正在审查 {display_path}（{total_lines} 行）...\n")
    try:
        report = review_code(code=code, file_path=display_path)
    except Exception as exc:                  # 网络/结构化解析失败都不该炸掉 CLI
        print(f"⚠️ 审查失败: {exc}\n")
        return

    _render_review(report, total_lines, display_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Code-Agent 智能代码与规范交互终端")
    parser.add_argument("--repo", "-r", type=str, default=".", help="目标工程根目录 (默认为当前目录)")
    parser.add_argument("--retriever", choices=["hybrid", "bm25", "vector"], default="hybrid",
                        help="检索策略 (默认 hybrid)")
    args = parser.parse_args()
    repo_root = Path(args.repo).resolve()
    if not repo_root.exists():
        print(f"❌ 指定的工程根目录不存在: {repo_root}")
        return

    print("=" * 64)
    print(f"📦 正在为目标工程构建检索索引: {repo_root} ...")
    retriever = build_retriever_for_project(repo_root, retriever_type=args.retriever)
    code_count = sum(1 for c in retriever.chunks if c.source_type == "code")
    doc_count = sum(1 for c in retriever.chunks if c.source_type == "doc")

    print(
        f"🚀 Code-Agent 已启动！(目标工程: {repo_root.name} ｜ 共 {len(retriever.chunks)} 个切块 ｜ "
        f"💻 源码: {code_count} ｜ 📄 文档: {doc_count})"
    )
    print("  - 普通提问：直接输入问题（基于目标工程自动检索并给出带行号依据的回答）")
    print("  - 检索代码：输入 /search <关键词/语义意图> (例如: /search stream_ask)")
    print("  - 审查文件：输入 /review <文件路径> (例如: /review src/code_agent/cli.py)")
    print("  - 退出程序：输入 exit")
    print("=" * 64 + "\n")

    while True:
        try:
            input_question = input("Question: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n👋 已退出。")
            break
        if input_question.lower() == 'exit':
            print("👋 再见！")
            break
        if not input_question:
            continue
        # 1. 拦截 /search 命令
        if input_question.startswith("/search"):
            query = input_question.replace("/search", "").strip()
            if not query:
                print("⚠️ 请提供要搜索的内容，例如: /search review_code 或 /search 校验引用合法性\n")
                continue
            results: list[Any] = list(retriever.search(query, top_k=3))
            if not results:
                print(f"🔍 未在代码库中找到与 '{query}' 相关的代码片段。\n")
            else:
                print(f"\n🔍 找到 {len(results)} 处高度相关的代码片段:")
                for rank, hit in enumerate(results, 1):
                    tag_map = {
                        "BOTH": "🔀 [双路共同命中]",
                        "VECTOR": "🧠 [语义向量命中]",
                        "BM25": "🔤 [词法关键词命中]",
                        "CONTEXT": "📎 [依赖补全引用]",
                    }
                    if isinstance(hit, HybridSearchResult):
                        hit_tag = tag_map.get(hit.source, hit.source)
                        chunk = hit.chunk
                        score_info = f"融合分: {hit.score:.4f} (BM25: {hit.bm25_norm_score:.2f} | 向量: {hit.vec_norm_score:.2f})"
                    else:
                        chunk, score = hit
                        hit_tag = f"[{args.retriever.upper()} 命中]"
                        score_info = f"得分: {score:.2f}"
                    display_file = to_repo_relative(Path(chunk.file_path), base_dir=repo_root)
                    if chunk.source_type == "code":
                        source_tag = "💻 [源代码]"
                        meta_info = f"符号: {chunk.symbol_name}" if chunk.symbol_name else "代码片段"
                    else:
                        source_tag = "📄 [规范文档]"
                        meta_info = f"章节: {chunk.title_path}" if chunk.title_path else "文档片段"
                    print(f"【TOP {rank}】{source_tag} {hit_tag} {score_info}")
                    print(f"        🏷️  {meta_info}")
                    print(f"        📁 {display_file} (第 {chunk.start_line}~{chunk.end_line} 行)")
                    print("-" * 64)
                    print(chunk.content)
                    print("-" * 64 + "\n")
            continue
        # 2. 拦截 /review 命令
        if input_question.startswith("/review"):
            handle_review(input_question[len("/review"):].strip(), repo_root=repo_root)
            continue
        # 3. 普通提问：直接交由统一业务内核 execute_query 处理
        print("Agent 正在分析并检索代码 ...\n")
        res = execute_query(
            project_root=repo_root,
            query=input_question,
            indexer=retriever,
            config=QueryConfig(top_k=3),
        )
        print("🤖 【回答内容】:")
        print(res.answer)
        print("-" * 64)
        # 终端呈现三维度健康体检卡
        status_icon = "✅" if res.system_status == SystemStatus.SUCCESS else "🛑"
        coverage_tag = "完整覆盖" if res.evidence_validity.context_covered else "⚠️ 部分未覆盖"
        if res.evidence_validity.context_covered is None:
            coverage_tag = "不适用(无引用)"
        print(
            f"📋 [三维体检] {status_icon} 系统: {res.system_status.value} ｜ "
            f"引用: {res.evidence_validity.citations_valid}/{res.evidence_validity.citations_total} 有效 ｜ "
            f"切片覆盖: {res.evidence_validity.citations_in_context} 处 ({coverage_tag}) ｜ "
            f"耗时: {res.latency:.2f}s"
        )
        if res.evidence_validity.context_covered is False:
            print("   ⚠️ 提示: 回答中的部分引用行号不在检索给出的切片范围内，可能存在凭记忆推测。")
        print("=" * 64 + "\n")



if __name__ == "__main__":
    main()
