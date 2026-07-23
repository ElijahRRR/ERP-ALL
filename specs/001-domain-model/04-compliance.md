# 04 compliance — 黑名单 / TRO / 钓鱼 / 命中记录 / 导入作业 + refdata 大参考数据

> 决策依据：D-Q12（合规数据入库）、D-Q14（软标记+团队级拦截开关）、D-Q35（合规数据经标准导入接口，格式由新 ERP 定义）、D-Q30（合规域可共享）。
> 调研输入：黑名单卖家权威源=飞书第 10 workbook 三列独立去重（SYNTHESIS §1.3）；uspto 库实为多域数据仓（§1.2）；lark 导出静默截断陷阱（§2.1）。

## 黑名单四表（结构同构，分表不分区）

四表共同骨架（以 blacklist_brand 为例）：

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NULL | **NULL=全局黑名单**（超管维护）；非空=团队私有 |
| *主体列* | | 见下表 | |
| reason | TEXT | NULL | |
| source | TEXT | NOT NULL CHECK IN (import, manual, tro_sync, trademark_sync) | |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN (active, removed) | 移除不删行 |
| added_by / added_at / removed_at | | | +公共列变体（created_by=added_by） |

| 表 | 主体列 | 唯一约束（active 内） | 现存量（导入基线） |
|---|---|---|---|
| blacklist_brand | brand_norm TEXT NOT NULL, brand_display TEXT | `(COALESCE(team_id,0), brand_norm) WHERE status='active'` | 2,380（飞书）+ walmart-trademark-sync 增量 |
| blacklist_seller | seller_ref TEXT NOT NULL（Amazon 卖家ID）, seller_name TEXT | `(COALESCE(team_id,0), seller_ref) WHERE …` | 1,308 |
| blacklist_asin | asin TEXT NOT NULL | `(COALESCE(team_id,0), asin) WHERE …` | 18,458 |
| blacklist_category | category_ref TEXT NOT NULL（Amazon 类目） | `(COALESCE(team_id,0), category_ref) WHERE …` | 11,810 |

- 审核 L0/Phase0 内存字典从这四表加载（移植 walmart-audit-system 语义，源=D-Q38）；加载器带版本号 + 变更失效。
- 全局行（team_id NULL）所有团队可见（RLS policy `team_id IS NULL OR team_id = current_team OR shared…`）。

## tro_case TRO 案件

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| case_no | TEXT | NOT NULL | 法院案号 |
| court | TEXT | NULL | |
| filed_date | DATE | NULL | |
| plaintiff | TEXT | NULL | 原告（权利人） |
| law_firm | TEXT | NULL | |
| brand_terms | JSONB | NOT NULL DEFAULT '[]' | 涉案品牌/关键词数组（审核 L2 检索目标） |
| source | TEXT | NOT NULL DEFAULT 'tro-scraper-matrix' | |
| raw_ref | TEXT | NULL | 原始数据引用 |
| status | TEXT | NOT NULL DEFAULT 'active' CHECK IN (active, dismissed, settled) | |
| imported_at / import_job_id | | | |
| created_at / updated_at | | 全局作用域（无 team_id） | 合规基础设施全团队共用 |

约束：`uq_tro_case (case_no, COALESCE(plaintiff,''))`。基线导入 11,893 行（uspto.tro_cases + matched_companies 关联信息合入 brand_terms/plaintiff）。
增量：tro-scraper-matrix 产物经标准导入接口按日进（scraping 域调度）。

## phishing_address / phishing_zip 钓鱼检测数据（订单四检）

| phishing_address 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | BIGINT | PK | |
| address_norm | TEXT | NOT NULL UNIQUE | 归一化地址（欺诈方数据，按调研保留明文） |
| zip | TEXT | NULL | |
| source / status / added_at / added_by | | | 同黑名单骨架 |

| phishing_zip 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id / zip TEXT UNIQUE / source / status / added_at | | | 基线 ~199 行 |

全局作用域。订单四检 phishing 项对 ship_to 做归一化匹配（07 号文档 order_check）。

## compliance_hit 合规命中（软标记，D-Q14）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NOT NULL | |
| object_type | TEXT | NOT NULL CHECK IN (product, listing, order) | |
| object_id | BIGINT | NOT NULL | |
| rule_code | TEXT | NOT NULL | 命中规则（blacklist_brand / tro_brand / phishing_addr …） |
| list_ref | TEXT | NULL | 命中的名单项标识（如黑名单行 id / 案号） |
| severity | TEXT | NOT NULL CHECK IN (info, warn, block) | |
| action_taken | TEXT | NOT NULL CHECK IN (flag, block) | 实际动作：默认 flag；团队开拦截开关后 block（automation_policy flow=compliance_block） |
| details | JSONB | NOT NULL DEFAULT '{}' | 证据 |
| resolved_by / resolved_at | BIGINT/timestamptz | NULL | 人工放行记录 |
| created_at | | append-only | |

索引：`(team_id, object_type, object_id)`、`(team_id, created_at DESC) WHERE resolved_at IS NULL`。

## import_job 标准导入作业（D-Q35 导入接口的 DB 侧）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| team_id | BIGINT | NULL | 全局数据导入为 NULL（超管） |
| domain | TEXT | NOT NULL CHECK IN (blacklist_brand, blacklist_seller, blacklist_asin, blacklist_category, tro, phishing, category_map, gtin, trademark, suspension_case, product, policy, pt_meta, pt_spec, listing_error_catalog) | 一 job 一目标域（policy=0011、pt_meta=0016、pt_spec=0019、listing_error_catalog=0020 陆续扩入） |
| source_kind | TEXT | NOT NULL CHECK IN (file, api) | |
| source_name | TEXT | NOT NULL | 文件名/来源标识 |
| format | TEXT | NOT NULL CHECK IN (csv, xlsx, jsonl) | 模板格式由 ERP 定义并可下载（D-Q35） |
| total_rows / ok_rows / err_rows / skip_rows | INT | NOT NULL DEFAULT 0 | skip=幂等重复 |
| status | TEXT | NOT NULL DEFAULT 'pending' CHECK IN (pending, validating, running, done, failed, cancelled) | |
| chunk_size | INT | NOT NULL DEFAULT 5000 | 分块校验（lark 截断教训：逐块行数核对） |
| verify | JSONB | NOT NULL DEFAULT '{}' | 每块 {expected, loaded} 核对记录 |
| error_report_ref | TEXT | NULL | 错误行报告文件引用（前台可下载） |
| started_at / finished_at | timestamptz | NULL | |
| +公共列 | | | |

导入器契约（EA-002 展开）：dry-run 校验 → 幂等 upsert（各表唯一键）→ 逐块核对 → 汇总报告；**任何一块行数不符即 failed 并回滚该块**。

## refdata schema（大参考数据，导入管道专写、业务只读）

### refdata.trademark USPTO 商标（14.18M）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| serial_no | TEXT | PK | USPTO 序列号 |
| mark_text | TEXT | NULL | |
| mark_norm | TEXT | NULL | 归一化（检索列） |
| status_code | TEXT | NULL | → refdata.tm_status_code 字典 |
| is_live | BOOLEAN | NULL | 由 status_code 派生（LIVE/DEAD） |
| nice_classes | SMALLINT[] | NULL | 尼斯分类数组 |
| owner_name | TEXT | NULL | v1 冗余单列（owners 32M 不整表导入） |
| filed_date / registered_date | DATE | NULL | |
| updated_at | timestamptz | NOT NULL | 同步管道时间戳 |

索引：`ix_tm_mark_trgm (mark_norm gin_trgm_ops)`、`(status_code)`、GIN(nice_classes)。
容量注记：~14M 行 + trgm 索引 ≈ 15-25GB —— **EA-004 RDS 规格的主要输入**。
同步：trademark-data 仓管道产物 → import_job(domain=trademark) 批量 upsert；classes/statements/design_codes 等 uspto 库其余域**不迁移**（审核用不到的留在原库，需要时再评估）。

字典表 `refdata.tm_status_code (code PK, description, is_live BOOL)`（uspto.status_code_mapping 导入）。

### refdata.suspension_case 封店案例库（75,703，D-Q33 申诉弹药）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| id | BIGINT | PK identity | |
| source_ref | TEXT | UNIQUE | 原库行标识 |
| store_hint | TEXT | NULL | 案例店铺特征（非本系统店铺） |
| suspended_at | DATE | NULL | |
| reason_category | TEXT | NULL | |
| reason_text | TEXT | NULL | |
| appeal_text | TEXT | NULL | 申诉信文本 |
| outcome | TEXT | NULL | 申诉结果 |
| embedding | vector(1024) | NULL | 相似案例检索（维度以导出模型为准，导入时定） |
| imported_at | timestamptz | | |

索引：embedding HNSW（vector_cosine_ops）。用途：store_incident 处理页「相似案例」检索 + 申诉信参考。

### refdata.pt_embedding 审核 L1 检索嵌入（6,832）

| 列 | 类型 | 约束/默认 | 说明 |
|---|---|---|---|
| wpt | TEXT | PK | Walmart Product Type |
| embedding | vector(1024) | NOT NULL | 维度以 walmart-audit-system 导出为准 |
| meta | JSONB | NOT NULL DEFAULT '{}' | |
| updated_at | timestamptz | | |

移植说明：walmart-audit-system 的 L1 混合检索（关键词+向量）依赖表；从源库导出直灌。

---

**已落地注记（R2-12 增量1 / RS-04D，2026-07-23）**：①新增 `app.blacklist_assertion`
断言账本（0035）——一主体 N 条源断言（verdict block/allow，allow 仅 manual 源；状态机
pending/active/revoked append-only），六张黑名单表的 active 行降级为 canonical=有效断言
投影（人工 allow 裁决压一切自动源，优先级 manual>tro_sync>trademark_sync>error_recycle>
import——D-Q65 P1）；存量 active 行已按原 source 回填断言，canonical 可由账本全量重建
（评审 B5① 四条硬验收有测试覆盖）。②本页四表 `source` 枚举扩 `error_recycle`
（D-Q65② 报错回收候选源）。③`tro_case` 表按本页 :30-48 建成（0035，此前 DDL 缺失）。
④黑名单导入通道改记 import 源断言（不再直写 canonical）。合规权限点
`compliance.blacklist_read/write、trademark_read、tro_read` 已种。

**已落地注记（R2-12 增量2 / TRO 链，2026-07-23）**：`tro` 导入域上线
（import_service `_apply_tro_row`，CLI `--domain tro` 同步可用）——tro_case 幂等
upsert（键 `(case_no, COALESCE(plaintiff,''))`，brand_terms 原文无损存 jsonb，
import_job_id 留痕）；active 案的 brand_terms 逐词派生**全局**（team_id NULL）
`tro_sync` 品牌断言（source_ref=case_no，归一走 _norm 与 L0/L2 一致，占位符词跳过）；
dismissed/settled 案撤销该案全部在册 tro_sync 断言并重投影（余源在册不误删——B5①
验收②语义，`assertion.revoke_by_source_ref`）。L2 命中复现有测试
（test_tro_import：scan_blacklist 自动机命中派生词）。上游采集（tro-scraper-matrix）
按 D-Q65① 整链驻部署机，向本域喂 jsonl/csv。
