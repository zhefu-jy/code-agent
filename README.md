# Code-Agent · 个人工程与代码评审助手

> 一个基于 **LangChain** 与 **DeepSeek** 构建的个人工程助手，融合**本地知识库检索**与**自动化代码评审**，从底层原理到状态图编排（LangGraph）逐步演进的落地实战项目。

---

## 核心特性

- ⚡ **流式交互与会话记忆**：支持打字机式逐字流式渲染与控制台多轮连续问答（Session Memory）。
- 🧩 **模型抽象与工厂解耦**：基于 `BaseChatModel` 抽象工厂模式，解耦具体厂商实现，支持灵活依赖注入与测试。
- 🔍 **结构化代码评审（Structured Review）**：基于 **Pydantic** 数据契约，精准定位缺陷代码行号、严重等级（FATAL / WARNING / INFO）并给出修改建议与质量打分。
- 📊 **可观测性与记账（Observability）**：内置 `loguru` 耗时统计与每次调用的输入/输出 Token 真实记账。
- 🗺️ **完整技术演进体系**：涵盖纯关键词基线 $\to$ 混合检索 $\to$ 工具调用与容错循环 $\to$ LangGraph 状态机编排。

---

## 项目结构

```
code-agent/
├── src/
│   └── code_agent/
│       ├── core/               # 核心抽象层（模型工厂、流式输出、Pydantic 契约）
│       │   ├── model.py        # LLM 调用、流式生成器与审查函数
│       │   └── schemas.py      # ReviewReport、ReviewIssue 等数据结构
│       └── cli.py              # 交互式命令行入口
├── tests/                      # 单元测试与压力测试
├── eval/                       # 自动化评测体系与黄金案例集
├── docs/                       # 规划文档与架构记录
│   ├── 安排.md                 # 16 周实战任务详细安排
│   └── 方案.md                 # 架构设计与能力地图
├── test_review.py              # 代码结构化审查端到端测试入口
├── pyproject.toml              # 项目配置
├── requirements.txt            # 依赖清单
└── .env.example                # 环境变量模版
```

---

## 快速启动

### 1. 激活环境与安装依赖

```bash
# 激活 conda 环境
conda activate code-agent

# 安装阶段依赖
pip install langchain-core langchain-deepseek python-dotenv loguru pydantic
```

### 2. 配置环境变量

在项目根目录下创建 `.env` 文件：

```env
DEEPSEEK_API_KEY=your_deepseek_api_key_here
```

### 3. 启动交互式命令行

```bash
python -m src.code_agent.cli
# 或直接运行
python src/code_agent/cli.py
```

### 4. 运行代码审查端到端测试

```bash
python test_review.py
```

---

## 学习路线

详细路线规划与实战任务请查阅 [docs/安排.md](docs/安排.md)。
