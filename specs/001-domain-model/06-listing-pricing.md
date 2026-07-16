# 06 listing + pricing — 刊登 / feed / spec / 错误字典 / 维护任务 / 定价策略 / 价格历史

> 决策依据：D-Q3（单管道双模式 offer_mode build|match）、D-Q9（End Date 统一 2049 + 自动续期）、D-Q23（跟卖定价独立成套）、D-Q25（默认库存 5）、D-Q26（盯价一天一次可配）、D-Q31（去重+店铺豁免）。
> 总账铁律映射：feed 提交**永不盲重试**（verify-back）；headline 计数不可信（item 级权威）；SKU_LOCKED 单独处置；MP_ITEM 配额 10/h、PRICE_AND_PROMOTION 6/day（渠道网关 GCRA 层控制）。

## listing 刊登（200 万在线 / 500 万累计，不分区）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| store_id | BIGINT | NOT NULL REFERENCES store | |
| product_id | BIGINT | NOT NULL REFERENCES product | |
| offer_mode | TEXT | NOT NULL CHECK IN (build, match) | 双模式共用本表与生命周期机器（D-Q3） |
| channel_sku | TEXT | NOT NULL | 新品=master_sku（D1）；重拉存量=原 SKU |
| gtin | TEXT | NULL | build 必填（服务层校验）；match 跟卖无需自有 GTIN |
| status | TEXT | NOT NULL DEFAULT 'draft' CHECK IN (draft, queued, submitted, processing, published, live, degraded, delist_pending, delisted, failed, retired) | 见状态机 |
| error_code | TEXT | NULL | 终态 failed / degraded 的当前错误 → listing_error_catalog |
| is_locked | BOOLEAN | NOT NULL DEFAULT false | SKU_LOCKED：渠道锁定，维护类操作全部跳过，专用解锁流程 |
| wpid / channel_item_id | TEXT | NULL | 渠道侧标识（上架成功回填） |
| end_date | DATE | NOT NULL DEFAULT '2049-12-31' | 统一远期 + 自动续期任务（D-Q9） |
| current_price | NUMERIC(12,2) | NULL | + currency CHAR(3) DEFAULT 'USD' |
| current_inventory | INT | NOT NULL DEFAULT 5 | 默认 5（D-Q25） |
| published_at / delisted_at | timestamptz | NULL | |
| last_synced_at | timestamptz | NULL | 渠道侧状态最近核对时间 |
| last_maintained_at | timestamptz | NULL | |
| +公共列 | | | |

约束与索引：
- `uq_listing (store_id, channel_sku)`。
- `ix_listing (team_id, status)`、`(store_id, status)`、`(product_id)`、`(status, last_maintained_at) WHERE status='live'`（维护扫描）、`(error_code) WHERE status IN ('failed','degraded')`。
- **去重（D-Q31）**：默认团队内一 product 一店在架；store.dedup_exempt=true 的店豁免。DB 无法表达含豁免的条件唯一 → 服务层检查（advisory lock on (team_id, product_id)）+ 支撑索引 `(product_id, store_id)`。README 开放点 4。

状态机（服务层唯一模块，全迁移写 listing_state_history）：
```
draft → queued → submitted → processing → published → live
processing → failed(error_code)          ← 渠道明确拒绝
live ⇄ degraded                          ← 维护发现渠道侧异常（价格错误/下架风险）
live → delist_pending → delisted         ← 主动下架（配额受 listing_delete 控）
delisted → retired                        ← 终局（GTIN 不回收）
failed → queued                           ← 修复后重投（error 处置=auto_retry/manual）
```

## listing_state_history 状态迁移史（月分区）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | identity | PK=(id, occurred_at) |
| listing_id | BIGINT | NOT NULL | 不建 FK |
| team_id | BIGINT | NOT NULL | 冗余 |
| from_status / to_status | TEXT | NOT NULL | |
| reason_code | TEXT | NULL | error_code / 人工原因 / feed_id 引用 |
| detail | JSONB | NOT NULL DEFAULT '{}' | |
| actor_type | TEXT | NOT NULL CHECK IN (user, system) | |
| actor_id | BIGINT | NULL | |
| occurred_at | timestamptz | NOT NULL DEFAULT now() | 分区键 |

索引：`(listing_id, occurred_at DESC)`。

## feed 渠道批量提交

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id / store_id | BIGINT | NOT NULL | |
| channel_feed_id | TEXT | NULL UNIQUE | 渠道 feedId；**提交无响应时为 NULL → 走 verify-back，禁止直接重试** |
| feed_kind | TEXT | NOT NULL CHECK IN (item_build, item_match, price, inventory, delete, lag_time) | item_build=MP_ITEM(10/h)、item_match=MP_ITEM_MATCH、price=PRICE_AND_PROMOTION(6/day！必聚合) |
| status | TEXT | NOT NULL DEFAULT 'building' CHECK IN (building, submitting, verify_pending, submitted, processing, processed, partial, error, lost) | submitting=已交 outbox 命令待发（RS-03b）；verify_pending=提交结果未知，查渠道近程 feeds 对账后归位；lost=对账确认渠道未收到 |
| item_count | INT | NOT NULL DEFAULT 0 | |
| headline | JSONB | NULL | 渠道汇总计数（**不可信**，仅展示；对账以 feed_item 为准——总账规则） |
| submitted_at / last_polled_at / completed_at | timestamptz | NULL | |
| poll_attempts | INT | NOT NULL DEFAULT 0 | |
| raw_response_ref | TEXT | NULL | 大响应落盘引用 |
| +公共列 | | | |

索引：`(store_id, feed_kind, created_at DESC)`、`(status) WHERE status IN ('verify_pending','submitted','processing')`（轮询队列）。
轮询节流：`/v3/feeds*` 共享 5000/min（网关层）；退避序列进 system_config。
自动轮询/对账（R2-04 beat）：`feed_poll`（submitted/processing，min_interval 节流 +
poll_attempts 卡死告警）与 `feed_verify_back`（verify_pending，min_age 滞留门槛）
周期驱动；人工端点 `/feeds/{id}/poll`、`/feeds/{id}/verify-back` 保留（并发安全，先完成者为准）。

**提交拓扑（RS-03b，评审 A7）**：feed 创建与 channel_command（02 §channel_command）
同事务落库（tx1，status=submitting）→ 执行器事务外发包 → tx2 fence 校验归位
（submitted/error/verify_pending）。行锁不跨 HTTP；进程任一点崩溃命令行即恢复线索
（pending→drain 补发；inflight 超 lease→verify_pending 对账，**绝不重发**）。
verify-back 归位（adopt/lost）同步终局命令，解开同店 FIFO 车道。

## feed_item 提交明细（月分区，年 7 千万行量级）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | identity | PK=(id, created_at) |
| feed_id | BIGINT | NOT NULL | 不建 FK（热路径） |
| team_id | BIGINT | NOT NULL | 冗余 |
| listing_id | BIGINT | NOT NULL | |
| channel_sku | TEXT | NOT NULL | |
| status | TEXT | NOT NULL DEFAULT 'pending' CHECK IN (pending, success, error) | item 级权威结果 |
| error_code / error_msg | TEXT | NULL | error_code 入 listing_error_catalog 闭环 |
| raw | JSONB | NULL | 渠道 item 级响应 |
| created_at | | 分区键 | |

索引：`(feed_id)`、`(listing_id, created_at DESC)`、`(error_code, created_at) WHERE status='error'`（错误分析）。

## listing_spec 规格构建产物（可复算缓存）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| product_id | BIGINT | NOT NULL REFERENCES product | |
| offer_mode | TEXT | NOT NULL CHECK IN (build, match) | 双模式 spec 构建器不同（D-Q3 分叉点之一） |
| wpt | TEXT | NOT NULL | 链：attrs.wpt 显式 > L1 直判（category_map，与审核同语义）> 默认配置；match 模式无 WPT 存 ''（R2-03） |
| spec_version | TEXT | NOT NULL DEFAULT 'v5' | build=header 完整时间戳版本（如 `5.0.20260304-22_45_32-api`，BR-LST-005）；match='4.2'（R2-03 实现修订） |
| payload | JSONB | NOT NULL | **单个 MPItem 元素模板**（build={Visible,Orderable}；match={Item}）——feed 级 header 是提交时组装，不入产品级缓存（R2-03 修订：原「最终提交体」含 header 的设计随构建器真实化调整）。SKU/GTIN/价格/库存/日期/PartnerID 为 listing/store 级参数经占位符实例化注入 |
| cert_overrides | JSONB | NOT NULL DEFAULT '{}' | 零认证覆盖记录（BR-AUD-006：被强制的字段→值 + `__cleared__` 被清除的文档字段） |
| build_hash | TEXT | NOT NULL | 输入指纹（product 关键属性+wpt+模式+配置+pt_spec dataset_revision）；输入未变直接复用，规格数据更新自动失效 |
| built_at | timestamptz | NOT NULL DEFAULT now() | |

约束：`uq_listing_spec (product_id, offer_mode, build_hash)`。

> **实测格式注记（BR-LST-005/006/007，构建器已固化）**：feed header 只能 3 字段
> （businessUnit/locale/version，官方 sample 的 sellingChannel/processMode/subset 会被拒）；
> `endDate` 必须 ISO DateTime（listing.end_date DATE 列在构建时转 `T00:00:00Z`，
> 纯日期被拒 EXT_DATA_ERROR_00030257670757）；productIdentifiers 单对象非数组、
> price 裸 number、inventory=[{quantity, fulfillmentCenterID=PartnerID}]
> （PartnerID 维护在 store.profile.partner_id）。

## listing_error_catalog 错误分类字典（全局，运营可维护 D-Q11）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| error_code | TEXT | PK | 渠道错误码或内部码（前缀区分 `WM_`/`ERP_`） |
| category | TEXT | NOT NULL | 内容/合规/GTIN/类目/限流/系统… |
| title | TEXT | NOT NULL | 中文名 |
| disposition | TEXT | NOT NULL CHECK IN (auto_retry, backoff_retry, rebuild_spec, skip, manual, fatal) | 处置策略：worker 按此分派 |
| max_retries | INT | NOT NULL DEFAULT 3 | |
| notes | TEXT | NULL | 运营处置手册 |
| enabled | BOOLEAN | NOT NULL DEFAULT true | |
| updated_by / updated_at / created_at | | | |

- 未登记错误码 → 默认 manual + 自动插入草稿行（category='未分类'）→ notification 提醒运营归类。**异步审核伪错误码**（总账：async-review fake errors）登记为 disposition=backoff_retry。

## maintenance_task 维护任务（价/库存/标题/续期/下架）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id / store_id | BIGINT | NOT NULL | |
| listing_id | BIGINT | NOT NULL | |
| task_kind | TEXT | NOT NULL CHECK IN (price_sync, inventory_sync, title_fix, end_date_renewal, delist, relist, unlock_probe) | unlock_probe=SKU_LOCKED 探测解锁 |
| status | TEXT | NOT NULL DEFAULT 'scheduled' CHECK IN (scheduled, running, done, failed, skipped) | skipped=is_locked 或配额不足 |
| priority | SMALLINT | NOT NULL DEFAULT 100 | 越小越先 |
| scheduled_at | timestamptz | NOT NULL | |
| started_at / finished_at | timestamptz | NULL | |
| result | JSONB | NULL | |
| error | TEXT | NULL | |
| created_by | BIGINT | NULL | 人工触发时记录；系统生成为 NULL |
| created_at | | | |

索引：`(status, priority, scheduled_at) WHERE status='scheduled'`（取件）、`(listing_id, task_kind, created_at DESC)`。
生成规则（automation 域调度产出）：盯价默认 1 次/日、store 级频率可配（D-Q26，配置在 automation_policy flow=pricing_watch 的 config）；end_date_renewal 扫描 end_date < now()+180d（理论上 2049 前不触发，作防御）；消耗 maintenance 配额（02 号文档）。

## pricing_strategy 定价策略注册表（D-Q23）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| store_id | BIGINT | NULL REFERENCES store | NULL=团队默认；非空=店铺覆盖 |
| offer_mode | TEXT | NOT NULL CHECK IN (build, match) | **双模式各配一套**（D-Q23：跟卖定价不同） |
| name | TEXT | NOT NULL | |
| algo_code | TEXT | NOT NULL | 策略算法注册名：`cost_plus`（build 默认）/ `manual`（match 现行：人工指定价）/ 未来 `follow_buybox`… |
| params | JSONB | NOT NULL DEFAULT '{}' | 系数/上下限/守护值（min_price 底线**可选**——D-Q62 补充裁定 2026-07-16：区间从 $0 起的业务形态下绝对底线价值有限，填了才生效且必须 >0；原「必填」表述废止） |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN (active, disabled) | |
| version | INT | NOT NULL DEFAULT 1 | params 每改 +1 |
| updated_by / updated_at / created_at | | | |

约束：`uq_pricing_strategy (team_id, COALESCE(store_id,0), offer_mode) WHERE status='active'` —— 解析顺序 store 级 > team 级，同键活跃唯一。
策略引擎契约：输入（成本/竞争价/参数）→ 输出（价格 + 计算明细 JSON），明细进 price_history.detail；改价过 min_price 守护（策略设了才生效——D-Q62 补充：可选）+ 限价四检口径一致（07 号文档）。

## price_history 价格变更史（月分区）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | identity | PK=(id, occurred_at) |
| listing_id | BIGINT | NOT NULL | |
| team_id | BIGINT | NOT NULL | 冗余 |
| old_price / new_price | NUMERIC(12,2) | | + currency |
| reason | TEXT | NOT NULL CHECK IN (strategy, manual, watchdog, initial) | watchdog=盯价触发 |
| strategy_id | BIGINT | NULL | + strategy_version INT |
| detail | JSONB | NOT NULL DEFAULT '{}' | 计算明细（可追溯） |
| actor_type / actor_id | | | |
| occurred_at | timestamptz | NOT NULL DEFAULT now() | 分区键 |

索引：`(listing_id, occurred_at DESC)`。
渠道执行注记：单品改价 PUT /v3/price=100/h、批量走 price feed=6/day —— **改价执行必须聚合成批**，由 automation 域按店按日窗口合并（网关层强制）。
