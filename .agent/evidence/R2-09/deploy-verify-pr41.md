# PR #41 真机验证指令（给部署 AI，可整段粘贴）

> 合并前闸序**第三闸**。第一闸 CI 绿已过（4/4）；第二闸独立审查**已通过**
> （差分实测 33 组零分歧 + 四种图纸漂移变异全红；唯一低级问题 F1 已修）。
>
> **本单是纯重构：零迁移、零新端点、零前端改动。** 代码改动只有一个 commit（`f22f011`），
> 5 个 backend 文件。它把两处「各写各的 SQL」的档位读取收进一个内核，**声明行为零变化**。
>
> **所以这一闸要验的不是逻辑对不对**（那由 CI 与审查侧的 33 组差分实测覆盖），
> **而是三件只有真机能回答的事**：
> ①新代码在这台机上真的起得来；②**现网真实数据**在新旧两套判定下结论是否一致；
> ③切离验证分支的路径通不通。
>
> 全程**不发任何渠道请求、不写任何业务数据、不跑迁移之外的 DDL**。

---

## 铁律（每次都读一遍，不许跳）

1. **绝不 `pg_restore` 进 `erp_all`**。本文对 `erp_all` **只做只读 SQL**，不导入任何转储、
   不建表、不改数据。本文**不需要**一次性容器（没有转储暂存环节）。
2. **不输出密钥**：贴回结果前自查，命令与输出里不得含口令、`client_secret`、token、代理账号密码。
   **`infra/.env` 的内容一个字都不要贴回来**——包括「我看了一眼，里面是 xxx」这种转述。
3. **不改码、不 push、不 merge**。你只做「切分支 → 起服务 → 只读取证 → 切回」。
   发现问题就停下贴回，**不要自行「修一下再试」**。
4. 若任一步骤报错：**停在那一步**，把该步的完整报错贴回，不要继续往下跑。
5. **判成败一律看退出码**，不要看输出里有没有红字。每步都给了 `*_EXIT`，照抄那个数字。
6. **本分支尚未合并**。第 6 步必须把这台机切离验证分支（推荐切到 `main`）。

---

## 前置：记住锚点

> **v2 修订（2026-07-28）**：初版这里写「`git status --porcelain` **期望为空**，非空就停」。
> **那条判据是我写宽了**——部署机据此正确地停在了这一步，问题在指令不在执行。
> **未跟踪文件（`??`）对切分支无害**，git 只在「未跟踪文件与目标分支的已跟踪文件同名」
> 时才会拒绝 checkout。真正该拦的是**已跟踪文件的未提交改动**（那会被 checkout 带走或冲突）。
> 下面改成分开查。

```powershell
cd <ERP-ALL 仓库根>
git fetch origin main
git fetch origin claude/r2-03-launch-leg5n8
git rev-parse --short HEAD
git status --porcelain --untracked-files=no
echo "TRACKED_DIRTY_EXIT=$LASTEXITCODE"
```

**贴回①**：`git rev-parse` 那一行 + `git status --porcelain --untracked-files=no` 的输出。

- **期望后者为空**。为空 = 没有已跟踪文件被改动，**可以安全切分支**（未跟踪文件不影响）。
- **非空才停**：那说明有人在这台机上直接改过仓库里的文件，停下贴回，不要继续。

> **本机已知的未跟踪文件（2026-07-28 回执）**：`.codex/`、`AGENTS.md`、`RS-02a-runbook.md`、
> `erp_all-before-0039.dump`、`frontend/.pnpm-store/`。云端侧已逐个核过：**这五个在 `main`
> 与验证分支上都不存在同名已跟踪文件**，所以 checkout 不会碰它们，也不会被拒绝。
>
> ⚠️ 其中 **`erp_all-before-0039.dump` 是一份生产库转储**。它躺在仓库根且此前**未被
> `.gitignore` 忽略**——一次 `git add -A` 就会把生产转储提交进仓库。本 PR 已把
> `*.dump` 加进 `.gitignore` 堵死这条路。**转储文件本身留不留、什么时候删，归 Owner 定；
> 你不要删它、也不要打开看它的内容。**

```powershell
# ⚠️ 必须用 -B：git fetch 只更新 origin/<分支>，**不会**动已存在的同名本地分支。
# 直接 `git checkout <分支>` 检出的是这台机上的陈旧本地副本（2026-07-28 实际踩到）。
git checkout -B claude/r2-03-launch-leg5n8 origin/claude/r2-03-launch-leg5n8
echo "CHECKOUT_EXIT=$LASTEXITCODE"
git rev-parse --short HEAD
git rev-parse --short origin/claude/r2-03-launch-leg5n8
git status -sb
git log --oneline -3
```

**贴回②**：`CHECKOUT_EXIT` + 两个 `rev-parse` 的输出 + `git status -sb` 那一行 + 三行 log。

**判据（自校验，不依赖我写死的 sha）**：
- `CHECKOUT_EXIT=0`；
- **两个 `rev-parse` 输出必须完全相同**——这就是「检出的确实是远端最新」的全部判据；
- `git status -sb` **不得出现 `ahead` / `behind` / `diverged`**（应形如
  `## claude/r2-03-launch-leg5n8...origin/claude/r2-03-launch-leg5n8`，后面没有计数）。

> **为什么改成自校验**：初版让你比对我写死的 sha（`8dac51e` → `28769e8` → …）。
> 但这个分支在 main 每次推进后都会 rebase，**sha 一直在变，我写进指令的那一刻就开始过期**。
> 「本地 HEAD == 远端分支尖端」是不会过期的不变量。**这个毛病在 PR 正文里已经犯过四次**，
> 不该再带到给你的指令里。

> **`-B` 会丢弃本地分支上的独有提交，这里是安全的**：2026-07-28 回执显示这台机的本地副本
> 停在 RS-02a 时期（`3d5178d` / `2aee4fb` / `23c8ede` 等 6 个）。云端侧已实证——
> 那些提交改的文件在 `main` 上**已是最终态**（逐文件 diff 为空，引入的标识符都在），
> `1986bb1` 是 PR #39 的 **squash 合并**（单亲），内容早已并入 `main`。
> 所以丢的是**已合并内容的陈旧副本**，零损失；且 git reflog 90 天内仍可恢复。
>
> 若 `git log --oneline origin/claude/r2-03-launch-leg5n8..HEAD@{1}` 里出现**不是我写的提交**
> （作者非 Claude、或消息与 R2-09/RS-02a 无关），**停下贴回**——那说明这台机上有人提交过东西。

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
> 这条同时是第 6 步的判据——切回 main 后同一条命令**必须失败**。

```powershell
curl.exe -s -o NUL -w "%{http_code}`n" http://127.0.0.1:8000/healthz
echo "HEALTH_EXIT=$LASTEXITCODE"
```

**贴回⑤**：HTTP 状态码 + `HEALTH_EXIT`（期望 `200` 与 `0`）。

> **v3 修订（2026-07-28）**：初版这里写的是 `/api/health`，**那个路径不存在，是我凭记忆编的**。
> 真实端点是 **`/healthz`**（`backend/src/erp/main.py:110`，`@app.get("/healthz")`，**无 `/api` 前缀**，
> 业务路由才挂 `/api/v1`）。部署机如实报回「分支与 main 都是 404」——**两边都 404 恰好证明
> 这不是回归而是我路径写错**，它没有自行改试其他路径，处理得对。

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

## 第 6 步：切回 main（**必做**，本分支尚未合并）

> **v2 修订（2026-07-28）**：初版这里叫「回滚」，措辞不准。2026-07-28 的回执显示
> **这台机的 HEAD 是 `1986bb1`（RS-02a 那次合并），比 `main` 落后 5 个提交**
> ——RS-02a 之后那次「切回 main」始终没做，这个悬了两天的回执**至此有答案了**。
>
> 所以本步对这台机而言**不是回滚而是前进 5 个提交**。云端侧已核过那 5 个提交的内容：
> `1986bb1..1f09edf` 在 `backend/` `frontend/` `workers/` `infra/` 下的**唯一改动是
> `infra/local-deploy/README.md`（+26 行文档）**，其余全是 `.agent/` 台账与 `specs/` 正文。
> **即：这台机在跑的运行代码与当前 main 逐字相同，没有功能漂移。**
> 切过去是把 git 状态对齐，不会改变正在跑的程序行为。
>
> 若你更希望保持原状不动，也可以停在这一步告诉 Owner——但**不要停在验证分支上**，
> 那是未合并代码。两个可接受的终态：`main`（推荐）或 `1986bb1`（原状）。

```powershell
cd <ERP-ALL 仓库根>
# ⚠️ 同样必须用 -B：`git fetch origin main` 只更新 origin/main，**不会动本地 main**。
# 2026-07-28 实际踩到：直接 `git checkout main` 落在陈旧的本地 main（1986bb1）而非远端最新。
git checkout -B main origin/main
git rev-parse --short HEAD
git rev-parse --short origin/main
cd infra
docker compose up -d --build
echo "ROLLBACK_UP_EXIT=$LASTEXITCODE"
```

**贴回⑨**：两个 `rev-parse` 的输出 + `ROLLBACK_UP_EXIT`。
**判据**：两个 `rev-parse` **必须完全相同**（自校验，同前置②的写法），`ROLLBACK_UP_EXIT=0`。

```powershell
docker compose exec -T api python -c "import erp.core.automation" 2>&1
echo "ROLLBACK_KERNEL_EXIT=$LASTEXITCODE"
```

**贴回⑩**：输出 + `ROLLBACK_KERNEL_EXIT`。
**期望：这条命令必须失败**（`ModuleNotFoundError: No module named 'erp.core.automation'`，
`ROLLBACK_KERNEL_EXIT` **非 0**）。

> **这是负向判据，反着看**：`main` 上没有这个模块，所以 import 必须报错。
> 若它反而成功了，说明**切换没真的生效**（旧镜像缓存、或 checkout 没成功）——
> **停下贴回**，这台机还跑在未合并的分支上。

```powershell
curl.exe -s -o NUL -w "%{http_code}`n" http://127.0.0.1:8000/healthz
echo "ROLLBACK_HEALTH_EXIT=$LASTEXITCODE"
```

**贴回⑪**：状态码 + `ROLLBACK_HEALTH_EXIT`（期望 `200` 与 `0`）——确认切回后服务仍正常。

---

## 汇总：一共 11 条贴回

①仓库 head 与 `git status --untracked-files=no` ②`CHECKOUT_EXIT`+两个 rev-parse（**必须相同**）+`status -sb`+三行 log ③`UP_EXIT`+`ps` ④内核 import+`KERNEL_EXIT`
⑤健康码 ⑥A/B/C/D 四段 + `CENSUS_EXIT` ⑦两个 count + `PROBE_EXIT` ⑧日志匹配（无则写「无匹配」）
⑨切回 main 的 log + `ROLLBACK_UP_EXIT` ⑩**必须失败**的 import + `ROLLBACK_KERNEL_EXIT` ⑪切回后健康码

**一票否决项**（命中任一即停，不要继续）：
- 前置 ② 两个 `rev-parse` **不相同**，或 `status -sb` 出现 ahead/behind/diverged
- 第 1 步 migrate 执行了新迁移（与「零迁移」矛盾）
- 第 2 步 `FLOWS` 不是 10 或 import 失败
- 第 3 步 `divergent_gate` / `divergent_mode` 不是 0
- 第 4 步 count 与第 3 步对不上（说明前面的「0」不作数）
- 第 6 步 import **没有**失败（切换未生效，机器还跑在未合并分支上）

---

## 这份指令自己的已知局限（如实写明，别当成验过了）

1. **没有真的走一遍 `order_block` 拦截**。要走它得创建采购执行单——那是往生产库写业务数据，
   不值得为验证造。等价性由 CI 与审查侧的 33 组差分实测覆盖，本闸用**现网数据对拍**替代。
2. **第 3 步的对拍是「用 SQL 复现两套判定逻辑」，不是「跑两份真实代码」**。
   它能抓住「现网存在会导致分歧的数据行」，抓不住「我把判定逻辑翻译进 SQL 时翻错了」。
   后者由审查侧独立实测兜住（它跑的是真实代码）。
3. 第 5 步只看最近 10 分钟日志。服务刚起，量本来就少——**「无匹配」的信息量有限**，
   它主要是用来发现「涌现」，不是用来证明「一条都没有」。
