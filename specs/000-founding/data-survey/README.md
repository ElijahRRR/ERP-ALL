# 本地数据调研清单（Owner 本机执行）

> 目的：为 canonical schema 设计与合规数据标准导入接口（D-Q35）提供事实依据。
> **我不需要完整数据**，每类资产只要三样：①结构（DDL/表头）②规模（行数/大小）③样例（10-20 行）。
> 产物统一放本目录 `out/` 下，commit + push 即可。含客户个人信息的数据（订单/售后）**不在调研范围**——D-Q35 已定重拉策略，不需要样例。

## 一、PostgreSQL 三库（跑脚本即可，见 survey_local_data.sh §1）

| 库 | 要什么 | 为什么 |
|---|---|---|
| `uspto` | 全部表 DDL + 每表行数 + `blacklist_brands`/`brand_nice_class`/`tro_cases`/`matched_companies`/`status_code_mapping` 各 20 行样例 | 合规域 schema 直接继承候选；确认 tro_cases/matched_companies 的实际部署结构（仓库 schema.sql 标注"数据生产脚本不在本仓"） |
| `walmart_cleanup` | DDL + 行数 + `error_items` 按 category 分布 + 10 行样例 | 问题商品域结构 + 13 类归类的真实分布（决定导入映射） |
| `erp_core` | DDL + 每表行数（重点 products_master / listings / upc_pool / audit_runs / walmart_orders） | 确认草稿库实际数据规模与 alembic 漂移；判断哪些表有真数据值得参考 |

## 二、SQLite 文件（脚本 §2，注意核对路径）

| 文件 | 要什么 | 为什么 |
|---|---|---|
| `沃尔玛UPC生成器/upc_history.db` | schema + 总数 + walmart_status 分布 | UPC 池统一设计（与飞书池、erp_core upc_pool 三源合一） |
| `walmart_settlement.db` | schema + 每表行数 + 5 行样例 | 财务域（回款对账）字段基础 |
| `auto_listing/state/retry_state.sqlite` | schema + kind 分布 | 失败累计状态机的真实错误类型分布 |
| TRO `merged.db`（tro-scraper-matrix 本地） | schema + 总数 + 按 source 分布 + 10 行样例（清洗后） | TRO 域结构 + 五站点数据质量 |

## 三、飞书表（每张：表头行 + 前 10 行 + 总行数，导出 CSV 放 out/lark/）

> 用 lark-cli 或你的 lark_io 顺手导出即可；文件名按下表"输出名"。

| 表（token / sheet_id） | 输出名 | 为什么 |
|---|---|---|
| 禁止品牌收集 `YlA1sz…/WvPTz2` | lark/brand_blocklist.csv | 与 uspto.blacklist_brands 的字段差异 → 单源合并方案 |
| 黑名单地址 `YnUH…/ZLUqxi` + 黑名单邮编 `NOn5x7` | lark/phishing_addr.csv / phishing_zip.csv | 钓鱼检测数据结构 |
| 监管合规删除 `YlA1sz…/eGjQRX` | lark/compliance_delete.csv | 合规删除工单结构 |
| 沃尔玛类目 `Gx9Hs…/0bdc8b` + 沃尔玛禁止 `OJSrkV` | lark/walmart_category.csv / prohibited.csv | 禁售 PT 判据（D/E 列语义）入库 |
| 类目映射明细 `Gx9Hs…/2p5sL6` | lark/mapping_detail.csv | Amazon→WPT 映射表结构（配合类目映射代码考古） |
| 采购方表 `YnUH…/OGBTUB` | lark/purchasers.csv | 采购方门户（D-Q27）数据基础 |
| 定价和上下架 `X4vMwQ…/2FJ2Np` | lark/pricing_quota.csv | 定价策略表 + 配额 + stockzero 配置的完整列语义（含表头前 3 行——它是双行表头） |
| UPC 池 `PDsRsfG…/NxlS1J` | lark/upc_pool_stats.txt（只要行数 + B 列状态分布，不用导全量） | 池规模与状态口径 |

## 四、位置未知的资产（不用导数据，先回答"在哪、长什么样"）

1. **黑名单卖家**（你在 Q35 提到）——现在维护在哪？表头是什么？
2. **店铺收款账户资料**（D-Q33 档案字段）——现在存在哪（xlsx？其他表）？字段有哪些（可打码）？
3. **1688 货源对应关系**（D-Q25）——现在人工找货后有没有记录"产品↔1688 链接/价格"的表（哪怕 Excel）？有的话给表头+几行样例。
4. **代理 IP 台账**（D-Q34）——除店铺API.xlsx 里的绑定外，有没有采购/到期信息的记录？

## 五、明确不需要的（避免白干）

- 订单表/售后表内容（重拉策略 + 含个人信息）
- 在线产品总表 148k 全量（只要表头一行，脚本外手工贴一下即可）
- llm_cache.sqlite、feed JSON 备份、日志（运行时产物，不入新库）
- amazon-scraper-v3 / walmart-scraper 的运行库（schema 已在代码仓可推导）

## 提交方式

`out/` 目录连同四类"位置未知"问题的答案（写进本 README 末尾或新建 ANSWERS.md），commit 到任意分支推上来说一声即可。我拿到后：完成 canonical schema 列级设计 + 标准导入接口规范 + 更新总账相应条目。
