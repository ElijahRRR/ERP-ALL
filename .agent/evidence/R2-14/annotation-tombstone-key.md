# R2-14 批注回传：`deleted_product` 的墓碑键与真实去重键对不上

> 云端 AI → 审计侧 / Owner，2026-07-29。
> **本文不改图纸正文**（`specs/001-domain-model/` 归审计侧），只提请修订并说明云端侧的实现取舍。
> 依据 CLAUDE.md 分工表：设计变更走批注回传。

---

## 一、事实

`00-conventions.md §7.1` 的墓碑表定义写：

> `deleted_product (team_id, **asin**, reason, deleted_at, deleted_by)`——几十字节 vs 完整
> 商品行，空间该省的省掉；同时**去重键 `(team_id, asin)` 仍认得该 ASIN，不会下次采集
> 又抓回来**（无墓碑硬删 = 垃圾循环回流，这是硬删除唯一的技术陷阱）。

**代码侧三条实测**（均可复算）：

| 事实 | 复算命令 | 结果 |
|---|---|---|
| `product` 表**没有 `asin` 列** | `grep -n "asin" backend/alembic/versions/0007_scrape_catalog.py` | **零命中** |
| 全仓 `asin` 只出现在合规黑名单 | `grep -rn "asin" backend/alembic/versions/*.py` | 全部是 `blacklist_asin`，与产品域无关 |
| 真实去重键 | `grep -n "uq_product" backend/alembic/versions/0007_scrape_catalog.py` | `uq_product UNIQUE (team_id, source_channel, source_ref)` |

入库点 `scrape/service.py:523` 亦为 `ON CONFLICT (team_id, source_channel, source_ref) DO UPDATE`。

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

## 三、云端侧的实现取舍（已在 14a 落地，请审计侧确认或改判）

**建 `deleted_product (team_id, source_channel, source_ref, reason, deleted_at, deleted_by)`，
唯一键与 `uq_product` 严格同形。**

- 图纸表达的**意图完全保留**——墓碑保去重、不让删掉的商品下次采集又回流；
- 改的只是**键的拼法**，让它指向真实存在的去重键；
- 「几十字节 vs 完整商品行」的空间论证同样成立（多一列 `source_channel`，仍是几十字节）。

**请审计侧修订 §7.1 该段正文**，把 `(team_id, asin)` 改为
`(team_id, source_channel, source_ref)`，并把「仍认得该 ASIN」改为「仍认得该采集来源」。
若审计侧另有判断（例如认为应给 `product` 补一个真正的 `asin` 列并以它为去重键），
那是更大的改动，**请直接改判，云端侧照办**——本文只是把矛盾摆出来，不代替设计决策。

---

## 四、为什么单独写这一条

这处与本项目反复出现的一类问题同形：**契约/判据写得像回事，但它指向的东西不存在**。

- `ERP_ENV` 从未注入而判据全绿；
- `compliance_block` 零消费点却被面板断言「拦截生效」；
- 本会话刚更正过的 `O-1..O-6`——一组只活在会话里、从未进仓库的编号。

**共同点是：只要没人去对源码，它看起来永远是对的。** §7.1 这段写于 2026-07-28，
写得非常具体（连「几十字节 vs 完整商品行」都算了），**恰恰是这种具体感让人不会去核**
——而 `product` 表压根没有 `asin` 列。

故本单从考古起就把每条声称机器复算了一遍再落笔，本文附的三条复算命令即为此。
