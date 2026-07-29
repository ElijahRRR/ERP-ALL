# PR #46 第三闸真机验证指令（R2-14 14a+14c 产品删除 + 墓碑 + 列表折叠）

> **给部署 AI（Win11 部署机）。整段可粘贴，逐步执行，每步贴回输出。**
>
> 被验代码：分支 **`claude/r2-03-launch-leg5n8` 的当前尖端**。
>
> **本指令不把任何具体 sha 写成判据**——判据是「你在分支尖端」+「迁移清单只有 0041」，
> 见②。你只需**把实际 sha 记进回执**。
>
> > 〔2026-07-29 修订，起因是部署机在第①步就撞上了：v1 把 `HEAD = b645901` 写成硬判据，
> > 而此后指令自身又改了两版（`05ff491`、`f731123`），于是「最新评论叫你用 f731123」与
> > 「正文要求 HEAD 必须是 b645901」直接打架，第②步必然停机。
> > **这是 CLAUDE.md 刹车第 5 条（正文只写不变量、不写会漂的快照数字）的同一个错，
> > 只是发生在部署指令而不是 PR 正文里**——我把那条纪律只用在了 PR 正文上，
> > 漏了这个适用面。**部署机停得对。**〕
>
> ## 铁律（本次指令全程有效）
>
> 1. **绝不 `pg_restore` 进 `erp_all`**。暂存一律用一次性容器，用毕删容器 + 匿名卷。
> 2. **不输出任何密钥、口令、token、`Authorization` 头**。口令自己在本机输入，不贴回对话。
> 3. **不改码、不 push、不 merge。**
> 4. **本次会真的删数据——只删本指令自己造的测试行**（`source_ref` 以 `B0R214V` 开头、
>    `channel_sku` 以 `R214VERIFY-` 开头）。**任何情况下都不要对真实产品执行删除**，
>    包括「顺手清理一下」。若某步要求你删的对象不带上述前缀，**停下来问**。
> 5. **清理类 SQL 不许拆 `psql -1 -v ON_ERROR_STOP=1`**——要么整段成功，要么整段回滚。
> 6. **审计三族（`audit_log`/`audit_run`/`audit_hit`）+ 财务表 + 订单与售后一行不删。**
>    本指令有一步专门断言这件事，那步失败即停止并报回。
>
> ## 本单与 #43 最大的不同：**这次有迁移**
>
> #43 是零迁移，判据是「migrate 不出现 `Running upgrade`」。**本单相反**：
> 必须出现 `Running upgrade 0040 -> 0041`。看到「无升级」就是分支没切对，停下来。

---

## ① 前置锚点（先记下，回滚要用）

```powershell
cd C:\ERP-ALL          # 若路径不同请以实际为准，并在回帖里说明
git fetch origin
git log --oneline -1 origin/main

# 工作区判据分两类，**只有第一类是阻塞项**
$TRACKED_DIRTY = @(git status --porcelain | Where-Object { $_ -notmatch '^\?\?' })
$UNTRACKED_PATHS = @(git status --porcelain | Where-Object { $_ -match '^\?\?' } |
                     ForEach-Object { $_.Substring(3).Trim() })
$BRANCH_FILES = @(git ls-tree -r --name-only origin/claude/r2-03-launch-leg5n8)
$COLLIDING = @($UNTRACKED_PATHS | Where-Object { $BRANCH_FILES -contains $_.TrimEnd('/') })

"tracked_dirty = $($TRACKED_DIRTY.Count)   (必须 0)"
$TRACKED_DIRTY
"untracked     = $($UNTRACKED_PATHS -join ', ')   (不阻塞，除非下一行非空)"
"colliding     = $($COLLIDING -join ', ')   (必须为空)"
docker compose ps
```

**判据**：
- `tracked_dirty = 0`——**有未提交的跟踪文件改动才是真阻塞**（切分支会丢改动或被拒）。
- `colliding` 为空——未跟踪文件只有在**与分支里的同名跟踪文件撞路径**时才会让
  `git checkout` 拒绝执行；不撞就既不挡路也不会被动到。

> 〔2026-07-29 修订〕v1 这里只写「`git status --short` 应为空」，部署机因三个**既有的
> 未跟踪文件**（`.codex/`、`AGENTS.md`、`RS-02a-runbook.md`）停机——**它按判据停是对的，
> 是判据把「未跟踪」和「有改动」混为一谈了**。已实测这三者在本分支与 main 上都不是
> 跟踪文件，故不构成阻塞；上面的写法把这个区分交给机器判，不再靠人临场拿捏。

**贴回**：`origin/main` 的 sha、上面四行输出、compose 各容器状态。

## ② 切分支 + 自校验

```powershell
git fetch origin claude/r2-03-launch-leg5n8
# **用一次性分支名 verify-pr46，不动你本地的 claude/r2-03-launch-leg5n8。**
# 见下方「为什么不 checkout 同名分支」。-B 可反复执行，重跑不会累积状态。
git checkout -B verify-pr46 origin/claude/r2-03-launch-leg5n8
$HEAD_SHA   = (git rev-parse --short HEAD)
$REMOTE_SHA = (git rev-parse --short origin/claude/r2-03-launch-leg5n8)
"HEAD=$HEAD_SHA  REMOTE=$REMOTE_SHA  at_tip=$($HEAD_SHA -eq $REMOTE_SHA)"
# 本单的迁移必须且只能有 0041
git diff --name-only origin/main...HEAD -- backend/alembic/
```

**判据**：
- `at_tip=True`（你在分支尖端）。**不比对任何写死的 sha**——指令自身还会修订，
  写死就会像 v1 那样自相矛盾。
- 迁移文件清单**只有** `backend/alembic/versions/0041_deleted_product_tombstone.py`。
  **这一条才是「被测内容对不对」的真判据**：它不随指令修订而变。

**把 `$HEAD_SHA` 记进回执第一行**，连同你所读指令的 sha。#43 那次改了五版指令，
全靠回执里记着版本号才对得上是哪一版出的问题。

> ### 为什么不 `checkout` 同名分支 + `pull --ff-only`
>
> 〔2026-07-29 修订，起因是部署机在此停机第二次〕
>
> **云端侧对该分支做过 force-push**：#45 squash 合并后，分支上的 commit 不再是 main 的
> 祖先（squash 的固有后果），故重开分支时从 main 重建并强推。部署机本地仍停在 #43 那轮的
> 尖端 `b1b0782`，于是 `ahead 8, behind 18`，`--ff-only` 必然 abort。
>
> **`git checkout -B verify-pr46 origin/...` 完全绕开这件事**：它只动一个一次性分支名，
> 你本地的 `claude/r2-03-launch-leg5n8` **原封不动**——**即便我对「本地那些 commit 可以丢」
> 的判断是错的，也不会丢任何东西**。这比先证明「丢了没关系」再执行 `reset --hard` 更稳：
> **前者不需要我的判断正确，后者需要。**
>
> （供参考的实测：`git diff --stat b1b0782 28fc76a` 为**纯增量、零删除**，
> 即那 8 个 commit 的内容已被 #43 的 squash 合并一字不落地收进 main。
> 但上面那条路本来就不依赖这个结论。）

## ③ 全栈重建并起服务（**判据查库里的状态，不查日志**）

```powershell
docker compose build
docker compose up -d
docker compose ps
docker compose logs migrate --tail 40     # 贴回来即可；**看不到 Alembic 正文不算失败**，见下
```

### 迁移是否真的落地——**这一条是判据**

```powershell
docker compose exec db psql -U erp_migrator -d erp_all -tA -v ON_ERROR_STOP=1 -c "
SELECT 'alembic_version=' || version_num FROM public.alembic_version;
SELECT 'tombstone_table=' || coalesce(to_regclass('app.deleted_product')::text, 'MISSING');
"
```

**判据**：`alembic_version=0041` 且 `tombstone_table=app.deleted_product`。
容器侧另需 migrate 为 `Exited (0)`、`api`/`beat`/`frontend` 为 `Up`、`db`/`redis` 为 `Up (healthy)`。

> 〔2026-07-29 修订，起因是部署机在此停机第三次〕
> v1 把判据写成「migrate 日志里出现 `Running upgrade 0040 -> 0041`」。**那是个坏判据**：
>
> - **日志会没有**——容器已退出、被重建、被截断，都会让正文取不到，而迁移其实跑成功了；
> - **更要命的是它只在第一次成立**：迁移一旦应用，**重跑时 Alembic 本来就不会再打印
>   `Running upgrade`**。而本次验证已经因为别的原因重跑过好几轮，这个判据从第二轮起
>   就永远不可能通过。
>
> **日志是证据，库里的状态才是事实。** 换成查 `alembic_version` 与表是否存在，
> 无论跑第几轮都成立，也不依赖日志有没有被留下。

### 等 API 就绪（**不是立刻请求**）

```powershell
$HEALTH_OK = $false
foreach ($tryIndex in 1..30) {
  try {
    $HEALTH_BODY = Invoke-RestMethod http://127.0.0.1:8000/healthz -TimeoutSec 3
    $HEALTH_OK = $true
    "healthz ok（第 $tryIndex 次）：$($HEALTH_BODY | ConvertTo-Json -Compress)"
    break
  } catch { Start-Sleep -Seconds 2 }
}
if (-not $HEALTH_OK) {
  "STOP：60 秒内 healthz 仍未就绪。贴回下面两段再停手："
  docker compose ps
  docker compose logs api --tail 50
}
```

**判据**：`healthz ok`，返回 `{"status":"ok", ...}`。

> 〔同上修订〕v1 在 `up -d` 之后**紧接着**打一次 `healthz`，部署机拿到
> 「基础连接已经关闭」而停手——**当时 api 容器启动不足 1 秒**。
> 这不是环境问题，是我漏写了就绪等待：`docker compose up -d` 返回只代表容器被创建，
> **不代表进程已经在监听**。轮询 30 次 × 2 秒足够覆盖冷启动。

## ④ 前端产物对拍（防「部署了但页面是旧的」）

```powershell
docker compose exec frontend sh -c "ls /usr/share/nginx/html/assets/index-*.js"
```

> 〔2026-07-29 修订，起因是部署机在此停机第四次〕
> v1 写的是 `sh -lc "ls ... | Select-String 'index-'"`——**`Select-String` 是 PowerShell
> 的 cmdlet，而 `sh -lc` 里跑的是容器内的 Alpine sh**，报 `sh: Select-String: not found`。
>
> **#43 那份原本是对的**（`sh -c "ls .../index-*.js"`，无管道无 cmdlet），
> 是我抄过来时顺手「改进」成管道 + cmdlet，把一条真机上跑通过的命令改坏了。
> **抄一条已经验证过的命令时不要顺手优化它**——它跑通过，就是它当前形态的全部价值。
>
> 该失败类别已机器化：`.agent/evidence/R2-14/crossshellcheck.py`
> （扫 `sh -c` / `bash -c` 的载荷里有没有 PowerShell 的 Verb-Noun cmdlet）。

浏览器打开系统 → F12 Network 刷新首页 → 看加载的 `index-*.js` 文件名。

**判据**：两个文件名**逐字相同**。不同即前端没刷新，后面的 UI 判据全部不算数。

---

## ⑤ 迁移落地核对（**纯读，零风险，先把地基验了**）

> **先探一下 psql 用哪个角色**（本指令后面所有 SQL 都用它）。容器内本地连接通常是 trust，
> 不需要口令；若 `-U erp_migrator` 报角色不存在或要口令，改用 compose 里 db 服务的
> `POSTGRES_USER`，并在回帖里说明你实际用的是哪个——**不要贴口令**。
>
> ```powershell
> docker compose exec db psql -U erp_migrator -d erp_all -tAc "SELECT current_user"
> ```

```powershell
docker compose exec db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "
\d app.deleted_product
"
```

**判据（逐条看）**：
- 列齐：`id / team_id / source_channel / source_ref / product_id / master_sku / reason / deleted_at / deleted_by`
- 唯一约束 `uq_deleted_product` 是 **`(team_id, source_channel, source_ref)`**
- Policies 有且只有 `deleted_product_sel`(SELECT) 与 `deleted_product_ins`(INSERT)

```powershell
docker compose exec db psql -U erp_migrator -d erp_all -tA -v ON_ERROR_STOP=1 -c "
SELECT 'tombstone_grants=' || string_agg(privilege_type, ',' ORDER BY privilege_type)
FROM information_schema.table_privileges
WHERE table_schema='app' AND table_name='deleted_product' AND grantee='erp_app';
SELECT 'listing_grants=' || string_agg(privilege_type, ',' ORDER BY privilege_type)
FROM information_schema.table_privileges
WHERE table_schema='app' AND table_name='listing' AND grantee='erp_app';
SELECT 'listing_spec_grants=' || string_agg(privilege_type, ',' ORDER BY privilege_type)
FROM information_schema.table_privileges
WHERE table_schema='app' AND table_name='listing_spec' AND grantee='erp_app';
SELECT 'perm_seeded=' || count(*) FROM app.permission WHERE code='catalog.product_delete';
SELECT 'granted_to_roles=' || count(*) FROM app.role_permission rp JOIN app.role r ON r.id=rp.role_id
WHERE rp.permission_code='catalog.product_delete';
"
```

**判据**：

| 输出 | 期望 | 为什么这条重要 |
|---|---|---|
| `tombstone_grants` | `INSERT,SELECT`（**不含 UPDATE/DELETE**） | 墓碑可改 = 「这商品没被删过」可以被伪造，去重保护随之失效 |
| `listing_grants` | 含 `DELETE` | 不含则②级删除必失败——这是 0041 唯一的权限放宽 |
| `listing_spec_grants` | 含 `DELETE` | 同上 |
| `perm_seeded` | `1` | |
| `granted_to_roles` | `≥1`（模板「团队管理员」+ 既有团队同名副本） | 为 0 则现网团队看不到删除入口且无任何报错 |

---

## ⑥ 造一次性测试数据（**本次唯一的写入，全部带前缀**）

先挑一家**测试店**。**必须 `is_test = true`**；若查不到，停下来问，不要拿真实店凑数：

```powershell
docker compose exec db psql -U erp_migrator -d erp_all -tA -v ON_ERROR_STOP=1 -c "
SELECT id || ' | ' || code || ' | team=' || team_id || ' | is_test=' || is_test
FROM app.store WHERE is_test = true ORDER BY id;
"
```

**贴回**结果，并记下要用的 `store_id` 与它的 `team_id`（下面记作 `<STORE_ID>` / `<TEAM_ID>`）。

```powershell
docker compose exec db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "
INSERT INTO app.product (team_id, source_channel, source_ref, title, status)
VALUES
  (<TEAM_ID>, 'amazon', 'B0R214V001', 'R2-14 verify - never listed', 'ingested'),
  (<TEAM_ID>, 'amazon', 'B0R214V002', 'R2-14 verify - has history',  'ingested'),
  (<TEAM_ID>, 'amazon', 'B0R214V003', 'R2-14 verify - live guard',   'ingested');

INSERT INTO app.listing (team_id, store_id, product_id, offer_mode, channel_sku, status)
SELECT p.team_id, <STORE_ID>, p.id, 'build', 'R214VERIFY-002', 'delisted'
FROM app.product p WHERE p.team_id=<TEAM_ID> AND p.source_ref='B0R214V002';

INSERT INTO app.listing (team_id, store_id, product_id, offer_mode, channel_sku, status)
SELECT p.team_id, <STORE_ID>, p.id, 'build', 'R214VERIFY-003', 'queued'
FROM app.product p WHERE p.team_id=<TEAM_ID> AND p.source_ref='B0R214V003';
"
```

> **为什么第三件用 `queued` 而不是 `live`**：`queued` 同属「渠道存活/在途」集合，同样能触发
> 那条 409；但定价与维护 beat 只扫 `('live','published')`，用 `live` 会让 beat 拿一个渠道上
> 不存在的 SKU 去打真实 Walmart API。**`queued` 验的是同一条守卫，却不碰渠道。**
>
> 已知的唯一副作用：`item_pull`（无状态过滤）若恰在这几分钟跑，会为这两个假 SKU 记一条
> 「本地有渠道无」差异——**只是一条差异记录，不产生渠道写**，且测试行删掉后自然消失。

```powershell
docker compose exec db psql -U erp_migrator -d erp_all -tA -v ON_ERROR_STOP=1 -c "
SELECT 'seeded=' || p.source_ref || ' id=' || p.id || ' sku=' || p.master_sku
FROM app.product p WHERE p.team_id=<TEAM_ID> AND p.source_ref LIKE 'B0R214V%' ORDER BY p.source_ref;
"
```

**贴回**三个 `product_id`（下面记作 `<PID1>` `<PID2>` `<PID3>`）与它们的 `master_sku`。

---

## ⑦ ③级基线快照（**删除前**，与⑬对拍）

```powershell
docker compose exec db psql -U erp_migrator -d erp_all -tA -v ON_ERROR_STOP=1 -c "
SELECT t || '=' || n FROM (
  SELECT 'audit_log' t, count(*) n FROM app.audit_log UNION ALL
  SELECT 'audit_run',   count(*) FROM app.audit_run   UNION ALL
  SELECT 'audit_hit',   count(*) FROM app.audit_hit   UNION ALL
  SELECT 'channel_order', count(*) FROM app.channel_order UNION ALL
  SELECT 'order_line',  count(*) FROM app.order_line  UNION ALL
  SELECT 'order_check', count(*) FROM app.order_check UNION ALL
  SELECT 'channel_return', count(*) FROM app.channel_return UNION ALL
  SELECT 'channel_return_line', count(*) FROM app.channel_return_line UNION ALL
  SELECT 'channel_return_event', count(*) FROM app.channel_return_event UNION ALL
  SELECT 'refund_request', count(*) FROM app.refund_request
) x ORDER BY t;
"
```

**贴回整段**，这是⑬的基线。

---

## ⑧ 登录取 token（**口令不要贴回来**）

用一个**团队管理员**账号（非超管），团队 = `<TEAM_ID>`：

```powershell
$BASE_URL = 'http://127.0.0.1:8000/api/v1'    # 按本机实际端口
$LOGIN_USER = Read-Host '团队管理员用户名'
$LOGIN_PASS = Read-Host '口令' -AsSecureString
$PLAIN_PASS = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
  [Runtime.InteropServices.Marshal]::SecureStringToBSTR($LOGIN_PASS))
$LOGIN_RESP = Invoke-RestMethod "$BASE_URL/auth/login" -Method Post -ContentType 'application/json' `
  -Body (@{ username = $LOGIN_USER; password = $PLAIN_PASS } | ConvertTo-Json)
$AUTH_HEADERS = @{ Authorization = "Bearer $($LOGIN_RESP.access_token)" }
$PLAIN_PASS = $null
"login ok, token_len=$($LOGIN_RESP.access_token.Length)"   # 只贴这一行，绝不贴 token 本身
```

> 字段名 `access_token` 已对着 `identity/schemas.py::TokenPairOut` 核过（`access_token` /
> `refresh_token` / `expires_in`）。`token_len` 打出来是为了**证明确实取到了值**——
> 若字段名写错，`$AUTH_HEADERS` 会静默变成 `"Bearer "`，后面每一步都回 401，
> 而症状看起来像「权限没授对」，会把排查方向带偏一整轮。

---

## ⑨ 验收①：删「从未上架」→ 物理消失、**无墓碑**

```powershell
$REASON_TEXT = 'R2-14 third-gate verify level-1'
$REASON_ENC  = [uri]::EscapeDataString($REASON_TEXT)
$DEL1_RESP = Invoke-RestMethod "$BASE_URL/products/<PID1>?reason=$REASON_ENC" -Method Delete -Headers $AUTH_HEADERS
"level=$($DEL1_RESP.level)  tombstoned=$($DEL1_RESP.tombstoned)  listings=$($DEL1_RESP.listings_deleted)  sku=$($DEL1_RESP.master_sku)"
```

**判据**：`level=no_history`，`tombstoned=False`，`listings=0`。

```powershell
docker compose exec db psql -U erp_migrator -d erp_all -tA -v ON_ERROR_STOP=1 -c "
SELECT 'product_rows=' || count(*) FROM app.product WHERE id = <PID1>;
SELECT 'tombstone_rows=' || count(*) FROM app.deleted_product WHERE source_ref='B0R214V001';
"
```

**判据**：`product_rows=0`，**`tombstone_rows=0`**。

> **`tombstone_rows=0` 是本步的重点，不是顺带**。Owner 2026-07-29 裁定①级维持不留墓碑，
> 代价是这类商品重采仍会入库——界面已把这句话写给运营看。此处若冒出墓碑，说明实现
> 与裁定不符。

## ⑩ 验收②：删「上过架」→ 留墓碑 + 重采被挡

```powershell
$REASON2_ENC = [uri]::EscapeDataString('R2-14 third-gate verify level-2')
$DEL2_RESP = Invoke-RestMethod "$BASE_URL/products/<PID2>?reason=$REASON2_ENC" -Method Delete -Headers $AUTH_HEADERS
"level=$($DEL2_RESP.level)  tombstoned=$($DEL2_RESP.tombstoned)  listings=$($DEL2_RESP.listings_deleted)  gtins=$($DEL2_RESP.gtins_released)  tasks=$($DEL2_RESP.maintenance_tasks_skipped)"
```

**判据**：`level=with_history`，`tombstoned=True`，`listings=1`。

```powershell
docker compose exec db psql -U erp_migrator -d erp_all -tA -v ON_ERROR_STOP=1 -c "
SELECT 'product_rows=' || count(*) FROM app.product WHERE id = <PID2>;
SELECT 'listing_rows=' || count(*) FROM app.listing WHERE channel_sku = 'R214VERIFY-002';
SELECT 'tomb=' || team_id || '/' || source_channel || '/' || source_ref
     || ' pid=' || product_id || ' sku=' || master_sku
FROM app.deleted_product WHERE source_ref='B0R214V002';
"
```

**判据**：`product_rows=0`、`listing_rows=0`、墓碑**恰好一行**且
`source_channel=amazon`、`product_id=<PID2>`、`master_sku` 与⑥记下的一致。

**再验墓碑真的挡住了重采**：

```powershell
$REINGEST_BODY = @{
  source   = 'amazon'
  job_kind = 'product_detail'
  input    = @{ targets = @('B0R214V002') }
} | ConvertTo-Json -Depth 5
$REINGEST_HEADERS = $AUTH_HEADERS + @{ 'Idempotency-Key' = [guid]::NewGuid().ToString() }
try {
  Invoke-RestMethod "$BASE_URL/scrape-jobs" -Method Post -Headers $REINGEST_HEADERS `
    -ContentType 'application/json' -Body $REINGEST_BODY
  'UNEXPECTED: 作业竟然建成了'
} catch {
  $REINGEST_ERR = $_.ErrorDetails.Message | ConvertFrom-Json
  "status=$($_.Exception.Response.StatusCode.value__)  code=$($REINGEST_ERR.error.code)"
}
```

**判据**：`status=422`，`code=SCRAPE_ALL_TARGETS_DELETED`。

> **诚实交代这一步验到了哪一层**：它验的是**作业侧预筛**。真正承重的那一层在
> `product_upsert` 的入库前检查，真机上没有不经真实抓取就触发它的路径，故那一层由 CI
> 覆盖——审查侧已做变异证伪（短路该检查后 16 条判据中只有验收②那条转红）。
> **不要把本步的绿当成「回流保护整条都验过了」。**

## ⑪ 承重守卫：在架/在途的**删不掉**（0041 权限放宽的唯一边界）

```powershell
$REASON3_ENC = [uri]::EscapeDataString('R2-14 third-gate verify guard')
try {
  Invoke-RestMethod "$BASE_URL/products/<PID3>?reason=$REASON3_ENC" -Method Delete -Headers $AUTH_HEADERS
  'UNEXPECTED: 在架产品竟然被删了'
} catch {
  $GUARD_ERR = $_.ErrorDetails.Message | ConvertFrom-Json
  "status=$($_.Exception.Response.StatusCode.value__)  code=$($GUARD_ERR.error.code)"
}
docker compose exec db psql -U erp_migrator -d erp_all -tA -v ON_ERROR_STOP=1 -c "
SELECT 'product_rows=' || count(*) FROM app.product WHERE id = <PID3>;
SELECT 'listing_rows=' || count(*) FROM app.listing WHERE channel_sku = 'R214VERIFY-003';
"
```

**判据**：`status=409`、`code=PRODUCT_DELETE_LISTING_ACTIVE`，且
**`product_rows=1` 与 `listing_rows=1` 都还在**（拒绝必须是整体回滚，不能删一半）。

> **这一步是本单最该在真机上验的一条。** 0041 给 `erp_app` 补了 `listing` 的 DELETE 权限，
> 而那条权限的全部边界就是这个 409。它之前只在单元测试里成立过。

## ⑫ 两条便宜的负向判据

```powershell
# reason 必填
try {
  Invoke-RestMethod "$BASE_URL/products/<PID3>" -Method Delete -Headers $AUTH_HEADERS
  'UNEXPECTED: 无 reason 竟然通过'
} catch { "no_reason_status=$($_.Exception.Response.StatusCode.value__)" }

# 不存在的 id
try {
  Invoke-RestMethod "$BASE_URL/products/999999999?reason=$REASON3_ENC" -Method Delete -Headers $AUTH_HEADERS
  'UNEXPECTED: 不存在的 id 竟然通过'
} catch { "ghost_status=$($_.Exception.Response.StatusCode.value__)" }
```

**判据**：`no_reason_status=422`；`ghost_status=404`。

## ⑬ ③级对拍：**订单 / 审计三族 / 售后一行没少**

重跑⑦那段完全相同的 SQL，与基线逐行比。

**判据**：
- **除 `audit_log` 外，每一张都与基线完全相等**；
- `audit_log` **恰好 +2**（⑨与⑩两次成功删除各写一条留痕；⑪⑫全是被拒的，不写）。

> 这条不是「行数不变」——**成功删除必然给 `audit_log` +1**，那正是留痕。
> （我本地第一版判据把它写成「不变」而判红，是判据错了不是代码错了，一并记在这里
> 免得你也踩。）若 `audit_log` 增量不是 2，说明有别的路径顺手记了账，值得查。

```powershell
docker compose exec db psql -U erp_migrator -d erp_all -tA -v ON_ERROR_STOP=1 -c "
SELECT 'action=' || action || ' obj=' || object_id
     || ' actor=' || coalesce(actor_id::text,'null')
     || ' reason=' || coalesce(after->>'reason','null')
     || ' tombstoned=' || coalesce(after->>'tombstoned','null')
     || ' before_keys=' || (SELECT string_agg(k, ',' ORDER BY k) FROM jsonb_object_keys(before) k)
FROM app.audit_log
WHERE action = 'catalog.product_delete' AND object_id IN ('<PID1>','<PID2>')
ORDER BY occurred_at;
"
```

**判据**：两行；`actor` 是你登录的那个用户；`before_keys` 里
**不含 `attrs` / `images` / `price_snapshot`**。

> 后半条是反向判据：那三样是占体积的大头，而 `audit_log` 永久保留——整行入快照会让
> 「删除」变成把字节搬进一张永不清理的表，**空间不减反增**。

---

## ⑭ UI 真链路（浏览器，走**内网 IP** 不要用 localhost）

1. **列表折叠（14c）**：产品页默认不出现「已下架」的产品；勾上「显示已下架」后出现；
   在状态下拉里选「已下架」时，该勾选框**变灰不可点**并有 Tooltip 说明。
2. **删除入口**：团管账号能看到行内红色「删除」按钮；点开弹窗，**逐字读一遍确认文案**，
   确认它写明了这四件事：物理删除无回收站 / 上过架的会留墓碑且重采不再入库 /
   **从未上架的不留墓碑、重采仍会入库、要彻底清掉需同时从采集清单去掉该 ASIN** /
   订单审计财务不受影响。
3. **不填原因点确认**：应被表单挡住，不发请求。
4. **不要在 UI 上删任何真实产品。** 想实际点一次的话，用⑥的方式再造一件
   `B0R214V004` 再删它。

**贴回**：截图或逐条描述；特别确认第 2 条的**①级那句**在页面上真的存在——那是本次
Owner 裁定的落点，只写进代码不显示给运营等于没落。

---

## ⑮ 迁移可逆性演练（在测试数据清完之后做）

先清残留（**只清本指令造的**）：

```powershell
docker compose exec db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "
DELETE FROM app.listing      WHERE channel_sku LIKE 'R214VERIFY-%';
DELETE FROM app.product      WHERE source_ref LIKE 'B0R214V%';
DELETE FROM app.deleted_product WHERE source_ref LIKE 'B0R214V%';
"
```

```powershell
docker compose run --rm migrate alembic downgrade 0040
docker compose exec db psql -U erp_migrator -d erp_all -tA -c "SELECT 'after_down=' || coalesce(to_regclass('app.deleted_product')::text,'gone');"
docker compose run --rm migrate alembic upgrade head
docker compose exec db psql -U erp_migrator -d erp_all -tA -c "SELECT 'after_up=' || coalesce(to_regclass('app.deleted_product')::text,'gone');"
```

**判据**：`after_down=gone`、`after_up=app.deleted_product`。

> ⚠️ **降级会 DROP 墓碑表**，即降级后「已删商品可被重新采集回来」。本次演练前已把测试
> 墓碑清空，所以不损失任何东西；**但正式环境若要降级，须先知悉这条语义损失**
> （已写进 `.agent/evidence/R2-14/runbook.md`）。

## ⑯ 收尾

```powershell
git checkout main
git pull --ff-only origin main
git branch -D verify-pr46        # 一次性分支，用完删掉；删它不影响任何东西
docker compose build
docker compose up -d
docker compose ps
Invoke-RestMethod http://127.0.0.1:8000/healthz
docker compose exec db psql -U erp_migrator -d erp_all -tA -c "
SELECT 'leftover_products=' || count(*) FROM app.product WHERE source_ref LIKE 'B0R214V%';
SELECT 'leftover_listings=' || count(*) FROM app.listing WHERE channel_sku LIKE 'R214VERIFY-%';
SELECT 'leftover_tombstones=' || count(*) FROM app.deleted_product WHERE source_ref LIKE 'B0R214V%';
"
```

**判据**：三个 leftover 全为 0；healthz 正常；未改码、未 push、未 merge。

> **注意**：切回 main 后数据库仍停在 0041（⑮ 最后一步 upgrade 到了 head）。
> main 的代码不认识 `deleted_product`，但**多一张没人用的表不影响 main 运行**——
> 不要为了「干净」再降一次，降级反而会在合并后需要重新升。

---

## 回帖格式

请按 ①–⑯ 逐条给「过 / 不过 + 关键输出」。**任何一条不过就停在那里**，
把原始输出贴回来，不要跳过继续。

**过往六轮的经验：#43 第三闸六次阻断全部是验证指令自身写错，产品零缺陷。**
所以若你判断「指令与实际不符」，那大概率是对的——**照旧直接指出来，别硬凑**。
