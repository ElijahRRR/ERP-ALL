# R1-11 本地验证记录（2026-07-10）

## 验收对照（specs/003 §R1-11）

| 验收项 | 结果 | 证据 |
|---|---|---|
| listing 六表 + state_history migration | ✅ | 0009：listing/state_history(月分区)/feed/feed_item(月分区)/listing_spec/error_catalog/maintenance_task + gtin_pool；升降升演练过；错误字典种子 5 条（含 WM_ASYNC_REVIEW=backoff_retry 总账规则） |
| GTIN 20 EAN-13 入池 held→used 全程 | ✅ | test_import_20_ean13（GS1 校验位+重复报告）→ allocate 预占 held → poll 成功 used（终身绑定）→ 失败归还 free → delist 后仍 used（永不回收） |
| allocate/submit/轮询/状态回写链路 | ✅ | 一 feed 一店一模式组批；配额 listing_create 扣减/失败返还；poll 以 item 级为权威（headline 造假 99 仍按 1成1败回写）；成功回填 wpid→published→live |
| feed verify-back 分支（channel_feed_id=NULL） | ✅ | 传输错误→status=None→verify_pending（断言只发过一次包——永不盲重试）；对账 lost→回队+返配额；对账唯一匹配→adopt 渠道 feedId+listing 推进 |
| listing_state_history 完整链 | ✅ | 断言逐条：draft→queued→submitted→published→live→delist_pending→delisted |
| 未登记错误码自动入字典 | ✅ | WM_TEST_NEWCODE → 草稿行（manual/未分类） |
| failed 重投（按处置策略校验） | ✅ | fatal/skip 拒重投；build 重投自动重新占号 |
| dry-run 全量证据 | ✅ | dry-run-feed-snapshot.json（MPItem v5 形态+GTIN/价格实例化） |
| A152 真实上架 1 SKU → PROCESSED → live → delist | ⏸ Owner 机器 | a152-live-runbook.md 已交付；等部署验收+凭证录入后执行（沙盒宪法禁真调） |

## 测试

- `uv run pytest` → **75 passed**（新增 test_listing_api.py 8 例）×2 稳定；ruff/format/mypy 全绿。
- 渠道全程 MockTransport；网关三模式闸复用（live_test 走替身、dry_run 出快照）。

## 关键实现注记

- **网关传输错误语义**：gateway 吞传输异常返回 status=None（不抛）——submit 必须把
  status=None 当"结果未知"进 verify_pending，绝不能落"渠道拒绝"分支（本单自踩自修）。
- spec 缓存：build_hash=sha256(产品属性+wpt+mode)；SKU/GTIN/价格是 listing 级实例化参数不进缓存键。
- WPT 来源链：product.attrs.wpt > system_config listing.default_wpt；category_map 映射 R2。
- 定价：R1 取 price_snapshot.list 原价直传；pricing_strategy 引擎（cost_plus/min_price 守护）R2。
