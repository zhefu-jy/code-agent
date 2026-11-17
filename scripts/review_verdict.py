"""人工复核标记工具：审核评测 Run 或 Trace 中的待确认项，完成‘机器查规则，人工审事实’闭环。
用法示例：
    # 1. 查看 Run 中所有待人工复核的题目 (NEEDS_HUMAN_REVIEW)
    python scripts/review_verdict.py --run eval/runs/run-009.json --list
    # 2. 交互式复核待确认题目并录入意见
    python scripts/review_verdict.py --run eval/runs/run-009.json --interactive
    # 3. 命令行一键标定单题
    python scripts/review_verdict.py --run eval/runs/run-009.json --case 001 --verdict CORRECT --note "人工核查：定义行与签名完全属实"
    # 4. 标定单个 Trace 审计文件
    python scripts/review_verdict.py --trace traces/trace_xxxx.json --verdict CORRECT --note "经抽检回答准确"
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any
# Windows 控制台防乱码与 emoji 保护
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
from code_agent.core.schemas import AnswerVerdictKind, JudgeSource  # noqa: E402
from code_agent.core.trace import load_trace, save_trace  # noqa: E402

def review_single_trace(
    trace_path: Path,
    verdict_str: str,
    note: str = "",
) -> None:
    """对单个 Trace 实体进行人工复核升级标定。"""
    record = load_trace(trace_path)
    old_verdict = record.answer_verdict.verdict.value
    old_source = record.answer_verdict.judge_source.value
    record.answer_verdict.verdict = AnswerVerdictKind(verdict_str)
    record.answer_verdict.judge_source = JudgeSource.HUMAN_REVIEW
    if note:
        record.answer_verdict.human_note = note
    save_trace(record, trace_dir=trace_path.parent)
    print(f"✅ Trace 标定完成: {trace_path.name}")
    print(f"   结论变迁: {old_verdict} ({old_source}) -> {record.answer_verdict.verdict.value} (HUMAN_REVIEW)")
    if note:
        print(f"   人工备注: {note}")

def list_run_pending_cases(cases: list[dict[str, Any]], show_all: bool = False) -> None:
    """打印 run 中待复核或全量的用例清单。"""
    print("=" * 72)
    print("📋 【人工复核用例大盘】")
    print("=" * 72)
    pending_count = 0
    for idx, c in enumerate(cases, 1):
        cid = str(c.get("id", ""))
        av = c.get("answer_verdict", {})
        verdict = av.get("verdict", "UNKNOWN")
        source = av.get("judge_source", "AUTO_RULE")
        note = av.get("human_note", "")
        is_pending = verdict == AnswerVerdictKind.NEEDS_HUMAN_REVIEW.value
        if is_pending:
            pending_count += 1
        if is_pending or show_all:
            tag = "⏳ 待复核" if is_pending else "✔️ 已确认"
            print(f"[{idx:02d}] Case {cid:4s} | {tag} | 结论: {verdict:18s} | 来源: {source}")
            print(f"     问题: {c.get('question', '')[:50]}...")
            if note:
                print(f"     备注: {note}")
            print("-" * 72)
    print(f"\n📊 统计: 总用例 {len(cases)} 题，待人工复核项 {pending_count} 题。\n")

def apply_verdict_to_case(
    case_dict: dict[str, Any],
    verdict_str: str,
    note: str = "",
) -> None:
    """将人工审核结果应用到用例字典中。"""
    av = case_dict.setdefault("answer_verdict", {})
    av["verdict"] = verdict_str
    av["judge_source"] = JudgeSource.HUMAN_REVIEW.value
    if note:
        av["human_note"] = note
    # 同步通关状态 (通过仅限 CORRECT 或 EXPECTED_REFUSAL)
    case_dict["is_passed"] = verdict_str in (
        AnswerVerdictKind.CORRECT.value,
        AnswerVerdictKind.EXPECTED_REFUSAL.value,
    )


def interactive_review_run(run_data: dict[str, Any], run_path: Path) -> None:
    """交互式逐题审核待复核用例。"""
    cases = run_data.get("cases", [])
    pending = [
        c for c in cases
        if c.get("answer_verdict", {}).get("verdict") == AnswerVerdictKind.NEEDS_HUMAN_REVIEW.value
    ]
    if not pending:
        print("🎉 太棒了！当前 Run 中没有任何待人工复核的题目 (NEEDS_HUMAN_REVIEW)。")
        return
    print(f"\n🚀 开始交互式复核流程，共 {len(pending)} 题需要人工审定。")
    print("输入快捷键: [c] 正确(CORRECT) | [f] 事实错误(FACTUAL_ERROR) | [s] 跳过 | [q] 退出并保存\n")
    modified = False
    for i, c in enumerate(pending, 1):
        cid = c.get("id")
        print("=" * 72)
        print(f"🔍 [{i}/{len(pending)}] 审核 Case {cid}: {c.get('question')}")
        print("-" * 72)
        print("🤖 【模型原始回答】:")
        print(c.get("answer", "").strip())
        print("-" * 72)
        ev = c.get("evidence_validity", {})
        print(
            f"📊 规则命中: 文件={'✅' if c.get('file_hit') else '❌'} | "
            f"符号={'✅' if c.get('symbol_hit') else '❌'} ｜ "
            f"引用有效: {ev.get('citations_valid', 0)}/{ev.get('citations_total', 0)}"
        )
        choice = input("\n👉 请裁决 [c=正确 / f=事实错误 / s=跳过 / q=退出并保存]: ").strip().lower()
        if choice == "q":
            print("⏹️ 提前退出复核。")
            break
        elif choice == "s":
            print("⏭️ 已跳过。")
            continue
        elif choice == "c":
            note = input("✍️ 人工审核备注 (可直接回车跳过): ").strip()
            apply_verdict_to_case(c, AnswerVerdictKind.CORRECT.value, note=note)
            modified = True
            print(f"✅ Case {cid} 已标定为 CORRECT (HUMAN_REVIEW)\n")
        elif choice == "f":
            note = input("✍️ 事实错误说明 (必填或简述): ").strip()
            apply_verdict_to_case(c, AnswerVerdictKind.FACTUAL_ERROR.value, note=note)
            modified = True
            print(f"❌ Case {cid} 已标定为 FACTUAL_ERROR (HUMAN_REVIEW)\n")
        else:
            print("⚠️ 无效输入，自动跳过。")
    if modified:
        _save_run_data(run_data, run_path)


def _save_run_data(run_data: dict[str, Any], run_path: Path) -> None:
    """重新计算大盘并写回 Run JSON。"""
    cases = run_data.get("cases", [])
    if cases:
        passed_count = sum(1 for c in cases if c.get("is_passed"))
        total = len(cases)
        meta = run_data.setdefault("meta", {})
        meta["pass_rate"] = (passed_count / total * 100)
    with open(run_path, "w", encoding="utf-8") as f:
        json.dump(run_data, f, ensure_ascii=False, indent=2)
    print(f"💾 复核结果已成功写回: {run_path}")
def main() -> None:
    parser = argparse.ArgumentParser(description="人工复核标定工具（Human Review）")
    parser.add_argument("--run", type=str, default=None, help="评测 Run JSON 文件路径")
    parser.add_argument("--trace", type=str, default=None, help="单个 Trace JSON 文件路径")
    parser.add_argument("--list", "-l", action="store_true", help="列出待复核的题目")
    parser.add_argument("--all", action="store_true", help="配合 --list 展示全量用例")
    parser.add_argument("--interactive", "-i", action="store_true", help="进入交互式逐题审核模式")
    parser.add_argument("--case", "-c", type=str, default=None, help="指定需要标定的 Case ID")
    parser.add_argument(
        "--verdict",
        type=str,
        choices=[v.value for v in AnswerVerdictKind],
        default=None,
        help="目标定性结论",
    )
    parser.add_argument("--note", type=str, default="", help="人工复核意见备注")
    args = parser.parse_args()
    # 1. 单个 Trace 处理
    if args.trace:
        if not args.verdict:
            print("⚠️ 标定 Trace 文件时必须通过 --verdict 指定结论，如 --verdict CORRECT")
            return
        review_single_trace(Path(args.trace), verdict_str=args.verdict, note=args.note)
        return
    # 2. 评测 Run 处理
    if args.run:
        run_path = Path(args.run).resolve()
        if not run_path.exists():
            print(f"❌ 找不到 Run 文件: {run_path}")
            return
        with open(run_path, "r", encoding="utf-8") as f:
            run_data = json.load(f)
        cases = run_data.get("cases", [])
        if args.list:
            list_run_pending_cases(cases, show_all=args.all)
            return
        if args.interactive:
            interactive_review_run(run_data, run_path)
            return
        if args.case:
            if not args.verdict:
                print(f"⚠️ 请提供为 Case {args.case} 赋予的结论 (--verdict)")
                return
            target = next((c for c in cases if str(c.get("id")) == str(args.case)), None)
            if not target:
                print(f"❌ 在 {args.run} 中未找到 Case ID 为 {args.case} 的记录")
                return
            apply_verdict_to_case(target, args.verdict, note=args.note)
            _save_run_data(run_data, run_path)
            print(f"✅ Case {args.case} 标定成功为 {args.verdict} (HUMAN_REVIEW)")
            return
    print("⚠️ 请提供有效参数。输入 python scripts/review_verdict.py --help 查看用法。")
if __name__ == "__main__":
    main()

