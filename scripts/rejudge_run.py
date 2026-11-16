"""对历史 run JSON 做离线重判（不调用大模型、不加载向量库、不烧 Token）。

用途：判卷口径一改，就必然要回答一个问题——"上次那批回答，按新口径到底是什么结果？"
如果每次都要重烧 30 次 API 才能知道，就没人敢改口径，判卷里的洞会长期留着
（Report-006 把拒答判成通过、Report-007 把 017 的"查不到"判成通过，都是这么来的）。

用法：
    python scripts/rejudge_run.py --run eval/runs/run-007.json \
        --report eval/runs/rejudge-007.md
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import yaml

# Windows 控制台默认 GBK，直接 print emoji 会炸；统一转 utf-8
if sys.platform == "win32":
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")

_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from code_agent.core.judge import CaseSpec, case_spec_from_mapping, judge_answer  # noqa: E402


def load_specs(cases_dir: Path) -> dict[str, CaseSpec]:
    """从 eval/cases/*.yaml 读取用例元数据，按 id 建索引"""
    specs: dict[str, CaseSpec] = {}
    for file_path in sorted(cases_dir.rglob("*.yaml")):
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        spec = case_spec_from_mapping(data, fallback_id=file_path.stem)
        specs[spec.id] = spec
    return specs


def load_run_cases(run_path: Path) -> list[dict[str, Any]]:
    with open(run_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [c for c in (data.get("cases") or []) if isinstance(c, dict)]


def main() -> None:
    parser = argparse.ArgumentParser(description="历史 run 离线重判（当前判卷口径）")
    parser.add_argument("--run", required=True, help="历史 run JSON 路径")
    parser.add_argument("--cases", default=str(_ROOT / "eval" / "cases"), help="用例目录")
    parser.add_argument("--report", default=None, help="输出 Markdown 对照报告路径")
    parser.add_argument("--base-dir", default=str(_ROOT), help="引用物理校验的基准目录")
    args = parser.parse_args()

    run_path = Path(args.run)
    specs = load_specs(Path(args.cases))
    raw_cases = load_run_cases(run_path)
    base_dir = Path(args.base_dir)

    rows: list[dict[str, Any]] = []
    for item in raw_cases:
        case_id = str(item.get("id", ""))
        spec = specs.get(case_id)
        if spec is None:
            print(f"⚠️ 用例 {case_id} 在 {args.cases} 中找不到对应 YAML，跳过")
            continue

        verdict = judge_answer(spec, str(item.get("answer") or ""), base_dir=base_dir)
        rows.append({
            "id": case_id,
            "type": str(item.get("type") or ""),
            "old_passed": bool(item.get("is_passed")),
            "new_passed": verdict.passed,
            "refusal": verdict.refusal,
            "reasons": verdict.fail_reasons,
        })

    if not rows:
        print("没有可重判的用例")
        return

    total = len(rows)
    old_pass = sum(1 for r in rows if r["old_passed"])
    new_pass = sum(1 for r in rows if r["new_passed"])
    flipped_to_pass = [r["id"] for r in rows if r["new_passed"] and not r["old_passed"]]
    flipped_to_fail = [r["id"] for r in rows if r["old_passed"] and not r["new_passed"]]

    lines: list[str] = []
    lines.append("# 历史 Run 离线重判对照报告 (Rejudge Diff)")
    lines.append("")
    lines.append(
        f"> 数据源：`{run_path.as_posix()}` ｜ 判卷口径：`src/code_agent/core/judge.py` ｜ 未调用大模型、未消耗 Token"
    )
    lines.append("")
    lines.append("## 一、总体差异")
    lines.append("")
    lines.append("| 口径 | 通关数 | 通关率 |")
    lines.append("| :--- | :---: | :---: |")
    lines.append(f"| 旧判卷 | {old_pass}/{total} | {old_pass / total * 100:.1f}% |")
    lines.append(f"| 新判卷 | {new_pass}/{total} | {new_pass / total * 100:.1f}% |")
    lines.append("")
    lines.append(f"- 由 ❌ 翻为 ✅：{', '.join('`' + i + '`' for i in flipped_to_pass) or '无'}")
    lines.append(f"- 由 ✅ 翻为 ❌：{', '.join('`' + i + '`' for i in flipped_to_fail) or '无'}")
    lines.append("")
    lines.append('> 翻为 ❌ 通常不是"系统变差"，而是旧判卷把「模型拒答」或「只答了一半」误判成了通过。')
    lines.append("")
    lines.append("## 二、逐题对照")
    lines.append("")
    lines.append("| ID | 题型 | 旧判定 | 新判定 | 拒答分级 | 新口径失败原因 |")
    lines.append("| :--- | :--- | :---: | :---: | :---: | :--- |")
    for r in rows:
        old_mark = "✅" if r["old_passed"] else "❌"
        new_mark = "✅" if r["new_passed"] else "❌"
        change = " ⟵ 已翻转" if r["old_passed"] != r["new_passed"] else ""
        reason = "；".join(r["reasons"]) or "—"
        lines.append(
            f"| `{r['id']}` | {r['type']} | {old_mark} | {new_mark}{change} | {r['refusal']} | {reason} |"
        )
    lines.append("")

    report_text = "\n".join(lines)
    print(report_text)

    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(report_text, encoding="utf-8")
        print(f"\n📄 重判对照报告已生成至: {report_path}")


if __name__ == "__main__":
    main()