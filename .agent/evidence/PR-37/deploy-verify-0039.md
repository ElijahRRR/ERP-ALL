# PR #37 真机验证指令（给部署 AI，可整段粘贴）

> 合并前闸序第三闸。第一闸 CI 绿已过（run 239+，4/4）；第二闸「审查 AI 通读 diff」并行进行。
> **本文只验迁移 0039 与权限两层可见性，不动业务数据、不发任何渠道请求。**

## 铁律（每次都读一遍，不许跳）

1. **绝不 `pg_restore` 进 `erp_all`**。本文全部操作都在 `erp_all` 上**只做 alembic 与只读 SQL**，
   不导入任何转储。
2. **不输出密钥**：贴回结果前自查，命令与输出里不得含口令、`client_secret`、token、代理账号密码。
   下文所有连库命令都用 `.env` / 环境变量里的既有 DSN，**不要把 DSN 明文贴回来**。
3. 若任一步骤报错：**停在那一步**，把该步的完整报错贴回，不要继续往下跑、也不要自行「修一下再试」。
4. 本文**不需要**一次性容器（没有转储暂存环节）。

## 前置

```powershell
# 切到 PR 分支的当前 head（验证对象必须是分支，不是 main）
git fetch origin claude/r2-03-launch-leg5n8
git checkout claude/r2-03-launch-leg5n8
git log --oneline -1
```

**贴回①**：上面这行 `git log` 的输出（用于确认验的是哪个 commit）。

## 第 1 步：迁移三步演练（升 → 降 → 升）

```powershell
alembic upgrade head
echo "UPGRADE_EXIT=$LASTEXITCODE"
alembic downgrade base
echo "DOWNGRADE_EXIT=$LASTEXITCODE"
alembic upgrade head
echo "REUPGRADE_EXIT=$LASTEXITCODE"
alembic current
```

**贴回②**：三个 `*_EXIT` 的值 + `alembic current` 的输出（期望三个都是 `0`、current 为 `0039 (head)`）。

> ⚠️ `downgrade base` 会清空 `erp_all` 的业务表。**如果这台机上的 `erp_all` 存着你不想丢的数据，
> 先停在这里告诉我**——我改成只跑 `upgrade head` 的简化版，不做降级演练（降级路径 CI 已覆盖）。

## 第 2 步：权限两层可见性（只读）

**这一步的关键在于「分两层看」**。0039 是按**角色**授权的，而本机 `user_role` 目前是空的，
所以**按用户查会看到 0，那不是迁移失败**——两层要分开验。

```sql
-- 第一层：0039 涉及的 8 个码是否落到角色上（**只查这 8 个码，对 0039 直接敏感**）
SELECT r.name, count(*) AS granted
FROM app.role_permission rp
JOIN app.role r ON r.id = rp.role_id AND r.team_id IS NULL   -- 只看模板角色，滤掉团队副本
WHERE rp.permission_code IN ('procurement.execute','procurement.admin','pricing.write',
  'compliance.import_read','catalog.import_write','catalog.category_write',
  'catalog.source_write','listing.error_admin')
GROUP BY r.name ORDER BY r.name;
```

**期望**（云端 2026-07-27 在**改完码之后**实跑量得，按 psql 输出原样抄、含行序，不是推导；
`downgrade 0038 → upgrade head` 与整链 `downgrade base → upgrade head`（即你上一步跑的那个）
两种跑法结果相同）：

| name | granted |
|---|---|
| 团队管理员 | 8 |
| 审核员 | 2 |
| 维护员 | 1 |
| 订单员 | 1 |
| 采集员 | 1 |

**模板角色合计 13 行**（＝迁移里 `_GRANTS` 的条目数）。本机若建过团队，库里会**额外**有同名团队
角色副本各一份（0039 按角色名匹配、不带 `team_id` 过滤，模板与副本都授到）；上面的
`r.team_id IS NULL` 已把副本滤掉，**所以不管建过几个团队，这张表都该长这样**。

> 团管那 8 行里有一行（`compliance.import_read`）其实是 `0010_import_job.py` 的产出，0039 的
> `WHERE NOT EXISTS` 会跳过它。这张表锁的是**跑完全链后应有的终态**，不是「0039 这一步插了几行」
> ——终态才是这台机上要验的东西。

> **这条 SQL 被改过两次，都是同一类错：判据和被判对象对不上。**
> 1. 审查 AI 的 F9：原判据首列是 `count(*) FILTER (WHERE permission_code LIKE 'compliance.%')`，
>    而当时 0039 的 8 个码里一个 compliance 都没有——原表里的「团管 5 / 审核员 3」全部来自
>    0010 与 0035，**跑不跑 0039 都是这两个数**。拿它判「0039 是否生效」是看错了列。
> 2. 审查 AI 的 N1：F1 把 0039 授的码从 `catalog.import_read` 改成了 `compliance.import_read`，
>    这条 SQL 的 IN 列表**没跟着改**。云端实测过后果：在一个**迁移成功**的库上，旧 IN 列表
>    返回的是「团管 7 / 审核员 1 / 维护员 1 / 订单员 1，采集员整行不见」＝10，与期望的 13 对不上，
>    **会把成功判成失败、诱使人去回滚一个好的迁移**（而回滚这个动作本身还踩着 N2 那个坑）。
>    ——本轮已改 IN 列表，并在改码**之后**重测了上面那张表。

```sql
-- 第二层：有没有用户真拿到（现状预期为空）
SELECT u.id, u.username, u.is_super, r.name AS role
FROM app.app_user u
LEFT JOIN app.user_role ur ON ur.user_id = u.id
LEFT JOIN app.role r ON r.id = ur.role_id
ORDER BY u.id;
```

**期望**：`role` 全为 `NULL`。**这是正确状态，不是缺陷**——绑角色是运维动作，由 Owner 决定谁拿
什么角色；`admin` 靠 `is_super=true` 短路获得全部权限，与授权表无关。

**贴回③**：上面两条 SQL 的完整结果表。

## 第 3 步：服务能起来（冒烟，不发渠道请求）

```powershell
# 按本机既有方式启动后端后：
curl -s http://127.0.0.1:8000/healthz
```

**贴回④**：`/healthz` 的返回。

## 不要做的事

- **不要**动 `channel.gateway_mode` 配置，本文全程不涉及渠道写路径。
- **不要**给任何用户绑角色——那是 Owner 的决定，不在本次验证范围。
- **不要**切回 main。PR 尚未合并，切回去会让分支上的运维资产从检出树消失
  （`infra/local-deploy/README.md`「切回 main 前必做」那节讲的就是这个）。
  〔2026-07-27 PR #37 已合并（`31e0828`），此条解除；切回 main 前仍须过「运维资产在位检查」。〕

## 与 USPTO 连测的关系

今天 18:00 北京的 **USPTO 连测第 2 日 A 段照常跑，优先级更高**。本文这几步都是分钟级的，
可以在 A 段之前或之后做，两者互不干扰（不共用数据表、不共用定时任务）。

---

## 回执（2026-07-27，部署机贴回，验证对象 `042469c`）

**四项全通过。** 原文按贴回内容记录，未改写。

| 项 | 结果 |
|---|---|
| 分支对齐 | 本地曾落后 27 个提交且有 2 个本地提交（`9fc9711`/`b0d335d`，均为已合并内容的旧副本）；建备份分支 `deploy-local-20260727` + 另存 automation 后 `reset --hard`，对齐到 `042469c` |
| 升级前基线 | `alembic current` = **`0038 (head)`** |
| 转储 | `pg_dump -Fc` 退出码 0，**271 669 356 bytes**（只 dump 不 restore） |
| 迁移 | `migrate` 容器 `Exited (0)`，日志无异常；升级后 `alembic current` = **`0039 (head)`** |
| 权限第一层 | 团队管理员 8 / 审核员 2 / 维护员 1 / 订单员 1 / 采集员 1 ＝ **13**，与期望表逐行相符 |
| 权限第二层 | `admin`（is_super=t）与 `pr35_nocompliance` 均 `role = NULL`——正确状态 |

> 上表是**验证当时**的状态，不改。此后（同日）Owner 手工给 `pr35_nocompliance` 绑了 `text` 团队的
> 团队管理员副本（role_id=14 / 42 权限），核过绑的是**团队副本而非模板**、`user_team = role_team = 1`
> ——这正是 `identity/router.py:321-329` 要求的形态。连带后果记在 `progress.md`：该账号不再能用于
> 「无权限即 403」的否定验证。
| 冒烟 | `/healthz` → `{"status":"ok","version":"0.1.0"}` |

全程未降级、未发渠道请求、未改 `channel.gateway_mode`、未绑定角色、未跑 `git clean`。

### 两条过程记录（都值得留下）

**① 原生 Windows 跑不了 alembic——`alembic.ini` 有中文注释。** 本文第 1 步原写的是
PowerShell 原生 `alembic upgrade head`，部署机执行即 `UnicodeDecodeError: 'gbk' codec can't
decode byte 0xb1 in position 129`。成因已定位：`backend/alembic.ini:7` 是中文注释，而
`configparser` 读 ini 时不指定编码、用系统本地编码（中文 Windows 为 GBK），撞上 UTF-8 的
「由」（`e7 94 b1`，文件第一个非 ASCII 字节在 121，与报错位置吻合）。

**这不是环境问题，是本文给错了路径**：`infra/local-deploy/README.md` 的增量验证流程本就是
`make up`，迁移跑在 `migrate` 容器里（Linux/UTF-8），从来碰不到这个坑。已改用容器路径重跑。
`alembic.ini` 的非 ASCII 属真欠账（原生调用在 GBK 机器上一律起不来），另行清偿。

**② 部署机在两处主动停手，都停对了。** 一是发现本地分支未对齐远端（验证对象不是 PR 最新
head），二是发现本机 `erp_all` 存有业务数据、不能跑 `downgrade base`。两处都按本文铁律 3
停在原地等确认，没有自行「修一下再试」——本文第 1 步预写的那条退路（数据不能丢就改简化版）
正是为此，实际用上了。

### 后续（合并后）

PR #37 已于 2026-07-27 合并进 main（`31e0828`，squash）。部署机切回 main 前须先过
`infra/local-deploy/README.md`「切回 main 前必做：运维资产在位检查」——期望三行齐全
（`.gitattributes` / `automation/README.md` / `automation/uspto-daily.bat`），云端侧已核过
main 上三者都在。切回后 `make up` 不会再动库（main 与本次验证同为 0039）。
