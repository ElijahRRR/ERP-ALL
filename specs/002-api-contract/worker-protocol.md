# Worker 拨入协议 v1（机器协议，独立于 UI 契约）

> 移植源：amazon-scraper-v3 server/worker 协议（D-Q42/47）。worker 部署在本地机器，
> **出站拨入**云端/部署机 API，无入站端口。语义对照表：`.agent/evidence/R1-09/archaeology.md`。

## 认证

- 注册：`POST /api/v1/worker/v1/register`，body 携带 `enroll_token`（=system_config
  `scrape.worker_enroll_token`，Owner 设置；未配置=注册关闭）。
- 成功返回 `node_token`（**仅此一次**下发明文；库中只存 sha256）。令牌遗失需管理员
  删除节点后重新注册。
- 其余端点全部要求请求头 `X-Node-Key` + `X-Node-Token`。

## 端点

| 端点 | 语义 |
|---|---|
| `POST /worker/v1/register` | 注册节点：`{node_key, kind, enroll_token, version?, capacity?}` → `{node_id, node_token}` |
| `POST /worker/v1/sync` | 心跳 + 指标上报（`{version?, metrics, window_state}`）→ `{settings, draining}`；顺带触发一轮任务回收 |
| `GET /worker/v1/tasks/pull?count=N` | 领任务（原子租约）→ `{tasks: [{task_id, job_id, source, job_kind, target_ref, attempt, max_attempts}]}` |
| `POST /worker/v1/tasks/release` | 优雅归还：`{tasks: [{task_id, attempt}]}` → `{released}` |
| `POST /worker/v1/tasks/result` | 回传：`{task_id, attempt, success, payload?, payload_ref?, fetched_at?, error_type?, error_detail?}` → `{accepted, stale, product?}` |

## 租约协议（v3 lease_epoch 语义）

- `attempt` 兼作租约纪元：每次派发 +1；回传/归还必须携带**领取时拿到的 attempt**。
- 租约不符（任务已被回收再派发、或非本节点持有）→ `{accepted: false, stale: true}`，
  worker 丢弃即可，**不要重试**。
- 失败回传且 `attempt < max_attempts` → 任务自动归还队列等待重派；达到上限 → dead 终态。

## 回收（断连自愈）

- 心跳超时（默认 90s，`scrape.heartbeat_timeout_s`）→ 节点标 offline，其在途任务归还。
- 任务硬超时（默认 10min，`scrape.task_timeout_min`）→ 无条件归还。
- 回收在 sync/pull 时顺带执行（beat 定时任务接管为 R2 项）。

## worker 侧建议节奏

- sync 每 10–15s 一次；pull 按并发窗余量拉取；结果尽量批量回传（batch 端点 R2 视量增补）。
- 收到 `draining: true` → 完成在途任务后停止 pull（优雅下线）。

## product 入库（product_detail 作业）

成功结果的 `payload` 期望字段：`title`（必填）、`brand`、`category_path`、
`amazon_leaf_id`、`images[]`、`attrs{}`、`price_snapshot{}`。
服务端按 001 §03 去重协议 upsert：`ON CONFLICT (team_id, source_channel, source_ref)
DO UPDATE` 刷新 title/price_snapshot/attrs，**不重置 status**。
