"""合规域路由：导入作业历史（只读）。

导入执行走 CLI（erp.tools.import_blacklist，部署机 api 容器内跑，读本地文件）——
大文件不经 HTTP 上传，且黑名单为全局数据需超管 system_tx。此路由仅供前端看进度。
"""

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

compliance_router = APIRouter(tags=["Compliance"])


class ImportJobOut(BaseModel):
    id: int
    team_id: int | None
    domain: str
    source_name: str
    format: str
    status: str
    total_rows: int
    ok_rows: int
    err_rows: int
    skip_rows: int
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime


@compliance_router.get("/import-jobs")
async def list_import_jobs(
    user: Annotated[CurrentUser, Depends(require_permission("compliance.import_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    domain: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> Page[ImportJobOut]:
    where = "WHERE true"
    params: dict[str, Any] = {}
    if domain:
        where += " AND domain = :d"
        params["d"] = domain
    if status:
        where += " AND status = :st"
        params["st"] = status
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.import_job {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT id, team_id, domain, source_name, format, status, total_rows,"
                " ok_rows, err_rows, skip_rows, started_at, finished_at, created_at"
                f" FROM app.import_job {where}"
                " ORDER BY id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return Page(items=[ImportJobOut(**r) for r in rows], total=total, page=page, size=size)


@compliance_router.get("/import-jobs/{job_id}")
async def get_import_job(
    job_id: int,
    user: Annotated[CurrentUser, Depends(require_permission("compliance.import_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, domain, source_kind, source_name, format, status,"
                    " total_rows, ok_rows, err_rows, skip_rows, chunk_size, verify,"
                    " error_report_ref, started_at, finished_at, created_at"
                    " FROM app.import_job WHERE id = :j"
                ),
                {"j": job_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise BusinessError("IMPORT_JOB_NOT_FOUND", "导入作业不存在")
    return dict(row)
