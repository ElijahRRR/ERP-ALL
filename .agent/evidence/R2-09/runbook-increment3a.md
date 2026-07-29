# Runbook：批量送审（R2-09 增量3a，纯手工路径）

> 覆盖端点 `POST /api/v1/products/audit`（tag=Audit，权限点 `audit.run`）。
> **本端点不读 `automation_policy`、不调 `resolve_mode`、不接三档、不起 beat**——
> 三档接线是增量3b 的事。看到「自动化档位」相关现象与本端点无关，别在这里查。
>
> 契约：`specs/002-api-contract/openapi-v0.yaml` → `AuditBatchResult`
> 实现：`backend/src/erp/audit/batch.py`（执行体）、`backend/src/erp/audit/router.py`（闸序）
> 判据：`backend/tests/db/test_audit_batch.py`、`frontend/e2e/products-batch.mjs`

---

## 0. 先记住这四个桶的代价差别（回执与排障都按它读）

| 桶 | 含义 | 花钱了吗 | 产品状态 | 该做什么 |
|---|---|---|---|---|
| `audited[]` | 产生了 `audit_run`（含 `verdict=needs_review`） | **可能花了** | 已终局（`audit_passed`/`audit_rejected`/`needs_review`） | 正常结果，无需处置 |
| `skipped[]` | 开审**之前**就被判掉，没建 run | **没有** | 未变 | 修数据/换状态后再送 |
| `failed[]` | 已开审、中途异常中断 | **可能花了** | **停在 `auditing`** | 见 §4（需运维介入） |
| `remaining[]` | 墙钟预算耗尽或 provider 熔断，**根本没轮到** | **没有** | 未变 | 原样续跑（§2） |

**结构不变量**：`len(audited)+len(skipped)+len(failed)+len(remaining)` 恒等于去重后的入参条数，
四桶 id 并集 == 去重入参、两两不交。前端会当场对拍并在不平时弹红字「对账不平」——
**看到这句就是服务端有 id 下落不明，截图连时间报开发，不要让运营自己重发**。

---

## 1. 成本观测：按 `trigger_kind='batch'` 切片

批量与单品走**同一段**编排（`audit/service.py::audit_one` 一行未改），落库的表完全相同。
唯一的区分列是 `audit_run.trigger_kind`，批量恒为 `'batch'`（服务端强制写，请求体
**不接受**该字段，所以这一列不会被客户端伪装）。

```sql
-- 今日批量送审的花费与件数（按团队）
SELECT team_id,
       count(*)                       AS runs,
       count(*) FILTER (WHERE verdict = 'pass')         AS passed,
       count(*) FILTER (WHERE verdict = 'reject')       AS rejected,
       count(*) FILTER (WHERE verdict = 'needs_review') AS needs_review,
       round(sum(llm_cost_usd)::numeric, 6)             AS cost_usd,
       round(avg(cache_hit_rate)::numeric, 3)           AS avg_cache_hit
FROM app.audit_run
WHERE trigger_kind = 'batch'
  AND created_at >= date_trunc('day', now())
GROUP BY team_id ORDER BY cost_usd DESC;

-- 批量 vs 单品的成本对比（同口径，验证「批量没有额外开销」）
SELECT trigger_kind, count(*), round(sum(llm_cost_usd)::numeric, 6) AS cost_usd
FROM app.audit_run
WHERE created_at >= now() - interval '7 days'
GROUP BY trigger_kind ORDER BY cost_usd DESC;
```

**回执上的 `total_llm_cost_usd` == 本批 `audited[]` 各条 `llm_cost_usd` 之和**，
与上面 SQL 按 `run_id` 求和可对拍（T3 钉住了这条）。若运营报的数与库对不上，
先确认他看的不是**重放**回执（重放页顶有蓝色提示条，那一次一分钱没花）。

### 谁在什么时候送了哪一批

批量的审计是**一批一条**（不是逐条），`action='audit.run_batch'`：

```sql
SELECT id, created_at, actor_id, after
FROM app.audit_log
WHERE action = 'audit.run_batch' AND team_id = :team
ORDER BY id DESC LIMIT 50;
```

`after` 里有四桶计数、`idempotent_replay`（true = 那一次是重放，没跑没花钱），
以及 **`run_ids`**——拿它回查逐品：

```sql
-- 某一批具体审了哪些产品
SELECT r.id AS run_id, r.product_id, p.master_sku, r.verdict, r.llm_cost_usd
FROM app.audit_run r JOIN app.product p ON p.id = r.product_id
WHERE r.id = ANY (ARRAY[...把 after->'run_ids' 粘进来...]);

-- 反过来：产品 X 是被谁、在哪一批送审的
SELECT l.id, l.created_at, l.actor_id
FROM app.audit_log l
WHERE l.action = 'audit.run_batch'
  AND l.after -> 'run_ids' @> to_jsonb(ARRAY[<产品 X 的 run_id>]);
```

> `after` **不存 `product_ids`**：入参无数量上限（Owner 2026-07-28 裁定），
> 几万个 id 会把这一行 jsonb 撑爆；`run_ids` 天然受墙钟预算约束（跑得完才有 run）。

---

## 2. `remaining[]` 续跑

`remaining` 是**裸 id 数组、保序**，语义是「本次没开审、没花钱、状态没动，原样重发即可」。

- **运营侧**：回执右下角有「继续送审剩余 N 件」按钮，点它即用 `remaining` 作为下一批
  的 `product_ids` 再送一次。**载荷变了 → 幂等键自动变 → 那是一次真实的新执行**（不是重放）。
- **脚本侧**：把上一次响应的 `remaining` 原样当作下一次的 `product_ids`，
  **Idempotency-Key 必须换新**（载荷不同，沿用旧键会撞 `IDEMPOTENCY_CONFLICT`）。

出现 `remaining` 只有两个原因，先分清再决定要不要立刻续跑：

| 现象 | 原因 | 处置 |
|---|---|---|
| `remaining` 多、`audited` 里有正常判定 | 墙钟预算耗尽（跑太久） | 直接续跑，正常现象 |
| `remaining` 多、`audited` 末尾恰好 3 条连续 `needs_review` | **provider 熔断跳闸** | 先按 §5 排查 provider，**不要盲目续跑**——provider 没恢复时续跑只会再刷 3 条垃圾 needs_review |

---

## 3. 网关 504：用同一个键把结果取回来

**这是本端点最常见的现象，不是故障。** 本端点是同步的，真实上界 =
墙钟预算 90s + 单条最坏 240s = **330s**，而 `frontend/nginx.conf` 的
`proxy_read_timeout` 是 120s ——**跑久了网关必然先掐断**，返回一个不带 002 错误信封
的 HTML 504 页；此时**后端仍在跑、钱仍在花**。

前端已经处理好了：504 归入 `NO_RESULT` 档，提示「原样重试同一批」，且幂等键**稳定**
（`stableIdemKey`，按载荷 + sessionStorage nonce 生成），重试自动带同一个键。

运营只需照提示做：**不要改动勾选，等一会儿再点一次同一批**。会遇到两种结果：

| 再点的结果 | 含义 | 处置 |
|---|---|---|
| 409 `IDEMPOTENCY_IN_PROGRESS` | 上一次**还在跑** | 再等 1~2 分钟，仍点同一批 |
| 200 且顶部有「这是上一次同批请求的结果重放」 | 上一次**已跑完**，这是那一次的结果 | 正常收工，一分钱没重花 |

**绝对不要教运营「换一批再试」或「刷新页面重新勾」**——换了载荷就是新的一批，
会真的重跑、真的付第二遍钱。

### 命令行侧把结果取回来

```bash
# 用**同一个** Idempotency-Key 重发同一个载荷即可；服务端 24h 内存有该键的响应
curl -sS -X POST http://<host>/api/v1/products/audit \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -H "Idempotency-Key: $SAME_KEY" \
  -d '{"product_ids": [ ...与上次逐字相同... ]}'
```

```sql
-- 查这个键当前是「处理中」还是「已有结果」
SELECT id, created_at, status_code, (response IS NOT NULL) AS has_result
FROM app.api_idempotency
WHERE endpoint = 'POST /products/audit' AND idem_key = :key;
```
`has_result = false` → 还在跑（或已崩，见下）；`true` → 直接读 `response` 就是那一批的完整回执。

> **幂等占位失效阈值 = 30 分钟**（`_BATCH_AUDIT_STALE_MINUTES`），必须大于 330s。
> 这条不变量由 `test_audit_batch.py::T11` 从 `audit/llm.py` 的
> `HTTP_TIMEOUT_SECONDS × MAX_ATTEMPTS` 派生断言——**调大 provider 超时或重试次数时，
> 这个阈值要同步调大，否则同键重试会重占占位并发双跑（两批同时审同一组品、钱付两遍）**。

### 运营端还想「真的重审一遍」怎么办

重放回执顶部的蓝条上有 **「重新审一遍（会计费）」** 按钮：点它作废本会话的 nonce，
下一次确认就是一次真实执行。**不点它的话，同一批在同一会话内会一直命中重放**——
这是刻意的默认（安全侧），但政策更新后要重跑就必须显式点这一下。

---

## 4. `auditing` 遗孤：怎么查、怎么自愈

**成因**：`audit_one` 是三段式（tx1 落 run + 置 `auditing` → HTTP 调 LLM → tx2 写终局）。
tx1 与 tx2 之间进程被杀 / DB 断连 / 其它非业务异常 → 该条落进 `failed[]`，
**产品停在 `auditing`、`audit_run` 悬挂 `status='running'`**。批量把这个既有形状的
发生机会从「一次一件」放大到「一批 N 件」。

### 查

```sql
-- 悬挂超过 10 分钟的遗孤（正常一条最多跑 240s，超 10 分钟必是遗孤）
SELECT r.id AS run_id, r.product_id, p.master_sku, p.status AS product_status,
       r.trigger_kind, r.started_at, now() - r.started_at AS stuck_for, r.created_by
FROM app.audit_run r JOIN app.product p ON p.id = r.product_id
WHERE r.status = 'running' AND r.started_at < now() - interval '10 min'
ORDER BY r.started_at;

-- 只看「产品卡在 auditing」这一面（有的品 run 已被清扫但状态没回来）
SELECT id, master_sku, status, updated_at, latest_audit_run_id
FROM app.product
WHERE status = 'auditing' AND updated_at < now() - interval '10 min'
ORDER BY updated_at;
```

### 自愈到什么程度（**先看清楚，别以为它会自己全好**）

- **`audit_run` 会自愈**：下一次对**同一件**产品发起送审时，tx1 的懒清扫会把
  `started_at` 超过 `_STALE_RUNNING_MINUTES`（10 分钟）的 `running` 判为 `failed`
  （`audit/service.py`）。**没有常驻 beat 做这件事**——不送审就不清扫。
- **`product.status` 不会自己回来**：懒清扫只动 `audit_run`。产品会一直停在 `auditing`，
  而 `auditing` **不在**前端的可送审状态集合里（`frontend/src/pages/products/labels.ts`
  的 `AUDIT_ELIGIBLE_STATUSES`），所以**运营点不到「重审」，也勾不上批量**——
  等于这件品卡死，需要运维介入。

### 处置（运维执行，两步）

后端本身**允许**对 `auditing` 的产品送审（`service.py` 只硬闸 `listed`/`retired`），
所以只要把状态拨回一个可送审态，链路就自己接上了。

```sql
-- 步骤 1：确认这些品确实是遗孤（run 已 failed 或悬挂 > 10min），不要动正在跑的批量
SELECT p.id, p.master_sku, p.status, r.id AS run_id, r.status AS run_status, r.started_at
FROM app.product p
LEFT JOIN app.audit_run r ON r.id = p.latest_audit_run_id
WHERE p.status = 'auditing' AND p.updated_at < now() - interval '10 min';

-- 步骤 2：把遗孤拨回上一个可送审态（**逐个核对后再执行，不要无 WHERE 批量刷**）
--   没审过的品 → 'ingested'；此前有终局 run 的 → 按那条 run 的 verdict 回填。
BEGIN;
UPDATE app.product SET status = 'ingested', updated_at = now()
WHERE id = ANY (ARRAY[<逐个核对过的 id>]) AND status = 'auditing';
-- 核对影响行数与预期一致再 COMMIT
COMMIT;
```

> ⚠️ **别在批量正在跑的时候动**：正常批量执行期间产品也短暂处于 `auditing`。
> 判据是「`updated_at` 早于 10 分钟前」+「对应 run 不是刚起的」，两条都满足才是遗孤。
>
> 前端 `AUDIT_ITEM_FAILED` 的提示语已改为「请把产品 ID 报运维按 runbook 处置」，
> 不再说「单独重试它即可」——那句话在 UI 上做不到（2026-07-28 审查 F2）。

---

## 5. provider 熔断跳闸后怎么排查

**跳闸判据（故意写得很窄）**：连续 `_BREAKER_CONSECUTIVE = 3` 条 `needs_review`，
**且这 3 条 run 都带 `rule_code='llm_unavailable'` 的 `audit_hit`** 才跳。
非连续不跳；`l3_policy_missing`（本地策略缺失）这类**真判定**不跳——那是配置问题，
跟 provider 死活无关，误熔断会把一批本该跑完的产品截在半路。

跳闸后：剩余全进 `remaining`（没花钱、状态没动），本次请求照常 200 返回。

### 第一步：确认是 provider 还是配置

```sql
-- 最近 30 分钟落 needs_review 的 run，按命中码分类
SELECT h.rule_code, count(*) AS n, max(r.created_at) AS last_seen
FROM app.audit_run r JOIN app.audit_hit h ON h.run_id = r.id
WHERE r.verdict = 'needs_review' AND r.created_at > now() - interval '30 min'
GROUP BY h.rule_code ORDER BY n DESC;
```

| 主导码 | 结论 | 下一步 |
|---|---|---|
| `llm_unavailable` | **provider 侧**（超时 / 非 200 / 响应缺字段 / 缺 API Key） | 走下面第二步 |
| `l3_policy_missing` | **本地配置**：`audit_policy` 里 `l3_intellectual_property` 不存在或被禁 | 查该策略行，与 provider 无关（这类**不会**触发熔断） |
| `llm_needs_review` | provider 通了但模型输出坏（解析失败/非法 verdict） | 看模型与 prompt，不是可用性问题 |

### 第二步：看具体错因

```sql
-- llm_unavailable 的 evidence 里有 error 码与前 300 字原文
SELECT r.id AS run_id, r.created_at, h.evidence ->> 'error' AS err,
       left(h.evidence ->> 'detail', 200) AS detail
FROM app.audit_hit h JOIN app.audit_run r ON r.id = h.run_id
WHERE h.rule_code = 'llm_unavailable' AND r.created_at > now() - interval '30 min'
ORDER BY r.id DESC LIMIT 20;
```

| `error` | 含义 | 处置 |
|---|---|---|
| `LLM_KEY_MISSING` | 没配 `ERP_LLM_API_KEY` | 配置中心/环境变量补上并重启 api |
| `LLM_CALL_FAILED` | provider 返回非 200（`detail` 里有 HTTP 码与响应体片段） | 按码判：401/403=凭证，429=限流，5xx=对方故障 |
| `LLM_EMPTY_RESPONSE` | 连续两次空响应（已内建重试一次） | provider 抖动，稍后重试 |
| `ReadTimeout` / `ConnectTimeout` 一类 | 网络或对方挂死 | 查出网链路；此形状下墙钟预算会先刹车 |

日志侧（结构化 JSON，按事件名过滤）：

- `audit.l3_unavailable` —— 每条落 `llm_unavailable` 时写，带 `run_id` 与 `error`
- `audit.batch.breaker_probe_failed` —— **熔断判定查询自己失败了**（DB/连接池问题，
  不是 provider 问题）。此时代码保守地**不跳闸、继续跑**，只留这条告警
- `audit.batch.item_failed` —— 进 `failed[]` 的那条，**原始异常文本只在这里**
  （回执给运营的 message 只含异常类名 + 固定人话，因为 DB 异常的原文常含 SQL/连接参数）
- `audit.batch.unexpected_business_error` —— 白名单外的业务异常被归进 `failed`
- `audit.batch.unknown_verdict` —— 出现了 pass/reject/needs_review 之外的 verdict（上游改口径了）

### 第三步：恢复后续跑

provider 恢复后，把上一次回执里的 `remaining` 原样再送一批即可（§2）。
**跳闸时已经落库的那 3 条 `needs_review` 不会自动重跑**——它们是真实结果（fail-closed
转人工复核），要重审就在产品页对这几件重新送审。

---

## 6. 整批 4xx 的四种码（都在开审之前，零金钱代价）

| HTTP | code | 含义 | 处置 |
|---|---|---|---|
| 422 | `AUDIT_TEAM_REQUIRED` | 超管没带 `X-Act-Team` | 右上角切到具体团队再送 |
| 422 | `AUDIT_LEVELS_INVALID` | `levels` 含 `l0..l4` 之外的值（含大小写写错，如 `L0`） | 客户端/脚本传错。**这道闸很重要**：没有它，`["L0"]` 会让每层判定都不命中而 verdict 保持初值 `pass`，整批产品零检查落 `audit_passed` |
| 422 | `AUDIT_L4_DISABLED` | `levels` 含 `l4` | R2 阶段只跑 L0/L2/L3 |
| 409 | `IDEMPOTENCY_IN_PROGRESS` / `IDEMPOTENCY_CONFLICT` | 上一次同键还在跑 / 同键被不同载荷用过 | 前者等，后者换键 |

除这四类外**一律逐条进桶、HTTP 恒 200**。原因：每条自己 COMMIT 两次，第 N 条出事时
前 N-1 条已落库、钱已花，而审核域没有 DELETE 授权、补偿路径不存在——此时返回 4xx
就是「响应说失败、库里成功了 N-1 条」的谎，运维照响应重发就会付第二遍钱。

> 另有一类**不到业务层**的 422：缺 `product_ids`、元素非整数、缺 `Idempotency-Key` 头，
> 由 FastAPI 请求校验直接拒，走默认 `{"detail": [...]}` 信封（**无 `error.code`**）。
> 前端会把它归进 `NO_RESULT` 兜底档并提示「后端可能仍在跑」——对这类请求那句提示是
> 多余的（它根本没到达业务层），但触发面只有非常规客户端，不影响运营。

---

## 7. 已知不做的事（登记，不是遗漏）

- **UI 单次上限事实性为 20 件**：端点无 `maxItems`（Owner 裁定），但产品页 `size=20`
  且切页清空勾选，所以界面一次最多勾 20 件。要一次送更多，走脚本直调端点。
  这也意味着**墙钟预算（90s）在 UI 路径上几乎永不触发**。
- **请求级事务在整批期间保持打开**：`get_session` 是一请求一事务，而 `authn` 已在其上
  跑过 SQL，故连接从请求开始就被占住，直到批量跑完（最坏 330s）才释放。
  连接池默认 5 + overflow 10，约 15 个并发批量即打满。根因在 `authn` 的请求级事务形状
  （单品端点同形），不是本端点能单独修的，另开单。**症状**：并发批量多时逐条落
  `failed / AUDIT_ITEM_FAILED`（`ctx_tx` 取不到连接）。
- **巨量 id 的三步不受墙钟约束**：去重、预检 `id = ANY(:ids)`、`remaining` 物化与序列化。
  实际上限由 nginx 默认 `client_max_body_size 1MB`（≈13 万个 id）兜着。
  若将来出现「提交巨量 id 把 API 拖住」，根因在这三步而不在 LLM。
