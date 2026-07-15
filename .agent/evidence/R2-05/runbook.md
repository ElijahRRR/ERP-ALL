# R2-05 订单履约最小闭环 验收 runbook

> L1 = 真实订单只读拉取入库对账一致；L2 = 测试单全流程流转（A152 真实来单）。
> 「测试单」无渠道原生概念：= A152（is_test 店）上的真实订单（系统无造单入口，D-Q35）。

## 部署机指令（可整段粘贴）

```
【铁律】绝不操作生产库 erp_all 的结构；暂存用一次性容器；用毕清理；不输出密钥。

任务：拉 R2-05 版本并做 L1 订单拉取对账。

1) 拉代码到 main 最新（合并后以 GitHub 端提供的 SHA 为准）：
   git fetch origin main && git checkout <SHA>
2) 重建（新迁移 0025/0026 自动执行；beat 已含 order_pull/ship_recon 两个新任务）：
   docker compose -f infra/docker-compose.yml up -d --build db redis migrate api beat
3) 核验：
   - migrate 日志升级到 0026；beat 日志无异常
   - 等 ≤15 分钟（order_pull cron */15），或立即手动触发一轮：
     docker compose -f infra/docker-compose.yml exec api \
       python -c "import asyncio; from erp.order.pull import run; from erp.core.db import get_session_factory; print(asyncio.run(run(get_session_factory(), {})))"
4) L1 对账（渠道为准，只读）：
   docker compose -f infra/docker-compose.yml exec api \
     python -m erp.tools.order_pull_verify --store <A152的store_id> --days 30
   期望：退出码 0「对账一致 ✅」；如有「状态差」先重跑步骤 3 的手动触发再复核
   （渠道状态在窗口内变化属正常滞后）。
5) （可选）导入钓鱼黑名单激活 phishing 检：
   从 lark 导出地址/邮编两表为 csv（注意地址表表头在第 5 行，先删前 4 行噪声），然后：
   docker compose -f infra/docker-compose.yml exec api \
     python -m erp.tools.import_blacklist --domain blacklist_address --file /data/phishing_addr.csv
   docker compose -f infra/docker-compose.yml exec api \
     python -m erp.tools.import_blacklist --domain blacklist_zip --file /data/phishing_zip.csv
6) 回报：拉取 stats（订单/行数）、对账结果全文、异常日志（如有）。
```

## L2 测试单全流程（Owner 操作，需 A152 有真实订单）

前提：A152 在架商品有真实买家下单（或自购一单）。订单会在 ≤15 分钟内自动入库并完成四检。

1. **看单**：订单页 → 找到该单（internal_status=checked；有 flagged 标记则点开详情看四检证据，
   确认无误可「放行」）。
2. **采购**：订单详情 →「建执行单」→ 二选一：
   - 分配给采购方 → 领单（此刻锁定汇率快照）→ 回填采购信息+物流单号；
   - 或不分配直接「代填」（op_direct）。
3. **发货**：订单详情 →「发货」→ 填承运商 + 运单号（真实履约用真单号）→ 提交。
   系统自动先 acknowledge 再 shipping 推 Walmart（同店保序；结果未知会自动对账，绝不重发）。
4. **核对**：Walmart 后台该订单行应变为 Shipped 且带运单号；ERP 侧订单 internal_status=shipped、
   回传单 pushed。下一轮 order_pull 会把渠道侧状态刷新回来（闭环自证）。
5. 全链走通即 L2 过——回报截图/状态即可。

## 调参速查（改数据不改代码）

| 键/位置 | 内容 | 默认 |
|---|---|---|
| schedule `order_pull`.config | overlap_hours/initial_days/created_floor_days/page_limit/max_pages | 1/30/179/200/30 |
| schedule `ship_recon`.config | batch/min_age_s/grace_s | 10/300/3600 |
| system_config `order.checks` | margin_factor/usd_rmb_rate/consistency_ratio（team_config 可覆盖，D-Q11/C10） | 0.85/6.8/0.9 |
| system_config `order.ship` | method_code | Standard |
| automation_policy `order_block` | manual=软标记；semi/auto=flagged 冻结不进分配 | manual |

## 边界与已知注记

- refund/cancel 执行、channel_return 拉取：随售后单（R2 迭代 7）；契约未冻结 API。
- 采购方门户（外部账号/portal 路由）：R2#6；本期 external 采购方仅建档。
- purchaser 检为降档口径（存在 active 采购方）；BR-ORD-007 的区间/配送方式匹配需档案扩列（待决）。
- 订单不联动库存/listing（源驱动库存模型，考古 §6）。
