"""listing 域路由（契约 Listing 段 + GTIN 池）。

R1 注记：submit/delist 契约标 202 异步——试点期在请求内同步执行（量小），
返回结构与契约一致；worker 队列化随 R2 automation 域接入。
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.audit import AuditWriter
from erp.core.authn import CurrentUser, require_permission
from erp.core.db import get_session
from erp.core.errors import BusinessError
from erp.identity.schemas import Page
from erp.listing import gtin as gtin_service
from erp.listing import service

listing_router = APIRouter(tags=["Listing"])


class AllocateIn(BaseModel):
    product_ids: list[int] = Field(max_length=500)
    store_id: int
    offer_mode: str = Field(pattern="^(build|match)$")


class SubmitIn(BaseModel):
    listing_ids: list[int] = Field(max_length=500)


class GtinImportIn(BaseModel):
    gtins: list[str] = Field(max_length=10000)
    source: str = Field(
        default="generator_import", pattern="^(generator_import|feishu_import|purchased)$"
    )


class ListingOut(BaseModel):
    id: int
    store_id: int
    product_id: int
    offer_mode: str
    channel_sku: str
    gtin: str | None
    status: str
    error_code: str | None
    is_locked: bool
    wpid: str | None
    current_price: float | None
    current_inventory: int
    published_at: datetime | None
    created_at: datetime


@listing_router.get("/listings")
async def list_listings(
    user: Annotated[CurrentUser, Depends(require_permission("listing.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    store_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    offer_mode: str | None = Query(default=None),
    error_code: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> Page[ListingOut]:
    where = "WHERE team_id = :team"
    params: dict[str, Any] = {"team": user.team_id}
    for key, val in (("store_id", store_id), ("status", status),
                     ("offer_mode", offer_mode), ("error_code", error_code)):  # fmt: skip
        if val is not None:
            where += f" AND {key} = :{key}"
            params[key] = val
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.listing {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT id, store_id, product_id, offer_mode, channel_sku, gtin, status,"
                " error_code, is_locked, wpid, current_price, current_inventory,"
                " published_at, created_at"
                f" FROM app.listing {where}"
                " ORDER BY id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return Page(items=[ListingOut(**r) for r in rows], total=total, page=page, size=size)


@listing_router.get("/listings/{listing_id}")
async def get_listing(
    listing_id: int,
    user: Annotated[CurrentUser, Depends(require_permission("listing.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, store_id, product_id, offer_mode, channel_sku, gtin, status,"
                    " error_code, is_locked, wpid, channel_item_id, end_date, current_price,"
                    " currency, current_inventory, published_at, delisted_at, created_at"
                    " FROM app.listing WHERE id = :id"
                ),
                {"id": listing_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise BusinessError("LISTING_NOT_FOUND", "listing 不存在")
    history = (
        await session.execute(
            text(
                "SELECT from_status, to_status, reason_code, detail, actor_type, occurred_at"
                " FROM app.listing_state_history WHERE listing_id = :id"
                " ORDER BY occurred_at, id"
            ),
            {"id": listing_id},
        )
    ).mappings()
    return {**dict(row), "state_history": [dict(h) for h in history]}


@listing_router.post("/listings/allocate")
async def allocate(
    body: AllocateIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("listing.allocate"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    if user.team_id is None:
        raise BusinessError("LISTING_TEAM_REQUIRED", "超管需切换到具体团队")
    result = await service.allocate(
        session,
        team_id=user.team_id,
        store_id=body.store_id,
        product_ids=body.product_ids,
        offer_mode=body.offer_mode,
        actor_id=user.id,
    )
    await AuditWriter.for_user(session, user, request).log(
        "listing.allocate",
        "store",
        body.store_id,
        after={"created": len(result["created"]), "rejected": len(result["rejected"])},
    )
    return result


@listing_router.post("/listings/submit", status_code=202)
async def submit(
    body: SubmitIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("listing.submit"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    if user.team_id is None:
        raise BusinessError("LISTING_TEAM_REQUIRED", "超管需切换到具体团队")
    result = await service.submit(
        session, team_id=user.team_id, listing_ids=body.listing_ids, actor_id=user.id
    )
    await AuditWriter.for_user(session, user, request).log(
        "listing.submit", "feed", result.get("feed_id"), after={"queued": result["queued"]}
    )
    return result


@listing_router.post("/listings/{listing_id}/delist", status_code=202)
async def delist(
    listing_id: int,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("listing.delist"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    if user.team_id is None:
        raise BusinessError("LISTING_TEAM_REQUIRED", "超管需切换到具体团队")
    result = await service.delist(
        session, team_id=user.team_id, listing_id=listing_id, actor_id=user.id
    )
    await AuditWriter.for_user(session, user, request).log("listing.delist", "listing", listing_id)
    return result


@listing_router.post("/listings/{listing_id}/retry", status_code=202)
async def retry(
    listing_id: int,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("listing.submit"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    if user.team_id is None:
        raise BusinessError("LISTING_TEAM_REQUIRED", "超管需切换到具体团队")
    result = await service.retry_failed(
        session, team_id=user.team_id, listing_id=listing_id, actor_id=user.id
    )
    await AuditWriter.for_user(session, user, request).log("listing.retry", "listing", listing_id)
    return result


# ── feeds ──


@listing_router.get("/feeds")
async def list_feeds(
    user: Annotated[CurrentUser, Depends(require_permission("listing.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    store_id: int | None = Query(default=None),
    feed_kind: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    where = "WHERE team_id = :team"
    params: dict[str, Any] = {"team": user.team_id}
    for key, val in (("store_id", store_id), ("feed_kind", feed_kind), ("status", status)):
        if val is not None:
            where += f" AND {key} = :{key}"
            params[key] = val
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.feed {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT id, store_id, channel_feed_id, feed_kind, status, item_count,"
                " headline, submitted_at, completed_at, poll_attempts, created_at"
                f" FROM app.feed {where}"
                " ORDER BY id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return {"items": [dict(r) for r in rows], "total": total, "page": page, "size": size}


@listing_router.get("/feeds/{feed_id}")
async def get_feed(
    feed_id: int,
    user: Annotated[CurrentUser, Depends(require_permission("listing.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, store_id, channel_feed_id, feed_kind, status, item_count,"
                    " headline, submitted_at, last_polled_at, completed_at, poll_attempts,"
                    " created_at FROM app.feed WHERE id = :f"
                ),
                {"f": feed_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise BusinessError("FEED_NOT_FOUND", "feed 不存在")
    return dict(row)


@listing_router.get("/feeds/{feed_id}/items")
async def feed_items(
    feed_id: int,
    user: Annotated[CurrentUser, Depends(require_permission("listing.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    where = "WHERE feed_id = :f"
    params: dict[str, Any] = {"f": feed_id}
    if status:
        where += " AND status = :st"
        params["st"] = status
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.feed_item {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT listing_id, channel_sku, status, error_code, error_msg, created_at"
                f" FROM app.feed_item {where}"
                " ORDER BY id LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return {"items": [dict(r) for r in rows], "total": total, "page": page, "size": size}


@listing_router.post("/feeds/{feed_id}/poll")
async def poll_feed(
    feed_id: int,
    user: Annotated[CurrentUser, Depends(require_permission("listing.submit"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """手动触发轮询（beat 自动轮询随 R2；A152 试点期人工点查）。"""
    return await service.poll_feed(session, feed_id)


@listing_router.post("/feeds/{feed_id}/verify-back")
async def verify_back(
    feed_id: int,
    user: Annotated[CurrentUser, Depends(require_permission("listing.submit"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """verify_pending 对账归位（adopt/lost）——总账铁律：永不盲重试。"""
    return await service.verify_back(session, feed_id)


# ── GTIN 池 ──


@listing_router.get("/gtin-pool/stats")
async def gtin_stats(
    user: Annotated[CurrentUser, Depends(require_permission("catalog.gtin_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            text(
                "SELECT gtin_kind, status, count(*) AS count FROM app.gtin_pool"
                " WHERE team_id = :t GROUP BY gtin_kind, status ORDER BY gtin_kind, status"
            ),
            {"t": user.team_id},
        )
    ).mappings()
    return [dict(r) for r in rows]


@listing_router.get("/gtin-pool")
async def gtin_list(
    user: Annotated[CurrentUser, Depends(require_permission("catalog.gtin_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    status: str | None = Query(default=None),
    gtin_kind: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    where = "WHERE team_id = :team"
    params: dict[str, Any] = {"team": user.team_id}
    if status:
        where += " AND status = :st"
        params["st"] = status
    if gtin_kind:
        where += " AND gtin_kind = :k"
        params["k"] = gtin_kind
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.gtin_pool {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT id, gtin, gtin_kind, status, source, held_listing_id,"
                " used_listing_id, created_at"
                f" FROM app.gtin_pool {where}"
                " ORDER BY id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return {"items": [dict(r) for r in rows], "total": total, "page": page, "size": size}


@listing_router.post("/gtin-pool/import", status_code=201)
async def gtin_import(
    body: GtinImportIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("catalog.import_write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    if user.team_id is None:
        raise BusinessError("LISTING_TEAM_REQUIRED", "超管需切换到具体团队")
    result = await gtin_service.import_batch(
        session, team_id=user.team_id, gtins=body.gtins, source=body.source, created_by=user.id
    )
    await AuditWriter.for_user(session, user, request).log(
        "catalog.gtin_import", "gtin_pool", user.team_id, after={"imported": result["imported"]}
    )
    return result
