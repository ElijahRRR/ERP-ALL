# A152 真实上架 1 SKU — Owner 部署机执行手册（R1-11 收尾步）

> 前置：本地部署验收通过 + 前端可登录 + A152 凭证与代理已在「店铺管理」录入。
> 沙盒宪法禁真调渠道（每店固定出口 IP 防关联）——此步只能在部署机执行。

## 0. 准备（一次性）

1. 店铺管理 → A152 → 勾选 **is_test 测试店**（live_test 闸门要求）。
2. 系统配置写入（当前用 SQL，配置界面 R2）：
   ```sql
   -- Git Bash: docker compose -f infra/docker-compose.yml exec db psql -U postgres -d erp_all
   INSERT INTO app.system_config (key, value) VALUES
     ('channel.gateway_mode', '"live_test"'),
     ('listing.default_wpt', '"Drinkware"')   -- 按选的测试品实际类目改
   ON CONFLICT (key) DO UPDATE SET value = excluded.value;
   ```
3. GTIN：登录 ERP → 用 `POST /api/v1/gtin-pool/import` 导入 20 枚 EAN-13
   （生成器产出；前端页面 R2，暂用 /api/docs 的 Swagger UI 操作）。

## 1. 干跑确认（不发包）

`channel.gateway_mode` 先保持 `"dry_run"` 走一遍 allocate→submit，
检查返回的 request_snapshot：URL/feedType/MPItem 字段/GTIN/价格全部符合预期后再切 live_test。

## 2. 真调链路（Swagger UI /api/docs 逐步）

1. 选 1 个已审核通过（audit_passed）的产品 → `POST /listings/allocate`
   `{product_ids: [<id>], store_id: <A152>, offer_mode: "build"}`
2. `POST /listings/submit` `{listing_ids: [<listing_id>]}`
   → 期望 `feed_status: "submitted"` + `channel_feed_id`
   - 若返回 `verify_pending`：**不要重发**，等 1 分钟 → `POST /feeds/{id}/verify-back`
3. 每 5-10 分钟 `POST /feeds/{id}/poll`（MP_ITEM 配额 10/小时，别频繁点）
   直至 `feed_status: processed`；listing 应变 `live` 并回填 wpid
4. Walmart Seller Center（A152 后台）确认商品可见 → 截图存档
5. 收尾：`POST /listings/{id}/delist` → 状态 `delisted`
6. 验证 `GET /listings/{id}` 的 state_history 完整链：
   draft→queued→submitted→published→live→delist_pending→delisted

## 3. 出问题时

- item 级错误：`GET /feeds/{id}/items?status=error` 看 error_code/error_msg，
  把错误码原文发给远端 agent（错误字典自动收录草稿行，agent 归类处置策略）
- 全程记录 x-current-token-count 报警/429：发给 agent 调限流表
- **任何步骤卡住：停下来把响应原文发给 agent，不要自行改数据库**
