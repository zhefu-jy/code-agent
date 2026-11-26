# MiniSite 工程实现与安全规范（测试样本）

## 1. 背景说明

MiniSite 是一个轻量级静态站点生成工具，用于读取本地纯文本内容并渲染生成静态 HTML 页面。本文档用于规范配置校验、异常观测、系统调用安全及部署约束，便于工程落地自查。

## 2. 配置数值与上限规范

- **R-CFG**：`SiteConfig.max_posts` 的默认值不得超过 `100`；配置校验函数 `validate_config` 必须拒绝小于等于 `0` 的 `max_posts` 配置，并抛出 `ConfigError`。

## 3. 异常分支处理规范

- **R-ERR**：在 `BuildEngine.build` 渲染与写盘流程中捕获异常后，除抛出 `RenderError` 外，必须使用标准库 `logging.warning` 记录告警日志（若其直接上游入口 `run_cli` 在捕获 `MiniSiteError`/`RenderError` 时已调用 `logging.warning` 记录告警日志，亦可视为满足要求）。

## 4. 接口禁用与替代规范

- **R-SEC**：工程源码中严禁直接使用 `os.system` 执行系统命令；如需调用外部命令，必须使用标准库 `subprocess` 模块替代。

## 5. 部署配置与外部网络边界规范

- **R-DEPLOY**：若仓库提供生产环境配置文件 `minisite.json`，其中的 `max_posts` 配置值上限不得超过 `50`（仅可基于仓库内实际存在的配置文件核验，禁止在缺少文件时臆断合规或违规）。
- **R-HTTP**：若工程包含外部 HTTP 请求客户端（如 `requests` / `httpx`），单次请求超时上限必须小于等于 `10` 秒，且捕获 `ConnectionError` 时必须记录 `warning` 级别日志。