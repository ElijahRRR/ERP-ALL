# R2-08 财务域考古（阶段一：闸门核实 + 现状基线 + 渠道供给）

> **状态**：阶段一完成（2026-07-27，R2-12 三日连测墙钟等待期间）。只读，未改任何实现代码，未立项。
> **阶段二待做**：旧仓 `fetch_walmart_settlement.py` 全文逐段语义（820 行，本轮只读了端点与表结构）、
> `settlement_snapshots` 全字段与 headline 平账口径、旧 `store_kpi_snapshots`/`payout_accounts`、
> 前端 `erp-core/handoff-design/project/src/pages-finance.jsx` 的既有形态、契约与权限点缺口。

---

## 0 结论速览

| # | 结论 | 性质 |
|---|---|---|
| 1 | **工单写的「硬前置：等图纸」早已解除**——图纸 `421f83d`（2026-07-17）已按 immutable event ledger 修订完毕，commit 标题就叫「R2-08 闸门解除」，台账停在 07-16 未回写 | **台账失实**，已修 |
| 2 | 财务域**代码与表全仓为零**，真空地；`financial_event`/`ledger_entry`/`profit_ledger`/`settlement` 在 `backend/src/` 与全部 39 个迁移中零命中 | 现状核实 |
| 3 | 图纸提议的幂等自然键与渠道**实际提供的字段对不上**——这是「重拉不二次过账」这条硬保证的地基 | **待裁，阻塞增量1** |
| 4 | 旧仓实际用的两个 recon 端点**未列入官方限额表**，只能按同族类推 | 风险登记 |
| 5 | D-Q56 原文说「R4/R5 建域前改图纸」，而 007 把财务域排成 R2-08——顺序错位，图纸已按 R2-08 口径改了，此处只作记录 | 口径注记 |

---

## 1 闸门核实：工单说「等图纸」，图纸十天前就改完了

`review_list.json` 的 R2-08 条（`last_checked_at: 2026-07-16`）写：

> 硬前置：§08 图纸按 immutable event ledger 修订(D-Q56 裁定,审计工作区落笔)——**开发等图纸**

**实际**：`git log --follow specs/001-domain-model/08-finance.md` 头一条就是

```
421f83d docs(spec): §08 财务图纸 immutable event ledger 修订(D-Q56 C4)——R2-08 闸门解除
        2026-07-17 03:46:59 +0000
```

图纸正文 `08-finance.md:4` 亦自注「**D-Q56·评审 C4（2026-07-16 修订，审计工作区落笔，R2-08 建域前生效）**」。

**结论：R2-08 无前置阻塞，可立项。** 这是又一例「关账/解闸没回写 review_list」——与 2026-07-26 查出的 PR #29 那类同源（当时修的是 R2-11 accepted 却挂 in_progress 达 9 天）。本轮已修 R2-08 条并记入台账。

> 台账门禁（`tests/test_agent_ledger.py`）目前只校验结构不校验「与 specs 的一致性」，抓不到这类。
> 是否值得加一条「finding 里声称的前置与 specs 现状对拍」——**不建议**：那需要自然语言理解，
> 判据不可机械化，做出来必然是脆的。这类只能靠例行核对（如 2026-07-26 那次全面核对）兜。

## 2 现状基线：财务域是空地

```bash
grep -rln 'financial_event|ledger_entry|profit_ledger|settlement' backend/src/ backend/alembic/versions/
# → 零命中
ls backend/src/erp/    # aftersale audit automation catalog channel compliance core identity
                       # listing notify order pricing scrape tools —— 无 finance
```

- **无 `erp/finance` 模块**，无任何财务相关服务/路由/任务。
- **迁移链 0001~0039 无一张财务表**。图纸里 5 张表（`financial_event` / `ledger_entry` /
  `settlement_snapshot` / `settlement_line` / `profit_ledger` / `exchange_rate_log`）全部待建。
- 结论：R2-08 是**纯新建**，不是移植改造，也没有「现状与图纸不符」的历史包袱要清。
  （对照 R2-11 当初「图纸说列亦未建、实际 0007 已建全套 DDL」那种错位，此处不存在。）

## 3 ⚠️ 幂等自然键：图纸提议 vs 渠道实际供给，对不上

这是本阶段最要紧的发现，**直接阻塞增量1**。

### 图纸怎么定的（`08-finance.md:49-52`）

> **幂等协议**：`uq_financial_event (source_kind, source_ref, event_kind)`。
> `source_ref` 用**来源自然键**而非行 id——结算重拉产生新 snapshot 版本时，同一笔结算行
> （**`store:period:line_kind:order_no:sku:seq`**）不会二次过账。

### 渠道实际给什么（旧仓 `fetch_walmart_settlement.py:192` 的唯一键）

```sql
UNIQUE(store, report_date, transaction_key, amount_type)
```

`recon_details` 的交易核心字段：`transaction_type` / **`transaction_key`** / `transaction_desc` /
`transaction_reason_desc` / `transaction_posted_ts` / **`amount_type`** / `amount` / `total_payable`；
订单维度另有 `purchase_order` / `purchase_order_line` / `customer_order` / `customer_order_line`；
商品维度 `partner_item_id` / `partner_gtin`。

### 错位在哪

| 图纸提议的组成 | 渠道是否直接提供 | 说明 |
|---|---|---|
| `store` | ✅ | |
| `period` | ⚠️ 半 | 渠道给的是 `report_date`（报告日）与 `period_start/end`；「period」指哪个需定死 |
| `line_kind` | ❌ | 渠道给 `transaction_type` + `amount_type` 两个维度，图纸的 `line_kind`（sale/refund/fee/adjustment）是**我方归类**，不是渠道字段——归类规则本身要版本化，否则规则一改，同一行会算出不同 `source_ref`，幂等失效 |
| `order_no` | ✅ | `purchase_order` / `customer_order` 二选一（需定死用哪个） |
| `sku` | ⚠️ | `partner_item_id`，但**费用类行无 SKU**（图纸自己也写了「费用类无单」） |
| `seq` | ❌ | 渠道无此概念；旧仓靠 `transaction_key` 区分 |
| — | | **渠道真正的唯一标识是 `transaction_key` + `amount_type`**，图纸没提到这两个字段 |

**风险**：若照图纸字面实现 `source_ref`，会出现两类失效——
①`line_kind` 归类规则变更 → 同一结算行生成不同 `source_ref` → 重拉时**二次过账**（重复计收入）；
②费用类行 `sku`/`order_no` 为空 → 多条不同费用行拼出**相同** `source_ref` → 后到的被幂等键**静默吞掉**（少计费用）。
两个方向都是钱算错，且都不会报错。

**建议（待 Owner/规划侧裁）**：`source_ref` 直接采用渠道自然键
`store:report_date:transaction_key:amount_type`，与旧仓唯一键同构；`line_kind` 降级为**派生列**
（由 `transaction_type`+`amount_type` 按版本化规则映射，规则版本记在 `posting_rule_version` 上），
不参与幂等键。这样幂等只依赖渠道给的事实，不依赖我方可变的归类口径。

> 图纸 §「结构参考」本就写着「walmart_settlement.db 的 settlement_snapshots + recon_details
> ——**语义继承，字段按新模型规范化**」。本条即是把「语义继承」落到幂等键这一具体处。

## 4 渠道供给与限额（两条端点未列入官方表）

旧仓三条端点（`fetch_walmart_settlement.py:388-433`）：

| 端点 | 用途 | 官方限额表 |
|---|---|---|
| `GET /v3/report/payment/statement` | headline 结算摘要 | ✅ **15/min**（Payment Reports） |
| `GET /v3/report/reconreport/availableReconFiles?reportVersion=v1` | 列可用对账文件日期 | ❌ **未列** |
| `GET /v3/report/reconreport/reconFileJson?reportDate=…` | 对账明细 JSON | ❌ **未列** |

官方表里有同族的 `GET /v3/report/reconreport/reconFile` = **100/min**（非 JSON 变体）。
`reconFileJson` / `availableReconFiles` 只能按同族类推 100/min。

**这与 MP_MAINTENANCE 是同一类风险**（R2-12 已踩过）：`rate_limiter.py` 里给 MP_MAINTENANCE 配的
`10/3600` 就是从 MP_ITEM 实测值类推的，官方表没列。**处置口径应一致**——落 `rate_limiter.py`
时把这两条按 100/min 配上，并在注释里写明「类推值，非官方；真实上限未知」，
且**必须读响应头** `x-current-token-count` / `X-Next-Replenishment-Time` 做自适应退避。

> 另注：`/v3/report/payment/performance` 也是 15/min，日报 KPI 若要用得算进同一预算。

## 5 D-Q56 与 007 的顺序错位（记录，不阻塞）

D-Q56 原文（`DECISION-FORM.md:221`）：

> ④财务域按 immutable event ledger 设计（**R4/R5 建域前改图纸**）

而 007 把财务域排成 **R2-08**（R2 阶段）。图纸修订时已按 R2-08 口径落笔（`08-finance.md:4`
写的是「R2-08 建域前生效」，且 commit 标题写「R2-08 闸门解除」），**实质已对齐**，此处仅作
口径注记，避免将来有人拿 D-Q56 原文质疑「财务域为何提前到 R2」。

---

## 6 阶段二取证清单（下一轮做）

1. `fetch_walmart_settlement.py` 全文 820 行逐段——重点：增量拉取的断点续跑口径、
   `save_snapshot` 的 headline 字段全集（含二十余个 `adj_*` 列）、`--force` 全量重拉语义、
   `_to_float`/`_to_int` 的空值与脏值处理（图纸「headline 与明细求和不平 → 告警」需要它的口径）。
2. `settlement_snapshots` 全字段 → 图纸 `settlement_snapshot` 的列映射表，标出**旧有而图纸无**
   与**图纸有而旧无**两类缺口。
3. 旧 `store_kpi_snapshots` / `payout_accounts`（工单点名的日报 KPI 考古源）——**尚未定位到文件**，
   本轮 `find` 未命中，需在 erpAPI 仓与 T7 备份中继续找。
4. `erp-core/handoff-design/project/src/pages-finance.jsx`——既有前端形态，供 R2-08 页面参考
   （D-Q53 前端打磨是 Owner 触发制，但财务页的信息架构可先摸）。
5. 契约与权限点缺口：现有 `permission` 表**无任何 finance 模块权限码**（0002 的 perms 列表里
   `order`/`pricing`/`listing`/`catalog`/`channel`/`identity`/`compliance` 俱全，**无 finance**）——
   R2-08 需新增权限码，且按 2026-07-26 新上线的可达性门禁，**新增即须同时授给模板角色或声明超管专属**，
   否则 CI 直接红。这条是新门禁上线后第一个会撞上它的工单，正好验证门禁有效。

## 7 给立项的初步判断（非结论，供拆单参考）

- **量级**：图纸 5 张表 + 三层过账链 + 4 条验收补强（可复算 / 不可变实证 / 冲销演练 / 汇率审计），
  外加渠道拉取与对账工作台。粗估**接近 R2-12**，明显小于 R2-09（后者要接 9 条 flow）。
- **可拆点（初判）**：①结算文档层拉取入库（settlement_snapshot/line + 版本化重拉）
  ②事件层 + 幂等过账（含 §3 那条自然键裁定）③分录层 + 汇率块 + 过账规则版本化
  ④投影层 + rebuild_check 内建对账 ⑤对账工作台页 + 未匹配行人工核对 ⑥日报 KPI。
- **最该先裁的**：§3 的 `source_ref` 组成。它是增量2 的地基，且改起来是**破坏性**的
  （幂等键定错，已过账的事件要全量重算），不能边做边改。
