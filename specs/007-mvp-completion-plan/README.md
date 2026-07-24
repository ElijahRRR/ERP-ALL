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
| 2 合规导入+L4 | 🟡（存量导入 ✅；**持续供给未立单**，Owner 2026-07-17 指出） | → R2-12 |
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

**现状核实（2026-07-24 修订，开发侧批注核实采纳）**：§03 图纸完备；0007 迁移
实已建 `variant_group`/`variant_member`/`product.variant_group_id` 全套 DDL——
原文"代码零实现（列亦未建）"中 DDL 半句不成立，缺的是**服务与端点层**（归组
服务/契约端点/构建器变体段/守卫）。范围按此收窄执行，实现口径经 D-Q63/D-Q64
两轮 Owner 拍板细化（实时归组/批次原子性/variant_mode 双模式/live 补挂）。

- 建表迁移 + 采集端 parent ASIN 归组（source_parent_ref 图纸已留）；
- spec 构建器变体段（R2-03 构建器扩展；variation_theme → Walmart variant 属性映射）；
- 组完整性守卫（status=broken 拒绝构建，图纸原文）；
- **随单补欠（审计发现 2026-07-18）**：catalog 路由（产品编辑/状态操作）未接
  AuditWriter 操作日志——identity/listing/order/aftersale/channel/scrape/pricing
  七域已接，唯 catalog 漏。本单动 catalog 域时一并接上（验收加一条：改一个产品
  字段 → audit_log 可见操作人与前后值）。

**验收**：一组真实变体（≥3 成员）A152 上架为 Walmart variant group 并 live；
组员缺失时构建拒绝且可见原因。

### R2-12 合规数据供给持续化【L1】（黑名单多源 + USPTO 自增量 + 合规页面）

**缺口来源（Owner 2026-07-17 指出，审计核实为真）**：存量迁移只解决"库里有数"；
旧系统的持续供给链全在旧仓单机 cron 上，新系统无工单承接，13 页中亦无合规页。
001 §04 图纸有落点（source 枚举 tro_sync/trademark_sync、tro_case"按日进"、
refdata.trademark"同步管道产物"）但无人立单；RS-04A/B/C/D 只覆盖**通道与账本
机制**，不覆盖**源头采集与常驻调度**。

四条供给链考古（源码逐条核实，2026-07-17）：

1. **USPTO 日度自增量**：`walmart-trademark-sync/daily_update.py`——查
   `trademarks.MAX(filing_date)` → 从 `data.uspto.gov`（TRTDXFAP/apcYYMMDD.zip）
   下载缺失日包 → `etl_trademarks.py` iterparse 流式入 5 表（ON CONFLICT 覆盖）
   → `etl_progress` 断点续传。**新系统接法**：部署机 daily_update（uspto 库）→
   RS-04A 搬运通道增量 upsert 进 `refdata.trademark`（is_live/nice_classes 派生
   同基线导入）→ refdata_revision 递增失效审核缓存；挂 beat 调度 + 失败告警。
2. **TRO 采集**：`tro-scraper-matrix`——5 站点（123tro/61tro/ipsebe/saibeiip/
   worldtro，httpx+Playwright）→ 各站 SQLite → merge → cleaning_engine（品牌归一/
   原告/法院提取）。**新系统接法**：产物经 import_job(domain=tro) 按日进
   `tro_case` + 派生 blacklist_brand 断言（source=tro_sync，RS-04D 账本消费）。
3. **后台报错回收**：旧 `沃尔玛问题商品清理`——daily_cleanup（日 4 次拉
   UNPUBLISHED+SYSTEM_PROBLEM 分类处置）+ `brand_collector.py`（C 品牌限制/
   E 知产 → 品牌黑名单，来源列标注）+ `blacklist_sync.py`（永久禁售类
   B/C/E/F/G/K 的 ASIN → 选品 ASIN 黑名单）。**新系统接法（两段，2026-07-18
   补强：Owner 问询核实"全店后台 SKU 拉取对账"此前无任何工单承接）**：
   **上游=全店后台 SKU 拉取对账**——GET /v3/items 全量翻页（无 query 300/min）
   拉渠道侧全部 SKU → 与本地 listing 集合对账：后台有本地无 / 状态漂移 /
   UNPUBLISHED+SYSTEM_PROBLEM 错误 SKU → 落 listing 错误处置与维护任务，挂
   beat（旧 daily_cleanup 日 4 次节奏参照）；
   **下游=报错回收**——错误处置命中永久禁售类 → 自动生成黑名单**候选**
   （brand/ASIN）→ 人工确认落库；§04 source 枚举随本单扩 `error_recycle`
   （图纸小修由审计侧落笔）。
4. **邮件与人工渠道**：核实无自动化旧代码（旧链路=人工阅侵权/投诉邮件→填飞书）。
   近期=合规页人工录入（source=manual）+ 标准导入接口；R2-07c 落地后，Walmart
   通知/侵权邮件 LLM 分类可生成黑名单候选进人工确认队列（**不自动入名单**）。

**页面（随本单配套）**：合规中心页——四黑名单管理（增删/来源与断言追溯/RS-04D
裁决视图）+ 商标查询（mark_norm trgm + Nice class）+ TRO 案件查询 + import_job
上传/进度/错误报告下载。

**依赖**：RS-04A 通道（已建成）；RS-04D 断言账本（blacklist 写路径，**先行或与
本单同窗执行**）；beat 调度（R2-04 已有）；07c 邮件钩子（后置，不阻塞本单）。

**验收**：① USPTO：连续 3 个日度增量自动入 refdata.trademark，行数与 etl_progress
对账一致，审核检索可见新商标；② TRO：一次真实采集→tro_case 入库→派生品牌断言
→L2 命中可复现；③ 报错回收：一个真实永久禁售错误商品自动生成黑名单候选、人工
确认落库全程可追溯；④ 页面：四黑名单可管理、商标可查询、import_job 全流程走通。

### FE-DESIGN 前端设计打磨（D-Q53，Owner 触发制）

**现状核实**：前端已有 13 个功能页（AntD 功能件，按域随单配套）；视觉基准
`erpAPI/erp-core/handoff-design/`（chats+project 完整在档，已核实存在）。
D-Q53 原文=功能闭环稳定后统一执行。**不排默认顺序，Owner 说"启动设计工单"才动**。
**执行方式（Owner 指令，2026-07-17 补记）：调用 Claude Design 设计**——
handoff-design 存档作为风格基线输入，新页面视觉由 Claude Design 产出，数据接
现有 API，禁止重写业务逻辑。

## 动工顺序（✅ 已定案，Owner 2026-07-16："动工顺序符合开发推进逻辑即可"）

**R2-11（小，上架欠账）→ R2-07（运营刚需）→ R2-12（数据供给，与 RS-04D 同窗）
→ R2-09（贯通）→ R2-08 → R2-10（RS-01/02 前置）**；FE-DESIGN 由 Owner 择机触发。
（2026-07-17 插入 R2-12：黑名单/商标数据新鲜度直接决定审核质量，随时间衰减，
属运营刚需；其不依赖 07c/09/08，仅依赖已建成的 RS-04A 通道 + RS-04D 账本。）R2-04 收尾与 RS 系列
（04A 14M 实测/05/06…）由开发侧按既有节奏穿插，不受本序影响。
**R2-08 前置已解除**（2026-07-16）：§08 财务图纸按 immutable event ledger 修订完成
（financial_event/ledger_entry 追加式两层 + 显式汇率块 + 过账幂等/冲销协议 +
验收补强四条），见 `specs/001-domain-model/08-finance.md`。

## 角色分工（Owner 2026-07-16 指令）

规划与审计=审计工作区（本文档作者）；开发=ERP-ALL 工作区。开发侧对本计划的
异议走批注回传（同外部评审通道），**不直接改本文档**；工单验收判据如需调整，
经 Owner 或审计侧确认后修订。
