"""W10 S2 证据记录表（Evidence Ledger）与防死锁守护单元测试（纯离线）。"""

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from code_agent.core.trace import compute_content_hash
from code_agent.orchestration import (
    CitationCheckStatus,
    EvidenceKind,
    EvidenceLedger,
    InvestigationStatus,
    investigate,
    InvestigationBudget
)
from code_agent.tools.base import adapt_code_snippet
from code_agent.tools.code_reader import read_file_snippet
from code_agent.tools.search_tools import search_text
from code_agent.tools.symbol_tools import lookup_symbol
from code_agent.tools.base import adapt_symbol_lookup


class _BoundFakeModel:
    """模拟 model.bind_tools(...) 返回的可调用对象。"""

    def __init__(self, parent: "FakeChatModel") -> None:
        self._parent = parent

    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> Any:
        return self._parent._handle_invoke(input, is_bound=True)


class FakeChatModel:
    """按预设响应队列确定性回放模型行为的离线测试替身。"""

    def __init__(self, script: list[AIMessage]) -> None:
        self._script = list(script)
        self.invoke_history: list[tuple[bool, list[BaseMessage]]] = []

    def bind_tools(self, tools: Any, **kwargs: Any) -> _BoundFakeModel:
        return _BoundFakeModel(self)

    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> Any:
        return self._handle_invoke(input, is_bound=False)

    def _handle_invoke(self, messages: list[BaseMessage], is_bound: bool) -> Any:
        self.invoke_history.append((is_bound, list(messages)))
        if not self._script:
            raise AssertionError("FakeChatModel 响应队列已空")
        return self._script.pop(0)


def _make_ai_msg(
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> AIMessage:
    msg = AIMessage(content=content, tool_calls=tool_calls or [])
    msg.usage_metadata = {"input_tokens": 80, "output_tokens": 30, "total_tokens": 110}
    return msg


def _setup_multi_line_repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "\n".join(
            [
                "def compute_fee(amount: int) -> int:",
                "    base = amount + 5",
                "    if base > 100:",
                "        return 100",
                "    return base",
                "",
                "def checkout(amount: int) -> int:",
                "    return compute_fee(amount)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def test_ledger_records_only_returned_range_on_truncation(tmp_path: Path) -> None:
    """验证读取截断时仅登记实际 returned_range，引用未读区间会被标为 UNVERIFIED。"""
    repo = _setup_multi_line_repo(tmp_path)
    ledger = EvidenceLedger(revision="rev-1")

    res = adapt_code_snippet(
        read_file_snippet(repo, "app.py", start_line=1, end_line=8, max_lines=3)
    )
    items = ledger.record_tool_result(res)

    assert len(items) == 1
    item = items[0]
    assert item.id == "E1"
    assert item.kind == EvidenceKind.SNIPPET_BODY
    assert item.is_truncated is True
    assert item.line_range == (1, 3)
    assert item.content_hash == compute_content_hash(item.content)

    # 仅读了 1-3 行，模型却引用第 5 行（未返回区间）和伪造编号 E9
    verification = ledger.verify_answer("折扣封顶逻辑见 [E1] [E9] [app.py:4-5]。")
    assert verification.bound_evidence_ids == ("E1",)
    assert verification.unknown_evidence_ids == ("E9",)
    assert verification.unverified_count == 1
    assert verification.citation_checks[0].status == CitationCheckStatus.UNVERIFIED
    assert verification.has_unverified_claims is True


def test_ledger_merges_paginated_snippets_for_verification(tmp_path: Path) -> None:
    """验证分页续读的多段 SNIPPET_BODY 合并后，跨页引用可被核验为 VERIFIED_BODY。"""
    repo = _setup_multi_line_repo(tmp_path)
    ledger = EvidenceLedger(revision="rev-1")

    page1 = adapt_code_snippet(
        read_file_snippet(repo, "app.py", start_line=1, end_line=3, max_lines=3)
    )
    page2 = adapt_code_snippet(
        read_file_snippet(repo, "app.py", start_line=4, end_line=6, max_lines=3)
    )
    e1 = ledger.record_tool_result(page1)
    e2 = ledger.record_tool_result(page2)
    assert (e1[0].id, e2[0].id) == ("E1", "E2")

    verification = ledger.verify_answer("实现见 [E1] [E2] [app.py:2-5]。")
    assert verification.verified_count == 1
    assert verification.unverified_count == 0
    assert verification.has_unverified_claims is False
    assert verification.citation_checks[0].status == CitationCheckStatus.VERIFIED_BODY
    assert verification.citation_checks[0].matched_evidence_ids == ("E1", "E2")


def test_ledger_isolates_coordinate_evidence_from_body_evidence(tmp_path: Path) -> None:
    """验证 lookup_symbol 与 search_text 仅生成坐标/单行级证据，不等价于已读函数正文。"""
    repo = _setup_multi_line_repo(tmp_path)
    ledger = EvidenceLedger(revision="rev-1")

    sym_res = adapt_symbol_lookup(lookup_symbol("compute_fee", project_root=repo))
    txt_res = search_text(repo, "return 100")
    ledger.record_tool_result(sym_res)
    ledger.record_tool_result(txt_res)

    assert ledger.items[0].kind == EvidenceKind.SYMBOL_SIGNATURE
    assert ledger.items[1].kind == EvidenceKind.TEXT_MATCH

    verification = ledger.verify_answer("函数实现见 [E1] [app.py:1-5]，单行见 [E2] [app.py:4]。")
    assert verification.verified_count == 0
    assert verification.coordinate_only_count == 2
    assert all(
        chk.status == CitationCheckStatus.COORDINATE_ONLY
        for chk in verification.citation_checks
    )
    assert len(verification.warnings) == 2


def test_ledger_flags_naked_answer_missing_evidence_binding(tmp_path: Path) -> None:
    """验证账本已有证据但模型未绑定任何 E# 或行号时，必须触发 missing_evidence_binding。"""
    repo = _setup_multi_line_repo(tmp_path)
    ledger = EvidenceLedger(revision="rev-1")
    res = adapt_code_snippet(read_file_snippet(repo, "app.py", start_line=1, end_line=2))
    ledger.record_tool_result(res)

    verification = ledger.verify_answer("compute_fee 会把金额加 5。")
    assert verification.missing_evidence_binding is True
    assert verification.has_unverified_claims is True
    assert len(verification.warnings) == 1


def test_investigator_blocks_duplicate_tool_calls_with_loop_guard(tmp_path: Path) -> None:
    """验证防死锁守护会拦截相同参数（含不同键顺序）的重复调用，不重复执行工具。"""
    repo = _setup_multi_line_repo(tmp_path)
    model = FakeChatModel(
        [
            _make_ai_msg(
                tool_calls=[
                    {
                        "id": "d1",
                        "name": "read_file_snippet",
                        "args": {"file_path": "app.py", "start_line": 1, "end_line": 3},
                    }
                ]
            ),
            # 键顺序打乱但语义完全一致的重复调用
            _make_ai_msg(
                tool_calls=[
                    {
                        "id": "d2",
                        "name": "read_file_snippet",
                        "args": {"end_line": 3, "start_line": 1, "file_path": "app.py"},
                    }
                ]
            ),
            _make_ai_msg(content="已读取实现 [E1] [app.py:1-3]。"),
        ]
    )

    result = investigate(repo, "读取 compute_fee", model=model)

    assert result.status == InvestigationStatus.COMPLETED
    assert result.budget.tool_calls_used == 1  # 第 2 次被拦截，未实际消耗工具次数
    assert len(result.tool_records) == 2
    assert result.tool_records[0].ok is True
    assert result.tool_records[1].ok is False
    assert result.tool_records[1].error_code == "duplicate_tool_call"
    assert len(result.evidence_items) == 1
    assert result.evidence_verification is not None
    assert result.evidence_verification.verified_count == 1
    assert result.evidence_verification.has_unverified_claims is False

    # 确认拦截消息已通过 ToolMessage 回传模型
    _, last_msgs = model.invoke_history[-1]
    tool_msgs = [m for m in last_msgs if isinstance(m, ToolMessage)]
    assert "duplicate_tool_call" in str(tool_msgs[1].content)


def test_investigator_injects_pivot_hint_after_consecutive_empty_results(
    tmp_path: Path,
) -> None:
    """验证连续 2 次空命中后，在 ToolMessage 中注入思路切换提示。"""
    repo = _setup_multi_line_repo(tmp_path)
    model = FakeChatModel(
        [
            _make_ai_msg(
                tool_calls=[
                    {"id": "e1", "name": "lookup_symbol", "args": {"name": "nonexistent_a"}}
                ]
            ),
            _make_ai_msg(
                tool_calls=[
                    {"id": "e2", "name": "search_text", "args": {"pattern": "nonexistent_b"}}
                ]
            ),
            _make_ai_msg(
                tool_calls=[
                    {"id": "e3", "name": "lookup_symbol", "args": {"name": "compute_fee"}}
                ]
            ),
            _make_ai_msg(content="已定位符号坐标 [E1] [app.py:1-5]，未读完整正文。"),
        ]
    )

    result = investigate(
        repo,
        "查找不存在的符号后切换思路",
        model=model,
        budget=InvestigationBudget(max_turns=5, reserve_summary_turns=1),
    )

    assert result.status == InvestigationStatus.COMPLETED
    assert result.budget.tool_calls_used == 3

    _, final_msgs = model.invoke_history[-1]
    tool_msgs = [m for m in final_msgs if isinstance(m, ToolMessage)]
    payload_1 = json.loads(str(tool_msgs[0].content))
    payload_2 = json.loads(str(tool_msgs[1].content))
    payload_3 = json.loads(str(tool_msgs[2].content))

    assert "loop_guard_hint" not in payload_1
    assert "loop_guard_hint" in payload_2
    assert "思路切换提示" in payload_2["loop_guard_hint"]
    # 第 3 次命中有效结果，不再注入切换提示且仅保留本次证据 E1
    assert "loop_guard_hint" not in payload_3
    assert payload_3["evidence_ids"] == ["E1"]


def test_tool_message_payload_strips_duplicate_text_while_ledger_keeps_raw(
    tmp_path: Path,
) -> None:
    """验证喂给模型的 ToolMessage 剥离重复内容以省 Token，但账本仍保留原始 content 与哈希。"""
    repo = _setup_multi_line_repo(tmp_path)
    model = FakeChatModel(
        [
            _make_ai_msg(
                tool_calls=[
                    {
                        "id": "s1",
                        "name": "search_text",
                        "args": {"pattern": "return 100"},
                    },
                    {
                        "id": "r1",
                        "name": "read_file_snippet",
                        "args": {"file_path": "app.py", "start_line": 1, "end_line": 5},
                    },
                ]
            ),
            _make_ai_msg(content="实现见 [E2] [app.py:1-5]。"),
        ]
    )

    result = investigate(repo, "检查封顶逻辑", model=model)
    _, final_msgs = model.invoke_history[-1]
    tool_msgs = [m for m in final_msgs if isinstance(m, ToolMessage)]

    search_payload = json.loads(str(tool_msgs[0].content))
    read_payload = json.loads(str(tool_msgs[1].content))

    assert "matches" in search_payload["data"]
    assert "formatted_preview" not in search_payload["data"]
    assert "numbered_content" in read_payload["data"]
    assert "content" not in read_payload["data"]

    # 账本仍保留原始未加行号的 content 及其准确哈希
    snippet_item = result.evidence_items[1]
    assert snippet_item.kind == EvidenceKind.SNIPPET_BODY
    assert "def compute_fee" in snippet_item.content
    assert snippet_item.content_hash == compute_content_hash(snippet_item.content)