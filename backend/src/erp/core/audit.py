"""审计唯一写出口（D-Q16）。

规则：所有写操作的服务层代码必须经 AuditWriter 记账；
禁止业务代码直接 INSERT app.audit_log（CR 检查点）。
audit_log 表本身 append-only（无 UPDATE/DELETE 授权 + 无对应 RLS policy）。
"""

import ipaddress
import json
from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.authn import CurrentUser


class AuditWriter:
    """绑定到（请求会话 + 行为主体）的审计记账器。

    与业务写操作同事务：业务回滚则审计随之回滚，不产生"幽灵审计"。
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        actor_type: str = "system",
        actor_id: int | None = None,
        team_id: int | None = None,
        request: Request | None = None,
    ) -> None:
        self._session = session
        self._actor_type = actor_type
        self._actor_id = actor_id
        self._team_id = team_id
        self._ip: str | None = None
        self._user_agent: str | None = None
        self._request_id: str | None = None
        if request is not None:
            raw_ip = request.client.host if request.client else None
            self._ip = raw_ip if raw_ip and _is_ip(raw_ip) else None
            self._user_agent = request.headers.get("user-agent")
            self._request_id = getattr(request.state, "request_id", None)

    @classmethod
    def for_user(
        cls, session: AsyncSession, user: CurrentUser, request: Request | None = None
    ) -> "AuditWriter":
        return cls(
            session,
            actor_type="user",
            actor_id=user.id,
            team_id=user.team_id,
            request=request,
        )

    async def log(
        self,
        action: str,
        object_type: str,
        object_id: object,
        *,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
    ) -> None:
        await self._session.execute(
            text(
                "INSERT INTO app.audit_log"
                " (actor_type, actor_id, team_id, action, object_type, object_id,"
                "  before, after, ip, user_agent, request_id)"
                " VALUES (:at, :ai, :tm, :ac, :ot, :oi,"
                "  cast(:bf AS jsonb), cast(:af AS jsonb), cast(:ip AS inet), :ua, :rid)"
            ),
            {
                "at": self._actor_type,
                "ai": self._actor_id,
                "tm": self._team_id,
                "ac": action,
                "ot": object_type,
                "oi": str(object_id),
                "bf": json.dumps(before, ensure_ascii=False) if before is not None else None,
                "af": json.dumps(after, ensure_ascii=False) if after is not None else None,
                "ip": self._ip,
                "ua": self._user_agent,
                "rid": self._request_id,
            },
        )


def _is_ip(value: str) -> bool:
    """非法来源串（测试桩/脏代理头）不入 inet 列，置 NULL。"""
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True
