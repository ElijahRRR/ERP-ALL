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

## 仓库—工单归属核对（2026-07-28，防 R2-13 类缺口复发）

> **成因教训**：R2-13（自动采购插件）之所以整整漏掉，是因为需求源只有 erpAPI 仓考古，
> 而该能力住在独立仓 + 第三方 SaaS，**考古照不到**。故建立此表：Owner 名下每个仓
> 逐个对照有无工单认领，新增仓时补录。

| 仓 | 认领工单 |
|---|---|
| amazon-scraper-v3 | R2-01 采集引擎移植 |
| walmart-audit-system | R2-02 审核弹药 |
| trademark-data / walmart-trademark-sync | R2-02 基线 + R2-12 USPTO 日增链 |
| tro-scraper-matrix | R2-12 TRO 链 |
| amazon-walmart-category-mapping | R2-02 类目映射 |
| walmart-scraper | 跟卖/竞品数据（模块 5，随 R2-06 与货源占位链） |
| AMZ-Purchase-Assistant | **R2-13**（2026-07-27 立单） |
| erpAPI | 遗留系统，考古源 |
| **PIM-HTML / Get-product-win / Product-Get-mac-** | **无——Owner 2026-07-28 确认已废弃**，不立单、后续核对不再提 |

（与 ERP 无关的仓：ai-skills / openclaw-skills / crypto-signal-engine / flow2api /
gemini-multi-account / gpt-batch-register / amazon-scraper-v2〔已被 v3 取代〕。）

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
- **07c 邮箱底座**（✅ 阻塞已解除，Owner 2026-07-27 确认有真实 163 邮箱与授权码；
  **2026-07-29 重新定范围：先建底座、AI 后置**）：§09 mail 域图纸（IMAP/SMTP
  客户端模式，非自建服务器，D-Q22）

  > **选型调研结论（审计侧 2026-07-29，GitHub + 官方文档实证）**：**没有可直接采用的
  > 成品**——① `EmailEngine`/`RustMailer` 生产需商业许可（前者 $1,450/年，**内部自用
  > 亦收费**），且 163 支持未经验证；② `Bichon` 虽 AGPL 免费，但现为 `2.0.0-alpha.1`
  > 且刚换底层存储引擎，与「稳定优先」相悖，其 163 支持在发布说明与 FAQ 中**查无实据**
  > （搜索摘要的「已修复」结论无法从一手来源证实，不予采信）；③ `isync/mbsync`、
  > `offlineimap` 极成熟但**至今缺 IMAP ID 扩展**（isync bug #73 未决），**连不上 163**；
  > ④ AI 邮件助手类项目全为 0★ 玩具，无可移植者。
  > **定案：自建**，连接层用 `imapclient`（562★，BSD，内置 `id_()`），架在现有渠道网关
  > 骨架（多账号/定时/重试/凭证加密/审计）之上——**不是新建基础设施，是换协议**。
  > 可借鉴的一手参考：`EthanYoQ/Invoice-Downloader`（136★，**Apache-2.0 可商用**，
  > 生产级 163 IMAP 消费者）；分层思路另参 datawhale「163 邮箱助手实战」（脚本层出
  > 结构化数据／凭证层／提示词层／定时层四层分离，与我方 L0→L3 同构）。

  **底座范围（只做这四件）**：①**账号池**（`mailbox` 表：授权码加密、绑店铺、**代理默认
  取店铺代理**、启用状态、同步水位）；②**拉取调度**（挂 R2-04 beat，按账号增量拉取，
  UID 水位 + 去重，失败退避与告警）；③**收发**（正文/附件落库，发信用**该店铺自己的
  邮箱**经 SMTP 发出并留痕）；④**可观测**（每账号最近成功时间、失败次数、当前状态
  一屏可见——几十个邮箱没有这个面板等于盲飞）。
  **明确不做**：实时 IDLE 长连接、文件夹全量同步、离线缓存、多协议抽象层、通用邮件
  客户端 UI——这些是无底洞且我们不需要。**AI 分类完全剥离**为后续独立单。——收件入库+店铺关联+Walmart 通知 LLM 分类（`llm_usage.module=mail_classify`
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

- flow 全集接线：以 **§09 flow 注册清单 v2（2026-07-26 冻结）** 为唯一权威，九条：
  `scrape_to_audit` · `audit_to_listing` · `listing_dispatch` · `pricing_watch` ·
  `order_block` · `compliance_block` · `refund` · `cancel` · `maintenance_run`。
  （原文误写的 `listing_pricing` 系本计划笔误，代码与图纸真名均为 `pricing_watch`，
  2026-07-26 归一；v1 的 `gtin_alert`/`suspension_reminder` 已移出注册表——阈值与
  节奏另有落点，告警能力不变，详见 §09。）
- 三档语义统一（人工/半自动/全自动，团队级面板）+ 前端策略面板页；
  **`order_block`/`compliance_block` 只有 manual/auto 两档**（裁定 3）；
- **档位在每次决策时直读 `automation_policy`，不进缓存**（裁定 4：原文"吃 R2-04
  Redis pubsub 配置广播"的实现指定作废——该缓存无业务读者且方向为 fail-open，
  与档位闸必须 fail-closed 相悖；直读延迟≈0）。即时生效的**目标**保留。

**验收（Owner 2026-07-27 裁定 Q1 改判据，替代原"同一商品三档各跑一遍"）**：
**同一 SKU 家族取 A/B/C 三件，分别在 manual / semi / auto 下各跑一档**采集→审核→
上架→定价全链：全自动零人工介入、半自动在设定环节停、人工档每环节停。
> 改判据理由：商品状态**单向前进**（`ingested → audit_passed → listing draft → live`），
> 跑完 auto 档回不到 manual 档，全仓无状态回退工具——**原判据在当前状态机下物理上
> 无法执行**，会在验收当天卡住。判据的真实目的是"三档在同一条流水线上都走得通"，
> 三件等价输入同样证明。**明确不做**为验收引入状态回退能力（含 `is_test` 专用重置
> 脚本）：收益一次性、风险长期。**四环对应 flow**（裁定 2 补齐，判据不下调）：
①`scrape_to_audit` ②`audit_to_listing` ③`listing_dispatch` ④`pricing_watch`。
**切档生效口径（裁定 4 修订，替代原"60s 内生效"）**：实时求值类 flow 于**下一次
决策**即生效（含 auto 档 beat 逐条目读档，最坏陈旧=一个条目，非一批）；创建快照类
（`refund`/`cancel`）对在途请求**不生效属正确行为**，验收查新建请求即可。

### R2-10 采购方门户对外【L2】—— ⛔ **已移出 MVP（D-Q66，2026-07-27）**

> **MVP 阶段不做**：采购方直接用内部账号、由运营兼职操作，权限默认管理员档、超管持全部
> 权限。本单整体推至 MVP 后。**模块 6 的 MVP 验收口径随之收窄** = 订单四检 + 内部采购
> 执行闭环（R2-05 已验收），不含对外门户。
> ⚠️ **不得误读为"安全加固可推迟"**：本决策只移除 RS-01/02 四触发条件中的"门户路由
> 启用"，而"API 绑定非 loopback"**早已触发**（内网浏览器访问即是）——见 D-Q68 / RS-02a。

以下内容保留待 MVP 后启用：

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
确认落库全程可追溯；④ 页面：四黑名单可管理（含被压制主体的独立追溯入口，
008§6）、商标可查询、TRO 可查询、import_job **进度与报错报告可见**。
**验收④措辞修订（2026-07-25）**：原文"import_job 全流程走通"含 HTTP 批量上传，
现改为**批量导入走 CLI**（部署机读本地文件：大文件不经 HTTP + 全局黑名单需超管
system_tx，`compliance/router.py` 既有铁律），页面负责进度/报错报告/人工单条
登记（`POST /blacklist/assertions`，粒度更合适）。依据=开发侧 PR #35 呈报取舍，
Owner 合并 #35 即认可；如需 HTTP 批量上传，另起增量（分块+超管门控+临时文件安全）。

### R2-13 自动采购接入【L1→L2】（Amazon 采购插件，D-Q69，**MVP 内**）

**缺口来源**：Owner 2026-07-27 指出。specs 全库此前**零处**提及采购插件——需求源是
erpAPI 仓考古，而插件在独立仓（`ElijahRRR/AMZ-Purchase-Assistant`）+ 第三方 SaaS
（`smallbee168.com`），考古照不到。**性质=从厂商 SaaS 迁移，非新建能力**（现产线在跑）。

**现状核实（审计侧解包 v2.5.0 crx + 对比仓内 v2.4.1，2026-07-27）**：
- 能力=**完整自动采购机器人**：拉待采购任务→清空购物车→加购→填地址（含日本都道府县
  匹配）→结账下单→等待成功→抓回订单号→回填；另一条链抓运单号/物流事件/预计送达并回填；
  内置验证码遮罩与等待人工点击的人机接力通道。支持 amazon.com/.ca/.co.jp。
- **接入契约现成**=插件既有 9 个端点（`getNeedPurchaseOrders` / `getNeedSyncOrders` /
  `purchaseOrderFinishUpdate` / `updateOrderStatus` / `updateAmzOrderStatus` /
  `updateTrackingInfo`×2 / `updateBuyerCookie`），与 `procurement_order` 几乎逐字段对应。
- 两版差异极小（2.5.0 仅多两个日本地址选择函数）；仓内版 `baseUrl` 为占位符。

**路线（D-Q69 定案）**：**fork 插件、baseUrl 指向 ERP、由 ERP 实现该端点组**。
不保留厂商 SaaS 双向同步（数据裂两半、cookie 暴露照旧），不自建服务端无头浏览器
（亚马逊风控与封号风险不对称）。

**分片**：
- **13a 契约端点组 + 实例认证**：实现插件端点组；`plugin_instance` 实例专属 token
  （**禁全局共享密钥**）；`buyer_account.external_customer_id` ↔ 插件 `customerId` 映射；
  越权必败（实例只能取到自己账号的任务）。
- **13b 买家账号池 + 任务路由**：`buyer_account` 建表（§07 图纸）；`procurement_order`
  增 `buyer_account_id`；按站点+可用性+`daily_cap` 路由；**同一订单只派一个账号**（防重复下单）。
- **13c 三档接线**：`purchase_execute` flow（§09 v2.1，创建快照型）；**auto 档护栏必备**
  ——`amount_ceiling` 单单上限、`daily_cap` 账号日限、`price_delta_pct` 较预估涨价超阈值
  转人工。**花真金白银的自动化，护栏缺失即禁止开 auto**。
  ⚠️ **护栏消费点须从零建（2026-07-27 核实）**：`automation_policy.config` 目前**全仓零
  读者**——内核只 `SELECT mode, enabled`，四个护栏键在 `backend/src` 零命中。
  **不得假设"config 里配上就生效"**；13c 必须同时交付读取与执行护栏的代码，并以
  "超 `amount_ceiling` 必转人工且零下单"的测试证伪之（判据已在本单验收②）。
- **13d 回填与异常**：回填 `purchase_order_ref`/`purchase_cost`/`carrier`/`tracking_no`
  与状态流转；缺货/涨价/账号被风控 → `exception_reason` 并转人工；与渠道订单对账。
- **13e 迁移切换（本单最高风险片）**：从厂商 SaaS 切换按**买家账号逐个灰度**；
  **红线：同一浏览器配置内绝不同时启用两个插件——会重复下单，损失真金白银**；
  每切一个账号先跑一单人工档验证再放开档位；保留回切路径。

**安全要求（随单强制）**：fork 时**收窄 `host_permissions` 至 amazon 域**（现为 `*://*/*`，
可读取该浏览器访问过的任何站点 cookie，含 Walmart 卖家后台与飞书——防关联风险）；
**若不启用 `buyer_session` 则一并删除 `cookies` 权限与 `updateBuyerCookie` 调用**；
ERP 不可达时插件**不得自行决定采购**（fail-closed）。

**验收**：①一张真实订单在人工档由插件完成采购并回填，金额/单号/运单与亚马逊后台一致；
②三档各跑一遍（人工点采/半自动待确认/全自动），auto 档超 `amount_ceiling` 必须转人工
且零下单；③越权测试：A 实例取不到 B 账号的任务（必败）；④同一订单不产生两个采购任务
（并发下发压测）；⑤一个买家账号完成厂商 SaaS→ERP 灰度切换并稳定运行，回切路径演练一次。

### R2-14 生命周期出口【L1】（硬删除 + 墓碑 + 列表折叠，Owner 2026-07-28 指出）

**缺口来源**：Owner 使用视角发现——"不能删产品、不能删用户，只有进没有出"。
审计核实成立且更严重：**全仓 API 契约零 DELETE 端点**；产品与代理页**零写操作**
（纯只读）；图纸虽有 `retired`/`disabled` 软态，**但从未接出口**。
**最有力的证据**：清理 team 2 的 400 个产品只能由部署 AI 手写 SQL 直连生产库 + 四道闸
——系统本身没有出口，才让例行清理变成高风险手术。
**漏掉的原因**：队列按能力模块（采集/审核/上架/订单/财务）切分，而"生命周期出口"
**横切所有模块**，不属于任何一个，故无人认领。

**口径见 00-conventions §7.1**（三级规则 + 两张墓碑表 + 外键现状实测）。分片：

- **14a 产品删除（最急，量最大）**：无历史直删 / 有历史删实体留 `deleted_product` 墓碑；
  **墓碑保去重**——无墓碑硬删会让同一 ASIN 下次采集又抓回来；
- **14b 主体删除**：用户/角色/采购方/买家账号，留 `deleted_principal` 墓碑；
  审计页操作人解析改为「主表 → 墓碑 →『XX（已删除）』」三级回落；
- **14c 列表折叠**：各列表默认隐藏已停用/已归档项 + 「显示已停用」开关
  （**比删除更快见效**，且可独立先上）；
- **14d 权限与留痕**：删除动作独立权限点 + 二次确认 + **删除本身写 `audit_log`**（含 before 快照）。

**绝不删清单（硬约束）**：`audit_log`、`financial_event`/`ledger_entry`、订单与售后
（D-Q18）。清理类指令须显式声明这些表一行不删。

**验收**：①删一个从未上架的产品→物理行消失、无墓碑、列表不再出现；②删一个上过架的
产品→实体行消失但**重新采集同 ASIN 不会再入库**（墓碑生效）；③删一个操作过的用户→
其历史 `audit_log` 仍在且操作人显示「XX（已删除）」而非裸 id；④删除动作在 `audit_log`
中可查（谁/何时/删了什么/before 快照）；⑤订单、审计、财务表在任何删除路径下行数不变；
⑥列表默认不显示已停用项，开关可切换。

**排期建议（待 Owner 定）**：14a+14c 进 MVP（每天在用，产品列表会先变成垃圾堆）；
14b+14d 紧随。

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

## MVP 剩余路径（✅ 定案，Owner 2026-07-27：D-Q66/67/68）

> 口径：PRD §8 九模块 + MVP 必做加固三张。九模块现状 ≈60-65%（完成 1/3；收尾中 2/4；
> 部分 5/6/7；未动 8/9）。**门户移出 MVP 后，模块 6 视为 MVP 口径已达成**。

| 序 | 事项 | 性质 | 备注 |
|---|---|---|---|
| **0** | **RS-02a 端口与口令**（P0 即刻） | 开发 AI 改 compose | **不排队、插队做**：db/redis 绑 127.0.0.1、换 PG 默认口令、Redis `requirepass`、默认密钥硬失败。依据 D-Q68 实测 |
| 1 | R2-09 三档自动化贯通 | 在推 | 前置已解除（flow v2 冻结） |
| 2 | R2-07c 邮箱最小闭环 | 小 | IMAP 凭证已具备（D-Q66 轮次确认），模块 7 收官 |
| **2.5** | **R2-13 自动采购接入** | **MVP 内（D-Q69）** | 紧接 R2-09——需要 `purchase_execute` 三档护栏才敢开 auto；每天真实省人力，价值高于报表类。**注意是迁移不是新建**（现跑在厂商 SaaS），13e 灰度切换为最高风险片 |
| **2.7** | **R2-14 的 14a + 14c**（产品删除+墓碑、列表折叠） | **MVP 内（D-Q70）** | 每天在用，产品列表最先变垃圾堆；14c 可独立先上。**14a 陷阱**：无 `deleted_product` 墓碑则同 ASIN 重采回流 |
| 3 | R2-08 财务域 | 大 | 图纸就绪（幂等键 2026-07-26 已修正为渠道自然键）。**同币种分支**：美亚采购 USD 收支同币，`fx_rate=1` 不折算（§07 修订） |
| 4 | RS-06 人工复核工作台 | MVP 必做 | needs_review 无承接界面则单子堆积无人处理 |
| 5 | RS-08 LLM 预算闸 | MVP 必做 | 日 20 万审核量的成本硬护栏（现仅告警版） |
| 并行 | R2-12 收尾（验收①三日连测）、R2-04 收尾（beat A152 实测）、RS-02b（备份加密+校验+月度恢复演练） | 收尾 | 不占主路径 |

**挂账/推后**（不进 MVP）：R2-05 发货环节（Owner 2026-07-27 决定暂不动，等真实新单）、
R2-10 门户（D-Q66）、RS-01（单团队自用可等，开第二团队或对外前必做）、
RS-05/07/09/10 与 RS-04C（D-Q67）、FE-DESIGN（Owner 触发制）。

**质量项挂点**（Owner 2026-07-27 认可）：**压测**挂 RS-06 之后（那时才有真实并发场景）；
**前端底线单测**（008 §7 FE-TEST-01）在 FE-DESIGN 启动前完成。

**备份红线（D-Q52）执行口径**（随 RS-02b 验收）：`backup.sh` 已具备 pg_dump + 本地
14 天 + rclone 通道，缺四项——① 异地目标未配；② Win11 定时任务未确认在位；③ **恢复演练
一次未做**；④ dump 含店铺凭证但未加密。落地要求：**异地副本排除 `refdata` schema**
（1400 万商标行可从 USPTO 重建，不占异地带宽/成本）；**上传前经 `rclone crypt`**（云端
只见密文，守住"凭证不出机"）；**每日轻量校验**（对最新 dump 跑 `pg_restore --list`，
损坏一天内暴露）+ **每月真恢复演练**（临时库比对 5 张关键表行数）；失败必须告警。
Owner 侧唯一动作 = 选云并提供访问密钥。

## 角色分工（四方，Owner 2026-07-26 新增独立审查 AI）

| 角色 | 职责 | 写权限边界 |
|---|---|---|
| **云端 AI**（开发） | 写代码、写 `.agent/` 台账、开 PR、写批注回传 | 代码 + `.agent/` + **其余 specs（含 002 契约：openapi 随端点同 PR 维护）**；**不写** `specs/007-*` 与 `specs/001-domain-model/` 图纸正文——该两处只可**追加"已落地"实现附注**，设计变更走批注回传 |
| **部署 AI**（Win11 部署机） | 真机验证、数据迁移、定时任务、取证 | **不改码、不 push**；指令须可整段粘贴且自带铁律 |
| **审查 AI**（独立评审，2026-07-26 新设） | **逐 PR 通读实际 diff** 出审查报告 | **只读**——结论**必须发成 PR 评论**（`.agent/evidence/reviews/PR-<n>.md` 为可选留档；只写文件不发评论＝对方收不到唤醒＝报告没交）；**不写** specs、不写 `review_list.json`、不改代码、不 push 分支 |
| **规划/审查 AI**（本文作者，审计侧） | specs/007 与 001 图纸正文落笔、跨域架构、验收判定、给 Owner 打包决策 | `specs/**` + `review_list.json` 的 gate/验收字段 |

**合并前闸序（2026-07-26 起）**：CI 绿 → **审查 AI 通读 diff** → 部署机真机验证 →
**Owner 授权合并**。审查 AI 位于 Owner 拍板之**前**，其价值即保护该次授权决策。

**审查 AI 的首要纪律**：**以源码为准，不以 PR 正文为准**——第一件事永远是核对
"正文声称的改动范围 == 实际 diff"（本项目已发生两次正文漂移：一次写"纯台账回写
零代码改动"、一次写"零迁移"而实际含迁移，而 Owner 的合并授权正是基于正文）。
重点域：fail-open/fail-closed 方向错置（本项目复发 bug 类，合规与闸门一律
fail-closed）、迁移 up/down 与权限授予范围、并发与事务边界、声称的测试是否真能红。
**不复查 CI 已覆盖项**（lint/类型/格式）。只报会改变代码或改变 Owner 决策的问题。

**为何独立**：规划侧写规范又判规范符合性，结构上不擅长发现"规范本身错了"——
2026-07-24~26 连续三例由开发侧纠正审计侧论断（R2-11 现状、R2-07 考古锚点、
§08 幂等键），即为实证。独立审查方无此包袱。

开发侧与审查侧对本计划的异议一律走**批注回传**（同外部评审通道，落
`.agent/evidence/`），**不直接改本文档**；工单验收判据如需调整，经 Owner 或
审计侧确认后由审计侧修订。
