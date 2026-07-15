# RS-03b 考古 + 设计（channel 写路径 outbox + 幂等）— 2026-07-15

> 工单：review_list RS-03（RS-03a 已完 2026-07-12）。闸门=A152 L2 真实渠道写入之前。
> 评审依据：external-review-round-1.md §A7（top-1 严重度）+ §C2；验收单=review_list.acceptance。

## 1. 现状核实（A7 论断逐条对码，全部属实）

| 路径 | 事务边界问题 | 代码位置 |
|---|---|---|
| submit | `_load_listing` FOR UPDATE 锁全部待提交 listing → 同一请求事务内 `gateway.request` POST /v3/feeds。锁窗口=HTTP 时长（timeout 30s，含重试更长） | listing/service.py:257,376 |
| delist | 锁单 listing → 同事务 POST RETIRE_ITEM | service.py:713,721 |
| poll_feed | 锁 feed FOR UPDATE → 同事务 GET /v3/feeds/{id} | service.py:487,499 |
| verify_back | 锁 feed → 同事务 GET /v3/feeds | service.py:634,642 |
| **崩溃窗口** | 渠道已收 feed、进程在请求事务 commit 前死 → **feed 行本身回滚**，DB 全失忆（比 verify_pending 更糟：连对账线索都没有）。verify-back 机制只覆盖"请求抛异常"，不覆盖"进程死" | submit 全函数 |
| Idempotency-Key | 契约 002 标 required（allocate/submit/delist），服务端零消费，前端零发送（C2 漂移属实） | openapi-v0.yaml:1046; frontend/src/api/client.ts |

已就位、必须保真的语义（R1-11 总账铁律）：**永不盲重试**（无响应→verify_pending→对账 adopt/lost）；
headline 不可信（对账以 feed_item 级为准）；配额 consume/release 点位；GTIN held→used 永不回收；
state_history 全链；dry-run 快照返回结构（TestDryRunEvidence + R1-11 证据文件）。

## 2. 设计（评审 A7 处方原样落地）

### 2.1 表（0021）

**app.channel_command（transactional outbox）**
- `UNIQUE (team_id, action, idempotency_key)`（评审原文约束）+ `payload_hash`：同键同载荷→返既有命令（同结果）；异载荷→409 IDEMPOTENCY_CONFLICT。
- `status ∈ pending|inflight|succeeded|failed|verify_pending`；`fence`（每次 claim +1）+ `lease_expires_at`：迟到 worker 的回写被 fence+status 双重拒绝。
- payload=完整出站请求（method/path/params/json_body=feed 封套）——**按构造无凭证**（只存 store_id 引用，凭证由网关执行时解密）+ enqueue 处递归键名扫描防御断言。
- action ∈ feed_submit|item_retire（价格/库存/lag_time feed 随 R2-04 复用本表）。
- **同店 FIFO**：claim 条件=同 store 无更早未终局命令（pending/inflight/verify_pending 均挡道）。verify_pending 挡道=有意背压（fail-closed：上一发结果未知时禁止继续发，旧系统"先对账再重投"语义的推广）。

**app.api_idempotency（C2 消费存储）**
- `UNIQUE (team_id, endpoint, idem_key)`；先占位（reservation，独立短事务）后执行，响应回填；>24h 惰性清理（契约 002 §写操作幂等）；占位残留（崩溃）超时视为失效可重占。

### 2.2 三段式（RS-03a 模式推广到 channel）

```
tx1(短)   校验+配额+spec+feed/feed_item+transition(queued)+模式预检+enqueue(command) → COMMIT
          （模式闸 GATEWAY_LIVE_DISABLED/STORE_NOT_TEST 在 tx1 预检→整体回滚，API 行为不变）
HTTP      claim(短事务, fence+1, lease) → COMMIT → gateway.request（零 DB 锁/零事务）
tx2(短)   complete(WHERE id AND fence AND status='inflight') 成功才 apply：
          200+feedId→submitted；明确拒→error+返配额+GTIN 归还+failed；
          无响应/超时→feed+command 双 verify_pending（不重试，等对账）
```

- 进程在 HTTP 后、tx2 前死 → command 停留 inflight，lease 过期 → sweep 归 verify_pending →
  verify-back 对账 adopt/lost（**不重复提交**——验收故障注入项）。
- verify_back 归位时同步终局 command（adopt→succeeded / lost→failed），解开店铺 FIFO 车道。
- poll/verify_back（读路径）同样改三段：行锁不再跨 HTTP；tx2 重锁+复核状态后回写。
- dry_run 模式走同一管道（command 即刻 succeeded，快照原样返回）——A152 真调与沙盒同代码路径（D-Q37）。

### 2.3 幂等键

- feed_submit：`feed:{feed_id}`（feed 行 tx1 先建，键天然唯一且可重放）。
- item_retire：`retire:{listing_id}:{第 N 次 delist_pending}`（同一 listing 多轮下架各占新键）。
- API 层：请求头 Idempotency-Key（契约 required）→ allocate/submit/delist 三端点接入消费；
  前端 api.post 统一自动生成 `crypto.randomUUID()`（401 刷新重试复用同 init=同键）。

### 2.4 明确不做（范围钉死）

- **inbox**：渠道进站事件（webhook 通知订阅）尚无摄取面——R2-04 接通知订阅时按本表对称落 inbox。当前进站只有主动轮询读，无重复消费风险。
- worker/beat 常驻消费者：R2-04 底座职责。本单执行器=请求内三段（RS-03a 同模式）+ sweep 懒清扫 + `erp.tools.drain_channel_outbox` CLI（部署机/beat 可调）。
- 契约 ship/refund-execute 端点的 Idempotency-Key：随 R2-05 订单域实现时用同一助手接入。

## 3. 验收对拍表（review_list.acceptance → 测试）

| 验收项 | 测试落点 |
|---|---|
| 同key同payload同结果 | outbox 单元 + API 重放（同响应体、单 feed 行） |
| 异payload 409 | outbox 单元 + API 409 IDEMPOTENCY_CONFLICT |
| 外部成功后回写前崩溃→verify-back 不重复提交 | 故障注入：claim+HTTP 成功后不 complete→sweep→verify-back adopt；断言 POST 计数=1 |
| lease/fencing 拒迟到 worker | fence 过期 complete 返 False，状态不被覆盖 |
| 同store/SKU命令有序 | 同店第二命令在首命令未终局时 claim 不到；跨店不互挡 |
| HTTP 期间行锁已释放 | FOR UPDATE NOWAIT 旁路探针（RS-03a test_row_lock_released 同法，探 listing+feed+command 三行）|
| outbox payload 脱敏 | 真实 submit 命令行扫描无凭证物料；含敏感键 payload 被 enqueue 拒绝 |
