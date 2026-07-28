# PR #42 真机验证指令（给部署 AI，可整段粘贴）

> 合并前闸序**第三闸**。第一闸 CI 四项绿；第二闸独立审查**五轮全通过、无阻拦项**
> （F1/N2 已修并经审查侧变异实测；新增的前端渲染判据亦由其独立起 worktree 实测可红）。
>
> **v2（2026-07-28 重发）——上轮在 UI 判据 C-unset 停手，缺陷已修**：未配置行的档位
> 选择器曾显示「人工」为选中态（`?? undefined` 命中 rc-segmented 回落到第一个选项），
> 把 unset≠configured-as-manual 就地抹平；且点它不触发 change，「显式设为人工」发不出
> 请求。已修为 `?? ''`，CI 里新增的渲染判据钉住这两条。**本轮 C-unset 应转过。**
>
> **另：team 2「R2-02对拍」残留已按 Owner 终裁清除**（产品 400 + 规格产物 4 + 流水 1580
> + 用户/团队；审计三族保留）。故本机现在**只有 team 1**，第 3 步判据随之收紧（见该步）。
>
> **本单与 #41 有三点不同，先记住再动手**：
> ① **含一条迁移 `0040`**（新增权限码 `automation.read`/`automation.write` + 授团队管理员 +
>    回填既有团队副本）——migrate 服务执行它是**预期**，不是异常；
> ② 有**前端新页 `/automation`** 与 UI 人工判据（仓内 runbook §② 四项）；
> ③ **切回 main 之前必须先 `alembic downgrade 0039`**——main 的代码只认到 0039，
>    DB 停在 0040 时 main 的 migrate 服务会报 `Can't locate revision '0040'` 起不来。
>    0040 的降级只删那两个权限码与其授权，不动任何档位数据与业务表。

---

## 铁律（每次都读一遍，不许跳）

1. **绝不 `pg_restore` 进 `erp_all`**。本文对 `erp_all` 的写操作**只有三种**：migrate 服务跑
   0040（第 1 步）、你在面板 UI 上对**测试团队**做的改档（第 4 步）、按第 4/6 步写明的
   还原/清理 SQL。除此之外只读。
2. **不输出密钥**：贴回结果前自查，命令与输出里不得含口令、`client_secret`、token、代理账号密码。
   **`infra/.env` 的内容一个字都不要贴回来**。UI 验证用的账号**只贴用户名不贴口令**；
   若用浏览器开发者工具看请求，**不要把请求头里的 Authorization 贴回来**。
3. **不改码、不 push、不 merge**。发现问题就停下贴回，**不要自行「修一下再试」**。
4. 若任一步骤报错：**停在那一步**，把该步的完整报错贴回，不要继续往下跑。
5. **判成败一律看退出码与写明的判据**，不要看输出里有没有红字。UI 步骤按 runbook 逐条报「过/不过」。
6. **本分支尚未合并**。第 7 步必须降级 0040 并把这台机切回 `main`。
7. UI 改档**只动测试团队**；§C 的非法档位 SQL 用完必须按给出的还原语句改回去。

---

## 前置：锚点与切分支（自校验，无写死 sha）

```powershell
cd <ERP-ALL 仓库根>
git fetch origin main
git fetch origin claude/r2-03-launch-leg5n8
git rev-parse --short HEAD
git status --porcelain --untracked-files=no
echo "TRACKED_DIRTY_EXIT=$LASTEXITCODE"
```

**贴回①**：`rev-parse` 一行 + `git status --porcelain --untracked-files=no` 的输出。
**期望后者为空**（未跟踪文件无害，不看）。非空 = 有人改过已跟踪文件，停下贴回。

```powershell
# 必须 -B：git fetch 不更新已存在的同名本地分支（此前实际踩到）
git checkout -B claude/r2-03-launch-leg5n8 origin/claude/r2-03-launch-leg5n8
echo "CHECKOUT_EXIT=$LASTEXITCODE"
git rev-parse --short HEAD
git rev-parse --short origin/claude/r2-03-launch-leg5n8
git status -sb
git log --oneline -3
```

**贴回②**：`CHECKOUT_EXIT` + 两个 `rev-parse` + `status -sb` + 三行 log。
**判据**：`CHECKOUT_EXIT=0`；**两个 `rev-parse` 完全相同**；`status -sb` 无
`ahead`/`behind`/`diverged`。

---

## 第 1 步：起服务（会重建镜像 + **会执行迁移 0040，属预期**）

```powershell
cd infra
docker compose up -d --build
echo "UP_EXIT=$LASTEXITCODE"
docker compose ps -a
docker compose logs migrate | Select-String -Pattern "Running upgrade|0040|error" | Select-Object -First 10
```

**贴回③**：`UP_EXIT`（期望 `0`）+ `ps -a` 表格（migrate 行应 `Exited (0)`）+ 日志匹配行。
**期望日志出现 `Running upgrade 0039 -> 0040`**。
若 migrate 报错或 `Exited` 非 0：停下贴回。

> 前端也是 compose 服务（`frontend`，nginx 静态伺服），`up -d --build` 已一并重建。
> 但浏览器仍可能缓存旧页——**第 4 步开页面前先 Ctrl+F5 硬刷新**。

---

## 第 2 步：确认容器跑的是新代码 + 等就绪

```powershell
docker compose exec -T api python -c "from erp.automation.router import automation_router; from erp.core.automation import WIRED_FLOWS; print('WIRED=', len(WIRED_FLOWS)); print('OK')"
echo "PROBE_EXIT=$LASTEXITCODE"
```

**贴回④**：输出 + `PROBE_EXIT`。**期望 `WIRED= 3` 与 `OK`，`PROBE_EXIT=0`**。

> `erp/automation/router.py` 是本单新增的包，`main` 上不存在——它能 import 就证明
> 容器跑的是分支代码。这条同时是第 7 步的负向判据（切回 main 后必须失败）。

```powershell
$code = "000"
foreach ($i in 1..30) {
  $code = (curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8000/healthz)
  if ($code -eq "200") { break }
  Start-Sleep -Seconds 2
}
"HEALTH_CODE=$code (after $i tries)"
```

**贴回⑤**：`HEALTH_CODE`（期望 `200`）。轮询中 curl 的非 0 退出码不是判据；60 秒仍非 200
才停，并附 `docker compose ps` 与 `docker compose logs --tail 60 api`。

---

## 第 3 步：迁移落库取证（只读 SQL）

```powershell
$sql = @'
\pset pager off
-- A. 迁移版本（注意表在 public，不在 app）
SELECT version_num FROM alembic_version;

-- B. 权限码种进来了吗（应恰好 2 行，module=automation）
SELECT code, module FROM app.permission WHERE code LIKE 'automation.%' ORDER BY code;

-- C. 模板 + 每个团队的各角色拿到几个 automation 码（T11 回填判据的真机版）
--    ⚠️ **SQL 里不写中文字面量**：2026-07-28 实测，含中文的 WHERE 经 PowerShell 管道
--    进 psql 后匹配不上任何行（全篇唯一含中文的查询恰是唯一返回 0 行的），被误判成
--    「角色全没了」而停手。改为不带谓词全量列出，由人读哪一行是团管——更不易出错也更信息量足。
SELECT coalesce(r.team_id::text, '(模板)') AS role_scope, r.name AS role_name,
       count(*) FILTER (WHERE rp.permission_code LIKE 'automation.%') AS automation_codes
FROM app.role r
LEFT JOIN app.role_permission rp ON rp.role_id = r.id
GROUP BY r.team_id, r.name ORDER BY r.team_id NULLS FIRST, r.name;

-- D. 档位表现状（第 6 步清理判据要用，原样贴回）
SELECT count(*) AS policy_rows FROM app.automation_policy;
SELECT team_id, flow_code, mode, enabled FROM app.automation_policy ORDER BY team_id, flow_code;

-- E. 自检：C 段应有 (团队数 + 1) 行
SELECT count(*) AS total_teams FROM app.team;
'@
$sql | docker exec -i erp-all-db-1 psql -v ON_ERROR_STOP=1 -U postgres -d erp_all
echo "CENSUS_EXIT=$LASTEXITCODE"
```

**贴回⑥**：A-E 全部输出 + `CENSUS_EXIT`。

**期望**：
- `CENSUS_EXIT=0`；A 段 = `0040`；B 段恰好 2 行；
- **C 段：`role_name` 为团队管理员的那些行，`automation_codes` 必须都是 `2`**；其余角色为 `0` 属正常
  （0040 只授团管）。**本轮 team 2 已清除，故团管应恰好 2 行：`(模板)` 与 `1`；`total_teams = 1`。**
  若团管行的 `automation_codes` < 2 → **停下贴回**（回填缺口）；若整张表**一个角色都没有**
  → 那才是真异常（角色由 0002 种子 + 建团复制而来）。
  任何一行是 0 或 1 → **停下贴回**：要么该团队把「团队管理员」改过名（0040 按名匹配，
  匹配不上静默跳过），要么回填有缺口——这正是「既有团队看不到面板且无任何报错」的成因。
- D 段预期 0 行（增量1 对拍时该表为空）。**非 0 不是失败**，但把行贴回——第 6 步只清理
  验证期间新建的行，需要这份「验证前底账」。

> 容器名若不是 `erp-all-db-1`，用 `docker compose ps` 里 db 那行的真实名字替换。

---

## 第 4 步：UI 四项人工判据（本闸核心，**必须非超管账号**）

详细步骤与判据在仓内 **`.agent/evidence/R2-09/runbook-increment2.md`**（你已经检出的分支上就有，
§① 前置与 §② A-D 四项）。此处只列要点与贴回格式；**逐条按 runbook 原文执行**。

**前置一（产物对拍，UI 判据之前必做）**：本项目「浏览器跑旧产物」已复发三次
（HF-0716①、FE-0716、2026-07-28 本单 C-unset 假阳性），**只靠 Ctrl+F5 的口头约定挡不住**。
改为机器判据——两个 chunk 名必须相同才继续：

```powershell
docker compose exec frontend sh -c "ls /usr/share/nginx/html/assets/index-*.js"
```
再在页面控制台执行 `document.querySelector('script[type=module]')?.src`。
**两者文件名不一致 → 浏览器拿的是缓存旧壳，任何 UI 判据结论都不作数**：
DevTools 勾 Disable cache 后 Ctrl+Shift+R（或关标签页重开），直到一致为止。

**前置二**：用一个**绑了「团队管理员」角色的普通账号**登录（不是超管——超管权限恒短路，
验不出授权）。**上轮建的 `pr42_verify`（team 1，团管+采集员）仍在，直接复用即可**，不必新建。

- **A 改档**（`pricing_watch` 点「半自动」）：绿提示 + **不弹**确认框 + 状态变蓝「半自动」+
  开关自动变启用 + 最后修改列出现「时间 · 用户 #id」。五条全中才算过。
- **B 闸类二次确认**（`order_block`）：切「全自动」**不弹**；**B1** 再点「人工」→ 弹红按钮
  确认框（含「只软标记不冻结」「拦截将不生效」），点取消档位不变，确认后红字「拦截当前不生效」
  重现；**B2** 切回全自动后关「启用」→ 同样弹红确认，确认后橙 Tag「已停用」且红字仍在；
  **B反向** 对 `pricing_watch` 做同样两个动作**都不得弹**；**B补充（必验）** 用 §C 的 SQL 把
  `order_block` 造成 `mode='semi'` 后点「人工」→ **同样必须弹**（semi 在订单闸上真在拦截）。
- **C 三态渲染（本轮重点：上轮就卡在 C-unset）**：`unset` 行除灰 Tag「未配置（等同人工）」、
  开关关、最后修改「—」之外，**档位选择器必须无任何选中项**（不得有
  `ant-segmented-item-selected`）——这正是上轮报的缺陷，已修。再验一条连带症：
  **在未配置行上点「人工」必须真的保存成功**（绿提示 + 状态转为「人工」），
  上轮那个版本点了毫无反应。其余：`disabled` 橙「已停用」/ `configured` 档位 Tag /
  非法档位红 Tag「档位非法（semi），实际按「半自动」生效」。**要害**：非法档位必须显示
  「按 semi 原样生效」——若显示成「人工」，说明有一侧擅自改写，**必报缺陷**。
  另验：`compliance_block` 行有灰 Tag「未接线（改档暂不生效）」且**无**红字/无「拦截生效」
  字样（它没接消费点，这是真话）；`order_block` 未配置时有一条红字（也是真话，不是 bug）。
- **D 无权限只读**：只挂 `automation.read` 的账号（重新登录）→ 菜单在、10 行可见、
  「只读（缺 automation.write）」标签在、控件全灰不可点。再摘掉 read 重登 → 菜单消失。

  > **凭据自助，不要为此等 Owner**（2026-07-28 在此卡过一次）：本机已有 super 凭据，
  > 而超管足以自造本步所需账号——`POST /api/v1/users`（收 `password`，≥8 位）、
  > `PATCH /api/v1/users/{id}`（可重置密码）、`POST /api/v1/roles` +
  > `PUT /api/v1/roles/{id}/permissions`（造只挂 `automation.read` 的角色）、
  > `PUT /api/v1/users/{id}/roles`（挂角色）。UI「用户管理 / 角色管理」页同款操作。
  > **密码你自己生成、只写进本机密码文件，永远不要贴进对话。**
  >
  > **PowerShell 两处坑（2026-07-28 各踩一次）**：① 没有 `crypto` 全局对象（那是 Node 的），
  > 安全随机要走 .NET；② **`$pwd` 是自动变量（当前目录），不能拿来存口令**——换个名字。
  >
  > ```powershell
  > $b = New-Object byte[] 24
  > $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
  > $rng.GetBytes($b); $rng.Dispose()
  > $newPwd = ([Convert]::ToBase64String($b) -replace '[+/=]','x')   # 32 位，勿打印
  > ```
  > `RandomNumberGenerator::Create()` 在 Windows PowerShell 5.1 与 PS 7 都在。

**§C 非法档位造数与还原**（只打测试团队，`<团队id>` 用你实际测试团队的 id）：

```sql
-- 造（验 B补充 与 C 的非法档位行时用）
UPDATE app.automation_policy SET mode='semi', enabled=true
WHERE team_id=<团队id> AND flow_code='order_block';
-- 还原（验完立刻执行）
UPDATE app.automation_policy SET mode='auto', enabled=true
WHERE team_id=<团队id> AND flow_code='order_block';
```

**贴回⑦**：`A: 过/不过`、`B1/B2/B反向/B补充: 各 过/不过`、`C: 四态+未接线 各 过/不过`、
`D: 过/不过`，不过的写清现象（弹没弹框、Tag 文案、报错码）。**B 段任何一条确认框缺失都是
一票否决**——那是本页唯一必须挡在动作之前的东西。

---

## 第 5 步：审计链取证（只读）

```powershell
$sql = @'
\pset pager off
SELECT id, team_id, actor_id, action,
       (before IS NULL) AS is_first_write, after
FROM app.audit_log
WHERE action = 'automation.policy_set'
ORDER BY id DESC LIMIT 10;
'@
$sql | docker exec -i erp-all-db-1 psql -v ON_ERROR_STOP=1 -U postgres -d erp_all
echo "AUDIT_EXIT=$LASTEXITCODE"
```

**贴回⑧**：输出 + `AUDIT_EXIT`。
**期望**：行数 ≥ 你在第 4 步做的改档次数；每行 `after` 含 `flow_code`/`mode`/`enabled`；
对某 flow 的**首次**写行 `is_first_write = t`。0 行 = 审计链断了，停下贴回。

---

## 第 6 步：清理验证痕迹（**条件执行**）

> D 步若自造了临时角色/账号：临时账号 `PATCH` 成 `status=disabled`、临时角色权限清空即可
> （本仓无用户删除端点）。**不要动 team 1 既有的「团队管理员」角色**——那是在跑的授权。

**仅当第 3 步 D 段是 0 行（验证前表空）时执行**——那么现在表里的行全部是第 4 步造的：

```powershell
$sql = @'
DELETE FROM app.automation_policy WHERE team_id = <团队id> RETURNING flow_code, mode;
'@
$sql | docker exec -i erp-all-db-1 psql -v ON_ERROR_STOP=1 -U postgres -d erp_all
echo "CLEANUP_EXIT=$LASTEXITCODE"
```

**贴回⑨**：RETURNING 的行 + `CLEANUP_EXIT`。删的应当就是你第 4 步动过的那几条 flow。

- 若第 3 步 D 段**非** 0 行：**不要执行本步**，写明「D 段非空，跳过清理」并把 D 段行贴回，
  由 Owner 决定怎么处理。**绝不删验证前就存在的行。**
- `audit_log` 一行都不删——审计就是用来留底的。

---

## 第 7 步：降级 0040 → 切回 main（**必做，顺序不能反**）

**先降级再切分支**。main 只认到 0039，DB 停在 0040 时 main 的 migrate 起不来。

```powershell
docker compose run --rm migrate alembic downgrade 0039
echo "DOWNGRADE_EXIT=$LASTEXITCODE"
$sql = @'
SELECT version_num FROM alembic_version;
SELECT count(*) AS automation_codes FROM app.permission WHERE code LIKE 'automation.%';
'@
$sql | docker exec -i erp-all-db-1 psql -v ON_ERROR_STOP=1 -U postgres -d erp_all
echo "DOWNGRADE_CHECK_EXIT=$LASTEXITCODE"
```

**贴回⑩**：`DOWNGRADE_EXIT`（期望 0）+ 两条 SQL 输出（期望 `0039` 与 `0`）。

```powershell
cd <ERP-ALL 仓库根>
git checkout -B main origin/main
git rev-parse --short HEAD
git rev-parse --short origin/main
cd infra
docker compose up -d --build
echo "MAIN_UP_EXIT=$LASTEXITCODE"
docker compose exec -T api python -c "from erp.automation.router import automation_router" 2>&1
echo "MAIN_PROBE_EXIT=$LASTEXITCODE"
```

**贴回⑪**：两个 `rev-parse`（**必须相同**）+ `MAIN_UP_EXIT`（期望 0）+ probe 输出与
`MAIN_PROBE_EXIT`。**probe 必须失败**（`ModuleNotFoundError`，`MAIN_PROBE_EXIT` 非 0）——
它成功了说明切换没生效，这台机还跑在未合并分支上，停下贴回。

```powershell
$code = "000"
foreach ($i in 1..30) {
  $code = (curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8000/healthz)
  if ($code -eq "200") { break }
  Start-Sleep -Seconds 2
}
"MAIN_HEALTH_CODE=$code (after $i tries)"
```

**贴回⑫**：`MAIN_HEALTH_CODE`（期望 `200`）。

---

## 汇总：一共 12 条贴回

①head+status ②checkout 自校验 ③`UP_EXIT`+ps+migrate 日志（应见 0039→0040）
④新包 probe（`WIRED= 3`）⑤健康码 ⑥迁移取证 A-E ⑦UI 四项逐条过/不过
⑧审计行 ⑨清理（或写明跳过原因）⑩降级取证（`0039` / `0`）⑪切 main 自校验+**必须失败**的 probe
⑫切回后健康码

**一票否决项**（命中任一即停）：
- 前置②两个 `rev-parse` 不同，或 ahead/behind/diverged
- 第 1 步 migrate 报错或 `Exited` 非 0
- 第 2 步 `WIRED` 不是 3 / import 失败 / 健康码非 200
- 第 3 步 C 段任何一行 `automation_codes` < 2（回填缺口，即「既有团队看不到面板」）
- 第 4 步 B 段任何一条确认框缺失；C 段非法档位被显示成「人工」（有一侧在改写）
- 第 7 步降级后 `automation_codes` 非 0；切 main 后 probe **没有**失败；健康码非 200

---

## 这份指令自己的已知局限（如实写明）

1. **UI 判据是人工目验**，不产退出码——按 runbook 原文逐条报「过/不过」，不过的写现象。
2. **不验 `order_block` 真实拦截链**（要造采购执行单，不值得为验证写业务数据）；
   拦截逻辑等价性由 CI 测试与审查侧实测覆盖，本闸验的是「面板↔库↔审计」三角。
3. `compliance_block` 等 7 条 flow **未接线**，改档无行为变化是**预期**（面板有灰 Tag 标注），
   验收时不得当缺陷报（runbook §④）。
4. 验证结束时 DB 回到 0039、代码回到 main、档位表回到验证前状态；audit_log 里会多出
   验证期间的 `automation.policy_set` 行——**这是留痕不是残留**，不清理。
