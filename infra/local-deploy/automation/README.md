# 部署机自动化脚本（Windows）

本目录存放部署机计划任务实际调用的脚本。**部署机应从本仓检出使用，不要在机器上另存一份。**

| 文件 | 用途 | 调用方 |
|---|---|---|
| `uspto-daily.bat` | USPTO 商标供给链日常编排（runbook「USPTO 商标供给链 · 日常链路」第 1-4 步） | Windows 任务计划 `\ERP-ALL USPTO Daily`，每日 18:00 |

## 两条硬纪律（都栽过，各值一个验收日）

### 1. `.bat` 必须 CRLF

仓根 `.gitattributes` 已声明 `*.bat text eol=crlf` 强制检出为 CRLF。
**只提交 CRLF 字节是不够的**——部署机 `core.autocrlf` 若不是 `true`，仍会检出 LF。

`cmd.exe` 按 CRLF 切行，遇 LF-only 会**逐行吞掉前缀**。2026-07-25 现场：

| 源码 | cmd 实际执行 |
|---|---|
| `setlocal EnableExtensions` | `EnableExtensions`（未识别） |
| `set "SYNC_DIR=..."` | `NC_DIR` |
| `set "PYTHON=..."` | `HON` |
| `set "SECRET_FILE=..."` | `RET_FILE` |
| `set "COMPOSE=..."` | `POSE` |

后果：`SECRET_FILE` 从未赋值 → `if not exist ""` 恒真 → `exit /b 10`，日志写下
`ERROR: local secret file missing`。**这条日志是假象**——密钥文件当时好好地在那儿。
照字面去查密钥会一路查错方向，整个验收日作废。

一秒自查：

```powershell
$b = [IO.File]::ReadAllBytes("D:\...\uspto-daily.bat")
$s = [Text.Encoding]::ASCII.GetString($b)
"CRLF={0} LoneLF={1}" -f ([regex]::Matches($s,"`r`n").Count), ([regex]::Matches($s,"(?<!`r)`n").Count)
# LoneLF 非 0 即中招
```

### 2. `.bat` 必须纯 ASCII

文件以 UTF-8 存储，而 `cmd.exe` 按 OEM 代码页（中文机器上是 936/GBK）读取字节。
原实现硬编码了 `D:\项目文件\ERP-ALL\infra\docker-compose.yml`，实际被读成
`D:\椤圭洰鏂欢\ERP-ALL\infra\docker-compose.yml` —— 路径不存在，链路第 3-4 步必挂。

因此：

- **机器相关路径一律放进密钥文件**（`ERP_COMPOSE` 键），不写进 `.bat`；
- 该值**填 8.3 短路径**，纯 ASCII、免疫任何代码页问题：
  ```cmd
  for %I in ("D:\项目文件\ERP-ALL\infra\docker-compose.yml") do @echo %~sI
  ```
  若该盘禁用了 8.3（`fsutil 8dot3name query D:`），退而求其次是把 `.bat` 另存为
  ANSI/936，或把仓迁到纯 ASCII 路径；
- 日志时间戳不用 `%date% %time%`（中文星期会写成 `[鍛ㄦ棩 ...]`），改用 ASCII 的 `%RUN_ID%`；
- **中文说明写在本 README，不写进 `.bat`**。

改完务必用上面两条自查确认 `LoneLF=0` 且非 ASCII 字节数为 0。

## 密钥文件

路径：`D:\erp-staging-backup\uspto-db.env`（机器本地，**不入仓**）。
格式是普通 `KEY=VALUE` 文本，**不是** `set "KEY=VALUE"` 的 cmd 片段。

| 键 | 说明 |
|---|---|
| `POSTGRES_PASSWORD` | `uspto-db` 容器的 postgres 口令 |
| `ERP_COMPOSE` | ERP-ALL `docker-compose.yml` 的**8.3 短路径** |

`DB_CONN` 由 `.bat` 自行拼装（**空格分隔 kv，非 URI**），无须在文件里给。

> ⚠ 连接自检要连**宿主机**映射端口。容器内 PostgreSQL 监听 `5432`，`5433` 是宿主机映射；
> 在容器内用 `host=127.0.0.1 port=5433` 自检必然 `Connection refused`——那是自检姿势错，
> 不是配置错。

口令丢失时无需回忆：容器内本地连接是 trust 认证，可直接轮换

```cmd
docker exec -i uspto-db psql -U postgres -d uspto -c "ALTER USER postgres PASSWORD '<新口令>';"
```

新口令**只用大小写字母+数字、长度 24+**：空格会破坏 `DB_CONN` 的 kv 解析，
`% & ^ < > | "` 是 cmd 元字符会破坏 `set`。

## 无人值守的真实约束（不是只改调度就行）

2026-07-25 实测：Docker Desktop 4.57.0，全部进程在 **Console 会话 1**，
`com.docker.service` 为 **`Stopped` / `Manual`**，登录自启 `True`。

即 **Docker Desktop 依赖交互式用户会话存在**。链路第 3-4 步要 `docker compose cp/exec`，
Docker Desktop 不运行则必挂。所以：

- 只把计划任务改成 `/RU /RP` 存储凭据**解决不了**——引擎命名管道是机器级、跨会话可达
  （调用方需在 `docker-users` 组），但**没人登录时 Docker Desktop 压根没启动**；
- **绝不要改成 `SYSTEM`**：SYSTEM 不在用户会话里，Docker Desktop 对它不可用；
- 正解 = **常驻登录**（保 Docker 活着；锁屏、RDP 断开都不影响，只要不注销）
  ＋ **存储凭据**（保会话锁定/断开等边角情形下任务照样触发）；
- **真正的缺口是重启**：机器重启且无人登录时 Docker Desktop 不启动、链路必挂。
  要名副其实的无人值守，须配 **Windows 自动登录**（`netplwiz` 取消「必须输入密码」
  或 `AutoAdminLogon`），让重启后自动进桌面会话、Docker 随之自启。

**待实测**：注销后容器是否存活、非交互任务能否访问 Docker。属破坏性实测，
需另排不影响验收窗口的时间做。

## 退出码

| 码 | 含义 |
|---|---|
| 0 | 成功 |
| 10 | 密钥文件不存在——**先排查 LF-only**（见纪律 1），再怀疑文件真缺 |
| 11 | 密钥文件缺 `POSTGRES_PASSWORD` 键 |
| 12 | 密钥文件缺 `ERP_COMPOSE` 键，或该 compose 文件不存在 |
| 20 / 21 / 22 | delta 导出 / 拷入容器 / ERP 导入 失败 |
| 267011 | 任务从未运行过（`schtasks` 侧，非本脚本） |

日志：`D:\erp-staging-backup\logs\uspto-daily-<RUN_ID>.log`。
delta CSV 保留 14 天于 `D:\walmart-trademark-sync\out\`，供事后对账核查。
