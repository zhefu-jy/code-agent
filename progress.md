# 学习进度与技术问题日志 (Progress Log)

> 记录每日实际完成的模块与代码，沉淀对话中提出的有价值技术问题。

## 2026-11-12 (Day 23 · W7 S1 评测三权分立契约与自测集归档)

### 一、今日完成事项
1. 废除单一大盘“必须 100% 全绿”神话，将 30 道自测题迁移归档至 `eval/cases/regression_self/` 作为内部防退化回归集。
2. 改造 `eval/run_eval.py` 中的 `load_cases()`，支持 `rglob` 递归扫描及 `subset` 测试子集自动标记。
3. 在 `src/code_agent/core/schemas.py` 中定义三维度解耦评测契约：`SystemStatus`（系统执行）、`EvidenceValidity`（举证真实性与切片覆盖）、`AnswerVerdict`（业务结论与拒答判定）。
4. 改造 `src/code_agent/core/judge.py` 与 `eval/run_eval.py`，打通三维度判定逻辑并计算 `citations_in_context`，`mypy src eval` 零告警通过。
5. 运行 `--replay 001` 与 `--replay 009`，验证正常定位与虚构拒答场景下三维度状态流转正常。

### 二、待消化理解清单（极简待办 · 大白话版）
- [ ] **待办 1：弄明白昨天的 `judge.py` 到底在干啥**
  - 它为什么把“判卷打分”专门从“调大模型”里拆出来单独放一个文件？
  - 为什么说以后改了打分规则，不需要每次花钱重新请求大模型，直接看历史答案就能重新判？
- [ ] **待办 2：弄明白今天新加的“评测三维度”是什么意思**
  - **第 1 维（程序跑通没）**：大模型 API 挂没挂、有没有超时报错？
  - **第 2 维（引用的行号真不真）**：回答里引用的代码行号是不是瞎编的？是不是真在搜出来的代码里出现过？
  - **第 3 维（最后到底答对没）**：核心内容答对没？如果大模型老老实实说“没找到”，算通过还是算没完成？
- [ ] **待办 3：找时间用大白话彻底理顺一遍从提问到打分的完整流程**

### 三、今日有价值提问清单（复习索引）
1. 为什么将历史自测题强行当成“泛化证明”会导致过拟合？
2. 面对正常提问，模型的诚实拒答（HONEST_REFUSAL）在评测中应该计为通过还是未完成？为什么？
3. 为什么单跑 `mypy eval/run_eval.py` 会报 `[import-untyped]`，而 `mypy src eval` 却完全正常（PEP 561 机制）？

---

## 2026-11-11 (Day 22 续 · 判卷口径与检索召回的三处定向修复)

### 一、今日完成事项
1. **判卷内核解耦（P0）**：把判卷逻辑从 `eval/run_eval.py` 抽到 `src/code_agent/core/judge.py`（纯函数，不导入 chromadb / langchain / 大模型），并配套 `scripts/rejudge_run.py`：对历史 run JSON 离线重判，零 Token 验证口径改动。
2. **拒答分级修复**：`is_full_refusal` 三处加固——空回答不再算作拒答（原来边界题一次空返回会被判"已拒答 → 通过"）；`**加粗**` 不再打穿首句前缀匹配（Case 014 曾因此漏网）；新增"……无法确定默认排除了哪些具体目录"句式（Case 017）。
3. **file_hit 口径收紧**：期望文件命中只认"物理校验通过"的引用。原实现用正则提取结果，引用一个不存在的文件也算命中期望文件。
4. **硬编码清除**：删除 `case_id == "019"` 特例，改为 YAML 数据开关 `allow_partial_refusal`（019 未启用，恢复如实判定）。新增 `tests/test_judge.py`（18 项）与 `tests/test_no_case_id_hardcode` 式的源码断言，锁定"不允许再按 id 打补丁"。
5. **符号契约修复（P1）**：`CodeChunk` 新增 `symbol_names: list[str]` 与 `all_symbols()`，AST 顶层聚合块不再把多符号拼成 `"A, B"`（这是"按符号精确命中"对聚合块永久失效的根因，Case 014 的直接原因）；向量库写入/读出同步持久化该字段。
6. **定义匹配置信度**：新增 `definition_strength`（类名 + 方法名都命中得 2 分，仅方法名或裸函数得 1 分），解决 `BM25CodeIndex.build_from_directory` / `VectorCodeIndex.build_from_directory` / `HybridRetriever.build_from_directory` 三个同名方法并列抢占 Top-1 的问题。
7. **同文件软配额**：把"每文件严格 1 块"改为"每文件最多 2 块"，并新增阶段 3.5（同名定义块的同文件保底）。这是 Case 003 / 016 的结构性病因：`chunk_file` 同时在 `ast_chunker.py` 与 `bm25_index.py`，严格 1 块导致真正被问的文件永远进不了上下文。
8. **依赖补全（一次静态引用跳转）**：选中主定义块后，把同文件内被它引用的**模块级常量**一并带上（`build_from_directory → DEFAULT_IGNORE_DIRS`、`has_no_evidence → _NO_EVIDENCE_PATTERN`）。约束：只补常量、只针对主定义块、且不得挤掉其它定义块（试错中发现补全函数/类会把 `bm25_index.py:55` 顶掉）。
9. **实机验证**：`scripts/check_retrieval.py` 建立 5 个定义块召回探针（014/017/019/011/006），从 3/5 提到 5/5；全量 30 题实跑 `run-009` 通关率 100%（25/25 代码 + 5/5 文档），并对 30 条回答逐条人工抽检，无"判过但未答/答错"，边界题 5/5 均正常拒答。

### 二、今日有价值提问清单（复习索引）
1. 为什么"空回答"与"拒答"必须是两个状态？把 `is_full_refusal("")` 判成 True 会在边界题上造成什么后果？
2. 用字符串前缀表判定拒答，会被哪些正常写法打穿（Markdown 加粗、定语前置、转折句）？前缀匹配与"分句 + 语义落点"的边界在哪里？
3. 为什么"引用物理有效率 100%"不能当作质量信号？它与"引用支撑度"（引用区间是否真的落在召回切块内）差在哪一步？
4. 一个切块聚合了多个符号时，把符号名拼成逗号串存入单字段 `symbol_name`，会让哪些下游逻辑静默失效？
5. 为什么"每文件只保留 1 块"这种多样性策略，在"同一文件内跨块取证"的问题上会造成结构性失败？软配额与 MMR 的取舍是什么？
6. 同名方法分布在多个类里时，只按方法名做定义匹配会发生什么？用"命中标识符个数"作为置信度，在什么情况下仍会并列？
7. 什么时候需要做"检索层的一跳静态补全"（go-to-definition），什么时候应该交给 W8 的 tool calling 让模型自己二跳取证？
8. 评测出现 100% 时，为什么它反而是"尺子失效"的警报？如何在不改题目的前提下让满分重新变得有信息量？

---

## 2026-11-11 (Day 22 · W6 W 检索系统与自动化评测科学工程化重构)

### 一、今日完成事项
1. 科学评测“三段论解耦”与假阴性/假阳性根治（P0）：重构 `eval/run_eval.py` 判卷逻辑，彻底拆解检索召回（Stage 1）、实质答题（Stage 2）与物理引用验证（Stage 3）三段判据；编写 `is_full_refusal` 严密区分“全文拒答”与“局部严谨免责”，根除 Case 006 假阴性；封堵误判放行漏洞，根除假阳性；全面支持含中文、连字符的真实 Markdown 文档引用路径校验；拆除 `case.id != "019"` 等一切硬编码特例，评测规则完全数据化。
2. 攻克 AST 语法切块顶层赋值丢弃盲区（P1）：重构 `src/code_agent/index/ast_chunker.py`，改变原实现只切首个类/函数前头部并忽略后续所有顶层赋值的缺陷，使用顺序缓冲区对非类/非函数语句进行聚合切块并提取赋值符号（如 `_CITATION_PATTERN`、`DEFAULT_IGNORE_DIRS`），彻底拯救 Case 014 与 Case 017 的物理检索盲区。
3. 语料角色隔离与过程文档“词法磁铁”清除（P1）：在 `HybridRetriever.build_from_sources` 中引入 `doc_exclude_patterns`，严格排除 `安排.md`、`progress.md`、`notes.md` 等过程演进文档，切断过程文档对真实事实源码的词法抢占（Case 001、008 根治）。
4. 语言感知自适应融合与确定性 Tie-Break（P1）：针对英文小向量模型（`all-MiniLM-L6-v2`）在长中文下的余弦噪音问题，在 `HybridRetriever.search` 中实现跨语言自适应加权（中文查询动态提权 BM25 到 `max(alpha, 0.7)`），解决 Min-Max 极端放大导致标题块反超真实决策表格的问题（Case 027 根治）；同时实现文件级多样性（MMR 思想）、显式提及文件保底与严格确定性 Tie-Break（消除哈希随机性，评测 100% 稳定可复现）。
5. 30 题黄金评测集 100% 满分验收与大盘分维度透视（P2）：重构自动化报告输出格式，同时提供“核心代码子集（25 题）”、“架构规约子集（5 题）”与“全量综合大盘（30 题）”三重视角。实测 `eval/runs/report-007.md` 实现 **30/30 全绿 100% 满分通关**：
   - 核心代码子集：100.0% (25/25)
   - 架构规约子集：100.0% (5/5)
   - 检索阶段召回率：100.0% (30/30)
   - 引用物理有效率：100.0% (107/107 全部物理行号精准闭合)
   - 虚构边界防御率：100.0% (5/5 面对不存在功能严谨拒答)
   - 全量 pytest（32 个单测）与 `mypy src eval`（14 个源文件）100% 零告警通过。

### 二、今日有价值提问清单（复习索引）
1. 为什么把过程性文档（如 `安排.md`、`progress.md`）作为设计规约入库会成为最强“词法磁铁”？知识库工程中应如何对语料进行角色分类与物理隔离？
2. 在 AST 语法树解析中，若只遍历函数和类节点，为什么会导致定义在类与函数之间的全局配置与正则表达式成为永久检索盲区？
3. 当使用英文单语向量模型（`all-MiniLM-L6-v2`）检索中文技术文档时，为什么 Min-Max 归一化反而会成为“放大噪声的帮凶”？自适应融合权重（Adaptive Alpha）的底层信息检索理论依据是什么？
4. 评测系统为什么不能简单使用一个布尔值判定用例是否通过？如何通过“三段论解耦（召回 $\to$ 实质回答 $\to$ 依据支撑）”彻底消灭假阳性与假阴性？
5. 为什么说使用硬编码特例（如 `if case.id != "019"`）或为迎合测试随意修改提示词不是在解决问题，而是在制造不可持续的系统灾难？

---

## 2026-11-10 (Day 21 · W6 S3 双源混合检索与 CLI 跨源透视升级)

### 一、今日完成事项
1. 混合检索器双源调度整合：在 `src/code_agent/index/hybrid_retriever.py` 中重构并扩展 `HybridRetriever`，新增底层原子灌库接口 `build_from_chunks`，新增多源自动调度接口 `build_from_sources`（分别调度 `ASTChunker` 提取代码类与函数符号，以及 `MarkdownDocChunker` 提取文档树形面包屑），并在 `build_from_directory` 中平滑支持 `doc_dir` 可选参数，保持既有单测与评测执行器 100% 向前兼容。
2. 攻克 Python 单元素元组语法陷阱：排查并修复 `code_extensions: Tuple[str, ...] = (".py")` 丢失逗号导致 mypy 报错的类型隐患（`str` vs `tuple`），保持静态类型严格清零。
3. 自动化双源单元测试全量通过：在 `tests/test_hybrid_retriever.py` 中新增 `test_hybrid_retriever_dual_sources`，验证代码与 Markdown 双源联合扫描、多态契约还原（`source_type` / `symbol_name` / `title_path`）与双路打分；深刻印证并巩固了 `ASTChunker` 中对于小于 50 行小类保持整体内聚的符号归一设计；全量 30 个 pytest 单测 100% 全绿通过（2.98s）。
4. CLI 跨源透视与卡片交互落地：升级 `src/code_agent/cli.py`，启动时联合扫描 `src/` 与 `docs/` 并输出细分切块统计（138 个切块：62 源码 + 76 文档）；重塑 `/search` 命令输出视图，以 `💻 [源代码]` vs `📄 [规范文档]`、命中类型标签（`🔀 [双路命中]` / `🧠 [语义命中]` / `🔤 [词法命中]`）、细分打分、符号/章节标识及物理行号卡片完整透视检索结果。
5. 端到端实机真实意图验证：实跑 CLI 验证代码意图（`/search stream_ask` 双路 1.0 满分锁定 `model.py` 第 24~47 行）与架构决策意图（`/search 阶段1-2不上向量库的原因` 词法穿透锁定 `ADR-001` 第 38~49 行），成功打通“代码事实”与“设计规约”的双源协同；`mypy src eval` 零告警通过（14 个源文件）。

### 二、今日有价值提问清单（复习索引）
1. 混合检索器 `HybridRetriever` 为什么不能直接让向量库单独扫描目录，而必须抽象出多源汇流调度？
2. 在 Python 中，为什么 `(".py")` 会被识别为 `str` 而非 `tuple`？单元素元组为什么必须强制加尾随逗号？
3. 在针对双源混合检索编写单测时，为什么包含 4 行方法的小类切块，其 `symbol_name` 断言为类名而非方法名？`ASTChunker` 的小类内聚设计初衷是什么？
4. CLI 跨源透视升级如何实现“代码事实（事实本身）”与“设计规约（权威依据与约束来自哪）”在终端的视觉化解耦与协同？
5. 在 Windows 控制台下打印 Emoji 和多源标签时，为什么容易触发 `UnicodeEncodeError`（GBK 代码页限制）？如何通过标准流重定向进行工程级防御？

---

## 2026-11-05 (Day 20 · W6 S2 双源索引构建与中英文混合分词打通)

### 一、今日完成事项
1. BM25 中英文混合分词改造：针对纯代码正则 `[a-zA-Z0-9]+` 过滤中文导致技术文档“语义蒸发”的问题，在 `src/code_agent/index/bm25_index.py` 中引入零外部依赖的 CJK 1-gram（单字保证全量召回）与 2-gram（双字保证连续紧邻权重）联合分词机制；编写 `tests/test_bm25_index.py` 验证驼峰英文、纯中文与中英混合切词，既有单测 100% 保持向前兼容。
2. 向量索引多态底座升级：在 `src/code_agent/index/vector_index.py` 中新增 `build_from_chunks` 接口，解耦目录扫描与多源切块直接入库；在 ChromaDB `metadatas` 中持久化 `source_type` 与 `title_path`，并在 `search()` 余弦查询中完整还原数据契约，消除无用导入。
3. 单元测试契约化与模型边界辨析：编写 `tests/test_vector_index.py` 的多源入库与契约还原单测，明确区分“单测确定性契约验证”与“小模型跨语言语义打分偏好”，剔除人为伪造 Docstring 导致的测试失真，确保契约测试纯粹可信。
4. 双源实机汇流与交叉对照实验：编写 `scripts/verify_dual_source_index.py`，全量扫描项目 136 个真实切块（60 个 Python 代码块 + 76 个 Markdown 文档块），在真实语料下验证 BM25 与 Vector 召回能力：代码符号（`stream_ask`）双路 100% 锁定源码，中文架构决策（`阶段1-2不上向量库的原因`）BM25 凭借 24.35 高分穿透定位至 ADR-001 第 38~49 行。
5. 静态检查与全量单测验收：`mypy src eval` 零告警清零（14 个源文件），全量 29 个 pytest 单测 100% 全绿通过（2.43s）。

### 二、今日有价值提问清单（复习索引）
1. 为什么在单元测试中用中文自然语言查询纯英文裸代码，小向量模型会错误把结果引向中文文档？（多语言小模型对齐鸿沟与玩具测试数据失真）
2. 单元测试的核心职责是什么？为什么单测应关注“接口行为与数据契约还原”而非“小模型的语义打分偏好”？在测试中为了跑通而刻意给代码添加匹配的 Docstring 是否属于掩耳盗铃？
3. 为什么说单靠向量检索代码是脆弱的？工业界真实的代码助手（如 Cursor、GitHub Copilot）是如何结合词法倒排、LSP 编译器符号表与混合检索来构建防线的？
4. BM25 在不引入 jieba 等重型第三方库时，如何通过 CJK 1-gram + 2-gram 正则切词同时满足高召回率与词组紧邻权重？

---

## 2026-11-04 (Day 19 · W6 S1 开源切块适配与双源数据契约统一)

### 一、今日完成事项
1. 数据契约多态归一化升级：在 `src/code_agent/index/bm25_index.py` 中为 `CodeChunk` 新增 `source_type: Literal["code", "doc"] = "code"` 与 `title_path: str = ""` 字段，既有 18 个单测全部保持全绿，实现 100% 平滑向后兼容。
2. 基于 `markdown-it-py` 实现 `MarkdownDocChunker`：在 `src/code_agent/index/markdown_chunker.py` 中编写文档切块器；利用层级栈维护面包屑路径（`title_path`）；实现“纯父级容器标题（紧跟子标题且无正文）跳过”机制消除空切块，并将祖先标题路径注入到子块检索词袋；支持前置引言（Prelude）与无标题文档自动兜底。
3. 单元测试全量覆盖与实战落地：编写 `tests/test_markdown_chunker.py` 覆盖 6 大测试维度；实切项目真实文档 `docs/ADR-001-技术选型.md`，精准切出 6 个核心决策小节并校验物理行号闭合；全量 24 个单测 100% 通过（1.87s）。
4. 攻克异构字典类型推断陷阱（TypedDict）：解决在 `headings` 列表中因混合存放 `int` 与 `str` 导致 `mypy` 推断为 `dict[str, object]` 并报出 10 条运算/切片类型不兼容的报错；通过引入标准 `TypedDict`（`HeadingInfo`）保持业务逻辑零变动的同时，实现 `mypy src eval` 零告警清零（14 个源文件）。

### 二、今日有价值提问清单（复习索引）
1. `tokens = self.md.parse(content)` 解析返回的 Token 流具体包含什么？为什么解析器会把标题拆分成 `heading_open`、`inline`、`heading_close` 三个节点？
2. `enumerate(tokens)` 的目的是什么？为什么只判断 `type == "heading_open" and t.map:`？`t.tag[1:]` 与 `t.map` 的 `[start, end]` 行号映射机制是什么？
3. `CodeChunk` 的 `tokens` 字段有什么意义？为什么要在 `tokens` 中把 `title_path` 扩展进去？（文档语义孤儿与面包屑特征无污染注入）
4. 文档在第一个标题之前的前置引言（Prelude）文本是如何通过切片提取的？行号是如何与后续章节严丝合缝闭合的？
5. 遍历切块中是如何用标题层级栈（Stack）维护树形祖先路径并实现同级/高级弹栈的？什么是纯父级容器标题？为什么对它跳过切块而只保留面包屑？
6. 在 Python 静态类型系统中，为什么将包含 `int` 和 `str` 的无标注字典放入列表会导致 mypy 报错 `Unsupported operand types for < ("int" and "object")`？`TypedDict` 是如何解决这个问题的？

---

## 2026-11-03 (Day 18 · W5 W 评测大盘回归、消融实验与 ADR-001 决策闭环)

### 一、今日完成事项
1. 评测执行器多策略解耦：在 `eval/run_eval.py` 中新增 `--retriever` 参数（支持 `bm25` / `vector` / `hybrid`，默认 `hybrid`），解耦 `(CodeChunk, float)` 元组与 `HybridSearchResult` 对象契约，保证下游 `build_snippets_text` 与判卷器完全平滑兼容。
2. 单题多策略消融验证：针对 Case 001 进行横向消融测试，实测发现纯向量检索（`vector`）因通用词主导几何方向导致关键符号 `stream_ask` 发生稀释被挤出 Top-3；而混合检索（`hybrid`）借助 BM25 的精确硬匹配成功召回并通过。
3. 全量 25 题自动化回归跑分：运行 `python -m code_agent.eval --retriever hybrid --output eval/runs/run-005.json --report eval/runs/report-005.md`，全量 25 题 100.0% 全绿通关，引用物理有效率 100.0% (106/106)，边界防御率 100.0% (4/4)。
4. 横向消融对比分析：对比 Report-004 基线，AST 语法切块彻底消除了滑动窗口（60 行 + 20 overlap）中 28.5% 的重叠文本冗余与函数截断，**单题平均 Token 消耗由 1,763 tok 直降至 1,188 tok（降幅 32.6%）**；单题耗时仅增加 +0.47s（2.02s vs 1.55s），处在交互秒级预算内。
5. 静态检查与工程验收：`mypy src eval` 零告警清零通过（13 个源文件），全量 18 个 pytest 单元测试持续全绿通过（2.83s）。
6. 闭环 ADR-001 决策记录：在 `docs/ADR-001-技术选型.md` 的“为什么阶段 1-2 不上向量库”一节正式回填 W5 实测数据与结论，完成知识沉淀。

### 二、今日有价值提问清单（复习索引）
1. 为什么在提问中只保留英文字母给 BM25 能精准命中，而把整句中文完整传给向量检索反而会出现 `stream_ask` 被稀释的现象？
2. Embedding 向量检索模型与像 DeepSeek-V3 / Claude / GPT 这样的大语言模型（LLM）在理解提示词和注意力分配上有何本质区别？
3. 既然大模型（LLM）能通过 Full Attention 精确理解几千字的长文本，为什么我们不直接把整个代码库所有文件全塞给大模型，而必须做两阶段 RAG 检索？
4. 前沿代码助手（如 Cursor、Claude Code）在工业界是如何解决单向量检索符号稀释问题的？（代码专精 Embedding、ColBERT 迟交互、Reranker 重排、LSP 编译器符号表）
5. 用户提问发给大模型时，物理上是何时经过 Embedding 处理的？RAG 架构中“前置检索”与“后置生成”的完整数据流是怎样的？

---

## 2026-11-02 (Day 17 · W5 S3 双路召回与混合检索器)

### 一、今日完成事项
1. 解决分值量纲不一致痛点：设计并实现 Min-Max 动态归一化算法，抹平 BM25 无界得分与向量余弦相似度之间的尺度鸿沟，实现单候选、全相等分值与全零分的边界安全防御。
2. 语料底座对齐改造：在 `BM25CodeIndex` 中新增 `build_from_chunks` 接口，使 BM25 与向量库完全共享由 `ASTChunker` 生成的语义代码块，确保 `(file_path, start_line, end_line)` 实体对齐。
3. 手写混合检索器：实现 `src/code_agent/index/hybrid_retriever.py`（`HybridRetriever`），支持两路并发/双路召回、动态加权打分（$\alpha \cdot S_{\text{bm25}} + (1-\alpha) \cdot S_{\text{vec}}$）与来源标签判定（`BM25` / `VECTOR` / `BOTH`）。
4. 编写自动化单元测试：实现 `tests/test_hybrid_retriever.py`，覆盖归一化边界、端到端双路命中、向量单路挽救与 alpha 权重调节；全量 18 个单测全绿通过（1.83s）。
5. 静态检查与 CLI 交互升级：保持 `mypy src eval` 零告警通过；升级 `src/code_agent/cli.py` 的 `/search` 命令，直观展示命中来源标签（双路/词法/语义）与融合细分。

### 二、今日有价值提问清单（复习索引）
1. 为什么 BM25 的绝对得分不能直接与向量相似度相加？两者在数学上有何本质冲突？
2. 为什么要专门为 BM25 增加 `build_from_chunks` 接口？若让 BM25 保留滑动窗口而向量库用 AST 切块，会导致什么物理后果？
3. 在 Min-Max 归一化中，当某一查询只召回了 1 个候选块，或者所有候选块得分相同时，除零异常该如何防范？
4. BM25Okapi 在极小样本语料库（切块总数 $N \le 2$）中，为什么会出现 IDF 变负或为 0 的停用词效应？
5. 为什么“Min-Max 动态归一化 + 加权融合”在数学和工程上是合理正确的？它是如何做到保序性与共识双重印证的？

---

## 2026-10-27 (Day 16 · W5 S2 纯本地轻量向量索引与语义检索对照实验)

### 一、今日完成事项
1. 搭建纯本地轻量向量底座：引入向量库 `chromadb` 与本地 ONNX Embedding 模型（`all-MiniLM-L6-v2`），免 Docker 且 100% 离线本地计算。
2. 扩展代码切块数据契约：在 `CodeChunk` 数据结构中新增 `symbol_name` 符号元数据字段，联动 `ASTChunker` 在切块时持久化函数名、类名及方法作用域前缀。
3. 手写语义向量索引器：实现 `src/code_agent/index/vector_index.py`（`VectorCodeIndex`），承接 AST 语法感知切块输出，配置余弦空间（`hnsw:space: cosine`）与分批入库持久化（`file_path`、`start_line`、`end_line`、`symbol_name`），实现余弦相似度检索接口 `search(query, top_k) -> list[tuple[CodeChunk, float]]`。
4. 攻克 Chroma 类型系统与 Python List 不变性隐患：解决 `metadatas` 传参时 mypy 报出的类型不兼容问题，通过直接引入 `chromadb.api.types.Metadata` 消除静态类型告警，保持 `mypy src eval` 零告警清零。
5. 编写与运行自动化单元测试：编写 `tests/test_vector_index.py`，覆盖多语义概念检索、AST 代码块定位与空查询异常防御，全量 15 个 pytest 单测全绿通过（1.33s）。
6. 实机运行词法 vs 语义召回对照实验：编写并实跑 `scripts/compare_bm25_vs_vector.py`，用真实数据量化证实了 BM25 在纯中文环境下的“语义蒸发”盲区与向量检索的泛化命中能力。

### 二、今日有价值提问清单（复习索引）
1. `CodeChunk` 已经有了 `content` 和 `file_path`，为什么还需要单独加一个 `symbol_name` 字段？它在检索和 Agent 演进中有什么意义？
2. 为什么向量库的距离度量空间要选择 `cosine`（余弦距离）而不是默认的 `l2`（欧氏距离）？
3. ChromaDB 返回的余弦距离公式是 $1 - \cos(\theta)$，我们如何将其换算为下游检索打分所需的相似度得分？
4. 为什么在 Python 静态类型系统中，`list[dict[str, Any]]` 不能直接传递给期待 `list[Metadata]` 的参数？（List 的不变量机制）
5. 纯 Prompt 约束（如 `agent.md`）与代码物理防御（如 Hook/拦截器/状态机）在控制大模型越界行为上有什么本质差异？

---

## 2026-10-26 (Day 15 · W5 S1 AST 语法感知切块与结构化解析)

### 一、今日完成事项
1. 梳理滑动窗口物理缺陷：深入剖析 W4 S2 中 60 行滑动窗口机械切断函数头尾、类名与内部方法脱节、以及 20 行 overlap 重叠导致的 28.5% Token 冗余膨胀。
2. 手写 AST 语法切块器：基于 Python 标准库 `ast` 实现 `src/code_agent/index/ast_chunker.py`。实现小类整体保留（<=50行）、大类自动提取类头并拆分独立方法；包含非 Python 文件与 `SyntaxError` 自动降级回退机制。
3. 静态类型严格清零：分析并解决 `mypy` 报出的 `list[CodeChunk]` 传参类型错误（明确 `list.append` 单元素嵌套与 `list.extend` 扁平展开的本质区别）。
4. 攻克全局头部行号穿透缺陷：发现全局 `ast.Assign` 导致文件头部从第 1 行直接穿透至第 95 行（包裹了中间类与函数）的严重漏洞，确立“遇到首个类或函数定义即断开”的对称性收敛原则。
5. 解决类方法“上下文孤儿”难题：在 `_chunk_function` 中利用 `prefix` 将 `ClassName.method_name` 前缀注入到切块的 `tokens` 列表中，确保搜类名时能精准召回内部方法。
6. 实机自检与回归：在 `src/code_agent/core/citation.py` 上验证切分，9 个代码块全部边界闭合、内聚完整；全量 13 个 pytest 单元测试全绿通过。

### 二、今日有价值提问清单（复习索引）
1. `ast.parse` 生成的 `tree` 在底层到底长什么样？`tree.body` 包含了什么？
2. 为什么模块头部的 `header_nodes` 是一个数组，而不是一个单一的 Header 节点？
3. AST 节点中的 `lineno` 与 `end_lineno` 代表什么？如何通过物理行号截取代码并在列表中正确换算切片？
4. 提取大类的类头时，为什么从类的起始行截取到第一个方法的 `lineno - 1` 就能完整覆盖类属性和类级 Docstring？
5. 类内部的方法在 AST 中为什么依然表现为 `ast.FunctionDef`，而不是专门的 MethodDef？
6. 切分出独立方法块后，为什么代码内容中丢失了类名？`prefix` 是如何通过分词注入解决方法检索孤儿问题的？
7. 为什么提取模块头部时不能全量收集全局 `Assign`，而必须在遇到第一个类或独立函数时立即 `break`？

---

## 2026-10-21 (Day 14 · W1~W4 架构全局复盘与 Python 底层机制巩固)

### 一、今日完成事项
1. W1~W4 全局架构与数据流图梳理：绘制 CLI 端到端交互主流程、评测引擎双阶段判定流，厘清表现层、检索层、上下文治理层与评测体系四大子系统职责。
2. 基础语法与核心机制专项吃透：
   - 彻底推导 BM25 切片排序 `sorted(enumerate(scores), ...)` 下标绑定与 `(chunk, score)` 实体指标元组契约；
   - 深入剖析 CWD 运行目录漂移与全工程相对路径标尺一致性，明确 `base_dir` 统一对齐的物理原因与防御降级机制；
   - 深度辨析普通 class、`@dataclass` 与 Pydantic `BaseModel` 的底层差异与分工边界，吃透大模型 `with_structured_output` 依赖 JSON Schema 生成与递归反序列化的核心机制。
3. 工程构建与评测内核深度复盘：
   - 厘清 `pyproject.toml` 在现代 Python 工程中的核心职能（包元数据管理、`pip install -e .` 可编辑挂载消除寻址红线、以及开发工具链集中统管）；
   - 拆解 `run_eval.py` 的 Stage 1 检索命中与 Stage 2 推理引用命中解耦逻辑、关键符号全量匹配机制，以及单题调试保护基线大盘的防覆盖规则。
4. 沉淀至实战备忘录：在 `notes.md` 中新增检索下标绑定元组契约、CWD 路径漂移防御、双阶段评测解耦与基线防冲刷保护、以及第六章数据建模选型机制避坑。

### 二、今日有价值提问清单（复习索引）
1. `sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]` 是如何运作的？为什么下游返回必须是 `(chunk, score)` 元组？
2. 全工程路径转换中，如果 CLI 与 Eval 传入的 `base_dir` 不一致会有什么物理后果？
3. 如果只写普通 class，不加 `@dataclass`、不继承 `BaseModel`，不能达到同样的效果吗？
4. 为什么大模型结构化输出 `with_structured_output` 必须依赖 `BaseModel`，而普通 class 或 `@dataclass` 无法胜任？
5. `pyproject.toml` 这个文件在现代 Python 项目中到底有什么用？为什么能彻底解决 IDE 模块寻址报错和 `sys.path.insert`？
6. `run_eval.py` 中如何解耦判定“Stage 1 检索命中”与“Stage 2 推理引用命中”？
7. 判卷器面对虚构钓鱼题、行号幻觉以及局部免责时是如何做阶梯防御的？
8. `eval/runs/` 目录的生成与覆盖规则是怎样的？单题调试时为什么不能覆盖 `baseline_bm25.json`？

---

## 2026-10-20 (Day 13 · W4 W 类型清零、CLI 审查链路与阶段验收)

### 一、今日完成事项
1. 统一静态检查规范：`pyproject.toml` 增加 `[tool.mypy]`（`files`、`no_implicit_optional`、`warn_return_any`、`exclude`）与 `[tool.pytest.ini_options]`（`testpaths=["tests"]`），消除根目录 smoke 脚本被 pytest 误收集导致的真实 API 调用。
2. `mypy src eval` 的 11 条隐患清零：删除 `bm25_index.py` 幽灵导入；`usage` 改 `Mapping[str, Any]` 承接 TypedDict；`review_code` 以 `isinstance` 收窄 + `model_validate` 兜底；`cli.py` 修复 `chunk` 跨类型复用；`run_eval.py` 修复 `TextIO.reconfigure`、`Optional` 穿透与 usage 二次赋值。静态检查全绿（9 个源文件）。
3. 抽取 `core/context.py`（`to_repo_relative` / `build_snippets_text`），让 CLI 与评测共用同一份相对路径片段拼装；同时修掉 `-c` 单题调试会覆盖 `baseline_bm25.json` 与 `report-001.md` 的默认值地雷。
4. 三连回归：pytest 13 passed（0.39s，全程离线）；`python -m code_agent.eval --output eval/runs/run-004.json --report eval/runs/report-004.md` 跑出 25/25、引用 120/120、均耗 1.55s、单题均耗 1,763 tok，与 report-003 的 1,757 tok 相比仅 +0.3%，证明抽函数属零行为变更。
5. CLI 上线 `/review`：路径解析基准改为仓库根、800 行规模闸门、结构化 Pydantic 报告渲染（severity 分级）、`issue.line_number` 物理越界校验、审查调用异常隔离。
6. 端到端实机自检：用 CLI 审查 `src/code_agent/index/bm25_index.py` 与手工植入缺陷的样例文件，逐条核对报告中的行号与缺陷实际行号的一致性。

### 二、今日有价值提问清单（复习索引）
1. 为什么 `usage: dict[Any, Any]` 接不住 `UsageMetadata`，而 `Mapping[str, Any]` 可以？
2. `cast(ReviewReport, x)` 与 `isinstance` + `model_validate` 的本质差异是什么？
3. 为什么 PowerShell 里裸写 `--case 001,005` 会变成 `'1,5'`？
4. `if results:` 这种“调用点判空”为什么会埋下 `UnboundLocalError`？
5. “数据源治理”与“下游补丁”的判据是什么？为什么说治理上下文等于治理输出？
6. 为什么 `review_code` 从 W1 就写好了，直到 W4 W 才第一次有人真的用它？一个函数变成“产品能力”缺的是哪三样？

---

## 2026-10-19 (Day 12 · W4 S3 模块化评测与 Replay 白盒调试模式)

### 一、今日完成事项
1. 规范工程配置与模块化寻址：编写根目录 `pyproject.toml`，通过 `pip install -e .` 可编辑模式挂载 `src` 源码，创建 `src/code_agent/eval.py` 入口，打通 `python -m code_agent.eval` 命令行统一调用。
2. 规范 IDE 模块识别：在 `eval/` 下增加 `__init__.py`，消除静态类型检查器对 `eval.run_eval` 的解析报错。
3. 打造 `--replay <case_id>` 专属白盒调试模式：自动联动开启检索切块 Trace 与输入 Prompt，输出结构化的【判卷器 5 维白盒体检表】；实现 `--replay failed` 自动拉起历史失败用例进行白盒重放，并实现调试模式防覆盖基线数据保护。
4. 修复判卷器“全文无界扫描无依据”引发的假阴性缺陷：增加符号命中、文件命中与合法引用的复合守卫，拦截文末局部免责说明导致的整题误杀。
5. 全量 25 题自动化评测大盘回归：综合通关率达到 **100.0% (25/25)**，引用物理有效率 100.0% (121/121)，虚构边界防御率 100.0% (4/4)，单题平均耗时 1.67s。

### 二、今日有价值提问清单（复习索引）
1. `pip install -e .` 命令应该在 `agent-project` 还是在 `code-agent` 目录下执行？
2. 为什么在代码里写了 `sys.path.insert`，IDE 里的 `from run_eval import main` 依然会爆红？
3. `pyproject.toml` 中 `build-backend` 与 `where` 配置有哪些严格的命名与类型规范？
4. 在评测体系中引入 `is_replay` 和 `--replay` 参数的核心工程目的是什么？它与普通大盘评测有何本质差异？
5. 为什么模型给出了 15 处全部合法的真实代码引用，依然会被判卷器判定为“未通关”？底层机制是什么？

---

## 2026-10-15 (Day 11 · W4 S2 单点切块调优与 A/B 对比评测)

### 一、今日完成事项
1. 深入厘清滑动窗口切片机制：明确 `overlap` 为切块间的重叠交叉行数，滑动步长为 `step = chunk_size - overlap`（而非额外增加行数）。
2. 实施单点控制变量调优：在 `src/code_agent/index/bm25_index.py` 中将切块参数由 `chunk_size=40, overlap=10` 调整为 `chunk_size=60, overlap=20`。
3. 执行局部靶向评测：针对 W4 S1 遗留的 4 个失败用例（`005`, `013`, `017`, `021`）进行白盒验证，4 题 100% 全部翻盘通关。
4. 全量 25 题自动化回归评测大盘实跑：综合通关率由 **84.0% 提升至 96.0%（提升 12.0%）**，检索阶段召回率达成 100.0%（25/25），引用物理有效率 99.2%，单题均耗 1.93s，单题均耗 Token 从 1,409 增至 1,811 tok (+28.5%)。
5. 归档核心交付物：完成并落盘《W4 S2 调优对比报告》(`eval/runs/report-002.md`)，白盒剖析 Case 022 文末附加免责声明引发的判卷假阴性（False Negative）。

### 二、今日有价值提问清单（复习索引）
1. 切片中的 `overlap` 具体表示什么物理含义？它是让每个切块额外多出 10 行代码吗？
2. 调整切块参数后，为什么在工程实践中要先针对 Case 005 和 017 进行靶向验证，而不是直接跑全量大盘？
3. 在全量评测中，Case 022 已经给出了完美解答并命中了所有期望符号与文件，为什么判卷器依然将其判定为“未通关”？底层机制是什么？
4. 切片块增大后，原本 BM25 检索不到的块为什么现在能被检索到了？背后的三层物理与竞争机制是什么（单块逻辑闭环、重叠双重曝光、候选池分母收缩）？

---

## 2026-10-14 (Day 10 · W3 W 收官 & W4 S1 归因工程与 Prompt Trace)

### 一、今日完成事项
1. 运行 25 题自动化评测大盘（通关率 84.0%，21 题通过，4 题失败，平均耗时 2.04s），达成 W3 全部验收指标。
2. 评测执行器可观测性升级：在 `eval/run_eval.py` 中增加 `--verbose`（`-v` 查看召回切块）和 `--prompt`（`-p` 查看输入 Prompt 原文）参数，在 `CaseResult` 中补充 `snippets_text` 和 `full_prompt`。
3. 结合代码切片与完整 Prompt 逐一完成 4 个失败案例的白盒 Trace 追溯，确立定性结论：失败 100% 归因于检索层（切块截断、TF-IDF 偏好与跨文件配额），0% 归因于模型推理。
4. 归档 W4 S1 核心交付物：编写并落盘《W4 S1 失败用例白盒 Trace 追溯与归因说明报告》(`eval/runs/attribution-001.md`)。
5. 完善 `agent.md`，确立 `progress.md` 与 `notes.md` 的去形式化记录规范，完成全量历史文档精简重构。

### 二、今日有价值提问清单（复习索引）
1. 在 `run_eval` 中，如果模型回答包含了期望符号，但实际上是在表达“未找到该符号”（如“我未找到对应 symbol”），这种情况下系统是如何防止误判通过的？
2. 为什么判卷代码中要写 `elif no_evidence_declared and case.id != "019":`？Case 019 为什么要做特殊处理？
3. 为什么在命令行参数加了个 `-v`，解析器就会自动把 `verbose` 作为 `True` 传进去？底层机制是什么？
4. 评测报告里的“检索是否命中”，具体指的是不是指召回的切片中包含了我们期望的目标文件？
5. 为什么 Case 013 检索标记为“命中”，但实际传的代码都是 `citation.py` 而不是 `run_eval.py`？找对了目标文件为什么最终还是失败了？
6. 在 W4 S1 的归因工程中，失败到底是怪检索还是怪模型？两者的责任划分依据是什么？

---

## 2026-10-13 (Day 9 · W3 S2-S3 评测执行器参数化与 Markdown 报告)

### 一、今日完成事项
1. `eval/run_eval.py` 接入 `argparse` 参数化解析，支持 `--case` 单题或多题调试重放，支持 `--top-k`、`--output` 和 `--report` 自定义路径。
2. 在 `CaseResult` 中解耦 Stage 1 检索命中（`retrieval_file_hit`）与 Stage 2 推理命中；接入 `usage_metadata` 统计 Token 消耗。
3. 实现 `generate_markdown_report()`，支持将跑分结果自动渲染为结构化 Markdown 报告 (`eval/runs/report-001.md`)。
4. 全量 25 题实跑验证：通关率 84.0% (21/25)，总消耗 35,222 tok，单题均耗 1,409 tok。

### 二、今日有价值提问清单（复习索引）
1. 为什么在评测中要显式拆分 Stage 1（检索召回）与 Stage 2（大模型推理）两个阶段？
2. 使用单题参数 `--case` 进行局部调试，对评测开发反馈效率有哪些具体收益？

---

## 2026-10-12 (Day 8 · W3 S1-S2 黄金评测集扩展至25题与判卷器重构)

### 一、今日完成事项
1. 编写 011 ~ 025 共 15 道 YAML 评测用例，将用例集扩充至 25 题（覆盖代码定位、行为逻辑与虚构钓鱼题）。
2. 修复判卷器三大漏洞：拦截正常题因证据缺失造成的误拒答、升级为期望关键符号全量匹配、针对 Case 019 规则元语言题目做逻辑豁免。
3. 修正 012（默认模型名实为 `deepseek-flash`）与 015（行号纠错实为元组解包）用例的 Ground Truth 标错问题。
4. 生成 25 题初版基准跑分 (`eval/runs/baseline_bm25.json`)，通关率为 80.0% (20/25)。

### 二、今日有价值提问清单（复习索引）
1. 为什么正常功能题目如果模型回答了“无依据”，即使解释里引用了真实文件，也必须判为 FAIL？
2. 符号匹配判定时，为什么要使用 `missing_symbols` 全量匹配，而不能只要命中任意一个符号就通过？

---

## 2026-10-09 (Day 6-7 · W2 S3 & 周末基线评测体系建设)

### 一、今日完成事项
1. 制定统一评测数据契约，编写 001 ~ 010 共 10 道 YAML 黄金评测用例。
2. 编写 `eval/run_eval.py` 初版批量执行器，跑通 BM25 检索 -> 模型推理 -> 引用物理校验 -> 符号命中的全链路判卷。
3. 解决 Windows 盘符冒号导致引用提取正则瘫痪的缺陷，在 Prompt 上下文组装时统一转为相对路径。
4. 跑出第一个基线跑分数据 (`eval/runs/baseline_bm25.json`)。

### 二、今日有价值提问清单（复习索引）
1. 为什么单元测试全部通过，全链路端到端评测却只有 40% 的初跑通关率？
2. 为什么在喂给模型的上下文标尺中坚持使用相对路径而不是绝对路径？

---

## 2026-10-08 (Day 5 · W2 S2 引用物理校验器与钓鱼测试)

### 一、今日完成事项
1. 实现引用物理校验器 `validate_citation`：检查文件在磁盘上的真实存在性，校验行号是否在 `[1, total_lines]` 合法物理区间内。
2. 编写 `_resolve_file_path` 路径解析函数，支持工作目录（CWD）偏移自动回退到仓库根目录。
3. 编写针对不存在类（如 `RedisDistributedLocker`）的钓鱼测试脚本，验证模型能遵守 Prompt 铁律输出“无依据”。

### 二、今日有价值提问清单（复习索引）
1. 为什么大模型面对不存在的函数依然能编造出行号？行号漂移产生的物理根因是什么？
2. 为什么校验器内部状态要使用 `@property` 派生属性，而不是直接存储冗余布尔值？

---

## 2026-09-28 (Day 4 · W2 S1 出处提取与引用约束)

### 一、今日完成事项
1. 编写代码审查 System Prompt 中的引用约束铁律（必须输出 `[file:line]` 格式，无法定位时必须输出“无依据”）。
2. 使用正则表达式实现 `extract_citations`，提取文本中的引用出处并支持行号倒置自动纠正。
3. 实现 `has_no_evidence` 正则匹配，用于下游判定模型是否明确声明了无依据。

---

## 2026-09-23 (Day 3 · W1 S3 & 周末任务 BM25 索引器)

### 一、今日完成事项
1. 编写长上下文压力测试脚本 `test_context_limit.py`，测试不同代码行数下的 Token 消耗与检索表现。
2. 实现代码分词器 `tokenize_code`（支持驼峰与下划线拆分）和滑动窗口分块函数 `chunk_file`。
3. 封装 `BM25CodeIndex` 索引器，并在 CLI 中新增 `/search <关键词>` 指令。

---

## 2026-09-21 ~ 09-22 (Day 1-2 · W1 S1-S2 环境搭建与流式交互)

### 一、今日完成事项
1. 搭建 Conda Python 3.12 环境与 `src/` 目录骨架，调通 DeepSeek API 连接。
2. 实现 `stream_ask` 流式生成器与控制台多轮对话循环。
3. 基于 Pydantic 定义 `ReviewReport`、`ReviewIssue` 数据契约，利用 `with_structured_output` 实现结构化代码审查。
