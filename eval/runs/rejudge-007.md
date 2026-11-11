# 历史 Run 离线重判对照报告 (Rejudge Diff)

> 数据源：`eval/runs/run-007.json` ｜ 判卷口径：`src/code_agent/core/judge.py` ｜ 未调用大模型、未消耗 Token

## 一、总体差异

| 口径 | 通关数 | 通关率 |
| :--- | :---: | :---: |
| 旧判卷 | 30/30 | 100.0% |
| 新判卷 | 27/30 | 90.0% |

- 由 ❌ 翻为 ✅：无
- 由 ✅ 翻为 ❌：`014`, `017`, `019`

> 翻为 ❌ 通常不是"系统变差"，而是旧判卷把「模型拒答」或「只答了一半」误判成了通过。

## 二、逐题对照

| ID | 题型 | 旧判定 | 新判定 | 拒答分级 | 新口径失败原因 |
| :--- | :--- | :---: | :---: | :---: | :--- |
| `001` | location | ✅ | ✅ | none | — |
| `002` | location | ✅ | ✅ | none | — |
| `003` | location | ✅ | ✅ | none | — |
| `004` | location | ✅ | ✅ | scoped | — |
| `005` | behavior | ✅ | ✅ | none | — |
| `006` | behavior | ✅ | ✅ | scoped | — |
| `007` | behavior | ✅ | ✅ | none | — |
| `008` | behavior | ✅ | ✅ | none | — |
| `009` | boundary | ✅ | ✅ | global | — |
| `010` | boundary | ✅ | ✅ | global | — |
| `011` | location | ✅ | ✅ | scoped | — |
| `012` | location | ✅ | ✅ | none | — |
| `013` | location | ✅ | ✅ | none | — |
| `014` | behavior | ✅ | ❌ ⟵ 已翻转 | global | 模型声明无依据拒答（检索未召回关键上下文或信息不足） |
| `015` | behavior | ✅ | ✅ | none | — |
| `016` | behavior | ✅ | ✅ | none | — |
| `017` | behavior | ✅ | ❌ ⟵ 已翻转 | global | 模型声明无依据拒答（检索未召回关键上下文或信息不足） |
| `018` | behavior | ✅ | ✅ | none | — |
| `019` | behavior | ✅ | ❌ ⟵ 已翻转 | global | 模型声明无依据拒答（检索未召回关键上下文或信息不足） |
| `020` | behavior | ✅ | ✅ | none | — |
| `021` | behavior | ✅ | ✅ | none | — |
| `022` | behavior | ✅ | ✅ | scoped | — |
| `023` | behavior | ✅ | ✅ | scoped | — |
| `024` | boundary | ✅ | ✅ | scoped | — |
| `025` | boundary | ✅ | ✅ | global | — |
| `026` | location | ✅ | ✅ | none | — |
| `027` | location | ✅ | ✅ | none | — |
| `028` | behavior | ✅ | ✅ | none | — |
| `029` | behavior | ✅ | ✅ | none | — |
| `030` | boundary | ✅ | ✅ | global | — |
