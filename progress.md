# 学习进度与代码消化日志 (Progress & Review Log)

> **用途**：每日追加记录当天完成的实际代码与模块；重点登记**需要进一步消化、复习与深入理解的技术点**。

## 2026-10-12 (Day 8 · W3 S1-S2 黄金评测集扩展至25题与判卷执行器严密重构)

### 一、今日完成模块与代码
1. **构建与扩充黄金评测集至 25 题 ([eval/cases/](eval/cases/))**：
   - 编写并落盘了 011 ~ 025 共 15 道全新高质量 YAML 用例；
   - 覆盖代码定位（Location 7条）、行为机制与异常（Behavior 14条）、虚构钓鱼（Boundary 4条）；
   - 经实测反向审计，纠正了 012（默认模型名 `deepseek-flash`）与 015（元组交换）用例的标准答案缺陷。
2. **重构并严密化评测判卷器 ([eval/run_eval.py](eval/run_eval.py))**：
   - **堵住拒答放水漏洞**：正常功能题若触发 `no_evidence_declared` 立即判 FAIL，标记为检索未召回关键片段；
   - **全量符号严密匹配**：引入 `missing_symbols`，杜绝任何复读机假阳性通过；
   - **自指元语言隔离防御**：针对 Case 019 考察“无依据”词表的元语义题目做专属逻辑豁免。
3. **沉淀第一份客观可信的 25 题全量基准跑分 ([eval/runs/baseline_bm25.json](eval/runs/baseline_bm25.json))**：
   - 综合通关率：**80.0% (20/25)**，引用物理有效率：**98.3%**，虚构边界防御率：**100.0% (4/4)**，平均耗时：1.96s；
   - 真实捕获并归因了 5 个核心失败（001, 005, 013, 017, 021），全部确认为 BM25 纯行号切块截断与 Top-K 挤压所致，为阶段三引入 AST 语法切块立下无可辩驳的数据坐标。

---

## 2026-10-12 (Day 7 · W2 W 周末深度复盘与基线认知收官)

### 一、今日完成模块与深度沉淀
1. **全面消化 W2 核心待办与基线评测体系**：
   - 全面梳理并彻底销项了 Day 6 留存的 3 大待消化架构知识点（单测 vs Eval、路径治理 Token 经济学、有效率与通过率差异归因）；
   - 深入剖析了首份基线报告 ([eval/runs/baseline_bm25.json](eval/runs/baseline_bm25.json))（通关率 100%、有效率 94.87%）的技术代表性。
2. **现场抓捕 RAG 检索流水线三大底层经典故障（Case 005 实证分析）**：
   - **分词器语义蒸发**：实测确认 `tokenize_code()` 的 `[a-zA-Z0-9]+` 正则过滤掉了所有中文意图，只留下英文符号；
   - **Top-K 检索截断危机**：BM25 短块 TF-IDF 逆向偏好，导致包含越界核心代码的第 4 名切块（得分 0.79）被 `top_k=3` 暴力截断；
   - **固定行号切块断头案**：第 181 行一刀切切断了 `if` 语法头部，诱使模型忠实报告“状态为 VALID”，验证了大模型被工程缺陷“陷害”的真实场景。
3. **沉淀实战认知至备忘录 ([notes.md](notes.md))**：
   - 记录了代码切块物理边界、跨文件隔离、BM25 分词盲区与 Top-K 截断机制，为后续 W5 引入 AST 语法切块立下铁证；
   - 达成 W2 全部验收标准，圆满完成 W2 阶段交付，正式就绪迈向 W3。

---

## 2026-10-09 (Day 6 · W2 S3 & 周末任务提前攻坚：黄金评测集与自动化执行器 Baseline 实测)

### 一、今日完成模块与代码
1. **建立协作准则与纯手工编码原则 ([agent.md](agent.md))**：
   - 确立了 AI 助手作为“架构导师与结对编程搭档”的职责定位；
   - 确立“零自动生成代码、用户纯手工敲入每一行逻辑”的学习原则，杜绝形式主义，通过手动编码建立工程肌肉记忆。
2. **构建黄金评测数据集 (Golden Benchmark Dataset) ([eval/cases/](eval/cases/))**：
   - 制定了统一的标准数据契约（包含 `id`, `type`, `question`, `expected_files`, `expected_symbols`, `is_unanswerable`, `intent` 等）；
   - 亲手编写并落盘了 10 个高质量 YAML 黄金测试用例，严格维持 **4:4:2 黄金配比**：
     - **位置定位 (Location · 4题)**：`001-stream_ask`、`002-validate_citation`、`003-chunk_file`、`004-review_schemas`；
     - **行为机制 (Behavior · 4题)**：`005-line_out_of_bounds`、`006-tokenize_code`、`007-resolve_file_path`、`008-review_code_structured`；
     - **边界与钓鱼 (Boundary · 2题)**：`009-redis_locker`、`010-onnx_export`。
3. **实现自动化批量评测执行器 ([eval/run_eval.py](eval/run_eval.py))**：
   - 亲手实现了 `EvalCase` 与 `CaseResult` 数据契约；
   - 实现了 `load_cases()` 动态扫描加载器；
   - 实现了全链路判卷逻辑：BM25 检索 $\to$ LLM 推理 $\to$ 引用提取 $\to$ 物理存在与行号区间校验 $\to$ 期望文件与符号命中判决；
   - 实现了评测大盘自动统计（完成率、引用物理有效率、边界拒答率、综合通关率、延迟），并自动持久化导出为 JSON 报告。
4. **抓捕隐蔽重大跨平台 Bug 并完成数据源治理**：
   - 初次实跑暴露通关率仅 **40.0%**，现场捕获 Windows 盘符冒号（`D:`）导致引用正则全线瘫痪的致命陷阱；
   - 实施**方案 A（数据源治理）**：在喂给大模型的代码片段标尺中，将绝对路径统一转换为相对于工程根目录的标准相对路径（`src/...`）；
   - 治理后复测：**综合通关率从 40.0% 飙升至 100.0%**，引用物理有效率达 **94.87%**，提前达成 W2 周末基线里程碑！
   - 成功生成第一份基线评估报告 ([eval/runs/baseline_bm25.json](eval/runs/baseline_bm25.json))。

---

### 📌 【待消化 / 需复习强化的知识点清单】

- [x] **1. 全链路评测（Eval）与单元测试（Unit Test）的本质区别**：
  * *复习点*：为什么单测 100% 通过（S1/S2），全链路却只有 40%？Eval 考核的不是孤立组件，而是组件拼装后的“流水线协同”与“真实大模型输出分布”。
- [x] **2. Prompt 上下文路径治理与 Token 经济学**：
  * *复习点*：为什么在上下文中坚持使用相对路径而不是绝对路径？如何规避跨平台盘符差异（Windows `D:` vs Linux `/`）并节省 Token 成本？
- [x] **3. 引用有效率（94.87%）与通过率（100%）的差异归因**：
  * *复习点*：为什么通过率 100%，但引用有效率是 94.87%？复习行号漂移（Line Drift）在长文本中的局部偏差，以及“容错判定（只要关键文件与符号命中即通过）”在工程落地中的平衡设计。

---

## 2026-10-08 (Day 5 · W2 S2 引用物理校验与幻觉抓捕实测)

### 一、今日完成模块与代码
1. **引用物理校验器与契约设计 ([src/code_agent/core/citation.py](src/code_agent/core/citation.py))**：
   - 深入理解了“大模型无物理读盘能力，必须靠 RAG 上下文拼装”的底层机制；
   - 定义了 `CitationStatus` 枚举（继承 `(str, Enum)`），清晰区分 `VALID`、`FILE_NOT_FOUND`、`LINE_OUT_OF_BOUNDS`、`INVALID_RANGE`；
   - 实现了 `CitationValidationResult` 结果契约，运用 `@property` 实现动态派生属性，坚守“单一事实来源（Single Source of Truth）”；
   - 编写了 `_resolve_file_path()`，实现 CWD 工作目录漂移防御与 `Path(__file__).resolve().parents[3]` 仓库根目录自动回退兜底；
   - 实现了核心物理校验器 `validate_citation()` 与批量接口 `validate_citations()`，包含 `read_text(encoding="utf-8", errors="replace")` 与 `splitlines()` 跨平台防御。
2. **单元测试驱动验证（TDD）([tests/test_citation.py](tests/test_citation.py))**：
   - 编写了 5 组针对校验器的高防御用例（真实文件合法区间、假文件不存在拦截、行号越出文件尾部、第 0 行非法值、多引用批量校验）；
   - 13 个单元测试全部 100% 极速秒过（0.03s）。
3. **真实模型连调与钓鱼实验 ([tests/experiment_s2.py](tests/experiment_s2.py))**：
   - 将 W1 周末的 BM25 检索器与大模型端到端打通；
   - 故意构造不存在的模块（`RedisDistributedLocker`）和诱导假函数（`export_to_onnx`），验证了铁律 Prompt 成功迫使模型输出“无依据”，未产生盲目自嗨；
   - 正向实测中真实捕获了 `stream_ask` 从第 23 行偏移到第 21 行的**行号漂移（Line Drift）**现象；
   - 深度复盘了纯词频 BM25 检索下 `cli.py` 挤占排名，而大模型凭借代码语义理解作为隐式 Reranker 准确捞出 `model.py` 定义处的深层原理。

---

### 📌 【待消化 / 需复习强化的知识点清单】

- [ ] **1. Python `@property` 派生属性的架构价值**：
  * *复习点*：为什么不在 dataclass 里存冗余的 `is_valid: bool`？如何通过动态计算避免对象内部状态出现自相矛盾？
- [ ] **2. 运行目录偏移（CWD Drift）与相对路径解析**：
  * *复习点*：`Path.cwd()` 与 `Path(__file__).resolve().parents[3]` 的分层寻址机制，为什么底层工具库必须具备运行目录自愈能力？
- [ ] **3. BM25 纯词频检索的硬伤与大模型语义兜底边界**：
  * *复习点*：为什么 `cli.py` 的 BM25 分数会高于 `model.py`？大模型如何识别出 `def` 关键字？若 Top-K 较小发生截断时会带来什么危机？为 W4 归因与 W5 AST 切块提供动因。

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
