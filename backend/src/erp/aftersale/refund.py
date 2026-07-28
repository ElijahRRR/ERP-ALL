"""退款/取消申请服务（R2-07 增量2；D-Q29 三档，001 §07 图纸语义）。

本单范围（007 口径）：record / approval 两档**本地闭环**——
- record 档（automation_policy manual，缺省档）：创建即终态 recorded，只记账不执行；
- approval 档（semi）：pending_approval → approve/reject（refund.approve 权限）；
  approved 为"待执行"驻留态——渠道执行与 auto 档随 R2-09 flow=refund 接线
  （届时 approved/auto 进 executing，走 outbox return_refund + verify-back）；
- auto 档（auto）：接线前 fail-closed 拒绝创建（REFUND_AUTO_NOT_WIRED），
  不做"静默降档"——档位语义不许偏离（铁律1）。

档位快照：创建时读 automation_policy(flow_code = kind)，manual→record / semi→approval /
auto→auto；无策略行 = manual（fail-closed 最保守档）。
"""

import json
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.automation import AutomationFlow, resolve_mode
from erp.core.errors import BusinessError

log = structlog.get_logger()

# automation_policy 档位词表 → refund_request 图纸词表
_MODE_MAP = {"manual": "record", "semi": "approval", "auto": "auto"}
# 档位 → 初始状态（auto 在 R2-09 接线前到不了这里）
_INITIAL_STATUS = {"record": "recorded", "approval": "pending_approval"}


async def _resolve_mode(session: AsyncSession, *, team_id: int, kind: str) -> str:
    """→ refund_request 图纸词表的档位（record/approval/auto）。

    R2-09 增量1：档位读取改走三档内核（`core.automation.resolve_mode`），
    本函数只保留「automation_policy 词表 → refund_request 词表」的映射。
    **行为逐条对齐**：内核对无行 / `enabled=false` / 非三档取值一律回 `manual`，
    与原来 `AND enabled` + `scalar_one_or_none()` + `or "manual"` 的结果相同。
    """
    mode = await resolve_mode(session, team_id=team_id, flow=AutomationFlow(kind))
    return _MODE_MAP.get(mode.value, "record")


async def create_request(
    session: AsyncSession,
    *,
    team_id: int,
    order_id: int,
    kind: str,
    amount: float,
    reason_code: str,
    lines: list[dict[str, Any]] | None = None,
    reason_text: str | None = None,
    requested_by_kind: str = "user",
    requested_by: int,
) -> dict[str, Any]:
    """创建申请（档位快照随建）。返回落库行摘要。"""
    order = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, store_id, order_date, currency"
                    " FROM app.channel_order WHERE id = :o"
                ),
                {"o": order_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if order is None or order["team_id"] != team_id:
        raise BusinessError("ORDER_NOT_FOUND", "订单不存在")
    if amount <= 0:
        raise BusinessError("REFUND_AMOUNT_INVALID", "金额必须为正数")
    known_reason = (
        await session.execute(
            text(
                "SELECT 1 FROM app.sys_dict"
                " WHERE dict_type = 'refund_reason' AND code = :c AND enabled"
            ),
            {"c": reason_code},
        )
    ).first()
    if known_reason is None:
        raise BusinessError("REASON_CODE_UNKNOWN", f"原因码不在字典中：{reason_code}")

    mode = await _resolve_mode(session, team_id=team_id, kind=kind)
    if mode == "auto":
        raise BusinessError(
            "REFUND_AUTO_NOT_WIRED",
            "auto 档渠道执行随 R2-09 接线后开放；请先将该流程档位调至 人工/半自动",
        )

    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO app.refund_request"
                    " (team_id, store_id, order_id, order_date, kind, lines, amount,"
                    "  currency, reason_code, reason_text, mode_applied, status,"
                    "  requested_by_kind, requested_by)"
                    " VALUES (:t, :s, :o, :d, :k, cast(:ln AS jsonb), :a, :cur, :rc, :rt,"
                    "         :m, :st, :bk, :by)"
                    " RETURNING id, status, mode_applied"
                ),
                {
                    "t": team_id,
                    "s": order["store_id"],
                    "o": order_id,
                    "d": order["order_date"],
                    "k": kind,
                    "ln": json.dumps(lines or [], ensure_ascii=False),
                    "a": amount,
                    "cur": order["currency"],
                    "rc": reason_code,
                    "rt": reason_text,
                    "m": mode,
                    "st": _INITIAL_STATUS[mode],
                    "bk": requested_by_kind,
                    "by": requested_by,
                },
            )
        )
        .mappings()
        .one()
    )
    log.info(
        "refund_request.created",
        request_id=row["id"], order_id=order_id, kind=kind, mode=mode,
    )  # fmt: skip
    return {"id": int(row["id"]), "status": row["status"], "mode_applied": row["mode_applied"]}


async def decide_request(
    session: AsyncSession,
    *,
    request_id: int,
    team_id: int,
    decision: str,
    decided_by: int,
) -> dict[str, Any]:
    """审批（approval 档专用）：pending_approval → approved / rejected。

    approved 停在驻留态等 R2-09 执行接线；本函数不触达渠道。
    """
    if decision not in ("approved", "rejected"):  # 路由层已枚举，双保险
        raise ValueError(f"invalid decision: {decision}")
    row = (
        (
            await session.execute(
                text("SELECT id, team_id, status FROM app.refund_request WHERE id = :r FOR UPDATE"),
                {"r": request_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["team_id"] != team_id:
        raise BusinessError("REFUND_REQUEST_NOT_FOUND", "申请不存在")
    if row["status"] != "pending_approval":
        raise BusinessError(
            "REFUND_REQUEST_NOT_PENDING", f"申请不在待审批状态（当前 {row['status']}）"
        )
    await session.execute(
        text(
            "UPDATE app.refund_request SET status = :st, approved_by = :u, decided_at = now()"
            " WHERE id = :r"
        ),
        {"st": decision, "u": decided_by, "r": request_id},
    )
    return {"id": request_id, "status": decision}
