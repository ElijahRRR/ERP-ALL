"""合规域路由（R2-12 增量5 合规中心）。

- 导入作业历史（只读）+ 报错报告：导入执行走 CLI（erp.tools.import_blacklist，部署机
  api 容器内跑读本地文件——大文件不经 HTTP、全局数据需超管 system_tx），此路由仅供
  前端看进度 + 拉报错明细（对账可见性底线 008§6），不提供 HTTP 上传。
- 黑名单管理（RS-04D 断言账本）：canonical 有效项列表 / 断言追溯 / 候选闸队列 +
  人工登记·裁决·撤销（写侧全走 erp.compliance.assertion 服务，canonical 由投影维护）。
- 商标库查询（trigram + Nice 类）、TRO 案件查询——合规供给链的人眼窗口。
"""

from datetime import date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.compliance import assertion as assertion_svc
from erp.core.audit import AuditWriter
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


@compliance_router.get("/import-jobs/{job_id}/error-report")
async def get_import_error_report(
    job_id: int,
    user: Annotated[CurrentUser, Depends(require_permission("compliance.import_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """报错报告：err_rows + 逐块核对 + 逐行样本（import_rows 落 verify.sample_errors，≤50）。"""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, domain, status, err_rows, error_report_ref, verify"
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
    verify = row["verify"] or {}
    return {
        "job_id": row["id"],
        "domain": row["domain"],
        "status": row["status"],
        "err_rows": row["err_rows"],
        "error_report_ref": row["error_report_ref"],
        "chunks": verify.get("chunks", []),
        "errors": verify.get("sample_errors", []),
    }


# ─────────────────────── 黑名单管理（RS-04D 断言账本）───────────────────────

# domain → (canonical 表, 主体列, 展示列|None)；单一真相取自断言服务，避免各处再抄一份
_BL_TABLE = assertion_svc.TABLE_BY_DOMAIN


class BlacklistEntryOut(BaseModel):
    team_id: int | None
    subject: str
    display: str | None
    reason: str | None
    source: str
    status: str
    added_at: datetime | None


class AssertionOut(BaseModel):
    id: int
    team_id: int | None
    domain: str
    subject_norm: str
    subject_display: str | None
    verdict: str
    source: str
    source_ref: str | None
    reason: str | None
    status: str
    created_at: datetime | None
    decided_at: datetime | None
    revoked_at: datetime | None
    revoke_reason: str | None


_ASSERTION_COLS = (
    "id, team_id, domain, subject_norm, subject_display, verdict, source,"
    " source_ref, reason, status, created_at, decided_at, revoked_at, revoke_reason"
)


@compliance_router.get("/blacklist")
async def list_blacklist(
    user: Annotated[CurrentUser, Depends(require_permission("compliance.blacklist_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    domain: str = Query(...),
    q: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> Page[BlacklistEntryOut]:
    """canonical 有效项列表（blacklist_* active 行 = 断言投影当前生效面）。RLS 按 team 隔离。"""
    if domain not in _BL_TABLE:
        raise BusinessError("BLACKLIST_DOMAIN_INVALID", f"未知黑名单域：{domain}")
    table, subj_col, disp_col = _BL_TABLE[domain]  # 受控常量，非用户注入
    disp_sel = disp_col if disp_col else "NULL"
    where = "WHERE status = 'active'"
    params: dict[str, Any] = {}
    if q:
        where += f" AND {subj_col} ILIKE :q"
        params["q"] = f"%{q}%"
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.{table} {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                f"SELECT team_id, {subj_col} AS subject, {disp_sel} AS display, reason,"
                f" source, status, added_at FROM app.{table} {where}"
                " ORDER BY added_at DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return Page(items=[BlacklistEntryOut(**r) for r in rows], total=total, page=page, size=size)


@compliance_router.get("/blacklist/trace")
async def blacklist_trace(
    user: Annotated[CurrentUser, Depends(require_permission("compliance.blacklist_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    domain: str = Query(...),
    subject: str = Query(...),
) -> list[AssertionOut]:
    """断言追溯：某主体全部源断言（任意状态，含撤销留行），最近在前。"""
    if domain not in _BL_TABLE:
        raise BusinessError("BLACKLIST_DOMAIN_INVALID", f"未知黑名单域：{domain}")
    rows = (
        await session.execute(
            text(
                f"SELECT {_ASSERTION_COLS} FROM app.blacklist_assertion"
                " WHERE domain = :d AND subject_norm = :s"
                " ORDER BY created_at DESC, id DESC"
            ),
            {"d": domain, "s": subject},
        )
    ).mappings()
    return [AssertionOut(**r) for r in rows]


@compliance_router.get("/blacklist/pending")
async def list_pending_assertions(
    user: Annotated[CurrentUser, Depends(require_permission("compliance.blacklist_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    domain: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> Page[AssertionOut]:
    """候选断言队列（D-Q65② 报错回收人工闸；pending 待裁决），最早在前（先到先审）。"""
    where = "WHERE status = 'pending'"
    params: dict[str, Any] = {}
    if domain:
        where += " AND domain = :d"
        params["d"] = domain
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.blacklist_assertion {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                f"SELECT {_ASSERTION_COLS} FROM app.blacklist_assertion {where}"
                " ORDER BY created_at ASC, id ASC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return Page(items=[AssertionOut(**r) for r in rows], total=total, page=page, size=size)


class AssertionIn(BaseModel):
    domain: str
    subject_norm: str
    subject_display: str | None = None
    verdict: str = "block"
    reason: str | None = None


@compliance_router.post("/blacklist/assertions", status_code=201)
async def record_manual_assertion(
    body: AssertionIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("compliance.blacklist_write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """人工登记断言（source=manual，直挂 active——人工即权威）。

    verdict=block 拉黑；verdict=allow 白名单裁决（DDL 强制仅 manual，压一切自动源）。
    team_id 取用户当前作用域：超管未切团队=全局（NULL），团队管理员=本团队（RLS 一致）。
    """
    if body.verdict not in ("block", "allow"):
        raise BusinessError("BLACKLIST_VERDICT_INVALID", f"非法裁决：{body.verdict}")
    res = await assertion_svc.record_assertion(
        session,
        team_id=user.team_id,
        domain=body.domain,
        subject_norm=body.subject_norm,
        subject_display=body.subject_display,
        source="manual",
        reason=body.reason,
        verdict=body.verdict,
        status="active",
        created_by=user.id,
    )
    await AuditWriter.for_user(session, user, request).log(
        "compliance.blacklist_record",
        "blacklist_assertion",
        res.get("assertion_id"),
        after={"domain": body.domain, "subject": body.subject_norm, "verdict": body.verdict},
    )
    return res


class DecideIn(BaseModel):
    approve: bool
    reason: str | None = None


@compliance_router.post("/blacklist/assertions/{assertion_id}/decide")
async def decide_assertion(
    assertion_id: int,
    body: DecideIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("compliance.blacklist_write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """裁决候选断言（pending→active/revoked，D-Q65② 人工闸）。"""
    res = await assertion_svc.decide_assertion(
        session,
        assertion_id=assertion_id,
        team_id=user.team_id,
        approve=body.approve,
        actor_id=user.id,
        reason=body.reason,
    )
    await AuditWriter.for_user(session, user, request).log(
        "compliance.blacklist_decide",
        "blacklist_assertion",
        assertion_id,
        after={"approved": body.approve},
    )
    return res


class RevokeIn(BaseModel):
    reason: str | None = None


@compliance_router.post("/blacklist/assertions/{assertion_id}/revoke")
async def revoke_assertion(
    assertion_id: int,
    body: RevokeIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("compliance.blacklist_write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """撤销断言（active/pending→revoked，留行）；主体是否仍拉黑由余源重投影决定。"""
    res = await assertion_svc.revoke_assertion(
        session,
        assertion_id=assertion_id,
        team_id=user.team_id,
        actor_id=user.id,
        reason=body.reason,
    )
    await AuditWriter.for_user(session, user, request).log(
        "compliance.blacklist_revoke", "blacklist_assertion", assertion_id
    )
    return res


# ─────────────────────── 商标库查询（trigram + Nice）───────────────────────


class TrademarkOut(BaseModel):
    serial_no: str
    mark_text: str | None
    mark_norm: str | None
    status_code: str | None
    is_live: bool | None
    nice_classes: list[int]
    owner_name: str | None
    filed_date: date | None
    registered_date: date | None


@compliance_router.get("/trademarks")
async def list_trademarks(
    user: Annotated[CurrentUser, Depends(require_permission("compliance.trademark_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = Query(default=None),
    nice: int | None = Query(default=None),
    live_only: bool = Query(default=False),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> Page[TrademarkOut]:
    """商标查询：mark_norm ILIKE 模糊（走 trgm gin 索引）+ Nice 类过滤 + LIVE 过滤。"""
    where = "WHERE true"
    params: dict[str, Any] = {}
    if q:
        where += " AND mark_norm ILIKE :q"
        params["q"] = f"%{q.strip().lower()}%"
    if nice is not None:
        where += " AND nice_classes @> ARRAY[:n]::smallint[]"
        params["n"] = nice
    if live_only:
        where += " AND is_live IS TRUE"
    total = (
        await session.execute(text(f"SELECT count(*) FROM refdata.trademark {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT serial_no, mark_text, mark_norm, status_code, is_live,"
                " COALESCE(nice_classes, '{}') AS nice_classes, owner_name, filed_date,"
                f" registered_date FROM refdata.trademark {where}"
                " ORDER BY (mark_norm = :exact) DESC NULLS LAST, mark_norm LIMIT :lim OFFSET :off"
            ),
            {**params, "exact": (q or "").strip().lower(), "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return Page(items=[TrademarkOut(**r) for r in rows], total=total, page=page, size=size)


# ─────────────────────── TRO 案件查询 ───────────────────────


class TroCaseOut(BaseModel):
    id: int
    case_no: str
    court: str | None
    filed_date: date | None
    plaintiff: str | None
    law_firm: str | None
    brand_terms: list[str]
    status: str
    source: str
    imported_at: datetime | None


@compliance_router.get("/tro-cases")
async def list_tro_cases(
    user: Annotated[CurrentUser, Depends(require_permission("compliance.tro_read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    q: str | None = Query(default=None),
    status: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> Page[TroCaseOut]:
    """TRO 案件列表：案号/原告/品牌词模糊（q）+ 状态过滤，最近立案在前。"""
    where = "WHERE true"
    params: dict[str, Any] = {}
    if q:
        where += " AND (case_no ILIKE :q OR plaintiff ILIKE :q OR brand_terms::text ILIKE :q)"
        params["q"] = f"%{q}%"
    if status:
        where += " AND status = :st"
        params["st"] = status
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.tro_case {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT id, case_no, court, filed_date, plaintiff, law_firm,"
                " COALESCE(brand_terms, '[]'::jsonb) AS brand_terms, status, source, imported_at"
                f" FROM app.tro_case {where}"
                " ORDER BY filed_date DESC NULLS LAST, id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return Page(items=[TroCaseOut(**r) for r in rows], total=total, page=page, size=size)
