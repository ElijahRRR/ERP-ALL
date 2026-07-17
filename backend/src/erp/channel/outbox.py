"""channel 写路径 transactional outbox（RS-03b，评审 A7 处方）。

协议（.agent/evidence/RS-03b/archaeology.md §2）：
- enqueue：业务事务内落命令行（含幂等键+payload_hash）随业务一起 COMMIT——
  渠道已收/DB 失忆的崩溃窗口由此消除（命令即对账线索）。
  同 (team, action, idempotency_key)：同载荷→返既有命令（同结果）；异载荷→409。
- claim：短事务领取（fence+1 + lease）。只领 pending；同店更早未终局命令挡道
  （FIFO 车道，verify_pending 背压=fail-closed：上一发结果未知禁止继续发）。
- HTTP 段零事务（gateway.request_prepared）。
- complete：fence + status='inflight' 双重校验——迟到 worker（lease 过期后被
  sweep 归位）的回写整体拒绝。
- sweep：lease 过期的 inflight → verify_pending（**永不盲重试**，等 verify-back
  对账 adopt/lost），同时 fence+1 使迟到回写必拒；feed_submit 联动 feed 状态。
- payload 按构造无凭证（只存 store_id 引用，凭证由网关执行时解密）；
  enqueue 处递归键名扫描为纵深防御。
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.channel.gateway import gateway
from erp.channel.gateway.client import GatewayResponse
from erp.core.db import ctx_tx
from erp.core.errors import BusinessError

log = structlog.get_logger()

# applier 契约：tx2 内调用；必须先 complete(fence) 成功再落业务状态，
# fence 被拒时原样返回 {"command_status": "superseded"} 且不做任何写。
Applier = Callable[
    [AsyncSession, dict[str, Any], GatewayResponse | None], Awaitable[dict[str, Any]]
]

ACTIONS = ("feed_submit", "item_retire", "order_ack", "order_ship", "price_push")

# 敏感键黑名单（子串匹配，大小写不敏感）——payload 任何层级键名命中即拒
_FORBIDDEN_KEY_PARTS = ("authorization", "token", "secret", "password", "credential", "proxy")

_DEFAULTS = {"lease_seconds": 120}


def payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def scan_forbidden_keys(node: Any, path: str = "$") -> str | None:
    """→ 首个命中敏感键的 JSON 路径；None=干净。"""
    if isinstance(node, dict):
        for key, val in node.items():
            lowered = str(key).lower()
            if any(part in lowered for part in _FORBIDDEN_KEY_PARTS):
                return f"{path}.{key}"
            found = scan_forbidden_keys(val, f"{path}.{key}")
            if found:
                return found
    elif isinstance(node, list):
        for i, val in enumerate(node):
            found = scan_forbidden_keys(val, f"{path}[{i}]")
            if found:
                return found
    return None


async def outbox_config(session: AsyncSession) -> dict[str, Any]:
    row = (
        await session.execute(
            text("SELECT value FROM app.system_config WHERE key = 'channel.outbox'")
        )
    ).first()
    cfg = dict(_DEFAULTS)
    if row and isinstance(row[0], dict):
        cfg.update(row[0])
    return cfg


async def enqueue(
    session: AsyncSession,
    *,
    team_id: int,
    store_id: int,
    action: str,
    payload: dict[str, Any],
    idempotency_key: str,
    object_type: str | None = None,
    object_id: int | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    """业务事务内落命令。→ {command_id, existing, status}。

    同键同载荷：返既有命令（调用方复用其结果=同结果）；同键异载荷：409。
    """
    if action not in ACTIONS:
        raise BusinessError("OUTBOX_ACTION_UNKNOWN", f"未知命令类型：{action}")
    offending = scan_forbidden_keys(payload)
    if offending:
        raise BusinessError(
            "OUTBOX_PAYLOAD_FORBIDDEN",
            f"outbox payload 含敏感键（凭证/PII 脱敏铁律）：{offending}",
        )
    h = payload_hash(payload)
    row = (
        await session.execute(
            text(
                "INSERT INTO app.channel_command"
                " (team_id, store_id, action, object_type, object_id, idempotency_key,"
                "  payload, payload_hash, created_by)"
                " VALUES (:t, :s, :a, :ot, :oid, :k, cast(:p AS jsonb), :h, :u)"
                " ON CONFLICT (team_id, action, idempotency_key) DO NOTHING"
                " RETURNING id, status"
            ),
            {
                "t": team_id,
                "s": store_id,
                "a": action,
                "ot": object_type,
                "oid": object_id,
                "k": idempotency_key,
                "p": json.dumps(payload, ensure_ascii=False),
                "h": h,
                "u": created_by,
            },
        )
    ).first()
    if row is not None:
        return {"command_id": row.id, "existing": False, "status": row.status}
    existing = (
        await session.execute(
            text(
                "SELECT id, payload_hash, status FROM app.channel_command"
                " WHERE team_id = :t AND action = :a AND idempotency_key = :k"
            ),
            {"t": team_id, "a": action, "k": idempotency_key},
        )
    ).one()
    if existing.payload_hash != h:
        raise BusinessError(
            "IDEMPOTENCY_CONFLICT",
            f"幂等键已存在且载荷不同：{action}/{idempotency_key}",
            http_status=409,
        )
    return {"command_id": existing.id, "existing": True, "status": existing.status}


async def claim(
    session: AsyncSession, command_id: int, *, lease_seconds: int
) -> dict[str, Any] | None:
    """短事务领取：fence+1 + lease。只领 pending；同店同车道更早未终局命令挡道（FIFO）。

    车道 = (store_id, 是否 price_push)：price_push 是高量低配额 action
    （reprice 单次可入队数百条，PUT /v3/price 仅 100/hour），与订单回传/feed 提交
    共享一条车道会把 order_ack/order_ship 背压数小时（R2-06 增量3 评审发现 10）——
    故 price_push 独占车道，其余 action 维持 RS-03b 原有同店 FIFO 语义。

    → 命令行 dict（含本次 fence）；None=不可领（车道被挡/已被领/已终局/封店冻结）。

    封店门控与 pick_next 同口径（07b §02:185）：非 active 店只放行订单类命令，
    listing 维护类不可领；阻塞子查询同样忽略被冻结命令。
    """
    row = (
        (
            await session.execute(
                text(
                    "UPDATE app.channel_command c SET status = 'inflight',"
                    " fence = c.fence + 1, attempts = c.attempts + 1, claimed_at = now(),"
                    " lease_expires_at = now() + make_interval(secs => :lease)"
                    " WHERE c.id = :id AND c.status = 'pending'"
                    "   AND (c.action IN ('order_ack','order_ship') OR EXISTS"
                    "     (SELECT 1 FROM app.store s"
                    "        WHERE s.id = c.store_id AND s.status = 'active'))"
                    "   AND NOT EXISTS (SELECT 1 FROM app.channel_command b"
                    "     WHERE b.store_id = c.store_id AND b.id < c.id"
                    "       AND (b.action = 'price_push') = (c.action = 'price_push')"
                    "       AND (b.action IN ('order_ack','order_ship') OR EXISTS"
                    "         (SELECT 1 FROM app.store s"
                    "            WHERE s.id = b.store_id AND s.status = 'active'))"
                    "       AND b.status IN ('pending','inflight','verify_pending'))"
                    " RETURNING c.id, c.team_id, c.store_id, c.action, c.object_type,"
                    "   c.object_id, c.payload, c.fence, c.created_by"
                ),
                {"id": command_id, "lease": lease_seconds},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None


async def complete(
    session: AsyncSession,
    *,
    command_id: int,
    fence: int,
    status: str,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> bool:
    """fence 校验回写。False=迟到 worker（fence 或 status 不符），调用方必须丢弃结果。"""
    assert status in ("succeeded", "failed", "verify_pending")
    row = (
        await session.execute(
            text(
                "UPDATE app.channel_command SET status = :st, result = cast(:r AS jsonb),"
                " error_code = :e, lease_expires_at = NULL,"
                " completed_at = CASE WHEN :st IN ('succeeded','failed') THEN now() END"
                " WHERE id = :id AND fence = :f AND status = 'inflight'"
                " RETURNING id"
            ),
            {
                "st": status,
                "r": json.dumps(result, ensure_ascii=False) if result is not None else None,
                "e": error_code,
                "id": command_id,
                "f": fence,
            },
        )
    ).first()
    if row is None:
        log.warning("outbox.stale_worker_rejected", command_id=command_id, fence=fence)
        return False
    return True


async def release_claim(session: AsyncSession, *, command_id: int, fence: int) -> bool:
    """发包前被闸拒（限流 GATEWAY_RATE_EXCEEDED，零字节出门）：inflight → pending 归还。

    与「永不盲重试」不冲突——请求从未出门，无未知结果；命令留 pending 由
    beat channel_outbox_drain 在配额恢复后继续推。fence 校验同 complete：
    迟到 worker 的归还整体拒绝。仅限「确定未发包」场景调用。
    """
    row = (
        await session.execute(
            text(
                "UPDATE app.channel_command SET status = 'pending', lease_expires_at = NULL"
                " WHERE id = :id AND fence = :f AND status = 'inflight' RETURNING id"
            ),
            {"id": command_id, "f": fence},
        )
    ).first()
    return row is not None


async def resolve_verify(
    session: AsyncSession,
    *,
    command_id: int,
    status: str,
    result: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> bool:
    """verify-back 对账归位（权威通道，不需 fence）：verify_pending → succeeded/failed。"""
    assert status in ("succeeded", "failed")
    row = (
        await session.execute(
            text(
                "UPDATE app.channel_command SET status = :st, result = cast(:r AS jsonb),"
                " error_code = :e, lease_expires_at = NULL, completed_at = now()"
                " WHERE id = :id AND status = 'verify_pending' RETURNING id"
            ),
            {
                "st": status,
                "r": json.dumps(result, ensure_ascii=False) if result is not None else None,
                "e": error_code,
                "id": command_id,
            },
        )
    ).first()
    return row is not None


async def sweep_expired(session: AsyncSession) -> list[dict[str, Any]]:
    """懒清扫：lease 过期的 inflight（HTTP 后崩溃/悬挂）→ verify_pending。

    fence+1：迟到 worker 的 complete 必拒。feed_submit 联动 feed → verify_pending
    （对账入口在 feed 侧：POST /feeds/{id}/verify-back）。**绝不重发**（总账铁律）。
    """
    rows = (
        (
            await session.execute(
                text(
                    "UPDATE app.channel_command SET status = 'verify_pending',"
                    " fence = fence + 1, lease_expires_at = NULL"
                    " WHERE status = 'inflight' AND lease_expires_at < now()"
                    " RETURNING id, action, object_type, object_id"
                )
            )
        )
        .mappings()
        .all()
    )
    swept = [dict(r) for r in rows]
    for cmd in swept:
        if cmd["action"] == "feed_submit" and cmd["object_id"] is not None:
            await session.execute(
                text(
                    "UPDATE app.feed SET status = 'verify_pending',"
                    " submitted_at = coalesce(submitted_at, now())"
                    " WHERE id = :f AND status IN ('building','submitting')"
                ),
                {"f": cmd["object_id"]},
            )
        log.warning("outbox.swept_to_verify_pending", **cmd)
    return swept


async def pick_next(
    session: AsyncSession, *, exclude_store_ids: list[int] | None = None
) -> dict[str, Any] | None:
    """drain 工具用：下一条可领命令（领取本身由 claim 原子判定，选取仅作参考）。

    exclude_store_ids：本轮已确认闸拒/车道受阻的店铺——跳过其命令继续选其他店，
    否则单店限流会把全局最低 id 恒定选中、饿死其余店铺（R2-06 增量3 评审发现 3）。
    车道口径与 claim 一致（price_push 独占车道）。→ {id, store_id} | None。

    封店门控（07b §02:185）：店铺非 active 时 listing 维护类命令（feed_submit /
    item_retire / price_push）冻结不选，order_ack/order_ship 放行（已付订单履约
    不因封店中断）；恢复 active 后按原序自动续。阻塞子查询同口径——被冻结的
    listing 命令不得反向挡住同店订单命令的车道。
    """
    row = (
        await session.execute(
            text(
                "SELECT c.id, c.store_id FROM app.channel_command c"
                " WHERE c.status = 'pending'"
                "   AND NOT (c.store_id = ANY(:excl))"
                "   AND (c.action IN ('order_ack','order_ship') OR EXISTS"
                "     (SELECT 1 FROM app.store s WHERE s.id = c.store_id AND s.status = 'active'))"
                "   AND NOT EXISTS (SELECT 1 FROM app.channel_command b"
                "     WHERE b.store_id = c.store_id AND b.id < c.id"
                "       AND (b.action = 'price_push') = (c.action = 'price_push')"
                "       AND (b.action IN ('order_ack','order_ship') OR EXISTS"
                "         (SELECT 1 FROM app.store s"
                "            WHERE s.id = b.store_id AND s.status = 'active'))"
                "       AND b.status IN ('pending','inflight','verify_pending'))"
                " ORDER BY c.id LIMIT 1"
            ),
            {"excl": list(exclude_store_ids or [])},
        )
    ).first()
    return {"id": row.id, "store_id": row.store_id} if row else None


async def execute_command(
    sessions: async_sessionmaker[AsyncSession],
    command_id: int,
    *,
    team_id: int | None,
    is_super: bool = False,
    applier: Applier,
) -> dict[str, Any]:
    """三段式执行器（RS-03a 模式）：tx_claim → HTTP（零事务/零行锁）→ tx2。

    - tx_claim：懒清扫过期 inflight → 领取（fence+lease）→ gateway.prepare
      （模式闸在事务内，拒绝则领取一并回滚、命令留 pending 可重执行）。
    - HTTP：request_prepared，无 session。传输层无响应=结果未知（resp=None 同义）。
    - tx2：applier 先 complete(fence) 再落业务状态；fence 被拒=迟到 worker，丢弃结果。
    - 领不到（车道被挡/已终局/被并发领走）→ 返当前命令状态，不发包。
    """
    cmd: dict[str, Any] | None = None
    prepared: tuple[Any, Any] | None = None
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as s:
        await sweep_expired(s)
        cfg = await outbox_config(s)
        cmd = await claim(s, command_id, lease_seconds=int(cfg["lease_seconds"]))
        if cmd is not None:
            prepared = await gateway.prepare(s, cmd["store_id"])
    if cmd is None or prepared is None:
        async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as s:
            state = await command_state(s, command_id)
        status = (state or {}).get("status", "missing")
        log.info("outbox.claim_unavailable", command_id=command_id, status=status)
        return {
            "command_status": status,
            "error_code": (state or {}).get("error_code"),
            "result": (state or {}).get("result"),
        }

    payload = cmd["payload"]
    resp: GatewayResponse | None
    try:
        resp = await gateway.request_prepared(
            prepared[0],
            prepared[1],
            payload["method"],
            payload["path"],
            endpoint_key=payload.get("endpoint_key"),
            params=payload.get("params"),
            json_body=payload.get("json_body"),
            gate_max_wait=payload.get("gate_max_wait"),
        )
    except BusinessError as exc:
        if exc.code == "GATEWAY_RATE_EXCEEDED":
            # 限流闸在发包前拒绝（零字节出门，非未知结果）→ 归还 pending，
            # 由 beat channel_outbox_drain 在配额恢复后继续推（R2-06 reprice 语义）
            async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as s:
                released = await release_claim(s, command_id=cmd["id"], fence=cmd["fence"])
            log.info("outbox.rate_gated_released", command_id=command_id, released=released)
            return {"command_status": "pending", "error_code": "GATEWAY_RATE_EXCEEDED"}
        log.warning("outbox.request_unexpected_error", command_id=command_id, error=str(exc))
        resp = None
    except Exception as exc:
        # 网关已吞传输错误（status=None）；走到这里=意外异常，同样按"结果未知"处理
        log.warning("outbox.request_unexpected_error", command_id=command_id, error=str(exc))
        resp = None

    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as s:
        return await applier(s, cmd, resp)


async def command_state(session: AsyncSession, command_id: int) -> dict[str, Any] | None:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, action, status, result, error_code, fence, attempts"
                    " FROM app.channel_command WHERE id = :id"
                ),
                {"id": command_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row else None
