# 007 MVP 补全计划（R2 后半程工单，2026-07-16）

> **背景**：PRD §8 定义 R2 领域纵深 = 9 个模块，MVP 完成判据 = 九模块在 A152 + 一个
> 试点团队跑通。`005-r2-plan` 当时只铺了前五单（Owner 实测三缺口 + beat + 订单），
> 模块 6b/7/8/9 标注"后续，顺序待 Owner 定"后**一直未立单**——审计（2026-07-16）发现
> 该断层，Owner 指令：审计工作区补齐规划，开发工作区专心执行。
> **本计划全部条目均以实际调研为据**（001 图纸段落 / 决策原文 / 旧仓考古锚点 / 现状
> 核实逐条标注），无凭空规格。
>
> **进度口径修正（重要）**：自本计划起，总进度一律以 **PRD §8 九模块 + RS 闸门** 为
> 分母汇报，并附九模块对账表。按此口径 2026-07-16 现状 ≈ 50%（模块 1/2/4 完成，
> 3/5/6 部分，7/8/9 未动）。

## 现状对账（审计核实，2026-07-16）

| PRD §8 模块 | 状态 | 缺口 → 工单 |
|---|---|---|
| 1 店铺/代理/配额/GTIN | ✅ | — |
| 2 合规导入+L4 | ✅（L4 豁免 D-Q58） | — |
| 3 上架完整态机 | 🟡 | **变体组零实现** → R2-11 |
| 4 定价策略 | ✅ R2-06 | — |
| 5 跟卖模式 | 🟡 | match spec/定价已有；货源占位链随 R2-11/09 复核 |
| 6 订单四检+采购门户 | 🟡 | 内部入口已有；**门户对外未建** → R2-10 |
| 7 售后+封店+邮箱 | 🟡（07a 核心已落地，PR #18 待合并） | → R2-07 |
| 8 财务对账/利润/KPI | ❌ 零实现 | → R2-08 |
| 9 三档自动化贯通 | ❌（表已建,仅通 order_block 一档） | → R2-09 |
| （D-Q53）前端设计 | 前端已有 13 页功能件 | 设计打磨 → FE-DESIGN |

## 工单定义

### R2-07 售后与店铺事件域【L1→L2】（returns + 封店工作流 + 邮箱）

三片一单（内聚：封店靠邮件识别驱动，D-Q33 原文）：

- **07a returns 只读闭环**：`channel_return` + `refund_request`（001 §07 已有列级图纸）；
  官方 Returns API 只读拉取+对账。**考古锚点（2026-07-17 修订，开发侧批注核实采纳）**：
  R-ERP-006"returns 同步缺失"仅实证 erp-core；erpAPI 根目录另有独立生产脚本
  `售后订单同步/fetch_walmart_returns.py`（cron 全店并发拉 `/v3/returns` 全量翻页
  → 飞书 27 列台账，台账 §13 售后规则出处）——**旧语义参照以该脚本为准**（开发侧
  实现语义与 L1 对拍口径同源）；代码结论不变：属**新建**（旧脚本是导表器非域模型），
  接口结构以官方 `walmart-marketplace-returns` OpenAPI 为准。
  **进展（2026-07-17）**：07a 核心已由开发侧落地（退货三表 + return_pull 拉取 +
  查询端点，PR #18 待合并）；收尾项 = `refund_request` 表落地 + A152 真机对账。
  退款三档**执行**归 R2-09（本单只建申请表，D-Q29）。
- **07b 封店工作流**：`store_incident`（001 §02 图纸）+ 品牌占用批量释放
  （`brand_assignment.incident_id` 回链已预留，§03）+ 定时提醒（挂 R2-04 beat）。
  **考古锚点**：旧 erp_core `store_incidents` 表（data-survey schema 在档）。
- **07c 邮箱域最小闭环**：§09 mail 域图纸（IMAP/SMTP 客户端模式，非自建服务器，
  D-Q22）——收件入库+店铺关联+Walmart 通知 LLM 分类（`llm_usage.module=mail_classify`
  枚举 R1 就预留了）→ 生成 store_incident + 告警。**范围闸**：完整客户端体验
  （会话线程/模板/附件/回信）= MVP 后第一批（D-Q22 备注原文），本片不做。
  **无旧实现可移植**（已核实旧仓无 mail 模块），提示词类比 L3 政策路由自建。

**验收**：①A152 真实 returns 拉取与渠道后台对账一致；②手工造 incident 演练
封店→品牌占用批量 released→beat 提醒送达；③真实邮箱收一封 Walmart 通知类邮件
→ 自动分类 → incident+告警落库。

### R2-08 财务域【L1】（结算对账 + 利润账 + KPI）

- **硬前置（规划侧先行）**：§08 图纸按 immutable event ledger 修订（外部评审
  D-Q56 裁定"财务域建域前改图纸"）——由审计工作区落笔，开发等图纸。
- 结算拉取：官方 Reports/结算 API；**考古锚点**：旧 `fetch_walmart_settlement.py`
  （erpAPI 根目录，在档）+ `walmart_settlement.db`（91MB，T7 备份）可做对拍参照。
- 对账 recon（settlement_line ↔ channel_order/order_line）→ profit_ledger 三粒度
  物化（D-Q32）→ 日报 KPI（考古锚点：旧 erp_core `store_kpi_snapshots`/`payout_accounts`）。

**验收**：A152 真实结算单拉取入库；结算明细与订单对账差异率可解释；利润账三粒度
与结算金额闭合（抽 10 单人工核对）。

### R2-09 三档自动化贯通【L1】

**现状核实**：`automation_policy` 表已建（0025），但只消费 `flow=order_block` 一档。
本单=补全 flow 枚举与消费点，非建表：

- flow 全集接线：`audit_to_listing`（D-Q13，审核 pass 后是否自动进分配）、
  `listing_pricing`（改价守护联动 R2-06）、`refund`（D-Q29 三档原文）、
  `scrape_to_audit`（采集完自动送审）等——以 §09 automation 图纸 flow 清单为准；
- 三档语义统一（人工/半自动/全自动，团队级面板）+ 前端策略面板页；
- 档位变更即时生效（吃 R2-04 Redis pubsub 配置广播）。

**验收**：同一商品在三档下各跑一遍采集→审核→上架→定价全链：全自动零人工介入、
半自动在设定环节停、人工档每环节停；切档 60s 内生效。

### R2-10 采购方门户对外【L2】

- `portal_account`（§01 图纸；R1-03 明确记为偏离项"portal_account→R2#6"）+
  D-Q50 双入口的外侧（内侧采购执行单 R2-05 已建）；
- **硬前置=RS-01 + RS-02**（评审定的机器可判定闸门原文："API绑定非loopback/
  门户路由启用"任一发生前必须完成）——本单动工前先过这两单。

**验收**：门户账号仅见own采购单（跨账号越权测试必败）；RS-01 四角色读写矩阵
+ RS-02 HTTPS/密钥硬失败先行通过。

### R2-11 变体组【L1→L2】（上架态机欠账，D-Q2 MVP 项）

**现状核实**：§03 `variant_group`/`variant_member` 列级图纸完备，代码零实现
（含 `product.variant_group_id` 列亦未建）。

- 建表迁移 + 采集端 parent ASIN 归组（source_parent_ref 图纸已留）；
- spec 构建器变体段（R2-03 构建器扩展；variation_theme → Walmart variant 属性映射）；
- 组完整性守卫（status=broken 拒绝构建，图纸原文）。

**验收**：一组真实变体（≥3 成员）A152 上架为 Walmart variant group 并 live；
组员缺失时构建拒绝且可见原因。

### FE-DESIGN 前端设计打磨（D-Q53，Owner 触发制）

**现状核实**：前端已有 13 个功能页（AntD 功能件，按域随单配套）；视觉基准
`erpAPI/erp-core/handoff-design/`（chats+project 完整在档，已核实存在）。
D-Q53 原文=功能闭环稳定后统一执行。**不排默认顺序，Owner 说"启动设计工单"才动**；
执行时按 handoff-design 移植视觉、数据接现有 API，禁止重写业务逻辑。

## 动工顺序（✅ 已定案，Owner 2026-07-16："动工顺序符合开发推进逻辑即可"）

**R2-11（小，上架欠账）→ R2-07（运营刚需）→ R2-09（贯通）→ R2-08 → R2-10
（RS-01/02 前置）**；FE-DESIGN 由 Owner 择机触发。R2-04 收尾与 RS 系列
（04A 14M 实测/05/06…）由开发侧按既有节奏穿插，不受本序影响。
**R2-08 前置已解除**（2026-07-16）：§08 财务图纸按 immutable event ledger 修订完成
（financial_event/ledger_entry 追加式两层 + 显式汇率块 + 过账幂等/冲销协议 +
验收补强四条），见 `specs/001-domain-model/08-finance.md`。

## 角色分工（Owner 2026-07-16 指令）

规划与审计=审计工作区（本文档作者）；开发=ERP-ALL 工作区。开发侧对本计划的
异议走批注回传（同外部评审通道），**不直接改本文档**；工单验收判据如需调整，
经 Owner 或审计侧确认后修订。
