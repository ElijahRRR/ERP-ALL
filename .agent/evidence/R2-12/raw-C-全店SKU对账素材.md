# 考古C：全店后台 SKU 拉取对账 + 报错回收素材（2026-07-23）

## 1. 渠道读通道
- 网关唯一出口 client.py:242/268；endpoint_key 缺省剥 query（:289）——带 query 的 GET 必须显式传 key。
- 三段式：prepare 短事务 :224 → request_prepared 零事务发包。dry_run 快照 :291；限流闸 registry.gate :316；响应头回填 :344。
- 翻页三种语义：order_pull（nextCursor=带?query 串，path 拼接 :296-297）；return_pull（nextCursor=完整 URL，parse_qs 解回 :321-326）；items（旧仓实测：nextCursor=会话 ID 不变，真翻页靠 offset 递增，daily_cleanup.py:244-323）。
- 限流桶（GCRA per (store,endpoint)，rate_limiter.py:31-62）：GET /v3/items 带 query 须显式 endpoint_key="GET /v3/items?q"（60/min :40），无 query 300/min :41。quota_usage 记账仅写操作（listing_create/delete/maintenance），GET 只受限流桶。

## 2. 速率与响应
- rate_limits.tsv:88-91：GET /v3/items 300/min（无 query）/60/min（带 query）；单品 900→60。
- items openapi：totalItems :510 / limit :514 / nextCursor :518；wpid :533；lifecycleStatus(ACTIVE/ARCHIVED/RETIRED) :472；publishedStatus.status(PUBLISHED/READY_TO_PUBLISH/IN_PROGRESS/UNPUBLISHED/STAGE/SYSTEM_PROBLEM) :449-467；unpublishedReasons :134/:170。

## 3. 旧系统语义（daily_cleanup 全套可考据）
- TARGET_STATUSES=[UNPUBLISHED, SYSTEM_PROBLEM] :55；limit=200+offset 翻页 :263-319；带 query sleep1.1s :321-323；分页重复 SKU 去重 :325-335；原因取 unpublishedReasons.reason[] :295-297。
- 13 类归类表（feishu_sync.py:404-453 + README:54-98,167-183）：A 过期→MP_MAINTENANCE 反补 endDate；B 禁售/C 品牌/E 知产/F 限类/G 药品/K 审查→DELETE+黑名单（六类=永久禁售清单，blacklist_sync 写选品黑名单）；D 价格/H 信息/I 内容→DELETE；J 特殊（stage 待发布跳过）；L 系统→重试；Z 兜底。C/E 另入 brand_collector。DELETE 2 日去重；仅 ACTIVE 店。
- erp-core 另一套按 Feed errorCode 分类（error_classifier.py 8 类→8 处置），与 unpublishedReasons 文本分类是两套维度。

## 4. 新系统 listing 侧
- 对账键：channel_sku/wpid/channel_item_id；uq_listing(store_id, channel_sku)（0009:76-113）。status 11 态；ix_listing_error（failed/degraded 部分索引）:111。
- 既有框架未接 runner：app.maintenance_task（task_kind 7 种，0009:268-292，TASKS 无执行器）；app.listing_error_catalog（disposition 6 种 + WM_ASYNC_REVIEW/WM_SKU_LOCKED 种子，未登记码自动插草稿 0009:240-262）。
- 单品对账先例 retire_recon/price_recon（tasks.py:364/470，GET /v3/items/{sku}）。全店拉取/对账 runner/黑名单回流表均待建。

## 骨架建议素材
1. item_pull 独立模块照 order/aftersale pull 三段式；TASKS 注册 + schedule 种子。
2. 翻页：publishedStatus 逐态 + limit=200 + offset 递增；endpoint_key 显式 "GET /v3/items?q"。
3. 差异一（后台有本地无）→ upsert 建 listing + sync_state。
4. 差异二（状态漂移）→ transition + listing_state_history + maintenance_task。
5. 差异三（错误 SKU）→ listing_error_catalog 分类 + error_code/degraded；永久禁售类（旧 B/C/E/F/G/K）→ 黑名单候选（断言账本承载）。
6. 读写分离：拉取任务只读+落差异；执行交 maintenance_task runner（quota 记账）。
