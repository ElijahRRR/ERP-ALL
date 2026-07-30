# PR #49 第三闸真机验证指令（R2-14 14b 主体删除 + deleted_principal 墓碑）

> **给部署 AI（Win11 部署机）。整段可粘贴，逐步执行，每步贴回输出。**
>
> 被验代码：分支 **`claude/r2-03-launch-leg5n8` 的当前尖端**。
> 本指令不把任何具体 sha 写成判据——判据是「你在分支尖端」+「迁移清单只多 0047」，见②。
> 你只需把实际 sha 记进回执。
>
> 写法约定沿用 PR #48 指令 v7 的全部教训：每条命令写死 `-f infra/docker-compose.yml`、
> 服务名只用 `db/redis/migrate/api/beat/scraper/frontend` 真名、路径 `D:\项目文件\ERP-ALL`、
> SQL 载荷内**只有单引号**（JSON 用 `jsonb_build_object` 构造，双引号零出现）、
> 判据认状态不认日志、清理单事务、预期值判据写**条件式**不写「恒为」（#49 审查 N9）。

## 铁律（本次全程有效）

1. **绝不 `pg_restore` 进 `erp_all`**。暂存一律用一次性容器，用毕删容器 + 匿名卷。
2. **不输出任何密钥、口令、token、`Authorization` 头。**
3. **不改码、不 push、不 merge。**
4. **③级表一行不删**：`audit_log`、`channel_order` / `order_line` / `order_check`、
   财务表。**唯一例外**：⑥-0/⑩清理里那张 `purchase_order_ref = 'R14B0730-PO1'` 的
   **测试**采购执行单（⑥-1 造的），只能按该 ref **精确等值**圈定，不许放宽成前缀或范围。
5. **本次唯一的写入是测试数据**：用户名以 **`r14b0730`** 开头、显示名/角色名/采购方名以
   **`R14B`** 开头的行，及上一条说的那张测试执行单。若某步要求你动的对象不带这些前缀，
   **停下来问**。
6. 清理类 SQL 一律 `psql -1 -v ON_ERROR_STOP=1`——要么整段成功，要么整段回滚。
7. **本单会真删「用户/角色/采购方」的测试实体**——这正是被验功能。删除对象仅限⑥-1
   造的那几行；任何真实主体（含你的验收账号）都不在删除范围，⑧-6 的自删调用
   **预期被 409 拒绝**，那是守卫验证不是删除。

## 本单验什么（与 #46/#48 的差异）

- **仓内第二批 DELETE 端点**（14a 产品删除之后）：`DELETE /users/{id}`、`/roles/{id}`、
  `/purchasers/{id}`，按 §7.1 三级规则（①无历史直删无墓碑 / ②有历史删实体留墓碑 /
  ③审计订单财务绝不删）。
- **迁移 0047 含三件结构变更**：删 `procurement_order.purchaser_id` 外键（改软引用）、
  给 `purchaser` 补 DELETE 授权、补 `purchaser_del` RLS 策略。可逆性在⑪演练。
- **不触渠道**：全程无任何 Walmart API 调用，无 dry-run 载荷步骤。

---

## ① 前置锚点 + ③级表基线快照（承重，必须在③迁移之前取）

**为什么在迁移前取**：验收⑤是「订单、审计在任何删除路径下行数不变」——「不变」只能靠
「动之前的样子」来证，动完再取快照对拍自己是恒真判据。本快照在⑤与⑨两处对拍。

```powershell
cd 'D:\项目文件\ERP-ALL'
git fetch origin
git log --oneline -1 origin/main

$TRACKED_DIRTY = @(git status --porcelain | Where-Object { $_ -notmatch '^\?\?' })
$UNTRACKED_PATHS = @(git status --porcelain | Where-Object { $_ -match '^\?\?' } |
                     ForEach-Object { $_.Substring(3).Trim() })
$BRANCH_FILES = @(git ls-tree -r --name-only origin/claude/r2-03-launch-leg5n8)
$COLLIDING = @($UNTRACKED_PATHS | Where-Object { $BRANCH_FILES -contains $_.TrimEnd('/') })

"tracked_dirty = $($TRACKED_DIRTY.Count)   (必须 0)"
$TRACKED_DIRTY
"colliding     = $($COLLIDING -join ', ')   (必须为空)"
docker compose -f infra/docker-compose.yml ps
```

基线快照（**纯读**；`po_` 系列供⑨对拍，`principal_` 系列供⑤对拍）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS audit_rows_before FROM app.audit_log;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS po_rows_before, coalesce(max(id), 0) AS po_max_id_before, md5(coalesce(string_agg(id || ':' || coalesce(purchaser_id::text, '-'), ',' ORDER BY id), '')) AS po_fingerprint_before FROM app.procurement_order;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.app_user) AS users, (SELECT count(*) FROM app.role) AS roles, (SELECT count(*) FROM app.purchaser) AS purchasers, (SELECT count(*) FROM app.permission) AS perms, (SELECT count(*) FROM app.channel_order) AS channel_orders, (SELECT count(*) FROM app.order_line) AS order_lines;"
```

**判据**：`tracked_dirty = 0`、`colliding` 为空、三条查询都有输出；`perms` 在迁移前
应为 **56**（若不是，贴数停手——说明库上迁移状态与预期不符）。

**贴回**：`origin/main` sha、全部输出。**`audit_rows_before` / `po_*_before` /
六个计数务必原样贴回**——⑤⑨拿它们对拍。

---

## ② 切分支 + 自校验

```powershell
git checkout claude/r2-03-launch-leg5n8
git pull --ff-only origin claude/r2-03-launch-leg5n8
git log --oneline -1
git log --oneline -1 origin/claude/r2-03-launch-leg5n8
```

**判据**：本地与 remote 的 sha 相同（在尖端）。

> 若 `git pull --ff-only` 报 divergent：不要 force pull / reset --hard，改用
> `git fetch origin; git checkout -B claude/r2-03-launch-leg5n8 origin/claude/r2-03-launch-leg5n8`
> （①已确认 tracked_dirty=0，对工作区无破坏）。
> **本轮大概率会撞上**：PR #48 已 merge 合入 main，本地旧分支指针落后于新历史，属预期。

迁移清单自校验：

```powershell
git diff --name-only origin/main...HEAD -- backend/alembic/versions/
```

**判据**：**只有 `0047_deleted_principal_tombstone.py` 一个文件**。多或少都停下来问。

---

## ③ 重建并起服务（本 PR 动了 backend **和 frontend**）

> **进入本步前确认①已完成并已把基线贴进回执**——本步的迁移一旦落地，①的基线
> （perms=56 态的计数与指纹）就**再也取不到了**，⑤/⑨/⑫三条「与①相同」的判据将
> 全部失去可比对象，失败形态是「没法验」而不是「验不过」。①未做请回到①，不要往下跑。

```powershell
docker compose -f infra/docker-compose.yml build api beat migrate frontend
docker compose -f infra/docker-compose.yml up -d db redis
docker compose -f infra/docker-compose.yml run --rm migrate
docker compose -f infra/docker-compose.yml up -d api beat frontend
```

> | 服务 | build 来源 | 本 PR 改动 | 处置 |
> |---|---|---|---|
> | `api` / `beat` / `migrate` | `../backend` | 有 | **重建** |
> | `frontend` | `../frontend` | **有**（三页删除入口 + 审计操作人列） | **重建** |
> | `scraper` | `../workers` | 0 | 不重建，保持运行 |
>
> 可自行复算：`git diff --name-only origin/main...HEAD -- workers/` 应为空、
> `-- frontend/` **不为空**。与 #48 相反，本单**有**前端改动——不重建 frontend 就会
> 出现「后端能删、页面上没按钮」的旧产物假象（#46 问题 1 的形状）。

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT version_num FROM public.alembic_version;"
```

**判据**：`version_num = 0047`（状态，不看迁移日志）。

```powershell
$READY = $false
foreach ($i in 1..30) {
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/healthz" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { $READY = $true; break }
  } catch { }
  Start-Sleep -Seconds 2
}
"api_ready = $READY"
docker compose -f infra/docker-compose.yml ps
```

**判据**：`api_ready = True`；`frontend` Up。

---

## ④ 迁移结构核验（**纯读六条**）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT a.attname FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = ANY(c.conkey) WHERE c.conrelid = 'app.deleted_principal'::regclass AND c.contype = 'p' ORDER BY array_position(c.conkey, a.attnum);"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS fk_exists FROM pg_constraint WHERE conname = 'procurement_order_purchaser_id_fkey';"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT has_table_privilege('erp_app', 'app.purchaser', 'DELETE') AS purchaser_delete_granted;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS del_policy FROM pg_policies WHERE schemaname = 'app' AND tablename = 'purchaser' AND cmd = 'DELETE';"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT code FROM app.permission WHERE code IN ('identity.user_delete', 'identity.role_delete', 'procurement.purchaser_delete') ORDER BY code;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS perms_after FROM app.permission;"
```

**判据**：PK 两行 `kind`、`id`（**顺序也要对**）；`fk_exists = 0`；
`purchaser_delete_granted = t`；`del_policy = 1`；权限码三行齐；`perms_after = 59`
（= ①的 56 + 3）。

---

## ⑤ 承重：迁移不动数据

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.audit_log) AS audit_rows, (SELECT count(*) FROM app.app_user) AS users, (SELECT count(*) FROM app.role) AS roles, (SELECT count(*) FROM app.purchaser) AS purchasers, (SELECT count(*) FROM app.channel_order) AS channel_orders, (SELECT count(*) FROM app.order_line) AS order_lines;"
```

**判据**：六个数与①**逐个相同**（0047 的 DML 只动 permission / role_permission，
上面六张表一行不该动）。

---

## ⑥ 造一次性测试数据（**本次唯一的写入**）

### ⑥-0 先清残留（**每轮都跑**；判据是条件式，#49 审查 N9）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "DELETE FROM app.procurement_order WHERE purchase_order_ref = 'R14B0730-PO1'; DELETE FROM app.deleted_principal WHERE label LIKE 'R14B%'; DELETE FROM app.user_role WHERE user_id IN (SELECT id FROM app.app_user WHERE username LIKE 'r14b0730%'); DELETE FROM app.purchaser WHERE name LIKE 'R14B%'; DELETE FROM app.role WHERE name LIKE 'R14B%'; DELETE FROM app.app_user WHERE username LIKE 'r14b0730%';"
```

**判据（条件式）**：整段成功，六个 `DELETE n`。**首轮全部预期 0**；若非 0，
说明是上一轮中途停手的残留被幂等清掉，属预期，把数字贴回执即可。

### ⑥-1 造数（单事务；SQL 载荷零双引号）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "INSERT INTO app.app_user (team_id, username, password_hash, display_name) VALUES (1, 'r14b0730_u1', 'x-not-a-login', 'R14B验证用户甲'), (1, 'r14b0730_u2', 'x-not-a-login', 'R14B验证用户乙'); INSERT INTO app.audit_log (team_id, actor_type, actor_id, action, object_type, object_id) SELECT 1, 'user', id, 'r14b.noop', 'test', '0' FROM app.app_user WHERE username = 'r14b0730_u2'; INSERT INTO app.role (team_id, name) VALUES (1, 'R14B验证角色'); INSERT INTO app.purchaser (team_id, name, purchaser_kind, user_id, exchange_rate) SELECT 1, 'R14B内采绑乙', 'internal', id, 7.2 FROM app.app_user WHERE username = 'r14b0730_u2'; INSERT INTO app.purchaser (team_id, name, purchaser_kind, exchange_rate) VALUES (1, 'R14B外协丙', 'external', 7.2); INSERT INTO app.procurement_order (team_id, store_id, order_id, order_date, status, assignee_kind, purchaser_id, purchase_order_ref) SELECT 1, 1, 990730, now(), 'backfilled', 'external', id, 'R14B0730-PO1' FROM app.purchaser WHERE name = 'R14B外协丙';"
```

> `team_id`/`store_id` 写 1：这台机器只有一个团队（id=1）与一间测试店（store 1，
> #48 ⑥-1 实测确认）。若你机器上团队 id 不是 1，**停下来问**，不要自行改数。
> `password_hash` 是无效占位——这两个账号**不可登录**，只作删除对象。

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT u.username, u.id AS user_id, (SELECT count(*) FROM app.audit_log al WHERE al.actor_type = 'user' AND al.actor_id = u.id) AS audit_rows FROM app.app_user u WHERE u.username LIKE 'r14b0730%' ORDER BY u.username;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT p.name, p.id AS purchaser_id, p.user_id, (SELECT count(*) FROM app.procurement_order po WHERE po.purchaser_id = p.id) AS po_rows FROM app.purchaser p WHERE p.name LIKE 'R14B%' ORDER BY p.name; SELECT id AS role_id FROM app.role WHERE name = 'R14B验证角色';"
```

**判据**：u1 `audit_rows = 0`、u2 `audit_rows = 1`（①/②级前提就位）；
`R14B内采绑乙.user_id` = u2 的 id；`R14B外协丙.po_rows = 1`。
**记下 u1/u2 的 user_id、两个 purchaser_id、role_id——⑧全程要用。**

---

## ⑦ 验收账号权限前置（**只读**）

0047 按角色名「团队管理员」授三个删除码（模板与既有团队同名复制角色一并覆盖），
`pr42_verify-1` 挂着该角色（#48 ⑦实测），**应当自动获得**。核验：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT rp.permission_code FROM app.user_role ur JOIN app.role_permission rp ON rp.role_id = ur.role_id JOIN app.app_user u ON u.id = ur.user_id WHERE u.username = 'pr42_verify-1' AND rp.permission_code IN ('identity.user_delete', 'identity.role_delete', 'procurement.purchaser_delete', 'identity.audit_read') ORDER BY 1;"
```

**判据**：四个码齐（三删除 + `identity.audit_read`，⑧-3 要用）。缺任何一个 →
**贴回执停手**，等 Owner 补授，不要自行改角色。

登录（口令自己输入，不贴回）：

```powershell
$CRED = Get-Credential -UserName "pr42_verify-1" -Message "ERP 登录"
$LOGIN_BODY = @{ username = $CRED.UserName; password = $CRED.GetNetworkCredential().Password } | ConvertTo-Json
$LOGIN_RESP = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/auth/login" -Method Post -Body $LOGIN_BODY -ContentType "application/json"
$TOKEN = $LOGIN_RESP.access_token
"token_len = $($TOKEN.Length)   (>0 即可)"
```

---

## ⑧ 删除验收（真机承重六小步；`<U1>`/`<U2>`/`<P1>`/`<P2>`/`<R1>` 换⑥-1 记下的 id）

**⑧-1 ①级：无历史用户直删无墓碑**

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/users/<U1>?reason=R14B-14b-acceptance-1" -Method Delete -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.app_user WHERE id = <U1>) AS user_left, (SELECT count(*) FROM app.deleted_principal WHERE kind = 'user' AND id = <U1>) AS tombstone;"
```

**判据**：回包 `level = no_history`、`tombstoned = false`；`user_left = 0`、`tombstone = 0`。

**⑧-2 ②级 + 坑1：有历史用户删除，采购方解绑不连带**

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/users/<U2>?reason=R14B-14b-acceptance-2" -Method Delete -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.app_user WHERE id = <U2>) AS user_left, (SELECT label FROM app.deleted_principal WHERE kind = 'user' AND id = <U2>) AS tombstone_label, (SELECT user_id FROM app.purchaser WHERE id = <P1>) AS p1_user_id, (SELECT count(*) FROM app.audit_log WHERE actor_type = 'user' AND actor_id = <U2>) AS u2_audit_rows;"
```

**判据**：回包 `level = with_history`、`tombstoned = true`、`purchasers_unlinked = 1`；
`user_left = 0`、`tombstone_label = R14B验证用户乙`、`p1_user_id` 为空（NULL）、
`u2_audit_rows = 1`（③级一行不动——直插的那条还在）。

**⑧-3 验收③真机承重：审计里已删操作人显示「XX（已删除）」**

```powershell
(Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/audit-logs?actor_id=<U2>" -Headers @{ Authorization = "Bearer $TOKEN" }).items | Select-Object action, actor_id, actor_label | ConvertTo-Json
```

**判据**：至少一条（那条 `r14b.noop`），其 `actor_label` **= `R14B验证用户乙（已删除）`**。

**⑧-4 采购方：在途拒删 → 终态可删 + ③级行 id 原值保留（0047 软引用承重）**

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "UPDATE app.procurement_order SET status = 'claimed' WHERE purchase_order_ref = 'R14B0730-PO1';"
try { Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/purchasers/<P2>?reason=R14B-14b-acceptance-4a" -Method Delete -Headers @{ Authorization = "Bearer $TOKEN" } } catch { $_.ErrorDetails.Message }
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "UPDATE app.procurement_order SET status = 'backfilled' WHERE purchase_order_ref = 'R14B0730-PO1';"
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/purchasers/<P2>?reason=R14B-14b-acceptance-4b" -Method Delete -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.purchaser WHERE id = <P2>) AS purchaser_left, (SELECT purchaser_id FROM app.procurement_order WHERE purchase_order_ref = 'R14B0730-PO1') AS po_purchaser_id, (SELECT count(*) FROM app.deleted_principal WHERE kind = 'purchaser' AND id = <P2>) AS tombstone;"
```

**判据**：第一次删被拒且报文含 `PURCHASER_DELETE_IN_FLIGHT`；第二次回包
`level = with_history`；`purchaser_left = 0`、**`po_purchaser_id = <P2> 原值**
（不是 NULL——外键已改软引用，③级行保留原 id 供墓碑解析）、`tombstone = 1`。

**⑧-5 角色删除（判据条件式）**

```powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/roles/<R1>?reason=R14B-14b-acceptance-5" -Method Delete -Headers @{ Authorization = "Bearer $TOKEN" } | ConvertTo-Json
```

**判据（条件式）**：200；`level` 取决于该角色在 audit_log 里有无对象行——⑥-1 经 SQL
直插（不产生审计），**预期 `no_history`**；若为 `with_history` 请查一下
`audit_log WHERE object_type = 'role' AND object_id = '<R1>'` 并把结果贴回执（可能这台
机器上有人经 UI 动过它，属可解释差异，不是失败）。

**⑧-6 自删守卫（预期 409，不产生删除）**

```powershell
try { Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/users/<你的验收账号user_id>?reason=R14B-guard" -Method Delete -Headers @{ Authorization = "Bearer $TOKEN" } } catch { $_.ErrorDetails.Message }
```

**判据**：报文含 `USER_DELETE_SELF`；你的账号还在（能继续调 API 本身就是证明）。

---

## ⑨ 验收⑤真机：③级表在全部删除动作之后行数与指纹不变

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS po_rows_now, md5(coalesce(string_agg(id || ':' || coalesce(purchaser_id::text, '-'), ',' ORDER BY id), '')) AS po_fingerprint_now FROM app.procurement_order WHERE id <= <PO_MAX_ID_BEFORE>;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.audit_log) AS audit_rows_now, (SELECT count(*) FROM app.channel_order) AS channel_orders_now, (SELECT count(*) FROM app.order_line) AS order_lines_now;"
```

**判据**（`<PO_MAX_ID_BEFORE>` 填①记下的值）：

- `po_rows_now` / `po_fingerprint_now` 与①的 `po_rows_before` / `po_fingerprint_before`
  **逐字相同**（存量执行单一行没动、一个 purchaser_id 没改——测试单 id 大于基线 max，
  被 WHERE 自然排除）；
- `channel_orders_now` / `order_lines_now` 与①相同；
- **条件式公式**：`audit_rows_now = ①的 audit_rows_before + 1（⑥-1 直插）+ ⑧里
  成功的删除动作数`。按上文全过的话删除成功 4 次（⑧-1/2/4b/5），即 `+5`；若⑧有步骤
  未过或重试过，按实际成功次数算并在回执写明算式。

---

## ⑩ 清理测试数据（与⑥-0 逐字相同的六条）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "DELETE FROM app.procurement_order WHERE purchase_order_ref = 'R14B0730-PO1'; DELETE FROM app.deleted_principal WHERE label LIKE 'R14B%'; DELETE FROM app.user_role WHERE user_id IN (SELECT id FROM app.app_user WHERE username LIKE 'r14b0730%'); DELETE FROM app.purchaser WHERE name LIKE 'R14B%'; DELETE FROM app.role WHERE name LIKE 'R14B%'; DELETE FROM app.app_user WHERE username LIKE 'r14b0730%';"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.app_user WHERE username LIKE 'r14b0730%') + (SELECT count(*) FROM app.role WHERE name LIKE 'R14B%') + (SELECT count(*) FROM app.purchaser WHERE name LIKE 'R14B%') + (SELECT count(*) FROM app.deleted_principal WHERE label LIKE 'R14B%') + (SELECT count(*) FROM app.procurement_order WHERE purchase_order_ref = 'R14B0730-PO1') AS leftover;"
```

**判据（条件式）**：六个 `DELETE n`——本轮跑完⑧后预期为
`1(测试执行单) / 2(u2+P2墓碑) / 0 / 1(P1) / 0或1(R1，取决于⑧-5是否已删) / 0`
形态的组合；关键判据是**第二条查询 `leftover = 0`**。
（⑧已删掉的行在这里自然 DELETE 0，数字对不上表格不算失败，`leftover = 0` 才是判据。）

> `deleted_principal` 的测试墓碑行也在清理范围：墓碑对 erp_app 只授 SELECT/INSERT
> （追加式），但本清理走 `erp_migrator`，可删。**只许删 label 带 `R14B` 前缀的**。

---

## ⑪ 迁移可逆性演练（测试数据清完之后做；beat 全程停）

```powershell
docker compose -f infra/docker-compose.yml stop beat
docker compose -f infra/docker-compose.yml run --rm migrate alembic downgrade 0042
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT version_num FROM public.alembic_version) AS ver, (SELECT count(*) FROM pg_class WHERE relname = 'deleted_principal') AS tbl, (SELECT count(*) FROM pg_constraint WHERE conname = 'procurement_order_purchaser_id_fkey') AS fk, (SELECT has_table_privilege('erp_app', 'app.purchaser', 'DELETE')) AS del_grant, (SELECT count(*) FROM app.permission) AS perms;"
```

**判据**：`ver = 0042`、`tbl = 0`（墓碑表已删）、`fk = 1`（外键以 NOT VALID 回来）、
`del_grant = f`、`perms = 56`。

```powershell
docker compose -f infra/docker-compose.yml run --rm migrate
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT version_num FROM public.alembic_version) AS ver, (SELECT count(*) FROM pg_class WHERE relname = 'deleted_principal') AS tbl, (SELECT count(*) FROM pg_constraint WHERE conname = 'procurement_order_purchaser_id_fkey') AS fk, (SELECT count(*) FROM app.permission) AS perms;"
docker compose -f infra/docker-compose.yml start beat
```

**判据**：回到 `ver = 0047`、`tbl = 1`、`fk = 0`、`perms = 59`；beat 起回。

> 降级窗口内不要做任何主体删除操作（墓碑表不存在，删了就真的无痕）。窗口只有几分钟，
> 测试数据已清、真实数据不受影响。

---

## ⑫ 收尾

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT (SELECT count(*) FROM app.audit_log) AS audit_rows_final, (SELECT count(*) FROM app.channel_order) AS channel_orders_final, (SELECT count(*) FROM app.order_line) AS order_lines_final;"
docker compose -f infra/docker-compose.yml ps
```

**判据**：`channel_orders_final` / `order_lines_final` 与①相同；`audit_rows_final`
按⑨的条件式公式（清理不写 audit——⑩走的是 psql 不是 API）；各容器 Up。

**留在验证分支**，等 Owner 合并 PR #49 后再按常规切回。**任何切回 main 的操作之前
必须先 `alembic downgrade 0042`**——main 没有 0047 这个 revision，库停在 0047 时
`migrate`（`upgrade head`）会硬失败、服务全停（#46 第 7 次停机与 #48 N5 的口径，
「降库」「切分支」永远先降后切）。本单结束时两个都不做。

---

## 回帖格式

| 步 | 内容 | 结果 |
|---|---|---|
| ① | 锚点 + 基线（audit/po 计数与指纹、六计数、perms=56） | |
| ② | 分支尖端一致 + 迁移清单只有 0047 | |
| ③ | `alembic_version = 0047` + `api_ready = True` + frontend 重建 | |
| ④ | PK=(kind,id) / fk_exists=0 / 授权与策略在位 / 三码齐 / perms=59 | |
| ⑤ | **承重**：六计数与①逐个相同 | |
| ⑥ | 清残留（条件式）+ 造数判据 + 记下五个 id | |
| ⑦ | 验收账号四码齐 + token_len>0 | |
| ⑧-1~6 | ①级/②级+坑1/验收③/软引用承重/角色/自删守卫 | |
| ⑨ | **承重**：po 指纹逐字同① + audit 条件式公式成立 | |
| ⑩ | `leftover = 0` | |
| ⑪ | 0047→0042→0047 往返，四判据两向都过 | |
| ⑫ | 终态计数 + compose ps + 留在验证分支 | |

另请附：实际 `HEAD` sha、`origin/main` sha、以及**任何你觉得指令写错或写不清的地方**。
前两单的账：#46 七停九缺陷、#48 五停七缺陷，**全部在指令侧，产品代码零缺陷**——
**你停得对的时候就是在帮我修指令。**
