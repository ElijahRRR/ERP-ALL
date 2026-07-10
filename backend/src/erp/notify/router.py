"""通知中心 API：列表 / 未读数（铃铛轮询）/ 已读。任何登录用户可用（无权限点）。"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.authn import CurrentUser, get_current_user
from erp.core.db import get_session
from erp.identity.schemas import Page

notify_router = APIRouter(tags=["Notify"])

# 可见性 = 团队投递 OR 直达本人 OR 全局公告（RLS 已按 team 圈定 notification 本体）
_VISIBLE = (
    "(n.team_id IS NULL OR EXISTS ("
    " SELECT 1 FROM app.notification_target t WHERE t.notification_id = n.id"
    " AND ((t.target_kind = 'team' AND t.target_id = :team)"
    "      OR (t.target_kind = 'user' AND t.target_id = :uid))))"
)


class NotificationOut(BaseModel):
    id: int
    severity: str
    category: str
    title: str
    body: str | None
    object_type: str | None
    object_id: str | None
    created_at: datetime
    read_at: datetime | None


@notify_router.get("/notifications", response_model=Page[NotificationOut])
async def list_notifications(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Page[NotificationOut]:
    unread_cond = " AND r.read_at IS NULL" if unread_only else ""
    params = {"team": user.team_id or -1, "uid": user.id}
    total = (
        await session.execute(
            text(
                "SELECT count(*) FROM app.notification n"
                " LEFT JOIN app.notification_receipt r"
                "   ON r.notification_id = n.id AND r.user_id = :uid"
                f" WHERE {_VISIBLE}{unread_cond}"
            ),
            params,
        )
    ).scalar_one()
    rows = await session.execute(
        text(
            "SELECT n.id, n.severity, n.category, n.title, n.body,"
            " n.object_type, n.object_id, n.created_at, r.read_at"
            " FROM app.notification n"
            " LEFT JOIN app.notification_receipt r"
            "   ON r.notification_id = n.id AND r.user_id = :uid"
            f" WHERE {_VISIBLE}{unread_cond}"
            " ORDER BY n.created_at DESC LIMIT :l OFFSET :o"
        ),
        {**params, "l": size, "o": (page - 1) * size},
    )
    return Page(
        items=[NotificationOut(**r._asdict()) for r in rows], total=total, page=page, size=size
    )


@notify_router.get("/notifications/unread-count")
async def unread_count(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> dict[str, int]:
    n = (
        await session.execute(
            text(
                "SELECT count(*) FROM app.notification n"
                " LEFT JOIN app.notification_receipt r"
                "   ON r.notification_id = n.id AND r.user_id = :uid"
                f" WHERE {_VISIBLE} AND r.read_at IS NULL"
            ),
            {"team": user.team_id or -1, "uid": user.id},
        )
    ).scalar_one()
    return {"count": n}


@notify_router.post("/notifications/{notification_id}/read", status_code=204)
async def mark_read(
    notification_id: int,
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await session.execute(
        text(
            "INSERT INTO app.notification_receipt (notification_id, user_id)"
            " VALUES (:n, :u) ON CONFLICT DO NOTHING"
        ),
        {"n": notification_id, "u": user.id},
    )


@notify_router.post("/notifications/read-all", status_code=204)
async def mark_all_read(
    user: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> None:
    await session.execute(
        text(
            "INSERT INTO app.notification_receipt (notification_id, user_id)"
            " SELECT n.id, :uid FROM app.notification n"
            " LEFT JOIN app.notification_receipt r"
            "   ON r.notification_id = n.id AND r.user_id = :uid"
            f" WHERE {_VISIBLE} AND r.read_at IS NULL"
            " ON CONFLICT DO NOTHING"
        ),
        {"team": user.team_id or -1, "uid": user.id},
    )
