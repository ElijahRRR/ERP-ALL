# 2026-07-16 生产三缺陷整改（HF-0716）

Owner 报告：①ERP 无订单页；②采集器卡住（未真抓、无审核输入）；③真实 UPC 上架失败，
Walmart 后台报「数据错误：Invalid Date」（feed #36 / listing #46 M0002418）。

## 原因分析

### ① 无订单页 —— 非缺陷，部署滞后 + 部署指令缺项
订单页代码只存在于 R2-05（main `543685d`，2026-07-16 合并）；报告时部署机仍在
前一版 `0d1b3aa`（PR#6 后），必然无订单页。次因：此前部署指令只重建
`db redis migrate api beat`，未提前端（frontend 服务是 dev-profile vite bind-mount，
检出新代码即热更，但需确认容器在跑 + 浏览器强刷）。权限面无问题
（超管 `has()` 旁路；order.read/procurement.read 0002 已种）。

### ② 采集器卡住 —— 双层结构性缺陷（代码审计确认，最终卡点需部署机取证）
**A. beat 层**：单进程串行 `await` 循环，任务全链无超时
（beat.py tick 顺序执行 / run_tracked 无 wait_for / httpx 虽有四相超时但无第二道防线）。
任何一个渠道 HTTP/DB 调用挂起 ⇒ 整个 beat 冻结 ⇒ 全部周期任务停摆
（含 scrape_reclaim/feed_poll/order_pull）。领取语义为先推 next_run_at 后执行，
挂起那轮丢失且 task_run 停 running 永不收尾。
**B. 采集管线层**：无任何采集侧告警与整单收口——
- worker 不在线（profile 门控/enroll token 空/代理缺失 fail-closed 崩溃重启环）时，
  job 永停 pending，UI 只见 0% 进度条，无告警；
- worker 反复中途死亡时任务在 pending↔dispatched 乒乓（attempt 派发即增，
  终态只在 submit_result 产生），永不 dead、job 永不收口；
- reclaim 只动 task 不结算 job；前端无 worker 节点健康可见性。

### ③ Invalid Date —— 管线对日期格式零感知（最高嫌疑）+ endDate 写法未实测组合
- schema 的 `format: date/date-time` 在 FieldSpec 摘要即被丢弃：LLM prompt 不知格式、
  coerce 链无日期步骤、必填兜底会把 `"Not Available"` 塞进日期字段、
  validator 仅查 endDate 含 "T"（连 `{END_DATE}` 占位符都能过）。
  LLM 填的 `"2024"`/`"March 2024"`/`"N/A"` 类值原样直达渠道。
- endDate 组合风险：D-Q9 远期 2049 + 无毫秒 `T00:00:00Z` 从未实测；
  旧仓 2049 唯一成功写法带毫秒 `.000Z`（BR-RET-007，2026-05-08 校正：
  `'2049-12-31' is not a valid format` 被拒、`2049-12-31T00:00:00.000Z` 被收）；
  官方 OpenAPI 示例亦用 `2049-12-31T08:00:00Z`/2055——2049 年份本身无被拒证据。
- 具体拒收字段名已在部署机 DB（feed_item.error_msg/raw 存了渠道完整 ingestion 错误），
  见下方取证 SQL。

## 修复清单（本次提交）

| # | 修复 | 位置 |
|---|---|---|
| 1 | beat 任务级硬超时：schedule.config.task_timeout_seconds > system_config beat.task_timeout_seconds > 默认 900s；超时=失败记账+告警，循环继续 | automation/beat.py, task_runner.py |
| 2 | beat 启动回收崩溃遗留 running task_run（STALE_RUNNING） | task_runner.reclaim_stale_runs |
| 3 | scrape reclaim：attempt≥max_attempts 判 dead+计 failed_tasks+job 收口（乒乓终结） | scrape/service.py |
| 4 | scrape_reclaim beat 任务扩展：running job 收口结算兜底 + 无在线 worker 告警（critical，宽限默认 10min）+ 零进展 job 告警（warn，默认 60min；均 schedule.config 可调） | automation/tasks.py |
| 5 | 采集作业页顶部「采集节点健康」横幅（在线数/0 在线红色警示；无权限静默隐藏） | frontend ScrapeJobsPage |
| 6 | FieldSpec 透出 format；LLM prompt 对日期字段加格式指令（禁 N/A/纯年份，不确定则省略） | wpt_schema.py, attr_fill.py |
| 7 | coerce 新增 fix_date_formats：可解析→规范化（date=YYYY-MM-DD；date-time 纯日期提升 .000Z），不可解析→可选删/必填留给校验器；日期字段禁 'Not Available' 兜底臆造 | coerce.py |
| 8 | validator：format=date/date-time 真解析校验、startDate 纳入、占位符泄漏由跳过改报错 | validator.py |
| 9 | endDate 输出 `.000Z` 毫秒写法（2049 唯一实测成功组合）；远期默认值入配置 listing.orderable_defaults.end_date_default（默认 2049-12-31，D-Q9 不变） | spec.py |

## 部署机指令（可整段粘贴）

```
【铁律】绝不操作生产库 erp_all 的结构；暂存用一次性容器；用毕清理；不输出密钥。

任务：先取证（步骤 1-3 只读），再升级部署（步骤 4-5），回报全部输出。

1) feed #36 拒收取证（Invalid Date 具体字段名就在这里）：
   docker compose -f infra/docker-compose.yml exec db psql -U postgres -d erp_all -c \
     "SELECT error_code, error_msg, jsonb_pretty(raw) FROM app.feed_item WHERE feed_id = 36;"
   docker compose -f infra/docker-compose.yml exec db psql -U postgres -d erp_all -c \
     "SELECT id, jsonb_pretty(payload->'json_body') FROM app.channel_command
      WHERE action = 'feed_submit' ORDER BY id DESC LIMIT 2;"
2) 采集卡点取证：
   docker compose -f infra/docker-compose.yml exec db psql -U postgres -d erp_all -c "
     SELECT key, value FROM app.system_config WHERE key LIKE 'scrape.%';
     SELECT id, node_key, status, now()-last_heartbeat_at AS hb_age FROM app.worker_node ORDER BY id;
     SELECT status, count(*) FROM app.scrape_job GROUP BY status;
     SELECT id, status, total_tasks, done_tasks, failed_tasks, now()-created_at AS age
       FROM app.scrape_job WHERE finished_at IS NULL ORDER BY created_at LIMIT 20;
     SELECT status, count(*), max(attempt) FROM app.scrape_task GROUP BY status;"
   docker compose -f infra/docker-compose.yml ps
   docker compose -f infra/docker-compose.yml logs --tail=100 scraper 2>&1 | tail -50
3) beat 停摆取证：
   docker compose -f infra/docker-compose.yml exec db psql -U postgres -d erp_all -c "
     SELECT code, enabled, last_run_at, next_run_at, now()-last_run_at AS since_last
       FROM app.schedule ORDER BY next_run_at;
     SELECT id, task_code, status, started_at, now()-started_at AS age, left(error,120)
       FROM app.task_run WHERE status='running' OR started_at > now()-interval '2 hours'
       ORDER BY started_at DESC LIMIT 30;"
   docker compose -f infra/docker-compose.yml logs --tail=60 beat
   判读：进程 up 但日志无新 beat.tick + 有陈年 running task_run = 挂起任务顶死循环
   （本次升级已加超时护栏根治）。
4) 升级部署（含前端；<SHA> 用云端提供的最新 main）：
   git fetch origin main && git checkout <SHA>
   docker compose -f infra/docker-compose.yml up -d --build db redis migrate api beat
   docker compose -f infra/docker-compose.yml --profile dev up -d frontend
   若 scraper 此前在跑：docker compose -f infra/docker-compose.yml --profile scraper up -d --build scraper
5) 核验：
   - beat 日志出现 beat.start 且每轮 beat.tick 正常；
   - 若采集配置缺失（步骤 2 第一条查询为空）：不要手工 DDL/INSERT，回报即可，
     由云端出配置种子方案；
   - 浏览器强刷（Ctrl+F5）后确认左侧菜单出现「订单管理/采购方」；
   - ≤1 分钟内 scrape_reclaim 任务 task_run 有新 done 记录；若无在线 worker，
     通知中心应出现「采集停摆：无在线采集节点」告警（这是新增的预期行为）。
6) 回报：步骤 1-3 全部输出原文 + 步骤 5 核验结果。
```

## Owner 侧后续

1. 部署机回报 feed #36 的 error_msg/raw 后，云端确认 Invalid Date 具体字段：
   - 若为 LLM 填充的日期属性 → 本次修复已根治（重建 listing 重提交即可）；
   - 若为 endDate 本身 → 本次 .000Z 写法大概率已修；仍拒则改配置
     `listing.orderable_defaults` 的 `end_date_default`（如 2030-12-31）再试，零代码变更。
2. 失败的 listing #46：错误确认后在上架管理页重提交（spec 会按新构建器重建）。
3. R2-05 L1/L2 验收流程不变（evidence/R2-05/runbook.md），等新版本部署后进行。
