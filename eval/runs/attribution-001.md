# W4 S1 失败用例白盒 Trace 追溯与归因说明报告

> **评测基准**：eval/runs/report-001.md (通关率 84.0%)  
> **归因核心结论**：检索召回缺陷 100% (4/4)，模型推理缺陷 0% (0/4)。

---

## 一、归因汇总矩阵

| 用例 ID | 题目类型 | 期望文件 | 召回切块范围 | 缺失关键行 | 归因定性 | 责任方 |
| :---: | :---: | :--- | :--- | :--- | :--- | :---: |
| **005** | Behavior | `core/citation.py` | 181~196, 121~160, 91~130 | 第 180 行枚举定义 | 切块物理截断 | **检索层** |
| **013** | Location | `core/citation.py` | 151~190, 181~196, 121~160 | 第 46 行 `class` 声明 | TF-IDF 逆向挤压 | **检索层** |
| **017** | Behavior | `index/bm25_index.py` | 31~70 | 第 71 行配置字典 | 切块边界失落 | **检索层** |
| **021** | Behavior | `cli.py` + `model.py` | `cli.py` 1~40, `model.py` 1~75 | `cli.py` 41~75 行循环 | 跨文件配额不足 | **检索层** |

---

## 二、逐题详细 Trace 追溯与白盒事实

### 1. Case 005（行号越界校验分支）
* **问题**：当引用结束行号超出物理文件的总行数时，validate_citation 函数会返回什么状态枚举以及怎样的错误提示？
* **期望关键符号**：`['LINE_OUT_OF_BOUNDS', 'validate_citation']`
* **BM25 召回事实 (Top-1 切块)**：
  召回了 `src/code_agent/core/citation.py` 第 181~196 行。
* **证据缺失点**：
  源码第 180 行是 `status=CitationStatus.LINE_OUT_OF_BOUNDS`，第 181~183 行是 `error_message=f"引用行号超出文件总行数..."`。40 行滑动窗口机械切断在第 180 和 181 行之间，导致喂给模型的上下文里有错误提示字符串，但没有状态枚举。
* **模型表现**：
  模型准确提取了错误提示，并声明所给片段未包含状态赋值行，严守铁律拒答。
* **归因判定**：**检索切块物理截断（Retriever Truncation Failure）**。

---

### 2. Case 013（契约定义位置）
* **问题**：用于表示引用物理校验结果的数据契约 CitationValidationResult 是在哪个文件中定义的？
* **期望关键符号**：`CitationValidationResult` 在 `src/code_agent/core/citation.py`
* **BM25 召回事实 (Top-3 切块)**：
  召回了 `citation.py` 的 151~190 行、181~196 行、121~160 行。
* **证据缺失点**：
  `class CitationValidationResult:` 在第 46 行（只出现 1 次）。而下游 121~196 行反复实例化该类 6 次。BM25 纯按词频计分，前 3 名全被调用处占领，定义处被挤出 Top-3。
* **模型表现**：
  模型指出片段中仅看到构造使用，未看到类定义，诚实判定无依据。
* **归因判定**：**TF-IDF 逆向偏好挤压（TF-IDF Def Squeeze）**。

---

### 3. Case 017（目录扫描黑名单）
* **问题**：BM25CodeIndex.build_from_directory 在递归扫描目录构建索引时，默认排除了哪些目录？
* **期望关键符号**：`['ignore_dirs', 'build_from_directory']`
* **BM25 召回事实 (Top-1 切块)**：
  召回了 `src/code_agent/index/bm25_index.py` 第 31~70 行。
* **证据缺失点**：
  函数签名在第 61 行，文档字符串在 62~65 行，而配置 `ignore_dirs = {".git", "__pycache__", ...}` 偏偏在第 71 行。切块在第 70 行截断，配置刚好落在窗口之外。
* **模型表现**：
  模型指出仅看到了文档字符串开头，未展示具体实现逻辑，严谨拒答。
* **归因判定**：**切块步长边界失落（Boundary Loss）**。

---

### 4. Case 021（跨模块多轮会话协同）
* **问题**：cli.py 中的交互式会话是如何维护历史消息列表 messages，并将其传递给 model.py 中的 stream_ask 实现多轮对话的？
* **期望文件**：`src/code_agent/cli.py` + `src/code_agent/core/model.py`
* **BM25 召回事实 (Top-3 切块)**：
  召回了 `cli.py` 第 1~40 行，以及 `model.py` 的两个片段。
* **证据缺失点**：
  `cli.py` 全长 75 行，实际调用 `stream_ask` 与追加消息的循环在第 45~65 行。在 `top_k=3` 预算下，`cli.py` 后半截被丢弃，大模型只有拼图的一角。
* **模型表现**：
  模型确认了 `messages` 初始化和 `stream_ask` 签名，指出交互循环位于 40 行之后未提供，严谨拒答。
* **归因判定**：**跨文件拼接配额不足（Multi-file Assembly Fracture）**。
