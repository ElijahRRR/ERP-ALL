# PR #48 第三闸真机验证指令（R2-15 SKU 内外分离，D-Q72）

> **给部署 AI（Win11 部署机）。整段可粘贴，逐步执行，每步贴回输出。**
>
> 被验代码：分支 **`claude/r2-03-launch-leg5n8` 的当前尖端**。
> **本指令不把任何具体 sha 写成判据**——判据是「你在分支尖端」+「迁移清单只多 0042」，见②。
> 你只需**把实际 sha 记进回执**。（#46 v1 把 sha 写死当判据，第①步就必然停机。）
>
> > 〔**v2 修订，2026-07-30，起因是部署机在第③步停机——三处缺陷全在指令自身**〕
> >
> > 1. **compose 服务名是我凭空写的**：v1 写 `build backend frontend worker beat` /
> >    `up -d backend worker beat frontend`，而本仓实际服务是
> >    `db` / `redis` / `migrate` / `api` / `beat` / `scraper` / `frontend`——
> >    **根本没有 `backend`，也没有 `worker`**。
> > 2. **compose 文件不在仓库根**：它在 `infra/docker-compose.yml`，而 v1 所有命令都是
> >    裸 `docker compose`，在仓库根执行必然 `no configuration file provided: not found`。
> >    v2 起**每条命令都写死 `-f infra/docker-compose.yml`**（不用 `$env:COMPOSE_FILE`
> >    也不用变量——零状态，会话重启也不会失效）。
> > 3. 路径写的是 `C:\ERP-ALL`，实际是 `D:\项目文件\ERP-ALL`。
> >
> > **部署机停得对，而且停得干净**：它没有自行把 `backend→api`、`worker→scraper`，
> > 也没有自行补 `-f`——**那正是「指令写错即停」该有的样子**。若它猜着改了，这三处缺陷
> > 就会被掩盖，下一次换人执行还会撞。#46 那七次停机的价值也在这里。
>
> > 〔**v3 修订，2026-07-30，起因是部署机在⑥-1 停机——缺陷仍在指令自身**〕
> >
> > v2 的⑥-1 把 JSON 字面量（`'{\"wpt\":...}'::jsonb`）塞进 PowerShell 双引号字符串，
> > 用 `\"` 转义内层双引号。**PowerShell 的转义字符是反引号（`` ` ``），不是反斜杠**——
> > 内层 `"` 直接终止了参数，psql 收到的是碎块（实测报
> > `extra command-line argument ... ignored` + `syntax error at end of input`）。
> >
> > **修法不是换转义，而是让双引号根本不出现**：改用 `jsonb_build_object` /
> > `jsonb_build_array` 构造 JSON——整条 SQL 只含单引号，PowerShell 双引号字符串
> > 原样透传，无转义可言。顺手消掉 `<TEAM_ID>` 手抄占位符：team_id 直接从
> > `$STORE_A` 那行 store 里 SELECT 出来，少一处抄错的机会。
> > 新写法已在沙箱干净库全程实跑通过（`INSERT 0 3`，三行 `master_sku` 均为 M+9 位）。
> > 全文同类扫描（PowerShell 双引号串内含 `\"`）：仅⑥-1 这一处，其余命令行要么无内层
> > 引号、要么引号全为单引号。

## 铁律（本次全程有效）

1. **绝不 `pg_restore` 进 `erp_all`**。暂存一律用一次性容器，用毕删容器 + 匿名卷。
2. **不输出任何密钥、口令、token、`Authorization` 头。**
3. **不改码、不 push、不 merge。**
4. **绝不对 `app.master_sku_seq` 执行 `setval`。** 详见下方「本单一条不能在真机验的判据」。
5. **本次唯一的写入是测试数据**：`product.source_ref` 以 **`B0R215V`** 开头的行，
   及其派生的 listing / 订单行。**不删、不改任何真实数据**；若某步要求你动的对象不带该前缀，
   **停下来问**。
6. 清理类 SQL 一律 `psql -1 -v ON_ERROR_STOP=1`——要么整段成功，要么整段回滚。

## 本单与 #46 最大的不同

- **#46 会真删数据，本单不删任何东西**（0042 只 `ALTER COLUMN ... SET DEFAULT`）。
- **但本单改了发给 Walmart 的 SKU**：新上架的 `channel_sku` 从 `master_sku`（M 号）
  变成 ASIN。故本指令含一步 **dry-run 渠道载荷核对**（铁律 4 的渠道写路径证据），见⑪。

## ⚠️ 本单一条不能在真机验的判据（**先读完再动手**）

工单判据④ 是「`master_sku` 序号超 999,999,999 时自动变长不截断」。**这条不要在生产库上验**：

要验它必须 `setval('app.master_sku_seq', 999999999)`，而那会让**此后所有真实产品的
master_sku 直接跳到 10 位**——序号按 D1「终身不变不回收」，跳过去就回不来了
（即便再 setval 回来，中间若有产品入库就已经拿到 10 位号且永久保留）。

**④ 已由 CI 测试直接断言**（`tests/db/test_r2_15_sku_split.py::TestMasterSku::test_no_truncate_at_large_sequence`，
把序列推到 999,999,998 / 999,999,999 / 99,999,999,999 各取一次）。**真机只验④的前半——
函数在生产库上存在且返回正确格式**，见④。若本指令某处让你 setval 该序列，那是指令写错了，
**停下来问**。

---

## ① 前置锚点 + **存量 master_sku 快照（承重，必须在迁移之前取）**

**为什么这一步必须在②切分支、③迁移之前**：判据⑤ 是「既有 `master_sku` / `channel_sku`
一行不动」。**「不动」只能靠「动之前的样子」来证**——迁移之后再取快照，拿它跟自己比
永远相等，那是个恒真判据。（#46 审查侧 N4 抓的就是这个形状：承重守卫站在了它要防的
那次动作的下游。）本快照在⑤与①对拍。

```powershell
cd 'D:\项目文件\ERP-ALL'   # 本机实际路径（部署机 2026-07-30 回帖确认）；若不同以实际为准
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
"colliding     = $($COLLIDING -join ', ')   (必须为空)"
docker compose -f infra/docker-compose.yml ps
```

取快照（**纯读**）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS product_rows, count(DISTINCT master_sku) AS distinct_sku, min(length(master_sku)) AS min_len, max(length(master_sku)) AS max_len, md5(string_agg(id || ':' || master_sku, ',' ORDER BY id)) AS sku_fingerprint FROM app.product;"
```

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS listing_rows, md5(string_agg(id || ':' || channel_sku, ',' ORDER BY id)) AS channel_sku_fingerprint FROM app.listing;"
```

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT last_value AS seq_before FROM app.master_sku_seq;"
```

**判据**：`tracked_dirty = 0`、`colliding` 为空、三条查询都有输出。

**贴回**：`origin/main` 的 sha、上面三行输出、compose 各容器状态，以及**务必原样贴回**：

- **两个 fingerprint** —— **⑤** 拿它对拍（判据⑤ 的承重依据）；
- **`seq_before`** —— **④** 拿它对拍（④ 的探针会消耗一个序号，差值应为 1）。

> `product_rows = 0` 或 `listing_rows = 0` 说明这台机器的库里没有存量数据。
> **那样⑤就退化成一个空判据，请在回帖里明说**——本单最需要真机的正是「有存量」这个前提，
> 没有存量则⑤不成立，需要 Owner 决定是否换一台有数据的环境。

---

## ② 切分支 + 自校验

```powershell
git checkout claude/r2-03-launch-leg5n8
git pull --ff-only origin claude/r2-03-launch-leg5n8
git log --oneline -1
git log --oneline -1 origin/claude/r2-03-launch-leg5n8
```

**判据**：本地与 remote 的 sha **相同**（在尖端）。

> 若 `git pull --ff-only` 报 divergent / abort：**这台机器的本地分支与云端历史分叉了**
> （云端 squash 合并过会造成这种情况）。**不要 force pull，也不要 reset --hard**，
> 直接改用：
> ```powershell
> git fetch origin
> git checkout -B claude/r2-03-launch-leg5n8 origin/claude/r2-03-launch-leg5n8
> ```
> 这条是「丢弃本地分支指针、直接指向 remote」，对工作区无破坏（①已确认 tracked_dirty=0）。
> 〔#46 第 2 次停机就是这个：指令写了 `git pull --ff-only`，而本地 `ahead 8, behind 18`，必然 abort。〕

迁移清单自校验：

```powershell
git diff --name-only origin/main...HEAD -- backend/alembic/versions/
```

**判据**：**只有 `0042_master_sku_no_truncate.py` 一个文件**。多或少都停下来问。

**贴回**：四个 sha、迁移清单输出。

---

## ③ 全栈重建并起服务（**判据查库里的状态，不查日志**）

```powershell
docker compose -f infra/docker-compose.yml build api beat migrate
docker compose -f infra/docker-compose.yml up -d db redis
docker compose -f infra/docker-compose.yml run --rm migrate
docker compose -f infra/docker-compose.yml up -d api beat
```

> **为什么只重建 `api` / `beat` / `migrate`**：这三个都 `build: ../backend`，而本 PR 的代码
> 改动**全部在 `backend/`**。另两个不需要重建，**也不要动它们**：
>
> | 服务 | build 来源 | 本 PR 改动 | 处置 |
> |---|---|---|---|
> | `api` / `beat` / `migrate` | `../backend` | 6 个文件 | **重建** |
> | `scraper` | `../workers` | **0 个文件** | 不重建，保持运行 |
> | `frontend` | `../frontend` | **0 个文件** | 不重建，保持运行 |
>
> 可自行复算：`git diff --name-only origin/main...HEAD -- workers/ frontend/` **应为空**。
> 故本单**没有** #46 那样的「前端产物对拍」步骤——前端一行没改，不存在「部署了但页面是旧的」。

### 迁移是否真的落地——**这一条是判据，且不看日志**

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT version_num FROM public.alembic_version;"
```

**判据**：`version_num = 0042`。

> **为什么不拿 `Running upgrade 0040 -> 0042` 当判据**：**迁移一旦应用过，重跑就不再打印它**。
> 拿它当判据的话，第二轮起这条永远不可能通过。〔#46 第 3 次停机（缺陷 5）就是这个。〕
> **日志是过程，`alembic_version` 是状态——判据只认状态。**

### 等 API 就绪（**不是立刻请求**）

```powershell
$READY = $false
foreach ($i in 1..30) {
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/healthz" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -eq 200) { $READY = $true; break }
  } catch { }
  Start-Sleep -Seconds 2
}
"api_ready = $READY   (必须 True)"
```

> `docker compose up -d` 返回只代表**容器被创建**，不代表进程在监听。
> 〔#46 第 3 次停机（缺陷 4）就是「起完就打 healthz」。〕

**贴回**：`version_num`、`api_ready`。

---

## ④ 迁移 0042 落地核对（**判据落在库里的实际定义与实际行为**）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT pg_get_functiondef('app.next_master_sku()'::regprocedure) AS fn_def;"
```

**判据**：函数存在，且定义里能看到 `IF length(s) < 9` 这个分支
（**不是** `to_char(...)`、**不是**无条件 `lpad(s, 9, ...)`）。

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT pg_get_expr(adbin, adrelid) AS master_sku_default FROM pg_attrdef WHERE adrelid = 'app.product'::regclass AND adnum = (SELECT attnum FROM pg_attribute WHERE attrelid = 'app.product'::regclass AND attname = 'master_sku');"
```

**判据**：默认值是 `app.next_master_sku()`，**不是** `'M'::text || lpad(...)`。

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT app.next_master_sku() AS probe;"
```

**判据**：形如 `M` + **至少 9 位纯数字**（例 `M000012345`），**不含任何 `#`**。

> **这一次调用会消耗一个 master_sku 序号**（比如 12345 用掉了）。这是预期的、无害的：
> 序号按 D1 「终身不变不回收」本就不复用，跳一个与真实入库跳一个等价。**请在回帖里
> 记下探针返回值与下面的 `seq_after`**，以便与①的 `seq_before` 对上（差值应为 1）。

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT last_value AS seq_after FROM app.master_sku_seq;"
```

**贴回**：`fn_def` 全文、`master_sku_default`、`probe`、`seq_after`。

> ⚠️ **不要 setval 这个序列**去试大序号——理由见开头「本单一条不能在真机验的判据」。

---

## ⑤ 判据⑤：存量一行没动（**与①的快照对拍**）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS product_rows, count(DISTINCT master_sku) AS distinct_sku, min(length(master_sku)) AS min_len, max(length(master_sku)) AS max_len, md5(string_agg(id || ':' || master_sku, ',' ORDER BY id)) AS sku_fingerprint FROM app.product;"
```

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS listing_rows, md5(string_agg(id || ':' || channel_sku, ',' ORDER BY id)) AS channel_sku_fingerprint FROM app.listing;"
```

**判据（承重）**：两个 fingerprint 与①**逐字相同**，`product_rows` / `listing_rows` 也相同。

> **v2 补：①已在 2026-07-30 由部署机实测，基线值记录在此**——若本轮①未重跑
> （例如本轮是从中途某步续跑的），直接与下表对拍即可；若①重跑过则以本轮①的输出为准。
>
> ```
> product_rows = 67   distinct_sku = 67   min_len = 8   max_len = 8
> sku_fingerprint         = 6891e1e85ead793ba7822645bd5234b1
> listing_rows = 25
> channel_sku_fingerprint = ee988913afa56508ccb26daa484d7c94
> seq_before = 2486
> ```
>
> **这组数字顺带把判据⑤的前提坐实了**：`min_len = max_len = 8` 说明现存 67 行**全部是
> 旧式 `M`+7**；而 `seq_before = 2486` ⇒ 新号将是 `M000002487`（`M`+9 = 10 字符）。
> **长度不同 ⇒ 新号不可能等于任何存量号**——这条在本机测试里是构造出来的，
> 在这台机器上是真实数据自己成立的。
>
> 另：`seq_before = 2486` 也说明**截断撞号那颗炸弹离得很远**（要到 1000 万才撞），
> 本单的近期价值是编码对齐而不是拆弹。

> 这是本单**唯一必须真机、CI 测不到**的一条：本机测试库里的「存量」是构造的，
> 而生产库里的存量是真的（含各种历史格式的 M 号、已在架 listing 的渠道 SKU）。
> **fingerprint 不同即代表迁移动了存量数据 → 立即停止并报回，不要继续。**

**贴回**：两个 fingerprint + 两个行数，并明确写「与①相同 / 不同」。

---

## ⑥ 造一次性测试数据（**本次唯一的写入，全部带 `B0R215V` 前缀**）

### ⑥-0 先清掉上一轮的残留（**每轮都要跑，哪怕你确信是第一轮**）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "DELETE FROM app.order_line WHERE channel_sku LIKE 'B0R215V%'; DELETE FROM app.listing_state_history WHERE listing_id IN (SELECT id FROM app.listing WHERE channel_sku LIKE 'B0R215V%'); DELETE FROM app.listing WHERE channel_sku LIKE 'B0R215V%'; DELETE FROM app.product WHERE source_ref LIKE 'B0R215V%';"
```

**判据**：整段成功（贴回四个 `DELETE n`，n 可以是 0）。

> **为什么每轮都要跑**：本指令若在中途某步停机，下一轮重跑时⑥-1 的 INSERT 会撞
> `uq_product`，**整段回滚，于是你会停在本轮内容之前**。
> **不要改用 `ON CONFLICT DO NOTHING` 绕过**——那样第二轮拿到的是上一轮的旧行，
> ⑦⑧的后缀判据会读到上一轮留下的 `-2`/`-3`，**在本轮一行都没造成时照样判绿**。

### ⑥-1 挑测试店 + 造三个产品

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT id, code, name, is_test, dedup_exempt FROM app.store WHERE is_test = true ORDER BY id;"
```

**判据**：至少有一个 `is_test = true` 的店。**记下它的 `id` 作 `$STORE_A`**。
若还有第二个 `is_test = true` 且 `dedup_exempt = true` 的店，记作 `$STORE_B`（⑨要用）；
**没有第二个就跳过⑨并在回帖说明**——不要为此新建店铺。

造产品（**只需把 `<STORE_A>` 换成测试店实际 id**；team_id 由 SQL 自己从该店取，
不用你抄。`-c` 后包住整条 SQL 的那对双引号是本命令**仅有的**双引号——
**SQL 载荷内只有单引号，不含任何 `\"`**。若你拿到的版本载荷里有 `\"`，
那是已修掉的 v2 旧版，停下来问）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "INSERT INTO app.product (team_id, source_channel, source_ref, title, attrs, price_snapshot, status) SELECT s.team_id, v.chan, v.ref, v.title, jsonb_build_object('wpt','Drinkware','bullets',jsonb_build_array('x'),'description','y'), jsonb_build_object('list',19.99), 'audit_passed' FROM app.store s, (VALUES ('amazon','B0R215V001','R2-15 验证品一'), ('amazon','B0R215V002','R2-15 验证品二'), ('1688','B0R215V003','R2-15 非amazon货源')) AS v(chan, ref, title) WHERE s.id = <STORE_A>;"
```

**判据**：`INSERT 0 3`。若是 `INSERT 0 0`，说明 `<STORE_A>` 填错（WHERE 没匹配到店），
**不要**改成不带 WHERE 的写法硬插——回⑥-1 开头重新确认店 id。

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT id, source_channel, source_ref, master_sku, length(master_sku) AS len FROM app.product WHERE source_ref LIKE 'B0R215V%' ORDER BY source_ref;"
```

**判据**：三行；**`master_sku` 都是 `M` + ≥9 位**（即新生成式已生效，这是④的第二重证据）。

**贴回**：店铺列表、三个产品的 id / master_sku / len。

---

## ⑦ 判据①：新上架的 `channel_sku` = ASIN，**且库里与 API 回显一致**

先取 token（**口令自己输入，不要贴回来**）：

```powershell
$CRED = Get-Credential -UserName "<你的管理员账号>" -Message "ERP 登录"
$LOGIN_BODY = @{ username = $CRED.UserName; password = $CRED.GetNetworkCredential().Password } | ConvertTo-Json
$LOGIN_RESP = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/auth/login" -Method Post -Body $LOGIN_BODY -ContentType "application/json"
$TOKEN = $LOGIN_RESP.access_token
"token_len = $($TOKEN.Length)   (>0 即可，不要贴 token 本身)"
```

分配（**`<PID_1>` 换成 `B0R215V001` 的产品 id，`<STORE_A>` 换成测试店 id**）：

```powershell
$ALLOC_BODY = @{ product_ids = @(<PID_1>); store_id = <STORE_A>; offer_mode = "match" } | ConvertTo-Json
$ALLOC_RESP = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/listings/allocate" -Method Post -Body $ALLOC_BODY -ContentType "application/json" -Headers @{ Authorization = "Bearer $TOKEN"; "Idempotency-Key" = [guid]::NewGuid().ToString() }
$ALLOC_RESP.created | ConvertTo-Json -Depth 4
$ALLOC_RESP.rejected | ConvertTo-Json -Depth 4
```

**判据**：`created` 一条，其 `channel_sku` **等于 `B0R215V001`**（不是 M 号）；`rejected` 为空。

回库对拍（**这一步不可省**）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT l.id, l.channel_sku, p.master_sku, p.source_ref FROM app.listing l JOIN app.product p ON p.id = l.product_id WHERE p.source_ref = 'B0R215V001';"
```

**判据（承重）**：库里的 `channel_sku` **等于 API 回显的那个值，且等于 `source_ref`**。

> **为什么要回库对一次**：D-Q72 与工单都把实现锚点写成响应回显那一行。
> **只改回显会造出「库里存 M 号、API 回报 ASIN」的静默分叉，而分叉方向恰好是
> 「看起来成功了」**——只看 API 输出永远发现不了。这一步就是专门堵它的。

**贴回**：`created` / `rejected` 的 JSON、库里那一行。

---

## ⑧ 判据②：同店下架后重上 → 自动取 `-2`、`-3`

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "UPDATE app.listing SET status = 'delisted' WHERE channel_sku = 'B0R215V001';"
```

再分配同一个产品到同一个店（命令同⑦，`Idempotency-Key` 换一个新 guid）：

**判据**：新的 `channel_sku` = **`B0R215V001-2`**。

再把这条置 `retired`，第三次分配：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "UPDATE app.listing SET status = 'retired' WHERE channel_sku = 'B0R215V001-2';"
```

**判据**：第三次拿到 **`B0R215V001-3`**。

汇总核对：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT channel_sku, status FROM app.listing WHERE channel_sku LIKE 'B0R215V001%' ORDER BY id;"
```

**判据**：三行，`channel_sku` 分别是 `B0R215V001` / `-2` / `-3`，**互不相同**。

> 这三条能共存正说明后缀扫的是**全状态**：`uq_listing` 不带状态过滤，`delisted`/`retired`
> 的旧 listing 照样占着那个 SKU，而团内去重只挡在架的——**「下架后重上」是主场景**。

**贴回**：三次分配的 `channel_sku`、汇总查询三行。

---

## ⑨ 判据③：跨店同 ASIN 互不冲突（**仅在⑥-1 找到 `$STORE_B` 时做**）

用 `B0R215V002` 分配到 `$STORE_B`（命令同⑦，换 product_id 与 store_id）。

**判据**：`channel_sku` = **`B0R215V002`**（裸 ASIN，**不带后缀**）。

先把它也分配到 `$STORE_A`，再分配到 `$STORE_B`，两边都应拿到裸 `B0R215V002`：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT store_id, channel_sku FROM app.listing WHERE channel_sku LIKE 'B0R215V002%' ORDER BY store_id;"
```

**判据**：两行、两个不同 `store_id`、`channel_sku` 都是 `B0R215V002` 不带后缀。

> `uq_listing` 是 `(store_id, channel_sku)`，换店即不撞，第二个店不该莫名退到 `-2`。
> **注意**：跨店同产品需要目标店 `dedup_exempt = true`（D-Q31 店铺豁免）——团内去重按
> `(product_id, team_id)` 判、**没有 store_id 条件**，不豁免的话第二次会被
> `LISTING_DUP_IN_TEAM` 挡掉，那是正确行为不是缺陷。

**贴回**：两次分配结果、汇总两行。若跳过本步，写明原因。

---

## ⑩ 判据⑦：回连失败**不得静默**（证伪型，构造一个查不到 listing 的订单行）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS notif_before FROM app.notification WHERE dedupe_key LIKE 'order_line_unlinked:%';"
```

构造：往一个测试订单里塞一个**库里没有对应 listing** 的 `channel_sku`。
最省事的做法是走 `order_pull` 的替身通道；若本机没有替身，则**跳过本步并说明**——
该判据已由 CI 测试覆盖（`test_unlinked_line_is_signalled`），真机只是加一层。

若能构造，判据为：

- `order_line.listing_id` **为 NULL**（前提成立：这一行确实接不上）；
- `app.notification` 里**新增一条** `dedupe_key LIKE 'order_line_unlinked:%'` 且 `severity = 'warn'`；
- `sync_state` 里该店 `order_pull` 的 `stats` **含 `unlinked_lines >= 1`**：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT ref_id, stats->>'unlinked_lines' AS unlinked FROM app.sync_state WHERE scope = 'order_pull' ORDER BY ref_id;"
```

> **原实现是完全静默的**：两个子查询落空返回 NULL 而 INSERT 仍成功，订单就此失去产品
> 关联，下游只有订单四检以 `source_missing` 间接暴露。本步验的就是「现在它会喊」。

**贴回**：`notif_before`、构造方式、上述三项，或「跳过 + 原因」。

---

## ⑪ 渠道写路径 dry-run：feed 载荷里的 SKU **是 ASIN**（铁律 4 证据；v4 改受控窗口）

**你上一轮在本步因网关为 `"live_test"` 停手，停得对。** 这台机器的网关档位是 Owner 的
决定，不是指令能给的授权。v4 起本节改为**受控 dry_run 窗口**流程，且有硬闸：

> **A-0 授权闸：只有在 PR #48 评论区看到 Owner 明文授权「开 dry_run 窗口」之后，
> 才允许执行 A-2 起的任何写操作。没看到就停在这里，本节其余全部不做。**
> 若 Owner 裁定采信云端沙箱替证（同一生产代码路径的载荷快照已在沙箱取得），
> 本节整体跳过，回执写「⑪ 跳过（Owner 裁定采信沙箱替证）」。

窗口的安全性依赖三件事：预检确认没有真实渠道命令在途（A-1）、停 beat 杜绝后台
消费（A-2）、窗口审计确认窗口内只发生了本步这一条命令（A-9）。dry_run 比 live_test
更保守（不发任何 HTTP），唯一风险是「窗口内真实命令被假完成」，三重措施正是堵它。

**A-1 预检**（只读，可先跑）——记下 `cc_max_id_before`，并要求无在途命令：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT coalesce(max(id),0) AS cc_max_id_before, count(*) FILTER (WHERE status IN ('pending','inflight','verify_pending')) AS cc_nonterminal FROM app.channel_command;"
```

**判据**：`cc_nonterminal = 0`。不为 0 → **停**，把两个数贴回执（有真实命令在途，窗口不能开）。

**A-2 停 beat**（杜绝窗口内后台任务消费渠道命令）：

```powershell
docker compose -f infra/docker-compose.yml stop beat
```

**A-3 切 dry_run 并回读**（配置每请求现读库，**立即生效，不需要重启任何服务**）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "UPDATE app.system_config SET value = to_jsonb('dry_run'::text) WHERE key = 'channel.gateway_mode'; SELECT value FROM app.system_config WHERE key = 'channel.gateway_mode';"
```

**判据**：回显为 `"dry_run"`。

**A-4 给测试产品补 upc**（云端沙箱以同形态数据实跑撞出的墙——本单第 6 条指令缺陷）：
match 模式构建器按 BR-LST-015 fail-closed：产品无 gtin/upc/ean 时 submit 直接
`ERP_SPEC_BUILD_FAILED`。⑥-1 的造数没带 upc。修法（仍只写 `B0R215V*` 测试行，铁律内；
upc 用校验位合法的标准测试号）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "UPDATE app.product SET attrs = attrs || jsonb_build_object('upc', '012345678905') WHERE source_ref = 'B0R215V001'; SELECT source_ref, attrs->>'upc' AS upc FROM app.product WHERE source_ref = 'B0R215V001';"
```

**判据**：`upc = 012345678905`。

> 若你在补 upc 之前已经试过 submit 并收到 `ERP_SPEC_BUILD_FAILED`：那条 listing 已转
> `failed`，补完 upc 后先重投再继续（`POST /api/v1/listings/<LISTING_ID>/retry`，
> 判据 202 且回包 `status = "queued"`），否则 submit 会报 `LISTING_STATE_INVALID`。

**A-5 提交**⑦造的那条 listing（取 `B0R215V001-3` 那条的 id，它是唯一非终态的）：

```powershell
$SUBMIT_BODY = @{ listing_ids = @(<LISTING_ID>) } | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/v1/listings/submit" -Method Post -Body $SUBMIT_BODY -ContentType "application/json" -Headers @{ Authorization = "Bearer $TOKEN"; "Idempotency-Key" = [guid]::NewGuid().ToString() } | ConvertTo-Json -Depth 6
```

**判据**：回包含 `dry_run: true` 且含 `request_snapshot`。submit 是请求内三段式，
快照当场落库——**本步不依赖 beat**（beat 正停着，不影响）。

**A-6 读库里那条渠道命令的载荷快照**：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT id, action, status, jsonb_pretty(result) AS result FROM app.channel_command ORDER BY id DESC LIMIT 1;"
```

**判据**：`result.request_snapshot` 存在；其 `json_body` 里该商品的 SKU 字段
**是 `B0R215V001-3` 这个 ASIN 形态的值**，且 **M 号（`M000002488`）0 次出现**；
`endpoint_key` 含 `MP_ITEM_MATCH`。

> 这是本单唯一触及渠道写路径的地方。**dry_run 档不会真的发出去**，但快照里就是「真发会发什么」
> （该快照本就不含明文凭证：headers 只存字段名单、proxy 已脱敏）。

**A-7 切回 live_test 并回读**（拿到 A-6 输出后立刻做，不要先写回执）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "UPDATE app.system_config SET value = to_jsonb('live_test'::text) WHERE key = 'channel.gateway_mode'; SELECT value FROM app.system_config WHERE key = 'channel.gateway_mode';"
```

**判据**：回显为 `"live_test"`。

**A-8 起回 beat**：

```powershell
docker compose -f infra/docker-compose.yml start beat
```

**A-9 窗口审计**——窗口内新增的渠道命令应当**只有 A-5 那一条**（`<CC_MAX_ID_BEFORE>`
填 A-1 记下的数）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT id, action, status, created_at FROM app.channel_command WHERE id > <CC_MAX_ID_BEFORE> ORDER BY id;"
```

**判据**：恰好一行，`action = feed_submit`、`status = succeeded`。多于一行 → 全部贴回执并**停**。

**贴回**：A-1 至 A-9 每步的关键输出；A-6 的 `result` **若含超长 base64 请截断，
只保留能看清 SKU 的部分**。

---

## ⑫ 迁移可逆性演练（在测试数据清完之后做）

先清测试数据（与⑥-0 逐字相同的四条）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -1 -v ON_ERROR_STOP=1 -c "DELETE FROM app.order_line WHERE channel_sku LIKE 'B0R215V%'; DELETE FROM app.listing_state_history WHERE listing_id IN (SELECT id FROM app.listing WHERE channel_sku LIKE 'B0R215V%'); DELETE FROM app.listing WHERE channel_sku LIKE 'B0R215V%'; DELETE FROM app.product WHERE source_ref LIKE 'B0R215V%';"
```

```powershell
docker compose -f infra/docker-compose.yml run --rm migrate alembic downgrade 0041
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT version_num FROM public.alembic_version;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT pg_get_expr(adbin, adrelid) AS default_after_downgrade FROM pg_attrdef WHERE adrelid = 'app.product'::regclass AND adnum = (SELECT attnum FROM pg_attribute WHERE attrelid = 'app.product'::regclass AND attname = 'master_sku');"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS fn_exists FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname = 'app' AND p.proname = 'next_master_sku';"
```

**判据**：`version_num = 0041`；默认值回到 `lpad(...)` 形态；`fn_exists = 0`（函数已删）。

```powershell
docker compose -f infra/docker-compose.yml run --rm migrate
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT version_num FROM public.alembic_version;"
```

**判据**：回到 `0042`。

> ⚠️ **降级期间不要让任何产品入库**（回滚后的生成式带着原截断缺陷）。本演练在测试数据
> 清完后立即做完并升回来，窗口只有几分钟。若这台机器上有 beat 在跑采集，**先停 beat**：
> `docker compose -f infra/docker-compose.yml stop beat`，⑬再起回来。

**贴回**：四条判据的输出。

---

## ⑬ 收尾（**顺序不可换：先升回 0042，再切分支**）

⑫已升回 0042。现在：

```powershell
docker compose -f infra/docker-compose.yml start beat        # 若⑫停过
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS leftover_products FROM app.product WHERE source_ref LIKE 'B0R215V%';"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS leftover_listings FROM app.listing WHERE channel_sku LIKE 'B0R215V%';"
```

**判据**：两个都是 0。

**是否切回 main**：**本单结束时留在验证分支**，等 Owner 合并 PR #48 后再按常规切回。
不切回**不是因为兼容**：库停在 0042 与 main 树**不兼容**——main 的迁移链到 0041、
没有 0042 这个 revision，compose 起服务时 `migrate`（`alembic upgrade head`）遇到库里
未知的 `version_num` 会**硬失败**，而 `api`/`beat` 都等它成功才启动 ⇒ **服务全停**
（#46 第 7 次停机就是这个形状）。所以**任何**切回 main 的操作**之前必须先降到 0041**；
「降库」与「切分支」是两个独立动作，顺序永远是**先降后切**。本单两个都不做。

最后再确认①的两个 fingerprint 仍未变（**测试数据已清，应当与①、⑤三者一致**）：

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -v ON_ERROR_STOP=1 -c "SELECT count(*) AS product_rows, md5(string_agg(id || ':' || master_sku, ',' ORDER BY id)) AS sku_fingerprint FROM app.product;"
```

**判据**：与①的 `sku_fingerprint` 相同。

**贴回**：残留清点、最终 fingerprint、compose ps。

---

## 回帖格式

请按下表逐条回，**每条写「过 / 不过 / 跳过」+ 关键输出**：

| 步 | 内容 | 结果 |
|---|---|---|
| ① | 锚点 + 存量快照（两个 fingerprint、seq_before） | |
| ② | 分支尖端 + 迁移清单只有 0042 | |
| ③ | `alembic_version = 0042` + `api_ready = True` | |
| ④ | 函数定义含 `IF length(s) < 9`、默认值是 `app.next_master_sku()`、探针 M+≥9 位无 `#` | |
| ⑤ | **承重**：两个 fingerprint 与①相同 | |
| ⑥ | 清残留四条 + 三个产品 master_sku 都 M+≥9 位 | |
| ⑦ | **承重**：`channel_sku` = ASIN，且库里 == API 回显 | |
| ⑧ | `-2`/`-3` 依次分配，三条互不相同 | |
| ⑨ | 跨店两边都是裸 ASIN（或跳过 + 原因） | |
| ⑩ | 回连失败有信号（或跳过 + 原因） | |
| ⑪ | A-0 授权闸后：A-1~A-9 全过、载荷 SKU 是 ASIN（或 Owner 裁定跳过） | |
| ⑫ | 降级到 0041 → 默认值回旧式、函数消失 → 升回 0042 | |
| ⑬ | 残留 0、fingerprint 与①一致 | |

另请附：本次实际的 `HEAD` sha、`origin/main` sha、以及**任何你觉得指令写错或写不清的地方**
——上一单（#46）指令被打回七次，九条缺陷全部是指令自身的问题、产品代码零缺陷。
**你停得对的时候就是在帮我修指令。**
