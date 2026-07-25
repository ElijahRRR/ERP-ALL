# R2-12 验收① 证据：USPTO 供给链三日连测

> **验收① 口径**（D-Q65① 方案 A 落成后的唯一未收项）：USPTO 整链驻部署机后，
> 必须证明它**无人值守连续三日自动跑通**——不是「手动跑一次成功」。
> 本文件是三日证据容器，收齐即 R2-12 整单收账。
>
> 相关：链路定义见 `infra/local-deploy/README.md`「USPTO 商标供给链」；
> 修复分支 `ElijahRRR/walmart-trademark-sync` PR #1（`fa134dc`，两跳下载修复）待首跑绿后合并。

## 前提零：先核 HEAD 分支（每日必查，最容易踩）

```bash
cd /d D:\walmart-trademark-sync && git rev-parse --abbrev-ref HEAD
# 必须是 claude/fix-uspto-json-download —— 不是 main
```

修复前的 main 版本在两跳下载处拿到的是**中间跳转页而非 zip 实体**，会把 141 个待补日期
全部判为「非 zip」跳过。症状 = **跑了、退出码 0、但一条没导**。
若 HEAD 在 main：这**不算 FAIL**，切分支后补跑即可，当日按「情形 C」记。

## 前提一：`DB_CONN` 必须在进程环境里（07-25 实测踩中）

`etl_trademarks.py:24` 是 `DB_CONFIG = _parse_db_conn(os.environ["DB_CONN"])`——**模块级、无默认值**；
`daily_update.py` 顶部 `import etl_trademarks`，因此**缺 `DB_CONN` 时在 import 阶段就 KeyError**。
且全仓**没有任何 dotenv 加载**（`.env` 不会被 Python 自动读），必须由 `.bat` 读本地密钥文件再 `set`。

`.bat` 在调 Python 前有前置检查，缺文件即 `ERROR: local secret file missing` + `exit 10`
——**这是正确行为**（挡在 KeyError 之前给干净退出码），不要当成 bat 的 bug 去改。

密钥文件键名以仓内 `.env.example` 为准：`DB_CONN`（本链唯一必需）／`LARK_APP_ID`／
`LARK_APP_SECRET`／`LARK_SPREADSHEET_TOKEN`（后三个只给飞书同步脚本用，daily_update 不需要）。
`DB_CONN` 格式是**空格分隔 kv**（非 URI）：`dbname=uspto user=postgres password=<pw> host=127.0.0.1 port=5433`。

## 前提二：计划任务必须能在无人登录时触发

`schtasks /query /v` 里的 **`Logon Mode: Interactive only`** 意味着**只有该用户处于登录态才会触发**。
验收① 要证的正是「无人值守」——若这台机某天没人登录，任务根本不会跑，当日直接作废。
07-25 能触发是因为当时 Administrator 在登录态，属侥幸不是保障。

两条合规路径二选一（Owner 定）：
1. **保持该账号常驻登录**（锁屏即可）——最省事，但要写进运维约束，且重启后必须重新登录；
2. **改存储凭据**（`/RU <user> /RP <password>`，Logon Mode 变 `Interactive/Background`）——
   真正无人值守。⚠ 不要改成 `SYSTEM`：链路第 3-4 步要 `docker compose cp/exec`，
   Docker Desktop 是**按用户会话**跑的，SYSTEM 下通常不可用。

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
#         "非 zip" 字样出现即前提零没过
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

## 判定表

| 情形 | A 段 | B/C 段 | 记法 | 处置 |
|---|---|---|---|---|
| 正常 | 触发且 result=0 | 通过 | **PASS**（当日计入三日） | 无 |
| 无数据日 | 触发且 result=0 | 全 404、库无新增 | **PASS**（计入，标注「无数据日」） | 无 |
| 情形 A | 触发但 result≠0 / 日志报错 | — | **FAIL** | 只回报日志，不改码；等云端定位 |
| 情形 B | 触发且 0，但一条没导 | 日志含「非 zip」 | 不计入 | 前提零没过 → 切分支补跑 |
| 情形 C | 未触发（任务未建/未启用） | — | 不计入 | 建/修计划任务 + 今日手动补跑取 B/C 段 |

**手动补跑只证链路，不证调度**：出现情形 B/C 时，自动触发三日窗口从「调度首次真实触发的那天」重新起算。
（严口径。Owner 若认「链路三日 + 自动触发一日」足够收账，可放宽——需明示。）

## 三日记录（待填）

| 日期 | A 自动触发 | B 链路 | C 对账（revision / newest） | 判定 |
|---|---|---|---|---|
| ~~2026-07-25~~ | 已触发 18:00，**Last Result=10** | **报错**：`local secret file missing`，链路在下载前退出 | 跳过（B 无新 completed） | **FAIL（情形 A）·不计入** |
| 2026-07-26 | — | — | — | — |
| 2026-07-27 | — | — | — | — |
| 2026-07-28 | — | — | — | — |

**07-25 复盘**（第 1 日作废，连续三日窗口顺延至 07-26/27/28，最早收账 07-28 晚）：

- 调度侧本身是**好消息**——任务已建、`Enabled`、`Schedule Type: Daily / 18:00`、准点触发、
  `Next Run Time` 正常滚到次日。A 段机制被证明可用，**不需要补建任务**。
- 失败点是本机从未配置密钥文件（`etl_progress` 最新 `completed` 是 `apc260711.zip`，
  `completed_at` 2026-07-12 22:03 UTC——那是**迁入前 Owner Mac 上的历史记录随 dump 带来的**，
  说明这台部署机**一次都没成功跑过**）。因此积压约 13 个日增量待补。
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
