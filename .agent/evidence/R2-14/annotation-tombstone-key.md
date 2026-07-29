# R2-14 批注回传：`deleted_product` 的墓碑键应按 BR-CAT-002 展开为 `(source_channel, source_ref)`

> 云端 AI → 审计侧 / Owner，2026-07-29。
> **本文不改图纸正文**（`specs/001-domain-model/` 归审计侧），只提请补注并说明云端侧的实现取舍。
> 依据 CLAUDE.md 分工表：设计变更走批注回传。

> ## ⚠️ 本文 v1 的定性是错的，v2（2026-07-29 同日）已更正
>
> **v1 写的是**「图纸的墓碑键**指向一个不存在的列**」，并把它与 `ERP_ENV` 从未注入、
> `compliance_block` 零消费点、`O-1..O-6` 并列，称「共同点是只要没人去对源码，
> 它看起来永远是对的」。
>
> **那个类比错了。** 那三例是**真的不存在**；而 `asin` 这个词在文档里**有明确定义**，
> 只是定义不在 `00-conventions` 里（见 §一）。
>
> **错因**：v1 只检索了 `00-conventions.md` 与 `backend/alembic/versions/`，
> **没有检索 `03-catalog.md` 与 `business-rules-ledger.md`**——而定义正在那两处。
> 是 Owner 追问「开发文档中应该有写这些吧」才促成复核。
>
> **教训与上一条同族但更进一层**：上一次是 grep 被 `head` 截断，这一次检索没被截断，
> 是**检索面没覆盖到定义所在的文件**——**「查过了」不等于「查全了」，而下重结论
> （「它指向的东西不存在」）要求的恰恰是查全**。结论若只是「建议改键」，查得窄一点
> 尚可补救；说成「图纸凭空捏造」，就必须穷尽文档才有资格。
>
> **技术结论未变**（键仍应为 `(team_id, source_channel, source_ref)`），变的是**依据**：
> 从「纠正图纸的错误」改为「**按宪法把简写展开**」。

---

## 一、事实：`asin` 是**有定义的简写**，定义在别处

`00-conventions.md §7.1` 的墓碑表定义写：

> `deleted_product (team_id, **asin**, reason, deleted_at, deleted_by)`——几十字节 vs 完整
> 商品行，空间该省的省掉；同时**去重键 `(team_id, asin)` 仍认得该 ASIN，不会下次采集
> 又抓回来**（无墓碑硬删 = 垃圾循环回流，这是硬删除唯一的技术陷阱）。

**该简写在文档里有两处明确定义**（v1 漏检的正是这两处）：

| 出处 | 原文 |
|---|---|
| `001-domain-model/03-catalog.md:14` | `\| source_ref \| TEXT \| NOT NULL \| **ASIN（或未来其他源主键）** \|` |
| `000-founding/business-rules-ledger.md:44`（BR-CAT-002） | 内部身份 = master_sku（渠道中立终身不变）；**ASIN 降级为 `(source_channel, source_ref)` 属性** |

**BR-CAT-002 是宪法级依据**（铁律 1：`specs/000-founding/` 是宪法）。它直接说明
「ASIN」在本系统的存储表示就是 `(source_channel, source_ref)` 这一对。
因此 D-Q31 的「去重键 = `(team_id, asin)`」展开成存储语言即
`(team_id, source_channel, source_ref)`——**与代码里的 `uq_product` 完全一致**。

**代码侧三条实测**（均可复算，v1 的这三条本身没错）：

| 事实 | 复算命令 | 结果 |
|---|---|---|
| `product` 表无名为 `asin` 的列 | `psql -c "SELECT column_name FROM information_schema.columns WHERE table_schema='app' AND table_name='product'"` | 19 列，无 `asin` |
| 真实唯一键 | `grep -n "uq_product" backend/alembic/versions/0007_scrape_catalog.py` | `uq_product UNIQUE (team_id, source_channel, source_ref)` |
| 入库点同键 | `sed -n '520,532p' backend/src/erp/scrape/service.py` | `ON CONFLICT (team_id, source_channel, source_ref) DO UPDATE` |

**前端也遵循同一映射**（可佐证这不是代码擅自偏离）：
`ProductsPage.tsx:197` 是 `{ title: 'ASIN', dataIndex: 'source_ref' }`；
`ProductDetailDrawer.tsx:58` 更进一步，用 `source_channel === 'amazon'` 做条件才拼
亚马逊商品链接——**前端自己就知道「`source_ref` 只有在亚马逊渠道时才是 ASIN」**。
契约 002 的 `Product` schema 同样只有 `source_channel` / `source_ref`。

**结论：文档、契约、代码、前端四处一致，没有矛盾。** `§7.1` 用的是一个在别处
有定义的行业简写，不是笔误，更不是凭空捏造。

---

## 二、照字面实现会出什么事

**① 验收②直接失败。** R2-14 验收②是「删一个上过架的产品 → 重新采集同 ASIN 不会再入库」。
墓碑若键在 `(team_id, asin)`，而入库去重认的是 `(team_id, source_channel, source_ref)`，
**墓碑查不中，商品照样回流**——而这正是该验收唯一要验的东西。

**② `asin` 这一列在 product 侧没有源。** 只能从 `source_ref` 派生：`source_channel='amazon'`
时它恰好是 ASIN。**但那是巧合不是契约**——`source_channel` 这一列存在的全部意义就是
将来会有别的渠道，届时 `source_ref` 不再是 ASIN，派生逻辑无声失真。

**③ 多渠道时是静默误杀，比回流更难发现。** `(team_id, asin)` 会让渠道 A 删掉的商品
**把渠道 B 的同号商品一起挡在门外**。回流至少还能在列表里看见多余的商品；误杀是
「本该采进来的没进来」，没有任何东西会报。

---

## 三、云端侧的实现取舍（14a 落地，请审计侧确认或改判）

**建 `deleted_product (team_id, source_channel, source_ref, reason, deleted_at, deleted_by)`，
唯一键与 `uq_product` 严格同形。**

- 这**不是纠正图纸，是按 BR-CAT-002 把「asin」这个简写展开成它的存储表示**；
- 图纸表达的意图完全保留——墓碑保去重、不让删掉的商品下次采集又回流；
- 「几十字节 vs 完整商品行」的空间论证同样成立（多一列 `source_channel`，仍是几十字节）。

**提请审计侧在 §7.1 补一句指向定义**（不是改结论，是防照字面实现）：
写明此处 `asin` 即 BR-CAT-002 的 `(source_channel, source_ref)`，故墓碑键为
`(team_id, source_channel, source_ref)`。理由见 §二——**§7.1 是「三级删除规则」的
落地口径文档，读它的人多半直接照着建表，未必会回溯到 `03-catalog` 或宪法去查
「asin」的定义**；而这两个文件里的定义恰恰带着「（或未来其他源主键）」这个关键限定。

若审计侧另有判断（例如认为应给 `product` 补一个真正的 `asin` 列并以它为去重键），
那是更大的改动，**请直接改判，云端侧照办**。

---

## 四、这条为什么值得单独写：**不是图纸错了，是简写在落地文档里失去了限定**

`§7.1` 用「asin」是行业通行简写，也**确有定义**（§一两处）。问题不在用词，而在：

- 定义在 `03-catalog.md` 与宪法里，**带着「（或未来其他源主键）」这个限定**；
- 而 `§7.1` 是**落地口径文档**——读它的人是要照着建表的，**简写到了这里就只剩字面**；
- 于是「照字面建一个 `asin` 单列」变成一条自然而然的路，而那条路会丢掉 `source_channel`，
  在多渠道时静默误杀（§二③）。

**这与「契约指向不存在的东西」是两类问题，v1 把它们混为一谈是错的**（见文首更正）：
前者是无中生有，后者是**有定义但定义没跟着简写一起传到落地现场**。
后者的修法不是纠错，是**在落地现场把定义补回去**——这也是本文向审计侧提的唯一请求。

> **附：本文自身的方法论教训。** v1 下了「图纸指向不存在的列」这个重结论，而检索面
> 只覆盖 `00-conventions` 与迁移文件。**结论的分量决定检索面必须多宽**：
> 提「建议改键」，查代码即可；说「图纸凭空捏造」，就必须穷尽文档。
> v1 用前者的检索面下了后者的结论——**是 Owner 追问「开发文档中应该有写这些吧」
> 才促成复核**，否则这份批注会带着一个错误的类比进入审计侧的决策链。
