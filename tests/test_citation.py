import sys
from pathlib import Path

# 确保能正确导入 src 下的模块
src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

import pytest
from code_agent.core.citation import extract_citations, has_no_evidence, Citation


def test_extract_standard_range_citation():
    text = "模型工厂函数负责构造不同的模型实例 [src/code_agent/core/model.py:50-64]。"
    citations = extract_citations(text)
    assert len(citations) == 1
    assert citations[0].file_path == "src/code_agent/core/model.py"
    assert citations[0].start_line == 50
    assert citations[0].end_line == 64
    assert citations[0].raw_text == "[src/code_agent/core/model.py:50-64]"


def test_extract_single_line_citation():
    text = "在这里抛出了未识别异常 [src/code_agent/core/model.py:63]。"
    citations = extract_citations(text)
    assert len(citations) == 1
    assert citations[0].file_path == "src/code_agent/core/model.py"
    assert citations[0].start_line == 63
    assert citations[0].end_line == 63


def test_extract_windows_backslash_path():
    text = "Windows 路径格式引用 [src\\code_agent\\core\\schemas.py:10-25]。"
    citations = extract_citations(text)
    assert len(citations) == 1
    assert citations[0].file_path == "src/code_agent/core/schemas.py"
    assert citations[0].start_line == 10
    assert citations[0].end_line == 25


def test_extract_backtick_wrapped_citation():
    text = "某些大模型喜欢把路径用反引号包起来 [`src/code_agent/cli.py:15-30`]。"
    citations = extract_citations(text)
    assert len(citations) == 1
    assert citations[0].file_path == "src/code_agent/cli.py"
    assert citations[0].start_line == 15
    assert citations[0].end_line == 30


def test_extract_multiple_citations():
    text = (
        "首先在 [src/code_agent/core/schemas.py:20-30] 定义了结构，"
        "随后在 [src/code_agent/core/model.py:67-75] 中进行了结构化调用。"
    )
    citations = extract_citations(text)
    assert len(citations) == 2
    assert citations[0].file_path == "src/code_agent/core/schemas.py"
    assert citations[0].start_line == 20
    assert citations[0].end_line == 30
    assert citations[1].file_path == "src/code_agent/core/model.py"
    assert citations[1].start_line == 67
    assert citations[1].end_line == 75


def test_auto_correct_reversed_line_numbers():
    text = "模型偶发倒置行号 [src/foo.py:100-80]。"
    citations = extract_citations(text)
    assert len(citations) == 1
    assert citations[0].start_line == 80
    assert citations[0].end_line == 100


def test_ignore_non_citations():
    text = """
    这是一个普通的 Markdown 链接 [点击这里](https://github.com)。
    这是一个普通的数组切片 [1:10]。
    这是一个普通方括号注释 [TODO:123]。
    """
    citations = extract_citations(text)
    assert len(citations) == 0


def test_has_no_evidence_detection():
    assert has_no_evidence("经过检索当前代码库，该功能无依据。") is True
    assert has_no_evidence("未在代码中找到与该配置相关的实现。") is True
    assert has_no_evidence("代码库中未实现 Redis 缓存。") is True
    assert has_no_evidence("该函数定义在 [src/code_agent/core/model.py:50]。") is False


if __name__ == "__main__":
    pytest.main(["-v", __file__])
