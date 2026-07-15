# 验收① 真数据 dry-run · 第 1 轮（部署机回报，2026-07-15）

> 数据源：T7 MPSetup monolith `5.0.20260330-14_47_14-api`（SHA-256 校验过）→
> extract（bff03e6 修复版，6,951 PT + __orderable__）→ import job 14（6,952/0/0）。
> 错误码 job 15（65/65，字典 70 唯一码）。完整报告在部署机
> `D:\erp-staging-backup\out\r2-03-dryrun-report.json`（+console log）。

## 结果（--auto 12 --fill）

| 指标 | 值 |
|---|---|
| products | 12 |
| validation_ok | 9 |
| validation_errors_total | 3 |
| distinct_wpt_ok | Drinkware / Furniture Grippers, Pads & Sliders / Grill Grid Lifters / Hardware Screws / Other Fishing Accessories（5 个）|
| llm_unavailable / Traceback | 0 / 无 |

## 3 条失败判读（全部同因：源数据贫瘠，非工具/数据缺陷）

| product | ASIN | WPT | errors |
|---|---|---|---|
| 16 | B08NFJH5VL | Art Sets | visible.keyFeatures: 1 项 < minItems 3 |
| 17 | B0GVQPSSVK | Planter Boxes | visible.keyFeatures: 1 项 < minItems 3 |
| 23 | B0FSZWJNSM | Mirrors | visible.keyFeatures: 1 项 < minItems 3 |

- 三品源数据仅 1 条卖点且无可拆句描述，文案链（源仓同款补齐逻辑）补不满该 PT 的
  keyFeatures minItems=3。
- **旧系统对照**：旧 validate_payload 只查必填非空（1 条也算非空）→ 会照发并吃渠道拒
  EXT_DATA_ERROR_55506974520167（已在错误码字典）。新系统本地拦下=校验器本职，
  省 MP_ITEM 10/hour 配额。处置：等采集补文案或人工补齐后重投；不改产品业务数据。

## 判定

- **按 005 验收①原文**（「dry-run 产物通过官方 spec 校验（≥5 个不同 WPT 的产品）」）：
  9 个产品通过官方 spec 校验、覆盖 5 个不同 WPT —— **达标**。
- harness 第 1 版 pass 判定（要求全部产品过）严于验收原文，已修正为原文口径
  （失败品在 summary.failed 完整列报，不隐藏）；修正含回归测试。
- 待部署机用修正版重跑出 PASS 报告作为归档件；最终验收签字归 Owner。
