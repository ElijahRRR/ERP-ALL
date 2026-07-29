"""R2-14 14a：产品硬删除（`00-conventions.md §7.1` 三级规则的产品侧落地）。

**这是全仓第一个真正删数据的服务**——此前零 DELETE 端点，清理只能由部署 AI 手写 SQL
直连生产库加四道闸。故本模块的每一条守卫都按「宁可拒绝，不可静默多删」写。

三级规则在本模块的落点：

| 级 | 判定 | 动作 |
|---|---|---|
| ① 无历史 | 无任何 `listing` 行 | 物理删除，**不写墓碑**（§7.1 与验收①都明写「无墓碑」） |
| ② 有历史 | 有 `listing` 行 | 显式按序清理引用 → 物理删除 → 写 `deleted_product` 墓碑 |
| ③ 绝不删 | 审计三族 / 财务 / 订单售后 | 本模块**一个 DELETE 都不发给它们**，判据显式断言行数不变 |

「有历史」= 被 `listing` 引用。理由：`listing` 才是「上过架」的事实表；`variant_member`
（归组关系）与 `listing_spec`（listing 的规格产物）不是独立的历史来源，但它们是
**NOT NULL 外键**，删除时同样挡路，故一并显式清理。
"""

from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.audit import AuditWriter
from erp.core.authn import CurrentUser
from erp.core.errors import BusinessError
from erp.listing import gtin

# ── listing 状态分类（并集必须 == 0009 的 ck_listing_status，判据在
#    test_product_lifecycle.py::test_listing_status_classification_is_exhaustive）──
#
# **渠道侧存活或在途**：本地行删了，渠道上那条商品仍在，且再也下不掉架——
# 一个永久孤儿。比「不能删」严重得多，故一律拒绝。
_CHANNEL_ALIVE = frozenset(
    {"queued", "submitted", "processing", "published", "live", "degraded", "delist_pending"}
)
# **可随产品一并删**：从未上过渠道（draft/failed），或已确认下架（delisted/retired）。
_DELETABLE = frozenset({"draft", "delisted", "failed", "retired"})


async def _load_product(session: AsyncSession, product_id: int) -> dict[str, Any]:
    """取产品。**跨团队一律 404 而非 403**——403 等于确认「这个 id 存在，只是不属于你」，
    是资源存在性探测（同 `test_audit_batch.py` T4 的写法）。RLS 已按 team 过滤，
    故这里查不到与不属于本团队在响应上不可区分。
    """
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, master_sku, source_channel, source_ref, title, brand,"
                    " status, variant_group_id, created_at"
                    " FROM app.product WHERE id = :p"
                ),
                {"p": product_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在", http_status=404)
    return dict(row)


async def delete_product(
    session: AsyncSession,
    *,
    user: CurrentUser,
    request: Request | None,
    product_id: int,
    reason: str,
) -> dict[str, Any]:
    """按 §7.1 三级规则硬删除一个产品。整体在请求事务内，任一守卫抛错即全部回滚。"""
    product = await _load_product(session, product_id)

    listings = [
        dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT id, status, channel_sku, store_id FROM app.listing"
                    " WHERE product_id = :p ORDER BY id"
                ),
                {"p": product_id},
            )
        ).mappings()
    ]

    alive = [row for row in listings if row["status"] in _CHANNEL_ALIVE]
    if alive:
        raise BusinessError(
            "PRODUCT_DELETE_LISTING_ACTIVE",
            "该产品仍有在架或在途的上架记录，请先下架并等下架完成后再删除",
            {"listings": [{"id": r["id"], "status": r["status"]} for r in alive]},
            http_status=409,
        )
    # fail-closed：将来 0009 的 CHECK 加了新状态而这里忘了分类，宁可拒绝删除，
    # 也不要默认把它当成「可删」——默认可删的方向一旦错，错法是把在架商品删成孤儿。
    unclassified = [row for row in listings if row["status"] not in _DELETABLE]
    if unclassified:
        raise BusinessError(
            "PRODUCT_DELETE_LISTING_UNCLASSIFIED",
            "该产品的上架记录处于未分类状态，删除路径拒绝执行，请报运维",
            {"listings": [{"id": r["id"], "status": r["status"]} for r in unclassified]},
            http_status=409,
        )

    listing_ids = [row["id"] for row in listings]
    if listing_ids:
        # 正在被 runner 执行的维护任务：删掉它脚下的 listing 会让它跑向一个不存在的行。
        # 同样 fail-closed——等它跑完再删，代价只是等。
        running = (
            await session.execute(
                text(
                    "SELECT count(*) FROM app.maintenance_task"
                    " WHERE listing_id = ANY(:ids) AND status = 'running'"
                ),
                {"ids": listing_ids},
            )
        ).scalar_one()
        if running:
            raise BusinessError(
                "PRODUCT_DELETE_TASK_RUNNING",
                "该产品的上架记录有维护任务正在执行，请稍后再删除",
                {"running_tasks": int(running)},
                http_status=409,
            )

    # ── 显式按序清理引用（不加 ON DELETE CASCADE：CASCADE 会让将来任何一次误删
    #    静默扩散进 listing 域，而显式清理在代码里看得见、在判据里挡得住）──
    gtins_released = 0
    maintenance_skipped = 0
    if listing_ids:
        for lid in listing_ids:
            # held → free 归还；used **不回收**（已发到渠道，终身绑定，见 gtin.release）。
            # 不归还就是 GTIN 池静默泄漏——池空了才会发现，而那时已查不出是谁漏的。
            before_free = await _held_count(session, lid)
            await gtin.release(session, lid)
            gtins_released += before_free
        # 用 RETURNING 数行而不是 `.rowcount`：后者在 SQLAlchemy 的类型里挂在
        # `CursorResult` 上、`session.execute` 声明的却是 `Result[Any]`（mypy 判红），
        # 且它的语义随驱动而变。RETURNING 是显式的，数出来的就是真被改的那些行。
        maintenance_skipped = len(
            (
                await session.execute(
                    text(
                        "UPDATE app.maintenance_task SET status = 'skipped',"
                        " finished_at = now(),"
                        " error = '产品被删除（R2-14 14a），任务作废'"
                        " WHERE listing_id = ANY(:ids) AND status = 'scheduled'"
                        " RETURNING id"
                    ),
                    {"ids": listing_ids},
                )
            ).all()
        )
        await session.execute(
            text("DELETE FROM app.listing_spec WHERE product_id = :p"), {"p": product_id}
        )
        await session.execute(
            text("DELETE FROM app.listing WHERE product_id = :p"), {"p": product_id}
        )
    await session.execute(
        text("DELETE FROM app.variant_member WHERE product_id = :p"), {"p": product_id}
    )
    await session.execute(text("DELETE FROM app.product WHERE id = :p"), {"p": product_id})

    # ── ②级写墓碑；①级不写（§7.1 与验收①都明写「无墓碑」）──
    has_history = bool(listings)
    if has_history:
        await session.execute(
            text(
                "INSERT INTO app.deleted_product"
                " (team_id, source_channel, source_ref, product_id, master_sku, reason, deleted_by)"
                " VALUES (:t, :ch, :ref, :pid, :sku, :rsn, :by)"
                " ON CONFLICT (team_id, source_channel, source_ref) DO NOTHING"
            ),
            {
                "t": product["team_id"],
                "ch": product["source_channel"],
                "ref": product["source_ref"],
                "pid": product_id,
                "sku": product["master_sku"],
                "rsn": reason,
                "by": user.id,
            },
        )

    # ── 留痕（007 14d 要求「删除本身写 audit_log，含 before 快照」，本单自带不等 14d：
    #    先上线一个「谁都能删、删了查不到」的出口比没有出口更糟）──
    #
    # **快照只存身份与业务字段，不存 attrs / images / price_snapshot。**
    # 那三样正是 §7.1 说的「占体积的大头」，而 `audit_log` 是永久保留的月分区表——
    # 把整行塞进快照，删除就只是把字节从 product 搬进一张永不清理的表，
    # 空间非但没省，还更糟。
    await AuditWriter.for_user(session, user, request).log(
        "catalog.product_delete",
        "product",
        product_id,
        before={
            "master_sku": product["master_sku"],
            "source_channel": product["source_channel"],
            "source_ref": product["source_ref"],
            "title": product["title"],
            "brand": product["brand"],
            "status": product["status"],
            "variant_group_id": product["variant_group_id"],
            # 显式 isoformat：`AuditWriter.log` 的 json.dumps **没有** default=，
            # datetime 直接抛 TypeError。这是好事（开发期就炸，不是静默写进一条坏日志），
            # 但意味着调用方必须自己把非 JSON 原生类型转干净。
            "created_at": product["created_at"].isoformat(),
            "listings": [{"id": r["id"], "status": r["status"]} for r in listings],
        },
        after={"reason": reason, "tombstoned": has_history},
    )

    return {
        "product_id": product_id,
        "master_sku": product["master_sku"],
        # ①/②级的机器可读名。前端据此决定是否提示「重新采集不会再入库」。
        "level": "with_history" if has_history else "no_history",
        "tombstoned": has_history,
        "listings_deleted": len(listing_ids),
        "gtins_released": gtins_released,
        "maintenance_tasks_skipped": maintenance_skipped,
    }


async def _held_count(session: AsyncSession, listing_id: int) -> int:
    """该 listing 当前占着（held）几枚 GTIN——归还前先数，用于回执如实计数。"""
    return int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM app.gtin_pool"
                    " WHERE held_listing_id = :l AND status = 'held'"
                ),
                {"l": listing_id},
            )
        ).scalar_one()
    )
