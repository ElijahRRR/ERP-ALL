"""采集域路由。

两套面孔，物理分开：
- scrape_router：UI 契约端点（/scrape-jobs、/worker-nodes），用户会话 + 权限码。
- worker_router：worker 拨入机器协议（/worker/v1/*），node_key + 机器令牌认证，
  在 system_tx 下执行（worker 不是用户，任务跨团队派发；团队归属由任务行自带）。
  该协议不进 002 UI 契约，规格见 specs/002-api-contract/worker-protocol.md。
"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.audit import AuditWriter
from erp.core.authn import CurrentUser, require_permission
from erp.core.db import get_session, get_session_factory, system_tx
from erp.core.errors import BusinessError
from erp.identity.schemas import Page
from erp.scrape import service

scrape_router = APIRouter(tags=["Scrape"])
worker_router = APIRouter(tags=["ScrapeWorker"], prefix="/worker/v1")


# ── UI schemas ──


class JobCreate(BaseModel):
    source: str = Field(pattern="^(amazon|walmart)$")
    job_kind: str = Field(pattern="^(product_detail|keyword|seller|bestseller|price_watch)$")
    input: dict[str, Any]
    priority: int = Field(default=100, ge=1, le=1000)


class JobOut(BaseModel):
    id: int
    source: str
    job_kind: str
    status: str
    priority: int
    total_tasks: int
    done_tasks: int
    failed_tasks: int
    requested_by: int | None
    finished_at: datetime | None
    created_at: datetime


class WorkerNodeOut(BaseModel):
    id: int
    node_key: str
    kind: str
    version: str | None
    status: str
    capacity: int
    last_heartbeat_at: datetime | None
    stats: dict[str, Any]


# ── UI 端点 ──


@scrape_router.post("/scrape-jobs", status_code=201)
async def create_job(
    body: JobCreate,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("scrape.job_write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    if user.team_id is None:
        raise BusinessError("SCRAPE_TEAM_REQUIRED", "超管需切换到具体团队后建采集作业")
    result = await service.create_job(
        session,
        team_id=user.team_id,
        source=body.source,
        job_kind=body.job_kind,
        input_=body.input,
        priority=body.priority,
        requested_by=user.id,
    )
    await AuditWriter.for_user(session, user, request).log(
        "scrape.job_create",
        "scrape_job",
        result["id"],
        after={"job_kind": body.job_kind, "total_tasks": result["total_tasks"]},
    )
    return result


@scrape_router.get("/scrape-jobs")
async def list_jobs(
    user: Annotated[CurrentUser, Depends(require_permission("scrape.job_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    status: str | None = Query(default=None),
    job_kind: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> Page[JobOut]:
    where = "WHERE team_id = :team"
    params: dict[str, Any] = {"team": user.team_id}
    if status:
        where += " AND status = :st"
        params["st"] = status
    if job_kind:
        where += " AND job_kind = :jk"
        params["jk"] = job_kind
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.scrape_job {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT id, source, job_kind, status, priority, total_tasks,"
                " done_tasks, failed_tasks, requested_by, finished_at, created_at"
                f" FROM app.scrape_job {where}"
                " ORDER BY created_at DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return Page(items=[JobOut(**r) for r in rows], total=total, page=page, size=size)


@scrape_router.get("/scrape-jobs/{job_id}")
async def get_job(
    job_id: int,
    user: Annotated[CurrentUser, Depends(require_permission("scrape.job_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    job = (
        (
            await session.execute(
                text(
                    "SELECT id, source, job_kind, input, status, priority, total_tasks,"
                    " done_tasks, failed_tasks, requested_by, finished_at, created_at"
                    " FROM app.scrape_job WHERE id = :j"
                ),
                {"j": job_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if job is None:
        raise BusinessError("SCRAPE_JOB_NOT_FOUND", "作业不存在")
    tasks = (
        await session.execute(
            text(
                "SELECT status, count(*) AS n FROM app.scrape_task"
                " WHERE job_id = :j GROUP BY status"
            ),
            {"j": job_id},
        )
    ).mappings()
    return {**dict(job), "task_stats": {r["status"]: r["n"] for r in tasks}}


@scrape_router.post("/scrape-jobs/{job_id}/cancel")
async def cancel_job(
    job_id: int,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("scrape.job_write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    result = await service.cancel_job(session, job_id)
    await AuditWriter.for_user(session, user, request).log(
        "scrape.job_cancel", "scrape_job", job_id
    )
    return result


@scrape_router.get("/worker-nodes")
async def list_worker_nodes(
    user: Annotated[CurrentUser, Depends(require_permission("scrape.node_admin"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[WorkerNodeOut]:
    rows = (
        await session.execute(
            text(
                "SELECT id, node_key, kind, version, status, capacity,"
                " last_heartbeat_at, stats FROM app.worker_node ORDER BY id"
            )
        )
    ).mappings()
    return [WorkerNodeOut(**r) for r in rows]


# ── worker 拨入协议（机器端点）──


class NodeRegisterIn(BaseModel):
    node_key: str = Field(min_length=3, max_length=64)
    kind: str = Field(pattern="^(scraper_amazon|scraper_walmart)$")
    enroll_token: str
    version: str | None = None
    capacity: int = Field(default=1, ge=1, le=64)


class NodeSyncIn(BaseModel):
    version: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    window_state: dict[str, Any] = Field(default_factory=dict)


class TaskReleaseIn(BaseModel):
    tasks: list[dict[str, int]]  # [{task_id, attempt}]


class ResultIn(BaseModel):
    task_id: int
    attempt: int
    success: bool = True
    payload: dict[str, Any] | None = None
    payload_ref: str | None = None
    fetched_at: datetime | None = None
    error_type: str | None = None
    error_detail: str | None = None


async def _node_auth(
    x_node_key: Annotated[str, Header()],
    x_node_token: Annotated[str, Header()],
) -> dict[str, Any]:
    async with system_tx(get_session_factory()) as s:
        return await service.authenticate_node(s, x_node_key, x_node_token)


@worker_router.post("/register", status_code=201)
async def register(body: NodeRegisterIn) -> dict[str, Any]:
    async with system_tx(get_session_factory()) as s:
        return await service.register_node(
            s,
            node_key=body.node_key,
            kind=body.kind,
            enroll_token=body.enroll_token,
            version=body.version,
            capacity=body.capacity,
        )


@worker_router.post("/sync")
async def sync(
    body: NodeSyncIn, node: Annotated[dict[str, Any], Depends(_node_auth)]
) -> dict[str, Any]:
    async with system_tx(get_session_factory()) as s:
        return await service.sync_node(
            s,
            node,
            version=body.version,
            metrics=body.metrics,
            window_state=body.window_state,
        )


@worker_router.get("/tasks/pull")
async def tasks_pull(
    node: Annotated[dict[str, Any], Depends(_node_auth)],
    count: int = Query(default=10, ge=1, le=100),
) -> dict[str, Any]:
    async with system_tx(get_session_factory()) as s:
        tasks = await service.pull_tasks(s, node, count=count)
    return {"tasks": tasks}


@worker_router.post("/tasks/release")
async def tasks_release(
    body: TaskReleaseIn, node: Annotated[dict[str, Any], Depends(_node_auth)]
) -> dict[str, Any]:
    async with system_tx(get_session_factory()) as s:
        released = await service.release_tasks(s, node, body.tasks)
    return {"released": released}


@worker_router.post("/tasks/result")
async def tasks_result(
    body: ResultIn, node: Annotated[dict[str, Any], Depends(_node_auth)]
) -> dict[str, Any]:
    async with system_tx(get_session_factory()) as s:
        return await service.submit_result(
            s,
            node,
            task_id=body.task_id,
            attempt=body.attempt,
            success=body.success,
            payload=body.payload,
            payload_ref=body.payload_ref,
            fetched_at=body.fetched_at,
            error_type=body.error_type,
            error_detail=body.error_detail,
        )
