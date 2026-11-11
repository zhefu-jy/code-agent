"""判卷内核回归测试（纯离线：不加载向量库、不调用大模型）。

这些用例锁的是 Report-006 / Report-007 里真实发生过的判卷错误：

1. Case 014/017/019 的"模型明说答不了，却判通过"（假阳性）；
2. Case 006 的"局部免责被当成整题拒答"（假阴性）；
3. 边界题收到空回答被判通过（空回答不是拒答）；
4. file_hit 用未做物理校验的引用，导致"引用一个不存在的文件"也算命中期望文件。

注意：本文件刻意不使用 pytest 的 tmp_path —— 部分受限环境下
`mkdir(mode=0o700)` 创建出来的临时目录不可写（Windows + 沙箱），
这里直接引用仓库内真实文件做物理校验对象，既不依赖临时目录也更有代表性。
"""

from pathlib import Path

import pytest

from code_agent.core.citation import is_full_refusal
from code_agent.core.judge import CaseSpec, case_spec_from_mapping, judge_answer

REPO_ROOT = Path(__file__).resolve().parent.parent


# --------------------------------------------------------------------------
# 拒答分级：全局拒答 vs 局部免责
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        # 空回答不是拒答：那是"没作答"，必须由调用方单独判失败
        ("", False),
        ("   \n  ", False),
        ("无依据。", True),
        ("**结论：无依据。**", True),
        # Markdown 加粗曾经把前缀匹配打穿，让 014 这类拒答漏网
        ("根据提供的参考资料，**无法确定** `_CITATION_PATTERN` 的具体正则表达式内容。", True),
        # 换了句式的"答不了"：017 的原始形态
        ("根据参考资料，通过 `DEFAULT_IGNORE_DIRS` 排除目录，但未给出该常量内容，因此无法确定默认排除了哪些具体目录。", True),
        # 局部免责：只对某个子问题说明无依据，整题仍然作答了
        ("**结论：无法确认下划线命名的处理方式。**\n\n函数通过正则拆分驼峰 [src/x.py:1-2]。", False),
        ("正常回答，正文里没有任何免责措辞 [src/x.py:1-2]。", False),
    ],
)
def test_is_full_refusal(answer: str, expected: bool) -> None:
    assert is_full_refusal(answer) is expected


# --------------------------------------------------------------------------
# 判卷：真实回答的回归（路径指向仓库内真实文件，保证物理校验通过）
# --------------------------------------------------------------------------

def test_scoped_hedge_does_not_veto_correct_answer() -> None:
    """Case 006：答案对驼峰/下划线都做了说明，末尾一句局部免责不该判死

    注意：这里没点名 `tokenize_code`，而 006 的 YAML 目前仍把它列为 expected_symbols，
    所以整题仍会因"未点名符号"判失败（已知缺口，见复盘结论）。
    本用例只锁「局部免责不致命」这一条判卷语义。
    """
    answer = (
        "**结论：参考资料仅能确认驼峰命名的处理方式，无法确认下划线命名的处理方式。**\n\n"
        "函数通过 `re.sub(r'([a-z])([A-Z])', ...)` 在大小写交界插入空格来拆分驼峰"
        "[src/code_agent/index/bm25_index.py:27-29]。\n\n"
        "下划线则因为 `[a-zA-Z0-9]+` 不含下划线，被当作分隔符丢弃"
        "[src/code_agent/index/bm25_index.py:28-29]。\n\n"
        "因此关于下划线命名的设计依据：**无依据**。"
    )
    spec = CaseSpec(
        id="006",
        expected_files=("src/code_agent/index/bm25_index.py",),
        expected_symbols=("tokenize_code",),
    )
    verdict = judge_answer(spec, answer, base_dir=REPO_ROOT)
    assert verdict.refusal == "scoped"
    assert verdict.file_hit
    # 只有"未点名符号"这一个原因，不能出现"模型声明无依据拒答"
    assert verdict.fail_reasons == ["未命中期望关键符号 ['tokenize_code']"]

    # 点名了符号就把最后一块补齐
    mentioned = judge_answer(
        spec, "`tokenize_code` " + answer, base_dir=REPO_ROOT
    )
    assert mentioned.passed, mentioned.fail_reasons


def test_unanswered_question_fails_even_if_symbols_mentioned() -> None:
    """Case 017：模型明说"无法确定排除了哪些目录"，只是在讨论别的类时提到目录名"""
    answer = (
        "根据参考资料，`BM25CodeIndex.build_from_directory` 通过 `DEFAULT_IGNORE_DIRS` 常量排除目录，"
        "但参考资料中未给出该常量的具体内容，因此无法确定默认排除了哪些具体目录。\n\n"
        "补充说明：同仓库中 `VectorIndex` 使用了一个内联的 `ignore_dirs` 集合，"
        "包含 `.git`、`.idea`、`__pycache__`，但这是向量索引的独立实现"
        "[src/code_agent/index/bm25_index.py:90-95]。"
    )
    spec = CaseSpec(
        id="017",
        expected_files=("src/code_agent/index/bm25_index.py",),
        expected_symbols=(".git", "__pycache__"),
    )
    verdict = judge_answer(spec, answer, base_dir=REPO_ROOT)
    assert verdict.refusal == "global"
    assert not verdict.passed


def test_bold_refusal_is_caught() -> None:
    """Case 019：`**结论：无依据。**` 曾被 allow_partial_refusal 硬编码放行"""
    answer = (
        "根据提供的参考资料，**无法确定** `has_no_evidence` 具体检查哪些关键词。\n\n"
        "现有片段仅显示该函数通过 `_NO_EVIDENCE_PATTERN` 匹配"
        "[src/code_agent/core/citation.py:91-93]。\n\n**结论：无依据。**"
    )
    spec = CaseSpec(
        id="019",
        expected_files=("src/code_agent/core/citation.py",),
        expected_symbols=("has_no_evidence",),
    )
    verdict = judge_answer(spec, answer, base_dir=REPO_ROOT)
    assert verdict.refusal == "global"
    assert not verdict.passed


def test_cross_module_all_mode_requires_every_file() -> None:
    """Case 029：跨源题只引了文档、没引源码，不算通过"""
    answer = (
        "ADR-001 修订结论提到 `stream_ask` 因平均池化被稀释漏检"
        "[docs/ADR-001-技术选型.md:44-45]。"
    )
    spec = CaseSpec(
        id="029",
        expected_files=("docs/ADR-001-技术选型.md", "src/code_agent/core/model.py"),
        expected_symbols=("stream_ask",),
        match_mode="all",
    )
    verdict = judge_answer(spec, answer, base_dir=REPO_ROOT)
    assert not verdict.file_hit
    assert "src/code_agent/core/model.py" in verdict.missing_files
    assert not verdict.passed


def test_cross_module_any_mode_is_lenient() -> None:
    """any 模式下命中任一期望文件即可（与 all 模式形成对照）"""
    answer = "只答了一半 [docs/ADR-001-技术选型.md:44-45]。"
    spec = CaseSpec(
        id="t-any",
        expected_files=("docs/ADR-001-技术选型.md", "src/code_agent/core/model.py"),
        match_mode="any",
    )
    verdict = judge_answer(spec, answer, base_dir=REPO_ROOT)
    assert verdict.file_hit
    assert verdict.passed


def test_file_hit_ignores_physically_invalid_citations() -> None:
    """引用一个不存在的文件，不能算"命中期望文件"（旧实现只看正则提取结果）"""
    answer = "见 [src/code_agent/core/schemas.py:1-3] 与 [src/code_agent/core/nope.py:1-3]。"
    spec = CaseSpec(id="t-bad", expected_files=("src/code_agent/core/nope.py",))
    verdict = judge_answer(spec, answer, base_dir=REPO_ROOT)
    assert verdict.citations_total == 2
    assert verdict.citations_valid == 1
    assert not verdict.file_hit
    assert not verdict.passed


# --------------------------------------------------------------------------
# 边界题（虚构功能）
# --------------------------------------------------------------------------

def test_boundary_case_passes_only_with_refusal() -> None:
    spec = CaseSpec(id="t-boundary", is_unanswerable=True)
    refused = judge_answer(spec, "无依据。项目中不存在该类实现。", base_dir=REPO_ROOT)
    assert refused.passed

    hallucinated = judge_answer(
        spec, "该函数定义在 [src/code_agent/core/schemas.py:1-3]，实现是 blah。", base_dir=REPO_ROOT
    )
    assert not hallucinated.passed


def test_boundary_case_rejects_empty_answer() -> None:
    """空回答曾被 is_full_refusal("")==True 判成"已拒答 → 通过\""""
    verdict = judge_answer(CaseSpec(id="t-empty", is_unanswerable=True), "", base_dir=REPO_ROOT)
    assert not verdict.passed
    assert "回答为空" in "；".join(verdict.fail_reasons)


# --------------------------------------------------------------------------
# 用例元数据解析（判卷规则必须数据化，不允许代码里按 id 写特例）
# --------------------------------------------------------------------------

def test_case_spec_from_mapping_reads_data_driven_flags() -> None:
    spec = case_spec_from_mapping(
        {
            "id": "999",
            "expected_files": ["a.py"],
            "expected_symbols": ["Foo"],
            "is_unanswerable": True,
            "allow_partial_refusal": True,
            "match_mode": "all",
        }
    )
    assert spec.id == "999"
    assert spec.expected_files == ("a.py",)
    assert spec.expected_symbols == ("Foo",)
    assert spec.is_unanswerable is True
    assert spec.allow_partial_refusal is True
    assert spec.match_mode == "all"


def test_no_case_id_hardcoded_exception_in_loader() -> None:
    """eval 用例加载器不得再出现按 case_id 打补丁的逻辑"""
    source = (REPO_ROOT / "eval" / "run_eval.py").read_text(encoding="utf-8")
    assert 'case_id == "019"' not in source
