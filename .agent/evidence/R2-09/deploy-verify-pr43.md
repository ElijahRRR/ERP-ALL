# PR #43 真机验证指令（给部署 AI，可整段粘贴）

> 合并前闸序**第三闸**。第一闸 CI 四项绿 @ `c02328a`；第二闸独立审查**通过、无阻拦项**
> （审查侧另起临时 worktree 对幂等键判据做了变异证伪，4 条转红，不采信开发侧自陈）。
>
> **与 PR #42 有三点不同，先记住再动手**：
>
> ① **本单零迁移**。`git diff origin/main...HEAD -- backend/alembic/` 为空。
>    因此**第 6 步切回 main 不需要 `alembic downgrade`**——#42 那次必须降 0040，这次不要降，
>    降了反而会破坏 main 需要的表结构。
> ② 没有新页面。能力挂在既有 `/products` 页上（勾选 → 批量送审）。
> ③ **必须有一步经 `http://<内网IP>` 走写操作**（审查侧提出，见第 3 步）。这条是**结构性**的，
>    不是走个形式：`localhost` 本身就是安全上下文，本单修的那个 bug 用 localhost 验
>    **在原理上就抓不到**。

---

## 本文分三段，**花钱的只有 C 段**

| 段 | 内容 | 花钱 |
|---|---|---|
| **A**（第 3 步） | 内网 IP 写路径 | **零** |
| **B**（第 4 步） | 批量端点：入参校验 + 四桶结构不变量 + 幂等重放 | **零**（显式传 `levels=["l0","l2"]`，不碰 L3） |
| **C**（第 5 步） | UI 路径整链 | **花真钱**：UI 不传 levels → 后端默认 `["l0","l2","l3"]` → **L3 会真的调 LLM** |

**C 段没有 Owner 明确放行就不要跑。** A、B 两段已能覆盖本单绝大部分判据；C 段的独有价值只是
「UI 按钮到后端这一段接线」。若 Owner 未放行，跑完 A+B 就贴回，在回执里写明「C 段未执行（待放行）」。

---

## 铁律（每次都读一遍，不许跳）

1. **绝不 `pg_restore` 进 `erp_all`**。本文对 `erp_all` 的写操作**只有两类**：你在 B/C 段
   主动发起的送审（这正是被验的功能），以及第 6 步写明的还原语句。除此之外只读。
2. **不输出密钥**：贴回结果前自查，命令与输出里不得含口令、`client_secret`、token、代理账号密码。
   **`infra/.env` 的内容一个字都不要贴回来**。登录命令那一行**不要贴回**（含口令），
   只贴它后面那些命令的输出。若用浏览器开发者工具看请求，**不要把 `Authorization` 头贴回来**。
3. **不改码、不 push、不 merge**。发现问题就停下贴回，**不要自行「修一下再试」**。
4. 若任一步骤报错：**停在那一步**，把该步的完整报错贴回，不要继续往下跑。
5. **判成败一律看退出码与写明的判据**，不要看输出里有没有红字。
6. **本分支尚未合并**，第 6 步必须把这台机切回 `main`。**但不要跑 alembic downgrade**（见上）。

---

## 前置：锚点与切分支（自校验，无写死 sha）

```powershell
cd <ERP-ALL 仓库根>
git fetch origin main
git fetch origin claude/r2-03-launch-leg5n8
git rev-parse --short HEAD
git status --porcelain --untracked-files=no
```

**贴回①**：`rev-parse` 一行 + `status --porcelain` 输出。**期望后者为空**（未跟踪文件无害，不看）。
非空 = 有人改过已跟踪文件，停下贴回。

```powershell
# 必须 -B：git fetch 不更新已存在的同名本地分支（此前实际踩到）
git checkout -B claude/r2-03-launch-leg5n8 origin/claude/r2-03-launch-leg5n8
echo "CHECKOUT_EXIT=$LASTEXITCODE"
git rev-parse --short HEAD
git rev-parse --short origin/claude/r2-03-launch-leg5n8
git status -sb
git diff --stat origin/main...HEAD -- backend/alembic/
echo "ALEMBIC_DIFF_EMPTY=$($null -eq (git diff --name-only origin/main...HEAD -- backend/alembic/))"
```

**贴回②**：`CHECKOUT_EXIT` + 两个 `rev-parse` + `status -sb` + 最后两行。
**判据**：`CHECKOUT_EXIT=0`；**两个 `rev-parse` 完全相同**；`status -sb` 无 `ahead`/`behind`/`diverged`；
**alembic 那两行必须是空 / `True`**——这是「本单零迁移」的现场复算，不是采信 PR 正文。

---

## 第 1 步：起服务（**本单零迁移，migrate 不应出现 Running upgrade**）

```powershell
cd infra
docker compose up -d --build
echo "UP_EXIT=$LASTEXITCODE"
docker compose ps -a
docker compose logs migrate | Select-String -Pattern "Running upgrade|error|ERROR" | Select-Object -First 10
```

**贴回③**：`UP_EXIT`（期望 `0`）+ `ps -a` 表格（migrate 行应 `Exited (0)`）+ 日志匹配行。
**判据**：**不应出现任何 `Running upgrade`**（DB 已在 main 的版本，本单不加迁移）。
若出现了 `Running upgrade`：**停下贴回**——那说明这台机的 DB 版本比 main 落后，与本单无关但要先弄清。

---

## 第 2 步：产物对拍（**UI/浏览器判据之前必做的机器判据**）

本项目「浏览器跑旧产物」已复发三次（HF-0716①、FE-0716、#42 的 C-unset 假阳性）。
**只靠 Ctrl+F5 的口头约定挡不住**，改为机器判据——两个 chunk 名必须相同才继续：

```powershell
docker compose exec frontend sh -c "ls /usr/share/nginx/html/assets/index-*.js"
```

再在**浏览器页面控制台**执行：

```js
document.querySelector('script[type=module]')?.src
```

**贴回④**：两个文件名。
**判据**：**两者文件名一致**。不一致 = 浏览器拿的是缓存旧壳，**此后任何 UI/浏览器判据结论都不作数**。
处置：DevTools 勾 Disable cache 后 Ctrl+Shift+R，或**直接关掉标签页重开**（长活标签页只点页内刷新
按钮不会重取壳，#42 就栽在这），直到一致为止。

---

## 第 3 步（A 段·零花钱）：内网 IP 写路径 —— **本轮新增的核心判据**

### 背景（先读，否则不知道在验什么）

`main` 的 `frontend/src/api/client.ts:129`，`api.post` 的幂等头是 `crypto.randomUUID()`。
**该 API 只在安全上下文（HTTPS 或 localhost）存在**，而 `frontend/nginx.conf` 只 `listen 80`
——即明文 http 部署。于是 **main 上经 `http://<内网IP>` 访问时，每一个 `api.post` 都会在生成
幂等头那一步抛 TypeError，请求根本发不出去**。本 PR 换成 `randomHex(16)` 修掉了。

**用 `http://localhost` 验证这条在原理上抓不到**——localhost 是安全上下文，`randomUUID` 在那儿可用。

### 3.1 先证明「这个页面确实处在非安全上下文」

浏览器打开 **`http://<本机内网IP>/`**（例如 `http://192.168.x.x/`，**不要用 localhost / 127.0.0.1**），
登录后在控制台执行：

```js
[location.origin, window.isSecureContext, typeof crypto.randomUUID, typeof crypto.getRandomValues]
```

**贴回⑤**：这一行的输出（**不含任何 token**）。
**判据**：`isSecureContext === false`、`typeof crypto.randomUUID === "undefined"`、
`typeof crypto.getRandomValues === "function"`。
> 若 `isSecureContext` 是 `true`，说明你还是开的 localhost 或走了 HTTPS——**换成内网 IP 重来**，
> 否则这一步等于没验。

### 3.2 对照组（可选但推荐，一条命令说明为什么必须用内网 IP）

同一台机浏览器另开 `http://localhost/`，控制台执行同一行。
**期望**：`isSecureContext === true`、`typeof crypto.randomUUID === "function"`。
**这就是「localhost 验证抓不到该 bug」的现场证据。**

### 3.3 在内网 IP 页面上做一次写操作

回到 **`http://<内网IP>/`** 的标签页，点右上角**通知铃 → 全部已读**（对应
`POST /api/v1/notifications/read-all`；**幂等、零成本、只影响你自己账号的通知已读位**）。

**判据**：
- 操作**成功**（无红色报错提示）；
- DevTools Network 里那条 `read-all` 请求**状态码 2xx**，且请求头里**有** `Idempotency-Key`
  （**只看它存在与形状，不要把值贴回来**——它不是密钥，但同样没必要外流）。

**贴回⑥**：「成功/失败」+ 状态码 + 「Idempotency-Key 头存在：是/否」。

> **对照说明（不用真去跑）**：同样的操作在 main 上会在控制台抛
> `TypeError: crypto.randomUUID is not a function` 且**请求根本不出现在 Network 里**。
> 你若想加验一次，可以在第 6 步切回 main 后重做 3.3——但这不是必须项。

---

## 第 4 步（B 段·零花钱）：批量端点

### 4.0 取 token（**这一行不要贴回**）

```powershell
# <账号>/<口令> 换成本机已有的、绑「团队管理员」的普通账号（不是超管——超管还需 X-Act-Team）
$r = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1/api/v1/auth/login" `
     -ContentType "application/json" -Body '{"username":"<账号>","password":"<口令>"}'
$H = @{ Authorization = "Bearer $($r.access_token)" }
"TOKEN_OK=$($null -ne $r.access_token)"
```

**贴回⑦**：只贴 `TOKEN_OK=True` 这一行。**登录那条命令本身不要贴回。**

### 4.1 四条入参校验（**全在开审之前，零金钱代价**）

> **v2 修订（2026-07-29，部署机首轮在此停手，两处都是本指令的错，不是产品缺陷）**：
> **(a) 漏传 `Idempotency-Key`。** 该头是契约 002 的**必填头**，由 FastAPI 在进入业务层
> 之前校验。首轮三条请求都只带了 `$H`，于是全部停在头校验、返回同一个
> `{"detail":[{"type":"missing","loc":["header","Idempotency-Key"],...}]}`，
> **一条也没到达要验的业务判定**。
> **(b) 第③条的期望值本身写错了。** 空列表**返回 200 + 四个空桶**，不是 422——
> 这是 `AuditBatchIn` 头注明文承诺、`test_audit_batch.py::test_t20_*` 钉死的行为
> （不加 `min_length=1` 的理由正是：那会落进 FastAPI 无错误码的默认信封，绕过 002
> 统一错误信封）。首轮若真带了 key，第③条会因为我的期望值错误而误报缺陷。

**每条用一个独立的 `Idempotency-Key`**。原因：①②在幂等占位**之前**就被拒（键其实没被
消费），但④会走到幂等层；**若几条共用一个键而载荷不同，会拿到 409 `IDEMPOTENCY_CONFLICT`
而不是各自的业务错误**，判据就失效了。用独立键把这条干扰彻底排除。

```powershell
function Try-Post($body, $key) {
  $h = $H.Clone()
  if ($key) { $h['Idempotency-Key'] = $key }
  try {
    $resp = Invoke-WebRequest -Method Post -Uri "http://127.0.0.1/api/v1/products/audit" `
            -Headers $h -ContentType "application/json" -Body $body -SkipHttpErrorCheck
    "$($resp.StatusCode) $($resp.Content)"
  } catch { "EXCEPTION: $_" }
}

# ① 大小写笔误的层级（本单修掉的合规闸绕过）——本步最要紧的一条
Try-Post '{"product_ids":[1],"levels":["L0"]}'  'verify-pr43-case1'
# ② l4 未开放
Try-Post '{"product_ids":[1],"levels":["l4"]}'  'verify-pr43-case2'
# ③ 空列表——**期望 200，不是 4xx**
Try-Post '{"product_ids":[]}'                   'verify-pr43-case3'
# ④ 缺 Idempotency-Key（首轮意外撞出来的行为，现在把它钉成判据）
Try-Post '{"product_ids":[1]}'                  $null
```

**贴回⑧**：四行输出。
**判据**：

| 用例 | 期望状态 | 期望响应 |
|---|---|---|
| ① `levels:["L0"]` | 422 | 含 `code` = `AUDIT_LEVELS_INVALID` |
| ② `levels:["l4"]` | 422 | 含 `code` = `AUDIT_L4_DISABLED` |
| ③ `product_ids:[]` | **200** | `audited/skipped/failed/remaining` 四个桶全为空数组 |
| ④ 无 `Idempotency-Key` | 422 | `{"detail":[{"type":"missing","loc":["header","Idempotency-Key"],...}]}` |

> **①这条是本单最要紧的一条判据**。修之前，`levels=["L0"]` 会让 L0/L2/L3 **一个分支都不命中**，
> `verdict` 保持初值 `pass` 直接落库——产品当场变 `audit_passed`、零命中、cost=0，随即可分配上架，
> **整条合规闸被一个大小写笔误绕过**。现在必须是 422。
>
> **④的信封与①②不同是已知欠账，不是本轮缺陷**：①②走 002 统一信封（有 `code`），
> ④走 FastAPI 默认的 `{"detail": [...]}`（无 `code`）——全仓没有 `RequestValidationError`
> handler，这是跨端点的既有欠账（已登记 TD-6），本单不修。**判④只看状态码与 `loc` 指向
> 那个头，不要求它有 `code`。**

> **①这条是本单最要紧的一条判据**。修之前，`levels=["L0"]` 会让 L0/L2/L3 **一个分支都不命中**，
> `verdict` 保持初值 `pass` 直接落库——产品当场变 `audit_passed`、零命中、cost=0，随即可分配上架，
> **整条合规闸被一个大小写笔误绕过**。现在必须是 422。

### 4.2 四桶结构不变量（显式传 `levels`，**不碰 L3 = 零花钱**）

先挑 3 个**同一团队内、状态允许送审**的产品，记下它们的 id 与当前状态：

```powershell
docker compose exec db psql -U postgres -d erp_all -c `
  "SELECT id, status FROM app.product WHERE team_id = 1 ORDER BY id LIMIT 5;"
```

**贴回⑨**：这张表（**送审前的状态**，第 6 步要对照）。

用其中 3 个 id（下面用 `A,B,C` 占位，**替换成真实数字**），故意**写重一个**来同时验去重：

```powershell
$ids  = '[A,B,C,A]'          # 4 个元素、3 个不同 → 去重后 3
$key  = "verify-pr43-batch-1"
$body = "{""product_ids"":$ids,""levels"":[""l0"",""l2""]}"
$resp = Invoke-WebRequest -Method Post -Uri "http://127.0.0.1/api/v1/products/audit" `
        -Headers ($H + @{ 'Idempotency-Key' = $key }) -ContentType "application/json" `
        -Body $body -SkipHttpErrorCheck
$resp.StatusCode
$resp.Content
```

**贴回⑩**：状态码 + 完整响应体。
**判据**：
- 状态 **200**；
- `audited + skipped + failed + remaining` 四桶**元素总数 == 3**（去重后的入参数），
  且四桶里出现的 id **并集 == {A,B,C}、两两不相交**——用户勾了几件，响应必须对几件有交代，一件不吞；
- 不要求某件必须落在哪个桶（`skipped` 也是正常结果，例如该品已被判过）。

### 4.3 幂等重放（防重复扣费的承重件）

**用同一个 key、同一个 body 再发一次**：

```powershell
$resp2 = Invoke-WebRequest -Method Post -Uri "http://127.0.0.1/api/v1/products/audit" `
         -Headers ($H + @{ 'Idempotency-Key' = $key }) -ContentType "application/json" `
         -Body $body -SkipHttpErrorCheck
$resp2.StatusCode
"SAME_BODY=$($resp2.Content -eq $resp.Content)"
```

再数一次审核运行行数（**重放不得新增**）：

```powershell
docker compose exec db psql -U postgres -d erp_all -c `
  "SELECT trigger_kind, count(*) FROM app.audit_run WHERE created_at > now() - interval '30 min' GROUP BY 1;"
```

**贴回⑪**：`$resp2.StatusCode`、`SAME_BODY`、以及这张计数表（**跑第二次之后**）。
**判据**：状态 **200**、`SAME_BODY=True`、`audit_run` 里 `trigger_kind='batch'` 的行数
**与 4.2 之后相同**（重放取回已存响应，不新建 run、不重复花钱）。

### 4.4 成本切片（应为 0）

```powershell
docker compose exec db psql -U postgres -d erp_all -c `
  "SELECT count(*) AS calls, coalesce(sum(cost_usd),0) AS cost FROM app.llm_usage_log WHERE created_at > now() - interval '30 min';"
```

**贴回⑫**：这一行。
**判据**：**`calls=0` 且 `cost=0`**——B 段全程没传 `l3`，一分钱都不该花。
**若非 0：停下贴回**，说明 levels 没被尊重，那是缺陷。

---

## 第 5 步（C 段·**花真钱**，Owner 未放行就跳过）

UI 不传 `levels` → 后端默认 `["l0","l2","l3"]` → **L3 会真的调 LLM**。

**只勾 2 件产品**（不要更多），走 `/products` 页面：勾选 → 「批量送审」→ 确认。

**判据**：
- 弹窗如实列出将送审的件数；
- 提交后有结果反馈，四桶数字之和 == 勾选件数；
- 抽查一件，其审核详情能打开、能看到本次 run。

再看成本切片（同 4.4 的 SQL）：**`calls > 0`、`cost > 0`**，且：

```powershell
docker compose exec db psql -U postgres -d erp_all -c `
  "SELECT trigger_kind, count(*) FROM app.audit_run WHERE created_at > now() - interval '30 min' GROUP BY 1;"
```

**判据**：出现 `trigger_kind='batch'` 的行——这是「批量送审的钱能按入口切片归因」的现场证据。

**贴回⑬**：UI 三条判据的「过/不过」+ 两张表。

---

## 第 6 步：收尾（切回 main，**不要 downgrade**）

```powershell
docker compose exec db psql -U postgres -d erp_all -c `
  "SELECT id, status FROM app.product WHERE id IN (A,B,C) ORDER BY id;"
```

**贴回⑭**：这张表（与⑨对照）。产品状态由 `auditing`/`audit_passed`/`needs_review` 变化**是预期的**
——那正是被验的功能，不需要还原。**审计三族（`audit_log`/`audit_run`/`audit_hit`）一行都不要删。**

```powershell
cd <ERP-ALL 仓库根>
git checkout main
git pull origin main
cd infra
docker compose up -d --build
echo "BACK_EXIT=$LASTEXITCODE"
docker compose ps -a
```

**贴回⑮**：`BACK_EXIT`（期望 `0`）+ `ps -a`。
**再次强调：不要跑 `alembic downgrade`。** 本单零迁移，DB 版本本来就是 main 认的那个；
降级只会把 main 需要的东西删掉。

---

## 回执格式

按 ①–⑮ 编号逐条贴回。每条写明「过 / 不过」，不过的**停在那一步**并贴完整报错。
若 C 段未获放行，在⑬处写「C 段未执行（待 Owner 放行）」。
