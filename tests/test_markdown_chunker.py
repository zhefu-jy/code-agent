from pathlib import Path
import pytest

from code_agent.index.markdown_chunker import MarkdownDocChunker, chunk_markdown_file


def test_nested_headings_and_breadcrumbs(tmp_path: Path) -> None:
    doc_path = tmp_path / "architecture.md"
    doc_path.write_text(
        "# 系统架构总览\n"
        "这里是全系统的宏观总览正文。\n"
        "\n"
        "## 数据存储层\n"
        "这里是存储层的描述。\n"
        "\n"
        "### 关系型数据库\n"
        "采用 SQLite 作为本地轻量数据库。\n",
        encoding="utf-8",
    )

    chunker = MarkdownDocChunker()
    chunks = chunker.chunk_file(doc_path)

    assert len(chunks) == 3
    assert all(c.source_type == "doc" for c in chunks)

    # 1. 第一块：系统架构总览
    assert chunks[0].title_path == "系统架构总览"
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 3
    assert "系统架构总览" in chunks[0].content

    # 2. 第二块：数据存储层
    assert chunks[1].title_path == "系统架构总览 > 数据存储层"
    assert chunks[1].start_line == 4
    assert chunks[1].end_line == 6

    # 3. 第三块：关系型数据库（继承前两级祖先路径）
    assert chunks[2].title_path == "系统架构总览 > 数据存储层 > 关系型数据库"
    assert chunks[2].start_line == 7
    assert chunks[2].end_line == 8
    assert "SQLite" in chunks[2].content


def test_empty_parent_container_skipped(tmp_path: Path) -> None:
    doc_path = tmp_path / "faq.md"
    doc_path.write_text(
        "# 常见问题 FAQ\n"
        "这里是常见问题答疑清单。\n"  # 为 H1 补充实质正文
        "## 核心问题分类\n"          # 此 H2 下直接紧跟 H3，无任何正文（纯容器）
        "### 为什么选择 Python\n"
        "因为生态成熟且易于上手。\n",
        encoding="utf-8",
    )

    chunks = chunk_markdown_file(doc_path)
    # H2 是纯容器不单独生成切块，最终应该只有 H1 和 H3 共 2 个切块
    assert len(chunks) == 2

    # 第一块是 H1
    assert chunks[0].title_path == "常见问题 FAQ"
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 2

    # 第二块是 H3，面包屑中保留了父级 H2
    assert chunks[1].title_path == "常见问题 FAQ > 核心问题分类 > 为什么选择 Python"
    assert chunks[1].start_line == 4
    assert chunks[1].end_line == 5


def test_prelude_extraction(tmp_path: Path) -> None:
    doc_path = tmp_path / "guide.md"
    doc_path.write_text(
        "> 本文档为团队核心规约。\n"
        "> 所有人在提交 PR 前必须阅读。\n"
        "\n"
        "# 第一章 入门指引\n"
        "这是第一章的内容。\n",
        encoding="utf-8",
    )

    chunks = chunk_markdown_file(doc_path)
    assert len(chunks) == 2

    # 前置引言块
    assert chunks[0].title_path == "guide > 引言"
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 3
    assert "核心规约" in chunks[0].content

    # 第一章
    assert chunks[1].title_path == "第一章 入门指引"
    assert chunks[1].start_line == 4
    assert chunks[1].end_line == 5


def test_no_heading_doc(tmp_path: Path) -> None:
    doc_path = tmp_path / "notes.md"
    doc_path.write_text(
        "这是一篇完全没有一级、二级标题的纯记事本草稿。\n"
        "共包含两行文本记录。\n",
        encoding="utf-8",
    )

    chunks = chunk_markdown_file(doc_path)
    assert len(chunks) == 1
    assert chunks[0].title_path == "notes"
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 2
    assert chunks[0].source_type == "doc"


def test_non_markdown_and_empty(tmp_path: Path) -> None:
    chunker = MarkdownDocChunker()

    # 1. 非 Markdown 文件
    py_file = tmp_path / "test.py"
    py_file.write_text("print('hello')", encoding="utf-8")
    assert chunker.chunk_file(py_file) == []

    # 2. 空白 Markdown 文件
    empty_md = tmp_path / "empty.md"
    empty_md.write_text("   \n\n  ", encoding="utf-8")
    assert chunker.chunk_file(empty_md) == []


def test_real_adr_001_chunking() -> None:
    # 真实项目文档实测断言
    adr_path = Path("docs/ADR-001-技术选型.md")
    assert adr_path.exists(), "ADR-001 文件必须存在"

    chunker = MarkdownDocChunker()
    chunks = chunker.chunk_file(adr_path)

    # 应该精准切出 6 个语义完整的决策小节
    assert len(chunks) == 6
    assert all(c.source_type == "doc" for c in chunks)

    # 验证关键章节物理行号与内容真实映射
    # 第 1 块：文档头部与引言 (1-12)
    assert chunks[0].start_line == 1
    assert chunks[0].end_line == 12

    # 第 2 块：决策清单 (13-29)
    assert chunks[1].start_line == 13
    assert chunks[1].end_line == 29
    assert "决策清单" in chunks[1].title_path

    # 第 4 块：最重要的三条之一 · 为什么阶段 1-2 不上向量库 (38-49)
    assert chunks[3].start_line == 38
    assert chunks[3].end_line == 49
    assert "向量库" in chunks[3].content
    assert "最重要的三条" in chunks[3].title_path
    assert "阶段 1-2" in chunks[3].title_path