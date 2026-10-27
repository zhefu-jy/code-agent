# 学习进度与技术问题日志 (Progress Log)

> 记录每日实际完成的模块与代码，沉淀对话中提出的有价值技术问题。

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
