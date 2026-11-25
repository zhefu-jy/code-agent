"""W11 S1 规范文档条款拆解与人工确认机制单元测试（纯离线）。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langchain_core.messages import BaseMessage
from pydantic import BaseModel

from code_agent.spec import (
    RuleConfirmationStatus,
    RuleParseResult,
    RuleSourceLocation,
    SpecRule,
    add_rule,
    confirm_all_drafts,
    confirm_rule,
    edit_rule,
    format_rule_drafts_table,
    interactive_confirm_rules,
    parse_spec_rules,
    skip_rule,
)


class _FakeStructuredRunnable:
    """模拟 with_structured_output(...) 返回的可调用对象。"""

    def __init__(self, parent: "FakeStructuredModel") -> None:
        self._parent = parent

    def invoke(self, input: list[BaseMessage], **kwargs: Any) -> Any:
        self._parent.calls.append(list(input))
        if self._parent.error is not None:
            raise self._parent.error
        return self._parent.payload


class FakeStructuredModel:
    """按预设结构化载荷或异常回放模型行为的离线测试替身。"""

    def __init__(
        self,
        payload: Any = None,
        *,
        error: Exception | None = None,
    ) -> None:
        self.payload = payload
        self.error = error
        self.schemas: list[type[BaseModel]] = []
        self.calls: list[list[BaseMessage]] = []

    def with_structured_output(
        self, schema: type[BaseModel], **kwargs: Any
    ) -> _FakeStructuredRunnable:
        self.schemas.append(schema)
        return _FakeStructuredRunnable(self)


def _write_sample_spec(tmp_path: Path) -> Path:
    """构造一份包含多章节规范约束的测试 Markdown 文档。"""
    spec_file = tmp_path / "docs" / "api_spec.md"
    spec_file.parent.mkdir(parents=True, exist_ok=True)
    spec_file.write_text(
        "\n".join(
            [
                "# 服务稳定性规范",
                "",
                "本文档仅用于演示规范条款拆解。",
                "",
                "## 超时控制",
                "",
                "所有对外 HTTP 请求超时上限必须为 10 秒，健康检查接口除外。",
                "",
                "## 异常与接口禁用",
                "",
                "捕获 ConnectionError 时必须记录 warning 日志；严禁直接调用 os.system。",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    return spec_file


def test_parse_spec_rules_extracts_and_grounds_locations(tmp_path: Path) -> None:
    """验证正常解析多条规则草稿、归一化相对路径及初始 DRAFT 门禁状态。"""
    spec_file = _write_sample_spec(tmp_path)
    fake_model = FakeStructuredModel(
        payload={
            "rules": [
                {
                    "title": "外部 HTTP 请求超时上限",
                    "requirement": "对外 HTTP 请求超时时间不得超过 10 秒",
                    "target_scope": "所有对外 HTTP 请求客户端",
                    "conditions": ["单位: 10 秒 = 10000 ms", "例外: 健康检查接口除外"],
                    "chunk_index": 1,
                    "start_line": 5,
                    "end_line": 8,
                },
                {
                    "title": "ConnectionError 日志要求",
                    "requirement": "捕获 ConnectionError 时必须记录 warning 日志",
                    "target_scope": "网络请求异常捕获分支",
                    "conditions": [],
                    "chunk_index": 2,
                },
            ]
        }
    )

    result = parse_spec_rules(spec_file, project_root=tmp_path, model=fake_model)

    assert result.spec_path == "docs/api_spec.md"
    assert result.warnings == []
    assert len(result.rules) == 2
    assert result.active_rules() == []  # 人工确认前不允许激活

    r1, r2 = result.rules
    assert r1.rule_id == "R1"
    assert r1.status == RuleConfirmationStatus.DRAFT
    assert r1.source_location.file_path == "docs/api_spec.md"
    assert r1.source_location.start_line == 5
    assert r1.source_location.end_line == 8
    assert "超时控制" in r1.source_location.section_path
    assert "例外: 健康检查接口除外" in r1.conditions

    # 未显式给行号时应自动回填对应切片的完整物理区间
    assert r2.rule_id == "R2"
    assert r2.source_location.start_line == 9
    assert r2.source_location.end_line == 11
    assert len(fake_model.calls) == 1


def test_parse_spec_rules_clamps_out_of_bounds_line_and_chunk(tmp_path: Path) -> None:
    """验证模型返回越界行号或不存在 chunk_index 时会被程序夹紧并记录告警。"""
    spec_file = _write_sample_spec(tmp_path)
    fake_model = FakeStructuredModel(
        payload={
            "rules": [
                {
                    "title": "行号越界测试",
                    "requirement": "超时上限 10 秒",
                    "target_scope": "HTTP 客户端",
                    "conditions": [],
                    "chunk_index": 1,
                    "start_line": 1,
                    "end_line": 999,
                },
                {
                    "title": "切片越界测试",
                    "requirement": "禁止 os.system",
                    "target_scope": "全仓代码",
                    "conditions": [],
                    "chunk_index": 88,
                },
            ]
        }
    )

    result = parse_spec_rules(spec_file, project_root=tmp_path, model=fake_model)

    assert len(result.rules) == 2
    assert len(result.warnings) == 2
    # Chunk 1 真实边界为 5-8
    assert result.rules[0].source_location.start_line == 5
    assert result.rules[0].source_location.end_line == 8
    # 越界 chunk_index=88 应安全回退至 Chunk 0
    assert result.rules[1].source_location.start_line == 1
    assert result.rules[1].source_location.end_line == 4


def test_parse_spec_rules_handles_missing_empty_and_model_error(
    tmp_path: Path,
) -> None:
    """验证文件不存在、空文档与模型调用异常下的安全降级。"""
    missing_res = parse_spec_rules(tmp_path / "missing.md", project_root=tmp_path)
    assert missing_res.rules == []
    assert len(missing_res.warnings) == 1

    empty_file = tmp_path / "empty.md"
    empty_file.write_text("   \n\n", encoding="utf-8")
    empty_res = parse_spec_rules(empty_file, project_root=tmp_path)
    assert empty_res.rules == []
    assert len(empty_res.warnings) == 1

    spec_file = _write_sample_spec(tmp_path)
    broken_model = FakeStructuredModel(error=RuntimeError("API timeout"))
    err_res = parse_spec_rules(spec_file, project_root=tmp_path, model=broken_model)
    assert err_res.rules == []
    assert any("API timeout" in w for w in err_res.warnings)


def test_rule_confirmation_and_edit_lifecycle() -> None:
    """验证确认、修正、跳过、补录、批量确认与未知编号异常处理。"""
    loc = RuleSourceLocation(
        file_path="spec.md", start_line=1, end_line=4, section_path="规范"
    )
    rules = [
        SpecRule(
            rule_id="R1",
            title="超时限制",
            requirement="超时不超过 10s",
            target_scope="客户端",
            conditions=["10s"],
            source_location=loc,
        ),
        SpecRule(
            rule_id="R2",
            title="禁用接口",
            requirement="禁止 os.system",
            target_scope="全仓",
            conditions=[],
            source_location=loc,
        ),
        SpecRule(
            rule_id="R3",
            title="背景说明",
            requirement="仅作背景描述",
            target_scope="无",
            conditions=[],
            source_location=loc,
        ),
    ]

    confirm_rule(rules, "r1", note="原样确认")
    edit_rule(
        rules,
        "R2",
        target_scope="src/ 业务代码（排除 scripts/）",
        conditions=["允许替代: subprocess.run"],
        note="收紧适用范围",
    )
    skip_rule(rules, "R3", reason="非约束背景")
    added = add_rule(
        rules,
        title="异常日志",
        requirement="捕获 ConnectionError 记录 warning",
        target_scope="网络模块",
        conditions=["日志级别: warning"],
        spec_path="spec.md",
    )

    assert added.rule_id == "R4"
    assert added.status == RuleConfirmationStatus.EDITED
    assert rules[0].status == RuleConfirmationStatus.CONFIRMED
    assert rules[1].status == RuleConfirmationStatus.EDITED
    assert rules[2].status == RuleConfirmationStatus.SKIPPED

    active = [r for r in rules if r.is_active]
    assert [r.rule_id for r in active] == ["R1", "R2", "R4"]

    table = format_rule_drafts_table(rules)
    assert "[R1]" in table
    assert "收紧适用范围" in table
    assert "共 4 条规则，当前已激活待核查 3 条" in table

    with pytest.raises(ValueError, match="R99"):
        confirm_rule(rules, "R99")


def test_interactive_confirm_rules_end_to_end() -> None:
    """验证人工交互确认环：回车确认、修改字段、跳过与手动补录。"""
    loc = RuleSourceLocation(file_path="spec.md", start_line=1, end_line=3)
    parse_result = RuleParseResult(
        spec_path="spec.md",
        rules=[
            SpecRule(
                rule_id="R1",
                title="规则一",
                requirement="要求一",
                target_scope="范围一",
                source_location=loc,
            ),
            SpecRule(
                rule_id="R2",
                title="规则二",
                requirement="超时 10 秒",
                target_scope="全仓",
                conditions=["10 秒"],
                source_location=loc,
            ),
            SpecRule(
                rule_id="R3",
                title="规则三",
                requirement="废话条款",
                target_scope="无",
                source_location=loc,
            ),
        ],
    )

    scripted_inputs = iter(
        [
            "",                             # R1: 回车确认
            "e",                            # R2: 修改字段
            "超时不得超过 10 秒",            # R2 新 requirement
            "src/ 外部请求模块",             # R2 新 target_scope
            "10 秒 = 10000 ms；/health 除外", # R2 新 conditions（混合中英文分号）
            "补充毫秒换算与例外",            # R2 note
            "s",                            # R3: 跳过
            "非可执行规范",                  # R3 skip reason
            "y",                            # 手动补录一条
            "禁止 os.system",               # 补录 title
            "使用 subprocess.run 替代",      # 补录 requirement
            "",                             # 补录默认 target_scope
            "仅限 src/",                     # 补录 conditions
            "n",                            # 结束补录
        ]
    )
    outputs: list[str] = []

    active = interactive_confirm_rules(
        parse_result,
        input_fn=lambda _prompt: next(scripted_inputs),
        print_fn=outputs.append,
    )

    assert [r.rule_id for r in active] == ["R1", "R2", "R4"]
    assert active[0].status == RuleConfirmationStatus.CONFIRMED
    assert active[1].status == RuleConfirmationStatus.EDITED
    assert active[1].conditions == ["10 秒 = 10000 ms", "/health 除外"]
    assert active[2].target_scope == "全仓代码"
    assert parse_result.rules[2].status == RuleConfirmationStatus.SKIPPED
    assert any("已激活待核查 3 条" in line for line in outputs)


def test_interactive_confirm_rules_all_and_quit() -> None:
    """验证 [all] 批量确认与 [q] 提前退出时未审核草稿不被激活。"""
    loc = RuleSourceLocation(file_path="spec.md", start_line=1, end_line=2)
    base_rules = [
        SpecRule(
            rule_id="R1",
            title="规则一",
            requirement="要求一",
            target_scope="范围一",
            source_location=loc,
        ),
        SpecRule(
            rule_id="R2",
            title="规则二",
            requirement="要求二",
            target_scope="范围二",
            source_location=loc,
        ),
    ]

    res_all = RuleParseResult(
        spec_path="spec.md", rules=[r.model_copy() for r in base_rules]
    )
    all_inputs = iter(["all", "n"])
    active_all = interactive_confirm_rules(
        res_all, input_fn=lambda _: next(all_inputs), print_fn=lambda _: None
    )
    assert [r.rule_id for r in active_all] == ["R1", "R2"]

    res_quit = RuleParseResult(
        spec_path="spec.md", rules=[r.model_copy() for r in base_rules]
    )
    quit_inputs = iter(["q", "n"])
    active_quit = interactive_confirm_rules(
        res_quit, input_fn=lambda _: next(quit_inputs), print_fn=lambda _: None
    )
    assert active_quit == []
    assert all(r.status == RuleConfirmationStatus.DRAFT for r in res_quit.rules)
    assert confirm_all_drafts(res_quit.rules) == 2