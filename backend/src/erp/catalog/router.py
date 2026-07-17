"""catalog 最小路由（R1-12 前端产品页所需；完整 Catalog 段随 R2）。"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.catalog import variant as variant_service
from erp.core.authn import CurrentUser, require_permission
from erp.core.db import get_session
from erp.core.errors import BusinessError
from erp.identity.schemas import Page

catalog_router = APIRouter(tags=["Catalog"])


class ProductOut(BaseModel):
    id: int
    master_sku: str
    source_channel: str
    source_ref: str
    title: str
    brand: str | None
    status: str
    latest_audit_run_id: int | None
    created_at: datetime


@catalog_router.get("/products")
async def list_products(
    user: Annotated[CurrentUser, Depends(require_permission("catalog.product_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, max_length=100),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> Page[ProductOut]:
    where = "WHERE team_id = :team"
    params: dict[str, Any] = {"team": user.team_id}
    if status:
        where += " AND status = :st"
        params["st"] = status
    if q:
        where += " AND (title ILIKE :q OR source_ref ILIKE :q OR master_sku ILIKE :q)"
        params["q"] = f"%{q}%"
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.product {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT id, master_sku, source_channel, source_ref, title, brand,"
                " status, latest_audit_run_id, created_at"
                f" FROM app.product {where}"
                " ORDER BY id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return Page(items=[ProductOut(**r) for r in rows], total=total, page=page, size=size)


@catalog_router.get("/products/{product_id}")
async def get_product(
    product_id: int,
    user: Annotated[CurrentUser, Depends(require_permission("catalog.product_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, master_sku, source_channel, source_ref, title, brand,"
                    " category_path, images, attrs, price_snapshot, status,"
                    " latest_audit_run_id, created_at, updated_at"
                    " FROM app.product WHERE id = :p"
                ),
                {"p": product_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise BusinessError("PRODUCT_NOT_FOUND", "产品不存在")
    return dict(row)


# ── 变体组（R2-11 增量1；契约 002 /variant-groups；D-Q2/D-Q63）──


class VariantGroupIn(BaseModel):
    source_parent_ref: str | None = None
    variation_theme: str | None = None


class VariantMemberIn(BaseModel):
    product_id: int
    variant_attrs: dict[str, Any]
    is_primary: bool = False


async def _group_with_members(session: AsyncSession, group_id: int) -> dict[str, Any]:
    header = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, source_parent_ref, variation_theme, status,"
                    " anchor_store_id, created_at"
                    " FROM app.variant_group WHERE id = :g"
                ),
                {"g": group_id},
            )
        )
        .mappings()
        .one()
    )
    members = [
        dict(r)
        for r in (
            await session.execute(
                text(
                    "SELECT m.product_id, m.variant_attrs, m.is_primary,"
                    " p.master_sku, p.title, p.status AS product_status"
                    " FROM app.variant_member m JOIN app.product p ON p.id = m.product_id"
                    " WHERE m.group_id = :g ORDER BY m.product_id"
                ),
                {"g": group_id},
            )
        ).mappings()
    ]
    return {**{k: v for k, v in dict(header).items() if k != "team_id"}, "members": members}


@catalog_router.get("/variant-groups")
async def list_variant_groups(
    user: Annotated[CurrentUser, Depends(require_permission("catalog.product_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    status: str | None = Query(default=None),
    q: str | None = Query(default=None, description="source_parent_ref 精确查"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
) -> Page[dict[str, Any]]:
    where = "WHERE team_id = :team"
    params: dict[str, Any] = {"team": user.team_id}
    if status:
        where += " AND status = :st"
        params["st"] = status
    if q:
        where += " AND source_parent_ref = :q"
        params["q"] = q
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.variant_group {where}"), params)
    ).scalar_one()
    ids = [
        int(r.id)
        for r in await session.execute(
            text(
                f"SELECT id FROM app.variant_group {where} ORDER BY id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ]
    items = [await _group_with_members(session, gid) for gid in ids]
    return Page(items=items, total=total, page=page, size=size)


@catalog_router.post("/variant-groups", status_code=201)
async def create_variant_group(
    body: VariantGroupIn,
    user: Annotated[CurrentUser, Depends(require_permission("catalog.product_write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """人工建组（契约 D-Q2）。自动归组由 beat variant_group_sync 负责。"""
    group_id = (
        await session.execute(
            text(
                "INSERT INTO app.variant_group (team_id, source_parent_ref, variation_theme)"
                " VALUES (:t, :p, :th) RETURNING id"
            ),
            {"t": user.team_id, "p": body.source_parent_ref, "th": body.variation_theme},
        )
    ).scalar_one()
    return await _group_with_members(session, int(group_id))


@catalog_router.put("/variant-groups/{group_id}/members")
async def set_variant_members(
    group_id: int,
    body: list[VariantMemberIn],
    user: Annotated[CurrentUser, Depends(require_permission("catalog.product_write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """全量设置组成员与维度值（人工修正入口；判定 broken 随调）。"""
    result = await variant_service.set_members(
        session,
        group_id=group_id,
        team_id=user.team_id or -1,
        members=[m.model_dump() for m in body],
    )
    return {**result, **{"detail": await _group_with_members(session, group_id)}}
