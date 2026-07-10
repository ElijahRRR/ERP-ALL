# R1-09 考古对照表：amazon-scraper-v3 → ERP scrape 域

源仓：/workspace/amazon-scraper-v3（server/app.py 2634 行 + common/database.py 2486 行）。
逐机制对照，标注保真/改造/舍弃三类。

## 协议端点

| v3 | ERP | 处置 |
|---|---|---|
| `GET /api/tasks/pull?worker_id&count&prefer_zip` | `GET /worker/v1/tasks/pull?count` | 保真（租约语义）；prefer_zip 是 Amazon session 邮编亲和性优化，R1 采集端未移植 session 池 → 暂舍弃，接 worker 引擎时回补 |
| `POST /api/tasks/release`（worker_id+lease_epoch 校验） | `POST /worker/v1/tasks/release` | 保真：租约校验后归还；兼容旧格式分支（无校验直放）故意不移植——新系统无旧 worker |
| `POST /api/tasks/result`（lease 校验→accept_success/failed） | `POST /worker/v1/tasks/result` | 保真：stale 拒收语义一致；success 分支追加 product upsert（v3 写 results 表，ERP 直通产品库） |
| `POST /api/tasks/result/batch`（单事务批量） | 未移植 | R1 单结果足够；量起来后 R2 增补（SQLite 锁争用动机在 PG 不存在，优先级降低） |
| `POST /api/worker/sync`（心跳+指标+配额下发+restart 标记） | `POST /worker/v1/sync` | 保真心跳/指标/设置下发；全局并发配额协调器（AIMD 窗）与 restart 标记随 worker 引擎移植（R2） |
| worker 无认证（内网假设） | node_key + 一次性下发 token（sha256 入库）+ enroll_token 注册闸 | 改造：ERP worker 拨入公网/部署机，必须认证（001 §worker_node.token_hash） |

## 租约与回收

| v3 机制 | ERP 对应 | 处置 |
|---|---|---|
| `lease_epoch`：所有回收路径 bump，迟到结果失效 | `scrape_task.attempt` 兼作租约纪元：派发时 +1；回传校验 (worker_id, attempt, status) | 保真等价：v3 在回收时 bump、ERP 在再派发时 bump——两者都保证旧租约必然 ≠ 当前值；且 attempt 同时承担重试计数（v3 retry_count 独立列，ERP 合一，语义 = 派发次数） |
| `pull_tasks`：SQLite `_write_lock + BEGIN IMMEDIATE` 全局串行 | PG `FOR UPDATE OF t SKIP LOCKED` | 改造升级:多 worker 并发领取无锁争用（v3 的锁竞争诊断仪表因此无需移植） |
| `reclaim_dead_worker_tasks`：死 worker + 硬超时合成一条 SQL | `service.reclaim()`：心跳超时节点→offline；offline 节点任务+硬超时任务一条 UPDATE 归还 | 保真（合成一条 SQL 原则保留）；触发点 = sync/pull 顺带（v3 是后台循环，ERP beat 定时任务 R2 接管） |
| `auto_retry_failed_tasks`（终态失败再入队,轮次上限+NO_AUTO_RETRY 排除） | 未移植 | R1 minimal 不需要；R2 随 automation_policy 决定是否自动翻案 dead 任务 |
| 任务优先级：`MAX(priority)` 先服务 + FIFO | `ORDER BY j.priority DESC, t.id` | 保真（优先级挂 job 级，v3 挂 task 级——ERP 规格 001 §scrape_job.priority 决定） |

## 数据模型

| v3 (SQLite) | ERP (PG) | 处置 |
|---|---|---|
| batches 表 | scrape_job（+team_id/source/job_kind/input，D-Q4 多源扩展） | 改造：套进多租户+多源规格 |
| tasks 表（asin/zip_code/needs_screenshot/task_type/task_meta） | scrape_task（target_ref 通用化；截图/邮编列不移植） | 截图存证是 v3 独立能力，ERP R1 无此需求；月分区 12 个月保留 |
| results 表（FTS5 全文索引+变动检测） | scrape_result（月分区 90 天）+ product upsert | 改造：结果表只做对账暂存，真身进 product；FTS/变动检测由 product 域承接（R2） |
| workers 内存 registry（`_worker_registry` dict，重启即失） | worker_node 表持久化 | 改造：001 §worker_node 规格（重启不丢节点台账） |
| webhook 完成回调 | notify()（R1-06 通知中心） | 改造：站内通知替代（作业完成通知接 R2 选品 UI 时加） |
| 定时任务 schedules 表 | app.schedule（0004 已建） | 已有域，R2 接 beat |

## 实战经验直接继承

- 迟到结果必须显式拒收而非静默丢弃（v3 踩过重复入库）→ `{accepted, stale}` 显式回执。
- 回收与派发的 bump 必须原子（v3 "合成一条 SQL 防止双重 bump" 注释）→ ERP 派发/回收各自单语句 UPDATE。
- worker 长跑内存泄漏 → v3 `--auto-restart-hours`；ERP 侧 draining 状态支持优雅下线（sync 下发）。
