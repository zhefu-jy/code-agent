# 规范一致性审计报告

- **目标工程**: `D:/Dev/code/agent/agent-project/eval/fixtures/mini_repo`
- **代码修订版本**: `ea344dc5e207433348d76a66a497d67db2e04c89(dirty)`
- **四态汇总**: 共 7 条 | ✅ 符合 3 | ❌ 违规 1 | ⚠️ 证据不足 2 | ➖ 不适用 1 | 🛡️ 程序降级 2
- **成本基线**: 工具调用 56 次 | 总 Token 157,980 | 总耗时 58.19s

## 审计告警
- ⚠️ R4: 自动定位未找到候选代码，可人工补充或交由审计阶段按证据不足处理。
- ⚠️ R6: 自动定位未找到候选代码，可人工补充或交由审计阶段按证据不足处理。
- ⚠️ R6: 已触发程序降级 -> INSUFFICIENT_EVIDENCE（回答包含 2 处未在已读账本中核验通过的物理行号引用）
- ⚠️ R7: 已触发程序降级 -> INSUFFICIENT_EVIDENCE（回答包含 3 处未在已读账本中核验通过的物理行号引用）

## 四态审计矩阵对比表

| 规则 | 标题 | 最终判决 | 模型原判 | S2候选(人工介入) | 引用核验(正文/坐标/未验) | 工具/Token/耗时 | 核心结论 |
|---|---|---|---|---|---|---|---|
| R1 | max_posts 默认值上限 | ✅ COMPLIANT（符合） | COMPLIANT | 1 (无) | 6/0/0 | 5次 / 16,066 / 6.00s | `SiteConfig.max_posts` 的字段默认值被定义为 `100`，满足“默认值 <= 100”的约束（等… |
| R2 | validate_config 拒绝非法 max_po… | ✅ COMPLIANT（符合） | COMPLIANT | 2 (无) | 4/1/0 | 4次 / 11,869 / 5.12s | - `validate_config` 在 `minisite/config.py:56-57` 明确检查 `conf… |
| R3 | build 异常分支必须记录 warning 日志 | ❌ VIOLATED（违规） | VIOLATED | 5 (无) | 8/0/0 | 9次 / 29,052 / 8.97s | - `BuildEngine.build` 在渲染与写盘流程的 `except Exception` 分支中仅 `ra… |
| R4 | 禁用 os.system 执行系统命令 | ✅ COMPLIANT（符合） | COMPLIANT | 0 (无) | 11/12/0 | 10次 / 27,204 / 11.11s | - 全仓 10 个文件经 `os.system`、`system`、`subprocess`、`import os` … |
| R5 | 生产配置文件 max_posts 上限 | ➖ NOT_APPLICABLE（不适用） | NOT_APPLICABLE | 3 (无) | 13/0/0 | 10次 / 25,733 / 9.48s | - 该条款仅适用于仓库内实际存在生产环境配置文件 `minisite.json` 的情形；经全仓检索，仓库中不存在任何… |
| R6 | 外部 HTTP 请求超时上限 | ⚠️ INSUFFICIENT_EVIDENCE（证据不足） | NOT_APPLICABLE → 降级 | 0 (无) | 6/1/2 | 8次 / 25,436 / 9.67s | - 工程 `minisite` 是一个纯本地静态站点生成器，仅做本地文件发现、解析与 HTML 输出，全仓不存在任何外… |
| R7 | 捕获 ConnectionError 必须记录 war… | ⚠️ INSUFFICIENT_EVIDENCE（证据不足） | NOT_APPLICABLE → 降级 | 4 (无) | 8/3/3 | 10次 / 22,620 / 7.83s | - 该工程是一个仅使用标准库的静态站点生成器，全仓不存在任何外部 HTTP 请求客户端，也不存在任何 `Connect… |

## 逐条规则审计明细

### [R1] max_posts 默认值上限 — ✅ COMPLIANT（符合）
- **规约要求**: SiteConfig.max_posts 的默认值必须小于等于 100（不得超过 100）。 （出处: `eval/fixtures/sample_spec.md:9-9`）
- **适用范围**: SiteConfig.max_posts 字段定义（默认值）
- **判决结论**: `SiteConfig.max_posts` 的字段默认值被定义为 `100`，满足“默认值 <= 100”的约束（等于上限，未超过）。`load_config` 中读取 JSON 时的兜底默认值同样为 `100`，与字段默认值口径一致，未发现任何覆盖为更大值的实现。

**代码证据与事实**:
- [E1] [minisite/config.py:14-25] `@dataclass class SiteConfig` 的字段声明中，`max_posts: int = 100`（第 24 行），默认值恰为 100，符合“<= 100”的上限要求。
- [E2] [minisite/config.py:15-38] 符号索引确认 `minisite.config.SiteConfig` 为类定义，范围 15-38 行，与读取到的正文一致，无其他同名定义。
- [E3] [minisite/config.py:56-57] `validate_config` 仅校验 `config.max_posts <= 0` 时抛 `ConfigError`，未对默认值做任何改写或提升。
- [E4] [minisite/config.py:24-24] 全仓 `max_posts` 搜索命中该字段默认值行，确认默认值字面量为 100。
- [E8] [minisite/config.py:94-94] `load_config` 中 `max_posts=int(data.get("max_posts", 100))`，缺省兜底值同为 100，未出现大于 100 的默认口径。
- [E9] [minisite/config.py:93-98] 构造 `SiteConfig` 后立即调用 `validate_config(cfg)` 并返回，无 `__post_init__` 或其他后置改写默认值的逻辑。

- **已调查范围（程序实录）**: candidate:minisite/config.py:15-38；read:minisite/config.py:1-60；symbol:SiteConfig -> 1 hits；read:minisite/config.py:52-98；search:max_posts -> 5 hits；read:minisite/config.py:93-98
- **缺失线索/盲区**: - 读取：`minisite/config.py` 全文（1-98 行，分三次读取覆盖完整文件）。
- 符号：`lookup_symbol("SiteConfig")`，仅 1 处定义，无子类或重写。
- 搜索：`search_text("max_posts")` 全仓（扫描 10 个文件），命中 5 处，全部位于 `minisite/config.py`，无其他模块定义或覆盖默认值。
- 缺失线索：未检查测试夹具/文档中是否存在对默认值的断言性描述（不影响本条款“字段默认值”口径的判定）；未发现动态注入默认值的配置层。
- **整改建议/说明**: - 当前实现合规：默认值 100 处于“<= 100”边界内。若后续规范收紧为“< 100”，需同步调整第 24 行字段默认值与第 94 行 JSON 兜底默认值，避免两处口径不一致。
- **账本证据索引**: E1(minisite/config.py:1-51 [snippet_body]), E2(minisite/config.py:15-38 [symbol_signature]), E3(minisite/config.py:52-93 [snippet_body]), E4(minisite/config.py:24-24 [text_match]), E5(minisite/config.py:36-36 [text_match]), E6(minisite/config.py:56-56 [text_match]), E7(minisite/config.py:57-57 [text_match]), E8(minisite/config.py:94-94 [text_match]), E9(minisite/config.py:93-98 [snippet_body])

### [R2] validate_config 拒绝非法 max_posts — ✅ COMPLIANT（符合）
- **规约要求**: validate_config 必须拒绝 max_posts <= 0 的配置，并抛出 ConfigError。 （出处: `eval/fixtures/sample_spec.md:9-9`）
- **适用范围**: 配置校验函数 validate_config
- **判决结论**: - `validate_config` 在 `minisite/config.py:56-57` 明确检查 `config.max_posts <= 0` 并抛出 `ConfigError`，与条款要求完全一致。
- 抛出的异常类型经核实为 `minisite.exceptions.ConfigError`（继承 `MiniSiteError`），符合“异常类型必须为 ConfigError”的约束。

**代码证据与事实**:
- [E1] [minisite/config.py:41-51] `validate_config(config: SiteConfig) -> None` 定义存在，docstring 声明 `Raises: ConfigError: If any configuration value is invalid.`，且第 8 行 `from minisite.exceptions import ConfigError` 为真实导入。
- [E3] [minisite/config.py:56-57] 函数体内存在守卫 `if config.max_posts <= 0:` 后紧跟 `raise ConfigError("max_posts must be positive integer", field_name="max_posts")`，即对 `max_posts <= 0` 抛出 `ConfigError`。
- [E2] [minisite/exceptions.py:15-20] `class ConfigError(MiniSiteError)` 定义，`__init__(self, message, field_name=None)` 调用 `super().__init__(message, code="ERR_CONFIG")`，确认抛出类型确为 `ConfigError` 而非其他异常。
- [E4] [minisite/config.py:24-24] `max_posts: int = 100` 为 `SiteConfig` 的整型字段，`<= 0` 比较语义成立。
- [E8] [minisite/config.py:94-94] `load_config` 中 `max_posts=int(data.get("max_posts", 100))` 构造配置，说明该字段来自外部配置输入，校验路径具备实际业务意义。

- **已调查范围（程序实录）**: candidate:minisite/config.py:41-57；candidate:minisite/exceptions.py:15-20；read:minisite/config.py:1-80；read:minisite/exceptions.py:1-40；read:minisite/config.py:50-98；search:max_posts -> 5 hits
- **缺失线索/盲区**: - 已读取：`minisite/config.py:1-51`、`minisite/config.py:50-92`、`minisite/exceptions.py:1-37`。
- 已检索：全仓字面量 `max_posts`（10 个文件扫描，命中仅 `minisite/config.py` 的 5 处）。
- 未读取：`minisite/config.py:93-98`（`load_config` 尾部，仅涉及 `max_posts` 赋值与返回，不影响本条款判定）；未追踪 `validate_config` 的调用点，但条款仅约束该函数自身行为，不构成缺口。
- 无明显缺口。
- **整改建议/说明**: - 实现与条款一致，无需整改。补充说明：当前校验仅覆盖 `<= 0`，若后续规范要求拒绝非整数或超上限值，需另行扩展；本条不涉及。
- **账本证据索引**: E1(minisite/config.py:1-51 [snippet_body]), E2(minisite/exceptions.py:1-37 [snippet_body]), E3(minisite/config.py:50-92 [snippet_body]), E4(minisite/config.py:24-24 [text_match]), E5(minisite/config.py:36-36 [text_match]), E6(minisite/config.py:56-56 [text_match]), E7(minisite/config.py:57-57 [text_match]), E8(minisite/config.py:94-94 [text_match])
- **核验提示**: ⚠️ 引用 [minisite/config.py:94-94] 仅命中 text_match（E8），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。

### [R3] build 异常分支必须记录 warning 日志 — ❌ VIOLATED（违规）
- **规约要求**: BuildEngine.build 在渲染与写盘流程中捕获异常后，除抛出 RenderError 外，必须使用标准库 logging.warning 记录告警日志。 （出处: `eval/fixtures/sample_spec.md:13-13`）
- **适用范围**: BuildEngine.build 的渲染与写盘流程中的异常捕获分支
- **判决结论**: - `BuildEngine.build` 在渲染与写盘流程的 `except Exception` 分支中仅 `raise RenderError(...) from exc`，未调用 `logging.warning` 记录告警日志 [E6]。
- 例外条款不成立：直接上游入口 `run_cli` 捕获 `MiniSiteError` 时只调用 `print(..., file=sys.stderr)`，未使用 `logging.warning` [E5]；且全仓 `.py` 文件中不存在 `import logging`、`logger`、`warn` 等任何日志调用 [E7][E8]。

**代码证据与事实**:
- [E6] [minisite/engine.py:80-102] `build()` 的循环体内 `try` 包裹 `doc.render_html()` 与 `out_path.write_text(...)`，`except Exception as exc:` 分支只有 `raise RenderError(doc.slug, str(exc)) from exc`，无任何 `logging.warning` 调用。
- [E1] [minisite/engine.py:1-40] 模块导入区仅有 `pathlib`、`typing`、`minisite.config`、`minisite.exceptions`、`minisite.models`，未导入 `logging`。
- [E5] [minisite/cli.py:55-85] `run_cli` 的 `except MiniSiteError as err:` 分支为 `print(f"Error [{err.code}]: {err.message}", file=sys.stderr); return 1`，`except Exception as exc:` 分支为 `print(f"Unexpected crash: {exc}", file=sys.stderr); return 2`，均未调用 `logging.warning`，故例外条款不满足。
- [E2] [minisite/cli.py:1-56] `cli.py` 导入区为 `argparse`、`sys`、`pathlib`、`typing`、`minisite.config`、`minisite.engine`、`minisite.exceptions`，未导入 `logging`。
- [E7] [minisite/cli.py:43-43] 全仓 `*.py` 中唯一命中 "logging" 的位置是 argparse 帮助字符串 `help="Enable verbose output logging"`，属文案而非日志调用。
- [E8] [minisite/cli.py:67-67] `BuildEngine.build` 的唯一调用点为 `count = engine.build()`，位于 `run_cli` 的 `try` 块内，其异常最终由上述无日志的 `except` 分支处理。
- [E3] [minisite/exceptions.py:31-37] `RenderError` 继承 `MiniSiteError`，构造签名为 `(item_name, reason)`，与 `build` 中的抛出方式一致（抛出行为本身符合前提要求）。
- [E4] [minisite/engine.py:41-83] `build` 方法定义起始于第 80 行，其前无其他异常捕获分支或日志辅助方法。

- **已调查范围（程序实录）**: candidate:minisite/engine.py:80-102；candidate:minisite/cli.py:48-76；candidate:minisite/exceptions.py:31-37；candidate:minisite/engine.py:11-102；candidate:minisite/exceptions.py:6-12；read:minisite/engine.py:1-110；read:minisite/cli.py:1-90；read:minisite/exceptions.py:1-45；read:minisite/engine.py:41-102；read:minisite/cli.py:55-85；read:minisite/engine.py:80-102；search:logging (*.py) -> 1 hits；search:import logging|logger|warn (*.py) -> 0 hits；refs:BuildEngine.build -> 1 hits
- **缺失线索/盲区**: - 已读取：`minisite/engine.py` 全文（1-102 行，含 `BuildEngine`、`discover_documents`、`_parse_file`、`build`）、`minisite/cli.py` 全文（1-85 行，含 `create_parser`、`run_cli`、`main`）、`minisite/exceptions.py` 全文（1-37 行）。
- 已检索关键词：`logging`（`*.py`，1 处命中且为帮助文案）、正则 `import logging|logger|warn`（`*.py`，0 命中）；已用 `find_references` 追踪 `BuildEngine.build` 调用点（仅 `cli.py:67`）。
- 缺失线索：未逐一读取 `minisite/config.py`、`minisite/models.py` 正文（搜索已覆盖全仓 6 个 `.py` 文件，确认无日志调用，故不影响结论）；未检查是否存在非 `.py` 的日志配置（如 `logging.conf`），但该配置无法替代代码中缺失的 `logging.warning` 调用。
- **整改建议/说明**: - 在 `minisite/engine.py` 顶部添加 `import logging`，并在 `build()` 的 `except Exception as exc:` 分支中，于 `raise RenderError(...)` 之前加入 `logging.warning("Failed to render '%s': %s", doc.slug, exc)`（或使用模块级 `logger = logging.getLogger(__name__)` 后 `logger.warning(...)`）。
- 若选择依赖例外条款，则需将 `run_cli` 的 `except MiniSiteError` 分支改为调用 `logging.warning(...)` 记录告警（可保留 stderr 输出），当前实现为 `print`，不满足例外条件。
- **账本证据索引**: E1(minisite/engine.py:1-40 [snippet_body]), E2(minisite/cli.py:1-56 [snippet_body]), E3(minisite/exceptions.py:1-37 [snippet_body]), E4(minisite/engine.py:41-83 [snippet_body]), E5(minisite/cli.py:55-85 [snippet_body]), E6(minisite/engine.py:80-102 [snippet_body]), E7(minisite/cli.py:43-43 [text_match]), E8(minisite/cli.py:67-67 [reference_match])

### [R4] 禁用 os.system 执行系统命令 — ✅ COMPLIANT（符合）
- **规约要求**: 工程源码中严禁直接使用 os.system 执行系统命令；如需调用外部命令，必须使用标准库 subprocess 模块替代。 （出处: `eval/fixtures/sample_spec.md:17-17`）
- **适用范围**: 全仓工程源码
- **判决结论**: - 全仓 10 个文件经 `os.system`、`system`、`subprocess`、`import os` 四组关键词全文检索均无任何命中，且已读取的 `minisite/cli.py`、`minisite/engine.py` 正文中不存在任何外部命令执行逻辑，故判定符合规约。

**代码证据与事实**:
- [E1] [minisite/cli.py:13-13] 存在 `create_parser()` 定义；[E2] [minisite/cli.py:48-48] 存在 `run_cli()`；[E3] [minisite/cli.py:79-79] 存在 `main()`。
- [E4] [minisite/config.py:27-27]、[E5] [minisite/config.py:41-41]、[E6] [minisite/config.py:60-60] 分别为 `to_dict()`、`validate_config()`、`load_config()` 定义。
- [E7] [minisite/engine.py:14-14]、[E8] [minisite/engine.py:20-20]、[E9] [minisite/engine.py:60-60]、[E10] [minisite/engine.py:80-80] 分别为 `BuildEngine.__init__`、`discover_documents()`、`_parse_file()`、`build()` 定义。
- [E11] [minisite/exceptions.py:9-9]、[E12] [minisite/exceptions.py:18-18]、[E13] [minisite/exceptions.py:26-26]、[E14] [minisite/exceptions.py:34-34] 为异常类构造函数定义。
- [E15] [minisite/models.py:21-21]、[E16] [minisite/models.py:27-27]、[E17] [minisite/models.py:38-38]、[E18] [minisite/models.py:55-55]、[E19] [minisite/models.py:62-62] 为模型类方法定义。
- [E20] [minisite/cli.py:1-56] 导入区仅含 `argparse`、`sys`、`pathlib.Path`、`typing` 及 `minisite` 内部模块，无 `os`、无 `subprocess`；参数解析仅注册 `--config`、`--include-drafts`、`--dry-run`、`--verbose`。
- [E21] [minisite/engine.py:1-40] 导入区仅含 `pathlib.Path`、`typing` 及内部模块；文档发现通过 `self.source_dir.rglob("*.txt")` 与 `file_path.read_text(encoding="utf-8")` 完成，无命令执行。
- [E22] [minisite/cli.py:57-85] `run_cli` 主体仅做配置加载、`BuildEngine` 构造、`discover_documents()`/`build()` 调用与 `print` 输出，异常处理为 `MiniSiteError` 与通用 `Exception`，无 `os.system` 或任何 shell 调用。
- [E23] [minisite/engine.py:41-83] 文档实例化、草稿过滤与 `_parse_file` 的逐行元数据解析均为纯内存字符串处理，无外部命令调用。
- 检索事实：`os.system`、`system`、`subprocess`、`import os` 四次全仓 `search_text` 均返回 0 命中（scanned_files=10，skipped_files=0）；`lookup_symbol("system")` 返回 0 匹配。

- **已调查范围（程序实录）**: search:os.system -> 0 hits；search:system -> 0 hits；search:subprocess -> 0 hits；search:import os -> 0 hits；search:def -> 19 hits；symbol:system -> 0 hits；read:minisite/cli.py:1-90；read:minisite/engine.py:1-110；read:minisite/cli.py:57-85；read:minisite/engine.py:41-102
- **缺失线索/盲区**: - 已检索关键词：`os.system`、`system`、`subprocess`、`import os`、`def `；已调用符号查询：`system`。
- 已读取正文：`minisite/cli.py:1-85`（完整）、`minisite/engine.py:1-83`。
- 未读取正文：`minisite/config.py`、`minisite/models.py`、`minisite/exceptions.py` 全文（仅通过 `def ` 命中行与符号表确认其函数签名，未逐行读取实现）；`minisite/engine.py:84-102`（`build()` 尾部 19 行）未读取。
- 盲区：全仓仅 10 个文件且 `skipped_files=0`，检索覆盖完整；但上述未读正文文件理论上仍存在未被关键词命中的间接命令执行路径（如通过 `eval`/`exec`/`__import__` 动态调用），本次预算内未进一步核实。
- **整改建议/说明**: - 当前实现为纯静态站点生成器，不涉及外部命令调用，规约要求自然满足，无需整改。
- 建议后续在 CI 中固化一条静态检查（如禁止 `os.system`、`os.popen`、`subprocess.*(shell=True)` 的 lint 规则），以便在新增命令执行需求时强制走 `subprocess` 且避免 `shell=True`，从机制上保障该条款持续成立。
- **账本证据索引**: E1(minisite/cli.py:13-13 [text_match]), E2(minisite/cli.py:48-48 [text_match]), E3(minisite/cli.py:79-79 [text_match]), E4(minisite/config.py:27-27 [text_match]), E5(minisite/config.py:41-41 [text_match]), E6(minisite/config.py:60-60 [text_match]), E7(minisite/engine.py:14-14 [text_match]), E8(minisite/engine.py:20-20 [text_match]), E9(minisite/engine.py:60-60 [text_match]), E10(minisite/engine.py:80-80 [text_match]), E11(minisite/exceptions.py:9-9 [text_match]), E12(minisite/exceptions.py:18-18 [text_match]), E13(minisite/exceptions.py:26-26 [text_match]), E14(minisite/exceptions.py:34-34 [text_match]), E15(minisite/models.py:21-21 [text_match]), E16(minisite/models.py:27-27 [text_match]), E17(minisite/models.py:38-38 [text_match]), E18(minisite/models.py:55-55 [text_match]), E19(minisite/models.py:62-62 [text_match]), E20(minisite/cli.py:1-56 [snippet_body]), E21(minisite/engine.py:1-40 [snippet_body]), E22(minisite/cli.py:57-85 [snippet_body]), E23(minisite/engine.py:41-83 [snippet_body])
- **核验提示**: ⚠️ 引用 [minisite/config.py:27-27] 仅命中 text_match（E4），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/config.py:41-41] 仅命中 text_match（E5），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/config.py:60-60] 仅命中 text_match（E6），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/exceptions.py:9-9] 仅命中 text_match（E11），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/exceptions.py:18-18] 仅命中 text_match（E12），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/exceptions.py:26-26] 仅命中 text_match（E13），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/exceptions.py:34-34] 仅命中 text_match（E14），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/models.py:21-21] 仅命中 text_match（E15），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/models.py:27-27] 仅命中 text_match（E16），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/models.py:38-38] 仅命中 text_match（E17），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/models.py:55-55] 仅命中 text_match（E18），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/models.py:62-62] 仅命中 text_match（E19），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 探查阶段触及预算上限后由预留总结收口（explore_turns_exhausted(5/5)），建议人工复核完整度

### [R5] 生产配置文件 max_posts 上限 — ➖ NOT_APPLICABLE（不适用）
- **规约要求**: 若仓库提供生产环境配置文件 minisite.json，其中 max_posts 配置值必须小于等于 50。 （出处: `eval/fixtures/sample_spec.md:21-21`）
- **适用范围**: 仓库内生产环境配置文件 minisite.json 的 max_posts 字段
- **判决结论**: - 该条款仅适用于仓库内实际存在生产环境配置文件 `minisite.json` 的情形；经全仓检索，仓库中不存在任何 `.json` 文件，`minisite.json` 仅作为代码中的默认文件名常量与帮助文本出现，因此条款前提不成立，判定为不适用。

**代码证据与事实**:
- [E1] [minisite/config.py:1-51] 文件头定义 `DEFAULT_CONFIG_FILENAME = "minisite.json"`（第 10 行），仅为常量字符串，非实际配置文件；`SiteConfig.max_posts` 默认值为 100（第 24 行），属代码默认值而非配置文件字段。
- [E2] [minisite/cli.py:24-24] `--config` 参数帮助文本提到 “default: minisite.json if present”，说明该文件是可选外部输入，仓库内并未提供。
- [E3] [minisite/config.py:10-10] `minisite.json` 在仓库中仅此一处作为常量出现。
- [E4] [minisite/config.py:24-24] `max_posts: int = 100` 为 dataclass 默认值，不属于生产配置文件内容。
- [E6] [minisite/config.py:56-57] `validate_config` 仅校验 `max_posts <= 0` 时抛错，未设置 50 上限；但该逻辑作用于运行时加载的配置对象，非仓库内配置文件本身。
- [E8] [minisite/config.py:94-94] `load_config` 从外部 JSON 读取 `max_posts`（默认 100），进一步印证配置来自运行时外部文件而非仓库内文件。
- [E9] [minisite/config.py:52-93] `load_config` 在 `config_path is None` 时直接返回默认 `SiteConfig()`，不读取任何仓库内文件。
- [E10] [minisite/cli.py:24-24] 全仓 `.json` 正则检索仅命中该帮助文本，无任何实际 JSON 文件路径。
- [E11] [minisite/config.py:10-10] 同上，`.json` 检索第二处命中为常量定义。
- [E13] [minisite/cli.py:1-45] `create_parser` 中 `--config` 默认值为 `None`，未硬编码指向仓库内 `minisite.json`。
- [E14] [minisite/__init__.py:1-4] 包根文件仅含版本号，无配置内容。

- **已调查范围（程序实录）**: candidate:minisite/config.py:41-57；candidate:minisite/cli.py:13-45；candidate:minisite/__init__.py:1-4；read:minisite/config.py:1-80；search:minisite.json -> 2 hits；search:max_posts -> 5 hits；read:minisite/config.py:52-98；search:\.json -> 2 hits；search:max_posts (*.json) -> 0 hits；read:minisite/config.py:94-98；read:minisite/cli.py:1-45；read:minisite/__init__.py:1-4；search:json (*.md) -> 0 hits
- **缺失线索/盲区**: - 已读取：`minisite/config.py`（1-98 全文）、`minisite/cli.py`（1-45）、`minisite/__init__.py`（1-4）。
- 已检索关键词：`minisite.json`（全仓，2 处命中，均为代码字符串）、`max_posts`（全仓，5 处命中，均在 config.py）、`\.json`（正则，全仓，2 处命中）、`max_posts` 限定 `*.json`（0 命中，scanned_files=0）、`json` 限定 `*.md`（0 命中）。
- 缺失线索：仓库共 10 个文本文件，检索未发现任何 `.json` 配置文件；若存在二进制或非 UTF-8 编码的配置文件则不在检索范围内，但按常规工程布局可排除。
- **整改建议/说明**: - 当前仓库未提供生产环境配置文件 `minisite.json`，条款前提不成立，无需整改。
- 补充提示（非本条款判定依据）：若后续新增 `minisite.json`，需确保其中 `max_posts <= 50`；同时注意代码默认值 `max_posts = 100`（[minisite/config.py:24-24]）与校验逻辑仅检查正数（[minisite/config.py:56-57]），若希望从代码层面强制 50 上限，建议在 `validate_config` 中增加上限校验。
- **账本证据索引**: E1(minisite/config.py:1-51 [snippet_body]), E2(minisite/cli.py:24-24 [text_match]), E3(minisite/config.py:10-10 [text_match]), E4(minisite/config.py:24-24 [text_match]), E5(minisite/config.py:36-36 [text_match]), E6(minisite/config.py:56-56 [text_match]), E7(minisite/config.py:57-57 [text_match]), E8(minisite/config.py:94-94 [text_match]), E9(minisite/config.py:52-93 [snippet_body]), E10(minisite/cli.py:24-24 [text_match]), E11(minisite/config.py:10-10 [text_match]), E12(minisite/config.py:94-98 [snippet_body]), E13(minisite/cli.py:1-45 [snippet_body]), E14(minisite/__init__.py:1-4 [snippet_body])

### [R6] 外部 HTTP 请求超时上限 — ⚠️ INSUFFICIENT_EVIDENCE（证据不足）
- **规约要求**: 外部 HTTP 请求客户端的单次请求超时必须小于等于 10 秒（即 10000 ms）。 （出处: `eval/fixtures/sample_spec.md:22-22`）
- **适用范围**: 工程内外部 HTTP 请求客户端（如 requests / httpx）
- **🛡️ 程序防幻觉降级**: 模型原判 `NOT_APPLICABLE` → 强制降级为 `INSUFFICIENT_EVIDENCE`（原因：回答包含 2 处未在已读账本中核验通过的物理行号引用）
- **判决结论**: - 工程 `minisite` 是一个纯本地静态站点生成器，仅做本地文件发现、解析与 HTML 输出，全仓不存在任何外部 HTTP 请求客户端（requests / httpx / urllib / aiohttp / http.client 等），也无任何 `timeout` 相关配置。
- 依据条款前提“仅当工程包含外部 HTTP 请求客户端时适用”，该条款在本工程无对应业务场景，故判 NOT_APPLICABLE。

**代码证据与事实**:
- [E1] [minisite/cli.py:3-9] CLI 仅导入 `argparse`、`sys`、`pathlib.Path`、`typing` 及本工程内部模块 `minisite.config`、`minisite.engine`、`minisite.exceptions`，无任何网络库导入。
- [E2] [minisite/config.py:3-8] 配置模块仅导入 `json`、`dataclasses`、`pathlib`、`typing` 与内部 `minisite.exceptions`，无网络库。
- [E3] [minisite/engine.py:3-8] 构建引擎仅导入 `pathlib`、`typing` 与内部 `config`/`exceptions`/`models`，无网络库。
- [E4] [minisite/models.py:3-7] 数据模型仅导入 `abc`、`dataclasses`、`datetime`、`pathlib`、`typing`，无网络库。
- [E5] [minisite/exceptions.py:3-3] 异常模块仅导入 `typing`，无网络库。
- [E6] [minisite/engine.py:20-58] `discover_documents` 通过 `self.source_dir.rglob("*.txt")` 与 `file_path.read_text(encoding="utf-8")` 读取本地文件，全部为本地文件 I/O，无任何远程请求。
- [E7] [minisite/engine.py:60-78] `_parse_file` 为纯字符串行解析（`splitlines`、`startswith("@")`），无 I/O 与网络行为。
- [E8] [minisite/cli.py:48-76] `run_cli` 仅解析参数、加载本地配置、构造 `BuildEngine` 并调用 `discover_documents`/`build`，无网络调用。
- [E9] [minisite/cli.py:79-85] `main` 仅调用 `run_cli` 并 `sys.exit`，无网络调用。
- [E10] 全仓 `search_text` 检索 `requests\.|httpx|urllib|aiohttp|http\.client|urlopen`（正则）与 `http`（字面量）均返回 0 命中，扫描 10 个文件、0 个跳过；检索 `timeout` 亦 0 命中。

- **已调查范围（程序实录）**: search:requests\.|httpx|urllib|aiohttp|http\.client|urlopen -> 0 hits；search:timeout -> 0 hits；search:import |def |class -> 30 hits；search:http -> 0 hits；read:minisite/engine.py:1-80；read:minisite/cli.py:1-80；read:minisite/engine.py:41-102；read:minisite/cli.py:57-85
- **缺失线索/盲区**: - 已检索关键词：`requests\.|httpx|urllib|aiohttp|http\.client|urlopen`（正则）、`http`、`timeout`、`import |def |class `（正则，用于枚举全仓符号与导入）。
- 已读取文件正文：`minisite/cli.py`（1-85 全量）、`minisite/engine.py`（1-83，剩余 84-102 未读）、`minisite/config.py`、`minisite/models.py`、`minisite/exceptions.py`（仅导入区与符号定义行）。
- 缺失线索：`minisite/engine.py:84-102`（`build` 方法尾部）未逐行读取；但该文件导入区已确认无网络库，且全仓 `http`/`timeout` 零命中，不影响“无外部 HTTP 客户端”的结论。工程共 10 个文件，未发现测试目录或依赖清单（如 requirements/pyproject）中的网络依赖线索。
- **整改建议/说明**: - 无需整改。当前工程不包含外部 HTTP 请求客户端，条款 R6 不适用。
- 若后续引入远程抓取能力（如拉取远程图片、外部 Markdown 源），需在新增客户端处显式设置 `timeout <= 10` 秒（如 `requests.get(url, timeout=10)` 或 `httpx.Client(timeout=10.0)`），届时本条款将转为适用并需重新审计。
- **账本证据索引**: E1(minisite/cli.py:3-3 [text_match]), E2(minisite/cli.py:4-4 [text_match]), E3(minisite/cli.py:5-5 [text_match]), E4(minisite/cli.py:6-6 [text_match]), E5(minisite/cli.py:8-8 [text_match]), E6(minisite/cli.py:9-9 [text_match]), E7(minisite/config.py:3-3 [text_match]), E8(minisite/config.py:4-4 [text_match]), E9(minisite/config.py:5-5 [text_match]), E10(minisite/config.py:6-6 [text_match]), E11(minisite/config.py:8-8 [text_match]), E12(minisite/config.py:15-15 [text_match]), E13(minisite/engine.py:3-3 [text_match]), E14(minisite/engine.py:4-4 [text_match]), E15(minisite/engine.py:6-6 [text_match]), E16(minisite/engine.py:7-7 [text_match]), E17(minisite/engine.py:8-8 [text_match]), E18(minisite/engine.py:11-11 [text_match]), E19(minisite/exceptions.py:3-3 [text_match]), E20(minisite/exceptions.py:6-6 [text_match]), E21(minisite/exceptions.py:9-9 [text_match]), E22(minisite/exceptions.py:15-15 [text_match]), E23(minisite/exceptions.py:18-18 [text_match]), E24(minisite/exceptions.py:23-23 [text_match]), E25(minisite/models.py:3-3 [text_match]), E26(minisite/models.py:4-4 [text_match]), E27(minisite/models.py:5-5 [text_match]), E28(minisite/models.py:6-6 [text_match]), E29(minisite/models.py:7-7 [text_match]), E30(minisite/models.py:11-11 [text_match]), E31(minisite/engine.py:1-40 [snippet_body]), E32(minisite/cli.py:1-56 [snippet_body]), E33(minisite/engine.py:41-83 [snippet_body]), E34(minisite/cli.py:57-85 [snippet_body])
- **核验提示**: ⚠️ 引用 [minisite/config.py:3-8] 超出账本实际返回行号区间，已标为未验证。
- **核验提示**: ⚠️ 引用 [minisite/models.py:3-7] 超出账本实际返回行号区间，已标为未验证。
- **核验提示**: ⚠️ 引用 [minisite/exceptions.py:3-3] 仅命中 text_match（E19），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。

### [R7] 捕获 ConnectionError 必须记录 warning 日志 — ⚠️ INSUFFICIENT_EVIDENCE（证据不足）
- **规约要求**: 捕获 ConnectionError 时必须记录 warning 级别日志。 （出处: `eval/fixtures/sample_spec.md:22-22`）
- **适用范围**: 外部 HTTP 请求客户端中捕获 ConnectionError 的分支
- **🛡️ 程序防幻觉降级**: 模型原判 `NOT_APPLICABLE` → 强制降级为 `INSUFFICIENT_EVIDENCE`（原因：回答包含 3 处未在已读账本中核验通过的物理行号引用）
- **判决结论**: - 该工程是一个仅使用标准库的静态站点生成器，全仓不存在任何外部 HTTP 请求客户端，也不存在任何 `ConnectionError` 捕获分支，因此条款 [R7] 的适用前提（“仅当工程包含外部 HTTP 请求客户端时适用”）不成立，判定为不适用。

**代码证据与事实**:
- [E1] [minisite/config.py:41-57] `validate_config` 仅做标题、主题、max_posts 的语义校验并抛出 `ConfigError`，与网络请求无关。
- [E2] [minisite/exceptions.py:1-37] 异常体系仅含 `MiniSiteError`、`ConfigError`、`SourceNotFoundError`、`RenderError`，无任何网络/连接类异常定义或处理。
- [E3] [minisite/engine.py:1-40] `BuildEngine` 仅通过 `pathlib` 读取本地 `.txt` 文件并解析，无 HTTP 调用。
- [E4] [minisite/cli.py:1-40] CLI 仅使用 `argparse`/`sys`/`pathlib` 构建参数解析，无网络相关导入或调用。
- [E16] [README.md:1-10] 工程自述为 “A minimal, standard-library static site builder”，架构仅含 exceptions/config/models/engine/cli 五个模块，无 HTTP 客户端组件。
- [E17-E39] [minisite/cli.py:3-10]、[minisite/config.py:3-8]、[minisite/engine.py:3-8]、[minisite/exceptions.py:3-3]、[minisite/models.py:3-7] 全部 Python 导入语句仅涉及 `argparse`、`sys`、`json`、`pathlib`、`typing`、`dataclasses`、`datetime`、`abc` 及本包内部模块，无 `requests`/`httpx`/`urllib`/`aiohttp`/`socket` 等网络库。
- [E5-E15] [minisite/cli.py:71-74]、[minisite/config.py:81-81]、[minisite/engine.py:99-99]、[minisite/models.py:59-59] 全仓 `except` 分支仅捕获 `MiniSiteError`、`Exception`、`json.JSONDecodeError`、`ValueError`，无 `ConnectionError` 捕获。

- **已调查范围（程序实录）**: candidate:minisite/config.py:41-57；candidate:minisite/exceptions.py:23-28；candidate:minisite/engine.py:1-8；candidate:minisite/cli.py:1-10；read:minisite/config.py:30-70；read:minisite/exceptions.py:1-40；search:ConnectionError -> 0 hits；search:requests\.|urllib|httpx|urlopen|aiohttp -> 0 hits；read:minisite/engine.py:1-40；read:minisite/cli.py:1-40；search:http|socket|network|fetch|download|remote -> 0 hits；search:except -> 11 hits；read:README.md:1-30；search:import (*.py) -> 23 hits
- **缺失线索/盲区**: - 已读取：`minisite/config.py:30-70`、`minisite/exceptions.py:1-37`、`minisite/engine.py:1-40`、`minisite/cli.py:1-40`、`README.md:1-10`。
- 已全仓检索（共扫描 10 个文件，无跳过）：`ConnectionError`（0 命中）、`requests\.|urllib|httpx|urlopen|aiohttp`（0 命中）、`http|socket|network|fetch|download|remote`（0 命中）、`except`（11 命中，均非 ConnectionError）、`import`（23 命中，均非网络库）。
- 缺失线索：无。工程规模小且全量扫描完成，不存在动态导入或外部配置引入 HTTP 客户端的迹象。
- **整改建议/说明**: - 无需整改。当前工程无外部 HTTP 请求客户端，条款 [R7] 不适用；若后续引入网络抓取/远程资源拉取功能，则需在捕获 `ConnectionError` 的分支中补充 `logging.warning(...)` 记录，以满足该规约。
- **账本证据索引**: E1(minisite/config.py:30-70 [snippet_body]), E2(minisite/exceptions.py:1-37 [snippet_body]), E3(minisite/engine.py:1-40 [snippet_body]), E4(minisite/cli.py:1-40 [snippet_body]), E5(README.md:6-6 [text_match]), E6(minisite/cli.py:10-10 [text_match]), E7(minisite/cli.py:49-49 [text_match]), E8(minisite/cli.py:71-71 [text_match]), E9(minisite/cli.py:74-74 [text_match]), E10(minisite/config.py:8-8 [text_match]), E11(minisite/config.py:81-81 [text_match]), E12(minisite/engine.py:7-7 [text_match]), E13(minisite/engine.py:99-99 [text_match]), E14(minisite/exceptions.py:7-7 [text_match]), E15(minisite/models.py:59-59 [text_match]), E16(README.md:1-10 [snippet_body]), E17(minisite/cli.py:3-3 [text_match]), E18(minisite/cli.py:4-4 [text_match]), E19(minisite/cli.py:5-5 [text_match]), E20(minisite/cli.py:6-6 [text_match]), E21(minisite/cli.py:8-8 [text_match]), E22(minisite/cli.py:9-9 [text_match]), E23(minisite/cli.py:10-10 [text_match]), E24(minisite/config.py:3-3 [text_match]), E25(minisite/config.py:4-4 [text_match]), E26(minisite/config.py:5-5 [text_match]), E27(minisite/config.py:6-6 [text_match]), E28(minisite/config.py:8-8 [text_match]), E29(minisite/engine.py:3-3 [text_match]), E30(minisite/engine.py:4-4 [text_match]), E31(minisite/engine.py:6-6 [text_match]), E32(minisite/engine.py:7-7 [text_match]), E33(minisite/engine.py:8-8 [text_match]), E34(minisite/exceptions.py:3-3 [text_match]), E35(minisite/models.py:3-3 [text_match]), E36(minisite/models.py:4-4 [text_match]), E37(minisite/models.py:5-5 [text_match]), E38(minisite/models.py:6-6 [text_match]), E39(minisite/models.py:7-7 [text_match])
- **核验提示**: ⚠️ 引用 [minisite/config.py:3-8] 超出账本实际返回行号区间，已标为未验证。
- **核验提示**: ⚠️ 引用 [minisite/models.py:3-7] 超出账本实际返回行号区间，已标为未验证。
- **核验提示**: ⚠️ 引用 [minisite/cli.py:71-74] 超出账本实际返回行号区间，已标为未验证。
- **核验提示**: ⚠️ 引用 [minisite/config.py:81-81] 仅命中 text_match（E11），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/engine.py:99-99] 仅命中 text_match（E13），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。
- **核验提示**: ⚠️ 引用 [minisite/models.py:59-59] 仅命中 text_match（E15），尚未通过 read_file_snippet 读取完整源码正文，不可直接充当实现逻辑依据。


## 核查配置与 S2 定位分离统计

- **规约文件**: `D:/Dev/code/agent/agent-project/eval/fixtures/sample_spec.md`
- **检索策略**: `hybrid` | **人工确认模式**: `auto(--yes)`
- **S2 自主定位成绩（纯自动，不含人工补充）**: 覆盖规则 5/7 条（空命中 2 条），自动候选共 15 处
- **S2 人工辅助与最终生效（单独计列）**: 人工介入 0 条规则（+0 新增 / -0 排除），最终生效覆盖 5/7 条，送审候选共 15 处
