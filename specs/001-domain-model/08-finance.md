# 08 finance — 财务事件账 / 分录 / 结算文档 / 利润投影 / 汇率

> 决策依据：D-Q32（三粒度利润 + 采购方汇率即成本）、D-Q40（收款仅 PingPong 标签）、
> **D-Q56·评审 C4（2026-07-16 修订，审计工作区落笔，R2-08 建域前生效）**：
> "覆盖式月物化不是账"——权威记录 = 不可变 `financial_event` + `ledger_entry`
> 追加式两层，**利润是投影不是账**；汇率换算必须显式携带 base/quote/rate/来源/
> 锁定时刻/舍入规则。原版（直接物化 profit_ledger 为权威）作废。
> 结构参考：walmart_settlement.db 的 settlement_snapshots + recon_details（调研 §2.4）
> ——语义继承，字段按新模型规范化。

## 分层总览（本次修订的骨架）

```
结算文档层（channel 原始凭证，只进不改）
  settlement_snapshot / settlement_line
        │ 过账（posting，幂等）
        ▼
事件层 financial_event（append-only，权威事实）
        │ 过账规则（posting_rule_version）
        ▼
分录层 ledger_entry（append-only，规范化记账单元，含显式汇率块）
        │ 投影（可随时全量重建）
        ▼
投影层 profit_ledger（物化缓存，非权威）+ 报表
```

**不可变纪律**（沿 R1-03 audit_log 双重不可篡改同款）：`financial_event` 与
`ledger_entry` 对 `erp_app` 仅授 SELECT+INSERT（无 UPDATE/DELETE 授权）+
BEFORE UPDATE/DELETE 触发器 RAISE——**订正只能追加冲销事件**（见过账协议）。

## financial_event 财务事件（append-only，权威）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| event_kind | TEXT | NOT NULL CHECK IN (settlement_sale, settlement_refund, settlement_fee, settlement_adjustment, purchase_cost, purchase_freight, manual_adjustment, reversal) | |
| occurred_at | timestamptz | NOT NULL | 业务发生时刻（结算行=行日期；采购=锁定时刻） |
| amount | NUMERIC(14,4) | NOT NULL | 有向原币金额（退款/费用为负） |
| currency | CHAR(3) | NOT NULL | 原币 |
| source_kind | TEXT | NOT NULL CHECK IN (settlement_line, procurement_order, manual) | 溯源 |
| source_ref | TEXT | NOT NULL | 来源自然键（见幂等协议） |
| store_id | BIGINT | NULL | |
| order_id / master_sku | BIGINT/TEXT | NULL | 归因维度（可空，费用类无单） |
| reverses_event_id | BIGINT | NULL REFERENCES financial_event | 仅 event_kind=reversal 时非空 |
| note | TEXT | NULL | 冲销/手工事件必填原因 |
| created_by / created_at | | | 手工事件必填 created_by |

**幂等协议**：`uq_financial_event (source_kind, source_ref, event_kind)`。
`source_ref` 用**来源自然键**而非行 id——结算重拉产生新 snapshot 版本时，同一笔
结算行（`store:period:line_kind:order_no:sku:seq`）不会二次过账；重拉后金额有出入
→ 生成 `reversal`（冲原事件）+ 新事件，差异走 notification（不静默改）。
索引：`(team_id, occurred_at)`、`(source_kind, source_ref)`、`(reverses_event_id) WHERE reverses_event_id IS NOT NULL`。

## ledger_entry 分录（append-only，规范化记账单元）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| event_id | BIGINT | NOT NULL REFERENCES financial_event | 一事件 → 1..n 分录 |
| team_id | BIGINT | NOT NULL | 冗余 |
| account | TEXT | NOT NULL CHECK IN (revenue, refund, channel_fee, adjustment, purchase_cost, freight_cost) | 科目（MVP 单式有向记账；复式留扩展位） |
| amount_usd | NUMERIC(14,2) | NOT NULL | 有向 USD 入账额（舍入规则见汇率块） |
| **汇率块（评审 C4 显式要求）** | | | |
| fx_base / fx_quote | CHAR(3) | NOT NULL | 如 CNY / USD；原币=USD 时 rate=1 |
| fx_rate | NUMERIC(12,6) | NOT NULL | |
| fx_source | TEXT | NOT NULL CHECK IN (settlement_native, purchaser_locked, manual_ref) | 结算原币 / 采购方锁定汇率（D-Q32）/ 手工参考 |
| fx_locked_at | timestamptz | NOT NULL | 汇率锁定时刻 |
| fx_formula | TEXT | NOT NULL | 如 `amount / fx_rate`（显式公式，审计可复算） |
| rounding_rule | TEXT | NOT NULL DEFAULT 'half_even_2dp_entry' | **分录级舍入一次；聚合只求和，绝不再舍**（防分币漂移） |
| 维度列 | | | |
| store_id / master_sku | BIGINT/TEXT | NULL | 投影三粒度用（team 维度=team_id） |
| period_month | DATE | NOT NULL | 月初日（由 occurred_at 派生，过账时定死） |
| posting_rule_version | INT | NOT NULL | 过账规则版本（规则演进不改旧分录，只影响新事件） |
| created_at | | | |

索引：`(team_id, period_month, account)`、`(event_id)`、`(master_sku, period_month)`。
**过账规则（posting rules，版本化）**：settlement_sale→revenue、settlement_refund→refund、
settlement_fee→channel_fee、settlement_adjustment→adjustment、purchase_cost→purchase_cost
（fx_source=purchaser_locked，D-Q32）、purchase_freight→freight_cost、reversal→原分录逐条
取负重发。规则实现为纯函数，`posting_rule_version` 记录于分录。

## settlement_snapshot / settlement_line 结算文档层（channel 原始凭证）

结构与原版一致（headline 汇总 + 明细行 + matched 对账态），**语义修订三点**：

1. **定位降级**：本层是渠道凭证与对账工作台，**不是权威账**——权威在事件层。
   `matched/matched_at` 是工作流状态（可变），金额列一经写入不改。
2. **重拉协议**：`uq_settlement (store_id, period_start, period_end)` 的 upsert 改为
   **版本化**——新增 `version INT` 与 `status CHECK IN (current, superseded)`；重拉=
   插入新版本、旧版置 superseded（保留审计），过账幂等由事件层自然键保证。
3. headline 与明细求和不平 → 对账异常 notification（不静默吞，原版保留）。

其余列定义、索引、对账任务（按 channel_order_no+sku 匹配→回填 order_id/matched、
未匹配行进人工核对页）沿用原版语义：

| settlement_snapshot 列 | 说明 |
|---|---|
| team_id/store_id, period_start/end, currency | 渠道结算周期 |
| gross_sales/refunds/channel_fees/adjustments/net_payout | headline 汇总 |
| payment_processor | 收款通道标签，只显示 `PingPong`（D-Q40） |
| report_ref/pulled_at + version/status | 凭证引用 + 版本化（本修订新增） |

| settlement_line 列 | 说明 |
|---|---|
| snapshot_id, team_id | |
| line_kind CHECK IN (sale, refund, fee, adjustment) | |
| channel_order_no/order_id/channel_sku | 回连订单（解析回填） |
| amount（有向）/currency/detail | 渠道明细原字段 |
| matched/matched_at | 对账工作流状态（可变）；索引 `(matched) WHERE NOT matched` |

## profit_ledger 利润投影（物化缓存，非权威）

列结构沿用原版（granularity sku/store/team × period_month × revenue/channel_fees/
purchase_cost/refund_amount/profit + `uq_profit_ledger`），**语义修订**：

- 本表=`ledger_entry` 的聚合**投影缓存**，任何时刻可全量重建；数字冲突时
  **以分录层为准**，投影表重算，绝不反向修分录。
- 物化协议：每日重算当月+上月（幂等 DELETE+INSERT 该窗口）——作为缓存刷新合法；
  新增 `rebuild_check`：重算窗口的投影值必须与分录层直接聚合逐行相等（内建对账）。
- 覆盖率标注沿用：未匹配结算行不产生事件、不计入投影，报表标注覆盖率——
  **宁可标注不全，不做估算数**（D-Q37）。

## exchange_rate_log 汇率参考（全局，原版不变）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| pair | TEXT | NOT NULL DEFAULT 'USD/CNY' | |
| rate | NUMERIC(12,6) | NOT NULL | |
| source | TEXT | NOT NULL CHECK IN (manual, purchaser_avg) | 无外部行情依赖 |
| effective_date | DATE | NOT NULL | |
| created_by / created_at | | | |

约束：`uq_exchange_rate (pair, effective_date, source)`。
定位：**成本核算不用本表**（分录汇率块以 purchaser 锁定汇率为源，D-Q32）；
仅供报表折算展示与趋势参考。

## R2-08 验收补强（随本修订生效）

在 007 计划验收（真实结算入库/对账差异可解释/三粒度闭合抽检）之上追加：

1. **可复算**：任取一个月份，`profit_ledger` 三粒度全量重建 == 现存物化值（逐行）；
2. **不可变实证**：以 erp_app 对 financial_event/ledger_entry 执行 UPDATE/DELETE
   必须被拒（授权+触发器双关，测试化）；
3. **冲销演练**：人工造一笔错账 → reversal+新事件 → 投影自动归正，原事件仍在案；
4. **汇率审计**：抽 10 条 purchase_cost 分录，按 fx_formula 手工复算 == amount_usd。
