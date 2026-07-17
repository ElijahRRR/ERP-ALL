## R2-07 07b 封店工作流 · 侦察方向 D（API 契约与工单现状）

> 只读侦察，未改任何文件。核心结论先行：**07b 的契约、表、后端端点大部分已经存在**（不是绿地），真正的缺口是「品牌占用批量释放」——而它依赖的 `brand_assignment` 表在整个仓里根本还没建。

---

### 0. 一句话地形（最重要的硬事实）

| 组件 | 现状 | 位置 |
|---|---|---|
| `store_incident` 表 | **已存在，完整 schema**（不是 07b 新建） | `backend/alembic/versions/0003_channel.py:193-219` |
| `/store-incidents` 三个端点 | **契约 + 后端全已落地并接线** | 契约 `specs/002-api-contract/openapi-v0.yaml:345-375`；后端 `backend/src/erp/channel/router.py:511,556` |
| 封店→店铺状态联动 | 已有（suspended/active 回写） | `backend/src/erp/channel/router.py:536-541,577-582` |
| **SKU / 品牌释放作业** | **只有占位注释，从未落码** | `backend/src/erp/channel/router.py:537`「SKU/品牌释放作业随 R2#7 catalog 联动接入」 |
| **`brand_assignment` 表** | **整个仓不存在**（grep 空） | 仅规格 `specs/001-domain-model/03-catalog.md:72-89` 定义，无迁移 |
| incident 定时提醒 beat | **不存在** | 无 schedule 种子（0022-0024 均无） |
| 售后/店铺事件前端页 | **不存在**（占位都没有） | `frontend/src/pages/` 无对应文件 |

结论：07b 若要满足 review_list note② 的「incident→品牌 released→提醒送达」，**先得建 `brand_assignment` 表**（含 `incident_id` 回链列），再写批量释放服务 + beat，再回填 `store_incident.brand_released_at`。释放这条链目前是空的。

---

### 1. 契约现有形状抄录（openapi-v0.yaml）

#### 1a. store-incidents 路径（已存在，`Channel` tag——不是 Aftersale）

`specs/002-api-contract/openapi-v0.yaml:345-375`：
- `GET /store-incidents`（345-353）：`x-permission: channel.incident_read`；query = page/size/`status`；返回 `StoreIncidentPage`。
- `POST /store-incidents`（354-359）：summary「人工登记店铺事件（封店等，D-Q33）」；`x-permission: channel.incident_write`；body `StoreIncidentWrite`；201。
- `POST /store-incidents/{incidentId}/transition`（360-375）：summary「事件状态推进（observing/appealing/resolved/closed）+ **触发释放作业**」；`channel.incident_write`；body `{to_status: enum[observing,appealing,resolved,closed], notes}`；返回 200 / 409。

**命名惯例关键差异（07b 需对齐）**：incidents 归在 **`Channel`** tag（与 stores/proxies 同域），而 07a returns/refunds 归 **`Aftersale`** tag（`openapi:1096,1109,1116…`）。即使同属 R2-07，封店在契约里是 channel 域、权限点前缀 `channel.incident_*`，而不是 `aftersale.*`。

#### 1b. store-incident 相关 schema

- `StoreIncidentWrite`（requestBodies，`openapi:1312-1322`）：required `[store_id, incident_kind, occurred_at]`；`incident_kind` enum = `[suspension, warning, listing_block, other]`；`reason`。
- `StoreIncident`（schemas，`openapi:1552-1564`）：`id, store_id, incident_kind, source, occurred_at, reason, status, sku_released_at, brand_released_at, appeal_notes`。**注意契约是表的子集**——表里还有 `mail_message_id / mail_body_snapshot / closed_at / created_by`（`0003_channel.py:199,202,209,212`）未在契约暴露（07c 邮箱域会用到 mail_* 两列）。
- `StoreIncidentPage`（`openapi:1565-1568`）：`allOf [PageMeta, {items: [StoreIncident]}]`——分页统一惯例。

#### 1c. 07a Aftersale 路径长相（07b 新端点应参照的结构惯例）

`openapi:1093-1157`，成对「列表 + 详情 + 动作子路径」结构：
- `GET /returns`（1094-1106）：query 用 enum 过滤（`internal_status enum[pulled,reviewed,closed]`）+ `q` 精确查；
- `GET /returns/{returnId}`（1107-1113）：详情内嵌 lines+events；
- `GET /refund-requests` + `POST /refund-requests`（1114-1143）：POST 带 `Idempotency-Key`（`$ref parameters/idempotencyKey`，1129）；
- `POST /refund-requests/{refundRequestId}/approve` | `/reject`（1144-1157）：**动作用独立子路径 + 动词**，每个 method 单独 `x-permission`。

**惯例不一致点（07b 可顺手对齐）**：aftersale 的路径参数走**共享组件** `components/parameters/returnId`、`refundRequestId`（`openapi:1236-1237`），而 store-incidents 的 `incidentId` 是**内联**在路径里（`openapi:365`），未抽成共享 parameter。07b 若扩展 incident 端点，建议按 aftersale 惯例把 `incidentId` 抽进 `components/parameters`。

---

### 2. 工单现状摘要

#### 2a. review_list.json — R2-07 条目（`.agent/review_list.json:515-528`）

- `id: R2-07`，`priority: P0`，`status: "in_progress"`（`:516,517,520`）。
- **check 里 07b 的定义**（`:519`）：「07b 封店工作流(**store_incident 001§02 + 品牌占用批量释放 + beat 提醒**, 考古=旧 erp_core store_incidents)」。
- **finding 链**（`:525`）：07a 已整片收账——PR #18（0030 三表 + return_pull beat + GET /returns + aftersale.read）、PR #19（0031 refund_request + record/approval 两档）**均已合并**；验收①真机通过（A152 拉 36 单对账一致，evidence `a152-recon-20260717.md`，部署机 HEAD=176da4c/alembic 0031）。明写「待办：**07b 封店 → 07c 邮箱**（按 Owner 批准顺序，先开 R2-11 变体组再回本单）」。
- **07b 验收判据**（note，`:527`）：「②**封店演练(incident→品牌 released→提醒送达)**」——即分级验收 L2 要求：登记封店事件后，品牌占用需真被批量释放，且定时提醒需送达（notification）。（①是 07a returns 对账，③是 07c 邮件分类。）

#### 2b. task.md 当前状态（`.agent/task.md`）

- 动工顺序（2026-07-17 更新，`:2-3`）：`R2-11 → R2-07 → R2-12 → R2-09 → R2-08 → R2-10`。
- 当前实际停点：**R2-11 变体组开发面全完，停在人工验收点**（`:6-15`），等 Owner 做 L2 验收。
- 07b 是 R2-11 验收后的**接续第一项**（`:16`：「验收后接续…：R2-07 **07b 封店** → 07c 邮箱 → …」）。
- **全局挂账明确点名**（`:20`）：「**售后前端页（随 07b/07c）**」——即前端页是 07b/07c 的交付一部分，此前 07a 只做了后端。

#### 2c. 任务清单（TaskList）

- `#38 [in_progress] R2-07 07b 考古：图纸/旧表/现有代码/契约/旧仓五路侦察`（本侦察对应项）。
- `#39 [pending] R2-07 07b 增量1：store_incident 迁移 + 封店域服务 + 品牌批量释放 + beat 提醒 + 测试`。
  ⚠️ 注意 #39 写的是「store_incident 迁移」，但表其实**已在 0003 建好**——07b 真正要新建的迁移是 `brand_assignment` 表（当前完全缺失）+ 可能的种子/回链，而非 store_incident 本身。

---

### 3. 前端触点

#### 3a. 现有页面/路由（无任何售后/店铺事件占位）

- `frontend/src/pages/` 下 14 个页面，**无** Returns/Aftersale/Incident 页（有 `StoresPage.tsx`=店铺管理，属 channel 域）。
- 路由注册模式（`frontend/src/App.tsx:24-41`）：扁平 `<Route path="…" element={<XxxPage/>} />`，全部包在 `<Route element={<AppLayout/>}>`（Outlet）内。新页 = 顶部 import 一行（`:5-18` 风格）+ `<Routes>` 内加一行 `<Route>`。
- 侧栏菜单注册（`frontend/src/layout/AppLayout.tsx:26-40`）：`MENU` 常量数组，每项 `{key(=路由), icon, label, permission}`；`permission` 走 `has(...)` 权限点门控（`:54`）。新增「店铺事件/售后」菜单项即在此加对象，`permission` 应填 `channel.incident_read`（列表端点权限点）。

#### 3b. 前端类型已就绪（codegen 已含 incident）

- `frontend/src/api/schema.d.ts` **已包含** store-incidents 全部类型：`/store-incidents`（`:1030`）、`/store-incidents/{incidentId}/transition`（`:1087`）、`StoreIncident`（`:3675`）、`StoreIncidentPage`（`:3690`）、`StoreIncidentWrite`（`:4122`，含 `incident_kind: "suspension"|"warning"|"listing_block"|"other"`）。
  - 因此 07b 前端页可直接用现成类型；task.md 全局挂账里的「前端 schema.d.ts codegen」对 incident 部分实际已生效（未核实是否与最新契约完全同步，但 store-incidents 块已在）。
- API client 惯例：`frontend/src/api/client.ts` 统一 `fetch('/api/v1'+path)`（`:66`）。

---

### 4. 后端契约实现落差（07b 直接相关，补充给规划）

- **incident 端点已全接线**：`create_incident`（`backend/src/erp/channel/router.py:511-548`，source 硬置 `'manual'`、封店时 store→suspended）、`transition_incident`（`:556-589`，resolved 时 store→active）、列表查询（`:500-508`）；Pydantic 模型 `IncidentWrite`/`IncidentOut`（`:104-121`）。
- **释放链是空壳**：`router.py:537` 注释「SKU/品牌释放作业随 R2#7 catalog 联动接入」；`sku_released_at`/`brand_released_at` 两列从未被写入过。契约 `transition` 的 summary 号称「+触发释放作业」（`openapi:363`）但代码里没有该动作——**这正是 07b 要补的核心逻辑**。
- **`brand_assignment` 表缺失是硬阻塞**：grep `backend/src` + `backend/alembic` 无任何 `brand_assignment`。规格 `specs/001-domain-model/03-catalog.md:72-89` 定义了该表，其中 `incident_id BIGINT NULL REFERENCES store_incident`（`:85`）就是任务说的「回链」，唯一约束 `uq_brand_occupied (team_id, brand_norm) WHERE status='occupied'`（`:88`）。07b 的「批量释放」= 把某 store 下所有 `status='occupied'` 行改 `released` + 写 `released_at`/`release_reason='suspension'`/`incident_id`。**表不存在 → 07b 必须先建表**（或与 catalog 域协调建表归属，跨界需按 CLAUDE.md 角色制提单）。
- **beat 提醒需新建**：现有 schedule 种子（`0024_beat_alert_seeds.py` 等）无 incident 相关。惯例 = 新 alembic 种子 `INSERT INTO app.schedule (code, description, cron, config)`（参 `0024:23-30`）+ 在 `backend/src/erp/automation/tasks.py` 加异步任务函数（现有任务如 `feed_poll`/`retire_recon` 均为 `async def xxx(sessions, config)->dict` 形态，`tasks.py:235,357`）。规格 `02-channel.md:186` 明确提醒去向：「automation 域 schedule 定时提醒（观察放款/写申诉信）→ notification」。

#### 参考规格权威定义

- `store_incident` 表列定义 + 工作流联动四步：`specs/001-domain-model/02-channel.md:162-187`（其中 `:185` 第2步「brand_assignment 释放 + listing 停止维护 + gtin 保持 used 不回收」，`:186` 第3步定时提醒，`:187` 第4步 resolved 恢复须人工）。
- status 机：DEFAULT `'open'`，枚举 `open/observing/appealing/resolved/closed`（`0003_channel.py:203-205`）；但 transition 端点入参只允许 `observing/appealing/resolved/closed`（`router.py:552`），即无法回退到 open。
- D-Q33 是封店工作流的决策依据（多处引用，正文在 `specs/000-founding/DECISION-FORM.md`，本次未展开核实条款细节，标注「未核实」）。

---

### 5. 给 07b 增量1 的净结论

1. store_incident 表/端点/店铺状态联动 = **复用现成，勿重建**。
2. 必须新增：`brand_assignment` 表迁移（含 `incident_id` 回链 + `uq_brand_occupied` 唯一约束）——当前零实现，是最大真缺口。
3. 封店域服务 = 在 `transition_incident`（或 create suspension）里补「批量释放 brand_assignment + 回填 store_incident.brand_released_at」，把 `router.py:537` 的占位注释兑现。
4. beat 提醒 = 新 schedule 种子 + `automation/tasks.py` 任务函数，出 notification（观察放款/申诉提醒）。
5. 前端 = 新建店铺事件页（route + MENU 项，权限点 `channel.incident_read`），schema.d.ts 类型已就绪；task.md 已把「售后前端页」列为 07b/07c 交付。
6. 契约微调（可选对齐）：把 `incidentId` 抽成共享 `components/parameters`；`StoreIncident` schema 未暴露 `brand_released_at` 释放语义之外的字段——若释放要回前端展示，字段已在（`sku_released_at`/`brand_released_at` 已在 schema `:1562-1563`）。