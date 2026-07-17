"""catalog 品牌占用服务（封店批量释放 + 手动释放共用语义）。

- 占用生产端在 listing.allocate（_occupy_brand，build 上架时 upsert）。
- 本模块负责释放侧：封店工作流批量 released（release_for_incident）由 channel
  create_incident 的 suspension 分支编排调用；手动单条释放走 catalog 端点。
- 释放语义（001 §03 :80-84）：status occupied→released + released_at + release_reason
  + incident_id 回链；released 行不占部分唯一槽（uq_brand_occupied），品牌可再分配。
"""

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.notify.service import notify


async def release_for_incident(session: AsyncSession, incident: RowMapping) -> int:
    """封店批量释放该店品牌占用（001 §02 :185）。返回释放条数。

    幂等：释放集合 = 本店 occupied 行（WHERE status='occupied'）——重复建单场景
    第二单命中 0 行，天然不二次释放（brand_released_at 守卫为防御性冗余，供
    create 之外的潜在调用方短路）。回填 store_incident 两枚完成时间戳：
    brand_released_at（品牌释放）+ sku_released_at（listing 停止维护——渠道维护面
    全链已过 store.status='active' 门控：feed_poll/feed_verify_back/retire_recon/
    price_recon 四 beat + outbox pick_next/claim 的 listing 类命令冻结；订单类
    order_ack/order_ship 放行不受封店影响）。通知仅在实际释放 >0 条时发出，
    避免重复建单产生「已释放 0 个」噪声。
    """
    if incident["brand_released_at"] is not None:
        return 0
    iid, team_id, store_id = incident["id"], incident["team_id"], incident["store_id"]
    released = (
        await session.execute(
            text(
                "UPDATE app.brand_assignment SET status = 'released', released_at = now(),"
                " release_reason = 'suspension', incident_id = :iid"
                " WHERE team_id = :t AND store_id = :s AND status = 'occupied'"
                " RETURNING id"
            ),
            {"iid": iid, "t": team_id, "s": store_id},
        )
    ).all()
    count = len(released)
    await session.execute(
        text(
            "UPDATE app.store_incident"
            " SET brand_released_at = now(), sku_released_at = now() WHERE id = :iid"
        ),
        {"iid": iid},
    )
    if count == 0:
        return 0
    store_name = (
        await session.execute(text("SELECT name FROM app.store WHERE id = :s"), {"s": store_id})
    ).scalar_one()
    await notify(
        session,
        team_id=team_id,
        severity="warn",
        category="store_incident",
        title=f"封店释放完成：店铺「{store_name}」品牌占用已释放 {count} 个",
        body=(
            f"店铺「{store_name}」封店事件 #{iid} 已批量释放 {count} 个品牌占用；"
            "对应 listing 已停止维护，请关注观察放款 / 申诉进展。"
        ),
        object_type="store_incident",
        object_id=str(iid),
        dedupe_key=f"brand_release:{iid}",
    )
    return count
