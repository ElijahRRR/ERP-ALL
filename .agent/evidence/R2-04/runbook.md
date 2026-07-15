# R2-04 worker/beat 底座 部署与验收 runbook

> 面向部署机（Win11 + Docker Desktop）。以下「部署机指令」段可整段粘贴给本地 AI。
> 回滚 = 停 beat 容器：无状态残留，所有人工端点（手动轮询/verify-back/drain CLI）全部保留。

## 变更内容（本次拉新需要知道的）

- 新服务 **beat**（compose 已启用，`make up` 已包含）：读 `app.schedule` 表驱动周期任务。
- 新迁移 0022–0024：schedule 种子 9 条 + `ensure_month_partitions` 提权（SECURITY DEFINER）。
- 新依赖 cronsim（镜像重建时自动装）。
- api 启动时新增 Redis 订阅协程（配置失效广播）；Redis 不可用不影响 api 起动与正确性。

## 部署机指令（可整段粘贴）

```
【铁律】绝不操作生产库 erp_all 的结构；暂存用一次性容器；用毕清理；不输出密钥。

任务：拉新版并启用 beat 服务，然后做 R2-04 两项验收。

1) 拉代码到目标 commit（main 合并后以 GitHub 端提供的 SHA 为准）：
   git fetch origin && git checkout <SHA>
2) 重建并启动（beat 已并入默认 up）：
   docker compose -f infra/docker-compose.yml up -d --build db redis migrate api beat
3) 核验：
   - docker compose -f infra/docker-compose.yml ps   # beat 应为 running
   - docker compose -f infra/docker-compose.yml logs --tail 20 beat
     # 期望看到 "beat.start" 与 "config_bus.subscribed"
   - alembic 版本应为 0024（migrate 容器日志或 api 健康即可，不要手动动库结构）
4) 验收①（A152 提交后无人工点查自动轮询回写）：
   - ERP 前台用 A152 正常「分配上架 → 提交」一个测试 SKU（live_test 档）
   - 之后【不要点轮询按钮】，等待 ≤5 分钟（feed_poll 默认 */2 cron + 300s 节流）
   - 期望：feed 状态自动从 submitted 变为 processed/error，listing 状态自动回写，
     通知中心出现结果通知；task_run 表有 task_code='feed_poll' 的 done 记录
5) 验收②（模拟断连自动回收）：
   - 起一个采集 worker 领任务后直接杀掉容器（kill，不要优雅退出）
   - 【不要再起任何 worker】等待 ≤12 分钟（心跳超时 90s + 任务硬超时 10min + beat 周期）
   - 期望：worker_node 变 offline，卡在 dispatched 的任务自动回到 pending；
     task_run 表有 task_code='scrape_reclaim' 的 done 记录（stats.reclaimed ≥ 1）
6) 回报：beat 容器状态、两项验收的实际观察（含时间点）、异常日志（如有）。

调参说明（运营侧，不改代码）：所有任务节奏在 app.schedule 表（cron/enabled/config），
beat 轮询间隔在 system_config 'beat.tick_seconds'（默认 30）。停用某任务：
UPDATE app.schedule SET enabled=false WHERE code='<任务名>';（这是数据不是结构，允许）
```

## 任务清单与默认节奏（速查）

| code | cron | 说明 |
|---|---|---|
| feed_poll | */2 * * * * | feed 自动轮询（batch 20 / 单 feed ≥300s 间隔 / 48 次未终态告警） |
| feed_verify_back | */10 * * * * | verify_pending 对账 adopt-or-lost（滞留 ≥600s 才碰） |
| channel_outbox_drain | */5 * * * * | outbox 崩溃遗留补执行 + 过期 inflight 清扫 |
| retire_recon | */15 * * * * | 下架结果未知对账（商品实况权威；grace 3600s） |
| scrape_reclaim | * * * * * | 采集断连回收兜底 |
| partition_maintain | 0 3 1 * * | 分区预建（0004 老种子，beat 上线即接管） |
| api_idempotency_sweep | 40 4 * * * | 幂等表按龄清扫（TTL 24h 同源配置） |
| llm_cache_lru | 20 4 * * * | LLM 缓存淘汰（闲置 90 天 / 容量 20 万行） |
| gtin_watermark | 15 */6 * * * | GTIN 水位告警（warn 15% / critical 5%，team_config 可覆盖） |
| llm_budget_check | 5 * * * * | LLM 日预算超限告警（team_config llm_budget_daily_usd，未设=不告警） |

## 故障排查

- beat 起不来/循环崩溃：看 `logs beat`；tick 级异常不会退出进程（beat.tick_error 日志），
  进程级崩溃多为 DB 不可达。
- 某任务反复失败：通知中心会有 task_fail critical（每日去重一条）；task_run.error 有全文。
- 坏 cron 被写进 schedule：该行按 1 小时兜底推迟并记失败，不会拖死循环——修 cron 即恢复。
- 想临时回到全人工：停 beat 容器即可，或逐任务 `enabled=false`。
