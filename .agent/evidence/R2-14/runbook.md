# R2-14 产品删除 运维 runbook

> 对应 `DELETE /api/v1/products/{productId}`（仓内第一个 DELETE 端点）与迁移 0041。
> 口径 `00-conventions §7.1`；裁定见 `owner-rulings-20260729.md`。

## 一、两级删除的**实际后果**（给运营看的那一版）

| 级 | 何时命中 | 删掉什么 | **重新采集会怎样** |
|---|---|---|---|
| ①无历史 | 该产品从未有过 listing | 产品行 + 归组关系 | **会重新入库**（不留墓碑，Owner 2026-07-29 裁定维持） |
| ②有历史 | 有过 listing（哪怕已下架） | 产品行 + 已终态的 listing / listing_spec + 归组关系 | **不会再入库**（墓碑生效） |

**订单、审计三族、财务表在任何删除路径下一行不删**（判据显式断言，不靠「我们没写删它的代码」）。

### 清理一批「从未上架」的产品时，必须同时做的一步

①级不留墓碑 ⇒ **只删数据库不改采集清单，下次提交同一份清单它们会全部回来**。
清理动作应当是两步：

1. 从采集清单（Excel / 提交给 `/scrape-jobs` 的 targets）里**去掉这些 ASIN**；
2. 再执行删除。

顺序反了也行，但**第 1 步不能省**——省掉它，删除就是白做，且过程中没有任何报错。

## 二、删不掉时的三种 409，分别该怎么办

| 错误码 | 含义 | 处置 |
|---|---|---|
| `PRODUCT_DELETE_LISTING_ACTIVE` | 还有在架/在途的上架记录 | **先下架并等下架完成**。不要绕过——删了本地行，渠道上那条商品再也下不掉架，成为永久孤儿 |
| `PRODUCT_DELETE_TASK_RUNNING` | 有维护任务正在执行 | 等它跑完（分钟量级）再删 |
| `PRODUCT_DELETE_LISTING_UNCLASSIFIED` | listing 状态不在代码的分类表里 | **报运维，不要绕过**。正常情况不会出现；出现即说明 `ck_listing_status` 加了新状态而删除路径没跟上（CI 里有一条判据专门盯这个，它红了才对） |

## 三、⚠️ 改采集会话上下文时必须复验的一条（PR #46 审查侧点出）

墓碑检查 `_deleted_refs`（`scrape/service.py`）**受 RLS 管**，而代码把
**空结果当作「没有墓碑」放行**——「真没有」与「有但看不见」是同一个绿。

- `deleted_product` 的 SELECT 策略：`team_id = app.current_team() OR app.is_super()`
- 采集链走 `system_tx`（`core/db.py` 设 `app.is_super='on'`）→ 命中后半段 → 墓碑可见 ✅

**风险不在今天，在将来**：谁要是把采集改成按团队会话跑而没设 `app.current_team`，
**回流保护会在零报错的情况下整条消失**，而单元判据照样全绿（测试里带团队上下文）。

**硬规则：任何改动采集链事务上下文的 PR，必须复验一次「删过的商品重采仍不入库」**
（即 `test_product_lifecycle.py::test_level2_with_history_writes_tombstone_and_blocks_reingest`
在新上下文下仍绿，且不是靠 `is_super` 蒙混过去）。

## 四、迁移 0041 的降级语义

`alembic downgrade` 到 0040 会 **DROP `deleted_product`**，即**降级后已删商品可以被
重新采集回来**。这是降级固有的语义损失，不是缺陷——墓碑本就是 0041 引入的保护。

真机降级前须知悉这一点。0041 同时会 `REVOKE DELETE ON listing, listing_spec`，
删除端点随之失效（返回权限错误），属预期。

## 五、留痕在哪里查

- **谁删了什么**：`app.audit_log` 中 `action = 'catalog.product_delete'`，
  `object_id` = 产品 id，`before` 是精简快照（身份 + 业务字段 + 当时的 listing 列表），
  `after` = `{reason, tombstoned}`。
  > `before` **故意不含** `attrs`/`images`/`price_snapshot`——那三样是占体积的大头，
  > 而 `audit_log` 永久保留；整行入快照会让删除变成「把字节搬进一张永不清理的表」。
- **哪些商品被墓碑挡着**：`SELECT * FROM app.deleted_product WHERE team_id = ...`
- **某次采集被挡了几个**：作业的 `input->'skipped_deleted'`（预筛层），
  以及日志事件 `scrape.upsert_skipped_by_tombstone`（入库层，**这层才是保证**）。
