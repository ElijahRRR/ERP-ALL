# R2-06 定价引擎 验收 runbook

> 验收（review_list R2-06）：①新建 listing 自动带策略价（不再 0 价/手工价）；
> ②A152 真机一次改价经价格管道同步成功（渠道后台价变 + ERP 两段式回填闭环）。
> 现成验收对象：listing #46（M0002418，live，现价 39.99）。

## 部署机指令（可整段粘贴）

```
【铁律】绝不操作生产库 erp_all 的结构；暂存用一次性容器；用毕清理；不输出密钥。

任务：升级到 R2-06 版本（新迁移 0027/0028/0029 自动执行）。

1) git fetch origin main && git checkout <SHA——合并后云端提供>
2) docker compose -f infra/docker-compose.yml up -d --build db redis migrate api beat
   docker compose -f infra/docker-compose.yml --profile dev up -d --force-recreate frontend
3) 核验：migrate 日志升级到 0029；beat 日志正常（新任务 price_recon 每 15 分钟）；
   浏览器 Ctrl+F5 后左侧菜单出现「定价策略」。
4) 回报：以上核验结果。
```

## Owner 验收步骤（ERP 界面操作）

1. **建策略**：定价策略页 →「建策略」→ 模式 build、店铺留空（团队默认）、算法 cost_plus、
   min_price 留空（不设防）或填个兜底数；区间模板已预填（FBA 0-20/20-80、FBM 20-80/80-1000，
   D-Q62 定值）——**倍数自己填**（如 2.75/2.5）→ 保存。
2. **验收①（自动算价）**：产品库选一个有采集价的产品 → 上架管理 allocate 到 A152 →
   新 listing 的价格列应自动 = (源价) × 区间倍数（不再是空/手工价）；「历史」抽屉
   顶部新增「价格历史」小节——initial 价史带公式明细（如 FBA $19.99 × 2.75 = $54.97）。
   **履约判定（2026-07-16 两轮验收缺陷修复）**：按采集字段 is_fba 判 FBA/FBM（真实
   落点在 price_snapshot.is_fba——worker 适配器归类如此；attrs.is_fba 兜底）；
   is_fba 为 N/A/缺失的产品会被**拒绝出价**（旧仓同款 fail-closed），如需兜底可在
   策略 params 加 "default_fulfillment": "FBM"。区间外/无源价/判不出履约的拒绝均属
   预期行为。若产品实际带 Yes/No 仍被拒，用只读 SQL 核对数据形态：
   SELECT price_snapshot->>'is_fba', attrs->>'is_fba', count(*) FROM app.product GROUP BY 1,2;
3. **验收②（真机改价同步）**：上架管理页对 **listing #46**（live）点「改价」→ 输入新价
   （如 41.99；超 39.99 的 30% 会弹二次确认）→ 提交。系统经 PUT /v3/price 推渠道：
   - 改价瞬间 ERP 价格**不变**（两段式：在途标记 pending，渠道确认才回填）；
   - ≤5 分钟内 Walmart 后台该商品价格变为新价；
   - ERP 侧刷新后价格列变新价、「历史」多一条 manual 价史（渠道确认后回填）；
   - 若渠道结果未知，price_recon 每 15 分钟自动对账收敛，无需人工。
4. **（可选）试算/批量**：定价策略页「试算」输入 listing id 看新旧价对比；「批量重定价」
   对多个 live listing 按策略推价（超 100 条/时的部分自动排队由 beat 续推）。
5. 回报：验收①的新 listing 价格截图/数值 + 验收②的 Walmart 后台价格变化与 ERP 回填结果。

## 调参速查（改数据不改代码）

| 键/位置 | 内容 | 默认 |
|---|---|---|
| system_config `pricing.confirm_threshold_pct` | 改价二次确认阈值（team 可覆盖） | 0.30 |
| system_config `pricing.put_route_threshold` | 单店 ≤N 条走 PUT，更多聚合 feed | 5 |
| system_config `gtin.safe_prefixes` | GTIN 首位白名单（BR-UPC-002） | 0/1/6/7/8/9 |
| schedule `price_recon`.config | batch / min_age_s / grace_s | 10/300/3600 |
| 策略 params | bands 区间与倍数 / min_price（可选底线） | 建档模板 |

## 边界与已知注记

- follow_buybox 竞价、自动盯价（D-Q26）、促销价：本单不做（archaeology 范围边界）。
- 429/限流被挡的改价命令自动留队由 beat channel_outbox_drain（*/5）续推，非故障。
- 上架成功现在有 info 通知（SM-0716①）；GTIN 导入自动拒 2/3/4/5 开头受限号（SM-0716②）。
