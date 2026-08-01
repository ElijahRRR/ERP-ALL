"""请求级认证与授权：JWT 校验 → 用户装载 → GUC 注入 → 权限点检查。

顺序即安全模型（缺一不可）：
1. decode_token(aud=erp, kind=access)——门户 token 在此被拒；
2. app.auth_user_by_id（SECURITY DEFINER 通道）取用户，校验 status/token_version；
3. 在**本请求事务**上 SET LOCAL app.current_team / app.is_super —— 此后所有查询受 RLS；
4. 加载权限点集合；require_permission() 在路由上声明（与契约 x-permission 同码）。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.db import get_session
from erp.core.security import TokenError, decode_token
from erp.core.settings import get_settings


class AuthError(Exception):
    """认证失败——统一 401。"""

    def __init__(self, message: str = "未认证或凭证无效"):
        self.message = message
        super().__init__(message)


class PermissionDenied(Exception):
    """无权限点——统一 403。"""

    def __init__(self, permission: str):
        self.permission = permission
        super().__init__(permission)


@dataclass(frozen=True)
class CurrentUser:
    id: int
    team_id: int | None
    is_super: bool
    display_name: str
    permissions: frozenset[str]

    def has(self, permission: str) -> bool:
        return self.is_super or permission in self.permissions


_bearer = HTTPBearer(auto_error=False)


async def _load_identity_row(
    session: AsyncSession, credentials: HTTPAuthorizationCredentials | None
) -> Any:
    """把请求凭证解析成用户行（两条路，出口同形）。

    无凭证 + D-Q73 17a 单人模式：注入固定超管身份（免登录）。解析走与登录同一条
    SECURITY DEFINER 通道（auth_user_by_username），此后 GUC 注入/审计 actor 归属
    与正常登录完全同路——audit_log 三流可辨的「人工流」记的就是这个真实用户行。
    fail-closed：开关关着、或配置指向停用/非超管用户，一律 401，不把降级身份
    静默当超管用（验收⑤：关掉开关登录流程原样回来）。
    """
    if credentials is None:
        settings = get_settings()
        if not settings.single_user_mode:
            raise AuthError("缺少 Bearer token")
        row = (
            await session.execute(
                text(
                    "SELECT id, team_id, is_super, display_name, status, token_version"
                    " FROM app.auth_user_by_username(:u)"
                ),
                {"u": settings.single_user_admin.lower()},
            )
        ).first()
        if row is None or row.status != "active" or not row.is_super:
            raise AuthError("SINGLE_USER_MODE 需要指向一个在册且激活的超管账号")
        return row

    try:
        payload = decode_token(credentials.credentials, audience="erp", kind="access")
    except TokenError as exc:
        raise AuthError(str(exc)) from exc

    row = (
        await session.execute(
            text(
                "SELECT id, team_id, is_super, display_name, status, token_version"
                " FROM app.auth_user_by_id(:uid)"
            ),
            {"uid": int(payload["sub"])},
        )
    ).first()
    if row is None or row.status != "active" or row.token_version != payload["tv"]:
        raise AuthError("用户不存在、已停用或凭证已吊销")
    return row


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    row = await _load_identity_row(session, credentials)

    # GUC 注入：SET LOCAL 只影响当前事务（= 当前请求），请求间零泄漏
    team_id: int | None = row.team_id
    if row.is_super:
        await session.execute(text("SELECT set_config('app.is_super', 'on', true)"))
        # 超管代表团队操作（试点期核心用法）：X-Act-Team 头指定当前作用团队。
        # 校验必须在 is_super GUC 之后（team 表受 RLS，GUC 未注入前超管也查不到行）。
        act_team = request.headers.get("X-Act-Team")
        if act_team:
            try:
                act_id = int(act_team)
            except ValueError as exc:
                raise AuthError("X-Act-Team 必须是团队 ID") from exc
            exists = (
                await session.execute(
                    text("SELECT 1 FROM app.team WHERE id = :t AND status = 'active'"),
                    {"t": act_id},
                )
            ).scalar_one_or_none()
            if exists is None:
                raise AuthError("X-Act-Team 指定的团队不存在或已停用")
            team_id = act_id
    if team_id is not None:
        await session.execute(
            text("SELECT set_config('app.current_team', :t, true)"), {"t": str(team_id)}
        )

    if row.is_super:
        permissions: frozenset[str] = frozenset()
    else:
        perm_rows = await session.execute(
            text(
                "SELECT DISTINCT rp.permission_code"
                " FROM app.user_role ur"
                " JOIN app.role_permission rp ON rp.role_id = ur.role_id"
                " WHERE ur.user_id = :uid"
            ),
            {"uid": row.id},
        )
        permissions = frozenset(r[0] for r in perm_rows)

    user = CurrentUser(
        id=row.id,
        team_id=team_id,
        is_super=row.is_super,
        display_name=row.display_name,
        permissions=permissions,
    )
    request.state.current_user = user
    return user


def require_permission(permission: str) -> Callable[..., Awaitable[CurrentUser]]:
    """路由级权限点声明；权限码与 002 契约 x-permission 一字不差。"""

    async def _check(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        if not user.has(permission):
            raise PermissionDenied(permission)
        return user

    # 把权限码挂在依赖函数上，供 RS-11 的四向一致性校验内省
    # （tests/test_contract_permission_consistency.py）。上面那句 docstring 的
    # 「一字不差」此前只是口头约定、从无强制手段——挂上这个属性才使它可机器校验。
    # 不用 `__closure__` 反查：那依赖闭包变量顺序，改个形参就静默失效。
    _check.erp_permission = permission  # type: ignore[attr-defined]
    return _check
