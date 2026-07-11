# 006 — 审核数据入库与持续维护架构（数据治理）

> Owner 提出：数据不是"一次导入就完事"，需从多角度持续维护——店铺后台查询、邮件、
> 外部收集、外部数据库（USPTO 商标几十 G 在另一台机器、错误商品记录、黑名单库）；
> **飞书表格只是表现形式，不是数据真相源**。本文补齐"多来源持续维护"的完整设计。
>
> **现状诚实交代**：已建**地基**（`import_job` 批量导入通道 + `refdata` schema +
> 黑名单/商标/政策三个导入器，R2-02 前四片）；本文把它扩成体系化的持续维护架构，
> 并据此拆后续工单。此前尚无本方案——现补齐。

---

## 1. 五条核心原则

1. **DB 是唯一 master，飞书是工作台/视图**。飞书方便人肉编辑与查看，但真相在 PG。
   同步有明确方向（下表 §5），绝不让飞书的临时状态污染 master。
2. **每条数据带溯源**：`source`（import/manual/tro_sync/trademark_sync/email/feedback）、
   `reason`、`added_by`、`added_at`、`import_job_id`。任何一条黑名单/商标都能回答"谁、
   何时、依据什么加的"。（黑名单四表已有 source/reason/added_by/added_at。）
2. **大数据增量优先**：商标 14.18M 永不全量重导——USPTO 日度增量 + 断点续传。
3. **版本失效驱动缓存**：数据一变，内存缓存（AC 黑名单索引 / 政策块）按版本键自动重建
   （已实现 blacklist_version=count+max(added_at)、policy version=count+max(updated_at)）。
4. **幂等 + 逐块核对**：重导安全（ON CONFLICT）、声明行数与实到核对（防飞书分页截断）。

---

## 2. 数据域清单

| 域 | master 表 | 规模 | 主来源 | 更新频率 | 消费方 |
|---|---|---|---|---|---|
| 商标 | `refdata.trademark`(+tm_status_code) | 14.18M | USPTO bulk（data.uspto.gov，另一台机 几十G `uspto` 库）| 全量一次 + 日度增量 | 审核 L2-R5 |
| 品牌黑名单 | `app.blacklist_brand` | 3.6万+ | 飞书黑名单品牌 / 品牌明细12.6万(nice_class) / TRO / 商标反查 / 人工 / 反馈 | 持续 | 审核 L0/L2-R4 |
| 卖家/ASIN/类目黑名单 | `app.blacklist_{seller,asin,category}` | 变动 | 店铺后台 / 人工 / 反馈 | 持续 | 审核 L0 |
| 禁售政策 | `refdata.prohibited_policy` | 37 | 飞书 OJSrkV | 偶发 | 审核 L3 静态块 |
| 类目映射 | `refdata.category_map` | 15771 | 飞书"映射明细"2p5sL6（Amazon路径→WPT 多对多，带置信度/匹配方式/来源批次）| 偶发+人工修 | 审核 L1 / 上架 R2-03 |
| PT 元数据/规格 | `refdata.pt_meta` / `pt_specs` | 6832 / 变动 | 飞书 / 官方 MPSetup | 偶发 | L1 / 上架 |
| 错误商品记录（groundtruth）| `app.error_product_record` | 增长 | 店铺后台问题商品 → 飞书"错误商品记录"YlA1sz（9 日报 sheet，12 类错误）| 每日 | 审核回测/反馈闭环/申诉 |
| pt_embeddings（可选增强）| `refdata.pt_embeddings` | 6832 | 由 embedding API 生成 | 随 PT 变 | L1 语义召回（可选）|

---

## 3. 来源 → 通道矩阵

| 来源 \ 通道 | 批量导入 | 外部ETL | 飞书↔DB | 定时同步 | 人工UI | 邮件 | 反馈闭环 |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
| USPTO bulk（几十G）| | ✅ | | ✅日增 | | | |
| 飞书 sheets（黑名单/政策/映射/错误记录）| ✅ | | ✅ | ✅ | | | |
| 店铺后台（问题商品/封店）| | | (经飞书) | ✅ | ✅ | | ✅ |
| 邮件（TRO/投诉/侵权通知）| | | | | | ✅ | |
| 审核结果 / 订单问题 | | | (回写) | | | | ✅ |
| 人工收集（TRO 名单等）| ✅ | | | | ✅ | | |

---

## 4. 七类摄取通道（详设）

### 4.1 批量文件导入 `import_job`（✅ 已建，R2-02 前四片）
dry-run→幂等 upsert→逐块核对→报告。域：blacklist_*/trademark/policy。CLI 在部署机 api
容器内跑，读本地 csv/xlsx/jsonl。**待补域**：category_map、pt_meta/pt_specs、error_record。

### 4.2 外部大数据 ETL（USPTO 商标）
- 源仓 `walmart-trademark-sync` 已成熟：uspto.gov zip→XML→本地 `uspto` PG 5 表（全量+日增，
  `etl_progress` 断点续传，`retry_not_found` 4 策略增强匹配）。
- **ERP 定位**：ERP **不重造 USPTO ETL**。保留 Owner 的 `uspto` 库为上游"数据湖"，ERP 侧
  `refdata.trademark` 是**审核所需子集**（serial_no/mark_norm/status→is_live/nice_classes/
  owner）。同步 = `uspto` 库 → ERP `refdata.trademark` 的**增量投影**（按 updated_at 水位
  取变更行，走 import_job domain=trademark 或专用 sync 作业）。
- 首次：几十 G 只投影审核子集（~14M 行，非全表）；日常：日度增量。断点续传用 `sync_state`
  表记水位（等价 etl_progress）。

### 4.3 飞书 ↔ DB 双向同步
- **飞书 → DB（拉入）**：政策/映射/黑名单/PT。策略随域：政策/映射小表可 TRUNCATE+全量重灌
  （源仓做法）；黑名单增量 upsert（保留 source/reason）。
- **DB → 飞书（回写）**：审核结果回填错误记录表 C/D/E 列（源仓 `_backfill_lark_from_audit_runs`）；
  违规日报。**方向受控**：回写只写 ERP 产出的列，不覆盖人肉列。
- **漂移检测**：定期 snapshot 飞书 + diff baseline（源仓 `_snapshot`/`_diff`），发现人肉误改。
- 统一走 `lark-cli`（CLAUDE.md 已配 Bot 身份）。

### 4.4 定时同步（beat，R2-04 底座）
`schedule` 表驱动：商标日增、飞书拉入、错误记录聚合、飞书回写、漂移检测、缓存 LRU、
GTIN 水位。失败进通知中心 critical。**这是"持续维护"的调度心脏**。

### 4.5 人工录入（UI）
合规员在界面直接加/停一条黑名单（店铺后台发现问题品牌时）。写 source=manual + reason +
added_by，即时生效（版本失效触发缓存重建）。需补：黑名单管理页 + `compliance.blacklist_write` 权限。

### 4.6 邮件摄取
TRO 律所函 / 平台侵权通知 / 投诉邮件 → 解析出品牌/ASIN → 生成**黑名单候选**（不自动入
master，进人工复核队列，避免误杀）。属邮件域工单（R2 后续），依赖邮件域建设。

### 4.7 反馈闭环（审核/订单 → 错误记录 → 黑名单候选）
- 审核 reject / 订单四检命中 / 店铺后台被下架 → 落 `error_product_record`（groundtruth）。
- 错误记录按 12 类归因（过期/禁售/品牌/价格/知产/限类…，源仓分类）；其中**合规类**
  （品牌/知产/禁售）反哺黑名单候选 + L1 验收 groundtruth。
- 这让系统**越用越准**：真实封店/下架数据回流成审核弹药。

---

## 5. 主数据 vs 飞书视图（方向规则）

| 数据 | 飞书→DB | DB→飞书 | 说明 |
|---|:-:|:-:|---|
| 黑名单品牌 | ✅拉入 | — | 飞书是录入台，DB 是 master |
| 禁售政策 | ✅全量重灌 | — | 飞书 OJSrkV 编辑，DB 消费 |
| 类目映射 | ✅拉入 | (可选回写复核结果) | |
| 错误商品记录 | ✅拉入(groundtruth) | ✅回写审核 verdict | 双向：人肉录问题品 + 机器回填判定 |
| 审核结果 | — | ✅回写 | 只写机器产出列 |

**铁律**：飞书的临时/半成品状态绝不覆盖 master；回写只动"机器负责的列"。

---

## 6. 溯源、版本、冲突解决

- **溯源**：每行 source/reason/added_by/added_at/import_job_id。删除=软删（status=removed +
  removed_at），永不物理删（可追溯、可复活）。
- **版本失效**：内存缓存键 = count + max(时间戳)，数据一变自动重建（已实现 AC 黑名单 / 政策块）。
- **冲突**：同一主体多来源 → 优先级 tro_sync/trademark_sync > import > manual > feedback；
  唯一键 upsert，reason 累积记录来源链。

---

## 7. 与已建的映射 + 落地工单

| 能力 | 状态 | 工单 |
|---|---|---|
| import_job 通道 + 逐块核对 + 溯源 | ✅ | R2-02①（done）|
| 黑名单四域导入器 | ✅ | R2-02①（done）|
| 商标导入域（refdata.trademark）| ✅ | R2-02③（done）|
| 政策导入域 + L3 静态块 | ✅ | R2-02④（done）|
| category_map 导入域 + 表 | ⬜ | **R2-02-DG1**（L1 前置）|
| USPTO `uspto`库 → refdata.trademark 增量投影 + sync_state | ⬜ | **R2-02-DG2** |
| 飞书 ↔ DB 同步作业（拉入/回写/漂移）| ⬜ | R2-04（beat）|
| error_product_record 表 + 飞书拉入 + 反馈闭环 | ⬜ | **R2-02-DG3** |
| 黑名单人工录入 UI + 权限 | ⬜ | R2-02-DG4 |
| 邮件摄取 → 黑名单候选 | ⬜ | 邮件域工单 |
| 定时调度心脏（beat schedule）| ⬜ | R2-04 |

---

## 8. Owner 侧：一次性准备 vs 持续动作

**一次性（解锁）**：
1. USPTO `uspto` 库怎么到部署机——同机导入？还是那台机器跑同步把子集推过来？（决定 DG2 形态）
2. 各飞书表 token 已知（错误记录 YlA1sz、政策 OJSrkV、映射 2p5sL6）——确认 lark-cli Bot 有读权限。
3. category_map / 政策 / 黑名单首批导出（走已建/待建通道）。

**持续（beat 自动 + 人肉少量）**：
- 自动：商标日增、飞书拉入/回写、错误记录聚合、漂移检测。
- 人肉：店铺后台发现问题品 → UI 加黑名单；邮件侵权通知 → 复核队列确认。

---

## 附：L1 类目判定方法修正（Owner 澄清）

Owner 原系统类目判定 = **映射表 + LLM 语义理解**（不是向量嵌入）。故 ERP L1：
- **主路径**：category_map（Amazon 路径→WPT 候选）+ LLM 语义复排选最终 WPT。**只需
  category_map 数据 + 现有 LLM，不需新的 embedding API**——L1 因此不卡 embedding！
- **可选增强**：pt_embeddings 语义召回（补映射表未覆盖的品）——后置，非必需。
- 依赖降为：category_map 数据（DG1 通道可导）。详见 `.agent/evidence/R2-02/l1-category-design.md`（已据此修正）。
