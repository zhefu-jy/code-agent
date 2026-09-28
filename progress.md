# 学习进度与代码消化日志 (Progress & Review Log)

> **用途**：每日追加记录当天完成的实际代码与模块；重点登记**需要进一步消化、复习与深入理解的技术点**。

---

## 2026-09-28 (Day 4 · W2 S1 出处提取与引用铁律)

### 一、今日完成模块与代码
1. **统一数据契约与出处规约 ([src/code_agent/core/citation.py](src/code_agent/core/citation.py))**：
   - 深入理解了为什么模型生成行号需要经过计算与推理，以及行号漂移（Drift）和常识偷渡（Plausible Hallucination）的技术成因；
   - 制定了 `CITATION_PROMPT_REQUIREMENT` 铁律，强约束模型输出 `[file_path:start_line-end_line]`（或 `[file_path:line]`），无法回答时必须显式输出“无依据”，严禁推测；
   - 定义了 `Citation` 数据结构，将非结构化模型文本转换为机器可校验的精确元数据。
2. **高防御性正则解析与清洗实现**：
   - 编写并调优了 `_CITATION_PATTERN` 正则，覆盖括号、反引号（``[`path:line-line`]`` / `[`path`:line]`）、Windows/Unix 混合路径格式；
   - 实现了 `extract_citations()`，支持反斜杠标准化、单行补齐、起止行号倒置自动纠错（min/max）；
   - 实现了 `has_no_evidence()` 模式匹配，用于后续边界题目与拒答评测。
3. **单元测试驱动验证（TDD）([tests/test_citation.py](tests/test_citation.py))**：
   - 亲手编写并调试代码，定位并修复了函数漏写返回值 `return citations` 以及反引号跨边界匹配问题；
   - 8 个边界单元测试全部 100% 满分通过（标准区间、单行、Windows 路径、反引号、多引用提取、行号颠倒容错、非引用干扰过滤、无依据检测）。

---

### 📌 【待消化 / 需复习强化的知识点清单】
> *今天掌握了结构化引用的提取，为 S2 的物理文件与行号校验打下了基础：*

- [ ] **1. 大模型计算行号的本质限制**：
  * *复习点*：BPE 分词器对换行和缩进的切分特性，为什么长上下文下模型容易出现行号偏移？在给模型喂代码片段时带上行号标尺的重要性。
- [ ] **2. 复杂正则中的捕获组与边界处理**：
  * *复习点*：捕获组与非捕获组 `(?:\s*-\s*(\d+))?` 的区别；为什么反引号不仅需要防御在冒号前，更要防御在最外侧和结尾处。
- [ ] **3. 出处校验如何赋能自动化 Eval**：
  * *复习点*：为什么给模型回答加上结构化出处标签后，可以用零成本 Python 脚本直接替代昂贵的 LLM-as-a-Judge？

---

## 2026-09-23 (Day 3 · W1 S3 & W1周末任务提前攻坚)

### 一、今日完成模块与代码
1. **模块导入与工程路径深度理解**：
   - 彻底搞懂了 `sys.path`、`Path(__file__).resolve()` 的作用，掌握了 Python 脚本直接运行与模块导入的时序规则。
2. **长上下文压力测试 ([tests/test_context_limit.py](tests/test_context_limit.py))**：
   - 编写压测脚本，实测了 50 行（645 tok）vs 800 行（8,616 tok）vs 2500 行（26,825 tok）的性能与代价；
   - 破除了原计划中“认为模型在单文件几千行就会犯错”的过时假设，验证了现代模型极强的长文本定向检索能力；
   - 发现了更本质的瓶颈：Token 暴增 41.5 倍导致的费用飙升、云厂商 TPM 429 速率熔断、以及全局审查漏报问题。
3. **深入探讨 Agent 真实痛点**：
   - 提炼了**顺从偏见（Sycophancy）**与**注意力惯性**：用户给出的错误排查信息会导致模型陷入死胡同，单开窗口秒解的本质是抹除被污染的历史，为阶段 5/6 引入 LangGraph 状态回滚立下了核心动因。
4. **提前攻克周末核心任务：BM25 代码检索引擎 ([src/code_agent/index/bm25_index.py](src/code_agent/index/bm25_index.py))**：
   - 实现了代码专用分词器 `tokenize_code`（精准拆解驼峰 `OrderTimeout` 与下划线）；
   - 实现了带滑动窗口（overlap）的代码切块函数 `chunk_file`；
   - 封装了 `BM25CodeIndex` 索引器类，支持全目录递归扫描与相关度评分搜索。
5. **CLI 工具联动整合 ([src/code_agent/cli.py](src/code_agent/cli.py))**：
   - 启动时自动索引本地源码，新增 `/search <关键词>` 指令，实现 0 毫秒、0 Token 的本地代码片段高亮检索。

---

### 📌 【待消化 / 需复习强化的知识点清单】
> *今天代码推进速度极快，以下几处底层逻辑建议抽时间对照代码再过一遍：*

- [ ] **1. 滑动窗口切块与重叠机制（Overlap）**：
  * *复习点*：在 `bm25_index.py` 的 `chunk_file` 中，为什么 `step = chunk_size - overlap`？这种带重叠的滑动窗口是如何避免一个完整函数跨界被拦腰切断的？
- [ ] **2. 驼峰分词正则实现原理**：
  * *复习点*：`re.sub(r'([a-z])([A-Z])', r'\1 \2', text)` 是如何利用正则的分组捕获，在小写与大写字母之间无缝插入空格并完成分词的？
- [ ] **3. `@dataclass` 与 `Pydantic` 的选型边界**：
  * *复习点*：为什么内部数据流转（`CodeChunk`）用标准库 `@dataclass`，而面向大模型（`ReviewReport`）必须用 `Pydantic`？
- [ ] **4. Python 导入时序（先修路后开车）**：
  * *复习点*：为什么 `sys.path.insert` 必须放在所有 `from code_agent...` 之前执行？代码自上而下解析的执行期行为。
- [ ] **5. ReAct 循环的思维模型**：
  * *复习点*：大模型（大脑发指令）+ 本地代码（四肢调工具）+ 反馈闭环的运转流程。

---

## 2026-09-22 (Day 2 · W1 S1收尾 & W1 S2核心交付)

### 一、今日完成模块与代码
1. **流式输出生成器 ([src/code_agent/core/model.py](src/code_agent/core/model.py))**：
   - 学习 Python 生成器原理与 `yield`，实现 `stream_ask` 打字机流式输出。
2. **多轮对话会话管理 ([src/code_agent/cli.py](src/code_agent/cli.py))**：
   - 搞懂了大模型无状态本质，在外层维护 `messages: list[BaseMessage]` 实现真实记忆。
3. **架构解耦与工厂模式**：
   - 实现 `get_chat_model()` 工厂函数与依赖注入，解耦具体厂商。
4. **可观测性与记账机制**：
   - 开启 `stream_usage=True`，接入 `loguru` 记录耗时与输入/输出 Token 真实消耗。
5. **Pydantic 结构化代码审查 ([src/code_agent/core/schemas.py](src/code_agent/core/schemas.py))**：
   - 定义 `Severity`、`ReviewIssue`、`ReviewReport` 严格数据契约；
   - 封装 `review_code` 函数，通过 `with_structured_output` 让模型稳定返回结构化审查报告；
   - 编写 [test_review.py](test_review.py) 端到端跑通审查实测。

---

## 2026-09-21 (Day 1 · W1 S1 启动与环境就绪)

### 一、今日完成模块与代码
1. 搭建 conda 独立运行环境（Python 3.12），安装最小依赖集；
2. 搭建标准 `src/` layout 工程目录骨架；
3. 调通 DeepSeek API 连通性测试（单次 `invoke`）；
4. 对原方案进行了关键“去形式主义”重构，剔除繁杂八股文，确立了代码驱动的 16 周实战学习路线。

---

<!-- 后续每日追加格式模板：
## YYYY-MM-DD (Day X · 阶段/Session)
### 一、今日完成模块与代码
1. ...
### 📌 【待消化 / 需复习强化的知识点清单】
- [ ] 1. ...
-->
