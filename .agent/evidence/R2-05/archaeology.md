# R2-05 考古：订单履约最小闭环——现状盘点与设计依据

> 四路并行考古汇总（2026-07-16）：①specs 原始要求 ②backend 可复用面 ③Walmart Orders API+旧系统拉单实战 ④旧系统四检/采购/边界语义。
> 结论先行：**订单域全零起建**（无表/无模块/无前端；权限点 7 个已种未消费），但基座全齐——
> 拉单复用网关只读面、ship 回传复用 RS-03b outbox（真渠道已验证）、幂等复用 run_idempotent、
> 调度复用 beat（新增 order_pull）、四检数据走 blacklist 家族扩表。契约 Order 段已冻结（002:840-978）。

## 0. 工单与验收

- `review_list` R2-05（P0/feature）：真实只读拉取→四检→采购单→测试单发货。
  **验收 L1**=真实订单只读拉取入库对账一致；**L2**=测试单全流程流转（A152）。
- 主流程（PRD:84-88）：Walmart 拉单(15min) → 四检(软标记) → 分配采购方 → 领单 → 采购执行 → 回填物流 → ship 回传。
- 并入 RS-03b 尾账：**ship 幂等接入**（contract ship 端点 Idempotency-Key required）。
- 「测试单」考古结论：**无原生概念**——= A152（is_test）店上的真实订单（系统无造单入口，D-Q35）；
  L2 需要 A152 真实来单（Owner 自购或真实买家），物流单号人工录入（D-Q28）。

## 1. 口径裁定（写码前拍板）

1. **四检 = phishing / purchaser / price_limit / consistency**。005 计划一句话「限价/钓鱼/黑名单/重复」
   与 001 列级设计 + 002 冻结契约 + PRD:86 冲突；BR 台账（BR-ORD-005~010）只支撑后者，
   「黑名单单/重复单」无任何旧系统规则可保真。按铁律以宪法层为准；「黑名单」语义由 phishing
   的地址/邮编黑名单承载，「重复」由拉单 upsert 幂等承载。**报 Owner 知悉，不改 005 原文**。
2. **refund/cancel 执行与 channel_return 不入本单**：002 契约无 /refund /returns 路径（未冻结）；
   BR-AS-008 退款人工执行；D-Q29 三档从「记录」起步；R2 计划迭代 7=售后单。
   refund_request/channel_return 两表随售后单再建。本单 8 表建 6。
3. **portal（外部采购方③）不入本单**（R2#6）；内部双入口①②走 `procurement.execute` 权限点全做。
4. **acknowledge 不入契约但必须做**：Walmart 要求 ship 前 ack（erp-core 仅 Created 可 ack）。
   设计为内部自动步骤：ship 时若渠道态=Created，先 enqueue `order_ack` 命令再 enqueue `order_ship`
   ——同店 FIFO 车道天然保证先 ack 后 ship，无需契约新端点。
5. **BR-ORD-007 候选匹配降档**：配送方式/价格区间列不在 07 purchaser 表设计中（档案扩列未决），
   purchaser 检 MVP 判「团队存在 active 采购方」+ 限价检取 active 中最高汇率（对卖家最有利口径，
   BR-ORD-007 同向）。档案扩列留待 Owner/契约层决策，工单注记。

## 2. 建表清单（0025，全零起建 + 依赖件）

按 001/07 列级设计原样落：**purchaser / channel_order（月分区 by order_date）/ order_line（月分区随单）
/ order_check（月分区）/ procurement_order / shipment**。
依赖件：
- **automation_policy**（09:143-155，迁移中不存在）：三档开关面板（本单消费 flow=order_block；
  mode 默认 manual）。uq(team_id, flow_code)。
- **blacklist_address / blacklist_zip**：钓鱼黑名单（lark ~195/~199 条），复用 0008 `_blacklist_table`
  工厂模式（team_id 可空=全局/reason/source/status/active 唯一）；导入走 import_blacklist.py 扩 domain。
- **sync_state**（006 规划、未建）：最小形态 (scope, ref_id) PK + last_sync_at + stats——order_pull
  的每店 high-water mark（BR-ORD-001 成功才更新）。
- **分区注意**：订单 order_date 是外部数据（可达 now-180d）——迁移显式预建 [-7..+3] 月分区
  + DEFAULT 分区兜底（超范围旧单落 default 不炸任务；与 ensure_month_partitions 前瞻共存）。

## 3. 拉单协议（order_pull beat 任务，保真 BR-ORD-001~004 + 实战语义）

- 参数：`lastModifiedStartDate = max(last_sync − 1h, now − 30d)`（冷启动=now−初始天数，config）
  **+ 恒传 `createdStartDate = now − 179d`**（BR-ORD-002：不传则默认 7 天窗架空增量）+ limit=200。
- 分页：nextCursor 是**带 ? 的完整 query 串**——下页 url = `/v3/orders{cursor}` 且不带 params；
  店内串行；游标 2 分钟内用完；终止=无 cursor 或 hasMoreElements=false。
- 限流：GET /v3/orders 5000/min（网关闸富余）；rate_limiter 加 orders 键。
- upsert：channel_order `ON CONFLICT (store_id, channel_order_no, order_date)` 同步列覆盖，
  人工/内部列永不触碰（BR-ORD-003）；order_line by (order_id, channel_line_no, order_date)。
- 字段映射（实战语义）：**行状态 = orderLineStatuses[-1].status**；trackingInfo 深埋 status 对象内；
  金额 = Σ chargeType==PRODUCT（运费=Σ SHIPPING）；订单级 channel_status = 各行聚合
  （最落后未取消行；全取消=Cancelled——比旧系统"取任一行"确定性更强，specs 落笔记录）。
- 状态机：insert→internal_status=pulled；渠道 Cancelled 强制覆盖内部态 + notification（001:45）；
  四检完成→checked。成功整店才写 sync_state.last_sync（失败保留重拉窗口）。
- ship_to JSONB 存 postalAddress+phone（四检与履约需要）；customer 只存 name（PII 最小化）。

## 4. 四检引擎（软标记，公式沿用 BR-ORD-005/008/009，C7 全新实现）

- **phishing**：地址 A1+A2 标准化（去空格/大写/去标点）↔ blacklist_address 双向 substring
  （<8 字符条目跳过）；邮编 zip+4 取前 5 ↔ blacklist_zip；输出三档证据入 detail。
  BR-ORD-006：flagged 后不可被后续重检覆盖为 pass（人工 resolve 才清）。
- **purchaser**：团队无 active 采购方 → flagged（降档口径见 §1.5）。
- **price_limit**：限价 = walmart 行单价 × margin × usd_rmb ÷ 采购方汇率（active 最高）；
  source 价 = order_line→listing→product.price_snapshot['list']；source > 限价 → flagged；
  source 缺失 → flagged（BR-ORD-010「采集失败」档）。**参数进配置**：`order.price_limit`
  {margin_factor: 0.85, usd_rmb_rate: 6.8}（D-Q11/C10，team_config 可覆盖）。
- **consistency**：SequenceMatcher.ratio(渠道 productName, product.title 标准化) < 0.9 → flagged
  （阈值进同一配置键）。
- 结果 upsert by (order_id, check_kind, order_date)；任一 flagged → order.has_flag + notify
  category=order_flag（dedupe 按单）。order_block 档位（automation_policy，manual=软标记默认；
  semi/auto=flagged 冻结在 checked 不进分配）。契约端点 rerun / resolve（order.check）。

## 5. 采购执行单（D-Q50 双入口①②，portal ③不做）

- 契约端点全做：GET/POST /procurement-orders（mine=我的单）、/assign、/claim、/backfill、/exception；
  GET/POST /purchasers、PATCH /purchasers/{id}（本单 external 采购方仅建档不开门户账号）。
- 状态机 unassigned→assigned→claimed→purchased/shipped→backfilled（+exception/cancelled）；
  assignee_kind none/internal/external；**exchange_rate_locked 在领单/回填时锁快照**（D-Q32/C4）；
  backfill_actor_kind=op_direct 支持订单页代填（②）。
- 联动：procurement backfill（carrier/tracking_no）后订单可 ship；订单 internal_status
  assigned→purchasing 由分配/领单驱动。

## 6. ship 回传（L2 核心，复用 RS-03b outbox）

- 契约：POST /orders/{orderId}/ship（Idempotency-Key required，body lines/carrier/tracking_no/
  procurement_order_id，202）——run_idempotent 包裹（RS-03b 尾账收账）。
- outbox 扩 action：`order_ack` + `order_ship`（0025 改 ck_cc_action CHECK + ACTIONS + APPLIERS）。
  ship 端点：tx1 建 shipment(pending)+校验行→（渠道态 Created 先 enqueue order_ack）→enqueue
  order_ship（幂等键 `ship:{shipment_id}`；ack 键 `ack:{order_id}:{episode}`）→执行器三段式。
- 请求体（erp-core 生产实战版）：orderLine{lineNumber, sellerOrderId, orderLineStatuses[{status:
  Shipped, statusQuantity{EACH, str(qty)}, trackingInfo{shipDateTime=isoformat, carrierName{carrier},
  methodCode(默认 Standard，config), trackingNumber}}]}；carrier 白名单 USPS/UPS/FedEx/DHL/OnTrac/Other。
- applier：200→shipment pushed + 行 shipped+carrier/tracking 回填 + 全行 shipped→订单 internal
  shipped；明确拒→push_status failed + notify + 人工重推（episode 新键重投）；无响应→verify_pending，
  beat `ship_recon` 对账（GET /v3/orders/{po} 看行态，镜像 retire_recon 模式）。POST 永不盲重试
  （BR-GW-005）。

## 7. 增量拆分（每个 CI 绿可独立提交）

| # | 内容 | 验收锚点 |
|---|---|---|
| 增量1 | 0025 迁移（6 订单表+automation_policy+blacklist_address/zip+sync_state+outbox action 扩展+分区预建）+ order 模块骨架 + order_pull beat 任务（拉单/upsert/状态机/sync_state）+ 种子 + 测试 | mock 渠道拉单入库对账（L1 测试化） |
| 增量2 | 四检引擎 + 钓鱼黑名单导入通道 + order_flag 通知 + order_block 档位 + GET /orders 列表/详情 + rerun/resolve 端点 + 测试 | flagged 证据/不可覆盖/放行 |
| 增量3 | purchaser CRUD + procurement_order 双入口全端点（assign/claim/backfill/exception+汇率锁定）+ 测试 | 内部领单/代填/锁汇率 |
| 增量4 | ship 回传（run_idempotent + order_ack/order_ship applier + ship_recon beat + 人工重推）+ 测试 | L2 测试化：ship→渠道确认→行/单状态回写 |
| 增量5 | 前端订单页（列表/详情/四检/采购执行/发货表单）+ L1 对账 harness（erp.tools.order_pull_verify）+ specs 落笔 + runbook + 工单回写 | 部署机 L1 真拉对账 + A152 L2 步骤 |

## 8. 风险与暗礁

- **分区范围**：外部 order_date 超预建范围→DEFAULT 分区兜底（勿删）；partition_maintain 已自动纳管新分区表。
- **nextCursor 2 分钟时效**：店内串行、页间不做慢操作；游标过期=该店本轮失败，下轮重拉（last_sync 未推进）。
- **L1 对账口径**：以渠道为准——harness 重新 GET 同窗口比对（单数/单号集合/行数/状态/金额），报告差异清单。
- **L2 依赖真实来单**：A152 需有真实订单才能全流程；无单则 L2 等窗口（runbook 注明）。ship 是真金白银的渠道写（触发买家扣款）——**只允许 A152（is_test）灰度**，live 档闸门沿用 channel.live_enabled 语义。
- **旧系统对拍**：purchaser/limit/consistency 三检生产从未启用（BR-ORD-012）——无历史 groundtruth，验收以规则单测+人工抽查为准（C7 全新实现口径）。
- 契约冻结面不动：不加 /acknowledge、/refund 端点；ack 为内部自动步骤。
- lark 钓鱼黑名单导入陷阱：表头在第 5 行、分块 ≤5000 行（SYNTHESIS:44）——导入通道注意。
