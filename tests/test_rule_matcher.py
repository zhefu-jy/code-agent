"""W11 S2 规则与代码候选实现语义绑定单元测试（纯离线）。"""
from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from code_agent.analyzer.symbol_index import SymbolIndex, build_symbol_index
from code_agent.index.bm25_index import CodeChunk
from code_agent.index.hybrid_retriever import HybridSearchResult
from code_agent.spec import (
    CandidateSource,
    InterventionAction,
    RuleConfirmationStatus,
    RuleSourceLocation,
    SpecRule,
    add_candidate,
    build_rule_query,
    extract_rule_identifiers,
    format_candidate_map_report,
    interactive_review_candidates,
    match_rule_to_candidates,
    match_rules_to_candidates,
    remove_candidate,
)


class FakeHybridRetriever:
    """按预设 HybridSearchResult 或 (CodeChunk, score) 回放的离线检索替身。"""

    def __init__(self, items: Sequence[Any]) -> None:
        self._items = list(items)
        self.queries: list[tuple[str, int]] = []

    def search(self, query: str, top_k: int = 3) -> list[Any]:
        self.queries.append((query, top_k))
        return self._items[:top_k]


def _build_mini_project(tmp_path: Path) -> tuple[Path, SymbolIndex]:
    """在临时目录构造带 src 布局的微型测试工程并构建真实 SymbolIndex。"""
    pkg_dir = tmp_path / "src" / "demo"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "__init__.py").write_text("", encoding="utf-8")

    (pkg_dir / "client.py").write_text(
        "\n".join(
            [
                "DEFAULT_TIMEOUT = 10",
                "",
                "def fetch_data(timeout: int = DEFAULT_TIMEOUT) -> str:",
                "    return f'timeout={timeout}'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (pkg_dir / "runner.py").write_text(
        "\n".join(
            [
                "import subprocess",
                "",
                "def run_shell(cmd: list[str]) -> int:",
                "    completed = subprocess.run(cmd, check=False)",
                "    return completed.returncode",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path, build_symbol_index(tmp_path)


def _make_rule(
    rule_id: str,
    *,
    title: str,
    requirement: str,
    target_scope: str,
    conditions: list[str] | None = None,
    status: RuleConfirmationStatus = RuleConfirmationStatus.CONFIRMED,
) -> SpecRule:
    """构造测试用 SpecRule。"""
    return SpecRule(
        rule_id=rule_id,
        title=title,
        requirement=requirement,
        target_scope=target_scope,
        conditions=conditions or [],
        source_location=RuleSourceLocation(
            file_path="docs/spec.md",
            start_line=1,
            end_line=5,
            section_path="规范",
        ),
        status=status,
    )


def test_dual_source_fusion_merges_and_filters_doc_chunks(tmp_path: Path) -> None:
    """验证双路命中合并（含绝对路径归一化与 1 行偏差对齐）及文档切块过滤。"""
    project_root, symbol_index = _build_mini_project(tmp_path)
    client_abs = str((project_root / "src" / "demo" / "client.py").resolve())
    runner_abs = str((project_root / "src" / "demo" / "runner.py").resolve())

    doc_chunk = CodeChunk(
        file_path=str((project_root / "docs" / "spec.md").resolve()),
        start_line=1,
        end_line=10,
        content="fetch_data 超时上限为 10 秒",
        tokens=[],
        source_type="doc",
        title_path="规范 > 超时",
    )
    client_chunk = CodeChunk(
        file_path=client_abs,
        start_line=2,
        end_line=4,
        content="def fetch_data(timeout: int = DEFAULT_TIMEOUT) -> str:",
        tokens=[],
        symbol_name="fetch_data",
        source_type="code",
    )
    runner_chunk = CodeChunk(
        file_path=runner_abs,
        start_line=3,
        end_line=5,
        content="def run_shell(cmd: list[str]) -> int:",
        tokens=[],
        symbol_name="run_shell",
        source_type="code",
    )
    fake_retriever = FakeHybridRetriever(
        [
            HybridSearchResult(chunk=doc_chunk, score=0.99, source="BOTH"),
            HybridSearchResult(chunk=client_chunk, score=0.80, source="BOTH"),
            HybridSearchResult(chunk=runner_chunk, score=0.45, source="VECTOR"),
        ]
    )

    rule = _make_rule(
        "R1",
        title="外部请求超时控制",
        requirement="fetch_data 超时不得超过 10 秒",
        target_scope="client.fetch_data",
    )
    entry = match_rule_to_candidates(
        rule,
        project_root=project_root,
        symbol_index=symbol_index,
        retriever=fake_retriever,
        top_k=5,
    )

    # 文档切块必须被过滤掉
    assert all(not c.file_path.endswith(".md") for c in entry.candidates)
    # 排第一的应是双路合并命中的 fetch_data，且路径归一化为相对 POSIX 路径
    top = entry.candidates[0]
    assert top.candidate_id == "C1"
    assert top.file_path == "src/demo/client.py"
    assert top.start_line == 2 and top.end_line == 4
    assert set(top.sources) == {
        CandidateSource.SYMBOL_INDEX,
        CandidateSource.HYBRID_RETRIEVAL,
    }
    assert top.score == 1.65  # exact_name(0.85) + retriever(0.80)
    # 仅检索命中的 runner.py 也应保留
    runner_hits = [c for c in entry.candidates if c.file_path == "src/demo/runner.py"]
    assert len(runner_hits) == 1
    assert runner_hits[0].sources == [CandidateSource.HYBRID_RETRIEVAL]


def test_match_rules_enforces_active_gate_and_handles_empty_hits(tmp_path: Path) -> None:
    """验证默认仅处理已激活规则，且 0 命中规则如实留空并告警。"""
    project_root, symbol_index = _build_mini_project(tmp_path)
    rules = [
        _make_rule(
            "R1",
            title="未确认草稿",
            requirement="fetch_data 必须校验",
            target_scope="client",
            status=RuleConfirmationStatus.DRAFT,
        ),
        _make_rule(
            "R2",
            title="已跳过背景",
            requirement="说明性文字",
            target_scope="全仓",
            status=RuleConfirmationStatus.SKIPPED,
        ),
        _make_rule(
            "R3",
            title="不存在的量子上传器",
            requirement="QuantumUploader 必须加密传输",
            target_scope="QuantumUploader",
            status=RuleConfirmationStatus.CONFIRMED,
        ),
    ]

    cmap = match_rules_to_candidates(
        rules,
        project_root=project_root,
        symbol_index=symbol_index,
        retriever=FakeHybridRetriever([]),
    )

    assert len(cmap.entries) == 1
    assert cmap.entries[0].rule.rule_id == "R3"
    assert cmap.entries[0].candidates == []
    assert any("R1" in w and "draft" in w for w in cmap.warnings)
    assert any("R2" in w and "skipped" in w for w in cmap.warnings)
    assert any("R3" in w and "未找到候选代码" in w for w in cmap.warnings)

    metrics = cmap.summary_metrics()
    assert metrics.total_rules == 1
    assert metrics.auto_matched_rules == 0
    assert metrics.auto_empty_rules == 1


def test_raw_tuple_retriever_normalizes_unbounded_scores(tmp_path: Path) -> None:
    """验证纯 BM25 等 (CodeChunk, score) 二元组检索器能过滤零分并归一分值。"""
    project_root, symbol_index = _build_mini_project(tmp_path)
    c1 = CodeChunk(
        file_path="src/demo/client.py",
        start_line=3,
        end_line=4,
        content="def fetch_data...",
        tokens=[],
        symbol_name="fetch_data",
        source_type="code",
    )
    c2 = CodeChunk(
        file_path="src/demo/runner.py",
        start_line=3,
        end_line=5,
        content="def run_shell...",
        tokens=[],
        symbol_name="run_shell",
        source_type="code",
    )
    c3 = CodeChunk(
        file_path="src/demo/__init__.py",
        start_line=1,
        end_line=1,
        content="",
        tokens=[],
        source_type="code",
    )
    fake_bm25 = FakeHybridRetriever([(c1, 12.0), (c2, 6.0), (c3, 0.0)])

    rule = _make_rule(
        "R1",
        title="中文语义查询",
        requirement="要求使用安全子进程调用",
        target_scope="命令执行层",
    )
    entry = match_rule_to_candidates(
        rule,
        project_root=project_root,
        symbol_index=symbol_index,
        retriever=fake_bm25,
        top_k=5,
    )

    assert len(entry.candidates) == 2
    assert entry.candidates[0].score == 1.0
    assert entry.candidates[1].score == 0.5


def test_human_add_and_remove_keeps_auto_metrics_isolated(tmp_path: Path) -> None:
    """验证人工增删采用软删除+独立介入日志，不混淆自动定位与人工辅助成绩。"""
    project_root, symbol_index = _build_mini_project(tmp_path)
    r1 = _make_rule(
        "R1",
        title="超时常量与请求函数",
        requirement="DEFAULT_TIMEOUT 与 fetch_data 不得超过 10 秒",
        target_scope="client",
    )
    r2 = _make_rule(
        "R2",
        title="禁用不安全命令执行",
        requirement="禁止调用 os.system",
        target_scope="命令执行模块",
    )

    cmap = match_rules_to_candidates(
        [r1, r2],
        project_root=project_root,
        symbol_index=symbol_index,
        retriever=FakeHybridRetriever([]),
    )

    entry_r1 = cmap.get_entry("R1")
    entry_r2 = cmap.get_entry("R2")
    assert entry_r1 is not None and entry_r2 is not None
    auto_r1_count = len(entry_r1.auto_candidates)
    assert auto_r1_count >= 2
    assert len(entry_r2.auto_candidates) == 0

    # 人工排除 R1 的第 2 个自动候选，并验证重复排除幂等
    remove_candidate(cmap, "R1", "C2", reason="与超时逻辑无关")
    remove_candidate(cmap, "R1", "C2", reason="重复调用不应多记日志")
    assert len(entry_r1.interventions) == 1
    assert entry_r1.interventions[0].action == InterventionAction.REMOVE

    # 人工给空命中的 R2 补录 runner.py 的 run_shell（行号留空，由 symbol_index 自动推断）
    added = add_candidate(
        cmap,
        "R2",
        file_path="src/demo/runner.py",
        symbol_name="run_shell",
        reason="人工补充命令执行入口",
        symbol_index=symbol_index,
    )
    assert added.candidate_id == "C1"
    assert added.start_line == 3 and added.end_line == 5
    assert added.is_human_added and not added.is_auto_discovered

    # 核心断言：双口径指标彻底分离
    metrics = cmap.summary_metrics()
    assert metrics.total_rules == 2
    assert metrics.auto_matched_rules == 1          # R2 仍是纯自动未命中！
    assert metrics.auto_empty_rules == 1
    assert metrics.auto_total_candidates == auto_r1_count
    assert metrics.human_intervened_rules == 2
    assert metrics.human_added_total == 1
    assert metrics.human_removed_total == 1
    assert metrics.effective_matched_rules == 2     # 人工补录后最终生效覆盖 2/2
    assert metrics.effective_total_candidates == auto_r1_count - 1 + 1

    report = format_candidate_map_report(cmap)
    assert "自主定位成绩（纯自动，不含人工补充）" in report
    assert "人工辅助与最终生效（单独计列，不混入自主成绩）" in report
    assert "已人工排除" in report


def test_interactive_review_candidates_add_remove_and_all(tmp_path: Path) -> None:
    """验证终端人工复核环支持排除容错、新增候选、放行本条与 all 放行剩余。"""
    project_root, symbol_index = _build_mini_project(tmp_path)
    r1 = _make_rule(
        "R1",
        title="超时检查",
        requirement="检查 fetch_data 超时",
        target_scope="client",
    )
    r2 = _make_rule(
        "R2",
        title="子进程检查",
        requirement="检查 run_shell 调用",
        target_scope="runner",
    )
    cmap = match_rules_to_candidates(
        [r1, r2],
        project_root=project_root,
        symbol_index=symbol_index,
        retriever=FakeHybridRetriever([]),
    )

    scripted_inputs = iter(
        [
            "r", "C99", "无效编号测试",       # 容错分支：不存在的候选编号
            "r", "C1", "先排除自动候选",      # 正常排除 C1
            "a", "src/demo/runner.py", "run_shell", "3-5", "手动补充",  # 正常新增
            "",                             # 确认 R1
            "all",                          # 放行全部剩余（R2）
        ]
    )
    printed: list[str] = []

    reviewed = interactive_review_candidates(
        cmap,
        symbol_index=symbol_index,
        input_fn=lambda _prompt: next(scripted_inputs),
        print_fn=printed.append,
    )

    e1 = reviewed.get_entry("R1")
    assert e1 is not None
    assert len(e1.human_removed_candidates) == 1
    assert len(e1.human_added_candidates) == 1
    assert any("未找到候选编号" in line for line in printed)
    assert any("已放行全部剩余规则" in line for line in printed)


def test_extract_rule_identifiers_and_build_query() -> None:
    """验证标识符抽取的点分拆词、停用词过滤与查询拼装。"""
    rule = _make_rule(
        "R1",
        title="BuildEngine 构建超时约束",
        requirement="must not use os.system in BuildEngine.build",
        target_scope="minisite.engine",
        conditions=["超时 <= 10s", "except ConnectionError"],
    )

    identifiers = extract_rule_identifiers(rule)
    assert "BuildEngine" in identifiers
    assert "os.system" in identifiers
    assert "BuildEngine.build" in identifiers
    assert "ConnectionError" in identifiers
    # 停用词与过短词（os / in）应被过滤
    lowered = {t.lower() for t in identifiers}
    assert "must" not in lowered
    assert "os" not in lowered

    query = build_rule_query(rule)
    assert "BuildEngine 构建超时约束" in query
    assert "minisite.engine" in query
    assert "ConnectionError" in query


def test_retriever_filters_low_score_vector_tail_noise(tmp_path: Path) -> None:
    """验证默认相关性门槛过滤纯向量 Min-Max 拉伸出的 <=0.30 低信噪比尾噪。"""
    project_root, symbol_index = _build_mini_project(tmp_path)
    noise_1 = CodeChunk(
        file_path="src/demo/__init__.py",
        start_line=1,
        end_line=1,
        content="",
        tokens=[],
        source_type="code",
    )
    noise_2 = CodeChunk(
        file_path="src/demo/runner.py",
        start_line=1,
        end_line=5,
        content="import subprocess",
        tokens=[],
        source_type="code",
    )
    fake_retriever = FakeHybridRetriever(
        [
            HybridSearchResult(chunk=noise_1, score=0.30, source="VECTOR"),
            HybridSearchResult(chunk=noise_2, score=0.22, source="VECTOR"),
        ]
    )
    rule = _make_rule(
        "R3",
        title="禁用不存在的上传器",
        requirement="严禁调用 S3Uploader",
        target_scope="部署上传模块",
    )

    cmap_default = match_rules_to_candidates(
        [rule],
        project_root=project_root,
        symbol_index=symbol_index,
        retriever=fake_retriever,
    )
    assert cmap_default.entries[0].candidates == []
    assert cmap_default.summary_metrics().auto_empty_rules == 1
    assert any("R3" in w and "未找到候选代码" in w for w in cmap_default.warnings)

    cmap_relaxed = match_rules_to_candidates(
        [rule],
        project_root=project_root,
        symbol_index=symbol_index,
        retriever=fake_retriever,
        min_retriever_score=0.2,
    )
    assert len(cmap_relaxed.entries[0].candidates) == 2
