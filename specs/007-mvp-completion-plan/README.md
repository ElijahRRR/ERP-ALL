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
  > **定案：自建**，连接层用 **`imap_tools`**（834★，Apache-2.0，431 提交、仅 2 个未解
  > issue；消息/附件对象、搜索构建器、文件夹管理齐备），架在现有渠道网关骨架
  > （多账号/定时/重试/凭证加密/审计）之上——**不是新建基础设施，是换协议**。
  > **选 imap_tools 而非 imapclient 的理由（2026-07-29 读源码后修正初判）**：初判以
  > 「imapclient 内置 `id_()`」为主因，**该权重判断错误**——ID 是**一次性 5 行**成本，
  > 而 imap_tools 省掉的是**几百行 MIME 解析**（中文邮件的 GBK 信头/base64 主题/附件
  > 文件名编码是最耗时且最易埋隐蔽 bug 的地方）。
  > **163 接法（源码实证）**：`imap_tools.login()` 默认 `initial_folder='INBOX'`，
  > **登录即 select、无插入 ID 的窗口**，照默认写法必栽；须传 `initial_folder=None`
  > 跳过自动 select（作者提供的正规参数，见 `mailbox.py` 的 `if initial_folder is not
  > None`），经 `mb.client`（原生 imaplib 实例）发 ID，再 `mb.folder.set('INBOX')`。
  > **退路**：若实测该形态不通，退回 `imapclient`（内置 `id_()`）。
  > ✅ **IMAP ID 必要性已实测证实（2026-07-29 Owner 真机 A/B）**：不发 ID → `SELECT
  > INBOX` 报 `Unsafe Login`；先发 ID → 正常打开收件箱（96 封）并取回信头。**升级为
  > 硬约束**，实现不得省略。⏳ **仍未验证**：`imap_tools` 的
  > `initial_folder=None` + `mb.client` 发 ID + `folder.set()` 这一形态（探针 C 组因
  > 环境未装 `imap-tools` 跳过）——**列为动工第一步**，通过则按本图纸实现，
  > 不通过即退 `imapclient`。
  > 可借鉴的一手参考：`EthanYoQ/Invoice-Downloader`（136★，**Apache-2.0 可商用**，
  > 生产级 163 IMAP 消费者）；分层思路另参 datawhale「163 邮箱助手实战」（脚本层出
  > 结构化数据／凭证层／提示词层／定时层四层分离，与我方 L0→L3 同构）。

  **底座范围（只做这四件）**：①**账号池**（`mailbox` 表：授权码加密、绑店铺、**代理默认
  取店铺代理**、启用状态、同步水位）；②**拉取调度**（挂 R2-04 beat，按账号增量拉取，
  UID 水位 + 去重，失败退避与告警）；③**收发**（正文/附件落库，发信用**该店铺自己的
  邮箱**经 SMTP 发出并留痕）；④**可观测**（每账号最近成功时间、失败次数、当前状态
  一屏可见——几十个邮箱没有这个面板等于盲飞）。
  **明确不做**：实时 IDLE 长连接、文件夹全量同步、离线缓存、多协议抽象层、通用邮件
  客户端 UI——这些是无底洞且我们不需要。**范围闸**：完整客户端体验（会话线程/模板/
  附件/回信）＝MVP 后第一批（D-Q22 备注原文），本片不做。
  **无旧实现可移植**（已核实旧仓无 mail 模块）。
  **AI 分类完全剥离为后续独立单**：`mail_message.classification` 列先建好（底座期一律
  写 `none`），`llm_usage.module=mail_classify` 枚举 R1 已预留，**接分类时无需改表**；
  「邮件→自动分类→生成 store_incident + 告警」整条链归后续单，提示词类比 L3 政策路由自建。

**验收**：①A152 真实 returns 拉取与渠道后台对账一致；②手工造 incident 演练
封店→品牌占用批量 released→beat 提醒送达；③**07c 底座（AI 无关，2026-07-29 改判据）**：
真实 163 邮箱经**所绑店铺的代理出口**完成登录→发 IMAP ID→增量拉取→正文附件落库，
UID 去重不重不漏；用该邮箱 SMTP 发出一封并留痕；账号面板可见最近成功时间与失败次数；
**故意填错授权码 → 连续认证失败达阈值即自动停轮询并告警（不得重试撞墙）**。

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
  （**禁全局共享密钥**）。
  ⚠️ **令牌绑的是「一台授权浏览器」，不是「一个买家账号」**（Owner 2026-07-30 质疑
  「插件为什么做成每个买家号专用的」后更正，口径见 §07「身份从哪来」）：
  身份两段式——**令牌管授权**（这是团队 T 的一台机器吗）、**请求带的 `customerId` 管身份**
  （这台机器此刻登的是哪个买家号，插件从亚马逊页面现场提取）。
  越权边界＝**跨团队必败**，不是"实例只能取到自己账号的任务"。
- **13b 买家账号池 + 任务路由**：`buyer_account` 建表（§07 图纸）；`procurement_order`
  增 `buyer_account_id`；按站点+可用性+`daily_cap` 路由（`daily_cap` 读的是账号列，权威侧）；
  **同一订单只派一个账号**（防重复下单）。
  ⚠️ **`external_customer_id` 是首见自动登记，不是人工预录入**（Owner 2026-07-30 质疑
  「我插件没安装拿不到这个 ID」后更正——原写法要求装插件前先知道 ID，而 ID 只有装了插件
  才看得到，是个死循环）：服务端遇到本团队没见过的 `customerId` → 自动落一条
  `buyer_account(status='pending_claim')` 并通知，运营补 `label`/`site`/`daily_cap` 后转
  `active`；**`pending_claim` 一律不派单**。
  「提取 customerId」按钮**保留但定位是纯展示**——源码里 `handleExtractCustomerId()` 只把值
  写进页面一个 `<div>`，不存不发，用途是让人肉眼看见"这台机器登的是哪个号"。
- **13c 三档接线**：`purchase_execute` flow（§09 v2.1，创建快照型）；**auto 档护栏必备**
  ——`amount_ceiling` 单单上限、`price_delta_pct` 较预估涨价超阈值、`delivery_days_limit`
  预计送达超天数，三者任一触发转人工。**花真金白银的自动化，护栏缺失即禁止开 auto**。
  ⚠️ **`daily_cap` 不是本 config 的键**（Owner 2026-07-30 裁定：账号属性归账号表，业务策略归
  flow config）——账号日限的**唯一落点是 `buyer_account.daily_cap` 列**，由 **13b 路由时消费**；
  13c 不实现同名键，以免引回「两处定义、一处漏查即放行」的失效模式。
  ⚠️ **护栏消费点须从零建（2026-07-27 核实）**：`automation_policy.config` 目前**全仓零
  读者**——内核只 `SELECT mode, enabled`，护栏键在 `backend/src` 零命中（该结论对新旧键集
  同样成立，2026-07-30 复核）。**不得假设"config 里配上就生效"**；13c 必须同时交付读取与
  执行护栏的代码，并以"超 `amount_ceiling` 必转人工且零下单"的测试证伪之（判据已在本单验收②）。
  **开 auto 的闸**：不是"排在 R2-09 之后"（R2-09 从未建护栏消费点，照字面 R2-09 一收账就能开
  auto，是个洞），而是 **auto 档开关锁死在 13c 交付并验收之后**（Owner 2026-07-30 裁定，更严非放宽）。
- **13d 回填与异常**：回填 `purchase_order_ref`/`purchase_cost`/`carrier`/`tracking_no`
  与状态流转；缺货/涨价/账号被风控 → `exception_reason` 并转人工；与渠道订单对账。
- **13e 迁移切换（本单最高风险片）**：从厂商 SaaS 切换按**买家账号逐个灰度**；
  **红线：同一浏览器配置内绝不同时启用两个插件——会重复下单，损失真金白银**；
  每切一个账号先跑一单人工档验证再放开档位；保留回切路径。
  ⚠️ **回切方式 = 换回厂商原版插件，不是改 `baseUrl`**（Owner 2026-07-30 裁定"把插件改成
  一组"的下游后果）：fork 版的 URL 路径已与厂商后端不一致，指回去只会 404。**回切演练必须
  实测「装回原版插件 + 原 baseUrl」这条真路，不能只测改配置**；演练全程红线不变。

**安全要求（随单强制）**：fork 时**收窄 `host_permissions` 至 amazon 域**（现为 `*://*/*`，
可读取该浏览器访问过的任何站点 cookie，含 Walmart 卖家后台与飞书——防关联风险）；
删除 `storage`/`tabs`/`scripting` 三条零调用权限；ERP 不可达时插件**不得自行决定采购**
（fail-closed）。
⚠️ **cookies 整条链删除（Owner 2026-07-30 裁定，"先不收 cookie"）**：`cookies` 权限、
`updateBuyerCookie`、`getCookiesAsJson()`、`background.js` 的 `onMessage` 监听器**全部删掉**，
fork 后 `permissions` 应为空数组。**理由不是"暂时不用"而是"用途已被证伪"**——那份 jar 的字段
整形（`hostOnly`/`sameSite:'no_restriction'`/`storeId:'1'`/`id:index+1`）正是 Cookie-Editor
那类插件的导出格式，该格式**唯一目的是被导入回浏览器**，所以它是整套可搬运的登录态而非标识；
「这是哪个买家号」由同一调用里的独立参数 `customerId` 承载。目的既已另有达成路径，收 cookie
只剩风险没有收益（jar 一旦落库，持有者即可以买家身份下单）。**`buyer_session` 表不建**。
连带作废此前那条"给 `onMessage` 加 `sender` 校验"——监听器整个删掉比加校验更强，校验已失去对象。

**执行档位（Owner 2026-07-30 补充，图纸原先没有的必需能力）**：插件 fork 必须带
「走到付款页即停并回报」的档位，否则"停在付款前一步"的验收执行不了（现插件一路走到下单完成）。
沿用本仓 `channel.gateway_mode` 同款三态：

| 档 | 行为 | 用途 |
|---|---|---|
| `dry_run` | 只做校验与地址/购物车填充，**不进结账页** | CI 与本地 |
| `stop_before_payment` | 走完结账页、抓到实付金额与预计送达，**在点付款前停下并回报** | **不花钱验收的主力档** |
| `live` | 完整下单 | Owner 用真实订单收口 |

**验收（分两层，Owner 2026-07-30 定执行方式："前面停在最后一步付款就可以，前面通了我会拿
真实订单来测完整流程"）**：

- **`stop_before_payment` 可证伪层（不花钱）**：②三档各跑一遍（人工点采/半自动待确认/全自动），
  auto 档超 `amount_ceiling` 必须转人工且零下单；③**越权测试（边界＝团队，2026-07-30 更正）**：
  团队 A 的实例带团队 B 的 `customerId` 取任务必败；且未认领的 `customerId`（`pending_claim`）
  **不派单**、只落待认领行 + 通知；同一实例换带另一个**同团队已认领**的 `customerId` 应正常路由
  （证明令牌绑的是浏览器不是账号，换登即跟随）；
  ④同一订单不产生两个采购任务（并发下发压测）；⑥实付金额可抓回且价格护栏三段式的客户端判定成立；
  ⑦预计送达可抓回且超 `delivery_days_limit` 转人工；⑧非 FBA / bundle 前置拦截生效。
- **必须 `live` 层（花真钱，Owner 亲自收口）**：①一张真实订单完成采购并回填，
  金额/单号/运单与亚马逊后台一致；⑤一个买家账号完成厂商 SaaS→ERP 灰度切换并稳定运行，
  回切路径演练一次（按上文"换回原版插件"的真路演练）。

**范围与版本（Owner 2026-07-30）**：**日本站 MVP 暂不做** ⇒ fork 仓内 **v2.4.1 即可**
（v2.5.0 的核心新增就是 `JP_PREFECTURE_EN_TO_JA`）。但 `receivingDistrict`（JP 必填）与
异常类 `address` 的 JP 分支**保留代码路径**——不做不等于删掉，将来开 JP 只补映射表。

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

**落地进度（2026-07-30 核实）**：**14a + 14c 已合并**（PR #46，main `c2e5e72`，四闸全过：
CI 绿 → 审查变异证伪 → 部署机 ①–⑯ 真机全过 → Owner 授权）。全仓第一个 DELETE 端点
`DELETE /products/{id}`（权限点 `catalog.product_delete`），迁移 0041 建 `deleted_product`
墓碑并补授 `listing`/`listing_spec` 的 DELETE（0009:314 原只授 SELECT/INSERT/UPDATE）。
**已覆盖验收 ①②④⑤⑥**。**未做**：验收③ 属 **14b**（用户/角色/采购方删除 + `deleted_principal`
墓碑 + 审计页操作人三级回落）；`14d` 的权限点与二次确认已随 14a 落在产品侧，其余主体面未接。
**范围提醒**：现在能删的只有产品——listing / store / proxy / user 仍无删除出口
（`FX-0729` 代理页零写即其中一例）。故 R2-14 整单仍挂 `in_progress`。

⚠️ **"前端看不到删除入口"的成因（Owner 2026-07-30 反馈，已核实：代码在、部署机没更新）**：
删除按钮与二次确认弹窗**都在 main 里**（`frontend/src/pages/ProductsPage.tsx`：`canDelete`
门控的删除按钮、必填删除原因、按①/②级分别措辞的成功回执）；权限也不是问题——`has()`
是 `me.user.is_super || permissions.includes(...)`（`AuthContext.tsx:69`），后端
`CurrentUser.can()` 同款短路（`core/authn.py:47`），**超管一律放行**。
**真正原因**：R2-14 真机验证的收尾步骤 ⑯ 是"先降库、后切 main"，终态为
`main@18389b5` + `alembic_version=0040`（回执 §开头），而 **PR #46 是验证通过之后才合并的**
——部署机此后没有再拉一次，跑的仍是 #46 之前的代码。
→ **恢复步骤（部署机三件事）**：拉最新 `main` → `alembic upgrade head`（现 head=`0042`）
→ 重新 build 前端。
→ **流程缺陷（提请纳入 runbook）**：验证收尾把机器恢复到验证前状态是对的，但**"PR 合并后
重新部署"这一步不在任何人的清单里**，于是"验收通过"与"线上能用"之间断了一环。
建议：合并后由部署 AI 执行一次"上线部署"并回执，作为第四闸之后的固定收尾。

### 14b 的三个实现坑（考古实测，2026-07-30，开工前先看）

1. **`purchaser.user_id` 会挡住删用户**：`0025_order_domain.py:96` 是
   `bigint REFERENCES app.app_user(id)`——可空但**无 `ON DELETE`**，即默认 `NO ACTION`。
   删一个当过采购方的用户会被外键直接拒绝。须在删除服务里显式先处置（置 NULL 或连带），
   不能指望 CASCADE。（对照：`user_role` 对 `app_user`/`role` 都是 `ON DELETE CASCADE`，那两条没问题。）
2. **`deleted_principal` 的主键必须是 `(kind, id)`**：§7.1 写的是 `(kind, id, label, ...)`，
   四类主体（user/role/purchaser/buyer_account）各有各的自增序列，**跨表 id 必然撞号**，
   单列 `id` 做 PK 会把张三和某个角色写成同一行。
3. **"买家账号"那一类依赖 R2-13**：`buyer_account` 表由 **13b 建**，现库中不存在。
   → **14b 并行开工时先做 user / role / purchaser 三类**，`buyer_account` 分支挂在 13b 之后补，
   不要为了凑齐四类而阻塞整片。

### R2-16 存量接管【L1】（后台在线产品补挂，Owner 2026-07-30 指出）

**缺口来源**：Owner —— "拉取后台全部在线产品的功能没有"。
**审计核实：拉取有，落地没有。** `item_pull`（R2-12 增量 4a，PR #33）确为全量扫描
——按 `publishedStatus` 逐态扫、`offset` 翻页、跨页去重，A152 真店跑通并查出
`missing_local=45`。但三类差异按 P0-2 拍板「**只发现不执行**」，其中「后台有本地无」
这一类的全部落点只有：`sync_state.stats.missing_local` 一个计数 + `missing_sample`
前 50 条 + 通知正文前 10 条 SKU。**既不落表也无页面，超 50 条即丢**——Owner 说"没有"，
在他能操作到的层面完全成立。

**当时不做的理由成立，但那是「本轮不做」而非「不用做」**：`listing.product_id NOT NULL`
而拉取侧没有产品链路，硬造行会留半基线。**问题是没立后继单**——缺单成因同 R2-13/R2-14：
对账是横切能力，队列按模块切分时无人认领。

**为什么它比看上去要紧**：ERP-ALL 是来替代 erpAPI 的。补挂不做，新系统就只能管
**新上架**的品，存量在线 SKU 整体留在 ERP 之外——审核、改价、下架、订单回连全都够不着。

**技术前提（已核实）**：

- 存量 SKU **99% 就是 ASIN**（Owner），与 R2-15 定的 `channel_sku=ASIN` 同形，
  故补挂可**复用现役 scrape 链路**建 product，不必新造一条拉取侧采集通道；
- 但 `item_pull` 的 `remote` 字典只留 `published_status`/`lifecycle`/`reasons`，
  `ItemResponse` 里的 `wpid`/`gtin`/`price`/`productName` **全部丢弃**，补挂需回取或扩采；
- 墓碑闸必须共用：R2-14 删掉的产品不得从这条新路回流。

**分片**：16a 落表 + 页面可见（把计数变成可操作对象）；16b 单条/批量补挂
（建 product → 建 listing，`channel_sku` 保持后台原值）；16c 不可自动补挂项的显式
标注与人工通道。

#### 开工前设计裁定（2026-07-30 考古后落定；**裁定一于同日修正**）

**裁定一（修正版）：用图纸既有的 `sku_mapping`，不新建表。**

> ⚠️ **本条原写「必须新建 `channel_orphan_sku`」，是错的。** `maintenance_task` 装不下的
> 推理没问题（`listing_id bigint NOT NULL`，`0009_listing.py:272`，而补挂对象恰恰没有本地
> listing），但**结论跳过了一张早就为这件事设计的表**：`sku_mapping`（§03「渠道 SKU 映射
> （存量桥）」）。其原文用途就是「**从 Walmart 重拉在线商品时（D-Q35）批量生成 legacy 行**」，
> 且 `product_id` 一列明写 **NULL——「可空：重拉的历史在线品可能未入产品库」**。
> **成因**：2026-07-29 我为 R2-15 判据⑥写下「`sku_mapping` 保留为设计意图，不作为任何工单的
> 验收对象」，次日立 R2-16 时没有回头看它——**与前几次同源（写新内容时未回查既有定义），
> 只是方向相反：那几次是引用了不存在之物，这次是没看见已存在之物。**

**为什么这张表是对的**——它把两个被 `listing` 混在一起的概念拆开了：

| | 承载什么 | `product_id` |
|---|---|---|
| `listing` | **我们上架的东西** | `NOT NULL`（没有产品就不该有上架单） |
| `sku_mapping` | **渠道上存在这个 SKU** | **NULL 可**（渠道上有，本地未必有） |

Owner 2026-07-30 提的正是这个场景：「**有可能亚马逊端还没入库，但沃尔玛端的产品已经先进来了**」
——`sku_mapping` 的 `product_id` 可空，天生就是为这个留的。

**据此，「后台有本地无」不需要一张新的工作队列表**：它就是
`sku_mapping WHERE product_id IS NULL` 这个查询。建两张表会让"渠道 SKU 的存在性"有两个真相。

**图纸需改三处**（R2-16 落地时一并做）：

1. **加显式 `team_id` 列**。原文写「+公共列（team_id 经 store）」，但全仓 RLS 策略一律是
   `team_id = app.current_team() OR app.is_super()`**长在表自己身上**（`0009_listing.py:23-31`
   模板），没有 team_id 列就套不上这套策略。与 `listing`/`maintenance_task` 同形，冗余一列。
2. **补链接态与采集态列**：`link_state`（见下方阶梯）、`first_seen_at`/`last_seen_at`
   （连续多轮不再出现即可判定渠道侧已消失）、`published_status`/`lifecycle`、
   `wpid`/`gtin`/`title`/`price`（渠道侧原始信息）。
   ⚠️ **这些渠道字段 `item_pull` 现在全丢了**——`remote` 字典只留
   `published_status`/`lifecycle`/`reasons`，16a 须扩采。
3. **去掉「不作为任何工单的验收对象」那句**——R2-16 就是它的落地工单，该句已过期。

#### 对应关系怎么建立（Owner 2026-07-30 两轮追问 + 旧仓 `erp-core` 考古，本单核心设计）

> **Owner 裁定**：「**ASIN 是唯一的，不应该并存两条 product 指同一实物。**我目前沃尔玛的
> SKU 编码也是 ASIN，拉下来以后直接同步 SKU 和 ASIN 就可以了。然后为沃尔玛拉取下来数据
> 也建档，erp-core 中有部分实现，可以参考他的运作机理。」
>
> ⚠️ **这条推翻了我上一版的「`source_channel='walmart'` 占位产品 + 人工合并」**——那个方案
> 是在给自己造合并负担：先造出重复实体，再设计怎么把它合掉。**Owner 是对的，且旧仓
> `erp-core` 早就是这么做的**（`backend/app/api/v1/store_sync.py`，生产验证过）。

**核心一句话：产品身份就是 ASIN，与数据从哪来无关。**
从 Walmart 后台拉回来的数据，直接写进 `product(team_id, 'amazon', ASIN)` **那一行**——
不是另建一行。`source_channel` 表的是**身份命名空间**，不是"这次数据从哪个网站抓的"。

**旧仓的做法（`store_sync.py:520-570`，逐字可移植）**：

```python
if not real_asin:
    continue                     # 提不出 ASIN 的：只入 listings，不建产品行
# 以 ASIN 唯一确定：同 ASIN 跨多店铺 = 同一个 products_master 行
existing = SELECT id FROM products_master WHERE asin = :a
UPDATE products_master SET
    title = COALESCE(:t, title),          # ← 关键：只补空，不覆盖
    brand = COALESCE(:brand, brand),
    extra = COALESCE(extra,'{}') || :extra
```

`COALESCE(:新值, 现有值)` 是整个机制的关键：**Walmart 侧拿到的稀数据只填本地为空的列，
绝不覆盖亚马逊采集来的好数据**；后来采集补全时自然接管。**先到的占位，后到的补全，
自始至终只有一行。** 不需要 `pending_asin_link`，不需要人工合并，不需要墓碑清理。

**ASIN 怎么从 SKU 里拿到——旧仓是 regex `search` 不是相等判断**（`store_sync.py:239`）：

```python
asin_match = re.search(r"B[A-Z0-9]{9}", str(sku).upper())
```

用 `search` 而非 `fullmatch`，所以 **SKU 里"含有"ASIN 就能提出来**——这一条同时覆盖了
R2-15 的 `ASIN-2` 后缀、以及历史上各种把 ASIN 编进 SKU 的写法（`前缀-B0XXXXXXXX`）。
比我原设计"channel_sku 形如 ASIN"的相等判断宽得多，也实用得多。
> 建议收紧为 `B0[A-Z0-9]{8}`：真实 ASIN 除书籍（ISBN 10 位数字）外一律 `B0` 开头，
> 而 `B[A-Z0-9]{9}` 用 `search` 会在长 SKU 里误命中一段。**这一条要在真实 SKU 集上先验一遍
> 再改**——若存量里有非 `B0` 开头的 ASIN，收紧就会漏。

**于是五级阶梯塌缩成三级**：

| 级 | 条件 | 动作 | `product_id` |
|---|---|---|---|
| **0** | 一律 | upsert `sku_mapping(store_id, channel_sku, origin='legacy')` | 先留 NULL |
| **1** | 从 SKU **提得出 ASIN** | upsert `product(team,'amazon',ASIN)`：已有则 `COALESCE` 补空列，没有则用 Walmart 侧字段建行 → 回填 `product_id` | **立刻有** |
| **2** | 提不出 ASIN（存量手工编码，~1%） | 不建产品（旧仓 `if not real_asin: continue` 同款），`link_state='unresolvable'`，开人工填 ASIN 通道 | NULL |

**这个塌缩消掉了三个我上一版自造的问题**：

- **"亚马逊采不回怎么办"不再是问题**——建档根本不经亚马逊。Walmart 上还在卖、ASIN 已经死了
  的品（dropshipping 里极常见，且**最该被发现：还在收单但没货源**）照样建档、照样能下架、
  照样进合规审核。原设计把「建档」和「采集」耦在一起，导致亚马逊采不到就卡死，是设计错误。
- **"两条 product 指同一实物"不再存在**——一个 ASIN 一行，`uq_product` 直接保证。
- **"并发竞争"退化为普通 upsert**——补挂与亚马逊采集写的是同一行，
  `ON CONFLICT (team_id, source_channel, source_ref) DO UPDATE`（`scrape/service.py:606`）天然兜住。
  仍保留一条纪律：回填 `sku_mapping.product_id` 时**回查取 id，不用自己那条 INSERT 的 RETURNING**。

**"再去亚马逊采一次"变成独立的可选后续动作**（补全详情 + 过合规审核），
**不是补挂的前置条件**。这是本轮最大的简化。

**Walmart 侧能拿到什么字段**（旧仓 `fetch_my_walmart_items.py:60-82` + `store_sync.py:229-235` 实测）：
`sku` / `wpid` / `itemId` / `upc` / `gtin` / `productType` / `productName` / `brand` /
`manufacturer` / `shelf` / `mart` / `price.amount`+`currency` / `lifecycleStatus` /
`publishedStatus` / `availableToSellQuantity` / `mainImageUrl` / `wfsEnabled`+`shipNode`（推 WFS/SELLER）。
**足够建一条能用的产品行**（`product.title NOT NULL` 由 `productName` 满足）。

**裁定二：补挂出来的 listing 用 `offer_mode='adopt'`（新增第三值），状态走 `transition()` 不直写。**
现 CHECK 只有 `build`/`match`（`0009_listing.py:82`），而接管来的品**两者都不是**——它既不是我们
构建的，也不是我们匹配上架的。**不能拿 `match` 顶替**：`service.py:613` 是
`feed_kind = "item_build" if offer_mode == "build" else "item_match"`、`:643` 是
`skip_variant=(offer_mode != "match")`，混用会让接管来的行**被当成可重新提交的 match 单**，
而我们手里根本没有它的 spec。
→ 新增 `adopt` 值，并立一条硬规则：**`adopt` 的 listing 不进提交链路**，提交入口显式拒绝
（判据里要证伪）。状态则**必须走 `listing.service.transition()`**——它是状态迁移唯一出口、
同事务写 `listing_state_history`；直接 `INSERT ... status='live'` 会绕过状态机、留不下来源记录。
落法：先按默认 `draft` 插入，再 `transition(..., 'live', reason_code='ADOPTED_FROM_CHANNEL')`。

**裁定三（修正版）：补挂是同步的，不经采集。**

> ⚠️ **本条原写「补挂天然是两段式异步（提交采集 → 等回来 → 建 listing）」，已作废。**
> 那是「product 必须由 scrape 建」这个错误前提的推论。按 Owner 裁定与旧仓做法，
> 产品行**由 Walmart 侧字段直接 upsert**（身份=ASIN），一个事务内完成，
> 没有等待、没有中间态、没有"点完按钮不知道发生了什么"的问题。
> `link_state` 因此不需要 `adopting` 这个态。

**「再去亚马逊采一次」是独立的可选后续**：补全详情、跑合规审核。它失败不影响补挂已完成的事实。

⚠️ **「不造半基线行」的边界**：这条纪律**只管 `listing`**——listing 要么带着非空
`product_id` 完整建成，要么不建。它**不适用于 `sku_mapping`**：级 0 那行**永远保留**，
`product_id IS NULL`（级 2，提不出 ASIN）是它的合法常态而不是半成品。

#### 旧仓 `erp-core` 里现役 `item_pull` 没有的东西（16a 逐条对照）

Owner 指的「erp-core 中有部分实现，可以参考他的运作机理」——考古 `store_sync.py`
（1559 行，注释里带大量「用户校正 N 次」的生产教训）后，列出**现役 `item_pull` 的实际缺口**：

| # | 旧仓有 | 现役 `item_pull` | 影响 |
|---|---|---|---|
| 1 | 轮次含 **`STAGE`**（待 GoLive） | `statuses` 只有 PUBLISHED/UNPUBLISHED/SYSTEM_PROBLEM | **漏一类**在架品 |
| 2 | 单跑一轮 **`lifecycleStatus=RETIRED`** | 完全不扫 | 卖家已 RETIRE 的历史记录看不见 |
| 3 | **`OFFSET_CAP = 9800`**，超了显式报 error | `max_pages=100 × 200 = 20000`，无上限判断 | 单状态超 9800 时**静默截断**——循环按"本页 < limit"终止，会**误以为扫完**，于是超出部分全被算成 `gone_remote` |
| 4 | ⚠️ **同时传 `lifecycleStatus` + `publishedStatus=UNPUBLISHED/SYSTEM_PROBLEM/STAGE` → Walmart 返 404**（实测 2026-05-06） | 现役只传 `publishedStatus`，**恰好避开了** | 无缺口，但**扩 RETIRED 轮时必须知道这条**，否则一加 `lifecycleStatus` 就 404 |
| 5 | `unpublishedReasons` 兼容 dict / list / str 三种形态 | `_reasons()` 只取 `dict.reason` | 渠道换形态时静默取空 |
| 6 | **`gone_remote` 的收敛机制**：`orphan_miss_count` 计数器，看到就重置，**连续 3 次全量未见才推 deleted**（"避免 walmart 偶发分页漏拉就误删"） | 只计数，永不收敛 | 现役的"保守不迁移"是对的，但**没有出路**——旧仓给出了完整的安全收敛法 |
| 7 | **`_reconcile_lifecycle` 不限 `store_id`，全库扫**——"一个 ASIN 任一店成功上架就算 listed"（用户校正八次） | 无此概念 | 跨店状态联动缺失 |
| 8 | 库存字段**四级优先级**（`availableToSellQuantity` → `inventory.quantity` → `inventory.availableToSellQuantity` → `inventoryCount`，实测 2026-04-29） | 不取库存 | 补挂要写 `current_inventory` 时会踩 |

> **第 3 条是唯一的现役正确性缺陷**（其余是缺功能），建议**独立于 R2-16 先修**：
> 一个店铺单状态超 9800 SKU 就会拉不全，而失败方式是静默的。

## 三路并行开工须知（Owner 2026-07-30：R2-13 / R2-14 14b / R2-16 并行）

> 三条线各自的设计都已可开工（R2-16 的三个未决点已由上文三条裁定补齐）。**并行的风险不在
> 设计，在共享面**——下面五条是实测出来的碰撞点，开工前先按这个分。

### 1. alembic 迁移号必须预分配（不分配 = 必撞）

现 head = `0042`。三条线都要加迁移，各自从 `0042` 往下接就会生出三个 `0043`，
`down_revision` 链分叉，**后合的两条都得改文件名 + 改链**。预分配区段：

| 线 | 号段 | 预期内容 |
|---|---|---|
| R2-13 | `0043`–`0046` | `buyer_account` / `plugin_instance` / `procurement_order` 增列与 `status` CHECK 扩 `pending_review` / `procurement_logistics_event` |
| R2-14 14b | `0047` | `deleted_principal`（PK `(kind,id)`）+ 主体删除权限点 |
| R2-16 | `0048`–`0049` | `sku_mapping` 建表（§03，含 R2-16 补的 `link_state` 等列）+ `ck_listing_mode` 扩 `adopt` |

用不完的号**留空不补**——号段连续不是不变量，链正确才是。

### 2. 跨线依赖：只有一条，且方向单一

**14b 的「买家账号」那一类依赖 13b 建表**。处置见上文「14b 的三个实现坑」第 3 条：
14b 先做 user / role / purchaser 三类，`buyer_account` 分支挂在 13b 之后补。
除此之外三条线**无依赖**——R2-16 判据⑦要用的 `deleted_product` 墓碑闸已随 14a 在库里（0041）。

### 3. 文件冲突面（会同时被两条线改的）

| 文件 | 谁碰 | 说明 |
|---|---|---|
| `backend/src/erp/catalog/router.py` | 14b、R2-16 | 一个加主体删除、一个加补挂端点；同文件不同段，冲突可控 |
| `frontend/src/pages/ProductsPage.tsx` | 14b、R2-16 | 14b 基本不动它（主体删除在 Users/Roles 页），R2-16 若把 orphan 清单做成产品页的标签页则会撞——**建议 R2-16 单独开页**，见 008 §6「账本/投影独立入口底线」 |
| `backend/src/erp/listing/service.py` | R2-16 | `offer_mode` 新增 `adopt` 后，`:613`/`:643` 两处分支要显式处置 —— **只有 R2-16 碰，但改的是上架主链**，须跑全量 listing 测试 |

### 4. 三条线的验收都要真机，而部署机是单点

R2-13 的 `live` 层要花真钱、R2-14 14b 要删真用户、R2-16 要连真店铺后台——**三条同时排队占
部署机会互相堵**。建议按「可不花钱证伪的先在 CI/审查侧走完，真机验证串行排队」组织，
且**每条线合并后各自补一次上线部署回执**（见上文 R2-14 那条流程缺陷）。

### 5. 合并次序建议

无强依赖，故按**改动主链的深浅**排：**14b（最独立）→ R2-13（新域，几乎不碰既有表）→
R2-16（要动 `ck_listing_mode` 与上架主链分支，放最后，前面两条已稳）**。
若 R2-16 先合，另两条 rebase 时要连带复验上架链——代价不对称。

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
| **2.6** | **R2-15 SKU 内外分离 + 序号扩容**（D-Q72） | **MVP 内** | 改动小但影响此后每一个新上架；且 `lpad` 截断在 1000 万号会**撞号致产品入库停摆**，属定时炸弹，宜早不宜迟 |
| **2.7** | ~~**R2-14 的 14a + 14c**（产品删除+墓碑、列表折叠）~~ | ✅ **已合并**（PR #46，2026-07-30） | 全仓第一个 DELETE 端点 + 迁移 0041 墓碑。**14b（主体删除，验收③）未做**，R2-14 整单未关 |
| **2.8** | **R2-16 存量接管**（后台在线产品补挂） | **待 Owner 定是否进 MVP** | 拉取已全量（A152 实测 45 条 `missing_local`）但只发现不入库、无表无页面。不做则新系统只能管新上架的品，**存量在线 SKU 整体在 ERP 之外** |
| 3 | R2-08 财务域 | 大 | 图纸就绪（幂等键 2026-07-26 已修正为渠道自然键）。**同币种分支**：美亚采购 USD 收支同币，`fx_rate=1` 不折算（§07 修订） |
| 4 | RS-06 人工复核工作台 | MVP 必做 | needs_review 无承接界面则单子堆积无人处理 |
| 5 | RS-08 LLM 预算闸 | MVP 必做 | 日 20 万审核量的成本硬护栏（现仅告警版） |
| 并行 | R2-12 收尾（验收①三日连测）、R2-04 收尾（beat A152 实测）、RS-02b（备份加密+校验+月度恢复演练） | 收尾 | 不占主路径 |

**同期挂账小单**：`FX-0729` 代理管理页零写操作（只读，换代理只能直改库）；
`RS-12` AI 只读接入协议（最小只读角色 + 调用主体可辨识 + 独立限流——Owner 2026-07-29
「中途我就可能让 AI 来看数据，要预留好接口和协议」，能力面已具备、缺三样配套）。
**R2-12 余项（撤回收账后）**：TRO 供给链——存量基线 11,893 行导入 + 定期采集入库
（beat 无任何 TRO 任务，形态照抄已跑通的 USPTO 链：部署机采集 → 标准导入接口 →
新鲜度告警）+ tro-scraper-matrix 产物投递承接。

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
