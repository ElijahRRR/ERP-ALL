# 08 finance — 结算快照 / 结算明细 / 利润账 / 汇率

> 决策依据：D-Q32（三粒度利润 + 采购方汇率即成本）、D-Q40（收款仅 PingPong 标签）。
> 结构参考：walmart_settlement.db 的 settlement_snapshots + recon_details（调研 §2.4）——语义继承，字段按新模型规范化。

## settlement_snapshot 结算快照

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id / store_id | BIGINT | NOT NULL | |
| period_start / period_end | DATE | NOT NULL | 渠道结算周期 |
| currency | CHAR(3) | NOT NULL DEFAULT 'USD' | |
| gross_sales | NUMERIC(14,2) | NOT NULL DEFAULT 0 | |
| refunds | NUMERIC(14,2) | NOT NULL DEFAULT 0 | |
| channel_fees | NUMERIC(14,2) | NOT NULL DEFAULT 0 | 佣金+其他渠道费 |
| adjustments | NUMERIC(14,2) | NOT NULL DEFAULT 0 | 调整项净额 |
| net_payout | NUMERIC(14,2) | NOT NULL | 渠道放款额 |
| payment_processor | TEXT | NULL | 收款通道标签，只显示 `PingPong`（D-Q40） |
| report_ref | TEXT | NULL | 渠道结算报告原文件引用 |
| pulled_at | timestamptz | NOT NULL | |
| +公共列 | | | |

约束：`uq_settlement (store_id, period_start, period_end)`（重拉 upsert）。
索引：`(team_id, period_end DESC)`。
来源：渠道 reports API 定时拉取（automation schedule）；headline 与明细求和不平时 → 对账异常 notification（不静默吞）。

## settlement_line 结算明细（recon）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| snapshot_id | BIGINT | NOT NULL REFERENCES settlement_snapshot | |
| team_id | BIGINT | NOT NULL | 冗余 |
| line_kind | TEXT | NOT NULL CHECK IN (sale, refund, fee, adjustment) | |
| channel_order_no | TEXT | NULL | 回连订单（fee/adjustment 可无单） |
| order_id | BIGINT | NULL | 解析回填 |
| channel_sku | TEXT | NULL | |
| amount | NUMERIC(14,2) | NOT NULL | 有向（退款/费用为负） |
| currency | CHAR(3) | NOT NULL | |
| detail | JSONB | NOT NULL DEFAULT '{}' | 渠道明细原字段 |
| matched | BOOLEAN | NOT NULL DEFAULT false | 与 channel_order 对账成功 |
| matched_at | timestamptz | NULL | |
| created_at | | | |

索引：`(snapshot_id)`、`(channel_order_no)`、`(matched) WHERE NOT matched`（未匹配队列）。
对账任务：按 channel_order_no+sku 匹配 → 回填 order_id/matched；未匹配行进人工核对页。

## profit_ledger 利润账（三粒度物化，D-Q32）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| granularity | TEXT | NOT NULL CHECK IN (sku, store, team) | |
| object_key | TEXT | NOT NULL | master_sku / store_id / team_id（文本统一） |
| period_month | DATE | NOT NULL | 月初日 |
| revenue | NUMERIC(14,2) | NOT NULL DEFAULT 0 | 已结算销售额 |
| channel_fees | NUMERIC(14,2) | NOT NULL DEFAULT 0 | |
| purchase_cost | NUMERIC(14,2) | NOT NULL DEFAULT 0 | Σ(procurement.purchase_cost+freight)×exchange_rate_locked 换算 USD（D-Q32：锁定汇率） |
| refund_amount | NUMERIC(14,2) | NOT NULL DEFAULT 0 | |
| profit | NUMERIC(14,2) | NOT NULL DEFAULT 0 | revenue - fees - cost - refunds |
| currency | CHAR(3) | NOT NULL DEFAULT 'USD' | |
| computed_at | timestamptz | NOT NULL | |

约束：`uq_profit_ledger (team_id, granularity, object_key, period_month)`。
物化协议：每日重算当月+上月（幂等 DELETE+INSERT 该窗口）；源=settlement_line(matched) + procurement_order(backfilled)；未匹配结算行不计入并在报表标注覆盖率——**宁可标注不全，不做估算数**（稳定优先 D-Q37）。

## exchange_rate_log 汇率参考（全局）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| pair | TEXT | NOT NULL DEFAULT 'USD/CNY' | |
| rate | NUMERIC(12,6) | NOT NULL | |
| source | TEXT | NOT NULL CHECK IN (manual, purchaser_avg) | 无外部行情依赖；手工维护 + 采购方均值参考 |
| effective_date | DATE | NOT NULL | |
| created_by / created_at | | | |

约束：`uq_exchange_rate (pair, effective_date, source)`。
定位说明：**成本核算不用本表**（用 purchaser 锁定汇率，D-Q32）；本表仅供报表折算展示与汇率趋势参考。
