from code_agent.core.model import stream_ask
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, SystemMessage

def main() -> None:
    messages: list[BaseMessage] = [SystemMessage(content="你是一个专业的代码工程助手，回答简洁专业。")]
    while True:
        input_question = input("Question: ")
        if input_question == 'exit':
            break
        if not input_question.strip():
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