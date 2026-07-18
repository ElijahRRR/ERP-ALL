# R2-07 切片 07b 考古 · 方向 B——旧系统 store_incidents 表

侦察范围：`specs/000-founding/data-survey/` 全部存档（只读）。旧系统两个 PG 库的 schema 都在档：`erp_core`（`out/pg_erp_core_schema.sql`）与 `uspto`（`out/pg_uspto_schema.sql`）。**store_incidents 归属 erp_core 库，schema 在档，可逐列抄录。**

---

## 1. 旧 `erp_core.store_incidents` 表——完整抄录（07b 的直系旧图纸）

来源：`specs/000-founding/data-survey/out/pg_erp_core_schema.sql:1258-1277`

| 列名 | 类型 | 约束/默认 | 语义（推断，见末尾未核实标注） |
|---|---|---|---|
| `id` | `varchar(64)` | **NOT NULL**，主键 | 字符串主键（非自增整型），旧系统自造 id |
| `store_id` | `integer` | 可空，FK→stores(id) | 店铺外键，**可空** |
| `store_name` | `varchar(128)` | **NOT NULL**，有索引 | 店铺名字符串，事件主键维度 |
| `cluster` | `varchar(32)` | 可空 | 类目簇（对应 stores.category_cluster） |
| `tier` | `varchar(32)` | 可空 | 店铺分级（对应 stores.tier） |
| `kind` | `varchar(16)` | **NOT NULL**，有索引 | 事件类型（封店/警告/资金冻结等，枚举未核实） |
| `reason_code` | `varchar(64)` | 可空 | 原因代码 |
| `reason` | `text` | 可空 | 原因自由文本 |
| `poa_status` | `varchar(32)` | 可空 | Plan-of-Action（申诉方案）状态 |
| `poa_text` | `text` | 可空 | 申诉方案正文 |
| `next_appeal` | `date` | 可空 | **下次申诉日期**——07b beat 定时提醒的直系旧字段 |
| `fund_pending` | `boolean` | 可空 | 资金是否冻结待放 |
| `fund_amount` | `double precision` | 可空 | 冻结金额 |
| `fund_appeal_date` | `date` | 可空 | **资金申诉日期**——第二个提醒锚点 |
| `resolved` | `boolean` | 可空，有索引 | 是否已解决 |
| `resolved_at` | `timestamptz` | 可空 | 解决时间 |
| `created_at` | `timestamptz` | **NOT NULL** | 创建时间 |
| `updated_at` | `timestamptz` | `DEFAULT now()` | 更新时间 |

**主键**：`pk_store_incidents PRIMARY KEY (id)`（`pg_erp_core_schema.sql:2480-2481`）

**外键**：`fk_store_incidents_store_id_stores FOREIGN KEY (store_id) REFERENCES public.stores(id)`（`pg_erp_core_schema.sql:4024-4025`）——**注意无 ON DELETE 子句**（默认 NO ACTION），与 brand_store_assignments 的 `ON DELETE CASCADE` 不同。

**索引**（共 3 个，`pg_erp_core_schema.sql:3576 / 3583 / 3590`）：
- `ix_store_incidents_kind` on `(kind)`
- `ix_store_incidents_resolved` on `(resolved)`
- `ix_store_incidents_store_name` on `(store_name)`

无唯一约束（除主键），无 CHECK 约束，无 pg enum 类型——所有 `varchar` 列均为自由文本，枚举值不能从 schema 推断。

**行数**：**真实 3 行**（`out/pg_erp_core_rowcounts_exact.txt:34` = `store_incidents|3`）。注意 `out/pg_erp_core_rowcounts.txt:37` 显示 `store_incidents|0`——那是 n_live_tup 估计值，已被 SYNTHESIS 明确点名为陷阱：「n_live_tup 严重滞后（erp_core 实 38k 显示 0）——任何以行数为验收的对账必须 count(*)」（`SYNTHESIS.md:45`）。**以 3 为准。**

**状态分布/样本**：**无法抄录**。`out/` 下没有 store_incidents 的样本 dump（只有 settlement/tro_merged 等表有 sample），3 行数据的实际 `kind`/`reason_code`/`poa_status` 取值**未核实**。

---

## 2. 同库其它相关表（店铺状态 + 品牌占用）

### 2.1 `brand_store_assignments`——旧「品牌占用」表（07b 批量释放的直系旧图纸）

来源：`pg_erp_core_schema.sql:256-267`

| 列名 | 类型 | 约束/默认 |
|---|---|---|
| `id` | `integer` | NOT NULL，自增主键 |
| `brand` | `varchar(256)` | NOT NULL，有索引 |
| `brand_normalized` | `varchar(256)` | NOT NULL，有索引 |
| `store_id` | `integer` | NOT NULL，FK→stores(id) |
| `exclusive` | `boolean` | `DEFAULT true` NOT NULL（品牌独占标志） |
| `active_sku_count` | `integer` | `DEFAULT 0` NOT NULL |
| `created_by` | `varchar(64)` | 可空 |
| `notes` | `text` | 可空 |
| `created_at` | `timestamptz` | `DEFAULT now()` NOT NULL |
| `updated_at` | `timestamptz` | `DEFAULT now()` NOT NULL |

- 主键 `pk_brand_store_assignments (id)`（`:2296-2297`）
- **唯一约束 `uq_brand_store UNIQUE (brand_normalized, store_id)`**（`:2680-2681`）——品牌×店铺唯一占用
- FK `fk_brand_store_assignments_store_id_stores ... REFERENCES stores(id) ON DELETE CASCADE`（`:3776-3777`）——**注意带 CASCADE**
- 索引：`ix_..._brand`、`ix_..._brand_normalized`、`ix_..._store_id`（`:3051 / 3058 / 3065`）
- 行数：**真实 2 行**（`pg_erp_core_rowcounts_exact.txt:9` = `brand_store_assignments|2`）

**关键：此表无 `incident_id` 列、无「占用/已释放」状态列、无软删除标志、无 `team_id`。** 旧系统靠「删行」而非「回链事件 + 状态置为已释放」来释放品牌占用（配合 stores 删除的 CASCADE）。

### 2.2 `stores`——店铺主表（店铺状态字段所在）

来源：`pg_erp_core_schema.sql:1438-1462`。与封店状态相关的列：

- `status varchar(32) DEFAULT 'active' NOT NULL`（`:1449`）——观测到取值 `ACTIVE`/`SUSPENDED`（见 `out/answers/q4_proxy_ledger_sample.txt:18,20` 及 `out/lark/pricing_quota.csv:3,5-9,12-13`；完整枚举集**未核实**）
- `tier varchar(32) DEFAULT 'none' NOT NULL`（`:1450`）、`category_cluster varchar(32)`（`:1451`）——与 store_incidents 的 tier/cluster 冗余对应
- `paused_at timestamptz`（`:1455`）、`terminated_at timestamptz`（`:1456`）、`is_active boolean DEFAULT true NOT NULL`（`:1457`）——旧系统用「时间戳 + 布尔」表达停用/封停，而非单一状态机
- 行数：7 行（`pg_erp_core_rowcounts_exact.txt:39`）

### 2.3 `store_kpi_snapshots`——绩效快照（封店常见诱因，KPI 阈值触发）

`pg_erp_core_schema.sql:1286-1303`。列含 `otd / cancellation / vtr / srr / refund_rate / negative_review / return_rate / inr / composite`，唯一键 `uq_kpi_store_date_src (store_id, snapshot_date, source)`（`:2704-2705`），FK `ON DELETE CASCADE`（`:4032-4033`）。行数 1（`rowcounts_exact.txt:35`）。与 07b 无直接结构耦合，但是「为何封店」的证据来源。

### 2.4 `store_rules` / `store_pricing_rules` / `store_quota_config`

`store_rules`（`:1397-1407`，行数 0）、`store_pricing_rules`（`:1334`，24 行）、`store_quota_config`（`:1380`，0 行）——均与封店工作流无结构关联，仅列出以证「同库无其它 incident/释放相关表」。

---

## 3. 另有：`uspto.walmart_suspension_history`——封店「案例库」（非店铺事件表，勿混淆）

SYNTHESIS 把它标为「封店情报……D-Q33 封店工作流和申诉信的现成弹药」（`SYNTHESIS.md:18`）。但它**属于 uspto 库、是 listing/SKU 粒度的封禁语料库，不是店铺级 incident 表**，与 07b 的 store_incident 是不同物种，需明确区分。

来源：`pg_uspto_schema.sql:1268-1288`。列：`id bigint` PK、`shop text`、`amazon_asin text`、`walmart_sku text`、`feed_id text`、`title / title_clean text`、`product_type text`、`price numeric(10,2)`、`status text`、`status2 text`、`reason_raw text`、`reason_category text`、`reason_subcategory text`、`is_deleted boolean DEFAULT false`、`source_sheet_id text`、`source_date date`、`flagged_at timestamptz`、`imported_at timestamptz DEFAULT now()`。

- 唯一键 `UNIQUE (walmart_sku, source_date)`（`:1828-1829`）
- 索引：asin/date/is_deleted/product_type/reason_category 各一，外加 `idx_wsh_title_trgm` GIN trigram on `title_clean`（`:2242-2277`）
- 配套 `walmart_suspension_embeddings`（`vector(1024)`，`qwen-text-embedding-v3`，FK→history_id ON DELETE CASCADE，`:1254-1259 / 2380-2381`）
- 行数：**75,703**（`pg_uspto_rowcounts_exact.txt:37`；n_live_tup 估计再次显示 0，`pg_uspto_rowcounts.txt:32`）；embeddings 0 行（未生成）

对 07b 的用途：若 store_incident 的申诉信/POA 生成需要历史弹药，可 join 此语料 + 已建好的 embedding 表结构（但 embedding 数据是空的，需重算）。**它不能替代 store_incident，两表并存。**

---

## 4. 与新图纸（07b）的语义差异点

> 新图纸细节我未持有（属方向 A 侦察），以下差异基于任务描述给出的 07b 目标「store_incident 表 + brand_assignment.incident_id 回链 + beat 定时提醒」与旧表结构的硬对比。涉及新表字段处标注推断。

1. **品牌释放机制根本不同（最大差异）**：新图纸要 `brand_assignment.incident_id 回链` + 批量释放，暗示「保留占用行、置状态为已释放、回指触发事件」。旧 `brand_store_assignments` **无 incident_id、无状态列、无软删除**，靠删行 + stores 级 `ON DELETE CASCADE`（`:3777`）释放。新方案是从零加列，旧数据无可迁移的回链关系。

2. **多租户缺失**：旧 `store_incidents` 与 `brand_store_assignments` **均无 `team_id`**。而 CLAUDE.md 铁律 D-Q30 要求「资源默认 team_id 隔离」。新表须补 team_id，旧 3 行 incident / 2 行 assignment 无租户归属，导入需赋默认租户。

3. **主键类型差异**：旧 store_incidents 主键是 `varchar(64)`（旧系统自造字符串 id，`:1259`），新表大概率走整型/UUID 序列——迁移时旧 id 不可直接复用为新主键。

4. **事件与占用无关联**：旧 store_incidents 与 brand_store_assignments 之间**无任何外键**。旧系统「封店 → 释放品牌」是人工两步，无数据链。07b 要把这条链固化进 schema。

5. **store 键维度不一致**：旧 incident 以 `store_name varchar NOT NULL`（`:1261`）为主维、`store_id` 可空（`:1260`）；FK 无 CASCADE（`:4025`）。新图纸若以 store_id/team_id 为准键，需处理旧数据里 store_id 为空、仅有 store_name 的行。

6. **状态模型不同**：旧用离散字段 `resolved(bool) + resolved_at`，店铺侧另有 `paused_at/terminated_at/is_active/status` 四件套（`:1449,1455-1457`）表达封停，**无统一状态机**。新 store_incident 若引入状态枚举，需与 stores.status（观测值 ACTIVE/SUSPENDED，完整枚举未核实）对齐。

7. **提醒锚点已存在但语义窄**：旧表 `next_appeal` + `fund_appeal_date` 两个 date 列（`:1269,1272`）是 beat 定时提醒的天然锚点，但都是「申诉截止日」，没有提醒频率/已提醒标记/提醒渠道字段。07b 的 beat 提醒需新增调度状态列。

8. **kind/reason_code/poa_status 枚举无定义**：旧全是自由 varchar 无 CHECK 无 enum，且 3 行样本未 dump（**未核实**实际取值）。新图纸若要枚举约束，取值集需另定（可回捞 uspto.walmart_suspension_history 的 `reason_category/reason_subcategory` 作参考词表，`pg_uspto_schema.sql:1281-1282`）。

---

## 5. 确定性小结（供 07b 增量1 直接引用）

- 旧 store_incidents schema **在档可抄**，真实 3 行，**无样本数据**（状态分布未核实）。
- 旧品牌占用表 = `brand_store_assignments`（2 行），唯一键 `(brand_normalized, store_id)`，**无 incident 回链、无 team_id、无状态列**——07b 的回链 + 批量释放属净新增，无历史链可迁。
- `walmart_suspension_history`（75,703 行，uspto 库）是 SKU 级封禁语料库，与 store_incident 并存、不可互替；embedding 表结构在档但数据空。
- 全线注意 SYNTHESIS §2.2 的 n_live_tup 陷阱：估计行数表（`pg_*_rowcounts.txt`）对这些低频表全显 0，必须以 `*_rowcounts_exact.txt` 为准。

引用的存档文件绝对路径：
- `/home/user/ERP-ALL/specs/000-founding/data-survey/out/pg_erp_core_schema.sql`
- `/home/user/ERP-ALL/specs/000-founding/data-survey/out/pg_erp_core_rowcounts_exact.txt`
- `/home/user/ERP-ALL/specs/000-founding/data-survey/out/pg_uspto_schema.sql`
- `/home/user/ERP-ALL/specs/000-founding/data-survey/out/pg_uspto_rowcounts_exact.txt`
- `/home/user/ERP-ALL/specs/000-founding/data-survey/SYNTHESIS.md`
- `/home/user/ERP-ALL/specs/000-founding/data-survey/out/answers/q4_proxy_ledger_sample.txt`
- `/home/user/ERP-ALL/specs/000-founding/data-survey/out/lark/pricing_quota.csv`