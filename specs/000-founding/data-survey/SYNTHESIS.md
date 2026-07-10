# 数据调研综合结论（Synthesis）— 2026-07-09

> 输入：out/ 全部产物 + answers/ 四问答复。本文是 canonical schema 与导入接口设计的直接依据。

## 1. 重大发现

### 1.1 又发现两个在产系统 + 两个 PG 库（需 Owner 确认定位）

- **`新审核系统`**（~/Projects/新审核系统，PG 库 `walmart_audit`）：黑名单三表（sellers 1,308 / asins 18,458 / amazon_cats 11,810）每日 07:05 从飞书同步，Phase0 内存字典拦截——**这才是审核的在产实例**；erp-core 的 audit/ 是它的适配副本。
- **`沃尔玛操作台`**（~/Projects/沃尔玛操作台，PG 库 `walmart_os`）：又一个下游，自带 blacklist_sellers 副本（1,271 行，同步滞后 37 行）。
- ⚠️ 待 Owner 确认：①「考古移植审核 L0-L4」应以**新审核系统**为源还是 erp-core 副本？②沃尔玛操作台是什么、是否也要被新 ERP 吸收？（建议这两个项目也推 GitHub 供考古）

### 1.2 uspto 库远比 schema.sql 丰富（实为多域数据仓）

精确行数揭示 schema.sql 未覆盖的生产资产：
- **商标域**：trademarks 14.18M / classes 17.25M / owners 32.4M / statements 29.4M / design_codes 8.8M
- **专利域**：patent_grants 111k + citations 4.76M + 分类/发明人/图像（trademark-data 仓的产物在这里落库）
- **封店情报**：`walmart_suspension_history` **75,703 行**（+embeddings 表）——封店案例库，D-Q33 封店工作流和申诉信的现成弹药，此前完全不在任何文档里
- **类目映射域**：amazon_walmart_category_map 6,672 / walmart_product_types 2,959 / path_a_results 292k / ai_match_results——类目映射 pipeline 的落库产物（与飞书 mapping_detail 15,771 行并存）
- 合规域：blacklist_brands 35,914 / brand_nice_class 117,462 / matched_companies 11,893 / tro_cases 11,893 / company_brand_details 159,730

→ 新 ERP 合规+类目域的数据基础比预想厚实得多；导入接口按"域"而非按"表"设计。

### 1.3 黑名单卖家（Q1）：第 10 个飞书 workbook

- 权威源：飞书 `QNIpwrRWxiYPeVk86f8cemyenBb`/8280e8，三列各自独立去重（卖家ID/ASIN/Amazon类目）
- 双 PG 镜像链：飞书 → walmart_audit（TRUNCATE 重灌，每日 07:05）→ walmart_os（滞后）
- 新 ERP：入 compliance 域三张表，导入器以飞书为源；sheets_registry 需补第 10 个 workbook

### 1.4 UPC 现实画像（设计警报）

- 飞书池 148,328 行：已用 115,752 / 空闲 27,996 / 已领 4,580 → 池余量约 19%，200 店规模下**几个月内耗尽**
- 生成器历史 104,688 个：已校验里 **conflict 6,696 vs free 3,354 = 67% 冲突率**——随机生成撞库严重
- → 新系统 UPC 域必须：①提高生成通过率（更长前缀段/号段规划）或改采购 GS1；②校验流水线容量按 200/min×多店并发设计；③池水位告警

### 1.5 货源记录（Q3）与代理台账（Q4）现状

- 1688 货源：仅散落 Excel（`沃尔玛WFS选品.xlsx`：ASIN/名称/售价/拿货成本/1688链接/含运费成本，含风险备注自由文本）——sourcing 域从零建模，此表即字段来源
- 代理台账：**不存在**采购/到期/续费数据（供应商疑似易路 YiLu，信息只在供应商后台）；店铺API 飞书表还有 xlsx 快照没有的两列（运营模式/店铺状态）+ Sheet2 设备维度代理清单
- 收款账户（Q2）：无集中台账；PingPong 虚拟卡导出 + settlement 的 payment_processor 字段——store 档案的收款字段从零建

## 2. 导入接口设计输入（工程要点）

1. **lark 读取陷阱**（导入器必须内建）：+csv-get 约 50 万字符**静默截断**→ 分块 ≤5,000 行 + 逐块行数校验；黑名单地址表表头在第 5 行；定价表双行表头；店铺API 表 K-T 列是 A-J 的镜像块（跳过）
2. **PG 统计陷阱**：n_live_tup 严重滞后（erp_core 实 38k 显示 0）——任何"以行数为验收"的对账必须 count(*)
3. 待导入清单（按域）：合规（黑名单品牌 2,380 / 卖家三表 / TRO 11,893 / 监管合规删除 6,093）、类目（walmart_category 7,008 / mapping_detail 15,770 / prohibited）、钓鱼（地址 ~195 / 邮编 ~199）、采购方（~200）、定价配额（11 店行×双表头）、UPC 池（148k）、封店历史（75,703）
4. settlement_snapshots + recon_details（SQLite）→ 财务域表结构直接参考

## 3. 缺口状态更新

- G2（飞书活数据快照）✅ 关闭；Q1 黑名单卖家 ✅；Q2/Q4 = "无台账，从零建"（已是决策输入）；Q3 ✅ 字段拿到
- G1（类目映射入库）✅ 关闭——main `5ac847c` 已入 139 个文件（内嵌 git 仓以 .git-archive 保留，处理正确）→ REF-R0-002 考古解锁
- 新缺口 **G8**：`新审核系统` 与 `沃尔玛操作台` 两项目未考古（见 §1.1，待 Owner 定位）
