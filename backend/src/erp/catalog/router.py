"""catalog 最小路由（R1-12 前端产品页所需；完整 Catalog 段随 R2）。"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

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
