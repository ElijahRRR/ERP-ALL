# 位置未知资产答复（README §四）

> 2026-07-09 本机只读调研。四题均已定位或确认不存在；样例见 `out/answers/`。
> 搜索覆盖：9 个飞书 workbook 的全部 sheet 名、`~/Projects` 全部兄弟项目、
> `~/Downloads` `~/Desktop` `~/Documents`、本机 8 个 PG 库（erp/erp_core/uspto/walmart_audit/
> walmart_cleanup/walmart_os/signal_engine/postgres）的 pg_tables、Apple Notes 标题。

## Q1 黑名单卖家 —— ✅ 找到（飞书为权威源 + 两级 PG 派生镜像）

**权威源**：飞书「Amazon 选品黑名单」wiki `QNIpwrRWxiYPeVk86f8cemyenBb`
（spreadsheet `BV25suzM7htNtBte91jc40iXnvg`，sheet `8280e8`，单 sheet）。

- 表头：`A=黑名单卖家店铺ID  B=黑名单ASIN  C=黑名单类目`——三列功能独立，不强求同行都填，
  各列独立去重后各灌一张 PG 表。
- 规模：A 列 ~1,308 个独立 seller_id（网格 18,571 行由 B 列 18,482 个 ASIN 撑起）。
- 同步链路：飞书 →（每日 07:05 `新审核系统/sync/sync_phase0_blacklist.py`，TRUNCATE 全量重灌）→
  `walmart_audit.phase0_blacklist_sellers`（1,308 行，同表族 asins=18,458、amazon_cats=11,810）→
  `walmart_os.blacklist_sellers`（1,271 行，source='walmart_audit-phase0'，略滞后）。
- 样例：`out/answers/q1_blacklist_sellers_sample.txt` + `…_addendum.txt`。
- **新 ERP 建议**：合规域认飞书 A 列为权威源，两份 PG 表均为派生镜像，迁移以飞书为准。
  勿与黑名单品牌（WvPTz2 → uspto.blacklist_brands）及黑名单地址/邮编（ZLUqxi/NOn5x7，客户侧钓鱼检测）混淆。

## Q2 店铺收款账户资料 —— ⚠️ 部分找到（无集中台账，碎片化 4 处）

**核心结论：真正的「店铺 ↔ Walmart 打款收款账户」结构化台账不存在**，账户资料只在 PingPong 后台。
本机现存的碎片：

1. **PingPong Card 虚拟卡导出**（消费卡，非收款账户）：`~/Desktop/pingpong/PingPongCard*.xlsx`、
   `~/Desktop/公司/PingPongCard.xlsx`、`~/Downloads/pingpong卡.xls`，4 份约 20+ 张卡，
   表头 22 列（编号/卡号/卡类型/卡状态/备注名/CVC/有效期/总额度/币种/申请时间/…/单次取现额度）。
   卡与店铺的关联**仅靠「备注名」自由文本手填**（如 `环境73`、`A147广告卡`），无独立映射表。
2. **walmart_settlement.db::settlement_snapshots**：每店 `payment_processor=PingPong`、
   `settle_cycle=Bi-weekly`——证明渠道存在，但无账号资料。
3. **PG erp_core.payout_accounts**：空表，仅目标 DDL
   （id/name/account_masked/type/stores jsonb/kyc/status/month_income/pending/frozen/created_at）——
   与需求对齐良好，但**现存系统无源可迁**，初始数据需从 PingPong 后台人工/API 导入。
4. `~/Desktop/Super Browser/<店铺环境>/银行证明文件.pdf`（非结构化）。

样例（已打码：卡号留后 4 位，CVC/有效期全掩码）：`out/answers/q2_payout_accounts_sample.txt`。
**Owner 需确认**：PingPong 收款账户（Seller 账户，非虚拟卡）与店铺的对应关系是否只在 PingPong 后台。

## Q3 1688 货源对应记录 —— ✅ 找到（仅一份人工 Excel，极初期）

**位置**：`~/Downloads/沃尔玛WFS选品.xlsx`（WPS 生成，2026-05 前后），两个 sheet：

- 「工作表1」（表头第 2 行，8 条数据，8 条含 1688采购链接）：产品名称/定位、sku、当前进度状态、
  预估WFS售价、拿货成本、头程运费预估、WFS综合费用、预估单件净利、预估毛利率、**1688采购链接**、
  重量/包装/外箱尺寸、对标差异与打法调整、下一步行动、风险排查记录。
- 「工作表1 (2)」（表头第 1 行，10 条数据，6 条含 1688产品链接）：ASIN、产品名称、核心痛点、
  运营综合分析、卖家精灵数据分析、亚马逊售价、**1688产品链接、价格**、包装/外箱、预估成本、
  采购(含供应商寄渝运费)、头程运费(15/kg)。
- 合计约 18 条、**14 条有效「产品↔1688 链接」对应**；「价格」列为自由文本
  （如"18.5/盒，含税，不含运费"，个别行填的是风险备注），字段不结构化。

样例：`out/answers/q3_1688_sourcing_sample.txt`。
无飞书表、无数据库落地；`erp服务`/`沃尔玛操作台` 已有 SourceAdapter 抽象（AlibabaAdapter 标注
Phase ≥3 未实现），与 D-Q25 方向一致。**新 ERP 的 product_sources 需从零建模**，字段可参考该
Excel（拿货成本/含税与否/运费口径/外箱规格/进度状态）。

## Q4 代理 IP 台账 —— ❌ 未找到（采购/到期/续费维度不存在）

所有已知源中代理 IP 只有两类数据，**均无采购日期/到期/续费/供应商/价格字段**：

1. **绑定关系 + 连接凭证**：飞书 `X4vMwQ…/40383c`「店铺API」（197 网格行 × 21 列，K~T 为 A~J 的
   重复镜像块；I/J 为无名列=运营模式/店铺状态）；本地 `店铺API.xlsx` Sheet1（60 行，飞书旧快照）
   + **Sheet2（55 行，按"设备名称"维度的代理清单——最接近台账的东西，飞书版没有此表）**。
2. **运行时健康状态**：PG `erp_core.proxies`（10 行，爬虫 worker 代理监控，与店铺代理非同批资产，
   不宜直接复用作 D-Q34 资产表）。

线索：`~/Downloads/YiLuProxy.rar` 暗示代理经易路代理客户端管理，采购/到期信息大概率只在
代理供应商后台。样例（账密打码）：`out/answers/q4_proxy_ledger_sample.txt`。
**Owner 需口头确认**：代理供应商及其后台能否导出采购/到期记录；D-Q34 到期提醒数据需从零建立
（`店铺API.xlsx` Sheet2 的 55 行设备维度清单可作初始种子）。
