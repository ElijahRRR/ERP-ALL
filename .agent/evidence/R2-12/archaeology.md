# R2-12 合规数据供给持续化 · 考古综合（2026-07-23）

> 四路并行侦察（原始报告 raw-A/B/C/D.md）：断言账本设计输入 / 黑名单导入域触点 /
> 全店 SKU 对账素材 / 前端契约缺口。旧侧四链源码 007 已核实，本轮只挖新系统侧。
> 本文=综合结论 + 增量拆分 + P0 拍板清单。

## 1. 核心结论

1. **RS-04D 是全单地基**：图纸与现表都是「一行一 source、active 唯一」单源模型（0008
   uq_brand_active），三源并存/单源撤销/人工裁决/canonical 重建（B5① 四条硬验收）无处
   安放——需新表 blacklist_assertion（一品牌 N 条源断言，canonical 由投影重建）。黑名单
   「候选→人工确认」闭环（报错回收需要）现无 pending 态，正好由账本的断言状态承载，
   不必另建候选表。
2. **tro_case 表 DDL 缺失**（spec-only；phishing_address/zip、compliance_hit 同）——TRO 源
   断言缺上游，建表随本单（图纸 001 §04:30-48 已有完整列定义，ar 帽 0035）。
3. **导入写路径需小改**：import_service._apply_row 硬编码 source='import' 且 DO NOTHING
   首写者赢（:349-362）——接账本后改为「按域记源断言」，canonical 投影替代直写。
4. **USPTO 增量通道现成**：bulk_import_trademark（COPY staging+merge ON CONFLICT(serial_no)
   +manifest 断点+revision bump）天然支持增量文件幂等导入；R5 商标读侧直查无缓存，无需
   额外失效。同步 psycopg 工具，周期化宜由部署机跑旧 daily_update 导出+新系统导入。
5. **全店对账三个关键事实**：GET /v3/items 带 query 必须显式 endpoint_key="GET /v3/items?q"
   （60/min 桶；缺省剥 query 会落 300/min 桶撞 429）；items 翻页语义特殊（nextCursor=
   会话 ID，真翻页靠 offset）；旧 13 类归类表与永久禁售六类（B/C/E/F/G/K）全套可考据。
   新系统 maintenance_task/listing_error_catalog 框架已建未接 runner——对账任务只读落
   差异，执行走 runner（读写分离）。
6. **合规页从零但地基全在**：契约/权限点/页面三缺；DB 四表+trgm/nice 索引就绪。
   附带发现：契约 import-jobs 权限码 catalog.import_* 与 DB 已种 compliance.import_*
   不一致（需契约修正）；import 执行仅 CLI 无 HTTP 上传（合规页上传需补端点）。

## 2. 增量拆分

1. **增量1（RS-04D 断言账本，地基）**：0035 迁移（blacklist_assertion + tro_case 建表 +
   合规权限点种子）+ 断言服务（三源写入/单源撤销/人工裁决压自动源可回滚/canonical 全量
   重建投影，append-only）+ 导入路径改记断言（source 按域传入）+ blacklist_brand 真实域
   验收测试（B5① 四条全测）。canonical 保持 blacklist_brand 表形态与 L0 失效契约不变。
2. **增量2（TRO 链）**：import domain 'tro' 导入器（tro_case 入库 + brand_terms 派生
   tro_sync 断言走账本）+ L2 命中复现测试。
3. **增量3（USPTO 链）**：部署机日增量导出 runbook 指令块（旧 daily_update→csv）+
   bulk_import_trademark 增量实测 + beat trademark_freshness 新鲜度告警任务（max(filing_date)
   滞后阈值配置中心）。
4. **增量4（全店对账+报错回收）**：item_pull 三段式 beat 任务（publishedStatus 逐态+offset
   翻页+显式 endpoint_key）+ 三类差异落点（缺行 upsert/漂移 transition+maintenance_task/
   错误 SKU 分类进 error_catalog）+ 永久禁售类→黑名单候选断言（人工确认）。
5. **增量5（合规中心页）**：契约新增（四黑名单管理含断言追溯/商标查询 trgm+nice/
   tro-cases/import 上传+error-report 下载 + import 权限码对齐修正）+ CompliancePage
   （照 ListingsPage 骨架）+ 导航接线。

## 3. P0 拍板清单（阻增量2/3/4 口径，不阻增量1）

- **P0-1 USPTO daily_update 运行位置**：建议=部署机继续跑旧 walmart-trademark-sync
  daily_update（uspto 库）导出日增量 csv → 新系统 bulk_import_trademark 导入；新系统
  beat 只做新鲜度告警。备选=整链移植进新系统（大工程，收益低）。
- **P0-2 报错回收自动化档位**（D-Q13/29 三档）：建议=对账任务只发现+分类+生成维护任务
  与黑名单候选断言（人工确认落库）；DELETE/republish 执行走 maintenance_task runner
  人工/半自动档（runner 本身随增量4 最小接通 delist/end_date_renewal 两种即可）。
  旧系统为全自动 DELETE+入黑名单——是否保留人工闸请拍板。
- **P1 自定**：blacklist_assertion 的 canonical 投影优先级默认=manual>tro_sync>
  trademark_sync>import（人工裁决压一切）；候选断言态命名 pending（人工确认→active）。
