# 07 sourcing + order + aftersale — 采购方 / 订单 / 四检 / 采购执行单 / 发货 / 退货 / 退款

> 决策依据：D-Q27（采购方账号+订单从渠道拉取）、D-Q28（物流单号手动，未来第三方 API）、D-Q29（取消/退款 记录→审批→自动 三档）、D-Q32（采购方汇率即成本）、D-Q50（采购执行双入口+外部物理隔离）、D-Q18（订单售后永久保留）。
> 拉单节奏：15 分钟（automation schedule）；四检=钓鱼/采购方/限价/一致性，软标记（D-Q14 同款开关模式）。

## purchaser 采购方

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| name | TEXT | NOT NULL | |
| purchaser_kind | TEXT | NOT NULL CHECK IN (internal, external) | internal=运营兼采购（D-Q50①）；external=门户账号（D-Q50③） |
| user_id | BIGINT | NULL REFERENCES app_user | internal 必填（1:1 到内部成员）；external 为 NULL |
| contact | JSONB | NOT NULL DEFAULT '{}' | 电话/微信…（门户视图不可见他人 contact） |
| exchange_rate | NUMERIC(12,6) | NOT NULL | **该采购方的结算汇率 = 成本汇率**（D-Q32） |
| settle_currency | CHAR(3) | NOT NULL DEFAULT 'CNY' | |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN (active, disabled) | |
| +公共列 | | | |

约束：`uq_purchaser_user (user_id) WHERE user_id IS NOT NULL`；external 的登录凭证在 portal_account（01 号文档，1:1）。
基线导入：现有采购方 ~200（调研 answers）。

## channel_order 渠道订单（月分区 by order_date，永久保留 D-Q18）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | identity | PK=(id, order_date) |
| team_id / store_id | BIGINT | NOT NULL | |
| channel_order_no | TEXT | NOT NULL | 渠道订单号（Walmart purchaseOrderId） |
| order_date | timestamptz | NOT NULL | 渠道下单时间，分区键 |
| channel_status | TEXT | NOT NULL | 渠道原生状态（Created/Acknowledged/Shipped/Delivered/Cancelled…原样存） |
| internal_status | TEXT | NOT NULL DEFAULT 'pulled' CHECK IN (pulled, checked, assigned, purchasing, shipped, completed, cancelled, refunded) | 内部推进状态 |
| customer | JSONB | NOT NULL DEFAULT '{}' | 履约最小集（name）；PII 最小化（00 §10） |
| ship_to | JSONB | NOT NULL | 收件地址（四检 phishing 匹配对象） |
| order_total | NUMERIC(12,2) | NOT NULL | + currency CHAR(3) DEFAULT 'USD' |
| item_count | INT | NOT NULL | |
| has_flag | BOOLEAN | NOT NULL DEFAULT false | 四检任一 flagged 的快捷标记 |
| pulled_at | timestamptz | NOT NULL | |
| raw_ref | TEXT | NULL | 渠道原始 JSON 落盘引用 |
| created_at / updated_at | | | |

约束：`uq_channel_order (store_id, channel_order_no, order_date)`（分区唯一须含分区键；同单重拉 upsert）。
索引：`(team_id, internal_status, order_date DESC)`、`(store_id, order_date DESC)`、`(has_flag, order_date DESC) WHERE has_flag`。
拉单协议：15min/店（schedule）；upsert 幂等；channel_status 变化驱动 internal_status 推进（服务层状态机；渠道 Cancelled 强制覆盖内部状态并出 notification）。
已落地（R2-05 beat `order_pull`）：增量主键 lastModifiedStartDate（窗口 = max(last_sync−1h, now−30d)，恒传 createdStartDate=now−179d——BR-ORD-002）；high-water mark 在 sync_state(scope='order_pull', ref_id=store_id)，整店成功才推进；订单级 channel_status = 各行聚合（最落后未取消行；全取消=Cancelled）；行状态取 orderLineStatuses[-1]（实战语义）。

## order_line 订单行（月分区，随单）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | identity | PK=(id, order_date) |
| order_id | BIGINT | NOT NULL | 同分区月逻辑关联 |
| order_date | timestamptz | NOT NULL | 分区键（与主单一致） |
| team_id | BIGINT | NOT NULL | 冗余 |
| channel_line_no | TEXT | NOT NULL | |
| channel_sku | TEXT | NOT NULL | → sku_mapping 回连 product |
| listing_id / product_id | BIGINT | NULL | 解析回填（legacy SKU 可能无 listing） |
| qty | INT | NOT NULL | |
| unit_price | NUMERIC(12,2) | NOT NULL | |
| line_status | TEXT | NOT NULL CHECK IN (created, acknowledged, shipped, cancelled, refunded) | |
| carrier / tracking_no | TEXT | NULL | 行级回传信息 |
| shipped_at | timestamptz | NULL | |
| created_at / updated_at | | | |

约束：`uq_order_line (order_id, channel_line_no, order_date)`；索引 `(channel_sku)`、`(order_id)`。

## order_check 订单四检（月分区，软标记）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | identity | PK=(id, order_date) |
| team_id | BIGINT | NOT NULL | |
| order_id | BIGINT | NOT NULL | |
| order_date | timestamptz | NOT NULL | 分区键 |
| check_kind | TEXT | NOT NULL CHECK IN (phishing, purchaser, price_limit, consistency) | 钓鱼地址 / 采购方合理性 / 限价 / 一致性 |
| result | TEXT | NOT NULL CHECK IN (pass, flagged) | |
| detail | JSONB | NOT NULL DEFAULT '{}' | 命中证据（phishing 命中行 id / 限价差额…） |
| resolved_by / resolved_at | | NULL | 人工放行 |
| checked_at | timestamptz | NOT NULL DEFAULT now() | |

约束：`uq_order_check (order_id, check_kind, order_date)`（重检 upsert）。
拦截开关：automation_policy flow=order_block —— off=纯软标记（默认），on=flagged 单冻结在 checked 不进分配（D-Q14/29 同款三档思路）。
已落地（R2-05）：mode manual=软标记；semi/auto=建执行单/分配 409 冻结。四检参数键 `order.checks`（team>system>默认 {margin_factor:0.85, usd_rmb_rate:6.8, consistency_ratio:0.9}，D-Q11/C10）；phishing 数据源 blacklist_address/blacklist_zip（BR-ORD-005 口径归一化）；purchaser 检降档=存在 active 采购方（候选区间/配送方式档案列未入本表设计，扩列待决——BR-ORD-007 注记）。

## procurement_order 采购执行单（双入口核心表，D-Q50）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | 门户对外单号=id（零信息泄露） |
| team_id / store_id | BIGINT | NOT NULL | |
| order_id | BIGINT | NOT NULL | + order_date timestamptz（回连分区单） |
| status | TEXT | NOT NULL DEFAULT 'unassigned' CHECK IN (unassigned, **pending_review**, assigned, claimed, purchased, shipped, backfilled, exception, cancelled) | `pending_review`＝**护栏拦下的专用停留位**（2026-07-30 补，原约束漏列致图纸自相矛盾，13c 实现必撞——开发侧批注核实采纳）。**不是初始态**：DEFAULT 仍为 `unassigned`，以免扰动已验收在跑的人工采购路径（R2-05）；仅当派发前护栏评估不通过时由 `unassigned` 转入，人工处置（改地址/换 ASIN/调阈值/显式放行）后转回 `unassigned` 重新评估 |
| assignee_kind | TEXT | NOT NULL DEFAULT 'none' CHECK IN (none, internal, external) | none=不分配，运营代填路径（D-Q50②） |
| purchaser_id | BIGINT | NULL REFERENCES purchaser | assigned 起必填（internal/external 均指 purchaser 行） |
| assigned_by / assigned_at | | NULL | |
| claimed_at | timestamptz | NULL | 领单（门户或内部界面） |
| purchase_platform | TEXT | NULL | 1688/拼多多/其他 |
| purchase_order_ref | TEXT | NULL | 采购平台单号 |
| purchase_cost | NUMERIC(12,2) | NULL | **税前商品额**（对应插件回填 `totalBeforeTax`）——口径见下方「成本三分」 |
| tax_amount | NUMERIC(12,2) | NULL | **销售税**（插件回填 `tax`）。**2026-07-30 补**：美亚采购必有销售税，原模型缺此列 → 利润核算从第一天就会偏低 |
| purchase_currency | CHAR(3) | NOT NULL DEFAULT 'CNY' | 美亚采购为 USD（同币种分支见上节） |
| exchange_rate_locked | NUMERIC(12,6) | NULL | 领单/回填时从 purchaser.exchange_rate 锁定快照（D-Q32；汇率后改不影响已锁单） |
| freight_cost | NUMERIC(12,2) | NULL | 运费（插件回填 `shipping`） |
| payment_card_last4 | TEXT | NULL | **付款卡后四位**（插件回填 `creditCardNumber`）——资金来源可追溯，财务对账用 |
| delivery_est_date | DATE | NULL | 预计送达（插件回填 `deliveryTime`，已格式化） |
| delivery_est_raw | TEXT | NULL | 渠道原文（`origDeliveryTime`，如 `Thursday, August 7`）——解析出错时可回溯，**存原文比重新抓便宜** |
| carrier / tracking_no | TEXT | NULL | 物流回填（现阶段手动，D-Q28；插件路径由 R2-13 自动回填） |
| purchased_at / shipped_at / backfilled_at | timestamptz | NULL | |
| backfill_actor_kind | TEXT | NULL CHECK IN (internal, external, op_direct) | **op_direct=运营在订单页直接代填**（D-Q50②） |
| backfill_actor_id | BIGINT | NULL | app_user.id 或 portal_account.id（按 kind 解读） |
| exception_kind | TEXT | NULL CHECK IN (address, stock, product, delivery, checkout, price_guard, channel_cancelled, order_not_found, other) | **异常六类 + 三特例**（2026-07-30 立，源自厂商插件生产实测 17 条 `failContent`，非凭空设计） |
| exception_reason | TEXT | NULL | 异常原文（保留渠道/插件原始措辞，便于运营辨识与后续归类） |
| note | TEXT | NULL | |
| +公共列 | | | |

索引：`(team_id, status)`、`(purchaser_id, status)`（门户「我的单」主查询）、`(order_id)`。
双入口权限矩阵：

| 入口 | 主体 | 认证 | 可见范围 | 可写 |
|---|---|---|---|---|
| 内部界面 | 含 `procurement.execute` 权限的成员 | 内部 JWT | 本团队全部执行单 | 领单/处理/回填全字段 |
| 订单页代填 | 任意订单员 | 内部 JWT | 本团队订单 | 创建 assignee_kind=none 单并直接回填（backfill_actor_kind=op_direct） |
| 采购方门户 | portal_account | portal JWT | **仅 purchaser_id=自己 且 status IN (assigned, claimed, purchased, shipped)** | claim + 回填受限列 |
| **采购插件**（R2-13，2026-07-27 新增） | `plugin_instance`（一浏览器一实例） | **实例专属 token**（非共享密钥，可单独吊销） | **仅 buyer_account_id=本实例绑定账号 且 status IN (assigned, claimed)** 的任务 | 回填 `purchase_order_ref`/`purchase_cost`/`carrier`/`tracking_no`/状态流转 |

### 采购平台与币种口径（2026-07-27 修订）

- `purchase_platform` 明确含 **`amazon`**（美亚搬运是主业务线，插件即为其自动化）；
  原注释"1688/拼多多/其他"系考古期偏向国内货源，**不完整**；
- **同币种分支（D-Q32 补充）**：`purchase_currency = 'USD'` 时（美亚采购）收入与成本
  同币种，`exchange_rate_locked` 记 1.0 且**不做折算**；财务域（§08）过账时按
  `fx_source=settlement_native`、`fx_rate=1` 处理，**不得走采购方锁定汇率路径**
  ——否则 R2-08 会对同币种金额做一次无谓换算并引入舍入误差。

## 采购插件契约与成本口径（R2-13，2026-07-30 依 Owner 双份逆向报告落笔）

> 来源：Owner 2026-07-30 两份实测报告——厂商面板字段级分析（12,963 单实数据、导入模板、
> 状态实测分布）+ 插件源码逐字段追踪（接口契约、17 条异常原文、物流事件结构）。
> **本节所有字段与枚举均有一手出处，非推演。**

### 成本三分（不得合并为单一 total）

`purchase_cost`（税前商品额）· `tax_amount`（销售税）· `freight_cost`（运费）**三项分列存储**，
合计由计算得出、不落库。理由：①渠道回填本就是分开的四项
（`totalBeforeTax`/`tax`/`shipping`/`total`）；②只存 total 则**税额永久丢失**，而财务域
（§08）过账需要区分商品成本与税费；③`total` 可用于回填后自校验
（`purchase_cost + tax_amount + freight_cost == total`，不等即落 `exception_kind=other` 告警）。

⚠️ **金额解析纪律**：渠道回填的金额是**带货币符号的字符串**（`"$10.79"`、`"$0.00"`），
入库前必须解析为 `NUMERIC`。**须处理千分位与空串**（`"$1,234.56"`、`""`、`null`），
解析失败**不得静默写 0**——落异常并告警。这是最容易埋隐蔽账目错误的一处。

### 异常分类（`exception_kind`，源自 17 条生产实测原文）

| 类 | 覆盖的渠道原文 | 典型处置 |
|---|---|---|
| `address` | 地址保存超时／地址列表加载超时／地址信息不完整缺少区（JP）／未匹配到洲信息 | 修正地址后重投；反复出现疑似账号触发验证码 |
| `stock` | 商品无库存／商品库存不足或已售罄／未找到加入购物车按钮／加入购物车失败页面未跳转 | 换 ASIN／转人工／取消 |
| `product` | 配送方式非 FBA／**属于捆绑商品(bundle)请手动拍单**／无法修改商品购买数量 | **非 FBA 与 bundle 属业务规则不是故障**——应在派发前拦截，见下方护栏 |
| `delivery` | 预计送达时间超过 N 天／预计送达时间解析失败 | 调阈值或取消 |
| `checkout` | 商品验证失败／下单验证超时／回传订单失败请手动回填 | 重投；回传失败需人工补 AMZ 单号 |
| `price_guard` | （渠道侧不写原文，代码层直接取消） | 见下方价格护栏 |
| `channel_cancelled` | 渠道侧订单被取消/退款 | 对应厂商 `updateAmzOrderStatus(91)` |
| `order_not_found` | 渠道侧查不到该订单 | 对应厂商 `updateAmzOrderStatus(92)` |
| `other` | 通用处理失败／金额自校验不平 | 人工查看原文 |

> **归属澄清**：厂商的 `91/92` 在其面板中落在**物流状态**枚举（实测 `91=已取消` 10 条），
> 而插件函数名为 `updateAmzOrderStatus`。我方**不复制其双枚举结构**——统一收进
> `exception_kind` 的两个细分，物流状态另走 §07 既有 `shipment`/行级字段。

### 护栏与卡点（三档 `purchase_execute` 的实际停驻位）

**护栏在任务放行前评估，不合格的单停在 `status='pending_review'`，不得放给插件执行。**
（该设计取自厂商「待审核」态的位置——他们自己形同虚设〔实测 0 条〕，但**位置对**：
校验失败的单应当卡在派发前，而不是让插件拍完才发现。）

| 护栏 | 判据 | 出处 |
|---|---|---|
| `amount_ceiling` | 单单金额上限 | 我方新增 |
| `daily_cap` | 单账号日采购上限 | **唯一落点 = `buyer_account.daily_cap`**（2026-07-30 简化，Owner 裁定）。日采购上限是**账号物理属性**（该亚马逊账号一天下多少单不致触发风控），不是业务策略，故 **flow config 不再设同名键**；团队级「今天要不要采」由三档档位承担——人工档即不自动派发，比再加一个数字上限直接。NULL=不限 |
| `price_delta_pct` | 实付较预估涨幅超阈值 | 厂商硬编码 50% 于插件、**面板 0 处引用**（`priceCheck` 字段前端未使用）——我方阈值下发可配 |
| `delivery_days_limit` | 预计送达超 N 天（厂商默认 7） | 厂商实测有此规则，我方原设计缺失 |
| `fba_only` / `no_bundle` | 非 FBA、捆绑商品**不可自动采购** | 厂商作为运行时错误暴露；**我方应前置拦截**（业务规则非故障） |

**价格护栏三段式（判定必须在客户端，因为只有结账页知道实付）**：
服务端下发阈值（不硬编码）→ 客户端判定并拒绝下单 → **回填后服务端二次校验**，
超限落 `exception_kind=price_guard` 并告警。双保险，防客户端被绕过或版本落后。

**回填后核对（不可前置化）**：`asinCheck` / `postCodeCheck` 语义是「拍单**后**实际值与
下单时是否一致」，属**事后核对**，服务端在回填时执行——买错东西必须能被发现。

### 执行档位（Owner 2026-07-30，插件 fork 必备能力）

现插件的流程是**一路走到下单完成**，没有中途停下的档。Owner 定的验收执行方式是
「前面停在最后一步付款」，故 fork 必须补一个**走到付款页即停并回报**的档位，否则该执行方式
无法落地。沿用本仓 `channel.gateway_mode` 同款三态：

| 档 | 行为 | 用途 |
|---|---|---|
| `dry_run` | 只做校验与地址/购物车填充，**不进结账页** | CI 与本地 |
| `stop_before_payment` | 走完结账页、抓到实付金额与预计送达，**在点付款前停下并回报** | **不花钱验收的主力档** |
| `live` | 完整下单 | 真实订单收口 |

`stop_before_payment` 之所以是主力档，是因为它能验到几件原以为必须花钱才能验的东西：
**实付金额**（价格护栏三段式的客户端判定所需）、**预计送达**（`delivery_days_limit`）、
**非 FBA / bundle 的前置拦截**。工单验收据此分两层，见 007 R2-13 节。

### 端点契约（字段级，源自插件源码实测）

> **一手来源的权威边界（2026-07-30 精确化）**：Owner 的逆向报告对「**厂商实际怎么做**」
> 是权威——字段名、调用形态、页面行为以它为准；但对「**我们该怎么做**」的**推荐不是权威**。
> 已出现两处推荐失准：①「提取 customerId 按钮不需要」（实际是账号 ID 的唯一来源）；
> ②「`asinCheck` 前置化」（照做会直接撞回填后核对那条判据）。引用 §7 的推荐前先过这道筛。

**① 拉取待采购任务**（对应厂商 `getNeedPurchaseOrders?customerId=`）
插件实际只读取以下字段，其余仅供展示：`id`／`orderNo`／`receivingName`／`receivingPhone`／
`receivingAddress`／`receivingCity`／`receivingDistrict`(JP 必填，缺则中止)／`receivingPostCode`／
`receivingCountry`(驱动地址填写分支)／**`state` 与 `receivingState` 两个字段名并存**
（插件取 `order.state || order.receivingState`，**对外须两个都返回同值**）／
`products[]`（**只用 `asin` 与 `quantity`**）。

**② 采购完成回填**（对应 `purchaseOrderFinishUpdate`）：`id`／`platformOrderNo`（渠道单号）／
`asins`（逗号分隔）／`deliveryTime`+`origDeliveryTime`／`creditCardNumber`／
`mainPostCode`+`extPostCode`（美国 zip+4 扩展位）／`shipping`+`totalBeforeTax`+`tax`+`total`／
`products[]`（`unitPrice`／`totalPrice`／`productImage`）。

## procurement_logistics_event 采购物流事件（R2-13，2026-07-30）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| procurement_order_id | BIGINT | NOT NULL REFERENCES procurement_order | |
| team_id | BIGINT | NOT NULL | 冗余 |
| occurred_at | timestamptz | NULL | 由渠道原文 `day`+`time` 解析；**解析失败不丢事件**，原文仍入库 |
| raw_day / raw_time | TEXT | NULL | 渠道原文（英文，如 `July 28, 2026` / `8:42 AM`） |
| description | TEXT | NOT NULL | 事件描述（`tracking_info`） |
| city / state_code | TEXT | NULL | 由位置串解析 |
| seq | INT | NOT NULL | 渠道数组下标，**0 = 最新**（渠道倒序，勿按 seq 升序当时间序） |
| created_at | | | |

约束：`uq_proc_logistics_event (procurement_order_id, seq)`。
承运商／运单号／预计送达存 `procurement_order` 主表，**不重复存本表**。

> **明确不存 `trackingHtml`**（渠道回传的 base64 整页 HTML）：其信息已由本表结构化承载，
> 单条数十至数百 KB、万单即 GB 级，**存储代价与价值不成比例**；确需回溯时重新抓取。
> 此条为**明确的不做决定**，防实现侧照抄厂商字段。

## buyer_account 亚马逊买家账号池（R2-13，2026-07-27）

> 现状（Owner 2026-07-27）：数十个买家账号，**各自登录在一个独立指纹浏览器内**；
> 代理与指纹由外部浏览器管理，**ERP 不管代理**（区别于店铺侧 proxy 绑定）。

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| label | TEXT | NOT NULL | 运营可读名（对应哪个指纹浏览器） |
| site | TEXT | NOT NULL CHECK IN (amazon_com, amazon_ca, amazon_co_jp) | 插件支持三站 |
| external_customer_id | TEXT | NOT NULL | **插件侧 `customerId`**，任务路由键。⚠️ **来源＝插件运行时从亚马逊页面现场提取并随请求带上，由 ERP 首见自动登记**（见下方「身份从哪来」）——**不是人工预录入**。2026-07-30 早先版本写「提取按钮取出后人工录入、属一次性配置工具」，**已作废**：那会造成"装插件前先要知道 ID、而 ID 只能装了插件才看得到"的死循环 |
| status | TEXT | NOT NULL DEFAULT 'pending_claim' CHECK IN (pending_claim, active, paused, blocked, retired) | **`pending_claim`＝ERP 首见该 `customerId` 时自动落的待认领行**，运营补 `label`/`site`/`daily_cap` 后转 `active`；**未认领不派单**。blocked=账号异常/风控 |
| daily_cap | INT | NULL | 单日采购上限（**该账号的物理承受力，唯一落点**；null=不限）。**flow config 无同名键**，勿再引入第二处定义 |
| last_seen_at | timestamptz | NULL | 该账号插件实例最近一次拉任务时间（掉线可见） |
| note / +公共列 | | | |

约束：`uq_buyer_account (team_id, external_customer_id)`、`uq_buyer_account_label (team_id, label)`。
`procurement_order` 增列 `buyer_account_id BIGINT NULL REFERENCES buyer_account`
（插件路径必填；人工/门户路径可空），索引 `(buyer_account_id, status)`。

**任务路由**：采购任务按 `site` 与账号可用性（active + 未超 `buyer_account.daily_cap`）分配到具体
buyer_account；**同一订单的任务只能派给一个账号**（防重复下单）。分配策略 v1 = 轮转 +
容量约束；策略参数进配置中心，不写死。

## plugin_instance 插件实例（R2-13）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | **实例绑定的是「一台授权浏览器」，不是一个买家账号** |
| token_hash | TEXT | NOT NULL | **实例专属令牌哈希**（明文只在签发时出现一次） |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN (active, revoked) | 单实例可独立吊销 |
| version | TEXT | NULL | 插件版本（便于灰度与排障） |
| last_seen_customer_id | TEXT | NULL | **观察列，不参与鉴权**：该浏览器最近一次带上来的 `customerId`。排障用（"这台机器现在登的是哪个号"），换号即自然更新 |
| last_seen_at / created_by / created_at / revoked_at | | | |

**不得使用全局共享密钥**——一台机器被盗即全量失守；实例级令牌让吊销粒度=单个浏览器。

### 身份从哪来 —— **两段式，令牌管浏览器、`customerId` 管账号（2026-07-30 更正）**

> ⚠️ **本节推翻此前的「一实例绑定一买家账号 + `external_customer_id` 人工预录入」。**
> Owner 2026-07-30 质疑：「**插件为什么做成每个买家号专用的插件了？为什么提前填写
> customerID，我插件没安装拿不到这个 ID。**」——两条都成立，第二条指出的是死循环。

**插件源码实测（`js/popup.js`，逐字核对）**：

```js
function extractCustomerId(){
  const pageContent = document.documentElement.innerHTML;      // ← 读亚马逊页面本身
  const match1 = pageContent.match(/customerId:\s*"([^"]*)"/); // ← 现场正则
  ...
}
```

三条硬事实：

1. **`customerId` 是从亚马逊页面 HTML 现场抓的**，即"这个浏览器当前登录的是哪个亚马逊账号"。
   三个入口（`handleStartSync` / `handleStartSyncTracking` / `handleStartPurchase`）
   **每次点击都重抓一次**，`localStorage` 里**从不存**它。
2. **插件零 per-install 配置**：全仓 `localStorage` 只有 `sb2-delivery-days-limit` 一个键；
   `baseUrl` 是 `CONFIG` 里的构建期常量。**同一个 build 装在几十台浏览器上，装完即用。**
3. **「提取 customerId」按钮是纯展示**：`handleExtractCustomerId()` 只把值写进页面上一个
   `<div>` 的 `textContent`，**不存、不发**。它的用途是让人肉眼看见"这台机器登的是哪个号"
   ——正对上 Owner 说的「我一开始并不知道我的 ID 在哪里看」。

**故厂商的插件根本不是"每个买家号专用"，我方也不该做成那样。** 正确的分层：

| 层 | 由什么承载 | 回答什么问题 |
|---|---|---|
| **授权** | `plugin_instance` 令牌 | 这是不是我方团队 T 的一台授权浏览器？ |
| **身份** | 请求带上的 `customerId`（页面提取） | 这台浏览器**此刻**登的是哪个买家号？ |

服务端在**团队 T 的范围内**把 `customerId` 解析成 `buyer_account`。于是：

- **装插件不需要先知道 `customerId`**——令牌与账号无关，死循环消失；
- **一台浏览器换登另一个买家号，不用换令牌**，路由自然跟随；
- **越权边界仍在**：跨团队的 `customerId` 解析不到，直接拒。

**首见自动登记**：服务端遇到本团队没见过的 `customerId` → 落一条
`buyer_account(status='pending_claim', external_customer_id=<新值>)` 并通知，
运营在页面上补 `label` / `site` / `daily_cap` 后转 `active`。
**`pending_claim` 一律不派单**——既不阻断发现，也不会把任务派给一个还没设过限额的号。

> **这个方向比我原设计更安全，不只是更省事**：`customerId` 取自**活着的亚马逊登录态**，
> 所以任务天然只会流向"确实登着该账号"的浏览器。而预绑定方案在有人改了某台浏览器的
> 亚马逊登录后，会**继续按旧绑定派任务，用错号把单买了**——**厂商的做法自我纠正，
> 我的原设计会静默买错。**
>
> 团队内一台浏览器"冒充"同团队另一个 `customerId` 不在威胁模型内：那是自家机器、自家运营，
> 且它只能用**实际登录的那个**亚马逊账号付款，冒充只会让自己拿到买不了的单——是操作事故
> 不是越权。真正的重复下单风险由「同一订单只派一个账号」+ `claimed` 原子锁挡住。

## buyer_session 买家会话凭证 —— ⛔ **不建（Owner 2026-07-30 裁定：先不收 cookie）**

> **本节保留为判据，不是待建表。** 2026-07-27 曾按「默认不启用、启用条件二选一」写，
> 2026-07-30 Owner 裁定直接**不收**，理由不是"暂时不用"而是**用途已被证伪**。

**证伪过程（逐字读插件 `background.js` 的字段整形）**：

```js
{ domain, expirationDate, hostOnly, httpOnly, name, path,
  sameSite: cookie.sameSite || 'no_restriction',
  secure, session, storeId: cookie.storeId || '1', value, id: index + 1 }
```

`hostOnly` / `sameSite:'no_restriction'` / `storeId:'1'` / `id:index+1` 这一组字段，**正是
Cookie-Editor（EditThisCookie）那类插件的 JSON 导出格式**，而该格式存在的唯一目的是
**被导入回浏览器**。所以那份 jar 不是"一个标识"，是**整套可搬运的登录态**，用途是会话搬运
而非识别——识别由同一调用里的**独立参数** `customerId` 承载
（`ApiService.updateBuyerCookie(customerId, country, cookieJson)`）。
**目的既已另有达成路径，收 cookie 就只剩风险没有收益**：jar 一旦落库，持有者即可以买家身份下单。

**处置（随 13a 执行）**：`cookies` 权限、`updateBuyerCookie`、`getCookiesAsJson()`、
`background.js` 的 `onMessage` 监听器**全部删除**，fork 后 `permissions` 为空数组
（`chrome.runtime` 无需声明）。连带作废此前那条"给 `onMessage` 加 `sender` 校验"——
**整个监听器都删了，校验失去对象；删掉比"加校验 + 收窄 host_permissions"更强**。

**将来若真要建，判据仍是原来那两条**（任一成立）：需服务端在浏览器关闭时查订单状态 /
需集中监测会话健康。届时纪律照旧：加密存储（应用层密钥，密钥不入库）、**永不入日志、
永不进 API 响应体、永不进 dry_run 快照**（沿 R2-03 `request_snapshot` 的凭证断言同款）、
`erp_app` 无明文读取路径、每次解密访问写 `audit_log`。

### portal_procurement_v 门户视图（portal_app 角色唯一入口）

```sql
CREATE VIEW portal_procurement_v WITH (security_barrier) AS
SELECT p.id, p.status, p.claimed_at, p.purchase_platform, p.purchase_order_ref,
       p.purchase_cost, p.purchase_currency, p.freight_cost,
       p.carrier, p.tracking_no, p.exception_reason, p.note,
       o.ship_to, o.item_count,                -- 履约必需
       l.channel_sku, l.qty, l.unit_price      -- 行摘要（JOIN 聚合）
FROM procurement_order p JOIN … 
WHERE p.assignee_kind = 'external'
  AND p.purchaser_id = current_setting('app.portal_purchaser')::bigint
  AND p.status IN ('assigned','claimed','purchased','shipped');
```
- 不暴露：team/store 标识、customer 姓名、内部备注、其他采购方任何信息。
- 门户写操作走 API（服务层校验后 UPDATE 受限列：claim、purchase_*、carrier、tracking_no、exception_reason）；portal_app 无基表权限。

## shipment 发货回传

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id / store_id | BIGINT | NOT NULL | |
| order_id | BIGINT | NOT NULL | + order_date |
| procurement_order_id | BIGINT | NULL REFERENCES procurement_order | 代填路径可无执行单？——不：代填也建单（op_direct），本列 NOT NULL 化留 R1 定夺；先 NULL 容错 |
| lines | JSONB | NOT NULL | 回传行 [{line_no, qty}] |
| carrier / tracking_no | TEXT | NOT NULL | |
| push_status | TEXT | NOT NULL DEFAULT 'pending' CHECK IN (pending, pushed, failed) | ship 回传 Walmart（渠道网关） |
| pushed_at | timestamptz | NULL | |
| channel_response | JSONB | NULL | |
| +公共列 | | | |

索引：`(push_status) WHERE push_status='pending'`、`(order_id)`。
推送失败处置：失败保留 pending 重试（网关退避）；连续失败 → notification + 订单页人工重推按钮。
已落地（R2-05）：push 走 channel outbox（action=order_ship；Created 单自动前置 order_ack，同店 FIFO 保序）；明确拒=failed+notification，人工重推=换 Idempotency-Key 再发（新回传单新命令）；结果未知=verify_pending → beat `ship_recon` 以渠道订单实况对账（绝不重发，BR-GW-005）。发货请求 methodCode 走 system_config `order.ship`。

## channel_return 渠道退货（量小不分区，永久保留）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id / store_id | BIGINT | NOT NULL | |
| channel_return_no | TEXT | NOT NULL | |
| order_id | BIGINT | NULL | + order_date（回连） |
| return_date | timestamptz | NOT NULL | |
| reason | TEXT | NULL | 渠道退货原因 |
| channel_status | TEXT | NOT NULL | 原样存 |
| internal_status | TEXT | NOT NULL DEFAULT 'pulled' CHECK IN (pulled, reviewed, closed) | |
| qty | INT | NOT NULL | |
| refund_amount | NUMERIC(12,2) | NULL | + currency |
| pulled_at | timestamptz | NOT NULL | |
| raw_ref | TEXT | NULL | |
| +公共列 | | | |

约束：`uq_channel_return (store_id, channel_return_no)`。

已落地（R2-07 增量1，0030）：本表冻结列原样落库，另按 C6/BR-AS-003/004/006 行级语义补齐——
① 扩展列 customer_order_no / return_by_date / refund_mode / customer(jsonb, PII 最小化 name+email)；
channel_status 存行状态聚合（全行一致取其值，分歧=MIXED，行级才是权威）；order 回连按行
purchaseOrderId 反查 (store_id, channel_order_no)。
② 新增 `channel_return_line` 行表（uq (return_id, line_no)，line_no=销售行号；status/refundStatus/
deliveryStatus 三态行级原样存 + item/qty/refundedQty/单价/退货原因/承运商）。
③ 新增 `channel_return_event` 变更历史（行级状态字段 diff jsonb + observed_at——旧系统覆盖式
写入丢历史是 BR-AS-006 已知缺陷，upsert + 留痕是 C6 已决改进）。
拉取协议：无时间过滤全量 limit=200 翻页（BR-AS-001），nextCursor 完整 URL parse_qs 回参；
beat `return_pull` 08:00/日（BR-SCH-002）；见单量骤降 >50% 告警不重跑（BR-AS-007）。

## refund_request 退款/取消申请（三档，D-Q29）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id / store_id | BIGINT | NOT NULL | |
| order_id | BIGINT | NOT NULL | + order_date |
| kind | TEXT | NOT NULL CHECK IN (cancel, refund) | |
| lines | JSONB | NOT NULL DEFAULT '[]' | 部分退款行 [{line_no, qty, amount}] |
| amount | NUMERIC(12,2) | NOT NULL | + currency CHAR(3) DEFAULT 'USD' |
| reason_code | TEXT | NOT NULL | sys_dict(refund_reason)（运营可维护） |
| reason_text | TEXT | NULL | |
| mode_applied | TEXT | NOT NULL CHECK IN (record, approval, auto) | 创建时从 automation_policy(flow=refund / flow=cancel) 快照 |
| status | TEXT | NOT NULL DEFAULT 'recorded' CHECK IN (recorded, pending_approval, approved, rejected, executing, executed, failed) | record 档终态=recorded（只记账不执行）；auto 档直进 executing |
| requested_by_kind / requested_by | TEXT/BIGINT | NOT NULL | user/system |
| approved_by / decided_at | BIGINT/timestamptz | NULL | approval 档必填（refund.approve 权限点） |
| executed_at | timestamptz | NULL | 渠道退款执行完成 |
| channel_ref | TEXT | NULL | 渠道退款凭据 |
| +公共列 | | | |

索引：`(team_id, status)`、`(order_id)`。
执行纪律：executing→executed 必须拿到渠道确认（verify-back 同款：无响应先查后重试）；渠道写路径灰度期只允许 is_test 店真实执行。

已落地（R2-07 增量2，0031）：本表图纸原样落库；record/approval 两档本地闭环开通
（POST /refund-requests 幂等创建 + approve/reject，权限点 refund.request / refund.approve，
reason_code 走 sys_dict(refund_reason) 字典校验）。**auto 档在 R2-09 flow=refund 接线前
fail-closed 拒绝创建**（REFUND_AUTO_NOT_WIRED，不做静默降档）；approved 为驻留态，
渠道执行（executing→executed，outbox return_refund + verify-back）随 R2-09。
