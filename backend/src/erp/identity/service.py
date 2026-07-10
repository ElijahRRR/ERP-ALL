"""认证服务（R1-04）。

- 用户查找只允许经 SECURITY DEFINER 通道（app.auth_user_by_*，见 0002 migration 头注）。
- login 自管事务：失败计数必须落库，不能随请求事务回滚——因此收
  session_factory 而非请求级 session。
- 登录锁定：连续 5 次失败锁 15 分钟（0005 auth_record_login，参数在 DB 函数默认值；
  调档走 system_config 时再把参数透传）。
"""

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.core.authn import AuthError
from erp.core.security import TokenError, create_token, decode_token, verify_password
from erp.core.settings import get_settings


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


def _mint(user_id: int, team_id: int | None, is_super: bool, token_version: int) -> TokenPair:
    common = {
        "subject": user_id,
        "audience": "erp",
        "token_version": token_version,
        "team_id": team_id,
        "is_super": is_super,
    }
    return TokenPair(
        access_token=create_token(kind="access", **common),  # type: ignore[arg-type]
        refresh_token=create_token(kind="refresh", **common),  # type: ignore[arg-type]
        expires_in=get_settings().jwt_access_ttl_seconds,
    )


async def login(
    sessions: async_sessionmaker[AsyncSession], username: str, password: str
) -> TokenPair:
    async with sessions() as s:
        row = (
            await s.execute(
                text(
                    "SELECT u.id, u.team_id, u.is_super, u.status, u.token_version,"
                    " u.password_hash, u.locked_until, now() AS db_now"
                    " FROM app.auth_user_by_username(:u) u"
                ),
                {"u": username},
            )
        ).first()
        if row is None:
            raise AuthError("用户名或密码错误")
        if row.status != "active":
            raise AuthError("账号已停用，请联系管理员")
        if row.locked_until is not None and row.locked_until > row.db_now:
            raise AuthError("账号已锁定，请稍后再试")

        ok = verify_password(row.password_hash, password)
        await s.execute(text("SELECT app.auth_record_login(:id, :ok)"), {"id": row.id, "ok": ok})
        await s.commit()  # 失败计数必须落库，独立于是否抛错

    if not ok:
        raise AuthError("用户名或密码错误")
    return _mint(row.id, row.team_id, row.is_super, row.token_version)


async def refresh(sessions: async_sessionmaker[AsyncSession], refresh_token: str) -> TokenPair:
    try:
        payload = decode_token(refresh_token, audience="erp", kind="refresh")
    except TokenError as exc:
        raise AuthError(str(exc)) from exc
    async with sessions() as s:
        row = (
            await s.execute(
                text(
                    "SELECT id, team_id, is_super, status, token_version"
                    " FROM app.auth_user_by_id(:id)"
                ),
                {"id": int(payload["sub"])},
            )
        ).first()
    if row is None or row.status != "active" or row.token_version != payload["tv"]:
        raise AuthError("凭证已失效，请重新登录")
    return _mint(row.id, row.team_id, row.is_super, row.token_version)
