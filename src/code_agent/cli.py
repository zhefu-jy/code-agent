import sys
from pathlib import Path

# 确保在任意工作目录下直接执行此脚本都能正确找到包
src_dir = str(Path(__file__).resolve().parent.parent)
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from code_agent.index.bm25_index import BM25CodeIndex
from code_agent.core.model import stream_ask
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

def main() -> None:

    indexer = BM25CodeIndex()
    # 自动索引本地 src 目录下的代码
    chunk_count = indexer.build_from_directory(Path(src_dir))
    print("=" * 60)
    print(f"🚀 Code-Agent 已启动！(已索引 {chunk_count} 个本地代码切块)")
    print("  - 普通对话：直接输入问题与模型对话")
    print("  - 检索代码：输入 /search <关键词> (例如: /search stream ask)")
    print("  - 退出程序：输入 exit")
    print("=" * 60 + "\n")

    messages: list[BaseMessage] = [SystemMessage(content="你是一个专业的代码工程助手，回答简洁专业。")]
    while True:
        input_question = input("Question: ")
        if input_question == 'exit':
            break
        if not input_question.strip():
            continue

        # 拦截 /search 命令
        if input_question.startswith("/search"):
            query = input_question.replace("/search", "").strip()
            if not query:
                print("⚠️ 请提供要搜索的关键词，例如: /search review_code\n")
                continue

            results = indexer.search(query, top_k=2)
            if not results:
                print(f"🔍 未在代码库中找到与 '{query}' 相关的代码片段。\n")
            else:
                print(f"\n🔍 找到 {len(results)} 处高度相关的代码片段:")
                for rank, (chunk, score) in enumerate(results, 1):
                    print(
                        f"【TOP {rank}】得分: {score:.2f} | 文件: {chunk.file_path} (第 {chunk.start_line}~{chunk.end_line} 行)")
                    print("-" * 50)
                    print(chunk.content)
                    print("-" * 50 + "\n")
            continue


        messages.append(HumanMessage(content=input_question))
        full_answer = ""
        print("Agent: ", end="", flush=True)
        for chunk in stream_ask(messages):
            full_answer += chunk
            print(chunk, end="", flush=True)

        messages.append(AIMessage(content=full_answer))
        print("-" * 60)




if __name__ == "__main__":
    main()