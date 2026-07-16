# R2-06 定价引擎 考古汇总（2026-07-16，四路并行：specs/新系统触点/旧仓语义/渠道 API）

## 一、范围边界（冻结依据）

- **本单只做 cost_plus + min_price**（005:56 明文圈定）；match 现行 = manual 人工指定价（D-Q23）；
  follow_buybox 竞价为预留 algo_code，本单不实现（旧仓亦零实现——限额表有 Repricer 端点、代码全空）。
- 自动盯价（D-Q26 1 次/天）触发源 = scrape price_watch，本单不做自动盯价；价格同步管道
  （批量重定价→渠道）在范围内。促销价（?promo=true / promotionInformation）不做，
  且 builder 必须硬性禁止促销字段出门（同端点仅靠 query/字段区分，误用即事故）。
- Pricing Insights API（POST /v3/price/getPricingInsights，2/min）= 官方 Buy Box/竞品价/建议价
  数据源，本单不接，记为 follow_buybox 未来项的官方数据面（免抓页面）。

## 二、口径裁定（考古冲突的收敛结论）

1. **成本价优先级 = current_price 优先，缺才用 buybox_price**（BR-PR-002，2026-05-14 用户校正）。
   旧仓 erp-core 同名函数为 buybox 优先——判为移植失真，不带。
2. **价格 feed 格式收敛为单一 canonical builder**。旧仓三套并存（Promo&Discount v2.0 /
   PriceHeader 1.7 扁平 / PriceHeader 1.5 嵌套 value）：
   - 批量走 **PRICE_AND_PROMOTION**：`MPItemFeedHeader{businessUnit,locale,version:"2.0.20240126-12_25_52-api"}`
     + `MPItem[{"Promo&Discount":{sku, price}}]`（旧仓 A 版生产验证 + 官方 curl 示例一致）；
   - 单品走 **PUT /v3/price**：`{sku, pricing:[{currentPriceType:"BASE", currentPrice:{currency,amount}}]}`
     （扁平，官方 Price.json + 旧仓 A/C 两套一致生产验证）。
3. **限额修正（在线官方 2026-07-16 实抓）**：PRICE_AND_PROMOTION 已从 6/day 放宽为
   **10/hour（店铺级，与 legacy price feed、批量促销 feed 三入口共享池）**；PUT /v3/price
   100/hour（店铺级）。本地 TSV:125 与 erpAPI CLAUDE.md 速查为过时记载（顺带修正）。
   feed 状态轮询里价格 feed 的 feedType 显示为 `MP_ITEM_PRICE_UPDATE`。
4. **路由语义保真 BR-MT-002/PR-007 更新版**：单店改价条数 ≤ 阈值（默认 5，可配）走 PUT，
   否则聚合一个 price feed；价差 < $0.01 跳过（BR-PR-006）；feed ≤1000 项/片（建议值）。
5. **两段式写回保真 BR-LC-011**：派发只写 pending_price + 状态 updating；渠道终态 SUCCESS
   才回填 current_price + price_history，失败清 pending 复位。stale-update 假成功
   （ERR_EXT_DATA_0101198）按 STALE_NO_OP 处理不当真。
6. **CAP 归因终裁**：CAP 计划（Walmart-funded incentives）只降展示价、渠道补差、不动提交价
   与回款——与 HF-0716 的 field=CAP 拒收无关（那是 ingestion 定价校验的内部字段标签）。
   0 价本地拦截维持，定价引擎不需要为 CAP 计划做任何事。
7. **区间边界数据冲突消解**：BR-PR-001 文字（FBM 30-100/100-300）与 legacy 实表
   （FBM 15-80/80-1000）不一致——区间本属 params 数据（D-Q11/23 前台按团队配置），代码只冻结
   params schema（区间数组任意段数）；默认模板采 legacy 实表值（生产在用的那套）。→ 需 Owner 认可。
8. **min_price 口径（图纸空白的最小冻结）**：params.min_price = 策略级绝对值底线（USD）；
   算出价 < min_price → 不出价（fail-closed，同区间外语义），detail 记 below_min_price。
   建档必填（001/06:173「min_price 硬底线必填」）。
9. **30% 变动阈值（BR-PR-008 未固化 → 参数化落地）**：pricing 配置 confirm_threshold_pct
   默认 0.30，超阈需 force 二次确认（旧仓 erp-core 语义保真）。

## 三、保真移植清单（旧仓 → erp.pricing）

| # | 移植物 | 源 |
|---|---|---|
| 1 | 核心公式 `售价 = round((成本价+运费) × 区间倍数, 2)`，FBA/FBM 分表查区间 | auto_listing/pricing.py:221-250 |
| 2 | `_parse_multiplier`：'275%'→2.75 + 千分位防御（2026-06-11 整批误淘汰事故修复） | pricing.py:31-54 |
| 3 | `_parse_money`：Free/免运→0；N/A/None→None（无法计价） | pricing.py:57-69 |
| 4 | 区间外→不出价（上架链硬边界）；clamp 版仅供展示（双函数分工） | pricing.py:253-302 |
| 5 | 价差<0.01 跳过（省配额）+ 路由阈值 5/1000 | sync_price_inventory.py, maintenance_common.py |
| 6 | 30% 阈值 + force | erp-core listings.py:855-874 |
| 7 | pending_price 两段式 | listing_tasks_maint.py + feed_orchestrator.py |
| 8 | PUT 体 / PRICE_AND_PROMOTION envelope（见口径 2） | walmart_price_inventory.py:45-85 |

不带的旧债：三套 feed 格式并存、buybox 优先口径、飞书直读链路、数字 0 误判空（`v or ""`）、
无成本感知下限（min_price 补上）。

## 四、接线面（新系统触点考古结论）

- **现成资产直接复用**：feed_kind 枚举含 'price'（0009 预留）；pricing.read/write 权限点
  （0002 已种、三角色已配）；outbox 三段式 + APPLIERS 注册表；ConfigService team>system>default；
  beat 种子模式（0026 先例）；_check_positive_price 出门闸；_FakeChannel/dry-run 证据链。
- **必修缺陷**：rate_limiter 价格桶键 `feeds:PRICE_AND_PROMOTION` 缺 `POST /v3/feeds:` 前缀
  ——现状永远匹配不上、跌默认桶，限额被架空（本单必修 + 补 10/hour 新值）。
- **新增面**：erp/pricing/ 模块（service+router+engine 纯函数）；迁移 0027（pricing_strategy +
  price_history 按 001/06 图纸 + ck_cc_action 扩 price_push + schedule 种子）；outbox.ACTIONS
  加 price_push；drain APPLIERS 聚合；FEED_TYPE_BY_KIND 加 price；listing.update_price 的
  live 拒绝改为「归定价管道」的真实入口；前端 /pricing 页 + 菜单。
- price_snapshot 全库仅 'list' 键、无 cost——与乘数模型口径吻合（成本=源价+运费），无需补列。

## 五、增量拆分

| 增量 | 内容 | 验收锚点 |
|---|---|---|
| 1 | 0027 迁移（pricing_strategy/price_history/ck_cc_action/种子）+ engine 纯函数（公式/解析防御/min_price/clamp）+ 策略 CRUD 三端点（契约已冻结）+ 单元测试 | pytest 绿；策略 (team×store×offer_mode) 活跃唯一约束生效 |
| 2 | allocate/submit 接线自动算价（有 active 策略→算价；无策略→现状 price_snapshot 直取回退）+ 重定价预览端点（契约补充冻结）+ 30% 阈值 | 新建 listing 自动带策略价；预览可批量试算 |
| 3 | 价格同步管道：canonical builder（PUT + feed 双通道路由）+ outbox price_push 三段式 + pending_price 两段式回填 + feed_poll price 分支 + price_recon + rate_limiter 桶键修正 | dry-run 快照证据；_FakeChannel 全链测试 |
| 4 | 前端定价页（策略 CRUD/预览/批量重定价）+ SM-0716 小账三件 + erpAPI 速查修正 | pnpm lint+build 绿 |
| 验收 | L2：A152 对 live 的 listing #46 真机改价一次经 PUT 通道同步，渠道后台价变 + current_price 两段式回填 | Owner 验收节点 |

## 六、需 Owner 拍板（拟 D-Q）

1. **限额与路由规则更新**（→ 改 ledger BR-GW-011/BR-PR-007）：PRICE_AND_PROMOTION 6/day 已放宽为
   10/hour 共享池（官方现行）；路由改为「≤5 条走 PUT，否则聚合 feed」（不再"永远 PUT"）。
2. **默认区间模板取 legacy 实表值**（FBA 0-30/30-80、FBM 15-80/80-1000）而非 BR-PR-001 文字值
   （→ 修 ledger BR-PR-001 注记）；区间本身前台可配，此项只定默认模板。
3. **min_price 必填**：建策略必须填绝对值底线，算出价低于它不出价——运营侧每店/团队建策略时多一个必填项。
