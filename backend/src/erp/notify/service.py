"""站内通知服务（替代飞书通知的第一块砖，D-Q7）。

- 唯一发通知入口 notify()：任何域要提醒人，调这里，不自己插表。
- dedupe_key 抑制：同键 24 小时内只发一条（告警风暴闸）。
- 投递面 targets 缺省 = 全团队（target_kind=team）。
"""

from typing import Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger()

Severity = Literal["info", "warn", "critical"]
TargetKind = Literal["team", "role", "user"]


async def notify(
    session: AsyncSession,
    *,
    team_id: int | None,
    severity: Severity,
    category: str,
    title: str,
    body: str | None = None,
    object_type: str | None = None,
    object_id: str | None = None,
    dedupe_key: str | None = None,
    targets: list[tuple[TargetKind, int]] | None = None,
) -> int | None:
    """发通知；被 dedupe 抑制时返回 None，否则返回 notification id。"""
    if dedupe_key is not None:
        dup = (
            await session.execute(
                text(
                    "SELECT 1 FROM app.notification WHERE dedupe_key = :k"
                    " AND created_at > now() - interval '24 hours' LIMIT 1"
                ),
                {"k": dedupe_key},
            )
        ).first()
        if dup is not None:
            return None

    row = (
        await session.execute(
            text(
                "INSERT INTO app.notification"
                " (team_id, severity, category, title, body, object_type, object_id, dedupe_key)"
                " VALUES (:t, :sev, :cat, :ti, :b, :ot, :oi, :k)"
                " RETURNING id, created_at"
            ),
            {
                "t": team_id,
                "sev": severity,
                "cat": category,
                "ti": title,
                "b": body,
                "ot": object_type,
                "oi": object_id,
                "k": dedupe_key,
            },
        )
    ).one()

    if targets is None:
        # 全局公告（team_id None）无 target 行 = 所有团队可见（查询侧兜底）
        targets = [] if team_id is None else [("team", team_id)]
    for kind, target_id in targets:
        await session.execute(
            text(
                "INSERT INTO app.notification_target"
                " (notification_id, created_at, target_kind, target_id)"
                " VALUES (:n, :c, :k, :i) ON CONFLICT DO NOTHING"
            ),
            {"n": row.id, "c": row.created_at, "k": kind, "i": target_id},
        )
    log.info("notification.sent", category=category, severity=severity, team_id=team_id)
    return int(row.id)
