# RS-04A 100 万行演练记录（沙盒，2026-07-12）

工具：`erp.tools.bulk_import_trademark`（流式 → COPY UNLOGGED staging → set-based merge，
批 50k 按批提交，manifest 断点续跑）。数据：合成 USPTO 形态 csv 68MB / 100 万行。

## 实测（沙盒容器 PG16，单盘）

| 指标 | 值 |
|---|---|
| 总时长 | **48s（20,823 行/s）** |
| 吞吐曲线 | 26k→20.8k 行/s，索引增长期小幅下降后**平稳无衰减** |
| WAL 流量 | 1,142 MB / 1M 行（staging UNLOGGED 免 WAL；来自目标表+索引） |
| 表+索引增量 | 257 MB / 1M 行（pg_total_relation_size） |
| 内存 | 单批 buffer（50k 行），与文件大小无关 |
| 校验 | merged=staged_distinct 逐批核对全过；err=0；manifest+sha256 落盘 |

## 14.18M 外推（容量预算，评审 R2-17 前置项）

| 项 | 预算 | 备注 |
|---|---|---|
| 导入时长 | 沙盒口径 ~12 min；部署机（Win11 Docker 单盘）打 2-3 倍 → **~25-40 min** | 一次性初始载入 |
| 表+索引 | **~3.6 GB** | serial PK + mark_norm 索引 |
| WAL 总流量 | ~16 GB **流量**（非占用） | 284 批提交间 checkpoint 循环回收，峰值占用 ≤ max_wal_size；部署机建议临时调 max_wal_size=2GB 加速 |
| 磁盘峰值 | 源 csv ~1 GB + staging 瞬时 ≤1 批 + 表 3.6 GB → **预留 ≥8 GB 富余** | staging 每批 merge 后即删 |
| 备份影响 | 当日 pg_dump 增 ~2-3 GB（压缩后） | 首次导入后的当晚备份窗口注意 |
| 全量重导 | 幂等但会重写全部行（再 ~16GB WAL 流量）——**增量更新走 RS-04B 同步框架**，不要反复全量重导 | |

## 与验收项对照

- [x] 100 万行演练：吞吐/内存/WAL/表膨胀量化 ✅（本文件）
- [x] 中断续跑：db 测试故障注入 2 批中断 → --resume 补齐（test_bulk_interrupt_and_resume）
- [x] 重复导入不增行（test_bulk_idempotent_reimport）
- [x] manifest+checksum 落盘 + sha256 变更拒绝续跑
- [ ] 14.18M 实测——待 Owner 提供真实 USPTO 导出（部署机执行）
