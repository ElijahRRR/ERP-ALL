# R2-07 07b 封店工作流 · 侦察方向 A（规格图纸与决策）逐条抄录

所有引用为 `文件路径:行号`。仅收录与 07b（store_incident + 品牌占用批量释放 + beat 定时提醒）直接相关的硬事实。

---

## 1. store_incident 店铺事件表 —— 列级图纸（§02 店铺域）

出处：`/home/user/ERP-ALL/specs/001-domain-model/02-channel.md:162-193`（标题「store_incident 店铺事件（封店工作流，D-Q33）」在 :162）。**逐列抄录**（:164-180）：

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | :165 |
| team_id | BIGINT | NOT NULL | :166 |
| store_id | BIGINT | NOT NULL REFERENCES store | :167 |
| incident_kind | TEXT | NOT NULL CHECK IN (suspension, warning, listing_block, other) | :168 |
| source | TEXT | NOT NULL CHECK IN (mail, manual) | 邮件识别 or 人工标记 :169 |
| mail_message_id | BIGINT | NULL | → mail_message（09），邮件识别时回链 :170 |
| occurred_at | timestamptz | NOT NULL | 封店时间 :171 |
| reason | TEXT | NULL | 封店原因（邮件抽取或人工填） :172 |
| mail_body_snapshot | TEXT | NULL | 封店邮件正文转存（正文本体 30 天清，此处永留） :173 |
| status | TEXT | NOT NULL DEFAULT 'open' CHECK IN (open, observing, appealing, resolved, closed) | observing=观察放款 :174 |
| sku_released_at | timestamptz | NULL | 该店 SKU 全量释放完成时间（catalog 域动作回填） :175 |
| brand_released_at | timestamptz | NULL | 品牌占用释放完成时间 :176 |
| appeal_notes | TEXT | NULL | 申诉记录（refdata.suspension_case 75k 案例库辅助写信） :177 |
| closed_at | timestamptz | NULL | :178 |
| +公共列 | | | :179（约定见 00-conventions.md，未在本报告展开） |

**索引**（:182）：`(team_id, status)`、`(store_id, occurred_at DESC)`。

**工作流联动（服务层编排，注明 R2#7 落地）**（:183-187）逐条：
1. 创建 suspension 事件 → `store.status=suspended` + `suspended_at` 回写（:184）；
2. 触发释放作业：**brand_assignment 释放 + listing 停止维护 + gtin 保持 used（不回收，防重用关联）**（:185）；
3. automation 域 schedule 定时提醒（观察放款/写申诉信）→ notification（:186）；
4. `resolved` → `store.status` 恢复**由人工确认，不自动**（:187）。

**状态枚举要点**：
- `incident_kind` 4 值：`suspension / warning / listing_block / other`（:168）——只有 `suspension` 触发封店联动。
- `status` 5 值生命周期：`open → observing(观察放款) → appealing → resolved → closed`（:174）；DEFAULT `open`。
- `source` 2 值：`mail`（邮件识别，07c 生产者）/ `manual`（人工标记，07b 验收演练走此路）（:169）。

**关联字段（07b 需与 store 联动的落点）**——store 表侧回写字段：
`/home/user/ERP-ALL/specs/001-domain-model/02-channel.md:26`：`store.status` CHECK IN (active, paused, suspended, closed)，注「**suspended 由 store_incident 联动**」；`:34`：`suspended_at / closed_at timestamptz NULL`，注「**与 store_incident 联动回写**」。

---

## 2. brand_assignment 品牌店铺占用表 —— incident_id 回链与释放语义（§03 catalog）

出处：`/home/user/ERP-ALL/specs/001-domain-model/03-catalog.md:72-90`（标题「brand_assignment 品牌店铺占用」在 :72）。**逐列抄录**（:74-86）：

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | :75 |
| team_id | BIGINT | NOT NULL | :76 |
| brand_norm | TEXT | NOT NULL | :77 |
| brand_display | TEXT | NOT NULL | :78 |
| store_id | BIGINT | NOT NULL REFERENCES store | :79 |
| status | TEXT | NOT NULL DEFAULT 'occupied' CHECK IN (occupied, released) | :80 |
| assigned_at | timestamptz | NOT NULL DEFAULT now() | :81 |
| released_at | timestamptz | NULL | :82 |
| release_reason | TEXT | NULL CHECK IN (suspension, manual, store_closed) | :83 |
| **incident_id** | BIGINT | **NULL REFERENCES store_incident** | **封店释放回链（D-Q33）** :84 |
| +公共列 | | | :85 |

**incident_id 回链定义**（:84）：`BIGINT NULL REFERENCES store_incident`，注释「封店释放回链（D-Q33）」——即 07b 品牌批量释放时把每条被释放的 brand_assignment 行 `incident_id` 指向触发它的 store_incident.id。

**released 状态语义 / 批量释放规则**：
- 关键唯一约束（:88）：**`uq_brand_occupied (team_id, brand_norm) WHERE status='occupied'`** —— 一个品牌团队内**同一时刻只占用一店**（防跨店关联同品牌）；**历史占用记录保留**（released 行不删，只翻状态）。
- 分配/释放时机（:89）：「build 上架分配店铺时自动 upsert（同店已占则通过、异店占用则拒绝并出 compliance_hit 类警示）；**封店工作流批量 released**」。
- 释放动作语义推断（由 :80/:82/:83/:84 组合）：批量释放 = `status: occupied→released` + `released_at=now()` + `release_reason` 取 `suspension`（封店）或 `store_closed`（关店）或 `manual` + `incident_id=<本次事件>`。**注意**：`release_reason` 的 CHECK 值（`suspension/manual/store_closed`）与 store_incident.incident_kind（`suspension/warning/listing_block/other`）是两套不同枚举，07b 映射时勿混用（未在图纸中给出显式映射表，**映射规则未核实**，需服务层自定）。
- 释放后的 partial unique 效果：因 `WHERE status='occupied'`，released 行不占唯一槽，释放后该 (team_id, brand_norm) 可被别店重新 occupied。

---

## 3. DECISION-FORM.md 决策原文

出处：`/home/user/ERP-ALL/specs/000-founding/DECISION-FORM.md`

### D-Q33（封店工作流 + 店铺档案）—— 核心驱动
- 原始提问（:99）：「**Q33 店铺档案**：店铺导入时有哪些资料要管（凭证、代理 IP、邮箱、收款账户…）？**封店后的标准处理流程是什么（下架、归档、资金追踪）**？」
- **决策原文**（:122）：「D-Q33 | 店铺档案字段：凭证/代理/邮箱+IMAP·SMTP/收款账户/上下架限制/调价比例/品牌·SKU去重开关（可扩展）。**封店工作流**：记录封店时间 + 原因（人工填或**邮件自动识别**）→ **释放该店全部 SKU 与品牌占用** → **定时提醒团队观察放款或写申诉信** | store 档案 = 扩展属性模型；封店 = 状态机+自动化工作流（联动 mail 模块 D-Q22、品牌占用释放、定时提醒）；申诉信管理进 backlog」。
- backlog 增补条目（:132）：「10. **封店工作流**（D-Q33）——邮件识别封店 → 释放 SKU/品牌 → 定时提醒 → 申诉信管理。」

**D-Q33 拆解为 07b 三个硬需求**：① 记录封店（时间+原因，人工/邮件双源）；② **释放该店全部 SKU 与品牌占用**；③ 定时提醒（观察放款 / 写申诉信）。「申诉信管理」明确进 backlog（不在 07b 范围）。

### D-Q22（内置邮箱模块 —— store_incident 的生产者，07c 用，07b 需预留接口）
- 原文（:71）：「D-Q22 | **ERP 内置邮箱模块**…Walmart 通知类邮件（封店/合规/绩效）自动分类 → **生成店铺事件（store_incidents 的生产者）** + 站内告警。邮箱凭证随店铺导入时配置、加密存储 | 新增 mail 域模块；建议邮件接收+店铺关联+事件识别进 MVP…与 D-Q17（砍系统报告邮件）不冲突」。

### 其它与店铺事件相关的决策
- D-Q30（:119）：「店铺**团队独占**…」——store_incident 带 team_id（:166 图纸对应）。
- D-Q31（:120）：去重键 = (team_id, asin) + store 级豁免——与品牌/SKU 释放的隔离边界相关。
- D-Q36（:125）：「测试与验收店 = **A152**」——07b 演练/验收在 A152。
- 无其它独立的「封店/incident」决策条目（grep `Q33|封店|店铺档案|store_incident|incident` 仅命中 :71/:99/:122/:132/:159，:159 为 Q43 迭代顺序，非规则）。**其它 incident 决策：未核实到额外条目**。

---

## 4. business-rules-ledger.md 相关规则条目（BR-*）

出处：`/home/user/ERP-ALL/specs/000-founding/business-rules-ledger.md`

- **BR-CAT-004**（:46）：「**品牌唯一店铺策略:同一品牌绑定单店（brand assignment 占用/释放）→ 支撑"同 ASIN 只在一店"与 SKU 全局唯一假设** | ✅ | erp-core listings.py `_release_brand_assignment`」——**旧系统已有释放函数 `_release_brand_assignment`（erp-core listings.py），07b 品牌批量释放的旧实现锚点**（本报告为 ERP-ALL 只读侦察，未核实旧仓 erpAPI 内该函数具体代码）。
- **BR-EAI-004**（:274）：「**店铺状态门控**：上架侧每次运行直读店铺状态表（不依赖小时级缓存，防封店滞后感知半天）；fail-open——读失败或店不在表视为可上架 + WARNING，仅明确非 ACTIVE 才跳过 | ✅ | auto_listing/store_status.py」——封店后店铺状态须被上架侧实时感知的旧规则；对应新系统 store.status=suspended 联动（§02:26）。
- **BR-GW-001**（:15）：「所有 Walmart API 调用必经统一客户端；禁止直连…每个卖家账号绑定固定出口代理 IP，直连触发店铺关联风险（**封店级后果**）」——封店风险的根因规则（防关联），与 §02 代理独占约束呼应。
- **BR-ST-002**（:33）：「生产店铺规模 ~57 家；**店铺状态（sellerStatus）每小时同步**」——旧系统店铺状态同步节奏。

无以「BR-」编号、专写「品牌批量释放/store_incident 触发链」的独立条目（grep `品牌|_release_brand|封店|suspension|released` 仅命中上列 + 一批与释放无关的 BR-CAT-10x 类目映射条目）。**07b 特有的批量释放规则在 ledger 中未单列，权威在 §02:183-187 工作流联动图纸与 D-Q33 原文**。

---

## 5. §09 mail 域 —— store_incident 生成钩子（07c 用，07b 需预留接口形状）

出处：`/home/user/ERP-ALL/specs/001-domain-model/09-platform.md`

**mail_message 表回链字段**（:108）：`incident_id | BIGINT | NULL | → store_incident（suspension 自动开事件，02 号文档）`——即邮件识别为 suspension 时**自动开 store_incident 并双向回链**（store_incident.mail_message_id ←→ mail_message.incident_id）。

**正文转存钩子**（:109）：`body_ref … 清理任务 30 天后删文件并置 NULL（D-Q17 落地；**封店正文已转存 incident.mail_body_snapshot 永留**）`——07b 的 `store_incident.mail_body_snapshot`（§02:173）是封店邮件正文的永久留存点，07c 生成 incident 时须把正文快照写入此列（因 mail 正文 30 天清）。

**mail_message.classification 枚举**（:107）：`NOT NULL DEFAULT 'none' CHECK IN (none, suspension, listing, order, payment, other)`，注「mail_rule + LLM 兜底分类」——`suspension` 是触发开 incident 的分类值。

**mail_rule 分类规则表**（:129-139）：
- `rule_kind CHECK IN (suspension_detect, classify)`（:134）；
- `pattern JSONB {from_domain, subject_regex, body_keywords}`（:135）；
- `action JSONB {classification, open_incident: bool}`（:136）——**`open_incident` 布尔即「是否自动开 store_incident」的开关**，07b 应把「创建 incident」做成可被此 action 调用的服务接口形状（source=mail、incident_kind=suspension、回链 mail_message_id、写 mail_body_snapshot）。

**接口形状总结（07b 需预留，供 07c 调用）**：`create_incident(store_id, incident_kind='suspension', source='mail', mail_message_id=<id>, reason=<抽取>, mail_body_snapshot=<正文>, occurred_at=<邮件时间>)` → 内部触发 §02:183-187 联动。07b 的验收②走 `source='manual'` 手工造 incident 演练同一联动链。

---

## 6. 关键补充事实（beat 定时提醒 / 案例库 / 通知 / 字典）

这些不属图纸主表，但 07b「beat 定时提醒」与联动落点直接依赖。

### beat 定时提醒的配置落点（automation 域）
`/home/user/ERP-ALL/specs/001-domain-model/09-platform.md:166`（automation_policy flow_code 注册清单内）：`suspension_reminder | 封店提醒节奏（D-Q33） | remind_days`——**封店提醒是 automation_policy 的一个 flow_code，团队级三档开关（manual/semi/auto，:150），config 关键项 = `remind_days`**。约束 `uq_automation_policy (team_id, flow_code)`（:155）。

### ⚠️ 缺口：schedule 种子尚未登记 suspension_reminder
schedule 已登记种子清单（:184-190）逐项为：`partition_maintain / scrape_reclaim / api_idempotency_sweep / llm_cache_lru / feed_poll / feed_verify_back / channel_outbox_drain / retire_recon / gtin_watermark / llm_budget_check / order_pull / ship_recon`——**不含封店提醒任务**。schedule.code 示例串（:173）列了 `order_pull / mail_poll / settlement_pull / …` 也**未含封店提醒**。故 07b「挂 R2-04 beat」需**新增一个 schedule 种子行（新 code，如 suspension_reminder）+ 在 `erp.automation.tasks.TASKS` 注册表登记任务**（未注册 code → task_run failed + notify，:188-189）。beat 机制：读表驱动、tick 默认 30s（`beat.tick_seconds`）、单语句乐观领取、配置失效经 Redis pubsub `erp:config:invalidate` 广播（:184-190）。**该 schedule 种子当前不存在，属 07b 待建，已核实缺口。**

### 提醒产出通道 notification
`/home/user/ERP-ALL/specs/001-domain-model/09-platform.md:217`：notification.category 示例含 `store_incident`——封店提醒/事件告警的通知归此 category。`dedupe_key` 同键 24h 内不重复（:221），提醒风暴抑制可用。

### 申诉弹药案例库（appeal_notes 关联的检索源）
`/home/user/ERP-ALL/specs/001-domain-model/04-compliance.md:125-140`：`refdata.suspension_case`（75,703 行，D-Q33 申诉弹药），列含 `reason_category / reason_text / appeal_text / outcome / embedding vector(1024)`；用途（:140）「**store_incident 处理页「相似案例」检索 + 申诉信参考**」。对应 store_incident.appeal_notes（§02:177）注释「refdata.suspension_case 75k 案例库辅助写信」。**申诉信管理/检索属 backlog（D-Q33），07b 只需保留 appeal_notes 列，不建检索。**

### incident_kind 扩展位（字典）
`/home/user/ERP-ALL/specs/001-domain-model/09-platform.md:240`：sys_dict.dict_type 示例含 `incident_kind_ext`——incident_kind 的运营可扩展取值走 sys_dict（图纸 CHECK 4 值 + 字典扩展，D-Q11 禁写死）。

---

## 7. 旧系统 store_incidents 表（考古锚点，跨参考）

R2-07 计划点名的考古锚点：`/home/user/ERP-ALL/specs/007-mvp-completion-plan/README.md:45-47`「07b 封店工作流：store_incident（001 §02 图纸）+ 品牌占用批量释放（brand_assignment.incident_id 回链已预留，§03）+ 定时提醒（挂 R2-04 beat）。**考古锚点：旧 erp_core store_incidents 表（data-survey schema 在档）**。」验收②（:54-55）：「手工造 incident 演练封店→品牌占用批量 released→beat 提醒送达」。

旧表 DDL：`/home/user/ERP-ALL/specs/000-founding/data-survey/out/pg_erp_core_schema.sql:1258-1277`，逐列：
`id varchar(64) NOT NULL`（**注意旧表 PK 是字符串 id，新表改 BIGINT identity**）、`store_id integer`、`store_name varchar(128) NOT NULL`、`cluster varchar(32)`、`tier varchar(32)`、`kind varchar(16) NOT NULL`、`reason_code varchar(64)`、`reason text`、`poa_status varchar(32)`、`poa_text text`（POA=Plan of Action 申诉计划）、`next_appeal date`、`fund_pending boolean`、`fund_amount double precision`、`fund_appeal_date date`、`resolved boolean`、`resolved_at timestamptz`、`created_at timestamptz NOT NULL`、`updated_at timestamptz DEFAULT now()`。
索引（:3576/3583/3590）：`ix_...kind`、`ix_...resolved`、`ix_...store_name`。FK（:4024-4025）：`store_id → stores(id)`。

**旧表 vs 新图纸差异（07b 建表时注意）**：
- 旧有、新图纸**无**的语义列：`cluster/tier`（店铺分群/分级）、`poa_status/poa_text/next_appeal`（申诉计划——新系统对应 appeal_notes + suspension_case 库 + backlog 申诉管理）、`fund_pending/fund_amount/fund_appeal_date`（**资金追踪/放款**——新图纸只有 status=`observing`「观察放款」枚举，**未建资金字段**；D-Q33 原问提到「资金追踪」，新图纸未落列，属 backlog 或走 finance 域，**新系统资金追踪落点未核实**）。
- 新有、旧表**无**：`team_id`（多租户）、`source`（mail/manual 双源）、`mail_message_id`、`mail_body_snapshot`、`sku_released_at`、`brand_released_at`（**释放完成时间戳，是 07b 释放作业的回填点**）、`appeal_notes`。
- 数据量：`pg_erp_core_rowcounts_exact.txt` 显示旧 store_incidents **3 行**（rowcounts.txt 抽样为 0）——**几乎空表，D-Q35「不迁旧表、重新拉取」口径下无迁移压力**。

---

## 一句话结论（07b 落地要点）
1. 建 `store_incident`（§02:162-193 图纸为准，14 列 + 公共列，两索引），改旧表 varchar id→BIGINT，**丢弃 cluster/tier/poa/fund 系列**（资金追踪未落列）；
2. 封店服务编排 §02:183-187 四步联动：开 suspension → store.status=suspended+suspended_at → 批量释放 brand_assignment（status→released、released_at、release_reason、**incident_id 回链**、回填 brand_released_at）+ listing 停维护 + gtin 保 used → beat 提醒 → resolved 人工恢复；
3. **新增 beat schedule 种子（当前未登记）**+ automation_policy `suspension_reminder`（config.remind_days，三档）+ 注册 `erp.automation.tasks.TASKS`；提醒经 notification（category=store_incident，dedupe 24h）；
4. 预留 `create_incident(source=mail…)` 接口形状供 07c 邮件钩子（mail_rule.action.open_incident / mail_message.incident_id / mail_body_snapshot）调用，07b 自身验收走 source=manual 手工演练；
5. 旧释放函数锚点 `erp-core listings.py _release_brand_assignment`（BR-CAT-004），需在 erpAPI 侧核实其批量语义（本次 ERP-ALL 只读侦察未展开）。