# RS-02a 部署指令：换密钥 + 收端口（给部署 AI，可整段粘贴）

> D-Q68 / 审计侧 2026-07-27 P0。代码侧改动已在 PR 分支上；本文是**机器侧**那一半。
> 两半必须按下面的顺序做——**顺序错了不是报错，是「改了 env 但旧口令照样能用」
> 或者「应用连不上库」**，两种都不好排查。

## 铁律（每次都读一遍，不许跳）

1. **绝不 `pg_restore` 进 `erp_all`**。本文只做 `pg_dump`（只导不入）、`ALTER ROLE`、
   一次凭证重加密，不导入任何转储。
2. **不输出密钥**。下面所有步骤都设计成「口令从文件读进变量，不回显」。贴回结果前自查：
   命令与输出里不得含口令、`client_secret`、token、代理账号密码。
   **`infra/.env` 的内容一个字都不要贴回来。**
3. 任一步骤报错：**停在那一步**，把该步的完整报错贴回，不要继续、也不要自行「修一下再试」。
4. **判成败一律看退出码**，不看输出长得像不像成功。每步都印了 `*_EXIT`，贴回时带上。
5. 本文**不需要**一次性容器（没有转储暂存环节）。

## 时间窗（**先看这段**）

第 4～6 步之间服务是停的（约 5–15 分钟）。**避开 18:00 北京的 USPTO 连测 A 段**：
要么在它之前做完，要么等它跑完再做。USPTO 那条链优先级更高。

还有一条更隐蔽的牵连，**步骤顺序已按它排过**：`uspto-daily.bat` 跑的是
`docker compose -f <ERP_COMPOSE> exec …`，而新版 compose 必须能取到 `infra/.env`
才解析得动。**所以本文把「生成 `infra/.env`」放在「切分支」之前**——反过来做的话，
两步之间只要 `.bat` 恰好触发，当天的 USPTO 任务就会以一句难懂的
`required variable ... is missing a value` 失败。`infra/.env` 不进版本库，切分支不会动它，
先建好就一劳永逸。

---

## 第 0 步：前置核验（**先不要切分支**）

```powershell
cd <仓库目录>
git log --oneline -1
docker ps --format "{{.Names}}`t{{.Status}}"
```

**贴回①**：`git log` 那一行 + `docker ps` 的容器名清单（下面要用到 db 的容器名，
默认是 `erp-all-db-1`；若你这里不是这个名字，后面命令里相应替换）。

## 第 1 步：先备份（**做任何改动之前**）

```powershell
bash infra/local-deploy/backup.sh
"BACKUP_EXIT=$LASTEXITCODE"
Get-ChildItem $HOME\erp-backups\*.dump | Sort-Object LastWriteTime -Descending |
  Select-Object -First 1 Name, Length, LastWriteTime
```

**贴回②**：`BACKUP_EXIT` + 最新那个 dump 的文件名/大小/时间。

> `BACKUP_EXIT` 必须是 `0`，且**必须真的看到文件**——脚本里的 10KB 下限只挡得住空文件，
> 挡不住「压根没生成」。这一步不过就别往下走。

## 第 2 步：生成 `infra/.env`（强随机，不回显）

```powershell
@'
import secrets, pathlib
names = ["POSTGRES_PASSWORD","ERP_APP_DB_PASSWORD","ERP_MIGRATOR_DB_PASSWORD",
         "PORTAL_APP_DB_PASSWORD","REDIS_PASSWORD","ERP_JWT_SECRET","ERP_CREDENTIAL_KEY"]
p = pathlib.Path("infra/.env")
if p.exists():
    raise SystemExit("infra/.env 已存在——本步不覆盖，先确认是不是重复执行了")
p.write_text("".join(f"{n}={secrets.token_hex(32)}\n" for n in names), encoding="utf-8")
print("WROTE", len(names), "vars")
'@ | python -
"GEN_EXIT=$LASTEXITCODE"
# 只看有没有、多大，**不要 cat 它**
Get-Item infra\.env | Select-Object Name, Length
```

**贴回③**：`GEN_EXIT` + 那一行 `Name/Length`（应为 7 行 × 约 70 字节 ≈ 500B 上下）。

> 全部用 `token_hex`：hex 串是 URL 安全的。口令要被拼进 DSN
> （`postgresql://user:口令@host/db`），里面若出现 `@ : / ? # %` 会把 DSN 解析歪，
> 而症状是「连不上」不是「口令错」——很费时间。
>
> `infra/.env` 已被 `.gitignore` 挡住，不会进版本库；**它是这台机器上唯一的一份，
> 丢了库就打不开了**（尤其 `ERP_CREDENTIAL_KEY`）。请按 Owner 的凭证保管方式另存一份。
>
> **Swagger UI（`/api/docs`）在本次部署后是关的**，这也是有意的：8000 内网可达而该页
> 无鉴权。要接口文档就在 `infra/.env` 里加一行 `ERP_DOCS_ENABLED=true` 再 `make up`
> ——**不要为此去改 `ERP_ENV`**。这两件事已经拆开（原本是同一个开关），改 `ERP_ENV`
> 会连带把弱密钥放行重新打开。
>
> 生成的七个变量里**没有 `ERP_ENV`，这是有意的**：compose 写的是 `${ERP_ENV:-prod}`，
> 不设即 prod，而 prod 下放行开关 `ERP_ALLOW_INSECURE_DEFAULTS` 一律无效——本机将
> **没有任何办法**带着弱密钥启动。若某一步报「拒绝启动：检出已知默认/弱密钥」，
> 那是真的有一项没换到位，**不要**去设那个开关（在这台机上它也不起作用），
> 把报错原文贴回来即可，它会逐项点名是哪个变量。

## 第 2.5 步：切到 PR 分支（**.env 已就位才切**）

```powershell
git fetch origin claude/r2-03-launch-leg5n8
git checkout claude/r2-03-launch-leg5n8
git reset --hard origin/claude/r2-03-launch-leg5n8
git log --oneline -1
```

**贴回③′**：`git log` 那一行（确认验的是哪个 commit）。

> 本机不改码、无自产提交，`reset --hard` 零丢失（分支惯例，见
> `infra/local-deploy/README.md`「增量验证流程」）。

## 第 2.8 步：redis 预演（**把唯一没法在云端验的东西，挪到不可逆步骤之前**）

云端没有 docker 守护进程，以下三件事只能在这台机上第一次见真章：容器内 `$$` 的实际
展开、加了 `requirepass` 之后 healthcheck 还判不判健康、镜像里有没有 `grep`。
它们要是错了，`make up` 起不来——**而如果等到第 4 步改完库口令才发现，那时旧 compose
也连不上库了（口令已变），两头都动不了**。所以先单起 redis 探一次：这一步**不碰任何
口令、不碰库、不碰数据**，退路只有一行。

```powershell
docker compose -f infra/docker-compose.yml up -d redis
"REDIS_UP_EXIT=$LASTEXITCODE"
Start-Sleep -Seconds 12
docker compose -f infra/docker-compose.yml ps redis
# 未认证应被拒（期望看到 NOAUTH）
docker exec erp-all-redis-1 redis-cli --no-auth-warning -a "" ping
# 认证后应 PONG（口令由容器内的 REDISCLI_AUTH 提供，命令行里不出现口令）
docker exec erp-all-redis-1 sh -c 'redis-cli ping'
```

**贴回③″**：`REDIS_UP_EXIT`、`ps` 那行的 STATUS（**期望 `Up ... (healthy)`**，不是
`(unhealthy)` 也不是 `(health: starting)` 一直不变）、两条 `redis-cli` 的输出。

**期望**：未认证那条报 `NOAUTH`/`ERR ... without any password`，认证那条回 `PONG`，
容器 12 秒左右转 `healthy`。

> ⚠️ **不健康或起不来就停在这里**，把 `docker compose logs redis` 的尾部贴回。
> 退路一行，且**此刻还没有任何不可逆改动**：
>
> ```powershell
> git checkout main
> docker compose -f infra/docker-compose.yml up -d redis   # 回到无口令的旧 redis
> ```
>
> 顺带说明：这一步之后到第 6 步之间，**还在跑的 api/beat 连 redis 会认证失败**。
> 那条通道只用于配置广播（`config_service`，设计上 fail-open），失败不影响业务读写，
> 日志里会有几条告警，属预期。

## 第 3 步：停应用容器（db / redis 继续跑）

```powershell
docker compose -f infra/docker-compose.yml stop api beat frontend
"STOP_EXIT=$LASTEXITCODE"
```

**贴回④**：`STOP_EXIT`。

> 顺序说明：库口令一改，**还在跑的旧容器**就连不上库了，会开始刷连接错误日志、
> 还可能把重试打满。先停干净，改完再一起起来。

## 第 4 步：改库里四个角色的口令

```powershell
$envmap = @{}
Get-Content infra\.env | ForEach-Object {
  if ($_ -match '^\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.+?)\s*$') { $envmap[$Matches[1]] = $Matches[2] }
}
$need = "POSTGRES_PASSWORD","ERP_APP_DB_PASSWORD","ERP_MIGRATOR_DB_PASSWORD","PORTAL_APP_DB_PASSWORD"
$missing = $need | Where-Object { -not $envmap[$_] }
if ($missing) { throw "infra/.env 缺这些变量：$missing" }

$sql = @"
ALTER ROLE postgres     PASSWORD '$($envmap['POSTGRES_PASSWORD'])';
ALTER ROLE erp_app      PASSWORD '$($envmap['ERP_APP_DB_PASSWORD'])';
ALTER ROLE erp_migrator PASSWORD '$($envmap['ERP_MIGRATOR_DB_PASSWORD'])';
ALTER ROLE portal_app   PASSWORD '$($envmap['PORTAL_APP_DB_PASSWORD'])';
"@
$sql | docker exec -i erp-all-db-1 psql -v ON_ERROR_STOP=1 -U postgres -d erp_all
"ALTER_EXIT=$LASTEXITCODE"
```

**贴回⑤**：`ALTER_EXIT`（期望 `0`）+ psql 打出的那几行 `ALTER ROLE`。

> **为什么必须在这里单独改**：`POSTGRES_PASSWORD` 这类环境变量**只在空数据卷首次
> initdb 时生效**。这台机的卷早就建好了，改 compose 的 env 一点用没有——不改这一步，
> 结果就是「配置文件上写着新口令，库里认的还是旧口令」，最坏的一种状态：
> 看起来改完了，门其实还开着。

## 第 5 步：轮换凭证加密密钥（**先验证、再执行**）

`ERP_CREDENTIAL_KEY` 跟别的密钥不一样：`app.store_credential.client_secret_encrypted`
（全部店铺的 Walmart client_secret）与 `app.proxy.password_encrypted` 是用它加密的。
直接换掉，旧密文就再也解不开。故用工具重加密，且先 dry-run 证明旧密钥确实能解开。

```powershell
docker compose -f infra/docker-compose.yml build migrate
"BUILD_EXIT=$LASTEXITCODE"

# 旧密钥：合并前 compose 没注入过 ERP_CREDENTIAL_KEY，容器一直用的是代码里的默认值
docker compose -f infra/docker-compose.yml run --rm `
  -e ERP_CREDENTIAL_KEY_OLD=dev-only-change-me `
  migrate python -m erp.tools.rotate_credential_key --dry-run
"DRYRUN_EXIT=$LASTEXITCODE"
```

**贴回⑥**：`BUILD_EXIT`、`DRYRUN_EXIT`，以及 dry-run 打出的两行
（`app.store_credential: N 行可解开` / `app.proxy: M 行可解开`）。

> ⚠️ **`DRYRUN_EXIT` 不为 0 就停手**，把报错贴回。那说明旧密钥不是
> `dev-only-change-me`（可能有人手工设过），得先弄清真正的旧值再继续。
> 这一步不写库，随便试都安全。
>
> 如果 `N`、`M` 都是 `0`，也停一下告诉我：没有密文要迁，本步可以跳过，但我要确认
> 这台机是不是真的没配过店铺凭证。

dry-run 过了再执行真轮换：

```powershell
docker compose -f infra/docker-compose.yml run --rm `
  -e ERP_CREDENTIAL_KEY_OLD=dev-only-change-me `
  migrate python -m erp.tools.rotate_credential_key
"ROTATE_EXIT=$LASTEXITCODE"
```

**贴回⑦**：`ROTATE_EXIT` + 那两行 `N 行已重加密`。

> 工具在一个事务里「旧密钥解 → 新密钥加 → 回读逐行比对摘要」，比对不上就整体回滚。
> 失败时它会明说「已回滚，库未改动」——看到这句就是安全的，贴回来即可。

## 第 6 步：起全栈（新配置生效）

```powershell
make up
"UP_EXIT=$LASTEXITCODE"
docker compose -f infra/docker-compose.yml ps
```

**贴回⑧**：`UP_EXIT` + `ps` 的完整表格（migrate 应为 `Exited (0)`，其余 Up）。

## 第 7 步：验收

### 7.1 端口真的收了（**本步是本单的核心验收**）

```powershell
netstat -ano | Select-String ":5432|:6379|:8000|:5173" | Select-String "LISTENING"
```

**贴回⑨**：上面几行原样。

**期望**：`5432` 与 `6379` 前面是 **`127.0.0.1`**；`8000` 与 `5173` 仍是 `0.0.0.0`
（这两个是有意保留的内网服务，见 D-Q68）。

若手边有内网第二台机器，再补一条（**这是最直接的证据**）：

```powershell
# 在**另一台**内网机器上执行，<IP> 换成部署机内网 IP
Test-NetConnection -ComputerName <IP> -Port 5432 -InformationLevel Quiet
Test-NetConnection -ComputerName <IP> -Port 6379 -InformationLevel Quiet
Test-NetConnection -ComputerName <IP> -Port 5173 -InformationLevel Quiet
```

**期望**：前两个 `False`（连不上＝对的），第三个 `True`（前端仍可用）。

### 7.2 服务与数据都正常

```powershell
curl.exe -s http://127.0.0.1:8000/healthz
docker compose -f infra/docker-compose.yml exec -T api alembic current
# 凭证自查：不给旧密钥的 dry-run ＝ 用**当前**密钥试解全部密文
docker compose -f infra/docker-compose.yml run --rm migrate `
  python -m erp.tools.rotate_credential_key --dry-run
"VERIFY_EXIT=$LASTEXITCODE"
docker compose -f infra/docker-compose.yml logs --tail=30 beat
```

**贴回⑩**：`/healthz` 返回、`alembic current`（应为 `0039 (head)`）、`VERIFY_EXIT` 与它的
两行行数（**应与第 5 步 dry-run 的 N/M 相同**）、beat 最后 30 行日志（不应有 redis
`NOAUTH` / `WRONGPASS`，也不应有库连接失败）。

### 7.3 前端还能用

浏览器打开 `http://<部署机内网IP>:5173`，登录一次。

**贴回⑪**：能否正常登录进首页（截图或一句话即可）。

> 登录会失败一次是**正常的**——JWT 密钥换了，之前的登录态全部失效，重新登录即可。
> 若重新登录仍失败，那是真问题，停下来贴回。

## 出问题怎么回退

**别删数据卷**。按发生位置：

| 卡在哪 | 回退动作 |
|---|---|
| 第 2.8 步（redis 预演）失败 | **此刻还没有任何不可逆改动**：`git checkout main` + `docker compose ... up -d redis` 即回到无口令的旧 redis，口令、库、数据一律未动 |
| 第 4 步之后、第 5 步之前 | 把四个角色的口令 `ALTER ROLE` 回原值（`postgres`/`erp_app`/`erp_migrator`/`portal_app`），再 `git checkout main && make up` |
| 第 5 步失败 | 工具已自行回滚，库未改动；照上一行处理即可 |
| 第 6 步起不来 | 先贴回 `docker compose logs migrate api` 的尾部再说，**不要**先删卷重来——卷里是唯一的业务数据 |

第 1 步的 dump 是最后一道保险；真要用到它，**由 Owner 决策后另行给指令**
（恢复涉及写 `erp_all`，不在本文授权范围内）。

## 不要做的事

- **不要**把 `infra/.env` 的内容贴回来、也不要提交进 git。
- **不要**动 `channel.gateway_mode`，本文全程不发任何渠道请求。
- **不要**给用户绑角色。
- **不要**为了「省事」跳过第 5 步的 dry-run——那是整套流程里唯一能在写库之前
  发现「旧密钥不对」的地方。
