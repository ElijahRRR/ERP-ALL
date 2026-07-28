# PR #41 真机验证指令（给部署 AI，可整段粘贴）

> 合并前闸序**第三闸**。第一闸 CI 绿已过（4/4）；第二闸独立审查**已通过**
> （差分实测 33 组零分歧 + 四种图纸漂移变异全红；唯一低级问题 F1 已修）。
>
> **本单是纯重构：零迁移、零新端点、零前端改动。** 代码改动只有一个 commit（`8738c1d`），
> 5 个 backend 文件。它把两处「各写各的 SQL」的档位读取收进一个内核，**声明行为零变化**。
>
> **所以这一闸要验的不是逻辑对不对**（那由 CI 与审查侧的 33 组差分实测覆盖），
> **而是三件只有真机能回答的事**：
> ①新代码在这台机上真的起得来；②**现网真实数据**在新旧两套判定下结论是否一致；
> ③回滚路径通不通。
>
> 全程**不发任何渠道请求、不写任何业务数据、不跑迁移之外的 DDL**。

---

## 铁律（每次都读一遍，不许跳）

1. **绝不 `pg_restore` 进 `erp_all`**。本文对 `erp_all` **只做只读 SQL**，不导入任何转储、
   不建表、不改数据。本文**不需要**一次性容器（没有转储暂存环节）。
2. **不输出密钥**：贴回结果前自查，命令与输出里不得含口令、`client_secret`、token、代理账号密码。
   **`infra/.env` 的内容一个字都不要贴回来**——包括「我看了一眼，里面是 xxx」这种转述。
3. **不改码、不 push、不 merge**。你只做「切分支 → 起服务 → 只读取证 → 回滚」。
   发现问题就停下贴回，**不要自行「修一下再试」**。
4. 若任一步骤报错：**停在那一步**，把该步的完整报错贴回，不要继续往下跑。
5. **判成败一律看退出码**，不要看输出里有没有红字。每步都给了 `*_EXIT`，照抄那个数字。
6. **本分支尚未合并**。第 6 步必须执行回滚，把这台机切回 `main`。

---

## 前置：记住回滚锚点

```powershell
cd <ERP-ALL 仓库根>
git fetch origin main
git fetch origin claude/r2-03-launch-leg5n8
git rev-parse --short HEAD
git status --porcelain
```

**贴回①**：`git rev-parse` 那一行（**这是回滚锚点，第 6 步要用**）+ `git status --porcelain`
的输出（**期望为空**；非空说明本机有未提交改动，先停下告诉我，不要继续）。

```powershell
git checkout claude/r2-03-launch-leg5n8
git log --oneline -3
```

**贴回②**：`git log` 三行。**期望最上面一行是 `8dac51e`**（若不是，说明我又推了新提交，
停下告诉我实际看到的 sha，我核对后再让你继续）。

---

## 第 1 步：起服务（会重建镜像）

```powershell
cd infra
docker compose up -d --build
echo "UP_EXIT=$LASTEXITCODE"
docker compose ps
```

**贴回③**：`UP_EXIT`（**期望 `0`**）+ `docker compose ps` 的完整表格。

> `migrate` 服务会跑 `alembic upgrade head`。**本单零迁移，所以它应该是「已经在 head，无事可做」**。
> 若它报出正在执行某个新迁移，**立刻停下贴回**——那与「零迁移」矛盾，说明有东西不对。

---

## 第 2 步：确认容器里跑的确实是新代码（这一步是整套验证的前提）

```powershell
docker compose exec -T api python -c "import erp.core.automation as m; print('FLOWS=', len(m.FLOWS)); print('OK')"
echo "KERNEL_EXIT=$LASTEXITCODE"
```

**贴回④**：上面两行输出 + `KERNEL_EXIT`。
**期望**：打印 `FLOWS= 10` 与 `OK`，`KERNEL_EXIT=0`。

> **为什么先验这个**：`erp/core/automation.py` 是本单新增的文件，`main` 上**根本不存在**。
> 它能 import 成功且 `FLOWS` 恰好 10 条，就证明这个容器跑的是分支代码而不是旧镜像缓存。
> 这条同时是第 6 步回滚的判据——回滚后同一条命令**必须失败**。

```powershell
curl.exe -s -o NUL -w "%{http_code}`n" http://127.0.0.1:8000/api/health
echo "HEALTH_EXIT=$LASTEXITCODE"
```

**贴回⑤**：HTTP 状态码 + `HEALTH_EXIT`（期望 `200` 与 `0`）。

---

## 第 3 步：现网真实数据的新旧判定对拍（只读，本闸的核心）

把下面整段一次粘进 PowerShell：

```powershell
$sql = @'
\pset pager off
-- A. 现网 automation_policy 全量清点
SELECT flow_code, mode, enabled, count(*) AS rows
FROM app.automation_policy
GROUP BY flow_code, mode, enabled
ORDER BY flow_code, mode, enabled;

-- B. 旧读点 vs 新内核：逐行判定。divergent_* 必须都是 0
WITH e AS (
  SELECT team_id, flow_code, mode, enabled,
         CASE WHEN enabled THEN mode END                                    AS old_eff,
         CASE WHEN enabled AND mode IN ('manual','semi','auto')
              THEN mode ELSE 'manual' END                                   AS new_eff
  FROM app.automation_policy
), d AS (
  SELECT *,
    (coalesce(old_eff,'manual') = new_eff)                                  AS same_mode,
    ((old_eff IN ('semi','auto')) IS NOT DISTINCT FROM (new_eff IN ('semi','auto'))) AS same_gate
  FROM e
)
SELECT count(*) FILTER (WHERE NOT same_gate) AS divergent_gate,
       count(*) FILTER (WHERE NOT same_mode) AS divergent_mode,
       count(*)                              AS checked_rows
FROM d;

-- C. 会触发新增 warn 的行（运营需要知道：这些团队「配着但没生效」）
SELECT team_id, flow_code, mode, enabled,
       CASE WHEN NOT enabled THEN 'policy_disabled_treated_as_manual'
            WHEN mode NOT IN ('manual','semi','auto') THEN 'unknown_mode_treated_as_manual'
            WHEN flow_code IN ('order_block','compliance_block') AND mode = 'semi'
                 THEN 'illegal_mode_for_flow'
       END AS will_warn
FROM app.automation_policy
WHERE NOT enabled
   OR mode NOT IN ('manual','semi','auto')
   OR (flow_code IN ('order_block','compliance_block') AND mode = 'semi')
ORDER BY team_id, flow_code;

-- D. 闸类实际状态：哪些团队的订单拦截是真的开着的
SELECT t.id AS team_id, t.name,
       coalesce(p.mode,'(无行)')      AS order_block_mode,
       coalesce(p.enabled::text,'-')   AS enabled,
       CASE WHEN p.enabled AND p.mode IN ('semi','auto') THEN '拦截生效'
            ELSE '只软标记不冻结' END  AS effect
FROM app.team t
LEFT JOIN app.automation_policy p
       ON p.team_id = t.id AND p.flow_code = 'order_block'
ORDER BY t.id;
'@
$sql | docker exec -i erp-all-db-1 psql -v ON_ERROR_STOP=1 -U postgres -d erp_all
echo "CENSUS_EXIT=$LASTEXITCODE"
```

**贴回⑥**：A/B/C/D 四段的**完整输出** + `CENSUS_EXIT`。

**期望**：
- `CENSUS_EXIT=0`；
- **B 段 `divergent_gate` 与 `divergent_mode` 都必须是 `0`**——这是本闸最硬的一条。
  只要不是 0，**立刻停下贴回**：说明现网存在一行数据，在新旧两套判定下结论不同，
  而本单声明的是「行为零变化」。
- C 段可能 0 行也可能有行。**有行不是失败**，但那些团队值得注意——他们的策略「配着但没生效」。
- D 段是**给运营看的**：现网到底哪些团队的订单拦截是真的开着的。这个数以前没人查过。

> 容器名若不是 `erp-all-db-1`，用第 1 步 `docker compose ps` 里 db 那行的真实名字替换。

---

## 第 4 步：判据自检（证明第 3 步真的在查东西，不是在查空气）

**这一步不能跳。** 第 3 步的 C 段若返回 0 行，有两种可能：真的没有这类行，或者**查错了库/表**。
两者的输出长得一模一样。所以要证一次：

```powershell
$probe = @'
\pset pager off
SELECT count(*) AS total_rows FROM app.automation_policy;
SELECT count(*) AS total_teams FROM app.team;
'@
$probe | docker exec -i erp-all-db-1 psql -v ON_ERROR_STOP=1 -U postgres -d erp_all
echo "PROBE_EXIT=$LASTEXITCODE"
```

**贴回⑦**：两个 count + `PROBE_EXIT`。
**期望**：`total_rows` 应与第 3 步 **B 段的 `checked_rows` 相等**，`total_teams` 应与
**D 段的行数相等**。两者对不上就说明前面查的不是同一个库或同一批数据，**前面那些「0」不作数**。

---

## 第 5 步：看日志里有没有新的 automation 告警涌现

```powershell
docker compose logs --since 10m api beat | Select-String -Pattern "automation\." | Select-Object -First 40
echo "LOG_SCAN_DONE=1"
```

**贴回⑧**：匹配到的行（**没有匹配就明确写「无匹配」**）。

**怎么判读**：
- **无匹配 = 正常**（现网没有「停用/非法档位」的策略行，与第 3 步 C 段呼应）。
- 出现 `automation.policy_disabled_treated_as_manual` 或 `automation.illegal_mode_for_flow`：
  **不是故障**，是本单新增的留痕——它在说「某团队的某条策略配着但没按字面生效」。
  但请把命中的行贴回，它们应当与 C 段列出的团队对得上；**对不上就有问题**。
- 出现 `Traceback` / `ImportError` / `automation` 相关的 ERROR：**停下贴回**。

> ⚠️ 这一步用 `Select-String` 过滤，**`LOG_SCAN_DONE` 不是成败判据**（管道末端的退出码不可信，
> 这是我们踩过的坑）。这一步的判据是**你眼睛看到的匹配内容**，照抄贴回即可。

---

## 第 6 步：回滚到 main（**必做**，本分支尚未合并）

```powershell
cd <ERP-ALL 仓库根>
git checkout main
git log --oneline -1
cd infra
docker compose up -d --build
echo "ROLLBACK_UP_EXIT=$LASTEXITCODE"
```

**贴回⑨**：`git log` 那一行 + `ROLLBACK_UP_EXIT`（期望 `0`）。

```powershell
docker compose exec -T api python -c "import erp.core.automation" 2>&1
echo "ROLLBACK_KERNEL_EXIT=$LASTEXITCODE"
```

**贴回⑩**：输出 + `ROLLBACK_KERNEL_EXIT`。
**期望：这条命令必须失败**（`ModuleNotFoundError: No module named 'erp.core.automation'`，
`ROLLBACK_KERNEL_EXIT` **非 0**）。

> **这是负向判据，反着看**：`main` 上没有这个模块，所以 import 必须报错。
> 若它反而成功了，说明**回滚没真的生效**（旧镜像缓存、或 checkout 没成功）——
> **停下贴回**，这台机还跑在未合并的分支上。

```powershell
curl.exe -s -o NUL -w "%{http_code}`n" http://127.0.0.1:8000/api/health
echo "ROLLBACK_HEALTH_EXIT=$LASTEXITCODE"
```

**贴回⑪**：状态码 + `ROLLBACK_HEALTH_EXIT`（期望 `200` 与 `0`）——确认回滚后服务仍正常。

---

## 汇总：一共 11 条贴回

①仓库 head 与 `git status` ②分支三行 log ③`UP_EXIT`+`ps` ④内核 import+`KERNEL_EXIT`
⑤健康码 ⑥A/B/C/D 四段 + `CENSUS_EXIT` ⑦两个 count + `PROBE_EXIT` ⑧日志匹配（无则写「无匹配」）
⑨回滚 log + `ROLLBACK_UP_EXIT` ⑩**必须失败**的 import + `ROLLBACK_KERNEL_EXIT` ⑪回滚后健康码

**一票否决项**（命中任一即停，不要继续）：
- 第 1 步 migrate 执行了新迁移（与「零迁移」矛盾）
- 第 2 步 `FLOWS` 不是 10 或 import 失败
- 第 3 步 `divergent_gate` / `divergent_mode` 不是 0
- 第 4 步 count 与第 3 步对不上（说明前面的「0」不作数）
- 第 6 步 import **没有**失败（回滚未生效）

---

## 这份指令自己的已知局限（如实写明，别当成验过了）

1. **没有真的走一遍 `order_block` 拦截**。要走它得创建采购执行单——那是往生产库写业务数据，
   不值得为验证造。等价性由 CI 与审查侧的 33 组差分实测覆盖，本闸用**现网数据对拍**替代。
2. **第 3 步的对拍是「用 SQL 复现两套判定逻辑」，不是「跑两份真实代码」**。
   它能抓住「现网存在会导致分歧的数据行」，抓不住「我把判定逻辑翻译进 SQL 时翻错了」。
   后者由审查侧独立实测兜住（它跑的是真实代码）。
3. 第 5 步只看最近 10 分钟日志。服务刚起，量本来就少——**「无匹配」的信息量有限**，
   它主要是用来发现「涌现」，不是用来证明「一条都没有」。
