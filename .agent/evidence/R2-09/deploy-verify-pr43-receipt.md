# PR #43 第三闸真机验证回执（R2-09 增量3a 批量送审端点）

> **结论：通过。** 部署机在 Win11 部署机执行 `.agent/evidence/R2-09/deploy-verify-pr43.md`
> 的全部步骤，逐条判据均满足。本文是该次验证的取证留档，供审计与后续追溯。
>
> - 验证的代码产物：**`ba3cbdd`**（此后六个 commit 全是 `.agent/` 文档修订，未重建镜像）
> - 指令版本：**`b1b0782`**（v5）
> - CI：`b1b0782` 上四个 job 全绿

---

## 一、逐条判据结果

| 步骤 | 内容 | 结果 |
|---|---|---|
| ①–② | 前置锚点 / 切分支自校验（含 `ALEMBIC_DIFF_EMPTY=True`） | 过 |
| ③ | 起服务，**migrate 无 `Running upgrade`**（本单零迁移） | 过 |
| ④ | 产物对拍：镜像 `index-Bc88dF-S.js` == 浏览器同名 | 过 |
| ⑤ | 内网非安全上下文自证 | 过 |
| ⑥ | 内网 IP 写路径 | 过 |
| ⑦ | 团队管理员账号（非超管） | 过 |
| ⑧ | 四条入参校验 | 过 |
| ⑨ | 送审前产品状态 | 过 |
| ⑩ | 4.2 四桶结构不变量 | 过 |
| ⑪ | 4.3 幂等重放 | 过 |
| ⑫ | 4.4 成本切片（B 段应为 0） | 过 |
| ⑬ | C 段 UI 真链路（付费） | 过 |
| ⑭ | B 段产品终态 | 过 |
| ⑮ | 收尾切回 main | 过 |

### ⑤ 内网非安全上下文（本轮新增的核心判据）

```
["http://192.168.3.26:5173", false, "undefined", "function"]
```

`isSecureContext === false` 且 `typeof crypto.randomUUID === "undefined"`——**先证明该页面
确实处在非安全上下文，判据才算数**。这一条是判据的自我否定条款：若该页面是 localhost，
`randomUUID` 可用，整步等于没验。

### ⑥ 内网 IP 写路径

`POST /api/v1/notifications/read-all` → **HTTP 204**，页面无报错，请求带 `Idempotency-Key`。

**这条验的是本 PR 顺带修掉的全站缺陷**：`main` 的 `client.ts:129` 用
`crypto.randomUUID()` 生成幂等头，而该 API 只在安全上下文存在，`frontend/nginx.conf`
只 `listen 80`——经 `http://内网IP` 访问时 main 上每一个 `api.post` 都会抛 TypeError、
请求根本发不出去。**该缺陷用 localhost 验证在原理上抓不到。**

### ⑧ 四条入参校验（零金钱代价）

```
CASE_L0     = 422  code=AUDIT_LEVELS_INVALID
CASE_L4     = 422  code=AUDIT_L4_DISABLED
CASE_EMPTY  = 200  audited=[] skipped=[] failed=[] remaining=[]
CASE_NO_KEY = 422  loc=["header","Idempotency-Key"]
```

第一条是本单修掉的合规闸绕过的现场复验：修之前 `levels=["L0"]`（大小写笔误）会让
L0/L2/L3 一个分支都不命中，`verdict` 保持初值 `pass` 直接落库，产品当场
`audit_passed`、零命中、cost=0。

### ⑩ 4.2 四桶结构不变量（键 `verify-pr43-batch-5`）

入参 `2421/2422/2423` 含一次重复 → 服务端去重为 3 件。

```
FIRST_STATUS=200
audited=3  skipped=0  failed=0  remaining=0
run_id = 2457 / 2458 / 2459
verdict_counts = {pass:3, reject:0, needs_review:0}
total_llm_cost_usd = 0.0
```

四桶合计 3 == 去重后入参数，并集完整、两两不交。

### ⑪ 4.3 幂等重放（防重复扣费的承重件）

```
REPLAY_STATUS=200
FIRST_HAS_REPLAY_FLAG=False      重放标记只在重放侧出现
REPLAY_FLAG=True
AUDITED_SAME=True                逐字段比较，DIFF 行 0
COUNTS_SAME=True
COST_SAME=True
COST_TYPE=Decimal                数值型（非 String）
ITEM_COST_TYPE=Decimal
audit_run(batch)：首次后 9 → 重放后 9   未新增
```

**`AUDITED_SAME` 才是区分「取回存储响应」与「又跑了一遍」的判据**——行数不增单独看
不够：这批品已 `audit_passed`，若真重跑一遍会全部落进 `skipped` 桶而同样不新建 run。

### ⑫ 4.4 成本切片（按 run id 精确圈定）

`object_id IN (2457,2458,2459)` → **`calls=0`，`cost=0`**。

B 段全程显式传 `levels:["l0","l2"]`，L0/L2 是纯规则/查库、不出网。**若非 0 即说明
`levels` 没被尊重**——这条是缺陷判据，不只是省钱。

### ⑬ C 段 UI 真链路（Owner 授权，只勾 2 件，**真实付费**）

产品 `2481 / 2482`。UI 三条判据全过（弹窗如实显示「本批 2 件」；四桶合计 2；
审核详情可打开，抽查 2482 见 run #2461、判定通过、成本 $0.001204）。

| run_id | product_id | trigger_kind | llm_cost_usd |
|---:|---:|---|---:|
| 2461 | 2482 | batch | 0.001204 |
| 2460 | 2481 | batch | 0.000000 |

费用切片（run 2460/2461）：**`calls=1`，`cost=0.001204`**。**本轮真实花费 ≈ $0.0012。**

> **`calls=1` 而非 2 是正确的，不是漏账。** UI 结果是「通过 1 / 拒绝 1」，而
> `service.audit_one` 里 L0 是确定性硬判断、命中即短路，L3 只在 `verdict == "pass"`
> 时才跑——**被 L0 拒的那件根本没到 L3**，故不产生 LLM 调用。
> 若是缓存命中则会记一行 `cache_hit=true, cost=0`，`calls` 就该是 2；**`calls=1`
> 恰好排除了那种解释**。回执内部自洽，并顺带证明 L0 短路确实省钱。

`trigger_kind` 两行均为 `batch`——服务端恒写该值，「批量送审的钱能按入口切片归因」
在真机上成立。

### ⑮ 收尾

- **未执行任何 `alembic downgrade`**（本单零迁移，降级反而会破坏 main 需要的结构）
- 已切回并对齐 `main@9c567a5`
- 全栈重建退出码 0；migrate `Exited (0)`；api/beat/frontend `Up`；db/redis `Up (healthy)`
- `healthz` → `{"status":"ok","version":"0.1.0"}`
- 未改码、未 push、未 merge

---

## 二、本次验证的过程教训（写下来是因为它会重演）

**第三闸共开了六轮，六次阻断全部是验证指令自身的错误，产品零缺陷。**

| 轮 | 指令写的 | 事实 |
|---|---|---|
| 1 | `Try-Post` 不带 `Idempotency-Key` | 契约 002 必填头，FastAPI 在业务层之前拒，三条用例一条也没到达被测判定 |
| 2 | 空列表期望 422 | `AuditBatchIn` 头注承诺 200 + 四空桶，`test_t20_*` 钉死 |
| 3 | `SAME_BODY=($resp2.Content -eq $resp.Content)` 要求 True | `idempotency.py:112` 是 `{**existing.response, "idempotent_replay": True}`——**重放必然多一个字段，该等式设计上不可能为真** |
| 4 | `llm_usage_log.created_at` | 该列不存在，真实列是 `occurred_at`（且是分区键） |
| 5 | 解析后又 `ConvertTo-Json` 比字符串 | 与 v3 自己写下的「必须比解析后的 JSON」直接矛盾；且 Double/Decimal 字符串化后 `0` vs `0.0` 是独立的第二发 |
| 6 | `$A = @($a.audited)` | **PowerShell 变量名不区分大小写**，`$A` 覆盖 `$a`，症状 `COST_TYPE=Object[]` |

**共同点**：这份指令的每一条判据都是手写的，**没有任何东西验证过判据自身是否成立**。
真正的判据在 CI 里（T9/T20/契约门禁）——那些跑得起来；而这份 PowerShell 是一份
**从未被执行过的测试脚本**，却挡在 Owner 的合并授权之前。

**已落地的两道机器检查**（每识别出一个失败类别就机器化一个，而不是承诺「下次更仔细」——
前三轮反复承诺过，已被证伪）：

1. **SQL 列名校验**：起本地库 `alembic upgrade head`，抽出文中全部 SQL 对
   `information_schema.columns` 逐列核验（当时 6/6）。
2. **`psvarcheck.py`**：扫描全部 powershell 代码块，报告大小写碰撞的变量名（15 块全 OK）。
   写它时第一版把注释里提到的变量名也算成变量，加剥注释才干净——**检查器本身也需要被检查**。

**仍然敞开的口子**：这段 PowerShell 无法在云端沙箱执行（`pwsh` 不可用），语法级错误
仍可能漏。缓解手段是让它失败得可诊断——4.3 失配时打印
`DIFF item[i].field : 'x' vs 'y'`。

**已提请审计侧排期的独立欠账**：部署验证脚本需要一个能在 CI 里空跑一遍语法与判据逻辑的
机制，否则每张单都会重演这六轮。本单不做（属工具链变更）。

**另记一笔：部署机六次全部正确停手**，每次都把根因定位到「验证指令与实际不符」而不是
含糊报个失败，第五轮还直接给出了修法建议。它是这条链上最可靠的一环。
