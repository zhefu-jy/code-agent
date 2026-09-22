from dotenv import load_dotenv
from langchain_core.language_models import BaseChatModel
from langchain_deepseek import ChatDeepSeek
from typing import Iterator
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from loguru import logger
import time
import sys

from code_agent.core.schemas import ReviewReport

# 统一输出到 stdout，并简化日志时间格式
logger.remove()
logger.add(
    sys.stdout,
    format="<green>{time:HH:mm:ss}</green> | <cyan>{level}</cyan> | <level>{message}</level>",
    colorize=True
)

load_dotenv(override=True)


def stream_ask(messages:list[BaseMessage],model:BaseChatModel | None = None) -> Iterator[str]:
    start_time = time.perf_counter()
    usage = {}

    actual_model =  model if model is not None else get_chat_model()
    for chunk in actual_model.stream(messages):
        if hasattr(chunk, "usage_metadata") and chunk.usage_metadata:
            usage = chunk.usage_metadata
        if isinstance(chunk.content,str):
            yield chunk.content
        else:
            yield str(chunk.content)

    elapsed = time.perf_counter() - start_time
    # 从 usage 元数据中安全提取 token 数
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    total_tokens = usage.get("total_tokens", 0)
    # 换行后打印结构化日志，保证与流式内容隔离
    print()
    logger.info(
        f"[LLM 调用完成] 耗时: {elapsed:.2f}s | "
        f"输入: {input_tokens} tok, 输出: {output_tokens} tok, 总计: {total_tokens} tok"
    )



def get_chat_model(model_name: str = "deepseek-flash", temperature: float | int  = 0.0) -> BaseChatModel:
    if "deepseek" in model_name:
        return ChatDeepSeek(
            model=model_name,
            temperature=temperature,
            stream_usage= True,
            extra_body={
                "thinking":{
                    "type":"disabled"
                }
            }
        )
    else:
        raise ValueError(f"Unknown model: {model_name}")



def review_code(code:str,file_path:str = "snippet.py",model:BaseChatModel | None = None) -> ReviewReport:
    actual_model =  model if model is not None else get_chat_model(temperature=0.0)
    structured_llm =  actual_model.with_structured_output(ReviewReport)
    messages = [
        SystemMessage(content="你是一个代码审查助手。"),
        HumanMessage(content=f"请审查以下代码，并给出详细的问题总结。代码文件路径：{file_path}。代码内容：{code}。")
    ]
    return structured_llm.invoke(messages)


