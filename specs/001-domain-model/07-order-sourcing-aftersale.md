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
| status | TEXT | NOT NULL DEFAULT 'unassigned' CHECK IN (unassigned, assigned, claimed, purchased, shipped, backfilled, exception, cancelled) | |
| assignee_kind | TEXT | NOT NULL DEFAULT 'none' CHECK IN (none, internal, external) | none=不分配，运营代填路径（D-Q50②） |
| purchaser_id | BIGINT | NULL REFERENCES purchaser | assigned 起必填（internal/external 均指 purchaser 行） |
| assigned_by / assigned_at | | NULL | |
| claimed_at | timestamptz | NULL | 领单（门户或内部界面） |
| purchase_platform | TEXT | NULL | 1688/拼多多/其他 |
| purchase_order_ref | TEXT | NULL | 采购平台单号 |
| purchase_cost | NUMERIC(12,2) | NULL | 原币成本 |
| purchase_currency | CHAR(3) | NOT NULL DEFAULT 'CNY' | |
| exchange_rate_locked | NUMERIC(12,6) | NULL | 领单/回填时从 purchaser.exchange_rate 锁定快照（D-Q32；汇率后改不影响已锁单） |
| freight_cost | NUMERIC(12,2) | NULL | |
| carrier / tracking_no | TEXT | NULL | 物流回填（现阶段手动，D-Q28） |
| purchased_at / shipped_at / backfilled_at | timestamptz | NULL | |
| backfill_actor_kind | TEXT | NULL CHECK IN (internal, external, op_direct) | **op_direct=运营在订单页直接代填**（D-Q50②） |
| backfill_actor_id | BIGINT | NULL | app_user.id 或 portal_account.id（按 kind 解读） |
| exception_reason | TEXT | NULL | 缺货/涨价/无法发货… |
| note | TEXT | NULL | |
| +公共列 | | | |

索引：`(team_id, status)`、`(purchaser_id, status)`（门户「我的单」主查询）、`(order_id)`。
双入口权限矩阵：

| 入口 | 主体 | 认证 | 可见范围 | 可写 |
|---|---|---|---|---|
| 内部界面 | 含 `procurement.execute` 权限的成员 | 内部 JWT | 本团队全部执行单 | 领单/处理/回填全字段 |
| 订单页代填 | 任意订单员 | 内部 JWT | 本团队订单 | 创建 assignee_kind=none 单并直接回填（backfill_actor_kind=op_direct） |
| 采购方门户 | portal_account | portal JWT | **仅 purchaser_id=自己 且 status IN (assigned, claimed, purchased, shipped)** | claim + 回填受限列 |

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
