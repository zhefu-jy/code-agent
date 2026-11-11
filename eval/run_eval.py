import sys
import time
import json
import hashlib
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any
import yaml
import argparse
from code_agent.core.context import build_snippets_text

# 1. Windows 控制台防乱码与 emoji 保护
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")

# 2. 注入工程根目录与 src 目录，确保平稳导入
root_dir = Path(__file__).resolve().parent.parent
src_dic = root_dir / "src"
if str(src_dic) not in sys.path:
    sys.path.insert(0,str(src_dic))


from langchain_core.messages import SystemMessage, HumanMessage
from code_agent.core.model import get_chat_model
from code_agent.index.bm25_index import BM25CodeIndex, CodeChunk
from code_agent.index.vector_index import VectorCodeIndex
from code_agent.index.hybrid_retriever import HybridRetriever, HybridSearchResult
from code_agent.core.citation import CITATION_PROMPT_REQUIREMENT
from code_agent.core.judge import CaseSpec, MatchMode, Verdict, judge_answer

@dataclass
class EvalCase:
    """评测用例数据契约（对应 YAML 文件）"""
    id: str
    type: str
    question: str
    expected_files: list[str] = field(default_factory=list)
    expected_symbols: list[str] = field(default_factory=list)
    is_unanswerable: bool = False
    allow_partial_refusal: bool = False
    match_mode: str = "any"
    intent: str = ""
    source: str = ""
    note: str = ""

    def to_spec(self) -> CaseSpec:
        """转换为判卷内核所需的不可变用例规格"""
        mode: MatchMode = "all" if self.match_mode == "all" else "any"
        return CaseSpec(
            id=self.id,
            expected_files=tuple(self.expected_files),
            expected_symbols=tuple(self.expected_symbols),
            is_unanswerable=self.is_unanswerable,
            allow_partial_refusal=self.allow_partial_refusal,
            match_mode=mode,
        )

@dataclass
class CaseResult:
    """单题运行评测指标与结果"""
    case: EvalCase
    answer: str
    latency: float #单题耗时
    is_completed: bool  # 模型是否成功回答且未报错
    has_no_evidence_declared: bool  # 是否声明了无依据（局部免责也算，仅作展示）
    refusal: str  # none / scoped（局部免责）/ global（全局拒答）
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
    full_prompt : str = "" #实际喂给大模型的完整组装 Prompt 原文


def load_cases(cases_dir:Path) -> list[EvalCase]:
    """批量加载 eval/cases 目录下的所有 YAML 用例"""
    cases = []
    yaml_files = sorted(cases_dir.glob("*.yaml"))
    for file_path in yaml_files:
        with open(file_path, "r", encoding="utf-8") as f:
          data = yaml.safe_load(f)
          case_id = str(data.get("id", file_path.stem))
          cases.append(
              EvalCase(
                  id=case_id,
                  type=data.get("type", "location"),
                  question=data.get("question", ""),
                  expected_files=data.get("expected_files") or [],
                  expected_symbols=data.get("expected_symbols") or [],
                  is_unanswerable=data.get("is_unanswerable", False),
                  allow_partial_refusal=bool(data.get("allow_partial_refusal", False)),
                  match_mode=str(data.get("match_mode", "any")),
                  intent=data.get("intent", ""),
                  source=data.get("source", ""),
                  note=data.get("note", ""),
              )
          )

    return cases


def evaluate_single_case(
                            case: EvalCase,
                            indexer: BM25CodeIndex | VectorCodeIndex | HybridRetriever,
                            model,
                            top_k: int = 3,
                            base_dir: Path | None = None,
)-> CaseResult:
    """执行单个用例的全链路评测与客观判卷"""
    start_time = time.time()
    fail_reasons: list[str] = []

    # 1. BM25 检索相关代码片段
    raw_results = indexer.search(case.question, top_k=top_k)
    hits: list[tuple[CodeChunk, float]] = []

    for item in raw_results:
        if isinstance(item,HybridSearchResult):
            hits.append((item.chunk, item.score))
        else:
            hits.append(item)

    # 判定检索阶段是否成功召回了期望文件（Stage 1: 检索召回判断）
    retrieval_file_hit = False

    # 如果本身就没有期望文件，则默认召回成功
    if not case.expected_files:
        retrieval_file_hit = True
    elif hits:
        retrieved_paths =[Path(chunk.file_path).as_posix().lower() for chunk, _ in hits]
        for exp_f in case.expected_files:
            clean_exp = Path(exp_f).as_posix().lower()
            if any(clean_exp in rp or rp.endswith(clean_exp) for rp in retrieved_paths):
                retrieval_file_hit = True
                break

    snippets_text = build_snippets_text(hits, base_dir=base_dir)

    # 2. 组装 Prompt 并调用大模型（Stage 2）
    system_prompt = (
        "你是一个严谨的代码工程与技术规范助手。\n"
        f"{CITATION_PROMPT_REQUIREMENT}"
    )
    user_prompt = (
        f"【参考上下文片段（包含源码与技术文档）】:\n{snippets_text}\n\n"
        f"【用户问题】: {case.question}\n"
        "请根据参考资料回答，并严格标注每一处出处引用。"
    )
    full_prompt = (
        f"=== [System Message] ===\n{system_prompt}\n\n"
        f"=== [User Message] ===\n{user_prompt}"
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

    # 3. 分段判卷（纯函数内核，见 code_agent/core/judge.py；可离线复跑，不依赖本文件的检索链路）
    verdict: Verdict = judge_answer(case.to_spec(), answer, base_dir=base_dir)

    fail_reasons.extend(verdict.fail_reasons)
    if not is_completed and "回答未完成" not in " ".join(fail_reasons):
        fail_reasons.insert(0, "模型调用异常")

    return CaseResult(
        case=case,
        answer=answer,
        latency=latency,
        is_completed=is_completed,
        has_no_evidence_declared=verdict.refusal != "none",
        refusal=verdict.refusal,
        citations_count=verdict.citations_total,
        valid_citations_count=verdict.citations_valid,
        retrieval_file_hit=retrieval_file_hit,
        file_hit=verdict.file_hit,
        symbol_hit=verdict.symbol_hit,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=total_tokens,
        is_passed=verdict.passed and is_completed,
        fail_reason="; ".join(fail_reasons),
        snippets_text=snippets_text,
        full_prompt=full_prompt,
    )

def generate_markdown_report(
        results: list[CaseResult],
        report_file: Path,
        retriever_type: str = "hybrid",
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
    lines.append(
        f"\n> **评测时间**：{cur_time} ｜ **检索策略**：{retriever_type.upper()} (Top-K={top_k}) ｜ **模型**：DeepSeek (temp=0.0)\n")
    # 一、大盘看板（分维度与全量细分统计）
    lines.append("## 一、评测大盘总结 (Summary Dashboard)")
    lines.append("")
    lines.append("| 评测维度 / 子集 | 用例数 | 通关率 | 检索阶段召回率 (Stage 1) | 引用物理有效率 |")
    lines.append("| :--- | :---: | :---: | :---: | :---: |")

    code_results = [r for r in results if r.case.id.isdigit() and int(r.case.id) <= 25]
    doc_results = [r for r in results if r not in code_results]

    if code_results:
        c_tot = len(code_results)
        c_pass = sum(1 for r in code_results if r.is_passed)
        c_ret = sum(1 for r in code_results if r.retrieval_file_hit)
        c_cit_tot = sum(r.citations_count for r in code_results)
        c_cit_val = sum(r.valid_citations_count for r in code_results)
        c_cit_rate = (c_cit_val / c_cit_tot * 100) if c_cit_tot else 0.0
        lines.append(f"| 💻 **核心代码子集 (Cases 001–025)** | {c_tot} | **{c_pass/c_tot*100:.1f}%** ({c_pass}/{c_tot}) | {c_ret/c_tot*100:.1f}% ({c_ret}/{c_tot}) | {c_cit_rate:.1f}% ({c_cit_val}/{c_cit_tot}) |")

    if doc_results:
        d_tot = len(doc_results)
        d_pass = sum(1 for r in doc_results if r.is_passed)
        d_ret = sum(1 for r in doc_results if r.retrieval_file_hit)
        d_cit_tot = sum(r.citations_count for r in doc_results)
        d_cit_val = sum(r.valid_citations_count for r in doc_results)
        d_cit_rate = (d_cit_val / d_cit_tot * 100) if d_cit_tot else 0.0
        lines.append(f"| 📄 **架构规约与跨源子集 (Cases 026–030)** | {d_tot} | **{d_pass/d_tot*100:.1f}%** ({d_pass}/{d_tot}) | {d_ret/d_tot*100:.1f}% ({d_ret}/{d_tot}) | {d_cit_rate:.1f}% ({d_cit_val}/{d_cit_tot}) |")

    lines.append(f"| 🌟 **全量综合大盘 (Overall Benchmark)** | {total} | **{pass_rate:.1f}%** ({passed_count}/{total}) | {retrieval_recall:.1f}% ({retrieval_hits}/{total}) | {citation_valid_rate:.1f}% ({valid_citations}/{total_citations}) |")
    lines.append("")
    lines.append("### 核心工程与性能指标")
    lines.append("")
    lines.append("| 核心指标 | 实测数值 | 达标判定 / 说明 |")
    lines.append("| :--- | :--- | :--- |")
    lines.append(f"| **模型回答完成率** | **{completion_rate:.1f}%** ({completed_count}/{total}) | 模型未报错且有实质性回答 |")
    lines.append(f"| **虚构边界防御率** | **{boundary_rate:.1f}%** ({boundary_passed}/{len(boundary_cases)}) | 面对不存在功能严谨拒答比率 |")
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
             retriever_type: str = "hybrid",
             top_k : int = 3,
             verbose: bool = False, #控制是否详细打印切片
             show_prompt: bool = False, #控制是否打印完整Prompt
             is_replay: bool = False
             ) -> None:
    """运行全量评测并输出基线报告"""
    print("=" * 70)
    print(f"🚀 开始执行 Code-Agent 黄金案例集自动化评测 (检索策略: {retriever_type.upper()})")
    print("=" * 70)

    # 1. 构建 BM25 本地索引
    root_path = Path(__file__).resolve().parent.parent
    src_path = root_path / "src"
    docs_path = root_path / "docs"
    print(f"📦 正在扫描并构建索引 [{retriever_type.upper()}]: src={src_path}, docs={docs_path} ...")

    indexer: BM25CodeIndex | VectorCodeIndex | HybridRetriever
    if retriever_type == "bm25":
        indexer = BM25CodeIndex()
        total_chunks = indexer.build_from_directory(src_path)
    elif retriever_type == "vector":
        indexer = VectorCodeIndex()
        total_chunks = indexer.build_from_directory(src_path)
    elif retriever_type == "hybrid":
        indexer = HybridRetriever(alpha=0.5)
        total_chunks = indexer.build_from_sources(code_dir=src_path, doc_dir=docs_path)
    else:
        raise ValueError(f"不支持的检索策略: {retriever_type}")
    print(f"✅ 索引构建完成，总切块数: {total_chunks}")

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

        #Replay 模式专享的白盒体检表与模型原始回答
        if is_replay:
            print("\n" + "🔍" * 35)
            print(f"🎬 【Replay 深度诊断白盒透视 - Case {case.id}】")
            print("🔍" * 35)
            print(f"📌 [测试问题]: {case.question}")
            print(f"🎯 [期望文件]: {case.expected_files}")
            print(f"🎯 [期望符号]: {case.expected_symbols}")
            print(f"🎯 [是否虚构题]: {case.is_unanswerable}")
            print("-" * 70)
            print("🤖 【模型原始完整回答】:")
            print(res.answer.strip())
            print("-" * 70)
            print("📋 【判卷器 5 维白盒体检表】:")
            print(f"  1. [检索召回 Stage 1]: {'✅ 召回期望文件' if res.retrieval_file_hit else '❌ 未召回期望文件'}")
            print(
                f"  2. [拒答分级]:         {res.refusal}（global=整题拒答 / scoped=局部免责 / none=正常作答）")
            print(f"  3. [引用出处有效性]:   提取 {res.citations_count} 处引用，物理有效 {res.valid_citations_count} 处")
            print(
                f"  4. [文件与符号命中]:   文件命中={'✅' if res.file_hit else '❌'}, 符号命中={'✅' if res.symbol_hit else '❌'}")
            print(
                f"  5. [Token 与耗时]:     输入 {res.prompt_tokens} tok, 输出 {res.completion_tokens} tok, 耗时 {res.latency:.2f}s")
            print("-" * 70)
            print(f"🏆 【最终判定】: {'✅ PASS' if res.is_passed else '❌ FAIL'}")
            if not res.is_passed:
                print(f"🚨 【裁决因果】: {res.fail_reason}")
            print("🔍" * 35 + "\n")


        if verbose:
            print("\n" + "-" * 70)
            print(f"🔍 【检索追溯 Trace - Case {case.id}】(Top-{top_k} 代码切块)")
            print("-" * 70)
            if res.snippets_text.strip():
                print(res.snippets_text.strip())
            else:
                print("（⚠️ 未检索到任何相关代码切块）")
            print("-" * 70 + "\n")

        if show_prompt:
            print("\n" + "=" * 70)
            print(f"📝 【完整输入 Prompt - Case {case.id}】")
            print("=" * 70)
            print(res.full_prompt.strip())
            print("=" * 70 + "\n")

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
    total_tokens = sum(r.total_tokens for r in results)
    avg_tokens = total_tokens / total if total else 0.0

    code_res = [r for r in results if r.case.id.isdigit() and int(r.case.id) <= 25]
    doc_res = [r for r in results if r not in code_res]

    print("\n" + "=" * 70)
    print("📊 【评测大盘总结 (Benchmark Summary)】")
    print("=" * 70)
    print(f"  - 总用例数:           {total}")
    if code_res:
        c_p = sum(1 for r in code_res if r.is_passed)
        print(f"  - 核心代码子集:       {c_p/len(code_res)*100:.1f}% ({c_p}/{len(code_res)})")
    if doc_res:
        d_p = sum(1 for r in doc_res if r.is_passed)
        print(f"  - 架构规约子集:       {d_p/len(doc_res)*100:.1f}% ({d_p}/{len(doc_res)})")
    print(f"  - 综合通关率:         {pass_rate:.1f}% ({passed_count}/{total})")
    ret_rate = (sum(1 for r in results if r.retrieval_file_hit) / total * 100) if total else 0.0
    print(f"  - 检索阶段召回率:     {ret_rate:.1f}%")
    print(f"  - 模型回答完成率:     {completion_rate:.1f}% ({completed_count}/{total})")
    print(f"  - 引用物理有效率:     {citation_valid_rate:.1f}% ({valid_citations}/{total_citations})")
    print(f"  - 虚构边界防御率:     {boundary_rate:.1f}% ({boundary_passed}/{len(boundary_cases)})")
    print(f"  - 平均单题耗时:       {avg_latency:.2f}s")
    print(f"  - 总消耗 Token:       {total_tokens:,} tok")
    print(f"  - 单题平均 Token:     {avg_tokens:.0f} tok")
    print("=" * 70)

    # 6. 保存基线结果 JSON（仅在非重放调试且指定了 output_file 时保存）
    if output_file and not is_replay:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        dump_data = {
            "meta": {
                "total": total,
                "pass_rate": pass_rate,
                "retrieval_recall": ret_rate,
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
                    "retrieval_file_hit": r.retrieval_file_hit,
                    "file_hit": r.file_hit,
                    "symbol_hit": r.symbol_hit,
                    "citations_count": r.citations_count,
                    "valid_citations_count": r.valid_citations_count,
                    "latency": r.latency,
                    "fail_reason": r.fail_reason,
                    "answer": r.answer,
                    "total_tokens": r.total_tokens,
                }
                for r in results
            ],
        }
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(dump_data, f, ensure_ascii=False, indent=2)
        print(f"💾 基线数据已成功保存至: {output_file}")
    if report_file:
        generate_markdown_report(results, report_file, retriever_type=retriever_type,top_k=top_k)



def main():
    parser = argparse.ArgumentParser(description="Code-Agent 自动化黄金测评套件")
    parser.add_argument("--case", "-c", type=str, default=None, help="指定用例ID进行调试（如 -c 005 或 -c 001,005）")
    parser.add_argument("--replay", type=str, default=None, help="深度重放调试模式（如 --replay 022 或 --replay failed）")
    parser.add_argument("--top-k", "-k", type=int, default=3, help="BM25 检索召回的切块数量（默认 3）")
    parser.add_argument("--output", "-o", type=str, default=None, help="指定 JSON 报告输出路径")
    parser.add_argument("--report", "-r", type=str, default=None, help="指定 Markdown 报告输出路径")
    parser.add_argument("--verbose","-v", action="store_true", help="打印详细的检索代码切块 Trace")
    parser.add_argument("--prompt", "-p", action="store_true", help="打印喂给大模型的完整 Prompt 原文")
    parser.add_argument("--retriever",type=str,choices=["bm25","vector","hybrid"],default="hybrid",help="指定检索策略：bm25（词法）、vector（语义向量）、hybrid（双路加权融合，默认）",)

    args = parser.parse_args()

    root_path = Path(__file__).resolve().parent.parent
    cases_dir = root_path / "eval" / "cases"
    runs_dir = root_path / "eval" / "runs"

    is_replay = bool(args.replay)
    case_filter = args.case
    verbose = args.verbose
    show_prompt = args.prompt
    # 当指定 --replay 时，自动接管调试参数
    if is_replay:
       verbose = True
       show_prompt = True
       if args.replay.lower() == "failed":
       # 自动从上次 baseline_bm25.json 读取所有未通关的题号
          baseline_file = runs_dir / "baseline_bm25.json"
          if baseline_file.exists():
              with open(baseline_file, "r", encoding="utf-8") as f:
                  data = json.load(f)
              failed_ids = [c["id"] for c in data.get("cases", []) if not c.get("is_passed")]
              if not failed_ids:
                  print("🎉 上次评测大盘没有任何失败案例，无需重放！")
                  return
              case_filter = ",".join(failed_ids)
              print(f"🔄 自动抓取上次失败用例: {case_filter}")
          else:
              print("⚠️ 未找到历史基线 baseline_bm25.json，无法自动提取失败用例")
              return
       else:
            case_filter = args.replay


    if args.output:
        output_file: Path | None = Path(args.output)
    elif case_filter:
        output_file = None            # 局部调试不落盘，保护历史基线
    else:
        output_file = runs_dir / "baseline_bm25.json"

    if args.report:
        report_file: Path | None = Path(args.report)
    elif case_filter:
        report_file = None
    else:
        report_file = runs_dir / "report-001.md"

    run_eval(
        cases_dir,
        output_file=output_file,
        report_file=report_file,
        case_filter=case_filter,
        retriever_type =args.retriever,
        top_k=args.top_k,
        verbose=verbose,
        show_prompt=show_prompt,
        is_replay=is_replay,
    )


if __name__ == "__main__":
    main()