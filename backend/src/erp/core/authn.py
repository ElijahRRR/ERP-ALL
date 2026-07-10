"""请求级认证与授权：JWT 校验 → 用户装载 → GUC 注入 → 权限点检查。

顺序即安全模型（缺一不可）：
1. decode_token(aud=erp, kind=access)——门户 token 在此被拒；
2. app.auth_user_by_id（SECURITY DEFINER 通道）取用户，校验 status/token_version；
3. 在**本请求事务**上 SET LOCAL app.current_team / app.is_super —— 此后所有查询受 RLS；
4. 加载权限点集合；require_permission() 在路由上声明（与契约 x-permission 同码）。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.db import get_session
from erp.core.security import TokenError, decode_token


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


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> CurrentUser:
    if credentials is None:
        raise AuthError("缺少 Bearer token")
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

    # GUC 注入：SET LOCAL 只影响当前事务（= 当前请求），请求间零泄漏
    if row.is_super:
        await session.execute(text("SELECT set_config('app.is_super', 'on', true)"))
    if row.team_id is not None:
        await session.execute(
            text("SELECT set_config('app.current_team', :t, true)"), {"t": str(row.team_id)}
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
        team_id=row.team_id,
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

    return _check
