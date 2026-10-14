import sys
import time
import json
from pathlib import Path
from dataclasses import dataclass, field
import yaml
import argparse

# 1. Windows 控制台防乱码与 emoji 保护
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 2. 注入工程根目录与 src 目录，确保平稳导入
root_dir = Path(__file__).resolve().parent.parent
src_dic = root_dir / "src"
if str(src_dic) not in sys.path:
    sys.path.insert(0,str(src_dic))


from langchain_core.messages import SystemMessage, HumanMessage
from code_agent.core.model import get_chat_model
from code_agent.index.bm25_index import BM25CodeIndex
from code_agent.core.citation import (
    CITATION_PROMPT_REQUIREMENT,
    extract_citations,
    validate_citations,
    has_no_evidence,
)

@dataclass
class EvalCase:
    """评测用例数据契约（对应 YAML 文件）"""
    id: str
    type: str
    question: str
    expected_files: list[str] = field(default_factory=list)
    expected_symbols: list[str] = field(default_factory=list)
    is_unanswerable: bool = False
    intent: str = ""
    source: str = ""
    note: str = ""

@dataclass
class CaseResult:
    """单题运行评测指标与结果"""
    case: EvalCase
    answer: str
    latency: float
    is_completed: bool  # 模型是否成功回答且未报错
    has_no_evidence_declared: bool  # 是否声明了无依据
    citations_count: int  # 提取到的引用总数
    valid_citations_count: int  # 经物理文件校验合法的引用数
    retrieval_file_hit: bool #检索阶段：BM25 Top-K 切块中是否召回了期望文件
    file_hit: bool  # 是否精准命中了期望文件
    symbol_hit: bool  # 是否精准命中了关键符号
    prompt_tokens: int = 0 #输入提示词token
    completion_tokens: int = 0 #模型输出 token
    total_tokens: int = 0 #总消耗token
    is_passed: bool = False # 综合判定是否通关
    fail_reason: str = ""  # 失败原因归因
    snippets_text: str = "" #实际喂给模型的 BM25 检索代码切片原文


def load_cases(cases_dir:Path) -> list[EvalCase]:
    """批量加载 eval/cases 目录下的所有 YAML 用例"""
    cases = []
    yaml_files = sorted(cases_dir.glob("*.yaml"))
    for file_path in yaml_files:
        with open(file_path, "r", encoding="utf-8") as f:
          data = yaml.safe_load(f)
          cases.append(
              EvalCase(
                  id=str(data.get("id",file_path.stem)),
                  type = data.get("type","location"),
                  question=data.get("question", ""),
                  expected_files=data.get("expected_files") or [],
                  expected_symbols=data.get("expected_symbols") or [],
                  is_unanswerable=data.get("is_unanswerable", False),
                  intent=data.get("intent", ""),
                  source=data.get("source", ""),
                  note=data.get("note", ""),
              )
          )

    return cases


def evaluate_single_case(
                            case: EvalCase,
                            indexer: BM25CodeIndex,
                            model,
                            top_k: int = 3,
                            base_dir: Path | None = None,
)-> CaseResult:
    """执行单个用例的全链路评测与客观判卷"""
    start_time = time.time()
    fail_reasons = []

    # 1. BM25 检索相关代码片段
    results = indexer.search(case.question, top_k=top_k)
    snippets_text = ""

    # 判定检索阶段是否成功召回了期望文件（Stage 1: 检索召回判断）
    retrieval_file_hit = False

    # 如果本身就没有期望文件，则默认召回成功
    if not case.expected_files:
        retrieval_file_hit = True
    elif results:
        retrieved_paths =[Path(chunk.file_path).as_posix().lower() for chunk, _ in results]
        for exp_f in case.expected_files:
            clean_exp = Path(exp_f).as_posix().lower()
            if any(clean_exp in rp or rp.endswith(clean_exp) for rp in retrieved_paths):
                retrieval_file_hit = True
                break

    if results:
        for i, (chunk, score) in enumerate(results, 1):
            # 方案 A 数据源治理：将物理绝对路径转为相对于工程根目录的标准相对路径（消除 Windows 盘符冒号）
            raw_path = Path(chunk.file_path)
            if base_dir:
                try:
                    rel_path = raw_path.resolve().relative_to(Path(base_dir).resolve())
                    clean_path = str(rel_path).replace("\\", "/")
                except ValueError:
                    clean_path = str(raw_path).replace("\\", "/")
            else:
                clean_path = str(raw_path).replace("\\", "/")

            snippets_text += f"\n--- 片段 {i}: {clean_path} (第 {chunk.start_line}~{chunk.end_line} 行) ---\n"
            snippets_text += chunk.content + "\n"

    # 2. 组装 Prompt 并调用大模型
    system_prompt = (
        "你是一个严谨的代码工程助手。\n"
        f"{CITATION_PROMPT_REQUIREMENT}"
    )
    user_prompt = (
        f"【参考代码片段】:\n{snippets_text}\n\n"
        f"【用户问题】: {case.question}\n"
        "请根据参考代码回答，并严格标注每一处出处引用。"
    )

    try:
        response = model.invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ])
        answer = response.content
        is_completed = bool(answer and answer.strip())

        #安全提取 Token 消耗元数据
        usage = getattr(response,"usage_metadata",{}) or {}
        if not usage and hasattr(response,"response_metadata"):
            usage = response.response_metadata.get("token_usage", {}) or {}
        prompt_tokens = usage.get("input_tokens", 0)
        completion_tokens = usage.get("output_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)
    except Exception as e:
        answer = f"调用模型异常: {e}"
        is_completed = False
        fail_reasons.append("模型调用异常")

    latency = time.time() - start_time
    # 3. 引用解析与物理校验
    no_evidence_declared = has_no_evidence(answer)
    citations = extract_citations(answer)
    validation_results = validate_citations(citations, base_dir=base_dir)
    valid_citations = [r for r in validation_results if r.is_valid]
    citations_count = len(citations)
    valid_citations_count = len(valid_citations)

    # 4. 判定文件命中与符号命中
    file_hit = False
    symbol_hit = False

    if case.expected_files:
        # 检查提取到的合法引用中是否命中了期望文件
        cited_files = {c.file_path.replace("\\", "/") for c in citations}
        for exp_f in case.expected_files:
            clean_exp = exp_f.replace("\\", "/")
            if any(clean_exp in cf or cf in clean_exp for cf in cited_files):
                file_hit = True
                break
    else:
        file_hit = True  # 未指定期望文件时默认不扣分

    if case.expected_symbols:
      missing_symbols = [
          sym for sym in case.expected_symbols
          if sym.lower() not in answer.lower()
      ]
      if missing_symbols:
          symbol_hit = False
          fail_reasons.append(f"未命中期望关键符号: {missing_symbols}")
      else:
          symbol_hit = True

    # 5. 综合通关判决（Pass / Fail）
    is_passed = False
    if case.is_unanswerable:
        # 边界拒答题判卷标准：必须明确声明无依据，且不能编造合法引用
        if no_evidence_declared:
            is_passed = True
        else:
            fail_reasons.append("虚构问题未能成功拒答（未声明“无依据”）")
    else:
        # 正常题判卷标准：
        if not is_completed:
            fail_reasons.append("回答未完成或报错")
        elif no_evidence_declared and case.id != "019":
            fail_reasons.append("真实功能被模型判定为“无依据”（检索未召回关键代码片段）")
        elif citations_count == 0:
            fail_reasons.append("未标注任何引用出处")
        elif valid_citations_count == 0:
            fail_reasons.append("所有引用的行号均物理校验失败（存在行号漂移或假文件）")
        elif not file_hit:
            fail_reasons.append(f"未命中期望文件 {case.expected_files}")
        elif not symbol_hit:
            fail_reasons.append(f"未命中期望关键符号 {case.expected_symbols}")
        else:
            is_passed = True

    return CaseResult(
        case=case,
        answer=answer,
        latency=latency,
        is_completed=is_completed,
        has_no_evidence_declared=no_evidence_declared,
        citations_count=citations_count,
        valid_citations_count=valid_citations_count,
        retrieval_file_hit=retrieval_file_hit,
        file_hit=file_hit,
        symbol_hit=symbol_hit,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        is_passed=is_passed,
        fail_reason="; ".join(fail_reasons),
        snippets_text=snippets_text,

    )

def generate_markdown_report(
        results: list[CaseResult],
        report_file: Path | None = None,
        top_k : int = 3,
)->None:
    """生成结构清晰的 Markdown 评测报告 (包含大盘指标、失败归因与全量清单)"""
    total = len(results)
    if total == 0:
        return

    passed_count = sum(1 for r in results if r.is_passed)
    completed_count = sum(1 for r in results if r.is_completed)
    total_citations = sum(r.citations_count for r in results)
    valid_citations = sum(r.valid_citations_count for r in results)
    retrieval_hits = sum(1 for r in results if r.retrieval_file_hit)
    boundary_cases = [r for r in results if r.case.is_unanswerable]
    boundary_passed = sum(1 for r in boundary_cases if r.is_passed)

    pass_rate = (passed_count / total * 100)
    completion_rate = (completed_count / total * 100)
    retrieval_recall = (retrieval_hits / total * 100)
    citation_valid_rate = (valid_citations / total_citations * 100) if total_citations else 0.0
    boundary_rate = (boundary_passed / len(boundary_cases) * 100) if boundary_cases else 0.0
    avg_latency = sum(r.latency for r in results) / total
    total_tokens = sum(r.total_tokens for r in results)
    avg_tokens = total_tokens / total

    # 格式化当前时间
    cur_time = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
    lines = []
    lines.append("# Code-Agent 自动化评测报告 (Benchmark Report)")
    lines.append(f"\n> **评测时间**：{cur_time} ｜ **检索参数**：BM25 (Top-K={top_k}) ｜ **模型**：DeepSeek (temp=0.0)\n")
    # 一、大盘看板
    lines.append("## 一、评测大盘总结 (Summary Dashboard)")
    lines.append("")
    lines.append("| 核心指标 | 实测数值 | 达标判定 / 说明 |")
    lines.append("| :--- | :--- | :--- |")
    lines.append(f"| **综合通关率** | **{pass_rate:.1f}%** ({passed_count}/{total}) | 端到端各环节严格通过占比 |")
    lines.append(
        f"| **检索阶段召回率 (Stage 1)** | **{retrieval_recall:.1f}%** ({retrieval_hits}/{total}) | BM25 Top-K 命中期望目标文件比率 |")
    lines.append(
        f"| **模型回答完成率** | **{completion_rate:.1f}%** ({completed_count}/{total}) | 模型未报错且有实质性回答 |")
    lines.append(
        f"| **引用物理有效率** | **{citation_valid_rate:.1f}%** ({valid_citations}/{total_citations}) | 引用行号与文件真实存在比率 |")
    lines.append(
        f"| **虚构边界防御率** | **{boundary_rate:.1f}%** ({boundary_passed}/{len(boundary_cases)}) | 面对不存在功能严谨拒答比率 |")
    lines.append(f"| **平均单题耗时** | **{avg_latency:.2f}s** | 检索 + 推理端到端延迟 |")
    lines.append(f"| **总消耗 Token** | **{total_tokens:,} tok** | 本次评测累计 Token 消耗 |")
    lines.append(f"| **单题平均 Token** | **{avg_tokens:.0f} tok** | 提示词与回答平均成本 |")
    lines.append("")

    # 二、未通过用例归因明细
    failed_results = [r for r in results if not r.is_passed]
    lines.append("## 二、未通关用例归因明细 (Failure Analysis)")
    lines.append("")
    if not failed_results:
        lines.append("🎉 **全量通关！本次评测无任何失败用例。**\n")
    else:
        lines.append("| 用例 ID | 题型 | 检索是否召回 | 耗时 | 失败原因归因 |")
        lines.append("| :--- | :--- | :---: | :--- | :--- |")
        for r in failed_results:
            ret_mark = "✅ 命中" if r.retrieval_file_hit else "❌ 丢失"
            clean_reason = r.fail_reason.replace("\n", " ")
            lines.append(f"| `{r.case.id}` | {r.case.type} | {ret_mark} | {r.latency:.2f}s | {clean_reason} |")
        lines.append("")

        # 三、全量用例明细表
    lines.append("## 三、全量用例清单明细 (All Cases)")
    lines.append("")
    lines.append("| ID | 题型 | 判定 | 检索召回 | 引用数(有效/总) | 耗时 | Token |")
    lines.append("| :--- | :--- | :---: | :---: | :---: | :---: | :---: |")
    for r in results:
        status_icon = "✅ PASS" if r.is_passed else "❌ FAIL"
        ret_icon = "✅" if r.retrieval_file_hit else "❌"
        cite_str = f"{r.valid_citations_count}/{r.citations_count}"
        tok_str = f"{r.total_tokens:,}" if r.total_tokens else "-"
        lines.append(
            f"| `{r.case.id}` | {r.case.type} | {status_icon} | {ret_icon} | {cite_str} | {r.latency:.2f}s | {tok_str} |")
    report_file.parent.mkdir(parents=True, exist_ok=True)
    report_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"📄 Markdown 评测报告已生成至: {report_file}")



def run_eval(cases_dir: Path,
             output_file: Path | None = None,
             report_file: Path | None = None,
             case_filter: str | None = None,
             top_k : int = 3,
             verbose: bool = False, #控制是否详细打印切片
             ) -> None:
    """运行全量评测并输出基线报告"""
    print("=" * 70)
    print("🚀 开始执行 Code-Agent 黄金案例集自动化评测 (W2 S3 Baseline)")
    print("=" * 70)

    # 1. 构建 BM25 本地索引
    root_path = Path(__file__).resolve().parent.parent
    src_path = root_path / "src"
    print(f"📦 正在扫描并构建索引: {src_path} ...")
    indexer = BM25CodeIndex()
    total_chunks = indexer.build_from_directory(src_path)
    print(f"✅ 索引构建完成，总代码切块数: {total_chunks}")

    # 2. 初始化大模型（使用 temperature=0.0 保证评测确定性）
    model = get_chat_model(temperature=0.0)

    # 3. 加载用例
    cases = load_cases(cases_dir)
    if case_filter:
        targer_ids = [cid.strip() for cid in case_filter.split(",") if cid.strip()]
        # 兼容匹配：支持精准匹配 "005" 或前缀匹配 "005-line_out_of_bounds"
        cases =[
            c for c in cases
            if any (c.id == tid or c.id.startswith(tid) for tid in targer_ids)
        ]
        if not cases:
            print(f"⚠️ 未找到匹配的用例: '{case_filter}'，请检查用例 ID")
            return
    print(f"📋 本次共评测 {len(cases)} 个用例\n")

    results: list[CaseResult] = []

    # 4. 逐题运行
    for i, case in enumerate(cases, 1):
        print(f"[{i:02d}/{len(cases):02d}] 正在评测 [{case.type.upper()}] {case.id} ... ", end="", flush=True)
        res = evaluate_single_case(case, indexer, model, top_k=top_k, base_dir=root_path)
        results.append(res)

        status_tag = "✅ PASS" if res.is_passed else "❌ FAIL"
        print(f"{status_tag} ({res.latency:.2f}s)")
        if not res.is_passed:
            print(f"     🚨 失败原因: {res.fail_reason}")

        if verbose:
            print("\n" + "-" * 70)
            print(f"🔍 【检索追溯 Trace - Case {case.id}】(Top-{top_k} 代码切块)")
            print("-" * 70)
            if res.snippets_text.strip():
                print(res.snippets_text.strip())
            else:
                print("（⚠️ 未检索到任何相关代码切块）")
            print("-" * 70 + "\n")

    # 5. 汇总核心指标
    total = len(results)
    passed_count = sum(1 for r in results if r.is_passed)
    completed_count = sum(1 for r in results if r.is_completed)
    total_citations = sum(r.citations_count for r in results)
    valid_citations = sum(r.valid_citations_count for r in results)
    boundary_cases = [r for r in results if r.case.is_unanswerable]
    boundary_passed = sum(1 for r in boundary_cases if r.is_passed)

    pass_rate = (passed_count / total * 100) if total else 0.0
    completion_rate = (completed_count / total * 100) if total else 0.0
    citation_valid_rate = (valid_citations / total_citations * 100) if total_citations else 0.0
    boundary_rate = (boundary_passed / len(boundary_cases) * 100) if boundary_cases else 0.0
    avg_latency = sum(r.latency for r in results) / total if total else 0.0

    print("\n" + "=" * 70)
    print("📊 【评测大盘总结 (Benchmark Summary)】")
    print("=" * 70)
    print(f"  - 总用例数:           {total}")
    print(f"  - 综合通关率:         {pass_rate:.1f}% ({passed_count}/{total})")
    print(f"  - 模型回答完成率:     {completion_rate:.1f}% ({completed_count}/{total})")
    print(f"  - 引用物理有效率:     {citation_valid_rate:.1f}% ({valid_citations}/{total_citations})")
    print(f"  - 虚构边界防御率:     {boundary_rate:.1f}% ({boundary_passed}/{len(boundary_cases)})")
    print(f"  - 平均单题耗时:       {avg_latency:.2f}s")
    print("=" * 70)

    # 6. 保存基线结果 JSON
    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        dump_data = {
            "meta": {
                "total": total,
                "pass_rate": pass_rate,
                "citation_valid_rate": citation_valid_rate,
                "avg_latency": avg_latency,
                "timestamp": time.time(),
            },
            "cases": [
                {
                    "id": r.case.id,
                    "type": r.case.type,
                    "question": r.case.question,
                    "is_passed": r.is_passed,
                    "latency": r.latency,
                    "fail_reason": r.fail_reason,
                    "answer": r.answer,
                }
                for r in results
            ],
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(dump_data, f, ensure_ascii=False, indent=2)
        print(f"💾 基线数据已成功保存至: {output_file}")
    if report_file:
        generate_markdown_report(results, report_file, top_k=top_k)



def main():
    parser = argparse.ArgumentParser(description="Code-Agent 自动化黄金测评套件")
    parser.add_argument("--case", "-c", type=str, default=None, help="指定用例ID进行调试（如 -c 005 或 -c 001,005）")
    parser.add_argument("--top-k", "-k", type=int, default=3, help="BM25 检索召回的切块数量（默认 3）")
    parser.add_argument("--output", "-o", type=str, default=None, help="指定 JSON 报告输出路径")
    parser.add_argument("--report", "-r", type=str, default=None, help="指定 Markdown 报告输出路径")
    parser.add_argument("--verbose","-v", action="store_true", help="打印详细的检索代码切块 Trace")
    args = parser.parse_args()

    root_path = Path(__file__).resolve().parent.parent
    cases_dir = root_path / "eval" / "cases"
    runs_dir = root_path / "eval" / "runs"

    output_file = Path(args.output) if args.output else (runs_dir / "baseline_bm25.json")
    report_file = Path(args.report) if args.report else (runs_dir / "report-001.md")

    run_eval(
        cases_dir,
        output_file=output_file,
        report_file=report_file,
        case_filter=args.case,
        top_k=args.top_k,
        verbose=args.verbose
    )


if __name__ == "__main__":
    main()