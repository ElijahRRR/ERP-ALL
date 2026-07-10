"""密码散列与 JWT（双 audience：erp=内部 / portal=采购门户，互不接受）。"""

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError

from erp.core.settings import get_settings

Audience = Literal["erp", "portal"]
TokenKind = Literal["access", "refresh"]

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerificationError:
        return False


class TokenError(Exception):
    """token 无效/过期/受众不符——统一 401。"""


def create_token(
    *,
    subject: int,
    audience: Audience,
    kind: TokenKind,
    token_version: int,
    team_id: int | None = None,
    is_super: bool = False,
    ttl_seconds: int | None = None,
) -> str:
    settings = get_settings()
    if ttl_seconds is None:
        if kind == "access":
            ttl_seconds = settings.jwt_access_ttl_seconds
        else:
            ttl_seconds = settings.jwt_refresh_ttl_seconds
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": str(subject),
        "aud": audience,
        "kind": kind,
        "tv": token_version,
        "team": team_id,
        "sup": is_super,
        "iat": now,
        "exp": now + timedelta(seconds=ttl_seconds),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def decode_token(token: str, *, audience: Audience, kind: TokenKind) -> dict[str, Any]:
    settings = get_settings()
    try:
        payload: dict[str, Any] = jwt.decode(
            token, settings.jwt_secret, algorithms=["HS256"], audience=audience
        )
    except jwt.InvalidTokenError as exc:  # 过期/签名/受众 全部归一
        raise TokenError(str(exc)) from exc
    if payload.get("kind") != kind:
        raise TokenError(f"token kind 不符：期望 {kind}")
    return payload
