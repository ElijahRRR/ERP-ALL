# R2-07 售后域考古（2026-07-16）

> 五路侦察：宪法决策 / 冻结契约 / 旧仓生产语义 / 渠道 API / 现有基建模式。
> 首轮尝试 workflow 并行考古两轮均遇 API 529 过载，改主线程内联完成，结论等效。

## 1. 宪法与台账要求

- PRD-v1.md:56 — `aftersale ── 退货 channel_return / RMA / 退款(记录→审批→自动 三档)`；
  PRD-v1.md:145 — R2 面单：`售后 returns + 封店工作流 + 邮箱模块`（本单只取 returns 核，封店/邮箱不进）。
- D-Q29（DECISION-FORM.md:118）：取消/退款现全人工；目标 记录→审批流→自动 三阶段，
  **售后动作也走"三档自动化开关"模型**（同 D-Q13）。
- D-Q18（DECISION-FORM.md:41）：**订单/售后数据永久保留**。
- 台账 §13 AS（business-rules-ledger.md:189-201）全部保真移植：
  - BR-AS-001 Returns API **无时间过滤**（无 since 参数）→ 只能全量拉；limit=200 翻页，
    nextCursor 是完整 URL 需 parse_qs 解析回参数。
  - BR-AS-002 节流 1.3s/页 ≈46/min（官方 50/min 留余量）；并发按店（每店独立 token 桶）。
  - BR-AS-003 **行级展开**：一行 = returnOrderLine（同 RMA 多 SKU 拆多行）；按 returnOrderDate 倒序。
  - BR-AS-004 status/refundStatus/deliveryStatus 均**行级**；refundMode ∈
    COURTESY / REFUND_TO_PAYMENT_METHOD / MERCHANT_REFUND。
  - BR-AS-005 `replacementInfo=true` 预留换货语义。
  - BR-AS-006 + C6（ledger:315）已决：**upsert by (RMA, line) + 保留历史快照**（旧系统覆盖式写入是已知缺陷）。
  - BR-AS-007 运维基线 ≈3800 行；骤降 >50% 不要重跑，先排查代理/渠道故障。
  - BR-AS-008 退款 POST **不在自动化范围**（旧系统人工执行）→ 新系统按 D-Q29 三档制升级，
    auto 档灰度期仅 is_test 店（07 契约执行纪律同款）。
- BR-SCH-002（ledger:219）：旧系统售后同步节律 = 每日 08:00。
- BR-GW-005（ledger:19）：POST 默认不重试（防退款重复提交）——退款执行必须幂等 + verify-back。

## 2. 冻结契约（specs/001-domain-model/07-order-sourcing-aftersale.md）

- `channel_return`（07:157-175）：RMA 头表——store_id + channel_return_no 唯一、order_id 回连、
  channel_status 原样存、internal_status ∈ pulled/reviewed/closed、refund_amount+currency、raw_ref。
  量小不分区，永久保留。
- `refund_request`（07:177-200）：kind ∈ cancel/refund、lines JSONB [{line_no,qty,amount}]、
  reason_code 走 sys_dict(refund_reason)、**mode_applied ∈ record/approval/auto（创建时从
  automation_policy(flow=refund / flow=cancel) 快照）**、status ∈ recorded/pending_approval/
  approved/rejected/executing/executed/failed；record 档终态=recorded；auto 档直进 executing；
  approval 档必填 approved_by（refund.approve 权限点）。
  执行纪律：executing→executed 必须拿到渠道确认（verify-back 同款）；**灰度期只允许 is_test 店真实执行**。
- **契约张力（本单需 ar 帽补契约）**：C6/BR-AS-003/004/006 均为行级语义，但冻结 channel_return
  是 RMA 级单表（无行号列）。处置：channel_return 头表保持不动 + 新增 `channel_return_line`
  行表（uq (return_id, line_number)，行级状态三字段落行表）+ 状态变更历史，07 文档加"已落地（R2-07）"注记。
- openapi-v0.yaml 无售后端点（grep return/refund/aftersale 零命中）→ 照 R2-05 /orders 模式
  （openapi-v0.yaml:950-1056）本单补 /returns、/refund-requests 契约。
- automation_policy（0025_order_domain.py:296-311）：`(team_id, flow_code)` 唯一，
  mode ∈ **manual/semi/auto**。映射到 refund_request 词表：manual→record、semi→approval、auto→auto。
  缺省无策略行 = record（fail-closed，最保守档）。

## 3. 旧仓生产语义（/home/user/erpAPI/售后订单同步/，已上线，2026-05-26 版）

- fetch_walmart_returns.py + README.md：57 店并发 8 路 `GET /v3/returns`
  （limit=200, replacementInfo=true），nextCursor 用 urlparse+parse_qs 解析（README:69），
  页间 1.3s 节流；整表覆盖写飞书 27 列（README:122-154 完整字段映射——**这是 L1 对拍的口径基准**）。
- 27 列关键来源字段：returnOrder 级 = returnOrderId/customerOrderId/customerEmailId/
  returnOrderDate（排序键）/returnByDate/refundMode/totalRefundAmount.currencyAmount+Unit；
  returnOrderLine 级 = status/currentRefundStatus/currentDeliveryStatus/returnMethod/statusTime/
  item.sku/item.productName/item.condition/quantity.measurementValue/refundedQty/returnReason/
  returnDescription/salesOrderLineNumber/purchaseOrderId/
  charges[PRODUCT,ItemPrice].chargePerUnit.currencyAmount/
  returnLineGroups[0].labels[].carrierInfoList[0].carrierName+trackingNo。
- 覆盖式写入四风险（README:191-196）= 新系统 upsert+历史的动机（C6 已决）。
- 退款端点旧系统**不调用**（README:181），人工在 Seller Center 操作。

## 4. 渠道 API 面

- 官方 returns 规格（walmart-marketplace-returns-openapi-original.yml）仅 3 操作：
  `getReturns`（GET /v3/returns，:302）、`issueRefund`（POST /v3/returns/{returnOrderId}/refund，:32）、
  `bulkItemOverrideFeed`（退货规则 override feed，:195，本单不碰）。
- 限流（docs/walmart_rate_limits.tsv）：GET /v3/returns **50/min**；
  POST /v3/returns/{returnOrderId}/refund **60/min**；
  另有订单侧 POST /v3/orders/{purchaseOrderId}/refund（Refund Order Lines）60/min——
  无 RMA 的订单直退款走这条，**本单不做**（refund_request.kind=refund 先只接 RMA 退款）。
- 本地 YAML 可能过时；issueRefund 请求体（refund lines/charges 结构）实现增量3 时在线核实
  `developer.walmart.com/us-marketplace/reference/issuerefund`。

## 5. 现有基建模式（新域照抄清单）

| 模式 | 位置 | 用法 |
|---|---|---|
| 渠道拉取模板 | order/pull.py（R2-05 增量1） | prepare 短事务→逐页 HTTP 零事务→每页 upsert 短事务；gateway.prepare + request_prepared → GatewayResponse(.dry_run/.status/.data)；整店成功才推水位（returns 无水位，仍保留整店失败隔离） |
| beat 任务注册 | automation/tasks.py:828 TASKS 字典 | 加 "return_pull"；调度种子照 0026/0028 recon seed 模式（cron 08:00） |
| outbox 动作 | channel/outbox.py:40 ACTIONS 元组 | 加 "return_refund"；三段式 tx1 guard+enqueue → POST → tx2 归位；verify_pending 走对账 |
| 三档读取 | order/procurement.py:36 | SELECT mode FROM app.automation_policy WHERE team_id=… AND flow_code=… |
| 权限点 | core/authn.require_permission("xxx") | 新增 aftersale.read / refund.request / refund.approve |
| 迁移 | alembic/versions/ 最新 0029_pending_price | 本单从 **0030** 起；TOUCH/TEAM_RLS 宏照 0025 |
| db 测试 | backend/tests/db/test_order_pull.py 等 | 照 test_order_pull/test_order_ship 的 mock gateway 模式 |
| 前端页 | pages/OrdersPage.tsx + App.tsx:10,32 路由 | 新增 AftersalePage 照抄列表页模式 |

## 6. 增量拆分

1. **增量1（读闭环）**：0030 迁移（channel_return 头 + channel_return_line 行 + 行级状态变更历史）
   + gateway returns 拉取 + `return_pull` beat 任务（全量翻页、行级 upsert、变更留痕、整店失败隔离）
   + GET /returns 列表/详情端点 + db 测试 + openapi-v0 契约。
2. **增量2（三档记账/审批）**：0031 迁移 refund_request + sys_dict(refund_reason) 种子
   + create/approve/reject/list 端点 + mode 快照（manual→record/semi→approval/auto→auto，缺省 record）
   + 权限点 + db 测试。record/approval 档到此闭环。
3. **增量3（渠道执行）**：outbox action=return_refund + issueRefund 请求体（在线核实）
   + verify-back（以 GET /v3/returns?returnOrderId 行级 refundStatus 对账，绝不盲重发 BR-GW-005）
   + is_test 灰度门 + notification + dry-run 证据。
4. **增量4（面子+收尾）**：前端售后页（列表/详情/发起退款/审批）+ 07 契约注记 + runbook
   + specs 落笔 + 工单回写。

## 7. 验收（D-Q54 分级）

- ① L1 真机：A152（可扩全店，只读安全）拉回退货单头+行落库，字段与旧仓飞书表 27 列口径对拍。
- ② 三档流转：record 档记账终态 / approval 档审批流（refund.approve）/ auto 档直进执行——
  db 测试全绿；渠道执行 dry-run 证据（真实退款执行**挂账**等 A152 出现真实退货单，同 R2-05 L2 ship 模式）。
- ③ CI 全绿（pytest / ruff check+format / mypy / pnpm lint+build）。

## 8. 明确不进本单

封店工作流、邮箱模块（PRD:145 同段但独立块）；订单直退款（无 RMA，orders/{id}/refund）；
取消执行通道（kind=cancel 可记账/审批，渠道执行动作属订单域，挂账注记）；
换货 replacementInfo 消费（BR-AS-005 预留）；WFS 退货（fulfillment/return-orders）。
