import argparse
import sys
from pathlib import Path
from collections import Counter
from code_agent.analyzer.symbol_index import SymbolIndex, build_symbol_index
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
from collections.abc import Callable
from dataclasses import dataclass
import shlex

from code_agent.orchestration import InvestigationBudget
from code_agent.spec import (
    RuleCandidateMap,
    SpecAuditReport,
    audit_candidate_map,
    confirm_all_drafts,
    format_audit_report_markdown,
    format_candidate_map_report,
    interactive_confirm_rules,
    interactive_review_candidates,
    match_rules_to_candidates,
    parse_spec_rules,
)
from code_agent.orchestration import format_investigation_report, investigate
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


def handle_structure(symbol_index: SymbolIndex) -> None:
    """确定性输出工程目录拓扑、模块清单、构建耗时与 unresolved 汇总。"""
    st = symbol_index.structure
    sym_u, imp_u = symbol_index.all_unresolved()
    roots_text = ", ".join(r.as_posix() for r in st.source_roots) or "(无)"
    print("=" * 64)
    print(f"🏗️  [工程结构透视] {st.project_root.name}")
    print(f"   • 布局类型: {st.layout} ｜ 源码根: {roots_text}")
    print(
        f"   • 文件统计: 💻 源码 {len(st.source_files)} ｜ "
        f"🧪 测试 {len(st.test_files)} ｜ 📄 文档 {len(st.doc_files)} ｜ "
        f"⚙️ 配置 {len(st.config_files)}"
    )
    print(
        f"   • 符号索引: {len(symbol_index.dependency_graph.modules)} 个模块 ｜ "
        f"{len(symbol_index.symbol_table.symbols)} 个符号 ｜ "
        f"{len(symbol_index.dependency_graph.edges)} 条导入边 ｜ "
        f"耗时 {symbol_index.build_duration_ms:.2f} ms"
    )
    print(f"   • 未解析边界: AST 符号 {len(sym_u)} 项 ｜ Import 依赖 {len(imp_u)} 项")
    if st.warnings:
        print("   ⚠️ 扫描告警:")
        for w in st.warnings:
            print(f"      - {w}")
    print("-" * 64)
    print("📦 内部模块清单:")
    for mod in symbol_index.dependency_graph.modules:
        mod_sym_count = len(symbol_index.symbols_in_module(mod))
        print(f"   - {mod} ({mod_sym_count} 个符号)")
    print("=" * 64 + "\n")


def handle_symbols(raw_arg: str, symbol_index: SymbolIndex) -> None:
    """确定性查询符号定义位置、物理行号与签名。"""
    query = raw_arg.strip()
    if not query:
        print("⚠️ 用法: /symbols <符号名或FQN>，例如 /symbols BuildEngine 或 /symbols stream_ask\n")
        return
    res = symbol_index.lookup_symbol(query)
    if res.match_mode == "none":
        print(f"🔍 未找到匹配 '{query}' 的静态符号。\n")
        return
    mode_label = {
        "exact_fqn": "精确全限定名命中",
        "exact_name": "精确短名命中",
        "substring": "子串模糊命中",
    }.get(res.match_mode, res.match_mode)
    print("=" * 64)
    print(f"🔎 [符号查询] '{res.query}'（{mode_label}，共 {len(res.matches)} 项）")
    print("=" * 64)
    for idx, sym in enumerate(res.matches, 1):
        print(f"[{idx}] [{sym.kind}] {sym.fqn}")
        print(f"    📁 {sym.file_path.as_posix()}:{sym.start_line}-{sym.end_line}")
        if sym.signature:
            print(f"    ✍️  签名: {sym.signature}")
        if sym.bases:
            print(f"    🧬 基类: {', '.join(sym.bases)}")
    print("-" * 64 + "\n")

def handle_deps(raw_arg: str, symbol_index: SymbolIndex) -> None:
    """确定性透视指定模块的出入向依赖关系及未解析导入。"""
    module_name = raw_arg.strip()
    if not module_name:
        print("⚠️ 用法: /deps <模块全名>，例如 /deps code_agent.cli 或 /deps minisite.cli\n")
        return
    summary = symbol_index.inspect_module_deps(module_name)
    if not summary.exists:
        print(f"⚠️ 未在工程内部模块中找到 '{module_name}'。")
        sample = ", ".join(symbol_index.dependency_graph.modules[:8])
        if sample:
            print(f"   💡 可用模块示例: {sample}")
        print()
        return
    print("=" * 64)
    print(f"🕸️  [模块依赖透视] {summary.module_name}")
    print("=" * 64)
    print("➡️  内部依赖 (Internal):")
    if summary.internal_dependencies:
        for dep in summary.internal_dependencies:
            print(f"   - {dep}")
    else:
        print("   (无)")
    print("🌐 外部依赖 (External):")
    if summary.external_dependencies:
        for dep in summary.external_dependencies:
            print(f"   - {dep}")
    else:
        print("   (无)")
    print("⬅️  被谁依赖 (Dependents):")
    if summary.dependents:
        for src in summary.dependents:
            print(f"   - {src}")
    else:
        print("   (无)")
    if summary.outgoing_edges:
        print("📌 导入明细:")
        for edge in summary.outgoing_edges:
            member = f".{edge.imported_name}" if edge.imported_name else ""
            alias = f" as {edge.alias}" if edge.alias else ""
            tag = "内部" if edge.is_internal else "外部"
            print(f"   - L{edge.line_number} [{tag}] {edge.target_module}{member}{alias}")
    if summary.unresolved_in_module:
        print("⚠️ 该模块存在未解析导入 (unresolved):")
        for item in summary.unresolved_in_module:
            loc = f"L{item.line_number}" if item.line_number else "L?"
            print(f"   - {loc} [{item.reason_code}] {item.detail}")
    print("=" * 64 + "\n")


def handle_investigate(
    raw_arg: str,
    repo_root: Path,
    symbol_index: SymbolIndex | None = None,
    *,
    model: Any = None,
) -> None:
    """执行受控多步代码调查并输出高信噪比结构化证据报告。"""
    question = raw_arg.strip()
    if not question:
        print(
            "⚠️ 用法: /investigate <问题>，例如 /investigate execute_query 是如何调用模型与判卷的\n"
        )
        return

    print(f"🕵️  正在多步调查（预算内调工具核实）: {question}\n")
    try:
        result = investigate(
            project_root=repo_root,
            question=question,
            model=model,
            symbol_index=symbol_index,
        )
    except Exception as exc:
        print(f"⚠️ 调查执行失败: {exc}\n")
        return

    print(format_investigation_report(result))
    print()

@dataclass(frozen=True)
class _VerifySpecArgs:
    """/verify-spec 命令解析后的结构化参数。"""

    spec_file: str
    repo_override: str | None = None
    out_path: str | None = None
    auto_confirm: bool = False


def _strip_wrapping_quotes(token: str) -> str:
    """剥离 Windows shlex(posix=False) 保留的外层单/双引号。"""
    if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
        return token[1:-1]
    return token


def _parse_verify_spec_args(raw_arg: str) -> _VerifySpecArgs:
    """安全解析 /verify-spec 参数，兼容 Windows 反斜杠路径且不抛出 SystemExit。"""
    tokens = [_strip_wrapping_quotes(t) for t in shlex.split(raw_arg, posix=False)]
    spec_file: str | None = None
    repo_override: str | None = None
    out_path: str | None = None
    auto_confirm = False

    idx = 0
    while idx < len(tokens):
        tok = tokens[idx]
        if tok in ("--repo", "-r"):
            if idx + 1 >= len(tokens):
                raise ValueError("参数 --repo 缺少工程目录路径")
            repo_override = tokens[idx + 1]
            idx += 2
        elif tok in ("--out", "-o"):
            if idx + 1 >= len(tokens):
                raise ValueError("参数 --out 缺少报告输出文件路径")
            out_path = tokens[idx + 1]
            idx += 2
        elif tok in ("--yes", "-y"):
            auto_confirm = True
            idx += 1
        elif tok.startswith("-"):
            raise ValueError(f"不支持的参数选项: {tok}")
        else:
            if spec_file is not None:
                raise ValueError(f"检测到多余的位置参数: {tok}")
            spec_file = tok
            idx += 1

    if not spec_file:
        raise ValueError(
            "用法: /verify-spec <spec_file> [--repo <path>] [--out <report.md>] [--yes]"
        )

    return _VerifySpecArgs(
        spec_file=spec_file,
        repo_override=repo_override,
        out_path=out_path,
        auto_confirm=auto_confirm,
    )


def _resolve_spec_file(
    raw_spec: str,
    *,
    default_repo_root: Path,
    target_repo_root: Path,
) -> Path | None:
    """按绝对路径 -> 当前工作目录 -> 目标工程 -> 启动工程顺序定位规约文件。"""
    candidate = Path(raw_spec)
    if candidate.is_absolute():
        return candidate.resolve() if candidate.is_file() else None

    search_roots = [Path.cwd(), target_repo_root, default_repo_root]
    for base in search_roots:
        resolved = (base / candidate).resolve()
        if resolved.is_file():
            return resolved
    return None


def _build_full_spec_report_markdown(
    *,
    spec_path: Path,
    candidate_map: RuleCandidateMap,
    report: SpecAuditReport,
    retriever_type: str,
    auto_confirm: bool,
) -> str:
    """组合 S2 双口径定位统计与 S3 四态审计矩阵，生成完整可落盘 Markdown 报告。"""
    metrics = candidate_map.summary_metrics()
    base_md = format_audit_report_markdown(report)
    env_block = "\n".join(
        [
            "## 核查配置与 S2 定位分离统计",
            "",
            f"- **规约文件**: `{spec_path.as_posix()}`",
            f"- **检索策略**: `{retriever_type}` | **人工确认模式**: `{'auto(--yes)' if auto_confirm else 'human-in-the-loop'}`",
            (
                f"- **S2 自主定位成绩（纯自动，不含人工补充）**: "
                f"覆盖规则 {metrics.auto_matched_rules}/{metrics.total_rules} 条"
                f"（空命中 {metrics.auto_empty_rules} 条），自动候选共 {metrics.auto_total_candidates} 处"
            ),
            (
                f"- **S2 人工辅助与最终生效（单独计列）**: "
                f"人工介入 {metrics.human_intervened_rules} 条规则"
                f"（+{metrics.human_added_total} 新增 / -{metrics.human_removed_total} 排除），"
                f"最终生效覆盖 {metrics.effective_matched_rules}/{metrics.total_rules} 条，"
                f"送审候选共 {metrics.effective_total_candidates} 处"
            ),
            "",
        ]
    )
    return f"{base_md}\n\n{env_block}"


def handle_verify_spec(
    raw_arg: str,
    repo_root: Path,
    symbol_index: SymbolIndex | None = None,
    retriever: Any = None,
    *,
    retriever_type: str = "hybrid",
    parse_model: Any = None,
    audit_model: Any = None,
    budget: InvestigationBudget | None = None,
    input_fn: Callable[[str], str] = input,
    print_fn: Callable[[str], None] = print,
) -> SpecAuditReport | None:
    """执行 S1 拆解确认 -> S2 候选定位复核 -> S3 多步四态审计 -> Markdown 矩阵报告。"""
    try:
        parsed_args = _parse_verify_spec_args(raw_arg)
    except ValueError as exc:
        print_fn(f"⚠️ {exc}\n")
        return None

    default_root = repo_root.resolve()
    target_root = (
        Path(parsed_args.repo_override).resolve()
        if parsed_args.repo_override
        else default_root
    )
    if not target_root.is_dir():
        print_fn(f"❌ 指定的目标工程目录不存在: {target_root}\n")
        return None

    spec_path = _resolve_spec_file(
        parsed_args.spec_file,
        default_repo_root=default_root,
        target_repo_root=target_root,
    )
    if spec_path is None:
        print_fn(f"⚠️ 未找到规约文件: {parsed_args.spec_file}\n")
        return None

    # 1. 目标工程索引就绪（与当前启动目录一致时直接复用，切换 --repo 时就地构建）
    same_repo = target_root == default_root
    target_symbol_index = symbol_index if (same_repo and symbol_index is not None) else None
    target_retriever = retriever if (same_repo and retriever is not None) else None
    if target_symbol_index is None:
        print_fn(f"📦 正在为目标工程构建符号索引: {target_root} ...")
        target_symbol_index = build_symbol_index(target_root)
    if target_retriever is None:
        print_fn(f"📦 正在为目标工程构建检索索引 ({retriever_type}): {target_root} ...")
        target_retriever = build_retriever_for_project(
            target_root,
            retriever_type=retriever_type,
        )

    # 2. S1: 规范条款拆解 + 人工确认环 ①
    print_fn(f"\n📜 [S1] 正在拆解规约文档: {spec_path.as_posix()} ...")
    parse_result = parse_spec_rules(
        spec_path,
        project_root=Path.cwd(),
        model=parse_model,
    )
    if parsed_args.auto_confirm:
        for warn in parse_result.warnings:
            print_fn(f"⚠️ {warn}")
        confirm_all_drafts(parse_result.rules)
        active_rules = parse_result.active_rules()
        print_fn(f"✅ [--yes] 已自动确认全部 {len(active_rules)} 条规则草稿。")
    else:
        active_rules = interactive_confirm_rules(
            parse_result,
            input_fn=input_fn,
            print_fn=print_fn,
        )

    if not active_rules:
        print_fn("⚠️ 没有已激活的待核查规则，本次规范审计已结束。\n")
        return None

    # 3. S2: 规则与代码候选双路粗筛 + 人工复核环 ②
    print_fn(f"\n🗺️  [S2] 正在为 {len(active_rules)} 条规则定位候选实现 ...")
    candidate_map = match_rules_to_candidates(
        active_rules,
        project_root=target_root,
        symbol_index=target_symbol_index,
        retriever=target_retriever,
    )
    if parsed_args.auto_confirm:
        print_fn(format_candidate_map_report(candidate_map))
    else:
        candidate_map = interactive_review_candidates(
            candidate_map,
            symbol_index=target_symbol_index,
            input_fn=input_fn,
            print_fn=print_fn,
        )

    # 4. S3: 逐条多步调查与四态审计判定
    print_fn(
        f"\n🕵️  [S3] 开始对 {len(candidate_map.entries)} 条规则执行多步取证与四态审计 ..."
    )
    report = audit_candidate_map(
        candidate_map,
        model=audit_model,
        symbol_index=target_symbol_index,
        budget=budget,
    )
    markdown_report = _build_full_spec_report_markdown(
        spec_path=spec_path,
        candidate_map=candidate_map,
        report=report,
        retriever_type=retriever_type,
        auto_confirm=parsed_args.auto_confirm,
    )
    print_fn("\n" + markdown_report + "\n")

    if parsed_args.out_path:
        out_file = Path(parsed_args.out_path)
        if not out_file.is_absolute():
            out_file = (Path.cwd() / out_file).resolve()
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(markdown_report, encoding="utf-8")
        print_fn(f"💾 审计报告已写入: {out_file.as_posix()}\n")

    return report



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
    symbol_index = build_symbol_index(repo_root)
    code_count = sum(1 for c in retriever.chunks if c.source_type == "code")
    doc_count = sum(1 for c in retriever.chunks if c.source_type == "doc")

    print(
        f"🚀 Code-Agent 已启动！(目标工程: {repo_root.name} ｜ 共 {len(retriever.chunks)} 个切块 ｜ "
        f"💻 源码: {code_count} ｜ 📄 文档: {doc_count})"
    )
    print("  - 普通提问：直接输入问题（基于目标工程自动检索并给出带行号依据的回答）")
    print("  - 检索代码：输入 /search <关键词/语义意图> (例如: /search stream_ask)")
    print("  - 审查文件：输入 /review <文件路径> (例如: /review src/code_agent/cli.py)")
    print("  - 工程结构：输入 /structure (查看布局、模块清单与构建耗时)")
    print("  - 符号查询：输入 /symbols <符号名或FQN> (例如: /symbols BuildEngine)")
    print("  - 模块依赖：输入 /deps <模块全名> (例如: /deps minisite.cli)")
    print("  - 多步调查：输入 /investigate <问题> (例如: /investigate execute_query 是如何调用模型与判卷的)")
    print("  - 规范核查：输入 /verify-spec <规约文件> [--repo <路径>] [--out <报告.md>] [--yes]")
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
        if input_question == "/structure":
            handle_structure(symbol_index)
            continue
        if input_question == "/symbols" or input_question.startswith("/symbols "):
            handle_symbols(input_question[len("/symbols"):].strip(), symbol_index)
            continue
        if input_question == "/deps" or input_question.startswith("/deps "):
            handle_deps(input_question[len("/deps"):].strip(), symbol_index)
            continue
        if input_question == "/investigate" or input_question.startswith("/investigate "):
            handle_investigate(
                input_question[len("/investigate"):].strip(),
                repo_root=repo_root,
                symbol_index=symbol_index,
            )
            continue
        if input_question == "/verify-spec" or input_question.startswith("/verify-spec "):
            handle_verify_spec(
                input_question[len("/verify-spec"):].strip(),
                repo_root=repo_root,
                symbol_index=symbol_index,
                retriever=retriever,
                retriever_type=args.retriever,
            )
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
        if res.trace_path:
            display_trace = to_repo_relative(res.trace_path, base_dir=repo_root)
            print(f"   📝 Trace 审计已记录: {display_trace}")
        print("=" * 64 + "\n")



if __name__ == "__main__":
    main()
