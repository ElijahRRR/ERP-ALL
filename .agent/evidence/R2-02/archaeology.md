# R2-02 考古：审核弹药灌入（分片交付）

源仓：/workspace/walmart-audit-system。R2-02 = 数据灌入 + 代码补齐（见 specs/005 R2-02）。
本单是**第一片：import_job 数据载具 + 黑名单四域导入器**——所有弹药（黑名单/商标/政策/
category_map）都经此通道进库，是整单的地基。

## 为什么先做 import_job

R2-02 的四类数据（黑名单 3.6万 / 商标 14.18M / 37 政策 / pt_embeddings 6832）都要"导入"，
而 D-Q35 规定统一走 import_job 标准通道（dry-run→幂等 upsert→逐块核对→报告）。没有这个
载具，任何数据都进不来。故先建通道 + 最高价值域（黑名单，L0 品牌/卖家/ASIN/类目直接命中）。

## import_job 契约（specs/001 §04 + lark 截断教训）

- 一 job 一目标域；domain CHECK 一次覆盖全集（blacklist_*/tro/phishing/category_map/
  gtin/trademark/suspension_case/product），加新域不改表。
- **源截断守卫**：create 时声明 total_rows，import 时实收行数必须相符，不符即 failed。
  （旧系统踩过 lark 分页只回部分行、静默入库缺数据的坑）
- **逐块核对**：每块记 {expected, loaded} 进 verify JSONB，块内行数不符即 failed。
- 幂等 upsert：ON CONFLICT (COALESCE(team_id,0), subject) WHERE status='active' DO NOTHING
  → 有返回=新增(ok)，无返回=已存在(skip)。重跑安全。

## 归一化锁死（唯一必须保真的不变量）

四个黑名单域的主体全部走 `audit.pipeline._norm`（lowercase + 多空格压一）——因为审核 L0
查表也用 `_norm`（seller_ref/asin/category_ref/brand_norm 四处 `_blacklist_lookup` 全部
`_norm(value)`）。导入与查表归一化必须字节一致，否则导进去的词审核时查不到。
test_import_job.test_imported_brand_hits_l0_lookup 用大写+多空格变体反查锁死这一点。

品牌占位符跳过：`NON_BRAND_PLACEHOLDERS`（unbranded/generic/oem/无品牌…）不入黑名单，
否则会把 6/10 无品牌 Amazon 产品全 L0 拦掉（源仓 phase0_brand 同款白名单，R1-10 已移植）。

## 交付物

- migration 0010：import_job 表（月无分区，量小）+ RLS + compliance.import_read/admin 权限（→44）
- erp/compliance/import_service.py：create_job / import_rows（分块+核对+计数）/ mark_failed
- erp/compliance/router.py：GET /import-jobs[/{id}]（只读进度，权限 compliance.import_read）
- erp/tools/import_blacklist.py：CLI（csv/xlsx/jsonl→system_tx 灌入），部署机 api 容器内跑
- tests/db/test_import_job.py：6 用例（幂等/占位符/空主体/行数不符/四域/归一化查表一致/verify）

## R2-02 后续片（未做，挂后续 session）

- **黑名单全量导入**（Owner 执行）：飞书 3.6万 品牌/卖家/ASIN/类目 → 本通道
- 商标域导入器 + refdata.trademark 14.18M（uspto→部署机 PG，走同通道 domain=trademark）
- 37 条政策全量 → L3 system prompt 静态段（吃 provider cache，见 R1-10 archaeology §L3）
- L1 类目判定（pt_embeddings + 混合检索 + LLM 复排）+ L2 R1/R2/R3/R7/R8
- R4 升级为 Aho-Corasick + 黑名单内存字典加载器（版本失效）
- **L1 验收**：旧系统 4326 ASIN 子集 ≥100 重跑，verdict 一致率 ≥90%
