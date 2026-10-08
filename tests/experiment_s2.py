import sys
from pathlib import Path

# 针对 Windows 控制台编码保护，防止 emoji 触发 GBK 编码异常
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# 确保能正确导入 src 下的模块
src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from langchain_core.messages import SystemMessage, HumanMessage
from code_agent.core.model import get_chat_model
from code_agent.index.bm25_index import BM25CodeIndex
from code_agent.core.citation import (
    CITATION_PROMPT_REQUIREMENT,
    extract_citations,
    validate_citations,
    has_no_evidence,
)


def run_experiment_case(
    case_title: str,
    question: str,
    indexer: BM25CodeIndex,
    model,
    top_k: int = 3
) -> None:
    print("\n" + "=" * 70)
    print(f"🧪 【实验】: {case_title}")
    print(f"❓ 【提问】: {question}")
    print("=" * 70)

    # 1. BM25 检索代码片段
    results = indexer.search(question, top_k=top_k)
    snippets_text = ""
    if results:
        print(f"🔍 [BM25 检索命中 {len(results)} 个代码片段]")
        for i, (chunk, score) in enumerate(results, 1):
            # 将 Windows 反斜杠标准化
            clean_path = chunk.file_path.replace("\\", "/")
            snippets_text += f"\n--- 片段 {i}: {clean_path} (第 {chunk.start_line}~{chunk.end_line} 行, 得分: {score:.2f}) ---\n"
            snippets_text += chunk.content + "\n"
    else:
        print("🔍 [BM25 检索无结果]")
        snippets_text = "（未在代码库中检索到相关代码片段）"

    # 2. 组装 Prompt 喂给模型
    system_prompt = (
        "你是一个代码工程问答助手。\n"
        f"{CITATION_PROMPT_REQUIREMENT}\n"
    )

    user_prompt = (
        f"【参考代码片段】:\n{snippets_text}\n\n"
        f"【用户问题】: {question}\n"
        "请根据上述参考代码回答，并按铁律标注出处。"
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_prompt),
    ]

    # 3. 调用模型
    print("🤖 [大模型思考并回答中...]")
    response = model.invoke(messages)
    answer = response.content
    print("\n【模型回答】:")
    print(answer)
    print("-" * 70)

    # 4. 解析与物理校验
    no_evidence = has_no_evidence(answer)
    citations = extract_citations(answer)
    validation_results = validate_citations(citations)

    print("📊 【裁判质检结果】:")
    print(f"  - 是否声明无依据: {no_evidence}")
    print(f"  - 提取到引用数: {len(citations)}")
    for i, res in enumerate(validation_results, 1):
        c = res.citation
        status_tag = "✅ VALID" if res.is_valid else f"❌ {res.status.value}"
        print(f"  [{i}] 引用: {c.raw_text} -> 判定: {status_tag}")
        if not res.is_valid:
            print(f"      🚨 报警原因: {res.error_message}")
        else:
            print(f"      🎯 物理文件总行数: {res.total_lines} 行 (区间合法)")


def main():
    print("🚀 正在构建本地代码 BM25 索引...")
    indexer = BM25CodeIndex()
    root_path = Path(__file__).resolve().parent.parent / "src"
    total = indexer.build_from_directory(root_path)
    print(f"✅ 索引构建完成，共 {total} 个代码切块！")

    model = get_chat_model(temperature=0.0)

    # 实验 1: 真实存在的功能 (正向用例)
    run_experiment_case(
        case_title="正向测试 · 真实存在的函数",
        question="项目中的 stream_ask 函数是在哪个文件、哪几行定义的？",
        indexer=indexer,
        model=model,
    )

    # 实验 2: 完全不存在的功能 (钓鱼测试 A)
    run_experiment_case(
        case_title="钓鱼测试 A · 完全虚构的模块",
        question="项目中用于分布式锁的 RedisDistributedLocker 在哪个文件第几行实现？",
        indexer=indexer,
        model=model,
    )

    # 实验 3: 诱导性提问 (钓鱼测试 B: 诱导在已有文件中找假函数)
    run_experiment_case(
        case_title="钓鱼测试 B · 诱导在已有文件中找虚构函数",
        question="我记得 src/code_agent/core/model.py 里面有一个 export_to_onnx 模型导出函数，它在第几行？",
        indexer=indexer,
        model=model,
    )


if __name__ == "__main__":
    main()
