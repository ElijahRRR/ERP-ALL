# R1-09 本地验证记录（2026-07-10）

## 验收对照（specs/003 §R1-09）

| 验收项 | 结果 | 证据 |
|---|---|---|
| scrape_job/task/result 三表 + worker_node | ✅ | 0007 迁移，升→降→升演练通过；task/result 月分区自动建 |
| worker 拨入协议（注册/心跳/领任务/回传） | ✅ | /worker/v1/* 五端点；node_key+token 认证 + enroll 注册闸 |
| 建 job(单 ASIN)→派发→回传→product upsert | ✅ | test_full_minimal_loop / test_product_in_db_and_dedup_no_status_reset |
| product 表出现该 ASIN、job 计数正确 | ✅ | master_sku=M{7位}格式断言；done/failed/total 计数 + partial 状态断言 |
| worker 断连任务回收 | ✅ | test_disconnected_worker_reclaim_and_stale_reject（心跳倒拨→回收→重领 attempt+1→旧租约 stale 拒收） |

## 测试

- `uv run pytest` → **56 passed**（新增 test_scrape_api.py 10 例）×2 次稳定。
- ruff check / ruff format --check / mypy strict 全过。
- 基线权限计数 36→39（0007 补种 scrape.* 3 码），test_baseline 断言同步更新。

## 去重协议验证（001 §03，D-Q31）

同 ASIN 二次采集：title/price_snapshot/attrs 刷新，status 保持 audit_passed
（人工改状态后重采不回退），行数不翻倍。

## 说明

- 真实采集 worker 引擎（curl_cffi TLS 指纹/AIMD/session 池）不在 R1-09 范围：
  本单交付 server 侧协议 + 容器内模拟拨入（验收允许），引擎移植随选品功能排期。
- 回收触发点 = sync/pull 顺带执行；beat 定时兜底为 R2 项（schedule 表已就位）。
