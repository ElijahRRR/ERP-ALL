# R2-14 生命周期出口 —— 14a + 14c 考古（2026-07-29）

> Owner 2026-07-29 指定开工 **R2-14 的 14a + 14c**（与 R2-13）。
> 口径源：`specs/001-domain-model/00-conventions.md §7.1`（三级规则 + 两张墓碑表）、
> `specs/007-mvp-completion-plan/README.md:261`（分片 14a–14d）、MVP 路径序 **2.7**（D-Q70）。
> 本轮范围：**14a 产品删除 + 14c 列表折叠**。14b（主体删除）/ 14d（权限与留痕）紧随，不在本轮。

---

## 〇、**最要紧的一条：图纸的墓碑键与代码的去重键对不上，照图纸做验收②必失败**

图纸 §7.1 写：

> `deleted_product (team_id, **asin**, reason, deleted_at, deleted_by)`——……同时
> **去重键 `(team_id, asin)` 仍认得该 ASIN，不会下次采集又抓回来**

> ## ⚠️ 本节 v1 的定性是错的，v2（2026-07-29 同日）已更正
>
> v1 称「图纸的墓碑键**指向一个不存在的列**」并与 `ERP_ENV`/`O-1..O-6` 并列。
> **那个类比错了**：`asin` 在文档里**有明确定义**——`03-catalog.md:14` 写
> 「`source_ref` = ASIN（或未来其他源主键）」，宪法 `business-rules-ledger.md:44`
> （BR-CAT-002）写「**ASIN 降级为 `(source_channel, source_ref)` 属性**」。
> v1 的检索面只覆盖 `00-conventions` 与迁移文件，**漏检了定义所在的那两处**。
>
> **技术结论未变**（键仍为 `(team_id, source_channel, source_ref)`），**依据变了**：
> 从「纠正图纸错误」改为「**按宪法把简写展开**」。详见
> `.agent/evidence/R2-14/annotation-tombstone-key.md` 的 v2 更正说明。

**实测**：

| 事实 | 复算命令 | 结果 |
|---|---|---|
| `product` 表无名为 `asin` 的列 | `psql -c "SELECT column_name FROM information_schema.columns WHERE table_schema='app' AND table_name='product'"` | 19 列，无 `asin` |
| 「asin」的定义处① | `sed -n '14p' specs/001-domain-model/03-catalog.md` | `source_ref` = ASIN（或未来其他源主键） |
| 「asin」的定义处②（宪法） | `sed -n '44p' specs/000-founding/business-rules-ledger.md` | ASIN 降级为 `(source_channel, source_ref)` 属性 |
| 真实唯一键 | `grep -n "uq_product" backend/alembic/versions/0007_scrape_catalog.py` | `uq_product UNIQUE (team_id, source_channel, source_ref)` |

入库点 `scrape/service.py:523` 的 upsert 也确认：

```sql
INSERT INTO app.product (team_id, source_channel, source_ref, ...)
ON CONFLICT (team_id, source_channel, source_ref) DO UPDATE SET ...
```

**真正的风险不在图纸错，而在简写到了落地文档就只剩字面。** `§7.1` 是「三级删除规则」的
落地口径文档——读它的人是要照着建表的，未必会回溯到 `03-catalog` 或宪法去查「asin」
的定义，**而恰恰是那两处带着「（或未来其他源主键）」这个关键限定**。于是「照字面建一个
`asin` 单列」成了一条自然而然的路，代价是：

① `asin` 这一列在 product 侧没有对应源，只能从 `source_ref` 派生
（`source_channel='amazon'` 时它恰好是 ASIN——**这正是 `03-catalog.md:14` 那句限定要防的**）；
② 丢掉 `source_channel` 后墓碑查不中真实唯一键，**「删掉的商品下次采集又抓回来」照样发生**
——而这正是验收②唯一要验的东西；
③ 多渠道时更糟：`(team_id, asin)` 会让**渠道 A 删的商品把渠道 B 的同号商品一起挡住**，
**静默误杀比回流更难发现**（回流至少看得见，误杀是「本该进来的没进来」）。

**云端侧处置（不擅改图纸，走批注回传）**：
- 实现按 BR-CAT-002 把简写展开落地：
  `deleted_product (team_id, source_channel, source_ref, reason, deleted_at, deleted_by)`，
  唯一键与 `uq_product` 严格同形；
- **另开批注回传请审计侧在 §7.1 补一句指向定义**
  （`.agent/evidence/R2-14/annotation-tombstone-key.md`）——不是改结论，是防照字面实现；
  图纸正文归审计侧，云端侧只提请，不自己动笔。

> **前端佐证这层映射一直是自洽的**：`ProductsPage.tsx:197` 是
> `{ title: 'ASIN', dataIndex: 'source_ref' }`；`ProductDetailDrawer.tsx:58` 用
> `source_channel === 'amazon'` 做条件才拼亚马逊链接——**前端自己就知道
> 「`source_ref` 只有在亚马逊渠道时才是 ASIN」**。契约 002 的 `Product` 同样只有
> `source_channel` / `source_ref`。文档、契约、代码、前端四处一致。

---

## 一、14a 产品删除

### 1.1 现状：**全仓零 DELETE 端点**（图纸称零，实测确认）

`grep -rn "\.delete(" backend/src/erp/ --include=*.py`（排除 `session.delete`）→ **零命中**。
图纸把这件事记为缺口的证据：清理 team 2 的 400 个产品只能由部署 AI 手写 SQL 直连生产库
+ 四道闸——**系统本身没有出口，才让例行清理变成高风险手术**（本会话确实这么干过一次）。

### 1.2 三级规则落到本单

| 级 | 判定 | 动作 |
|---|---|---|
| ① 无历史 | 从未上过架 | 物理删除，**不写墓碑**（本就没有历史可指向） |
| ② 有历史 | 上过架 | 实体行物理删除 + 写 `deleted_product` 墓碑 |
| ③ 绝不删 | `audit_log` / `financial_event` / `ledger_entry` / 订单与售后（D-Q18） | 一行不删，且**判据要显式断言行数不变** |

**「有历史」的可判定定义**（图纸只说"上过架"，需落成代码判据）：产品被 `listing` 引用即为有历史。
理由：`listing` 才是"上过架"的事实表；`variant_member`（归组）与 `listing_spec`（listing 的规格产物）
都不是独立的历史来源——但它们是 **NOT NULL 外键**，删除时同样会挡路，需先处置。

### 1.3 外键实测（决定实现难度，图纸已给、本轮复核确认）

三处 `NOT NULL REFERENCES app.product(id)`：

| 表 | 位置 | 性质 |
|---|---|---|
| `variant_member` | `0007_scrape_catalog.py:101`（`product_id` **NOT NULL UNIQUE**） | 归组关系，删产品应连带删该行 |
| `listing` | `0009_listing.py:80` | **上过架的证据**——有它即走②级 |
| `listing_spec` | `0009_listing.py:219` | listing 的规格产物，随 listing 处置 |

无 `ON DELETE CASCADE`，故②级删除必须**显式按序清理引用**，或给这些外键补 CASCADE。
**倾向显式清理而非加 CASCADE**：CASCADE 会让将来任何一次误删静默扩散到 listing 域，
而显式清理在代码里看得见、在判据里挡得住。

### 1.4 本单必须自带的两样（形式上属 14d，但不能等）

图纸把「权限点 + 二次确认 + 删除写 `audit_log`」划给 **14d**。**但 14a 若不自带，
等于先上线一个「谁都能删、删了查不到」的出口**——这比没有出口更糟。故本单：

- **删除动作走独立权限点**（新增权限码，随迁移种子 + 授团队管理员，模式抄 `0039`）；
- **删除必写 `audit_log`**（含 before 快照）——`AuditWriter` 是现成的，
  `channel/router.py:551` 的 `channel.incident_create` 是模板，成本约 3 行。

**二次确认属前端**，随 14c 的前端改动一并做。

---

## 二、14c 列表折叠

图纸原话：**「折叠比删除更快见效，二者都要」**，且 007 注明 **14c 可独立先上**。

要点：各列表**默认隐藏已停用/已归档项**并提供「显示已停用」开关。
产品侧的"已归档"对应 `product.status`（合法值见 `0012`：
`ingested/auditing/audit_passed/audit_rejected/needs_review/sourcing/ready/listed/retired`），
**`retired` 即已归档**。

> 待确认（实现时逐页核，不在考古里猜）：除产品页外还有哪些列表需要同款开关
> （店铺 `suspended`、代理、用户、采购方…）。**本轮先做产品页**，其余随 14b/14d 收口——
> 理由是 007 说 14c「可独立先上」，而 MVP 口径下**产品列表最先变垃圾堆**。

---

## 三、增量拆分

| 增量 | 内容 | 备注 |
|---|---|---|
| **14c-1** | 产品列表默认隐藏 `retired` + 「显示已停用」开关（后端查询参数 + 契约 + 前端） | **可独立先上、零风险**，先落它把「更快见效」兑现 |
| **14a-1** | 迁移：`deleted_product` 墓碑表（键同 `uq_product`）+ 删除权限码种子 | 键的拼法见 §〇 |
| **14a-2** | 删除服务 + `DELETE /products/{id}` 端点：三级判定、显式清理引用、写墓碑、写 `audit_log` | 仓内**第一个 DELETE 端点**，契约同 PR 维护 |
| **14a-3** | 入库点接墓碑：`scrape/service.py` upsert 前查 `deleted_product`，命中即跳过并计数 | **验收②的承重件** |
| **14a-4** | 判据全套 + 前端删除入口（二次确认）+ 工单回写 | 判据见下 |

### 判据必须包含（不是「测了新路径」而是「测了不该变的东西没变」）

- ①无历史删 → 物理行消失、**墓碑零行**；
- ②有历史删 → 实体消失、墓碑一行，**且随后用同 `(team_id, source_channel, source_ref)`
  重新采集 → 不入库**（这条直接对应验收②，是本单唯一不可省的判据）；
- ③**订单 / 审计三族 / 财务表在任何删除路径下行数不变**——显式断言，不靠"我们没写删它的代码"；
- ④删除动作在 `audit_log` 可查（谁/何时/删了什么/before 快照）；
- ⑤跨团队：A 团队不能删 B 团队的产品（RLS 之上再加一条判据，抄 `test_audit_batch.py` T4 的
  防探测写法——**同码不同团队应回 404 而非 403**，否则是资源存在性探测）。

---

## 四、开工前已确认的事实清单（供审查复算）

| 声称 | 复算命令 | 期望 |
|---|---|---|
| 全仓零 DELETE 端点 | `grep -rn "\.delete(" backend/src/erp/ --include=*.py \| grep -v session.delete` | 空 |
| product 无 asin 列 | `grep -n "asin" backend/alembic/versions/0007_scrape_catalog.py` | 空 |
| 真实去重键 | `grep -n "uq_product" backend/alembic/versions/0007_scrape_catalog.py` | `(team_id, source_channel, source_ref)` |
| 三处 NOT NULL 外键 | `grep -rn "REFERENCES app.product(id)" backend/alembic/versions/*.py` | variant_member / listing / listing_spec |
| 入库点 | `sed -n '520,532p' backend/src/erp/scrape/service.py` | `ON CONFLICT (team_id, source_channel, source_ref)` |
