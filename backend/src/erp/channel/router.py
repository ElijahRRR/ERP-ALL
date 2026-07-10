"""channel 域路由（002 契约 Channel 段）。权限码与契约 x-permission 一致。"""

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.channel import service
from erp.core.audit import AuditWriter
from erp.core.authn import CurrentUser, require_permission
from erp.core.db import get_session
from erp.core.errors import BusinessError
from erp.core.settings import get_settings
from erp.identity.schemas import Page

channel_router = APIRouter(tags=["Channel"])

QUOTA_KINDS = ("listing_create", "listing_delete", "maintenance")


# ── schemas ──


class StoreOut(BaseModel):
    id: int
    channel_id: int
    code: str
    name: str
    status: str
    operating_mode: str
    dedup_exempt: bool
    is_test: bool
    proxy_id: int | None
    legal_entity: str | None
    payment_label: str | None
    opened_at: date | None
    suspended_at: datetime | None
    notes: str | None


class StoreWrite(BaseModel):
    channel_id: int | None = None
    code: str | None = Field(default=None, min_length=1, max_length=32)
    name: str | None = None
    status: str | None = Field(default=None, pattern="^(active|paused|suspended|closed)$")
    operating_mode: str | None = Field(default=None, pattern="^(build|match|mixed)$")
    dedup_exempt: bool | None = None
    is_test: bool | None = None
    legal_entity: str | None = None
    payment_label: str | None = None
    opened_at: date | None = None
    notes: str | None = None


class CredentialIn(BaseModel):
    client_id: str = Field(min_length=4)
    client_secret: str = Field(min_length=8)


class ProxyBindIn(BaseModel):
    proxy_id: int


class ProxyOut(BaseModel):
    id: int
    kind: str
    host: str
    port: int
    username: str | None
    vendor: str | None
    status: str
    expires_at: date | None
    bound_store_code: str | None = None


class ProxyWrite(BaseModel):
    kind: str | None = Field(default=None, pattern="^(socks5|http)$")
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = None
    password: str | None = None
    vendor: str | None = None
    status: str | None = Field(default=None, pattern="^(active|expired|disabled)$")
    expires_at: date | None = None
    note: str | None = None


class QuotaConfigIn(BaseModel):
    quota_kind: str = Field(pattern="^(listing_create|listing_delete|maintenance)$")
    daily_limit: int = Field(ge=0)
    enabled: bool = True


class QuotaUsageOut(BaseModel):
    quota_kind: str
    window_date: date
    used: int
    daily_limit: int | None


class IncidentWrite(BaseModel):
    store_id: int
    incident_kind: str = Field(pattern="^(suspension|warning|listing_block|other)$")
    occurred_at: datetime
    reason: str | None = None


class IncidentOut(BaseModel):
    id: int
    store_id: int
    incident_kind: str
    source: str
    occurred_at: datetime
    reason: str | None
    status: str
    sku_released_at: datetime | None
    brand_released_at: datetime | None
    appeal_notes: str | None


# ── stores ──


@channel_router.get("/stores", response_model=Page[StoreOut])
async def list_stores(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    status: str | None = None,
    _: CurrentUser = Depends(require_permission("channel.store_read")),
    session: AsyncSession = Depends(get_session),
) -> Page[StoreOut]:
    cond = " AND status = :st" if status else ""
    params: dict[str, Any] = {"l": size, "o": (page - 1) * size}
    if status:
        params["st"] = status
    total = (
        await session.execute(
            text(f"SELECT count(*) FROM app.store WHERE true{cond}"),
            params,
        )
    ).scalar_one()
    rows = await session.execute(
        text(
            "SELECT id, channel_id, code, name, status, operating_mode, dedup_exempt,"
            f" is_test, proxy_id, legal_entity, payment_label, opened_at, suspended_at, notes"
            f" FROM app.store WHERE true{cond} ORDER BY code LIMIT :l OFFSET :o"
        ),
        params,
    )
    return Page(items=[StoreOut(**r._asdict()) for r in rows], total=total, page=page, size=size)


@channel_router.post("/stores", response_model=StoreOut, status_code=201)
async def create_store(
    body: StoreWrite,
    request: Request,
    user: CurrentUser = Depends(require_permission("channel.store_write")),
    session: AsyncSession = Depends(get_session),
) -> StoreOut:
    if not (body.channel_id and body.code and body.name):
        raise BusinessError("STORE_FIELDS_REQUIRED", "channel_id/code/name 必填")
    if user.team_id is None:
        raise BusinessError(
            "TEAM_REQUIRED", "店铺团队独占：超管请以团队身份操作或指定团队（R2 支持）"
        )
    row = (
        await session.execute(
            text(
                "INSERT INTO app.store (team_id, channel_id, code, name, operating_mode,"
                " dedup_exempt, is_test, legal_entity, payment_label, opened_at, notes,"
                " created_by)"
                " VALUES (:t, :ch, :code, :name, COALESCE(:om, 'build'),"
                " COALESCE(:de, false), COALESCE(:it, false), :le, :pl, :oa, :no, :by)"
                " RETURNING id"
            ),
            {
                "t": user.team_id,
                "ch": body.channel_id,
                "code": body.code,
                "name": body.name,
                "om": body.operating_mode,
                "de": body.dedup_exempt,
                "it": body.is_test,
                "le": body.legal_entity,
                "pl": body.payment_label,
                "oa": body.opened_at,
                "no": body.notes,
                "by": user.id,
            },
        )
    ).one()
    await AuditWriter.for_user(session, user, request).log(
        "channel.store_create", "store", row.id, after={"code": body.code, "name": body.name}
    )
    return await _load_store(session, row.id)


@channel_router.get("/stores/{store_id}", response_model=StoreOut)
async def get_store(
    store_id: int,
    _: CurrentUser = Depends(require_permission("channel.store_read")),
    session: AsyncSession = Depends(get_session),
) -> StoreOut:
    return await _load_store(session, store_id)


@channel_router.patch("/stores/{store_id}", response_model=StoreOut)
async def patch_store(
    store_id: int,
    body: StoreWrite,
    request: Request,
    user: CurrentUser = Depends(require_permission("channel.store_write")),
    session: AsyncSession = Depends(get_session),
) -> StoreOut:
    updates = body.model_dump(exclude_none=True, exclude={"channel_id", "code"})
    if not updates:
        return await _load_store(session, store_id)
    sets = ", ".join(f"{col} = :{col}" for col in updates)
    row = (
        await session.execute(
            text(f"UPDATE app.store SET {sets} WHERE id = :id RETURNING id"),
            {**updates, "id": store_id},
        )
    ).first()
    if row is None:
        raise BusinessError("STORE_NOT_FOUND", "店铺不存在")
    await AuditWriter.for_user(session, user, request).log(
        "channel.store_update", "store", store_id, after=updates
    )
    return await _load_store(session, store_id)


async def _load_store(session: AsyncSession, store_id: int) -> StoreOut:
    row = (
        await session.execute(
            text(
                "SELECT id, channel_id, code, name, status, operating_mode, dedup_exempt,"
                " is_test, proxy_id, legal_entity, payment_label, opened_at, suspended_at,"
                " notes FROM app.store WHERE id = :id"
            ),
            {"id": store_id},
        )
    ).first()
    if row is None:
        raise BusinessError("STORE_NOT_FOUND", "店铺不存在")
    return StoreOut(**row._asdict())


# ── credential ──


@channel_router.get("/stores/{store_id}/credential")
async def get_credential_status(
    store_id: int,
    request: Request,
    user: CurrentUser = Depends(require_permission("channel.credential_view")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, object]:
    await _load_store(session, store_id)
    status = await service.credential_status(session, store_id)
    await AuditWriter.for_user(session, user, request).log(
        "channel.credential_view", "store_credential", store_id
    )
    return status


@channel_router.put("/stores/{store_id}/credential", status_code=204)
async def put_credential(
    store_id: int,
    body: CredentialIn,
    request: Request,
    user: CurrentUser = Depends(require_permission("channel.credential_write")),
    session: AsyncSession = Depends(get_session),
) -> None:
    await _load_store(session, store_id)
    await service.set_credential(session, store_id, body.client_id, body.client_secret, user.id)
    await AuditWriter.for_user(session, user, request).log(
        "channel.credential_write",
        "store_credential",
        store_id,
        after={"client_id": body.client_id[:4] + "****"},
    )


# ── proxy 绑定与资产 ──


@channel_router.put("/stores/{store_id}/proxy", status_code=204)
async def put_store_proxy(
    store_id: int,
    body: ProxyBindIn,
    request: Request,
    user: CurrentUser = Depends(require_permission("channel.store_write")),
    session: AsyncSession = Depends(get_session),
) -> None:
    await service.bind_proxy(session, store_id, body.proxy_id)
    await AuditWriter.for_user(session, user, request).log(
        "channel.store_proxy_bind", "store", store_id, after={"proxy_id": body.proxy_id}
    )


@channel_router.get("/proxies", response_model=Page[ProxyOut])
async def list_proxies(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("channel.proxy_read")),
    session: AsyncSession = Depends(get_session),
) -> Page[ProxyOut]:
    total = (await session.execute(text("SELECT count(*) FROM app.proxy"))).scalar_one()
    rows = await session.execute(
        text(
            "SELECT p.id, p.kind, p.host, p.port, p.username, p.vendor, p.status,"
            " p.expires_at, s.code AS bound_store_code"
            " FROM app.proxy p LEFT JOIN app.store s"
            "   ON s.proxy_id = p.id AND s.status <> 'closed'"
            " ORDER BY p.id LIMIT :l OFFSET :o"
        ),
        {"l": size, "o": (page - 1) * size},
    )
    return Page(items=[ProxyOut(**r._asdict()) for r in rows], total=total, page=page, size=size)


@channel_router.post("/proxies", status_code=201)
async def create_proxy(
    body: ProxyWrite,
    request: Request,
    user: CurrentUser = Depends(require_permission("channel.proxy_write")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    if not (body.kind and body.host and body.port):
        raise BusinessError("PROXY_FIELDS_REQUIRED", "kind/host/port 必填")
    if user.team_id is None:
        raise BusinessError("TEAM_REQUIRED", "代理默认团队隔离：超管请以团队身份操作")
    row = (
        await session.execute(
            text(
                "INSERT INTO app.proxy (team_id, kind, host, port, username,"
                " password_encrypted, vendor, expires_at, note, created_by)"
                " VALUES (:t, :k, :h, :p, :u,"
                " CASE WHEN cast(:pw AS text) IS NULL THEN NULL"
                "      ELSE pgp_sym_encrypt(cast(:pw AS text), :key) END,"
                " :v, :e, :n, :by) RETURNING id"
            ),
            {
                "t": user.team_id,
                "k": body.kind,
                "h": body.host,
                "p": body.port,
                "u": body.username,
                "pw": body.password,
                "key": _credential_key(),
                "v": body.vendor,
                "e": body.expires_at,
                "n": body.note,
                "by": user.id,
            },
        )
    ).one()
    await AuditWriter.for_user(session, user, request).log(
        "channel.proxy_create", "proxy", row.id, after={"host": body.host, "port": body.port}
    )
    return {"id": row.id}


@channel_router.patch("/proxies/{proxy_id}")
async def patch_proxy(
    proxy_id: int,
    body: ProxyWrite,
    request: Request,
    user: CurrentUser = Depends(require_permission("channel.proxy_write")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    updates = body.model_dump(exclude_none=True, exclude={"password"})
    for col, val in updates.items():
        await session.execute(
            text(f"UPDATE app.proxy SET {col} = :v WHERE id = :id"),
            {"v": val, "id": proxy_id},
        )
    if body.password is not None:
        await session.execute(
            text(
                "UPDATE app.proxy SET password_encrypted = pgp_sym_encrypt(:pw, :key)"
                " WHERE id = :id"
            ),
            {"pw": body.password, "key": _credential_key(), "id": proxy_id},
        )
    await AuditWriter.for_user(session, user, request).log(
        "channel.proxy_update",
        "proxy",
        proxy_id,
        after={k: v for k, v in updates.items()}
        | ({"password": "<reset>"} if body.password else {}),
    )
    return {"status": "ok"}


def _credential_key() -> str:
    return get_settings().credential_key


# ── quota ──


@channel_router.get("/stores/{store_id}/quota-configs", response_model=list[QuotaConfigIn])
async def get_quota_configs(
    store_id: int,
    _: CurrentUser = Depends(require_permission("channel.store_read")),
    session: AsyncSession = Depends(get_session),
) -> list[QuotaConfigIn]:
    rows = await session.execute(
        text(
            "SELECT quota_kind, daily_limit, enabled FROM app.quota_config"
            " WHERE store_id = :sid ORDER BY quota_kind"
        ),
        {"sid": store_id},
    )
    return [QuotaConfigIn(**r._asdict()) for r in rows]


@channel_router.put("/stores/{store_id}/quota-configs")
async def put_quota_configs(
    store_id: int,
    body: list[QuotaConfigIn],
    request: Request,
    user: CurrentUser = Depends(require_permission("channel.quota_write")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    await _load_store(session, store_id)
    for item in body:
        await session.execute(
            text(
                "INSERT INTO app.quota_config (store_id, quota_kind, daily_limit, enabled,"
                " updated_by) VALUES (:sid, :k, :lim, :en, :by)"
                " ON CONFLICT (store_id, quota_kind) DO UPDATE"
                " SET daily_limit = excluded.daily_limit, enabled = excluded.enabled,"
                " updated_by = excluded.updated_by"
            ),
            {
                "sid": store_id,
                "k": item.quota_kind,
                "lim": item.daily_limit,
                "en": item.enabled,
                "by": user.id,
            },
        )
    await AuditWriter.for_user(session, user, request).log(
        "channel.quota_write",
        "quota_config",
        store_id,
        after={i.quota_kind: i.daily_limit for i in body},
    )
    return {"status": "ok"}


@channel_router.get("/stores/{store_id}/quota-usage", response_model=list[QuotaUsageOut])
async def get_quota_usage(
    store_id: int,
    day: date | None = Query(None, alias="date"),
    _: CurrentUser = Depends(require_permission("channel.store_read")),
    session: AsyncSession = Depends(get_session),
) -> list[QuotaUsageOut]:
    rows = await session.execute(
        text(
            "SELECT u.quota_kind, u.window_date, u.used, c.daily_limit"
            " FROM app.quota_usage u LEFT JOIN app.quota_config c"
            "   ON c.store_id = u.store_id AND c.quota_kind = u.quota_kind"
            " WHERE u.store_id = :sid AND u.window_date = COALESCE(:d, u.window_date)"
            " ORDER BY u.window_date DESC, u.quota_kind LIMIT 50"
        ),
        {"sid": store_id, "d": day},
    )
    return [QuotaUsageOut(**r._asdict()) for r in rows]


# ── incidents ──


@channel_router.get("/store-incidents", response_model=Page[IncidentOut])
async def list_incidents(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    status: str | None = None,
    _: CurrentUser = Depends(require_permission("channel.incident_read")),
    session: AsyncSession = Depends(get_session),
) -> Page[IncidentOut]:
    cond = " AND status = :st" if status else ""
    params: dict[str, Any] = {"l": size, "o": (page - 1) * size}
    if status:
        params["st"] = status
    total = (
        await session.execute(
            text(f"SELECT count(*) FROM app.store_incident WHERE true{cond}"),
            params,
        )
    ).scalar_one()
    rows = await session.execute(
        text(
            "SELECT id, store_id, incident_kind, source, occurred_at, reason, status,"
            f" sku_released_at, brand_released_at, appeal_notes"
            f" FROM app.store_incident WHERE true{cond}"
            " ORDER BY occurred_at DESC LIMIT :l OFFSET :o"
        ),
        params,
    )
    return Page(items=[IncidentOut(**r._asdict()) for r in rows], total=total, page=page, size=size)


@channel_router.post("/store-incidents", status_code=201)
async def create_incident(
    body: IncidentWrite,
    request: Request,
    user: CurrentUser = Depends(require_permission("channel.incident_write")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    store = await _load_store(session, body.store_id)
    row = (
        await session.execute(
            text(
                "INSERT INTO app.store_incident (team_id, store_id, incident_kind, source,"
                " occurred_at, reason, created_by)"
                " SELECT s.team_id, s.id, :k, 'manual', :oa, :r, :by FROM app.store s"
                " WHERE s.id = :sid RETURNING id"
            ),
            {
                "k": body.incident_kind,
                "oa": body.occurred_at,
                "r": body.reason,
                "by": user.id,
                "sid": body.store_id,
            },
        )
    ).one()
    if body.incident_kind == "suspension":
        # 封店联动：店铺状态 + 时间戳（SKU/品牌释放作业随 R2#7 catalog 联动接入）
        await session.execute(
            text("UPDATE app.store SET status = 'suspended', suspended_at = :oa WHERE id = :sid"),
            {"oa": body.occurred_at, "sid": body.store_id},
        )
    await AuditWriter.for_user(session, user, request).log(
        "channel.incident_create",
        "store_incident",
        row.id,
        after={"store": store.code, "kind": body.incident_kind},
    )
    return {"id": row.id}


class IncidentTransitionIn(BaseModel):
    to_status: str = Field(pattern="^(observing|appealing|resolved|closed)$")
    notes: str | None = None


@channel_router.post("/store-incidents/{incident_id}/transition")
async def transition_incident(
    incident_id: int,
    body: IncidentTransitionIn,
    request: Request,
    user: CurrentUser = Depends(require_permission("channel.incident_write")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    row = (
        await session.execute(
            text(
                "UPDATE app.store_incident SET status = :st,"
                " appeal_notes = COALESCE(:n, appeal_notes),"
                " closed_at = CASE WHEN :st IN ('resolved','closed') THEN now() END"
                " WHERE id = :id RETURNING store_id, incident_kind, status"
            ),
            {"st": body.to_status, "n": body.notes, "id": incident_id},
        )
    ).first()
    if row is None:
        raise BusinessError("INCIDENT_NOT_FOUND", "事件不存在")
    if row.incident_kind == "suspension" and body.to_status == "resolved":
        # 恢复由人工确认（02-channel 设计），店铺回 active
        await session.execute(
            text("UPDATE app.store SET status = 'active', suspended_at = NULL WHERE id = :sid"),
            {"sid": row.store_id},
        )
    await AuditWriter.for_user(session, user, request).log(
        "channel.incident_transition",
        "store_incident",
        incident_id,
        after={"to_status": body.to_status},
    )
    return {"status": "ok"}
