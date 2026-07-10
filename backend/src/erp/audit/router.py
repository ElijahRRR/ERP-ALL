"""audit 域路由（契约 Audit 段）。R1-10：同步执行单品审核；批量/队列随 R2。"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.audit import service
from erp.core.audit import AuditWriter
from erp.core.authn import CurrentUser, require_permission
from erp.core.db import get_session
from erp.core.errors import BusinessError
from erp.identity.schemas import Page

audit_router = APIRouter(tags=["Audit"])


class AuditTriggerIn(BaseModel):
    levels: list[str] | None = Field(default=None, description="默认 l0/l2/l3；l4 需显式")
    trigger_kind: str = Field(default="manual", pattern="^(auto|manual|batch|re_audit)$")


class AuditRunOut(BaseModel):
    id: int
    product_id: int
    trigger_kind: str
    levels_requested: list[str]
    status: str
    verdict: str | None
    reject_level: str | None
    llm_cost_usd: float
    cache_hit_rate: float | None
    duration_ms: int | None
    created_at: datetime


@audit_router.post("/products/{product_id}/audit", status_code=201)
async def trigger_audit(
    product_id: int,
    body: AuditTriggerIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("audit.run"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    levels = body.levels or service.DEFAULT_LEVELS
    if "l4" in levels:
        raise BusinessError("AUDIT_L4_DISABLED", "L4 视觉审核未开放（R2）")
    result = await service.audit_one(
        session,
        product_id=product_id,
        trigger_kind=body.trigger_kind,
        levels=levels,
        created_by=user.id,
    )
    await AuditWriter.for_user(session, user, request).log(
        "audit.run", "product", product_id, after={"verdict": result["verdict"]}
    )
    return result


@audit_router.get("/audit-runs")
async def list_runs(
    user: Annotated[CurrentUser, Depends(require_permission("audit.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    product_id: int | None = Query(default=None),
    verdict: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> Page[AuditRunOut]:
    where = "WHERE team_id = :team"
    params: dict[str, Any] = {"team": user.team_id}
    if product_id:
        where += " AND product_id = :p"
        params["p"] = product_id
    if verdict:
        where += " AND verdict = :v"
        params["v"] = verdict
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.audit_run {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT id, product_id, trigger_kind, levels_requested, status, verdict,"
                " reject_level, llm_cost_usd, cache_hit_rate, duration_ms, created_at"
                f" FROM app.audit_run {where}"
                " ORDER BY created_at DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return Page(items=[AuditRunOut(**r) for r in rows], total=total, page=page, size=size)


@audit_router.get("/audit-runs/{run_id}")
async def get_run(
    run_id: int,
    user: Annotated[CurrentUser, Depends(require_permission("audit.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    run = (
        (
            await session.execute(
                text(
                    "SELECT id, product_id, trigger_kind, levels_requested, status, verdict,"
                    " reject_level, llm_cost_usd, cache_hit_rate, duration_ms,"
                    " started_at, finished_at, created_at"
                    " FROM app.audit_run WHERE id = :r"
                ),
                {"r": run_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if run is None:
        raise BusinessError("AUDIT_RUN_NOT_FOUND", "审核运行不存在")
    hits = (
        await session.execute(
            text(
                "SELECT level, rule_code, is_hard, score, evidence, created_at"
                " FROM app.audit_hit WHERE run_id = :r ORDER BY id"
            ),
            {"r": run_id},
        )
    ).mappings()
    return {**dict(run), "hits": [dict(h) for h in hits]}
