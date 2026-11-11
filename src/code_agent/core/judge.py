"""判卷内核：把"判卷"从"检索与生成"里彻底剥出来。

存在的理由（W6 Report-006 / 007 复盘）：
判卷逻辑原先写在 `eval/run_eval.py` 的 `evaluate_single_case` 里，和 LLM 调用、
索引构建耦合在一起。结果是——改一次判卷口径就必须重烧 30 次 API 才能知道影响，
于是没人愿意改，口径一旦有洞（把拒答判成通过）就会长期留着。

本模块是纯函数：只吃「用例元数据 + 回答文本 + 基准目录」，
不导入 chromadb / langchain / 大模型，因此 `scripts/rejudge_run.py` 可以对
历史 run JSON 反复重判，零 Token 验证判卷口径的改动。

四个判定阶段（彼此独立记录，不允许互相塌陷）：
1. substance 作答实质：区分「全局拒答」「局部免责」「正常作答」；
2. evidence  引用举证：至少 1 处物理有效引用；
3. file      文件覆盖：按用例的 any / all 模式检查期望文件是否被**有效**引用命中；
4. symbols   符号覆盖：期望标识符必须在回答中被点名。
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Mapping

from code_agent.core.citation import (
    extract_citations,
    has_no_evidence,
    is_full_refusal,
    validate_citation,
)

RefusalKind = Literal["none", "scoped", "global"]
MatchMode = Literal["any", "all"]


@dataclass(frozen=True)
class CaseSpec:
    """判卷所需的用例元数据（与 eval/cases/*.yaml 一一对应，但不依赖 YAML 库）"""

    id: str
    expected_files: tuple[str, ...] = ()
    expected_symbols: tuple[str, ...] = ()
    is_unanswerable: bool = False
    # 允许"全局拒答但也点名了文件与符号"的回答通过（用于题目本身在讨论拒答机制的用例）。
    # 注意：这是数据化开关，不允许再在代码里按 case.id 写特例。
    allow_partial_refusal: bool = False
    match_mode: MatchMode = "any"


@dataclass
class Verdict:
    """一次判卷的结论与可解释证据"""

    passed: bool
    refusal: RefusalKind
    citations_total: int
    citations_valid: int
    file_hit: bool
    symbol_hit: bool
    missing_files: list[str] = field(default_factory=list)
    missing_symbols: list[str] = field(default_factory=list)
    fail_reasons: list[str] = field(default_factory=list)


def _normalize(path: str) -> str:
    return path.replace("\\", "/").strip().lower()


def path_matches(expected: str, cited: str) -> bool:
    """判断引用路径是否指向期望文件（容忍相对/绝对与前缀差异）"""
    exp = _normalize(expected)
    got = _normalize(cited)
    if not exp or not got:
        return False
    if exp == got:
        return True
    return got.endswith("/" + exp) or exp.endswith("/" + got) or got.endswith(exp)


def judge_answer(
    spec: CaseSpec,
    answer: str,
    base_dir: Path | str | None = None,
) -> Verdict:
    """对单条回答执行四阶段判定，返回可解释的结论。"""
    answer = (answer or "").strip()

    citations = extract_citations(answer) if answer else []
    valid_citations = [
        result.citation
        for result in (validate_citation(c, base_dir=base_dir) for c in citations)
        if result.is_valid
    ]

    # 拒答分级：全局拒答（整题答不了）/ 局部免责（某个子问题给不出依据）/ 正常作答
    if is_full_refusal(answer):
        refusal: RefusalKind = "global"
    elif has_no_evidence(answer):
        refusal = "scoped"
    else:
        refusal = "none"

    # 文件覆盖只用「物理校验通过」的引用：引用一个不存在的文件不能算命中期望文件
    cited_files = [c.file_path for c in valid_citations]
    missing_files = [
        expected
        for expected in spec.expected_files
        if not any(path_matches(expected, cited) for cited in cited_files)
    ]
    if not spec.expected_files:
        file_hit = True
    elif spec.match_mode == "all":
        file_hit = not missing_files
    else:
        file_hit = len(missing_files) < len(spec.expected_files)

    low_answer = answer.lower()
    missing_symbols = [sym for sym in spec.expected_symbols if sym.lower() not in low_answer]
    symbol_hit = not missing_symbols

    fail_reasons: list[str] = []

    if spec.is_unanswerable:
        # 边界题：必须显式声明无依据。空回答不算拒答（那是"没作答"）。
        if not answer:
            fail_reasons.append("回答为空")
        elif refusal == "none":
            fail_reasons.append("虚构问题未能成功拒答（回答中未出现任何无依据声明）")
        return Verdict(
            passed=not fail_reasons,
            refusal=refusal,
            citations_total=len(citations),
            citations_valid=len(valid_citations),
            file_hit=file_hit,
            symbol_hit=symbol_hit,
            missing_files=missing_files,
            missing_symbols=missing_symbols,
            fail_reasons=fail_reasons,
        )

    if not answer:
        fail_reasons.append("回答未完成或模型调用异常")
    elif refusal == "global" and not spec.allow_partial_refusal:
        fail_reasons.append("模型声明无依据拒答（检索未召回关键上下文或信息不足）")
    else:
        if len(citations) == 0:
            fail_reasons.append("回答未标注任何引用出处")
        elif len(valid_citations) == 0:
            fail_reasons.append("所有引用的行号均物理校验失败（存在行号漂移或假文件）")

        if not file_hit:
            fail_reasons.append(f"未命中期望文件 {list(spec.expected_files)}")
        if not symbol_hit:
            fail_reasons.append(f"未命中期望关键符号 {missing_symbols}")

    return Verdict(
        passed=not fail_reasons,
        refusal=refusal,
        citations_total=len(citations),
        citations_valid=len(valid_citations),
        file_hit=file_hit,
        symbol_hit=symbol_hit,
        missing_files=missing_files,
        missing_symbols=missing_symbols,
        fail_reasons=fail_reasons,
    )


def _as_str_tuple(raw: Any) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        return (raw,)
    return tuple(str(item) for item in raw)


def case_spec_from_mapping(data: Mapping[str, Any], fallback_id: str = "") -> CaseSpec:
    """把 YAML/JSON 里的一条用例记录转换为 CaseSpec（容错缺失字段）"""
    raw_mode = str(data.get("match_mode") or "any").strip().lower()
    match_mode: MatchMode = "all" if raw_mode == "all" else "any"
    return CaseSpec(
        id=str(data.get("id") or fallback_id),
        expected_files=_as_str_tuple(data.get("expected_files")),
        expected_symbols=_as_str_tuple(data.get("expected_symbols")),
        is_unanswerable=bool(data.get("is_unanswerable", False)),
        allow_partial_refusal=bool(data.get("allow_partial_refusal", False)),
        match_mode=match_mode,
    )