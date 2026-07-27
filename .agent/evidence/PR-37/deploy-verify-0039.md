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
-- 第一层：授权是否落到角色上（= 0039 是否生效）
SELECT r.name,
       count(*) FILTER (WHERE rp.permission_code LIKE 'compliance.%') AS compliance_perms,
       count(*) AS total_perms
FROM app.role r JOIN app.role_permission rp ON rp.role_id = r.id
GROUP BY r.name ORDER BY r.name;
```

**期望**（云端本地全量迁移实测值，供对拍）：

| name | compliance_perms | total_perms |
|---|---|---|
| 团队管理员 | 5 | 43 |
| 审核员 | 3 | 9 |
| 上架员 | 0 | 8 |
| 订单员 | 0 | 8 |
| 维护员 | 0 | 5 |
| 采集员 | 0 | 4 |
| 财务 | 0 | 3 |

> 本机若已建过团队，会**额外**出现同名的团队角色副本（0039 按角色名匹配、不带 team_id 过滤，
> 模板与副本都会授到）。行数比上表多属正常，**看数值对不对，不看行数**。

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

## 与 USPTO 连测的关系

今天 18:00 北京的 **USPTO 连测第 2 日 A 段照常跑，优先级更高**。本文这几步都是分钟级的，
可以在 A 段之前或之后做，两者互不干扰（不共用数据表、不共用定时任务）。
