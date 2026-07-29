# R2-07c 邮箱最小闭环 —— 考古（2026-07-29）

> 工单：R2-07 第三片（07a returns ✅ / 07b 封店 ✅ / **07c 邮箱 = 模块 7 收官**）。
> 计划位置：`specs/007-mvp-completion-plan/README.md:313` 表格序 **2**，性质「小」。
> 验收判据（R2-07 acceptance ③）：**真实邮箱收一封 Walmart 通知类邮件 → LLM 自动分类
> （`llm_usage.module=mail_classify`）→ 生成 `store_incident` + 告警落库，全链可追溯。**
> 范围闸（D-Q22）：**会话线程 / 模板 / 附件 / 回信 = MVP 后第一批，本单不做。**

---

## 一、图纸（`001-domain-model/09-platform.md:76` mail 域）

四张表，本单需要的是前两张 + 第四张；`mail_outbox` 属"发信"，图纸有但**本单不做**
（D-Q22 把回信划到 MVP 后）——建表与否见 §四「待定一条」。

| 表 | 用途 | 本单 |
|---|---|---|
| `mailbox` | 店铺邮箱（IMAP/SMTP 连接参数 + `password_encrypted`） | **建** |
| `mail_message` | 邮件元数据永留、正文 30 天（D-Q17） | **建** |
| `mail_outbox` | 发件 | 见 §四 |
| `mail_rule` | 分类规则（全局种子 + 可关，运营可维护 D-Q11） | **建** |

关键约束（图纸原文）：
- `uq_mail_message (mailbox_id, message_uid)` —— **IMAP UID 即幂等键**，重复收件不重复入库；
- 索引 `(team_id, classification, received_at DESC)` 与 `(purge_after) WHERE body_ref IS NOT NULL`；
- `classification ∈ (none, suspension, listing, order, payment, other)`，由 `mail_rule` 命中 + **LLM 兜底**；
- `body_ref` 落盘引用，**30 天后清理任务删文件并置 NULL**；
- **`suspension` 类的正文已转存 `store_incident.mail_body_snapshot` 永留** —— 即清理任务不会
  让封店证据消失，这是 D-Q17（正文不长留）与取证需求的交点，**实现时不能漏**。

---

## 二、可直接复用的既有基建（**四样里三样现成**）

### ✅ 凭证加密：`pgp_sym_encrypt` + `Settings.credential_key`

模板在 `channel/service.py:26`：

```sql
INSERT INTO app.store_credential (...) VALUES (:sid, :cid, pgp_sym_encrypt(:sec, :key), :by)
```

密钥来自 `Settings.credential_key`，**永不入库**。`mailbox.password_encrypted` 照此办理，
不另造通道（铁律 5：凭证只走加密存储）。

### ✅ LLM 计费：`mail_classify` 的 CHECK 约束**已经允许**

`0008_audit_compliance.py:210` 建 `llm_usage_log` 时 `module` 的 CHECK 就写了
`('audit','category_map','mail_classify','other')`，`0020` 扩到含 `listing` 时也保留着。
**即分类调用的计费落账零迁移**，直接 `log_usage(module="mail_classify", ...)`。

### ✅ 告警：`notify/service.py:20` 的 `notify()` + `dedupe_key`

去重模板见 `automation/tasks.py:821`（查 `notification.dedupe_key` 是否已存在，**无时间窗**）。
封店告警按 `(store_id, incident_id)` 派生 dedupe_key 即可，重复收到同一封信不重复告警。

### ✅ `store_incident` 的接点**是预留好的**

`0003_channel.py:190` 建表时就有：

| 列 | 用途 |
|---|---|
| `source text CHECK IN ('mail','manual')` | **`'mail'` 这个取值一直在，等的就是本单** |
| `mail_message_id bigint` | 反指邮件 |
| `mail_body_snapshot text` | 封店正文永留（对冲 30 天清理） |

**无需迁移改 `store_incident`。**

### ❌ 唯一缺口：`mail_poll` 的 beat 种子

全仓 `mail_poll` 零命中。`schedule` 表（`0004_system.py:78`）的列已具备
（`code/description/cron/timezone/enabled/config`），种子加法有三个现成模板可抄：
`0033_brand_assignment.py`、`0036_trademark_freshness_schedule.py`、`0037_item_pull_schedule.py`。

### ✅ IMAP 依赖：**不需要新增第三方包**

`backend/pyproject.toml` 里没有任何 imap 相关依赖，但 **Python 标准库 `imaplib` + `email`
已实测可用**（沙箱 `import imaplib, email` 通过）。收件是同步阻塞 IO，需在 beat 任务里
用 `asyncio.to_thread` 包一层，不要在事件循环里直接跑。

---

## 三、**本单最要紧的一处架构问题**：incident 创建逻辑内联在 router 里

`channel/router.py:513` 的 `create_incident` **把三件事写在了一个端点函数里**：

1. `INSERT INTO app.store_incident (... source) SELECT ..., 'manual', ...` —— **`'manual'` 写死**；
2. `incident_kind == 'suspension'` 时的封店联动：`UPDATE app.store SET status='suspended'`；
3. `await brand.release_for_incident(session, row)` —— 品牌占用批量释放。

邮件路径要建的是 `source='mail'` + 带 `mail_message_id`/`mail_body_snapshot` 的 incident，
**且必须触发同一套封店联动**（否则「邮件自动开的封店事件不释放品牌占用」，而人工开的会——
两条路径行为分叉，且分叉方向是**少做了释放**，属静默漏做）。

**若照抄一份到 mail 任务里，就是重演三档内核当初要消灭的东西**——`core/automation.py`
的模块头注写得很清楚：面板与订单闸各算一份判读，就会出现「面板显示 auto、闸实际按 manual 跑」
而无人发现。这里同形：**人工开的 incident 释放品牌、邮件开的忘了释放，而没有任何判据会红。**

**结论：本单第一步是把 incident 创建抽成共用服务函数**（`channel/incident.py` 或
`channel/service.py` 内），签名带 `source` / `mail_message_id` / `mail_body_snapshot`，
router 与 mail 任务**共用同一份**。抽取时行为对现有 router 路径**必须逐字等价**
（`source='manual'`、审计事件名 `channel.incident_create` 不变）。

配套判据：一条测试断言**两条路径产生的封店联动一致**（都置 store.suspended、都
`brand_released_at` 非空），而不是只测新路径——**只测新路径挡不住「后来有人只改了一边」**。

---

## 四、待定一条（不阻塞开工，实现到那步前定即可）

**`mail_outbox` 建不建表？** D-Q22 要求"收发聚合"，但本单范围闸把回信划到 MVP 后。
两个选项：

- **(a) 本单只建 `mailbox` / `mail_message` / `mail_rule` 三张**，`mail_outbox` 随 MVP 后
  的回信单一起建 —— 好处是不留空表，坏处是那时要再动一次迁移；
- **(b) 三张 + `mail_outbox` 一起建，本单不写任何读写代码** —— 好处是图纸一次落全，
  坏处是**留一张零消费点的空表**，而本项目刚因为「契约声明了端点却没建」立了
  `CONTRACT_AHEAD_OF_CODE` 白名单来治理同类问题。

**云端侧倾向 (a)**：与「002 契约已声明但端点未建」被当成欠账治理是同一个道理——
空表同样是「图纸说有、代码没有」的另一种形态，且 `mail_outbox` 没有 CI 判据会盯着它。
**若审计侧认为图纸应一次落全，改 (b) 也行，说一声即可。**

---

## 五、增量拆分（每个增量一个原子目标，逐个过四闸）

| 增量 | 内容 | 为什么单独成增量 |
|---|---|---|
| **07c-1** | 抽 incident 创建为共用服务 + 两路径一致性判据 | **行为零变化的重构**，先落地才能让后面的接线无分叉风险；单独成增量便于审查核"逐字等价" |
| **07c-2** | mail 域迁移（三表 + 约束/索引 + `mail_rule` 全局种子 + `mail_poll` schedule 种子） | 迁移单独一刀，便于 up/down 演练 |
| **07c-3** | `mail_poll` beat：IMAP 收件（`asyncio.to_thread`）+ 按 `(mailbox_id, message_uid)` 幂等入库 + 正文落盘 + `purge_after` 回填 | 纯收件，不含分类，先把"信能进来"这件事验干净 |
| **07c-4** | 分类：`mail_rule` 命中 → LLM 兜底（`module=mail_classify` 计费）→ `suspension` 命中开 incident（走 07c-1 的共用函数）+ 正文快照 + 告警 | 本单验收判据所在 |
| **07c-5** | 正文 30 天清理任务（D-Q17）+ 前端最小承接（邮件列表/分类可见）+ 工单回写 | 清理任务必须与"封店正文已转存"一起验，否则会删掉取证 |

**风险最高的是 07c-4 的 LLM 兜底**：分类会真花钱，且是**每封邮件**都可能触发。
实现时须先走 `mail_rule` 规则命中（零成本），规则未命中才落 LLM —— 这个顺序不是优化，
是成本护栏（对照 RS-08 LLM 预算闸尚未落地，现在只有告警版）。

---

## 六、开工前已确认的事实清单（供审查复算）

| 声称 | 复算命令 |
|---|---|
| `store_incident` 已有 mail 接点，无需迁移 | `grep -n "ck_incident_source\|mail_message_id\|mail_body_snapshot" backend/alembic/versions/0003_channel.py` |
| `mail_classify` 计费零迁移 | `grep -n "mail_classify" backend/alembic/versions/0008_audit_compliance.py` |
| `mail_poll` 未种子 | `grep -rn "mail_poll" backend/` → 空 |
| incident 创建逻辑内联在 router | `sed -n '513,555p' backend/src/erp/channel/router.py` |
| 无 imap 第三方依赖，用标准库 | `grep -nE "imap" backend/pyproject.toml` → 空 |
