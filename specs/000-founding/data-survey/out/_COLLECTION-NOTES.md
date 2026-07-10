# 采集备注（脚本产物之外的修正与说明）

> 2026-07-09 本机执行。所有操作只读（SELECT / .schema / pg_dump --schema-only / lark-cli 读接口）。

## 与 survey_local_data.sh 脚本的偏差

1. **PG 行数统计**：脚本用 `pg_stat_user_tables.n_live_tup`（统计信息，**严重滞后**）——
   如 erp_core 全部表显示 0 行但实际 `products_master` 38,119 行；uspto `trademarks` 显示 45k 实际 14.18M。
   已补 `pg_*_rowcounts_exact.txt`（逐表 `count(*)` 精确值）。**看行数请以 `*_exact.txt` 为准**，
   原 `pg_*_rowcounts.txt` 保留仅供对照。
2. **walmart_cleanup.error_items**：精确行数 452,086（n_live_tup 只报 30k）；category 分布合计与精确值吻合。
3. **TRO merged.db**：表名是 `tro_cases` 而非脚本猜的 `cases`，已手工补 source 分布
   （`sqlite_tro_merged_extra.txt`）与每站点 2 行样例（`sqlite_tro_merged_sample.txt`，
   截断 title/trademarks 长列）。`trademark_images` 表 0 行。
4. **upc_history.db**：表名是 `upc_history` 而非脚本猜的 `upcs`，已手工补 walmart_status 分布
   （`sqlite_upc_history_extra.txt`：unknown 94,638 / conflict 6,696 / free 3,354）。
5. **retry_state**：脚本未含 kind 分布，已补（`sqlite_retry_state_extra.txt`）。
6. **settlement**：脚本只取了第一张表（settlement_snapshots）样例，已补 `recon_details` 样例
   （`sqlite_settlement_recon_sample.txt`，**刻意排除 ship_to_city/state/zipcode 收货地列**——虽为
   城市/州级非精确地址，仍按隐私从严处理；customer_order 为订单号非个人信息，保留）。

## 飞书导出说明（out/lark/）

- 全部经 `lark-cli --as bot` 只读命令（+workbook-info / +csv-get）完成；行数汇总见 `lark/_rowcounts.txt`。
- **UPC 池坑**：`+csv-get` 单次读约 50 万字符上限且**静默截断**（40k/10k 行分块实测缺行无报错），
  最终 5,000 行/块 × 30 块导出并逐块校验，148,328 数据行无缺失。
- 黑名单地址表（phishing_addr.csv）前 4 行是横幅/说明行，**真正表头在第 5 行**。
- 定价表（pricing_quota.csv）为双行表头（第 1 行分组 + 第 2 行区间），数据自第 3 行起。
- 按 README §五 补了在线产品总表**仅表头行**（`lark/online_products_header_only.txt`，148k 全量未导）。

## 隐私处理

- 各样例已逐个人工复查：无客户姓名/精确地址/邮箱/电话。
- 店铺代号（A085朱丽霖 等）为内部业务标识，按任务书规则保留。
- 黑名单地址/邮编为钓鱼/取证下单方（欺诈侧）数据，非客户 PII，原样保留。
- answers/ 下样例：PingPong 卡号只留后 4 位、CVC/有效期全掩码；代理 IP 登录账密打码。
