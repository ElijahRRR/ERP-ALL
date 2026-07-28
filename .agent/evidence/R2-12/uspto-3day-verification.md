# R2-12 验收① 证据：USPTO 供给链三日连测

> **验收① 口径**（D-Q65① 方案 A 落成后的唯一未收项）：USPTO 整链驻部署机后，
> 必须证明它**无人值守连续三日自动跑通**——不是「手动跑一次成功」。
> 本文件是三日证据容器，收齐即 R2-12 整单收账。
>
> 相关：链路定义见 `infra/local-deploy/README.md`「USPTO 商标供给链」。
> ✅ `ElijahRRR/walmart-trademark-sync` PR #1（两跳下载修复）**已于 2026-07-26 squash 合入
> main `9bc0bbbf`**（Owner 授权）。

## 前提零：核 HEAD 分支（每日必查，最容易踩）

```bash
cd /d D:\walmart-trademark-sync && git rev-parse --abbrev-ref HEAD
```

**三日连测窗口内（07-26/27/28）：应仍是 `claude/fix-uspto-json-download`，不要切！**

PR #1 已合入 main，但**窗口期内不切分支**——该分支内容与 main 里的修复**逐字相同**，
中途切换只增加变量、零收益。远端分支**已刻意保留不删**（`fa134dc`），就是为了让部署机
安稳踩到窗口结束。

**收账（07-28 晚）之后**再按 runbook「部署机验完切回 main 常驻」处置：

```bash
git checkout main && git pull      # main 已含 9bc0bbbf 的两跳修复
git log --oneline -1               # 应见 9bc0bbbf
```
切回后本前提零改为核「HEAD=main 且含 `9bc0bbbf`」；届时远端分支可删。

**为什么这条必须每日查**：修复前的 main 在两跳下载处拿到的是**中间跳转页而非 zip 实体**，
会把 141 个待补日期全部判「非 zip」跳过，症状 = **跑了、退出码 0、但一条没导**。
（该风险自 `9bc0bbbf` 合入后已消除，但窗口期内仍逐日核 HEAD，防止有人误切到旧提交。）

## 前提一：`.bat` 必须是 CRLF 且纯 ASCII（07-25 实测踩中，代价=一个验收日）

> ⚠️ **`ERROR: local secret file missing` 这条日志是假象，不要照字面信。**
> 07-25 首跑就是被它误导：密钥文件其实一直在（`D:\erp-staging-backup\uspto-db.env`，
> 07-24 12:36 建，101 字节，含 `POSTGRES_PASSWORD`）。

真因是 **`.bat` 为 LF-only 换行**（实测 127 LF / 0 CRLF）。`cmd.exe` 按 CRLF 切行，
遇到 LF-only 会**逐行吞掉前缀**——现场证据：

| 源码 | cmd 实际执行 |
|---|---|
| `setlocal EnableExtensions` | `EnableExtensions`（未识别） |
| `set "SYNC_DIR=..."` | `NC_DIR`（未识别） |
| `set "PYTHON=..."` | `HON` |
| `set "SECRET_FILE=..."` | `RET_FILE` |
| `set "COMPOSE=..."` | `POSE` |

于是 `SECRET_FILE` **从未被赋值**，`if not exist ""` 恒真 → `exit /b 10`。
Python 一行都没跑到。

**根治**：`.bat` 已纳入版本管理（`infra/local-deploy/automation/uspto-daily.bat`），
仓根 `.gitattributes` 声明 `*.bat text eol=crlf` **强制检出为 CRLF**
——只提交 CRLF 字节不够，部署机 `core.autocrlf` 非 `true` 时仍会检出 LF、原样复发。

自查（怀疑时先做这个，一秒出结果）：

```powershell
$b=[IO.File]::ReadAllBytes("<bat路径>")
"CRLF={0} LoneLF={1}" -f ([regex]::Matches([Text.Encoding]::ASCII.GetString($b),"`r`n").Count),
                          ([regex]::Matches([Text.Encoding]::ASCII.GetString($b),"(?<!`r)`n").Count)
# LoneLF 非 0 即中招
```

**同一现场还挖出第二个坑：`.bat` 里不许出现非 ASCII 路径。**
原文硬编码 `D:\项目文件\ERP-ALL\infra\docker-compose.yml`，文件存为 UTF-8、被 cmd 按 GBK 读
→ 变成 `D:\椤圭洰鏂囦欢\...`，路径不存在。现已改为从密钥文件读 `ERP_COMPOSE` 键，
**值填 8.3 短路径**（`for %%I in ("<长路径>") do @echo %%~sI` 取，纯 ASCII，免疫代码页）。
日志时间戳也从 `%date% %time%`（中文区域名会写成 `[鍛ㄦ棩 ...]`）改用 ASCII 的 `%RUN_ID%`。

## 前提二：`DB_CONN` 的传递机制（背景知识，非 07-25 的失败原因）

`etl_trademarks.py:24` 是 `DB_CONFIG = _parse_db_conn(os.environ["DB_CONN"])`——**模块级、无默认值**；
`daily_update.py` 顶部 `import etl_trademarks`，因此**缺 `DB_CONN` 时在 import 阶段就 KeyError**。
全仓**没有任何 dotenv 加载**（`.env` 不会被 Python 自动读），必须由 `.bat` 读密钥文件再 `set`。

密钥文件（`uspto-db.env`）是 `KEY=VALUE` 文本，**不是** `set "KEY=VALUE"` 的 cmd 片段。
`.bat` 读的是 `POSTGRES_PASSWORD` 与 `ERP_COMPOSE` 两个键，`DB_CONN` 由 `.bat` 自行拼装
（格式是**空格分隔 kv 非 URI**）。

⚠ 连接自检要连**宿主机映射端口**：容器内 PostgreSQL 监听 5432，`5433` 是宿主机映射。
在容器内用 `host=127.0.0.1 port=5433` 自检必然 `Connection refused`——那是自检姿势错，不是配置错。

密钥文件键名以仓内 `.env.example` 为准：`DB_CONN`（本链唯一必需）／`LARK_APP_ID`／
`LARK_APP_SECRET`／`LARK_SPREADSHEET_TOKEN`（后三个只给飞书同步脚本用，daily_update 不需要）。
`DB_CONN` 格式是**空格分隔 kv**（非 URI）：`dbname=uspto user=postgres password=<pw> host=127.0.0.1 port=5433`。

## 前提三：计划任务必须能在无人登录时触发

`schtasks /query /v` 里的 **`Logon Mode: Interactive only`** 意味着**只有该用户处于登录态才会触发**。
验收① 要证的正是「无人值守」——若这台机某天没人登录，任务根本不会跑，当日直接作废。
07-25 能触发是因为当时 Administrator 在登录态，属侥幸不是保障。

但 07-25 的 Docker 诊断把结论改了——**光改调度不够，真正的约束在 Docker 这边**：

| 实测项 | 结果 |
|---|---|
| Docker 形态 | Docker Desktop 4.57.0（`Context: desktop-linux`） |
| 全部 `Docker Desktop.exe` / `com.docker.backend.exe` | 均在 **`Console` 会话 1** |
| `com.docker.service`（系统服务） | **`Stopped` / `Manual`** |
| 登录自启 | `AutoStart=True` + HKCU Run 项存在 |

**Docker Desktop 依赖交互式用户会话存在**：它随登录自启、进程全在 Console 会话，系统服务并未承担引擎。
链路第 3-4 步要 `docker compose cp/exec` 打进 api 容器，**Docker Desktop 不运行则必然失败**。

所以三条路径的真实效果是：

1. **只改存储凭据 `/RU /RP`** —— 解决「任务会不会触发」，**不解决 Docker**。
   引擎命名管道（`\\.\pipe\dockerDesktop*`）是机器级、跨会话可达（调用方需在 `docker-users` 组），
   所以只要 Docker Desktop **正在某个会话里跑**，非交互任务就能用它；但**没人登录时它压根没启动**。
2. **保持该账号常驻登录**（锁屏/RDP 断开都不影响，只要不注销）—— 这才是让 Docker 活着的前提。
3. **两者都做**（推荐）：常驻登录保证 Docker 在跑，存储凭据保证会话锁定/断开等边角情形下任务照样触发。

⚠ **绝不要改成 `SYSTEM`**——SYSTEM 不在用户会话里，Docker Desktop 对它不可用。

**真正的缺口是重启**：机器重启且无人登录时，Docker Desktop 不启动、链路必挂。要做到名副其实的
无人值守，得配 **Windows 自动登录**（`netplwiz` 取消「必须输入密码」或 `AutoAdminLogon`），
让重启后自动进入桌面会话、Docker 随之自启。这一条不做，「无人值守」就只是「没人动它的时候能跑」。

> ### 🔒 已知限制（2026-07-26 Owner 定案：**自动登录不配**）
>
> Owner 明确决定**不配置 Windows 自动登录**（自动登录须把口令写进注册表
> `DefaultPassword` 或凭据管理器，等于给这台存有全部店铺 API 凭证的机器开一道明文后门——
> 这个代价换「重启自愈」不值）。**因此本链路的无人值守能力有一个已知且被接受的空档**：
>
> | 情形 | 链路是否照跑 |
> |---|---|
> | 人已登录、锁屏 / RDP 断开 | ✅ 跑（存储凭据 + 常驻登录） |
> | 人已登录、机器空闲数日 | ✅ 跑 |
> | **机器重启 / 断电恢复后，到有人手工登录之前** | ❌ **不跑**，期间每天的 18:00 档全部丢失 |
> | 该账号被注销（logoff） | ⚠️ 未实测（见下） |
>
> **运维约束（必须执行）**：部署机重启后**须尽快人工登录桌面**，链路才恢复。这不是待办、
> 是长期约束——「无人值守」在本部署下的准确含义是「**无人干预**，但需要有人保持登录态」。
>
> **对验收①的影响**：三日连测期间若发生重启且未及时登录，当日按情形 C（未触发）判**不计入**，
> 窗口顺延——不是链路缺陷，是这条已知限制的直接后果。
>
> 若将来要真正闭掉这个空档，可选路（都不需要明文口令，留作备选不现在做）：
> ① 把 Docker 引擎从 Docker Desktop 换成 Windows 服务化的 containerd/dockerd；
> ② 整链搬进 WSL2 + systemd（不依赖桌面会话）；
> ③ 用带 TPM 保护的自动登录方案（如 Autologon + LSA secret，仍有残余风险）。

**待实测（不要猜）**：注销（logoff）后容器是否存活、非交互任务能否访问 Docker。
07-25 部署机正确地拒绝了破坏性实测——这条需要安排一个不影响验收窗口的时间做。

## 每日三段取证

### A 段 · 自动触发证据（证「无人值守」，不可用手动跑替代）

```bash
schtasks /query /tn "<任务名>" /v /fo LIST
# 取：Last Run Time / Last Result / Next Run Time / Logon Mode
```

判据：Last Run Time = 当日 18:00 档、Last Result = 0。
**A 段是三日连测的实质**——B/C 段手动也能跑出来，只有 A 段证明调度真的在工作。

`Last Result` 常见值：`0`=成功；`10`=本链 `.bat` 的前置检查失败（密钥文件缺失，见前提一）；
`267011`=任务从未运行过。

### B 段 · 链路证据（daily_update → uspto 库）

```bash
# 1. 本轮日志尾（下载/ETL/完整性校验三段）
#    关注：下载到的 apcYYMMDD.zip 列表、404=当日无数据（正常）、
#    ⚠ 「非 zip」要看**比例**，不要一见就停（见下方「非 zip 的两种含义」）
# 2. etl_progress 本轮新转 completed 的文件
#    注意：etl_progress 无 updated_at 列，只有 started_at / completed_at
#    （写入处见 walmart-trademark-sync/etl_trademarks.py process_zip）
psql "$DB_CONN_URI" -c "SELECT source_file, status, records_inserted, \
  COALESCE(completed_at, started_at) AS updated_at \
  FROM etl_progress WHERE data_type='trademark' \
  ORDER BY COALESCE(completed_at, started_at) DESC LIMIT 5;"
```

判据：本轮至少一个文件 completed（或全部 404=当日无新增，属正常，记「无数据日」）；
无孤儿/错误数告警。

### C 段 · 对账证据（delta 导出 → erp_all 导入 → 生效）

```bash
# 导入工具输出（total/merged/err）与 delta csv 行数、etl_progress.records_inserted 三方对齐
# 只读复核（不改库）：
$PSQL "SELECT count(*) AS total, max(filed_date) AS newest FROM refdata.trademark;"
$PSQL "SELECT revision FROM refdata.dataset_revision WHERE dataset='trademark';"
```

判据：`total/merged/err` ↔ csv 行数一致（差值只允许「无 mark 文本行」）；
`revision` 相对前一日**递增**；`newest` 前推；err 行逐条贴 errors.jsonl 摘要。

新鲜度守卫侧（新系统自动，出告警=链路断）：

```bash
docker compose -f infra/docker-compose.yml exec api \
  python -m erp.tools.run_task trademark_freshness
```

## 「非 zip」的两种含义（2026-07-26 踩过，勿再混淆）

`daily_update.py` 对「响应体非 zip」记 WARNING、计一次失败、连续 `CIRCUIT_BREAK_AFTER=3` 才熔断本轮。
这条日志有两种截然不同的含义，**判据是比例不是有无**：

| 形态 | 含义 | 处置 |
|---|---|---|
| **全部/绝大多数**候选文件都「非 zip」，`下载完成: 0 个` | 两跳修复失效（或 HEAD 在 main 跑了旧码） | **立即停**，回报，切分支 |
| **队尾一两个**「非 zip」，其余正常下载 | 门户对**尚未发布/限流中**的最新日期返回挑战页。`end = now - 1 天`，故当天跑必然会碰最新那个 | **正常，不要停**，余量次日自然重取 |

**2026-07-26 实测**：14 个候选 → 12 个成功下载（3.6~64 MB 真 ZIP）、`apc260723` HTTP 429 跳过、
`apc260725`（昨天的数据，门户未发布）「非 zip」。**这是健康形态**，链路已进入 Step 2 导入。
当时因指令写成「一见非 zip 就停」而被硬停在 ETL 中途——**是指令的错，不是链路的错**。

> **ETL 被中断不需要人工修库**：`process_zip` 只跳过 `status='completed'`，
> 残留的 `running` 行下次会 `ON CONFLICT DO UPDATE` 重置并重新处理；
> `insert_batch` 先按 serial_number 删子表再插、主表 `ON CONFLICT DO UPDATE`。
> **重跑幂等，禁止手工改 `etl_progress` 状态。**

## 判定表

| 情形 | A 段 | B/C 段 | 记法 | 处置 |
|---|---|---|---|---|
| 正常 | 触发且 result=0 | 通过 | **PASS**（当日计入三日） | 无 |
| 无数据日 | 触发且 result=0 | **无新文件可导**（全 404 / 剩余候选在 429 限流窗内 / 已全部 completed），库无新增 | **PASS**（计入，标注「无数据日」） | 无 |
| 情形 A | 触发但 result≠0 / 日志报错 | — | **FAIL** | 只回报日志，不改码；等云端定位 |
| 情形 B | 触发且 0，但一条没导 | **全部/绝大多数**文件「非 zip」 | 不计入 | 前提零没过 → 切分支补跑 |
| 情形 C | 未触发（任务未建/未启用） | — | 不计入 | 建/修计划任务 + 今日手动补跑取 B/C 段 |

**手动补跑只证链路，不证调度**：出现情形 B/C 时，自动触发三日窗口从「调度首次真实触发的那天」重新起算。
（严口径。Owner 若认「链路三日 + 自动触发一日」足够收账，可放宽——需明示。）

## 三日记录（待填）

| 日期 | A 自动触发 | B 链路 | C 对账（revision / newest） | 判定 |
|---|---|---|---|---|
| ~~2026-07-25~~ | 已触发 18:00，**Last Result=10** | **报错**：`local secret file missing`（假象，真因 bat LF-only） | 跳过 | **FAIL（情形 A）·不计入** |
| **2026-07-26** | **PASS**：`Last Run 18:00:01` / `Last Result=0` / `Ready` / `Enabled` / `Next Run 07-27 18:00` | **PASS（无数据日）**：新下载 0、新导入 0（`apc260723` HTTP 429 仍在限流窗内）；完整性检查全过、ETL 错误 0、孤儿检查通过；`ETL_PROCESS=NONE`；日志正常收尾 `USPTO daily chain done` | USPTO 14,216,076 / newest `2026-07-25` / completed 232 · ERP 4,475,105 / newest `2026-07-25` / **revision 204** | **PASS（第 1/3 日，情形「无数据日」）** |
| **2026-07-27** | **PASS（间接双时间戳互证，非 schtasks 直证——见下方判定依据）**：自动日志 `uspto-daily-20260727-180001.log`，启动 `18:00:01`，收尾 `USPTO daily chain done` | **PASS**：候选 2 → `apc260723` HTTP 429 跳过、`apc260726.zip` 下载 3.9 MB；导入 `4,901/4,901`、0 错误、7.6 秒；完整性检查全过、孤儿检查通过、ETL 总错误 0 | USPTO 14,217,597 / newest `2026-07-26` / completed 233 · ERP 4,476,727 / newest `2026-07-26` / **revision 206**（↑204）· 导入 4,753/4,753/err 0 | **PASS（第 2/3 日）** |
| **2026-07-28** | **PASS（直证）**：`Last Run 2026/7/28 18:00:01` / `Last Result=0` / `Next Run 07-29 18:00` / `Logon Mode Interactive only` / `Ready` / `Enabled` | **PASS**：候选 2 → `apc260723` HTTP 429 跳过、`apc260727.zip` 下载 55.0 MB；导入 `67,120/67,120`、0 错误、57.6 秒；完整性检查全过、孤儿检查通过；收尾 `USPTO daily chain done` | USPTO 14,220,248 / newest `2026-07-27` / completed 234 · ERP 4,483,613 / newest `2026-07-27` / **revision 210**（↑206）· 导入 65,177/65,177/err 0 | **PASS（第 3/3 日）** |

### 三日收账（2026-07-28）：**3/3 PASS，验收① 结清**

窗口 07-26（无数据日）+ 07-27 + 07-28 全 PASS。`completed_files` 232→233→234 每日恰 +1，
与 `latest_file` 逐日前推一致；`total` 增量小于 `records_inserted` 属正常
（`insert_batch` 对已有 serial 走 `ON CONFLICT DO UPDATE`，更新不涨行数）。

#### 第 2 日（07-27）判定依据：为什么无 schtasks 直证也算 PASS

`schtasks` 只保留最近一次运行，07-27 四项已被 07-28 覆盖，Windows 任务历史亦未留事件。
判定改由**两个相互独立的机器时间戳互证**：

1. 自动日志文件名 `uspto-daily-20260727-180001` —— 精确落在计划时刻那一秒；
2. `etl_progress.completed_at = 2026-07-27 10:00:21 UTC`（＝北京 18:00:21，触发后 20 秒）
   —— 这是**数据库自己写的**，与日志文件无关。

人工在同一秒同时造出这两条记录不现实，故判 PASS。**但如实标注为间接证据**：
以后复查时这个区别是重要的。**改进项（下轮窗口前做）**：`.bat` 收尾时把 `schtasks /query`
四项追加进当日日志，直证即可逐日留痕，不再依赖「最近一次」这个会被覆盖的窗口。

#### 判据第三句「审核检索可见新商标」的闭合方式（**代码事实，非本轮真机操作**）

三日回执全部止于库表层（count / newest / revision），**没有一次走过 ERP 检索出口**。
该子句改由三段可复核的事实接上：

1. **检索出口本身可用** —— 验收④ 已真机实测（`nike` + 仅 LIVE 208 条、`nice_classes` 208/208、
   无 `compliance.*` 账号访问 `/api/v1/trademarks` 得 403）；
2. **新行 `mark_norm` 必非空** —— `tools/bulk_import_trademark.py:110` 与
   `compliance/import_service.py:392` 逐字一致：`mark_norm = _norm(provided_norm) if provided_norm
   else _norm(mark_text)`；而**缺 `mark_text` 的行会计入 `err`**（`import_service.py:385-389`）。
   两日 **`err = 0`** ⇒ 所有导入行都有 `mark_text` ⇒ `mark_norm` 全部派生成功。
   这一条正是该子句要防的失败面：**只填 `mark_text` 不填 `mark_norm` 时，行数/newest 全对但搜不到**；
3. **检索直读该表无中间层** —— `compliance/router.py:394` 用 `mark_norm ILIKE` 直查
   `refdata.trademark`，无缓存、无物化视图（trgm GIN 只影响性能不影响可见性）。

**残余**：无人真的在搜索框里键入过一个 07-27 新导入的商标。风险已由上述三段压到极低，
**故不阻塞收账**；下次部署机上线时顺手补一次实搜即可（取 `filed_date='2026-07-27'` 的
样本 → 合规页商标查询 → 应命中同一 `serial_no`）。

#### 两条观察项（不阻塞，留待口径确认）

- **`revision` 步长与行数不成比例**：4,753 行 → +2；65,177 行 → +4。判据只要求「递增」且已满足，
  但步长规律未明（疑与导入分步而非行数相关）。
- **`[DELTA] rows=65184` vs 逻辑 65,177 的 7 行差**：部署侧解释为 CSV 字段内嵌换行使物理行数偏高；
  仓内判据写的允许差值口径是「无 mark 文本行」。两种解释都指向**没丢数**
  （`err=0` 且 `merged==total` 已证导入侧无丢弃），但口径不同，记此备查。

#### 第 1 日（2026-07-26）判定依据

按上表「情形」口径，本日属**无数据日**：A 段触发且 `Last Result=0`，B 段无新文件可导
（剩余候选 `apc260723` 在 429 限流窗内），库无新增——按既定判据 **计入三日、标注「无数据日」**。
这是本窗口第一条 **A 段实质证据**：`Last Run Time` 落在 18:00:01（自动触发，非手动），
`Next Run Time` 已自行推进到 07-27 18:00，证明计划任务日程在正常滚动。

- 完整日志：部署机 `D:\erp-staging-backup\logs\uspto-daily-20260726-180001.log`。
- 429 属预期、不构成 FAIL：`apc260723` 的余量按设计次日自然重取（见上文「非 zip / 限流」判据表）。
- 对账一致：USPTO 侧与 ERP 侧 newest 同为 `2026-07-25`，新鲜度守卫 `lag_days=1` 在容差内。

> **窗口维持 07-26/27/28，最早收账 07-28 晚。** 一度以为 07-26 因手动彩排 + 临时禁用调度而作废，
> 实际不会：**手动跑 bat 不推进 Windows 计划任务日程**，任务重新 `/enable` 后
> `Next Run Time` 仍是 **07-26 18:00**，当日自动触发照常发生。

### 2026-07-26 手动彩排：整链首次端到端跑通（非 A 段，不计入三日）

`START 16:22:07 → END 16:27:35，EXIT=0`，**5.5 分钟**跑完。这是链路后半段
（delta 导出 → `docker compose cp` → `bulk_import_trademark` → `[RECONCILE]`）**有史以来第一次被执行**。

- 12 个文件全部 completed、**0 ETL 错误**；最大的 `apc260713` 75,283 条 / 43.9 秒。
- `apc260725` 本轮已发布并成功导入（前一轮的「非 zip」确系门户未发布，判定正确）。
- **DELTA ↔ importer 全部对齐、err 全 0**（12 个文件逐一核对）。
  ⚠ 口径提醒：日志的 `[DELTA] rows` 是**物理文本行数**，CSV 字段内含换行时会大于逻辑记录数；
  对账要按**逻辑 CSV 记录**比，否则会误判成不一致。
- `[RECONCILE]`：uspto `total=14,216,076 / newest=2026-07-25 / completed_files=232`；
  ERP `total=4,475,105 / newest=2026-07-25 / revision=204`。
- 新鲜度守卫：`{"lag_days":1,"severity":"ok"}`。

**故 `walmart-trademark-sync` PR #1 的形式证据已齐**（多个 zip 走完两跳 + ETL completed + 全链对账），
合并前置**完全解除**。

### ⚠ 本次最贵的一课：`pg_restore` 后必须核验索引

彩排第一次跑时 ETL 只有 **5.5 条/秒**（12 个文件要 20 小时以上）。根因是**四张子表的
`serial_number` 索引在迁移中全部丢失**，单条 `DELETE ... WHERE serial_number = ANY(...)`
顺序扫 7.2 GB/3.2 GB 子表要 20~29 秒。

补索引 + ANALYZE **总共只花 23 秒**，之后 **1,500~2,300 条/秒（约 300 倍）**。

- 索引耗时：classes 2.9s / owners 7.3s / statements 6.2s / design_codes 1.3s / source_file 4.4s；
  五张表 ANALYZE 合计不到 1s。
- 冒烟：`Index Only Scan ... Execution Time: 0.485 ms`（此前同类删除 20~29 秒）。
- **迁移核验从此必须查 `pg_indexes` + 跑一条 `EXPLAIN ANALYZE` 冒烟**——
  原核验只查 `count(*)` 与 `max(filing_date)`，这两项**在无索引时照样通过**，
  盲区放过了整整一层，见 `infra/local-deploy/README.md` 一次性迁入第 1 步。

**07-25 复盘**（第 1 日作废，连续三日窗口顺延至 07-26/27/28，最早收账 07-28 晚）：

- 调度侧本身是**好消息**——任务已建、`Enabled`、`Schedule Type: Daily / 18:00`、准点触发、
  `Next Run Time` 正常滚到次日。A 段机制被证明可用，**不需要补建任务**。
- **失败点不是密钥文件**（初判有误，已更正）：`uspto-db.env` 07-24 12:36 就建好了、101 字节、
  含 `POSTGRES_PASSWORD`。真因是 **`.bat` 为 LF-only，cmd 吞掉每行前缀导致 `SECRET_FILE`
  从未赋值**，`if not exist ""` 恒真 → `exit 10`。详见「前提一」。
  日志那句 `local secret file missing` 是假象，照字面查会一路查错方向。
- 这台部署机**确实一次都没成功跑过**：`etl_progress` 最新 `completed` 是 `apc260711.zip`，
  `completed_at` 2026-07-12 22:03 UTC——那是**迁入前 Owner Mac 上的历史记录随 dump 带来的**。
  因此积压约 13 个日增量待补。
- 首跑还须注意：`download_new_files` 从 2026-03-05 起算、跳过 `existing ∪ completed`，
  且 `CIRCUIT_BREAK_AFTER=3`（连续 3 个失败即熔断本轮）。**积压首跑很可能只补一部分就熔断，
  这属设计内行为不算 FAIL**，余量次日续取。
- 连带待办：`walmart-trademark-sync` PR #1（两跳下载修复）的「首跑绿」至今**未被验证过**
  ——本次失败发生在下载之前，两跳代码一行没执行到。

## 铁律（部署机侧，每日适用）

- **只跑部署/数据/对拍，不改码不 push**；切分支只允许 `git checkout` 已有远端分支，不改文件不 commit。
- **绝不 `pg_restore` 进 `erp_all`**；uspto 库只被本链读写。
- **不输出密钥**：`DB_CONN` / POSTGRES_PASSWORD 一律不回贴到对话。
- **导入只走 CLI**（`bulk_import_trademark`），**禁直改 `refdata.trademark` / `blacklist_*` 表**
  ——canonical 由断言/导入通道维护，直改即失同步。
