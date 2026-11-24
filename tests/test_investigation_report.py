"""W10 S3 三板块结构化证据报告渲染与 CLI /investigate 单元测试（纯离线）。"""

from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
import pytest

from code_agent.analyzer.symbol_index import build_symbol_index
from code_agent.cli import handle_investigate
from code_agent.orchestration import (
    REQUIRED_REPORT_SECTIONS,
    InvestigationBudget,
    audit_report_sections,
    format_investigation_report,
    investigate,
)


class _BoundFakeModel:
    """模拟 model.bind_tools(...) 返回对象。"""

    def __init__(self, parent: "FakeChatModel") -> None:
        self._parent = parent

    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> Any:
        return self._parent._handle_invoke(input, is_bound=True)


class FakeChatModel:
    """离线回放预设模型响应的测试替身。"""

    def __init__(self, script: list[AIMessage]) -> None:
        self._script = list(script)

    def bind_tools(self, tools: Any, **kwargs: Any) -> _BoundFakeModel:
        return _BoundFakeModel(self)

    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> Any:
        return self._handle_invoke(input, is_bound=False)

    def _handle_invoke(self, messages: list[BaseMessage], is_bound: bool) -> Any:
        if not self._script:
            raise AssertionError("FakeChatModel 响应队列已空")
        return self._script.pop(0)


def _make_ai_msg(
    content: str = "",
    tool_calls: list[dict[str, Any]] | None = None,
) -> AIMessage:
    msg = AIMessage(content=content, tool_calls=tool_calls or [])
    msg.usage_metadata = {"input_tokens": 90, "output_tokens": 45, "total_tokens": 135}
    return msg


def _setup_demo_repo(tmp_path: Path) -> Path:
    (tmp_path / "app.py").write_text(
        "\n".join(
            [
                "def compute_fee(amount: int) -> int:",
                "    return amount + 5",
                "",
                "def checkout(amount: int) -> int:",
                "    return compute_fee(amount)",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def test_audit_report_sections_tolerates_markdown_and_flags_missing() -> None:
    """验证板块校验兼容 Markdown 修饰符，并能检出缺失板块。"""
    compliant_text = "\n".join(
        [
            "### 【已确认代码事实】",
            "- compute_fee 返回 amount + 5 [E1] [app.py:1-2]。",
            "**【架构推论与说明】**",
            "- 该函数承担基础加价计算。",
            "【未核实缺口/盲区】：",
            "- 未查看外部调用方。",
        ]
    )
    ok_audit = audit_report_sections(compliant_text)
    assert ok_audit.is_compliant is True
    assert ok_audit.present_sections == REQUIRED_REPORT_SECTIONS
    assert ok_audit.missing_sections == ()

    incomplete_text = "【已确认代码事实】\n- 仅有一节内容。"
    bad_audit = audit_report_sections(incomplete_text)
    assert bad_audit.is_compliant is False
    assert bad_audit.missing_sections == (
        "【架构推论与说明】",
        "【未核实缺口/盲区】",
    )


def test_format_investigation_report_renders_high_signal_output(tmp_path: Path) -> None:
    """验证结构化报告包含预算、工具轨迹、三板块正文、核验结论与底账索引。"""
    repo = _setup_demo_repo(tmp_path)
    answer = "\n".join(
        [
            "【已确认代码事实】",
            "- compute_fee 定义于 app.py 第 1~2 行，直接返回 amount + 5 [E1] [app.py:1-2]。",
            "【架构推论与说明】",
            "- checkout 通过调用 compute_fee 计算费用（未读取 checkout 正文）。",
            "【未核实缺口/盲区】",
            "- 尚未调用 read_file_snippet 读取 checkout 函数体。",
        ]
    )
    model = FakeChatModel(
        [
            _make_ai_msg(
                tool_calls=[
                    {
                        "id": "r1",
                        "name": "read_file_snippet",
                        "args": {"file_path": "app.py", "start_line": 1, "end_line": 2},
                    }
                ]
            ),
            _make_ai_msg(content=answer),
        ]
    )

    result = investigate(repo, "compute_fee 做了什么？", model=model)
    rendered = format_investigation_report(result)

    assert "[代码调查报告]" in rendered
    assert "T1/S1 read_file_snippet" in rendered
    assert "板块规范提示" not in rendered
    assert "【已确认代码事实】" in rendered
    assert "【架构推论与说明】" in rendered
    assert "【未核实缺口/盲区】" in rendered
    assert "绑定引用全部通过账本核验" in rendered
    assert "[E1] (snippet_body) app.py:1-2" in rendered
    assert "sha256:" in rendered


def test_format_report_warns_missing_sections_and_skips_on_fallback(tmp_path: Path) -> None:
    """验证模型漏写板块时报告头给出提醒；程序级兜底不触发该提醒。"""
    repo = _setup_demo_repo(tmp_path)
    # 场景 1：模型未按三板块回答
    model_bad = FakeChatModel(
        [
            _make_ai_msg(
                tool_calls=[
                    {
                        "id": "r1",
                        "name": "read_file_snippet",
                        "args": {"file_path": "app.py", "start_line": 1, "end_line": 2},
                    }
                ]
            ),
            _make_ai_msg(content="compute_fee 返回 amount + 5 [E1] [app.py:1-2]。"),
        ]
    )
    res_bad = investigate(repo, "测试缺板块", model=model_bad)
    rendered_bad = format_investigation_report(res_bad)
    assert "板块规范提示" in rendered_bad
    assert "【架构推论与说明】" in rendered_bad

    # 场景 2：Token 爆表走程序兜底，不应报模型格式警告
    model_fb = FakeChatModel(
        [
            _make_ai_msg(
                tool_calls=[
                    {
                        "id": "r2",
                        "name": "read_file_snippet",
                        "args": {"file_path": "app.py", "start_line": 1, "end_line": 2},
                    }
                ]
            )
        ]
    )
    # 使用极小 Token 上限迫使触发程序兜底
    res_fb = investigate(
        repo,
        "测试程序兜底",
        model=model_fb,
        budget=InvestigationBudget(max_total_tokens=100, reserve_summary_tokens=20),
    )
    rendered_fb = format_investigation_report(res_fb)
    assert "程序级确定性兜底输出" in rendered_fb
    assert "板块规范提示" not in rendered_fb


def test_handle_investigate_cli_empty_and_normal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """验证 CLI handle_investigate 的空参数提示与正常报告打印。"""
    repo = _setup_demo_repo(tmp_path)
    index = build_symbol_index(repo)

    handle_investigate("   ", repo, symbol_index=index)
    out_empty = capsys.readouterr().out
    assert "用法: /investigate <问题>" in out_empty

    model = FakeChatModel(
        [
            _make_ai_msg(
                tool_calls=[
                    {
                        "id": "c1",
                        "name": "read_file_snippet",
                        "args": {"file_path": "app.py", "start_line": 4, "end_line": 5},
                    }
                ]
            ),
            _make_ai_msg(
                content=(
                    "【已确认代码事实】\n- checkout 调用 compute_fee [E1] [app.py:4-5]。\n"
                    "【架构推论与说明】\n- 属于结账入口转发。\n"
                    "【未核实缺口/盲区】\n- 未读取 compute_fee 实现。"
                )
            ),
        ]
    )
    handle_investigate("checkout 做了什么？", repo, symbol_index=index, model=model)
    out_ok = capsys.readouterr().out
    assert "[代码调查报告]" in out_ok
    assert "[E1] (snippet_body) app.py:4-5" in out_ok
    assert "绑定引用全部通过账本核验" in out_ok