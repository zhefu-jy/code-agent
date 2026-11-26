# 规范一致性审计报告

- **目标工程**: `D:/Dev/code/agent/agent-project/eval/fixtures/mini_repo`
- **代码修订版本**: `ea344dc5e207433348d76a66a497d67db2e04c89(dirty)`
- **四态汇总**: 共 5 条 | ✅ 符合 3 | ❌ 违规 1 | ⚠️ 证据不足 1 | ➖ 不适用 0 | 🛡️ 程序降级 0
- **成本基线**: 工具调用 36 次 | 总 Token 106,821 | 总耗时 36.25s

## 审计告警
- ⚠️ R4: 自动定位未找到候选代码，可人工补充或交由审计阶段按证据不足处理。

## 四态审计矩阵对比表

| 规则 | 标题 | 最终判决 | 模型原判 | S2候选(人工介入) | 引用核验(正文/坐标/未验) | 工具/Token/耗时 | 核心结论 |
|---|---|---|---|---|---|---|---|
| R1 | max_posts 默认值上限 | ✅ COMPLIANT（符合） | COMPLIANT | 1 (无) | 5/1/0 | 4次 / 10,930 / 5.19s | - `SiteConfig.max_posts` 的字段默认值被显式定义为 `100`，满足“默认值必须小于等于 10… |
| R2 | validate_config 拒绝非法 max_po… | ✅ COMPLIANT（符合） | COMPLIANT | 2 (无) | 6/0/0 | 5次 / 16,775 / 5.81s | - `validate_config` 在 `minisite/config.py:56-57` 明确检查 `conf… |
| R3 | build 异常分支必须记录 warning 日志 | ❌ VIOLATED（违规） | VIOLATED | 5 (无) | 7/0/0 | 7次 / 21,337 / 6.83s | - `BuildEngine.build` 在渲染与写盘流程的 `except Exception` 分支中仅 `ra… |
| R4 | 禁止使用 os.system | ✅ COMPLIANT（符合） | COMPLIANT | 0 (无) | 14/15/0 | 10次 / 28,888 / 10.08s | - 全仓 10 个文件经 `os.system`、`system`、`subprocess\|popen\|Popen… |
| R5 | 生产配置文件 max_posts 上限 | ⚠️ INSUFFICIENT_EVIDENCE（证据不足） | INSUFFICIENT_EVIDENCE | 2 (有) | 13/0/0 | 10次 / 28,891 / 8.34s | - 本次审计未能在仓库中找到任何生产环境配置文件 minisite.json（全仓 *.json 文件扫描结果为 0 … |

## 逐条规则审计明细

### [R1] max_posts 默认值上限 — ✅ COMPLIANT（符合）
- **规约要求**: SiteConfig.max_posts 的默认值必须小于等于 100。 （出处: `eval/fixtures/sample_spec.md:9-9`）
- **适用范围**: SiteConfig.max_posts 字段定义（默认值）
- **判决结论**: - `SiteConfig.max_posts` 的字段默认值被显式定义为 `100`，满足“默认值必须小于等于 100（含 100）”的约束。
- 全仓 `max_posts` 仅出现在 `minisite/config.py`，加载路径的兜底默认值同样为 `100`，不存在其他覆盖默认值的实现。

**代码证据与事实**:
- [E1] [minisite/config.py:14-25] `@dataclass class SiteConfig` 中字段声明为 `max_posts: int = 100`，默认值恰为上限 100，符合“≤100（含 100）”。
- [E2] [minisite/config.py:15-38] 符号索引确认 `minisite.config.SiteConfig` 唯一定义位于该区间，无其他同名类定义。
- [E3] [minisite/config.py:56-57] `validate_config` 仅校验 `config.max_posts <= 0` 时抛 `ConfigError`，未对默认值做任何改写或下调。
- [E3] [minisite/config.py:72-73] `load_config(None)` 直接返回 `SiteConfig()`，即使用类默认值 100，无额外覆盖。
- [E8] [minisite/config.py:94] 从 JSON 加载时兜底为 `int(data.get("max_posts", 100))`，与类默认值一致，未引入超过 100 的默认值。
- [E4] [minisite/config.py:24-24] 全仓 `max_posts` 搜索命中仅 5 处且全部位于 `minisite/config.py`（第 24、36、56、57、94 行），无其他模块定义或改写默认值。

- **已调查范围（程序实录）**: candidate:minisite/config.py:15-38；read:minisite/config.py:1-60；symbol:SiteConfig -> 1 hits；read:minisite/config.py:52-98；search:max_posts -> 5 hits
- **缺失线索/盲区**: - 读取：`minisite/config.py` 第 1-51 行、第 52-93 行（含 `SiteConfig` 定义、`validate_config`、`load_config` 主体）。
- 符号检索：`SiteConfig`（精确名，1 处命中）。
- 全文检索：`max_posts`（全仓 10 个文件扫描，5 处命中，均在 `minisite/config.py`）。
- 未读取：`minisite/config.py` 第 94-98 行尾部（仅剩 `load_config` 构造参数收尾与返回语句，经 [E8] 已确认第 94 行兜底默认值为 100，不影响默认值判定）。
- 无明显缺口。
- **整改建议/说明**: - 当前实现与条款一致，无需整改。若后续调整默认值，需保持 ≤100；建议在 `validate_config` 中补充上限校验（如 `max_posts > 100` 报错）以在运行时固化该约束，但该增强不属于本条款强制要求。
- **账本证据索引**: E1(minisite/config.py:1-51 [snippet_body]), E2(minisite/config.py:15-38 [symbol_signature]), E3(minisite/config.py:52-93 [snippet_body]), E4(minisite/config.py:24-24 [text_match]), E5(minisite/config.py:36-36 [text_match]), E6(minisite/config.py:56-56 [text_match]), E7(minisite/config.py:57-57 [text_match]), E8(minisite/config.py:94-94 [text_match])
- **核验提示**: ⚠️ 引用 [minisite/config.py:94] 仅命中 text_match（E8），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。

### [R2] validate_config 拒绝非法 max_posts — ✅ COMPLIANT（符合）
- **规约要求**: validate_config 必须拒绝 max_posts <= 0 的配置，并抛出 ConfigError。 （出处: `eval/fixtures/sample_spec.md:9-9`）
- **适用范围**: 配置校验函数 validate_config
- **判决结论**: - `validate_config` 在 `minisite/config.py:56-57` 明确检查 `config.max_posts <= 0`，并抛出 `ConfigError`，与条款要求完全一致。
- `ConfigError` 定义于 `minisite/exceptions.py:15-20`，继承 `MiniSiteError`，且 `config.py:8` 已正确导入，异常类型匹配。

**代码证据与事实**:
- [E1] [minisite/config.py:41-51] `validate_config(config: SiteConfig) -> None` 定义存在，docstring 声明 `Raises: ConfigError`，并已导入 `ConfigError`（第 8 行）。
- [E3] [minisite/config.py:56-57] 存在守卫 `if config.max_posts <= 0:` 后 `raise ConfigError("max_posts must be positive integer", field_name="max_posts")`，覆盖 `<= 0` 全部边界（含 0 与负数）。
- [E2] [minisite/exceptions.py:15-20] `ConfigError(MiniSiteError)` 类定义，构造函数接受 `message` 与 `field_name`，与调用处参数一致。
- [E4] [minisite/config.py:92-98] `load_config` 在构造 `SiteConfig`（含 `max_posts=int(data.get("max_posts", 100))`）后调用 `validate_config(cfg)`，校验路径实际生效。
- [E5] [minisite/config.py:41-41] 全仓搜索 `validate_config` 仅命中该唯一定义处，无其他覆盖实现。
- [E6] [minisite/config.py:97-97] 全仓搜索 `validate_config` 仅命中 `load_config` 内一处调用，无绕过校验的旁路。

- **已调查范围（程序实录）**: candidate:minisite/config.py:41-57；candidate:minisite/exceptions.py:15-20；read:minisite/config.py:1-80；read:minisite/exceptions.py:1-40；read:minisite/config.py:50-98；read:minisite/config.py:92-98；search:validate_config -> 2 hits
- **缺失线索/盲区**: - 已读取：`minisite/config.py` 全文（1-98 行，分三次读取覆盖）、`minisite/exceptions.py` 全文（1-37 行）。
- 已检索：关键词 `validate_config`（全仓 10 个文件，2 处命中，均已核实正文）。
- 缺失线索：未逐一核查测试用例是否覆盖 `max_posts <= 0` 的断言，但条款仅约束 `validate_config` 实现行为，不影响判决。
- **整改建议/说明**: - 实现符合规约，无需整改。可选增强：错误信息可包含实际非法值（如 `f"max_posts must be positive, got {config.max_posts}"`）以提升可诊断性，但这不属于本条款强制要求。
- **账本证据索引**: E1(minisite/config.py:1-51 [snippet_body]), E2(minisite/exceptions.py:1-37 [snippet_body]), E3(minisite/config.py:50-92 [snippet_body]), E4(minisite/config.py:92-98 [snippet_body]), E5(minisite/config.py:41-41 [text_match]), E6(minisite/config.py:97-97 [text_match])

### [R3] build 异常分支必须记录 warning 日志 — ❌ VIOLATED（违规）
- **规约要求**: BuildEngine.build 在渲染与写盘流程中捕获异常后，除抛出 RenderError 外，必须使用标准库 logging.warning 记录告警日志。 （出处: `eval/fixtures/sample_spec.md:13-13`）
- **适用范围**: BuildEngine.build 的渲染与写盘流程中的异常捕获分支
- **判决结论**: - `BuildEngine.build` 在渲染与写盘流程的 `except Exception` 分支中仅 `raise RenderError(...) from exc`，未调用 `logging.warning` 记录告警日志 [E6]。
- 例外条款不成立：直接上游入口 `run_cli` 捕获 `MiniSiteError` 时只 `print(..., file=sys.stderr)`，未调用 `logging.warning` [E5]；且全仓 `*.py` 中除一处 argparse help 字符串外无任何 `logging` 引用 [E7]。

**代码证据与事实**:
- [E6] [minisite/engine.py:80-102] `build()` 循环内 `try` 包裹 `doc.render_html()` 与 `out_path.write_text(...)`，`except Exception as exc:` 分支唯一语句为 `raise RenderError(doc.slug, str(exc)) from exc`，无任何日志调用。
- [E1] [minisite/engine.py:1-40] 模块导入区仅有 `pathlib`、`typing`、`minisite.config`、`minisite.exceptions`、`minisite.models`，未导入 `logging`。
- [E5] [minisite/cli.py:55-85] `run_cli` 的 `except MiniSiteError as err:` 分支为 `print(f"Error [{err.code}]: {err.message}", file=sys.stderr); return 1`，`except Exception as exc:` 分支为 `print(f"Unexpected crash: {exc}", file=sys.stderr); return 2`，均未调用 `logging.warning`，故例外条款不满足。
- [E2] [minisite/cli.py:1-56] `cli.py` 导入区为 `argparse`、`sys`、`pathlib`、`typing`、`minisite.config`、`minisite.engine`、`minisite.exceptions`，未导入 `logging`。
- [E7] [minisite/cli.py:43-43] 全仓 `*.py` 中 `logging` 唯一命中为 argparse 的 help 文本 `"Enable verbose output logging"`，属字符串字面量，非日志调用。
- [E3] [minisite/exceptions.py:31-37] `RenderError.__init__(item_name, reason)` 仅构造消息与 `code="ERR_RENDER"`，不涉及日志记录。
- [E4] [minisite/engine.py:41-83] `build()` 之前的 `discover_documents`/`_parse_file` 无异常捕获分支，确认异常捕获点仅存在于 `build()` 内。

- **已调查范围（程序实录）**: candidate:minisite/engine.py:80-102；candidate:minisite/cli.py:48-76；candidate:minisite/exceptions.py:31-37；candidate:minisite/engine.py:11-102；candidate:minisite/exceptions.py:6-12；read:minisite/engine.py:1-110；read:minisite/cli.py:1-90；read:minisite/exceptions.py:1-45；read:minisite/engine.py:41-102；read:minisite/cli.py:55-85；read:minisite/engine.py:80-102；search:logging (*.py) -> 1 hits
- **缺失线索/盲区**: - 已读取：`minisite/engine.py:1-102`（含 `BuildEngine` 全类与 `build` 完整实现）、`minisite/cli.py:1-85`（含 `run_cli` 完整实现）、`minisite/exceptions.py:1-37`（`MiniSiteError`/`RenderError` 等）。
- 已检索：`search_text("logging", file_pattern="*.py")` 全仓 6 个 Python 文件，仅 1 处字符串命中。
- 缺失线索：未逐一读取 `minisite/models.py`、`minisite/config.py` 正文（其不属 `build` 渲染/写盘异常分支，且全仓 `logging` 检索已覆盖）；未检查是否存在非 `.py` 的日志配置（如 `logging.conf`），但该配置无法替代代码中的 `logging.warning` 调用，不影响判决。
- **整改建议/说明**: - 在 `minisite/engine.py` 顶部 `import logging`，并在 `build()` 的 `except Exception as exc:` 分支中于 `raise RenderError(...)` 之前加入 `logging.warning("Failed to render '%s': %s", doc.slug, exc)`（或使用模块级 `logger = logging.getLogger(__name__)` 后 `logger.warning(...)`，但需与条款“标准库 logging.warning”表述保持一致）。
- 若选择依赖例外条款，则需在 `run_cli` 的 `except MiniSiteError` 分支中补充 `logging.warning(...)` 调用；当前实现为 `print` 到 stderr，不构成日志记录，故不满足例外。
- **账本证据索引**: E1(minisite/engine.py:1-40 [snippet_body]), E2(minisite/cli.py:1-56 [snippet_body]), E3(minisite/exceptions.py:1-37 [snippet_body]), E4(minisite/engine.py:41-83 [snippet_body]), E5(minisite/cli.py:55-85 [snippet_body]), E6(minisite/engine.py:80-102 [snippet_body]), E7(minisite/cli.py:43-43 [text_match])

### [R4] 禁止使用 os.system — ✅ COMPLIANT（符合）
- **规约要求**: 工程源码中严禁直接使用 os.system 执行系统命令；如需调用外部命令，必须使用标准库 subprocess 模块替代。 （出处: `eval/fixtures/sample_spec.md:17-17`）
- **适用范围**: 全仓工程源码
- **判决结论**: - 全仓 10 个文件经 `os.system`、`system`、`subprocess|popen|Popen|check_output|check_call|os.exec|os.spawn|shell=True`、`import os` 多轮文本检索均无任何命中，且已读取的 `minisite/cli.py`、`minisite/engine.py` 正文中不存在任何外部命令执行调用，故判定符合“禁止使用 os.system”条款。
- 工程为纯静态站点生成器（文件读写 + argparse），本身不含调用外部命令的业务场景，因此不存在需以 subprocess 替代的违规点。

**代码证据与事实**:
- [E1] [minisite/cli.py:3-3] 导入 `argparse`；[E2] [minisite/cli.py:4-4] 导入 `sys`；[E3] [minisite/cli.py:5-5] 导入 `pathlib.Path`；[E4] [minisite/cli.py:6-6] 导入 `typing`；[E5] [minisite/cli.py:8-8] 导入 `minisite.config.load_config`——均非 `os` 或命令执行模块。
- [E6] [minisite/config.py:3-3] 导入 `json`；[E7] [minisite/config.py:4-4] 导入 `dataclasses`；[E8] [minisite/config.py:5-5] 导入 `pathlib.Path`；[E9] [minisite/config.py:6-6] 导入 `typing`；[E10] [minisite/config.py:8-8] 导入 `minisite.exceptions.ConfigError`。
- [E11] [minisite/engine.py:3-3] 导入 `pathlib.Path`；[E12] [minisite/engine.py:4-4] 导入 `typing`；[E13] [minisite/engine.py:6-6] 导入 `SiteConfig`；[E14] [minisite/engine.py:7-7] 导入 `RenderError, SourceNotFoundError`；[E15] [minisite/engine.py:8-8] 导入 `BaseItem, Page, Post`。
- [E16] [minisite/exceptions.py:3-3] 导入 `typing.Optional`；[E17] [minisite/exceptions.py:6-6] 定义 `class MiniSiteError(Exception)`；[E18] [minisite/exceptions.py:9-9] 定义 `__init__(self, message, code)`；[E19] [minisite/exceptions.py:15-15] 定义 `class ConfigError(MiniSiteError)`；[E20] [minisite/exceptions.py:18-18] 定义 `ConfigError.__init__`。
- [E21] [minisite/models.py:3-3] 导入 `abc`；[E22] [minisite/models.py:4-4] 导入 `dataclasses`；[E23] [minisite/models.py:5-5] 导入 `datetime`；[E24] [minisite/models.py:6-6] 导入 `pathlib.Path`；[E25] [minisite/models.py:7-7] 导入 `typing`。
- [E26] [minisite/cli.py:1-56] 正文为 argparse 参数构造与 `run_cli` 前半段（`load_config`、`BuildEngine` 实例化），无任何命令执行调用。
- [E28] [minisite/cli.py:57-85] 正文为 dry-run 分支、`engine.build()`、异常捕获与 `main()` 中 `sys.exit(run_cli())`，无 `os.system` 或 subprocess 调用。
- [E27] [minisite/engine.py:1-40] 正文为 `BuildEngine.__init__` 与 `discover_documents`（`is_dir`、`rglob`、`read_text`），仅文件系统操作。
- [E29] [minisite/engine.py:41-83] 正文为 `Post` 构造、草稿过滤、`_parse_file` 行级元数据解析与 `build()` 起始部分，无命令执行。
- 检索事实：`os.system` 命中 0 处；`system` 命中 0 处；`subprocess|popen|Popen|check_output|check_call|os.exec|os.spawn|shell=True` 命中 0 处；`import os` 命中 0 处；`lookup_symbol("system")` 静态符号表 0 匹配。

- **已调查范围（程序实录）**: search:os.system -> 0 hits；search:system -> 0 hits；search:subprocess|popen|Popen|check_output|check_call|os\.exec|os\.spawn|shell=True -> 0 hits；search:import os -> 0 hits；search:def |class |import -> 25 hits；symbol:system -> 0 hits；read:minisite/cli.py:1-80；read:minisite/engine.py:1-80；read:minisite/cli.py:57-85；read:minisite/engine.py:41-102
- **缺失线索/盲区**: - 已检索关键词：`os.system`、`system`、`subprocess|popen|Popen|check_output|check_call|os.exec|os.spawn|shell=True`、`import os`、`def |class |import `；已调用 `lookup_symbol("system")`。
- 已读取正文：`minisite/cli.py:1-85`（全文件）、`minisite/engine.py:1-83`。
- 未读取正文：`minisite/config.py`、`minisite/models.py`、`minisite/exceptions.py` 的完整函数体（仅通过导入行与符号定义行核实，未逐行读取实现）；`minisite/engine.py:84-102`（`build()` 尾部）未读取。
- 盲区：全仓扫描文件数为 10，若存在非 Python 脚本（如 shell/Makefile）或运行时动态拼接（`__import__`、`getattr`、字符串拼接模块名）调用，本次未做专项检索；但已确认无 `import os`，动态调用 `os.system` 需先获得 `os` 模块引用，风险极低。
- **整改建议/说明**: - 无需整改。建议后续若新增调用外部命令的需求（如调用外部渲染器），统一使用 `subprocess.run([...], check=True)` 并避免 `shell=True`，以持续满足本条款。
- **账本证据索引**: E1(minisite/cli.py:3-3 [text_match]), E2(minisite/cli.py:4-4 [text_match]), E3(minisite/cli.py:5-5 [text_match]), E4(minisite/cli.py:6-6 [text_match]), E5(minisite/cli.py:8-8 [text_match]), E6(minisite/config.py:3-3 [text_match]), E7(minisite/config.py:4-4 [text_match]), E8(minisite/config.py:5-5 [text_match]), E9(minisite/config.py:6-6 [text_match]), E10(minisite/config.py:8-8 [text_match]), E11(minisite/engine.py:3-3 [text_match]), E12(minisite/engine.py:4-4 [text_match]), E13(minisite/engine.py:6-6 [text_match]), E14(minisite/engine.py:7-7 [text_match]), E15(minisite/engine.py:8-8 [text_match]), E16(minisite/exceptions.py:3-3 [text_match]), E17(minisite/exceptions.py:6-6 [text_match]), E18(minisite/exceptions.py:9-9 [text_match]), E19(minisite/exceptions.py:15-15 [text_match]), E20(minisite/exceptions.py:18-18 [text_match]), E21(minisite/models.py:3-3 [text_match]), E22(minisite/models.py:4-4 [text_match]), E23(minisite/models.py:5-5 [text_match]), E24(minisite/models.py:6-6 [text_match]), E25(minisite/models.py:7-7 [text_match]), E26(minisite/cli.py:1-56 [snippet_body]), E27(minisite/engine.py:1-40 [snippet_body]), E28(minisite/cli.py:57-85 [snippet_body]), E29(minisite/engine.py:41-83 [snippet_body])
- **核验提示**: ⚠️ 引用 [minisite/config.py:3-3] 仅命中 text_match（E6），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/config.py:4-4] 仅命中 text_match（E7），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/config.py:5-5] 仅命中 text_match（E8），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/config.py:6-6] 仅命中 text_match（E9），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/config.py:8-8] 仅命中 text_match（E10），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/exceptions.py:3-3] 仅命中 text_match（E16），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/exceptions.py:6-6] 仅命中 text_match（E17），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/exceptions.py:9-9] 仅命中 text_match（E18），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/exceptions.py:15-15] 仅命中 text_match（E19），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/exceptions.py:18-18] 仅命中 text_match（E20），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/models.py:3-3] 仅命中 text_match（E21），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/models.py:4-4] 仅命中 text_match（E22），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/models.py:5-5] 仅命中 text_match（E23），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/models.py:6-6] 仅命中 text_match（E24），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/models.py:7-7] 仅命中 text_match（E25），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 探查阶段触及预算上限后由预留总结收口（explore_turns_exhausted(5/5)），建议人工复核完整度

### [R5] 生产配置文件 max_posts 上限 — ⚠️ INSUFFICIENT_EVIDENCE（证据不足）
- **规约要求**: 若仓库提供生产环境配置文件 minisite.json，其 max_posts 配置值必须小于等于 50。 （出处: `eval/fixtures/sample_spec.md:21-21`）
- **适用范围**: 仓库内生产环境配置文件 minisite.json 的 max_posts 字段
- **判决结论**: - 本次审计未能在仓库中找到任何生产环境配置文件 minisite.json（全仓 *.json 文件扫描结果为 0 个文件），因此无法核实该文件中 max_posts 的实际取值是否小于等于 50。依据条款前提与人工补充说明，缺失配置文件时必须判为证据不足，禁止判合规或违规。

**代码证据与事实**:
- [E1] [minisite/config.py:1-51] 定义 DEFAULT_CONFIG_FILENAME = "minisite.json"（第 10 行），SiteConfig 数据类中 max_posts 默认值为 100（第 24 行），validate_config 起始部分校验 title 与 theme。
- [E4] [minisite/config.py:52-93] validate_config 对 max_posts 仅做 `config.max_posts <= 0` 的正数校验（第 56-57 行），不存在任何 50 上限约束；load_config 在 config_path 为 None 时直接返回默认 SiteConfig（第 72-73 行），即不读取任何文件。
- [E10] [minisite/config.py:94-98] load_config 从 JSON 读取 `max_posts=int(data.get("max_posts", 100))`（第 94 行），随后调用 validate_config 并返回。
- [E13] [minisite/cli.py:1-45] create_parser 中 --config 参数 default=None（第 23 行），帮助文本仅说明“default: minisite.json if present”（第 24 行），代码未自动定位或加载仓库内 minisite.json。
- [E2] [minisite/cli.py:24-24] 与 [E3] [minisite/config.py:10-10] 为全仓 “minisite.json” 字面量的全部命中，均为常量/帮助文本，非配置文件实体。
- [E5] [minisite/config.py:24-24]、[E6] [minisite/config.py:36-36]、[E7] [minisite/config.py:56-56]、[E8] [minisite/config.py:57-57]、[E9] [minisite/config.py:94-94] 为全仓 max_posts 的全部命中，均位于 config.py，无任何 JSON 配置文件命中。
- [E11] [minisite/cli.py:24-24]、[E12] [minisite/config.py:10-10] 为全仓 “.json” 正则命中的全部结果，同样不含配置文件实体。

- **已调查范围（程序实录）**: candidate:minisite/config.py:41-57；candidate:minisite/cli.py:13-45；read:minisite/config.py:1-80；search:minisite.json -> 2 hits；read:minisite/config.py:52-98；search:max_posts -> 5 hits；read:minisite/config.py:94-98；search:\.json -> 2 hits；read:minisite/cli.py:1-45；search:max_posts|"title" (*.json) -> 0 hits；search:max_posts (*.md) -> 0 hits；search:config (*.json) -> 0 hits
- **缺失线索/盲区**: - 已读取正文：minisite/config.py 第 1-98 行（全文）、minisite/cli.py 第 1-45 行。
- 已执行检索：关键词 “minisite.json”（全仓，2 处命中，均为常量/帮助文本）；关键词 “max_posts”（全仓，5 处命中，全部在 config.py）；正则 “\.json”（全仓，2 处命中）；file_pattern=*.json 下检索 “max_posts|"title"” 与 “config”（scanned_files=0，仓库内无任何 .json 文件）；file_pattern=*.md 下检索 “max_posts”（1 个 md 文件，无命中）。
- 缺失线索：仓库内不存在 minisite.json 生产配置文件实体，无法读取其 max_posts 字段值；也未核实是否存在其他扩展名/隐藏目录下的等价生产配置（如 .minisite.json、config/*.json 等），但 *.json 全仓扫描为 0 已基本排除常规 JSON 配置存在。
- **整改建议/说明**: - 若该仓库本应提供生产环境配置文件 minisite.json，建议补充该文件并在其中显式声明 max_posts（取值 ≤ 50），以便规约可被实际校验；同时可考虑在 validate_config 中增加 max_posts ≤ 50 的上限校验，使约束在代码层可强制执行（当前仅有正数校验，见 [E4]）。
- 若该仓库设计上不提供生产配置文件（配置完全由 CLI 传入），则应明确该条款不适用于本仓库，或调整规约适用范围；在现有证据下按条款前提只能判为证据不足。
- **账本证据索引**: E1(minisite/config.py:1-51 [snippet_body]), E2(minisite/cli.py:24-24 [text_match]), E3(minisite/config.py:10-10 [text_match]), E4(minisite/config.py:52-93 [snippet_body]), E5(minisite/config.py:24-24 [text_match]), E6(minisite/config.py:36-36 [text_match]), E7(minisite/config.py:56-56 [text_match]), E8(minisite/config.py:57-57 [text_match]), E9(minisite/config.py:94-94 [text_match]), E10(minisite/config.py:94-98 [snippet_body]), E11(minisite/cli.py:24-24 [text_match]), E12(minisite/config.py:10-10 [text_match]), E13(minisite/cli.py:1-45 [snippet_body])
- **核验提示**: ⚠️ 探查阶段触及预算上限后由预留总结收口（explore_turns_exhausted(5/5)），建议人工复核完整度


## 核查配置与 S2 定位分离统计

- **规约文件**: `D:/Dev/code/agent/agent-project/eval/fixtures/sample_spec.md`
- **检索策略**: `hybrid` | **人工确认模式**: `human-in-the-loop`
- **S2 自主定位成绩（纯自动，不含人工补充）**: 覆盖规则 4/5 条（空命中 1 条），自动候选共 11 处
- **S2 人工辅助与最终生效（单独计列）**: 人工介入 1 条规则（+0 新增 / -1 排除），最终生效覆盖 4/5 条，送审候选共 10 处
