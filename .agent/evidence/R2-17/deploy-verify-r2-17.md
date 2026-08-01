# R2-17 单人模式收口 ③闸真机验证单 v1（验收①—⑤；D 编号，与清场单 C 编号不混）

> **给部署 AI。整段可粘贴，逐步执行，每步贴回输出。**
> 铁律照旧：**audit_log 一行不删**；不改码不 push；**不输出密钥**——本单两处要往 `.env`
> 写值（单人模式开关、插件共享 token），值由部署机本地生成/设置，**回执只记
> `token_len=<N>`，绝不贴明文**；一步不过就停下来贴现场，不自行补救。
>
> **前置（两条都要）**：① 清场单 v3 的 C6 停点已由 Owner 裁定处置完毕（多余 PO 已按
> 裁定拆除或基线已修订）；② Owner 授权在部署机上检出 PR #50 分支做本轮验证。
> 现场已知量：机器现在 `main@8cc90d1`、DB `0047`、服务健康（C5 回执）。

## D1 检出验证分支并锚定

```powershell
cd D:\项目文件\ERP-ALL
git status --porcelain
git fetch origin claude/r2-03-launch-leg5n8
git checkout claude/r2-03-launch-leg5n8
git merge --ff-only origin/claude/r2-03-launch-leg5n8
git rev-parse HEAD
git rev-parse origin/claude/r2-03-launch-leg5n8
```

**判据**：porcelain 无 tracked 脏（`.codex/`、`AGENTS.md`、`RS-02a-runbook.md` 既有未跟踪
照旧可留）；两条 `rev-parse` **逐字相等**（锚定等值）。截至本单落笔 head =
`e55d493`；若届时分支已前移，以等值闸为准并把实际 sha 记回执。

## D2 部署（迁移 0047 → 0048 链）

```powershell
docker compose -f infra/docker-compose.yml build api frontend migrate
docker compose -f infra/docker-compose.yml run --rm migrate
docker compose -f infra/docker-compose.yml up -d --force-recreate api beat frontend
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -q -tA -c "SELECT version_num FROM alembic_version;"
Start-Sleep -Seconds 8
(Invoke-WebRequest -Uri "http://127.0.0.1:8000/healthz" -UseBasicParsing -TimeoutSec 5).StatusCode
```

**判据**：三个 EXIT 全 0；`version_num=0048`（0043→0046 插件族 + 0048 认领闸放松，
均在链上）；healthz `200`（失败等 10 秒重试一次）。

## D3 开单人模式 + 验收①（免登录直达产品页）

在 `.env` **追加**两行（值不回贴）：`ERP_SINGLE_USER_MODE=true`；
`ERP_PLUGIN_SHARED_TOKEN=<本地生成的 ≥32 位随机串>`（PowerShell 可用
`-join ((48..57)+(97..122) | Get-Random -Count 40 | % {[char]$_})` 生成后手工粘入 .env）。

```powershell
docker compose -f infra/docker-compose.yml up -d --force-recreate api
Start-Sleep -Seconds 8
$me = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/me" -UseBasicParsing -TimeoutSec 5
$me.StatusCode
$me.Content
```

**判据**：**无 Authorization 头**的 `/me` 回 `200`，body 里 `"username":"admin"` 且
`"is_super":true`（回执贴 body 原文，内含无密钥）。随后**人工一步**：浏览器直开前端地址
⇒ 不经登录页直达工作台，右上角显示 admin（超管）——贴一句「已目检，直达/未直达」。

## D4 验收③（导航摘除，人工目检）

浏览器左侧导航**不得出现**：成员管理、角色权限、插件实例；顶栏**无团队切换器**。
回执贴一句「已目检，四项均无/发现 XX」。

## D5 验收④（插件共享 token：带 token 可拉、不带则拒）

```powershell
$T = "<粘 .env 里的 token 明文，仅本会话变量，不入回执>"
(Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/purchase-plugin/getNeedPurchaseOrders?customerId=A17VERIFY" -UseBasicParsing -TimeoutSec 5 -SkipHttpErrorCheck).StatusCode
Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/purchase-plugin/getNeedPurchaseOrders?customerId=A17VERIFY" -UseBasicParsing -TimeoutSec 5 -Headers @{"X-Plugin-Token"=$T} | Select-Object -ExpandProperty Content
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -q -tA -c "SELECT status, label FROM app.buyer_account WHERE external_customer_id = 'A17VERIFY';"
$T = $null
```

**判据**：不带 token ⇒ `401`；带 token ⇒ `{"code":200,"data":[]}`（通道开、无指派单故空）；
第三条 ⇒ `active|`（**17d 首见即 active**、label 空——顺带验到增量3）。回执记
`token_len=<N>`，不贴明文。

## D6 验收②（audit_log 三流可辨）

```powershell
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -q -tA -c "SELECT actor_type, action FROM app.audit_log WHERE action = 'buyer_account.auto_register' ORDER BY id DESC LIMIT 1;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -q -tA -c "SELECT actor_type, count(*) FROM app.audit_log WHERE actor_type = 'user' GROUP BY 1;"
docker compose -f infra/docker-compose.yml exec -T db psql -U erp_migrator -d erp_all -q -tA -c "SELECT count(*) FROM app.notification;"
```

**判据**：第一条回 `system|buyer_account.auto_register`（D5 刚产生的机器流）；第二条
`user` 计数 ≥1（历史人工操作即可；若为 0，在 UI 里做一次任意写操作再查）；第三条 ≥1
（报错/告警流的载体在，含 D5 首见登记那条 warn）。三流各有归属即过。

## D7 验收⑤（休眠可逆：关开关登录流程原样回来）

把 `.env` 里 `ERP_SINGLE_USER_MODE` 改为 `false`（**保留** `ERP_PLUGIN_SHARED_TOKEN`）：

```powershell
docker compose -f infra/docker-compose.yml up -d --force-recreate api
Start-Sleep -Seconds 8
(Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/v1/me" -UseBasicParsing -TimeoutSec 5 -SkipHttpErrorCheck).StatusCode
```

**判据**：无凭证 `/me` 回 `401`；**人工一步**：浏览器刷新 ⇒ 回到登录页，用既有账号可正常
登录。贴「已目检」。**收尾**：把 `ERP_SINGLE_USER_MODE` 改回 `true` 并再
`up -d --force-recreate api`（单人模式是本机常驻形态，⑤只验可逆性）。

## D8 终态回执

全过 ⇒ 回执贴回 PR #50：D1 sha、D2 版本链与 healthz、D3/D4/D7 目检结论、D5 三条输出
（token 只记长度）、D6 三流输出。这即解冻三条件里「验收①③④过 + 部署机回执」的取证面。
