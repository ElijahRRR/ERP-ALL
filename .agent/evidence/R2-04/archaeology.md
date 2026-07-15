# R2-04 考古：worker/beat 底座（schedule 表驱动）——现状盘点与设计依据

> 四路并行考古汇总（2026-07-15）：①既有异步/定时基建 ②待周期化欠账函数契约 ③采集回收与 worker 同步 ④specs/评审原始要求。
> 结论先行：**底座的存储层与记账层早已就位（schedule/task_run 表 + run_tracked + redis 服务），缺的只是执行体（beat 进程）本身**。R2-04 = 写 beat 循环 + 注册 9 个周期任务 + Redis pubsub 配置广播 + compose 角色启用。

## 0. 工单原文与验收（权威出处）

- `specs/005-r2-plan/README.md:46-49`：「beat 读 schedule 表驱动：feed 自动轮询、采集回收兜底、llm_cache LRU、GTIN 水位、预算闸；Redis pubsub 配置广播；compose worker/beat 角色启用。**验收**：无人工点查——A152 提交后自动轮询回写；模拟断连自动回收。」
- `.agent/review_list.json` R2-04（P1/foundation）：note「验收：A152 提交后无人工点查自动轮询回写；模拟断连自动回收」。
- 并入的 RS-03b 尾账（`.agent/progress.md:370,383`、`RS-03b/acceptance.md:31-36`）：outbox drain beat 周期化、retire verify_pending 渠道对账维护任务、api_idempotency 全表清扫。
- 另有基建欠账天然归此：partition_maintain 种子已在库但无人执行（`0004_system.py:94-95`、`00-conventions.md:96`「建分区/清理由 beat 任务或 pg_partman 负责并有告警」）。

## 1. 已就位的底座（不需要新建）

| 组件 | 现状 | 出处 |
|---|---|---|
| `app.schedule` 表 | 已建：code UNIQUE / cron / timezone(默认 Asia/Shanghai) / enabled / config jsonb / last_run_at / next_run_at；种子 1 条 `partition_maintain '0 3 1 * *'` | `0004_system.py:77-97`；设计 `09-platform.md:168-182` |
| `app.task_run` 表 | 已建：月分区（started_at），status ∈ running/done/failed，RLS | `0004_system.py:101-123` |
| `run_tracked()` | 已实现：任何后台任务经此执行，开始/结束落 task_run，失败 notify critical（按 task_code 每日 dedupe）——「任何静默失败都是缺陷」 | `automation/task_runner.py:23-76` |
| `system_tx` | 系统上下文事务（is_super 绕 RLS），worker/beat 专用 | `core/db.py:53-64` |
| Redis 基础设施 | compose redis:7-alpine 已启用 + healthcheck；`ERP_REDIS_URL` 注入各服务；`redis>=5.2` 已是正式依赖；**代码零使用** | `infra/docker-compose.yml:24-33,41`；`pyproject.toml:14`；`core/settings.py:24` |
| compose 角色占位 | `# worker: python -m erp.worker` / `# beat: python -m erp.beat`（注释，「随 R2-04 启用」）；模块不存在 | `infra/docker-compose.yml:57-65` |
| ConfigService | team>system>default + 60s 进程缓存；写后 `invalidate()` 仅本进程——**跨进程失效广播（Redis pubsub）预留未接** | `core/config_service.py:5-6,24,31,67-68` |

**无任何调度库依赖**（celery/apscheduler/arq/croniter 全仓 0 命中）；cron 解析需新增依赖（见 §5 决策）。

## 2. 九个周期任务的现状契约（逐个）

### 2.1 feed 自动轮询（验收①核心）
- `poll_feed(sessions, feed_id, *, team_id=None, is_super=False)`（`listing/service.py:556`）：自管三段式，**天然适配 beat 调用**（接收 sessionmaker）。可轮询态 = `submitted|processing`（:580）；tx2 FOR UPDATE 复核、并发返回 current（:599-606）——beat 与人工点查并发安全。
- 轮询队列索引已建：`(status) WHERE status IN ('verify_pending','submitted','processing')`（`06-listing-pricing.md:76`）。
- 节流依据：`/v3/feeds*` 共享 5000/min；「退避序列进 system_config」（`06-listing-pricing.md:77`）。
- 人工入口保留：`POST /feeds/{feed_id}/poll`（`router.py:362`，docstring 已注明「beat 自动轮询随 R2」）。

### 2.2 feed verify_pending 对账（verify-back 周期化）
- `verify_back(sessions, feed_id, *, ...)`（`service.py:730`）：要求 feed.status='verify_pending'；adopt-or-lost 保守语义，绝不重发（:757）。RS-03b 定的对账入口 =「POST /feeds/{id}/verify-back 或 R2-04 维护任务」（`drain_channel_outbox.py:10`）。
- 设计注意：需最小滞留时长门槛（config），避免刚被 sweep 的 feed 立即对账（渠道列表尚未可见→误判 lost）。lost 语义本身安全（listing 回 queued 重投、配额返还），但无谓抖动。

### 2.3 channel outbox drain
- `drain(*, sweep_only, limit)`（`tools/drain_channel_outbox.py:22-43`）已实现完整循环（sweep→pick_next→execute_command→车道背压 break）；头注明言「供部署机人工或 beat（R2-04）周期调用」。beat 任务直接复用 `drain()` 函数本体（非 subprocess）。

### 2.4 item_retire verify_pending 渠道对账（尾账，最难一个）
- 现状：`_apply_item_retire` 无响应→ complete(verify_pending)，listing 保持 delist_pending，不返还配额不重发（`service.py:947-951`，注释「渠道侧核对随维护任务（R2-04）」）；**无自动对账通道**（`RS-03b/acceptance.md:33`），期间该店 FIFO 车道背压（有意 fail-closed）。
- 需新写：查渠道 item 实际状态（GET /v3/items/{sku}）判 retire 是否生效 → resolve_verify 归位 succeeded（listing→delisted）或 failed（回滚 delist_pending→live+配额处理），复用 `outbox.resolve_verify`（:217）与 `_resolve_feed_command` 的对称模式。实现时先读 `_apply_item_retire` 成功/失败分支再镜像。

### 2.5 采集回收兜底（验收②核心）
- `reclaim(session, *, ...)`（`scrape/service.py:428-473`）已实现（心跳超时节点下线 + 超时任务归还 pending），但**纯请求驱动**（仅 `sync_node`:216 / `pull_tasks`:233 内联搭车）——「无 worker 拨入 = 无回收」。beat 任务 = system_tx 内调 reclaim()，即补上兜底。验收「模拟断连自动回收」即测此路径（既有测试 `test_scrape_api.py:217-272` 靠另一 worker sync 触发；beat 版不依赖任何 worker 存活）。

### 2.6 GTIN 水位告警
- 零代码实现；规格已定：`03-catalog.md:99`「automation 域按 (team, kind) 统计 free 占比，阈值进 team_config（默认 <15% warn、<5% critical）→ notification」；notification category `gtin_watermark`（`09-platform.md:209`）；D-Q39。查询模式可参考 `GET /gtin-pool/stats`（`router.py:387-401`）。阈值键沿用测试中已出现的 `gtin.warn_pct` 命名系。

### 2.7 llm_cache LRU
- 表有 `hit_count/last_hit_at` 但从不用于淘汰（`0008:185-196`；`llm.py:44,59`）；唯一驱逐 = 坏响应逐行 DELETE（`llm.py:167-171`）。0013 已补 GRANT DELETE。beat 任务 = 按 config（max_rows/max_idle_days）批量 DELETE 排序 last_hit_at。

### 2.8 LLM 预算闸（告警版）
- 规格：`05-audit.md:92`「每小时聚合 llm_usage_log → team_config `llm_budget_daily_usd` 超限 → notification critical + 降级建议（**不自动停，人决策**）」；schedule code `llm_budget_check`（`09-platform.md:173`）。
- **范围切割**：评审 RS-08 要求的「事前原子预留闸门」是独立加固单（`external-review-round-1.md:63-68`、`.agent/progress.md:272`「预算闸仍留 RS-08」）。R2-04 只做 schedule 底座上的**事后聚合告警版**（05-audit.md 原文语义），不越权实现 RS-08。
- 现有基础：`llm_usage_log` 记 cost_usd（`0008:202+`）、`llm.py:72` 计价。notification category `budget` 已在规格（`09-platform.md:209`）。

### 2.9 api_idempotency 全表清扫 + partition_maintain
- 清扫：per-key 惰性清理已有（`idempotency.py:60-70`，TTL 24h/stale 10min 自 `api.idempotency` 配置）；全表按龄清扫欠账（`idempotency.py:10`），索引已预留 `ix_api_idem_age(created_at)`（`0021:84-98`）。
- 分区维护：`app.ensure_month_partitions()` DB 函数已有（0004 内使用）；种子 `partition_maintain` 已在 schedule 表——beat 上线即被执行，实现体 = 对全部月分区表调该函数（预建未来 3 个月）。

## 3. 设计决策（本单拍板，写码前）

1. **beat = 单进程 asyncio 循环**（`erp.beat` 模块，compose 注释占位的命名）：每 tick（默认 30s，system_config）读 `app.schedule` 到期行，**原子领取**（`UPDATE ... SET last_run_at=now(), next_run_at=<cron 下一次> WHERE id=:id AND next_run_at <= now()` 单语句，多副本安全），领到后经 `run_tracked(sessions, code, fn, schedule_id=...)` 执行注册表中的任务函数。首次启动 next_run_at 为 NULL → 按 cron 补算不立即触发（防重启风暴）。
2. **任务注册表** `erp/automation/tasks.py`：`TASKS: dict[str, TaskFn]`，与 `listing.service.APPLIERS` 同构的显式注册模式。schedule 表出现未注册 code → task_run failed + notify（不静默跳过）。
3. **cron 解析依赖选 `cronsim`**（零依赖、活跃维护、5 段 cron + zoneinfo 时区）；croniter 上游已归档不选。timezone 用表内列（默认 Asia/Shanghai）+ zoneinfo。
4. **Redis pubsub 配置广播**：ConfigService.set_system/set_team 提交后 PUBLISH `erp:config:invalidate`（payload=key）；api 与 beat 进程 lifespan 起订阅协程收到即 `invalidate()`。**Redis 不可用不阻塞写**（fail-open：60s TTL 本就兜底一致性，广播只是加速），断线重连 + 降级日志。redis.asyncio 客户端从 `settings.redis_url` 建。
5. **`erp.worker`（通用队列消费者）本单不启用**：代码中不存在任何 Redis 队列生产者（审核异步化 RS-03a 走的是请求内三段式；采集派发走 PG pull 协议）。空转消费者=死代码。compose 该行保留注释并改注「随首个队列生产者启用」。R2-04 验收两条均为 beat 任务，不受影响。**此范围解读需 Owner 知悉**（工单回写时注明）。
6. **渠道类 beat 任务全部走既有函数**（poll_feed/verify_back/drain/execute_command），不新开渠道调用面——网关模式（dry_run/live_test/live）与限流语义自动继承，符合「不绕过 walmart_client 语义」铁律。
7. **所有节奏/阈值进配置**：tick 间隔、每轮 feed 批量上限、verify_back 最小滞留、LRU 上限、水位阈值（team_config 可覆盖）、预算阈值（team_config `llm_budget_daily_usd`）——零硬编码业务参数。
8. **新迁移 0022**：schedule 种子 8 条（feed_poll / feed_verify_back / channel_outbox_drain / retire_recon / scrape_reclaim / gtin_watermark / llm_cache_lru / llm_budget_check / api_idempotency_sweep 中 partition_maintain 已有）；无新表。

## 4. 增量拆分（每个 CI 绿可独立提交）

| # | 内容 | 验收锚点 |
|---|---|---|
| 增量1 | beat 核心循环 + cronsim 依赖 + 任务注册表 + 0022 种子迁移 + 低风险任务四件（partition_maintain / api_idempotency_sweep / llm_cache_lru / scrape_reclaim）+ 测试 | 「模拟断连自动回收」（beat tick → reclaim，不依赖 worker 存活） |
| 增量2 | 渠道任务四件（feed_poll / feed_verify_back / channel_outbox_drain / retire_recon）+ 测试 | 「A152 提交后无人工点查自动轮询回写」（mock 渠道：submit→beat tick→live 回写） |
| 增量3 | 告警任务两件（gtin_watermark / llm_budget_check）+ notification 断言测试 | 水位/预算 notify 落库 |
| 增量4 | Redis pubsub 配置广播 + compose beat 启用 + 部署 runbook + specs 落笔（09-platform/02-channel 对账小节）+ 工单回写 | 跨进程配置失效实测路径（测试用 fakeredis 或跳过标记 + 部署机实测项） |

## 5. 风险与暗礁

- **beat 与请求路径并发**：poll_feed/verify_back/execute_command 均有 FOR UPDATE 复核或 fence，双入口安全（RS-03b 已验）；reclaim 的 UPDATE 与 sync/pull 内联版并发是幂等 UPDATE，安全。
- **retire_recon 是唯一要写新渠道读逻辑的任务**——先考古 `_apply_item_retire` 全分支再动手；对账判定保守（查不到确证=维持 verify_pending，绝不臆断成功）。
- **A152 实测依赖部署**：验收「无人工点查」需部署机启 beat 容器后真提交观察——runbook 单列，含 compose profile/命令与回滚（停 beat 容器即回人工模式，无状态残留）。
- **沙箱 PG 空转即死**：db 测试前 `pg_ctlcluster 16 main start`。
- **本分支现挂 PR #5（draft，采集修复+停滞防御存档）**：环境铁律锁定单分支开发，R2-04 增量将继续压在同一 PR——推送后更新 PR 标题/描述反映实际内容，合并时机仍由 Owner 定。
- 数据治理 jobs（商标日增/飞书同步等，`006-data-governance/README.md:79-81`）用本底座但**不在本单**——底座验收后另行提单。inbox（webhook 进站）也随 R2-04 序列的 webhook 单独立做，不混入本底座单。
