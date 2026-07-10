"""identity 域路由（002 契约 Auth/Identity 段）。

- 每个 operation 的权限点与契约 x-permission 一字不差。
- 全部写操作经 AuditWriter 记账（唯一出口纪律）。
- 团队作用域由 RLS + GUC 自动生效（authn.get_current_user 注入），
  路由层不出现 team_id 过滤条件——这是设计而非遗漏。
"""

from typing import Any

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.audit import AuditWriter
from erp.core.authn import (
    AuthError,
    CurrentUser,
    PermissionDenied,
    get_current_user,
    require_permission,
)
from erp.core.db import get_session, get_session_factory
from erp.core.errors import BusinessError
from erp.core.security import hash_password
from erp.identity import service
from erp.identity.schemas import (
    AuditLogOut,
    DictItemOut,
    LoginIn,
    MeOut,
    Page,
    PermissionCodesIn,
    PermissionOut,
    RefreshIn,
    RoleIdsIn,
    RoleOut,
    RoleWrite,
    TeamOut,
    TeamWrite,
    TokenPairOut,
    UserCreate,
    UserOut,
    UserPatch,
)

auth_router = APIRouter(tags=["Auth"])
identity_router = APIRouter(tags=["Identity"])


# ─────────────────────────── Auth ───────────────────────────


@auth_router.post("/auth/login", response_model=TokenPairOut)
async def login(body: LoginIn) -> TokenPairOut:
    pair = await service.login(get_session_factory(), body.username, body.password)
    return TokenPairOut(**pair.__dict__)


@auth_router.post("/auth/refresh", response_model=TokenPairOut)
async def refresh(body: RefreshIn) -> TokenPairOut:
    pair = await service.refresh(get_session_factory(), body.refresh_token)
    return TokenPairOut(**pair.__dict__)


@auth_router.post("/auth/logout", status_code=204)
async def logout(
    request: Request,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    # 吊销全部已发 token（token_version+1）；无状态 access 在 15min 内自然过期
    await session.execute(
        text("UPDATE app.app_user SET token_version = token_version + 1 WHERE id = :id"),
        {"id": user.id},
    )
    await AuditWriter.for_user(session, user, request).log("identity.logout", "app_user", user.id)
    return Response(status_code=204)


@auth_router.get("/me", response_model=MeOut)
async def me(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> MeOut:
    u = await _load_user(session, user.id)
    return MeOut(user=u, permissions=sorted(user.permissions))


# ─────────────────────────── Teams（超管） ───────────────────────────


@identity_router.get("/teams", response_model=Page[TeamOut])
async def list_teams(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("identity.team_admin")),
    session: AsyncSession = Depends(get_session),
) -> Page[TeamOut]:
    total = (await session.execute(text("SELECT count(*) FROM app.team"))).scalar_one()
    rows = await session.execute(
        text("SELECT id, name, status, created_at FROM app.team ORDER BY id LIMIT :l OFFSET :o"),
        {"l": size, "o": (page - 1) * size},
    )
    return Page(items=[TeamOut(**r._asdict()) for r in rows], total=total, page=page, size=size)


@identity_router.post("/teams", response_model=TeamOut, status_code=201)
async def create_team(
    body: TeamWrite,
    request: Request,
    user: CurrentUser = Depends(require_permission("identity.team_admin")),
    session: AsyncSession = Depends(get_session),
) -> TeamOut:
    row = (
        await session.execute(
            text("INSERT INTO app.team (name) VALUES (:n) RETURNING id, name, status, created_at"),
            {"n": body.name},
        )
    ).one()
    # 复制全局模板角色到新团队（含权限映射）。
    # 必须两条语句：同一语句内 CTE 新插入的 role 行对 RLS 子查询不可见（语句快照），
    # 合并写会触发 role_permission 的 WITH CHECK 误拒——踩坑记录。
    await session.execute(
        text(
            "INSERT INTO app.role (team_id, name, description)"
            " SELECT :tid, name, description FROM app.role WHERE team_id IS NULL"
        ),
        {"tid": row.id},
    )
    await session.execute(
        text(
            "INSERT INTO app.role_permission (role_id, permission_code)"
            " SELECT nr.id, rp.permission_code"
            " FROM app.role nr"
            " JOIN app.role tpl ON tpl.team_id IS NULL AND tpl.name = nr.name"
            " JOIN app.role_permission rp ON rp.role_id = tpl.id"
            " WHERE nr.team_id = :tid"
        ),
        {"tid": row.id},
    )
    await AuditWriter.for_user(session, user, request).log(
        "identity.team_create", "team", row.id, after={"name": body.name}
    )
    return TeamOut(**row._asdict())


@identity_router.patch("/teams/{team_id}", response_model=TeamOut)
async def patch_team(
    team_id: int,
    body: TeamWrite,
    request: Request,
    user: CurrentUser = Depends(require_permission("identity.team_admin")),
    session: AsyncSession = Depends(get_session),
) -> TeamOut:
    row = (
        await session.execute(
            text(
                "UPDATE app.team SET name = COALESCE(:n, name), status = COALESCE(:s, status)"
                " WHERE id = :id RETURNING id, name, status, created_at"
            ),
            {"id": team_id, "n": body.name, "s": body.status},
        )
    ).first()
    if row is None:
        raise BusinessError("TEAM_NOT_FOUND", "团队不存在")
    await AuditWriter.for_user(session, user, request).log(
        "identity.team_update", "team", team_id, after=body.model_dump(exclude_none=True)
    )
    return TeamOut(**row._asdict())


# ─────────────────────────── Users ───────────────────────────


async def _load_user(session: AsyncSession, user_id: int) -> UserOut:
    row = (
        await session.execute(
            text(
                "SELECT id, team_id, username, display_name, is_super, status, last_login_at"
                " FROM app.auth_user_by_id(:id)"
            ),
            {"id": user_id},
        )
    ).first()
    if row is None:
        raise BusinessError("USER_NOT_FOUND", "用户不存在")
    roles = await _roles_of(session, user_id)
    return UserOut(**row._asdict(), roles=roles)


async def _roles_of(session: AsyncSession, user_id: int) -> list[RoleOut]:
    rows = await session.execute(
        text(
            "SELECT r.id, r.team_id, r.name, r.description,"
            " COALESCE(array_agg(rp.permission_code) FILTER (WHERE rp.permission_code"
            " IS NOT NULL), '{}') AS permission_codes"
            " FROM app.user_role ur JOIN app.role r ON r.id = ur.role_id"
            " LEFT JOIN app.role_permission rp ON rp.role_id = r.id"
            " WHERE ur.user_id = :uid GROUP BY r.id ORDER BY r.id"
        ),
        {"uid": user_id},
    )
    return [RoleOut(**r._asdict()) for r in rows]


@identity_router.get("/users", response_model=Page[UserOut])
async def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    _: CurrentUser = Depends(require_permission("identity.user_read")),
    session: AsyncSession = Depends(get_session),
) -> Page[UserOut]:
    total = (await session.execute(text("SELECT count(*) FROM app.app_user"))).scalar_one()
    rows = (
        await session.execute(
            text("SELECT id FROM app.app_user ORDER BY id LIMIT :l OFFSET :o"),
            {"l": size, "o": (page - 1) * size},
        )
    ).all()
    items = [await _load_user(session, r.id) for r in rows]
    return Page(items=items, total=total, page=page, size=size)


@identity_router.post("/users", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate,
    request: Request,
    user: CurrentUser = Depends(require_permission("identity.user_write")),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    team_id = user.team_id
    if user.is_super:
        if body.team_id is None:
            raise BusinessError("TEAM_REQUIRED", "超管建用户必须指定 team_id")
        team_id = body.team_id
    elif body.team_id is not None and body.team_id != user.team_id:
        raise PermissionDenied("identity.team_admin")

    row = (
        await session.execute(
            text(
                "INSERT INTO app.app_user (team_id, username, password_hash, display_name,"
                " created_by) VALUES (:t, lower(:u), :p, :d, :c) RETURNING id"
            ),
            {
                "t": team_id,
                "u": body.username,
                "p": hash_password(body.password),
                "d": body.display_name,
                "c": user.id,
            },
        )
    ).one()
    await AuditWriter.for_user(session, user, request).log(
        "identity.user_create",
        "app_user",
        row.id,
        after={"username": body.username, "team_id": team_id},
    )
    return await _load_user(session, row.id)


@identity_router.get("/users/{user_id}", response_model=UserOut)
async def get_user(
    user_id: int,
    _: CurrentUser = Depends(require_permission("identity.user_read")),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    # RLS 兜底：跨团队 id 在 app_user 直查为空 → 404 语义由 _guard 保证
    await _guard_same_team(session, user_id)
    return await _load_user(session, user_id)


@identity_router.patch("/users/{user_id}", response_model=UserOut)
async def patch_user(
    user_id: int,
    body: UserPatch,
    request: Request,
    user: CurrentUser = Depends(require_permission("identity.user_write")),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    await _guard_same_team(session, user_id)
    updates: dict[str, Any] = {}
    if body.display_name is not None:
        updates["display_name"] = body.display_name
    if body.status is not None:
        updates["status"] = body.status
    if body.password is not None:
        updates["password_hash"] = hash_password(body.password)
        # 重置密码同时吊销旧凭证
        await session.execute(
            text("UPDATE app.app_user SET token_version = token_version + 1 WHERE id = :id"),
            {"id": user_id},
        )
    for col, val in updates.items():
        await session.execute(
            text(f"UPDATE app.app_user SET {col} = :v WHERE id = :id"),
            {"v": val, "id": user_id},
        )
    await AuditWriter.for_user(session, user, request).log(
        "identity.user_update",
        "app_user",
        user_id,
        after={k: ("<reset>" if k == "password_hash" else v) for k, v in updates.items()},
    )
    return await _load_user(session, user_id)


@identity_router.put("/users/{user_id}/roles", response_model=UserOut)
async def set_user_roles(
    user_id: int,
    body: RoleIdsIn,
    request: Request,
    user: CurrentUser = Depends(require_permission("identity.user_write")),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    await _guard_same_team(session, user_id)
    # 目标角色必须是本团队角色（全局模板不可直挂用户）
    if body.role_ids:
        n = (
            await session.execute(
                text("SELECT count(*) FROM app.role WHERE id = ANY(:ids) AND team_id IS NOT NULL"),
                {"ids": body.role_ids},
            )
        ).scalar_one()
        if n != len(set(body.role_ids)):
            raise BusinessError("ROLE_INVALID", "包含不存在/非本团队/模板角色，不可挂载")
    before = [r.id for r in await _roles_of(session, user_id)]
    await session.execute(text("DELETE FROM app.user_role WHERE user_id = :uid"), {"uid": user_id})
    for rid in set(body.role_ids):
        await session.execute(
            text("INSERT INTO app.user_role (user_id, role_id, granted_by) VALUES (:u, :r, :g)"),
            {"u": user_id, "r": rid, "g": user.id},
        )
    await AuditWriter.for_user(session, user, request).log(
        "identity.user_roles_set",
        "app_user",
        user_id,
        before={"role_ids": before},
        after={"role_ids": body.role_ids},
    )
    return await _load_user(session, user_id)


async def _guard_same_team(session: AsyncSession, user_id: int) -> None:
    """RLS 直查兜底：不可见（跨团队）与不存在同样报 404 语义（防探测，契约约定）。"""
    n = (
        await session.execute(
            text("SELECT count(*) FROM app.app_user WHERE id = :id"), {"id": user_id}
        )
    ).scalar_one()
    if n == 0:
        raise BusinessError("USER_NOT_FOUND", "用户不存在")


# ─────────────────────────── Roles / Permissions ───────────────────────────


@identity_router.get("/roles", response_model=list[RoleOut])
async def list_roles(
    _: CurrentUser = Depends(require_permission("identity.user_read")),
    session: AsyncSession = Depends(get_session),
) -> list[RoleOut]:
    rows = await session.execute(
        text(
            "SELECT r.id, r.team_id, r.name, r.description,"
            " COALESCE(array_agg(rp.permission_code) FILTER (WHERE rp.permission_code"
            " IS NOT NULL), '{}') AS permission_codes"
            " FROM app.role r LEFT JOIN app.role_permission rp ON rp.role_id = r.id"
            " GROUP BY r.id ORDER BY r.team_id NULLS FIRST, r.id"
        )
    )
    return [RoleOut(**r._asdict()) for r in rows]


@identity_router.post("/roles", status_code=201)
async def create_role(
    body: RoleWrite,
    request: Request,
    user: CurrentUser = Depends(require_permission("identity.role_write")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    if user.team_id is None:
        raise BusinessError("TEAM_REQUIRED", "超管请在目标团队上下文中建角色（暂不支持全局建）")
    row = (
        await session.execute(
            text(
                "INSERT INTO app.role (team_id, name, description, created_by)"
                " VALUES (:t, :n, :d, :c) RETURNING id"
            ),
            {"t": user.team_id, "n": body.name, "d": body.description, "c": user.id},
        )
    ).one()
    await AuditWriter.for_user(session, user, request).log(
        "identity.role_create", "role", row.id, after=body.model_dump()
    )
    return {"id": row.id}


@identity_router.patch("/roles/{role_id}")
async def patch_role(
    role_id: int,
    body: RoleWrite,
    request: Request,
    user: CurrentUser = Depends(require_permission("identity.role_write")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    row = (
        await session.execute(
            text(
                "UPDATE app.role SET name = :n, description = COALESCE(:d, description)"
                " WHERE id = :id AND team_id IS NOT NULL RETURNING id"
            ),
            {"id": role_id, "n": body.name, "d": body.description},
        )
    ).first()
    if row is None:
        raise BusinessError("ROLE_NOT_FOUND", "角色不存在或为全局模板（模板不可改）")
    await AuditWriter.for_user(session, user, request).log(
        "identity.role_update", "role", role_id, after=body.model_dump()
    )
    return {"status": "ok"}


@identity_router.put("/roles/{role_id}/permissions")
async def set_role_permissions(
    role_id: int,
    body: PermissionCodesIn,
    request: Request,
    user: CurrentUser = Depends(require_permission("identity.role_write")),
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    exists = (
        await session.execute(
            text("SELECT count(*) FROM app.role WHERE id = :id AND team_id IS NOT NULL"),
            {"id": role_id},
        )
    ).scalar_one()
    if exists == 0:
        raise BusinessError("ROLE_NOT_FOUND", "角色不存在或为全局模板（模板不可改）")
    valid = (
        await session.execute(
            text("SELECT count(*) FROM app.permission WHERE code = ANY(:codes)"),
            {"codes": body.permission_codes},
        )
    ).scalar_one()
    if valid != len(set(body.permission_codes)):
        raise BusinessError("PERMISSION_INVALID", "包含未知权限码")
    await session.execute(
        text("DELETE FROM app.role_permission WHERE role_id = :id"), {"id": role_id}
    )
    for code in set(body.permission_codes):
        await session.execute(
            text("INSERT INTO app.role_permission (role_id, permission_code) VALUES (:r, :c)"),
            {"r": role_id, "c": code},
        )
    await AuditWriter.for_user(session, user, request).log(
        "identity.role_permissions_set",
        "role",
        role_id,
        after={"permission_codes": body.permission_codes},
    )
    return {"status": "ok"}


@identity_router.get("/permissions", response_model=list[PermissionOut])
async def list_permissions(
    _: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[PermissionOut]:
    rows = await session.execute(
        text("SELECT code, module, name FROM app.permission ORDER BY module, code")
    )
    return [PermissionOut(**r._asdict()) for r in rows]


# ─────────────────────────── Audit / Dicts ───────────────────────────


@identity_router.get("/audit-logs", response_model=Page[AuditLogOut])
async def list_audit_logs(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    actor_id: int | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    action: str | None = None,
    _: CurrentUser = Depends(require_permission("identity.audit_read")),
    session: AsyncSession = Depends(get_session),
) -> Page[AuditLogOut]:
    where = ["true"]
    params: dict[str, Any] = {"l": size, "o": (page - 1) * size}
    for col, val in (
        ("actor_id", actor_id),
        ("object_type", object_type),
        ("object_id", object_id),
        ("action", action),
    ):
        if val is not None:
            where.append(f"{col} = :{col}")
            params[col] = val
    cond = " AND ".join(where)
    total = (
        await session.execute(
            text(f"SELECT count(*) FROM app.audit_log WHERE {cond}"),
            params,
        )
    ).scalar_one()
    rows = await session.execute(
        text(
            "SELECT id, occurred_at, actor_type, actor_id, action, object_type, object_id,"
            f" before, after, ip::text AS ip FROM app.audit_log WHERE {cond}"
            " ORDER BY occurred_at DESC LIMIT :l OFFSET :o"
        ),
        params,
    )
    return Page(items=[AuditLogOut(**r._asdict()) for r in rows], total=total, page=page, size=size)


@identity_router.get("/dicts/{dict_type}", response_model=list[DictItemOut])
async def list_dict(
    dict_type: str,
    session: AsyncSession = Depends(get_session),
) -> list[DictItemOut]:
    rows = await session.execute(
        text(
            "SELECT code, label, sort FROM app.sys_dict"
            " WHERE dict_type = :t AND enabled ORDER BY sort, code"
        ),
        {"t": dict_type},
    )
    return [DictItemOut(**r._asdict()) for r in rows]


__all__ = ["AuthError", "auth_router", "identity_router"]
