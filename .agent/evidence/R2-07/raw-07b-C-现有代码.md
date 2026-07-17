## R2-07 07b「封店工作流」考古报告（方向 C：新系统 backend/ 现有代码触点）

### 结论速览（给实现单的 5 个硬判断）
1. **`store_incident` 表已存在**（0003 建），封店 API 与状态机已落地，但 SKU/品牌释放作业是**明确挖的坑**——`channel/router.py:537` 注释写死「随 R2#7 catalog 联动接入」，07b 就是来填这个坑的。
2. **`brand_assignment` 表从未建过**。图纸 `03-catalog.md:72-89` 定义了它（含 `incident_id` 回链在 :85），但**零迁移、零服务层代码**触碰它。任务说的「回链已预留」是**图纸预留、非 DB 预留**——07b 必须新建整张表。
3. **迁移号 0033 空闲**（当前最大 0032_variant_anchor.py，已核实）。
4. **通知/beat 基建齐备**，07b 提醒任务直接抄 `gtin_watermark` 告警任务范式 + `notify()` dedupe，不用造轮子。
5. **权限点 `channel.incident_write` 已种并已挂团队管理员**，品牌释放属封店联动，可直接复用，无需新权限点（若要独立点，抄 0030/0031 范式）。

---

### 1. brand_assignment 现状 —— 不存在，必须新建

- **无任何迁移建此表**：`grep brand_assignment alembic/versions/*.py` 仅命中 0007 的文件头注释（`0007_scrape_catalog.py:1`，那是 product/variant_group/variant_member），无 CREATE TABLE。0007 实际建的 catalog 表只有 `product`(`:50`)、`variant_group`(`:87`)、`variant_member`(`:99`)。
- **无服务层读写**：`grep brand_assignment` 全 `src/erp/` 无命中；`channel/service.py` 只有 `release_quota`(`:142`)，无品牌逻辑。
- **图纸（宪法级）完整定义**在 `specs/001-domain-model/03-catalog.md:72-89`：
  - 列：`team_id / brand_norm / brand_display / store_id→store / status DEFAULT 'occupied' CHECK IN (occupied,released) / assigned_at / released_at / release_reason CHECK IN (suspension,manual,store_closed) / incident_id NULL REFERENCES store_incident`（回链在 `:85`）+ 公共列。
  - 关键约束 `03-catalog.md:88`：`uq_brand_occupied (team_id, brand_norm) WHERE status='occupied'`（团队内一品牌同时刻只占一店）。
  - 分配时机 `03-catalog.md:89`：build 上架时 upsert；**封店工作流批量 released**。
- **`released` 语义**：图纸已定义（occupied/released 二态 + release_reason 三值），但代码层零实现——07b 从头写。
- 旧系统参照（方向 C 之外，仅指路）：`specs/000-founding/business-rules-ledger.md:46` BR-CAT-004 记旧仓有 `erp-core listings.py _release_brand_assignment`，可对拍语义。

### 2. store_incident 表现状 —— 已存在，状态机已跑，释放作业缺位

- **建表**：`alembic/versions/0003_channel.py:190-216`。关键列：
  - `incident_kind` CHECK IN (`suspension,warning,listing_block,other`)（`:194-196`）
  - `source` CHECK IN (`mail,manual`)（`:197-198`）、`mail_message_id`(`:199`)、`mail_body_snapshot`(`:202`，封店邮件正文永留)
  - `status` DEFAULT 'open' CHECK IN (`open,observing,appealing,resolved,closed`)（`:203-205`）
  - **`sku_released_at`（`:206`）、`brand_released_at`（`:207`）已预留时间戳列**——07b 释放完成后回填这两列。
  - `appeal_notes`(`:208`)、`closed_at`(`:209`)
  - 索引 `ix_incident_team (team_id, status)`(`:214`)、`ix_incident_store (store_id, occurred_at DESC)`(`:215`)；TEAM_RLS + touch 触发器(`:218-219`)。
- **API 已全落地** `src/erp/channel/router.py`：
  - `IncidentWrite`/`IncidentOut` 模型（`:104-121`，`IncidentOut` 已含 `sku_released_at`/`brand_released_at`/`appeal_notes`）
  - `GET /store-incidents` 列表（`:481-508`）、`POST /store-incidents` 建单（`:511-548`）、`POST /store-incidents/{id}/transition` 状态流转（`:556-589`）
  - **封店联动已接一半**：建单时 `incident_kind=='suspension'` → `store.status='suspended', suspended_at=oa`（`:536-541`）；resolved 流转 → `store.status='active'`（`:577-582`）。
  - **07b 的落点就在这里**：`:537` 注释原文「封店联动：店铺状态 + 时间戳（**SKU/品牌释放作业随 R2#7 catalog 联动接入**）」。品牌批量 released + 回填 `brand_released_at` 应挂进 `create_incident` 的 suspension 分支（`:536` 后）。
  - 状态流转 `transition` 用 `IncidentTransitionIn.to_status` CHECK `^(observing|appealing|resolved|closed)$`（`:551-552`）。
- 图纸释放动作清单 `specs/001-domain-model/02-channel.md:185`：「brand_assignment 释放 + listing 停止维护 + gtin 保持 used（不回收）」。

### 3. 通知/告警 + beat 基建 —— 07b 直接复用哪条路径

- **通知唯一入口** `src/erp/notify/service.py:20` `notify(session, *, team_id, severity, category, title, body, object_type, object_id, dedupe_key, targets)`：
  - dedupe：同 `dedupe_key` 24h 内只发一条（`:34-45`）；targets 缺省=全团队（`:68-70`）。07b 提醒必走此入口，不自插表。
- **beat 提醒任务范式 = `gtin_watermark`**（`src/erp/automation/tasks.py:593-657`，告警类，单 `system_tx` 聚合 + `notify` + dedupe）。`suspension_reminder` 照抄此结构最省事。
  - 契约（`tasks.py:1-14, 42`）：`async (sessions, config) -> stats dict`；config = `schedule.config` jsonb（提醒节奏参数落这里，零硬编码）。
  - 07a 兄弟任务 `return_pull` 已注册（`tasks.py:713-715` 定义、`:854` 注册），可作「售后域 beat 任务」结构参照。
- **任务注册表**：`tasks.py:840-856` 的 `TASKS: dict[str, TaskFn]`——07b 必须把 `suspension_reminder` 函数加进这个 dict（未注册值 beat 记失败不静默跳过，`:9`）。
- **schedule 种子怎么加**：
  - schedule 表 `alembic/versions/0004_system.py:77-79`（`code text NOT NULL UNIQUE`）。
  - 种子范式（`0022_beat_seeds.py:31-45` / `0024_beat_alert_seeds.py:20-32` / `0030:156-166`）：`INSERT INTO app.schedule (code, description, cron, config) VALUES (...) ON CONFLICT (code) DO NOTHING;`，downgrade 反向 DELETE。
  - **提醒节奏配置键有图纸依据**：`specs/001-domain-model/09-platform.md:166` automation_flow `suspension_reminder` → config 项 `remind_days`（D-Q33）。**已核实 backend 中 `suspension_reminder`/`remind_days` 零命中**（`grep src/ alembic/` 无结果），即种子 + 任务 07b 全新加。

### 4. 迁移编号现状 —— 0033 空闲（已核实）

- `alembic/versions/` 最大 = **`0032_variant_anchor.py`**。0033 空闲。
- 注意：图纸/CLAUDE 里写的 `backend/migrations/versions/` 目录**不存在**，实际迁移在 **`backend/alembic/versions/`**。

### 5. 权限点种子模式 —— 品牌释放可复用既有点，如需新点抄 0030/0031

- **`channel.incident_read` / `channel.incident_write` 已种**（`0002_identity.py:275-276`）**且已挂「团队管理员」**（`0002_identity.py:338-339`）。品牌批量释放是封店（suspension）联动，走 incident 权限即可，无需新权限点。
- 若要独立权限点（如 `catalog.brand_release`），两套现成范式：
  - **单点**（`0030_aftersale_returns.py:141-153`）：`INSERT INTO app.permission ... ON CONFLICT (code) DO NOTHING` + `INSERT INTO app.role_permission SELECT r.id, 'code' FROM app.role r WHERE r.name IN (...) AND NOT EXISTS (...)`——挂模板角色与既有团队同名复制角色。
  - **多点**（`0031_refund_request.py:84-98`）：两个 perm 一次种，用 `JOIN (VALUES ('角色','点')...) AS p(role_name, code) ON r.name = p.role_name` + NOT EXISTS 守卫。
  - downgrade 均反向 DELETE role_permission → permission（`0030:171-172` / `0031:120-123`）。
- 权限点全集在 `0002_identity.py:262-299`（perms 列表），模板角色→权限映射在 `:304-345`（`role_perm` dict）。

### 6. tests/db/ fixture 模式 —— 07b 测试照抄哪两个文件

- **基座 conftest** `tests/db/conftest.py`：
  - `migrated_db`（session 级，`command.upgrade(Config("alembic.ini"),"head")`，`:45-60`）、`team_ids`（`:63-69`）、`app_conn`（RLS 生效连接，`:72-77`）。
  - 需真实 PostgreSQL，不可达则整目录 skip（`:41-42`）。
- **beat 任务测试范式 = `tests/db/test_beat_alerts.py`**（`suspension_reminder` 测试照此）：
  - 用 `psycopg.connect(migrated_db, autocommit=True)` 直插种子（`:35-49`）；
  - **直调任务函数**：`await tasks.gtin_watermark(get_session_factory(), {})`（`:69`，import 在 `:11-12`）；
  - 断言 `app.notification` 行（severity + dedupe_key，`:52-57`, `:72-75`）；
  - 健康态零打扰断言（`:75` `== []`）——07b 应有「未到提醒周期不发」的对称用例。
- **封店 API 测试范式 = `tests/db/test_channel_api.py`**（品牌释放端点/联动照此）：
  - `TestClient(create_app())` fixture（`:86-98`，含 `get_settings.cache_clear()`/`get_session_factory.cache_clear()`）；
  - 种子：建团队 + 复制模板「团队管理员」角色并挂全权限 + 建用户（`:19-83`），登录用 `_login`/`PASSWORD`（import 自 `test_identity_api`，`:13`）；
  - **既有封店联动测试** `test_incident_suspension_links_store`（`:212-240` 区段）：`POST /api/v1/store-incidents`（`incident_kind:"suspension"`，`:220-223`）→ `POST /store-incidents/{rid}/transition`（`:233`）→ 断言 store 状态。07b 应在此测试基础上扩断言：suspension 后 brand_assignment 批量 released + `brand_released_at` 回填。
  - 清理示例含 `DELETE FROM app.store_incident WHERE team_id=%s`（`:77`），07b 新表清理照加。

---

### 给实现单的落点清单（方向 C 汇总）
| 工件 | 文件:行 | 07b 动作 |
|---|---|---|
| brand_assignment 建表 | 图纸 `03-catalog.md:72-89` | 0033 新建（含 `incident_id→store_incident` 回链、`uq_brand_occupied` 部分唯一） |
| 品牌批量释放挂载点 | `channel/router.py:536-541`（`:537` 坑注释） | suspension 建单分支内批量 released + 回填 `store_incident.brand_released_at` |
| store_incident 复用列 | `0003_channel.py:206-207` | `sku_released_at`/`brand_released_at` 现成，释放后回填 |
| 提醒 beat 任务 | 抄 `tasks.py:593-657`，注册进 `:840` TASKS | 新增 `suspension_reminder`，走 `notify()` + dedupe |
| schedule 种子 | 抄 `0030:156-166`，config 项 `remind_days`（`09-platform.md:166`） | 0033 或随迁移 INSERT ON CONFLICT |
| 权限 | `channel.incident_write` 已足（`0002:276,339`） | 复用；如需新点抄 `0030:141-153`/`0031:84-98` |
| 测试 | beat=`test_beat_alerts.py`；API=`test_channel_api.py:212+` | 两范式各一套 |
| 迁移号 | 现最大 `0032_variant_anchor.py` | 用 **0033**（目录=`backend/alembic/versions/`，非 migrations/） |