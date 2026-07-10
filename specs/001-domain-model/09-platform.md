# 09 platform — scraping / mail / automation / notify / system

> 决策依据：D-Q42（采集移植源=amazon-scraper-v3 独立仓）、D-Q47（server 云端、worker 本地拨入）、D-Q22（内置邮箱收发聚合）、D-Q17（邮件不长期保留）、D-Q13/26/29（三档自动化+可配频率）、D-Q11（参数运营维护）、通知中心替代飞书（D-Q7）。

## scraping 采集域

### scrape_job 采集作业

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| source | TEXT | NOT NULL CHECK IN (amazon, walmart) | 多源扩展点（D-Q4） |
| job_kind | TEXT | NOT NULL CHECK IN (product_detail, keyword, seller, bestseller, price_watch) | 选品三入口（D-Q24：产品ID/关键词/卖家）+ 盯价 |
| input | JSONB | NOT NULL | ASIN 列表 / 关键词 / 卖家 ID + 深度参数 |
| status | TEXT | NOT NULL DEFAULT 'pending' CHECK IN (pending, running, done, partial, failed, cancelled) | |
| priority | SMALLINT | NOT NULL DEFAULT 100 | |
| total_tasks / done_tasks / failed_tasks | INT | NOT NULL DEFAULT 0 | 计数器（task 终态回调累加） |
| requested_by | BIGINT | NULL | |
| finished_at | timestamptz | NULL | |
| +公共列 | | | |

索引：`(team_id, status, created_at DESC)`。

### scrape_task 采集任务（月分区，12 个月保留）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | identity | PK=(id, created_at) |
| job_id | BIGINT | NOT NULL | |
| team_id | BIGINT | NOT NULL | 冗余 |
| target_ref | TEXT | NOT NULL | ASIN/URL/关键词页 |
| status | TEXT | NOT NULL DEFAULT 'pending' CHECK IN (pending, dispatched, running, done, failed, dead) | dead=超过 max_attempts |
| attempt / max_attempts | SMALLINT | NOT NULL DEFAULT 0 / 3 | |
| worker_id | BIGINT | NULL | → worker_node |
| dispatched_at / finished_at | timestamptz | NULL | |
| error | TEXT | NULL | |
| created_at | | 分区键 | |

索引：`(job_id, status)`、`(status, created_at) WHERE status IN ('pending','dispatched')`。
**队列边界**：派发队列在 Redis（v3 的 server/worker 协议移植）；本表是事实对账源——worker 丢失/Redis 重启后由本表重建队列（00 §12 原则）。

### scrape_result 采集结果（月分区，90 天保留）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | identity | PK=(id, created_at) |
| task_id | BIGINT | NOT NULL | |
| team_id | BIGINT | NOT NULL | |
| target_ref | TEXT | NOT NULL | |
| payload | JSONB | NULL | 结构化抽取结果（供入库 product） |
| payload_ref | TEXT | NULL | 原始 HTML/大 JSON 落 OSS/盘引用 |
| fetched_at | timestamptz | NOT NULL | |
| created_at | | 分区键 | |

索引：`(task_id)`。转化协议：result → product upsert（03 号文档 ON CONFLICT 刷新）由采集域消费任务完成，转化成功即可等待分区过期清理。

### worker_node 采集节点（本地拨入，D-Q47）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| node_key | TEXT | NOT NULL UNIQUE | 节点注册标识（首次注册发放 token） |
| token_hash | TEXT | NOT NULL | 节点认证（出站拨入云端 API，无入站端口） |
| kind | TEXT | NOT NULL CHECK IN (scraper_amazon, scraper_walmart) | |
| version | TEXT | NULL | worker 版本（灰度升级用） |
| status | TEXT | NOT NULL DEFAULT 'offline' CHECK IN (online, offline, draining) | draining=下线前不派新任务 |
| capacity | INT | NOT NULL DEFAULT 1 | 并发窗上限 |
| window_state | JSONB | NOT NULL DEFAULT '{}' | AIMD 当前窗/冷却（v3 语义移植；权威副本在 Redis，本列为快照） |
| last_heartbeat_at | timestamptz | NULL | 超时 → offline + 任务回收 |
| stats | JSONB | NOT NULL DEFAULT '{}' | 累计成功/失败/封禁率 |
| registered_at | timestamptz | NOT NULL DEFAULT now() | |

作用域：全局（节点是基础设施，任务才有 team）。

## mail 邮件域（D-Q22 收发聚合；D-Q17 正文不长留）

### mailbox 店铺邮箱

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| store_id | BIGINT | NULL REFERENCES store | 店铺绑定邮箱；NULL=团队公共箱 |
| address | TEXT | NOT NULL UNIQUE | |
| imap_host / imap_port / imap_ssl | TEXT/INT/BOOL | NOT NULL | |
| smtp_host / smtp_port / smtp_ssl | TEXT/INT/BOOL | NOT NULL | 发信（D-Q22 要求收+发） |
| username | TEXT | NOT NULL | |
| password_encrypted | BYTEA | NOT NULL | 00 §10 |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN (active, error, disabled) | error=连续拉取失败（自动标记+通知） |
| poll_interval_sec | INT | NOT NULL DEFAULT 300 | |
| last_polled_at | timestamptz | NULL | |
| last_error | TEXT | NULL | |
| +公共列 | | | |

### mail_message 邮件（元数据永留，正文 30 天）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| mailbox_id | BIGINT | NOT NULL REFERENCES mailbox | |
| team_id | BIGINT | NOT NULL | 冗余 |
| message_uid | TEXT | NOT NULL | IMAP UID |
| subject | TEXT | NULL | |
| from_addr / to_addr | TEXT | NULL | |
| received_at | timestamptz | NOT NULL | |
| classification | TEXT | NOT NULL DEFAULT 'none' CHECK IN (none, suspension, listing, order, payment, other) | mail_rule + LLM 兜底分类 |
| incident_id | BIGINT | NULL | → store_incident（suspension 自动开事件，02 号文档） |
| body_ref | TEXT | NULL | 正文落盘引用；**清理任务 30 天后删文件并置 NULL**（D-Q17 落地；封店正文已转存 incident.mail_body_snapshot 永留） |
| purge_after | DATE | NOT NULL | 默认 received_at + 30 天 |
| created_at | | | |

约束：`uq_mail_message (mailbox_id, message_uid)`。索引：`(team_id, classification, received_at DESC)`、`(purge_after) WHERE body_ref IS NOT NULL`。

### mail_outbox 发件

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| mailbox_id | BIGINT | NOT NULL | |
| team_id | BIGINT | NOT NULL | |
| to_addr | TEXT | NOT NULL | + cc JSONB DEFAULT '[]' |
| subject / body_ref | TEXT | NOT NULL | 正文同样落盘、同保留策略 |
| status | TEXT | NOT NULL DEFAULT 'draft' CHECK IN (draft, queued, sent, failed) | |
| sent_at | timestamptz | NULL | |
| error | TEXT | NULL | |
| +公共列（created_by=撰写人） | | | |

### mail_rule 分类规则（全局种子 + 可关）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| rule_kind | TEXT | NOT NULL CHECK IN (suspension_detect, classify) | |
| pattern | JSONB | NOT NULL | {from_domain, subject_regex, body_keywords} |
| action | JSONB | NOT NULL | {classification, open_incident: bool} |
| priority | SMALLINT | NOT NULL DEFAULT 100 | |
| enabled | BOOLEAN | NOT NULL DEFAULT true | |
| updated_by / updated_at / created_at | | | 运营可维护（D-Q11） |

## automation 自动化域

### automation_policy 三档策略（D-Q13/14/26/29 的唯一开关面板）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | 团队级开关（档位是团队决策） |
| flow_code | TEXT | NOT NULL | 见下方注册清单 |
| mode | TEXT | NOT NULL DEFAULT 'manual' CHECK IN (manual, semi, auto) | **默认最保守档** |
| config | JSONB | NOT NULL DEFAULT '{}' | 流程私有参数（频率/阈值/批量大小） |
| enabled | BOOLEAN | NOT NULL DEFAULT true | |
| updated_by / updated_at / created_at | | | |

约束：`uq_automation_policy (team_id, flow_code)`。
flow_code 注册清单（v1，代码 Enum 对照 + CI 校验）：

| flow_code | 三档语义 | config 关键项 |
|---|---|---|
| audit_to_listing | 审核通过后：人工逐批确认 / 半自动（批量待确认队列）/ 全自动进分配（D-Q13） | batch_size |
| compliance_block | off(=manual 纯软标记) / block 拦截（D-Q14） | 按 severity 分档 |
| order_block | 四检 flagged 单是否冻结分配 | check_kinds |
| refund / cancel | 记录 / 审批 / 自动执行（D-Q29） | amount_ceiling（auto 档金额上限） |
| pricing_watch | 盯价改价：仅报告 / 待确认 / 自动改价 | frequency（默认 1/日，店铺覆盖 D-Q26） |
| gtin_alert | 水位告警阈值 | warn_pct, critical_pct |
| suspension_reminder | 封店提醒节奏（D-Q33） | remind_days |

### schedule 调度注册（beat 读表驱动，运营可维护）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| code | TEXT | NOT NULL UNIQUE | order_pull / mail_poll / settlement_pull / partition_maintain / recon_scan / gtin_watermark / llm_budget_check / tro_import… |
| description | TEXT | NOT NULL | |
| cron | TEXT | NOT NULL | 5 段 cron |
| timezone | TEXT | NOT NULL DEFAULT 'Asia/Shanghai' | |
| enabled | BOOLEAN | NOT NULL DEFAULT true | |
| config | JSONB | NOT NULL DEFAULT '{}' | |
| last_run_at / next_run_at | timestamptz | NULL | |
| updated_by / updated_at / created_at | | | |

作用域：全局（团队差异走 automation_policy）。种子清单在 EA-003 R1 分解冻结。

### task_run 任务运行记录（月分区，12 个月）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | identity | PK=(id, started_at) |
| task_code | TEXT | NOT NULL | schedule.code 或 worker 任务名 |
| schedule_id | BIGINT | NULL | |
| team_id | BIGINT | NULL | 团队任务记账 |
| status | TEXT | NOT NULL CHECK IN (running, done, failed) | |
| started_at | timestamptz | NOT NULL | 分区键 |
| finished_at | timestamptz | NULL | |
| stats | JSONB | NOT NULL DEFAULT '{}' | 处理量/成功/失败 |
| error | TEXT | NULL | |

索引：`(task_code, started_at DESC)`。连续失败 N 次（system_config 阈值）→ notification critical——**任何静默失败都是缺陷**（草稿系统 cron 黑箱教训）。

## notify 通知中心（替代飞书通知）

### notification 通知（月分区，12 个月）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | identity | PK=(id, created_at) |
| team_id | BIGINT | NULL | NULL=全局公告 |
| severity | TEXT | NOT NULL CHECK IN (info, warn, critical) | |
| category | TEXT | NOT NULL | quota / gtin_watermark / store_incident / order_flag / task_fail / budget / system… |
| title | TEXT | NOT NULL | |
| body | TEXT | NULL | |
| object_type / object_id | TEXT | NULL | 前端跳转锚点 |
| dedupe_key | TEXT | NULL | 同键 24h 内不重复发（告警风暴抑制） |
| created_at | | 分区键 | |

索引：`(team_id, created_at DESC)`、`uq_notification_dedupe (dedupe_key, created_at::date) WHERE dedupe_key IS NOT NULL`（近似实现，服务层先查后插）。

### notification_target / notification_receipt

| 表 | 列 | 说明 |
|---|---|---|
| notification_target | notification_id, created_at(分区对齐), target_kind CHECK IN (team, role, user), target_id, PK(notification_id, target_kind, target_id, created_at) | 投递面 |
| notification_receipt | notification_id, created_at(分区对齐), user_id, read_at, PK(notification_id, user_id, created_at) | 已读回执 |

## system 系统配置域

### sys_dict 字典（运营可维护取值，D-Q11）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| dict_type | TEXT | NOT NULL | refund_reason / error_category / incident_kind_ext / risk_label… |
| code | TEXT | NOT NULL | |
| label | TEXT | NOT NULL | 中文显示 |
| sort | SMALLINT | NOT NULL DEFAULT 0 | |
| enabled | BOOLEAN | NOT NULL DEFAULT true | |
| meta | JSONB | NOT NULL DEFAULT '{}' | |
| updated_by / updated_at / created_at | | | |

约束：`uq_sys_dict (dict_type, code)`。作用域：全局。

### system_config / team_config 配置中心（禁写死参数的落点）

| 表 | 列 | 说明 |
|---|---|---|
| system_config | key TEXT PK, value JSONB NOT NULL, description TEXT, updated_by, updated_at | 全局参数：LLM 单价表、轮询退避序列、水位默认阈值、feed 批量上限… |
| team_config | team_id, key TEXT, value JSONB, updated_by, updated_at, PK(team_id, key) | 团队覆盖：llm_budget_daily_usd、gtin 水位阈值… 读取顺序 team > system > 代码默认 |

配置读取契约：服务层统一 ConfigService（带 60s 进程缓存 + 变更失效广播）；**代码里出现魔法数字即 CR 打回**（CLAUDE.md 禁区）。
