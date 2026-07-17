"""变体归组服务（R2-11 增量1；D-Q2/D-Q63，001 §03 图纸语义）。

归组协议（.agent/evidence/R2-11/archaeology.md §2/§3）：
- 素材 = product.attrs（采集入库）：parent_asin（parser 无父体时填自身，归组必须排除
  parent==source_ref 的自指）、variant_attributes（twister "k=v; k=v" 串）——**只信
  twister 来源**（variant_attributes 为空不归组：旧全页正则 variation_asins 是
  「巨型伪组」根因，BR-ASC-003 考古坑账）；
- 组身份 = (team_id, source_parent_ref)；一品最多属一组（variant_member.product_id UNIQUE）；
- 双向关联同事务维护：variant_member 行 + product.variant_group_id；
- broken 判定 v1（D-Q63 配套口径，判定随每次归组/成员变更重跑，冲突消除自动回 active）：
  成员数 < 2 / 组内维度键集不一致 / 成员数 > variant.max_group_size
  （team_config > system_config > 默认 10——旧仓巨型组阈值经验值）；
- anchor_store_id 本服务不写：首次上架时由 listing 管道锁定（增量2，BR-LST-013）；
- match 模式豁免归组属上架期语义（增量2 spec 段处理），归组本身渠道无关。
"""

import json
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.core.db import system_tx
from erp.core.errors import BusinessError
from erp.notify.service import notify

log = structlog.get_logger()

MAX_GROUP_SIZE_KEY = "variant.max_group_size"
DEFAULT_MAX_GROUP_SIZE = 10


def parse_variant_attrs(raw: str | None) -> dict[str, str]:
    """twister 串 "color_name=Red; size_name=L" → dict（键保持 Amazon snake_case 原样）。"""
    out: dict[str, str] = {}
    for part in (raw or "").split(";"):
        k, sep, v = part.strip().partition("=")
        if sep and k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out


async def max_group_size(session: AsyncSession, team_id: int) -> int:
    """巨型组护栏（D-Q63 配套）：team_config > system_config > 默认 10。"""
    raw = (
        await session.execute(
            text(
                "SELECT value #>> '{}' AS v FROM ("
                "  SELECT value, 0 AS pri FROM app.team_config"
                "   WHERE team_id = :t AND key = :k"
                "  UNION ALL"
                "  SELECT value, 1 AS pri FROM app.system_config WHERE key = :k"
                ") c ORDER BY pri LIMIT 1"
            ),
            {"t": team_id, "k": MAX_GROUP_SIZE_KEY},
        )
    ).scalar_one_or_none()
    return int(raw) if raw is not None else DEFAULT_MAX_GROUP_SIZE


async def reassess_group(session: AsyncSession, group_id: int, *, limit: int) -> str:
    """broken 判定 v1：成员<2 / 维度键集不一致 / 超上限。返回归位后的状态。"""
    rows = (
        await session.execute(
            text("SELECT variant_attrs FROM app.variant_member WHERE group_id = :g"),
            {"g": group_id},
        )
    ).all()
    key_sets = {frozenset(dict(r.variant_attrs).keys()) for r in rows}
    if len(rows) < 2:  # noqa: PLR2004 图纸语义：单成员不成组
        status, reason = "broken", f"成员不齐（{len(rows)}）"
    elif len(key_sets) > 1:
        status, reason = "broken", "主题冲突（组内维度键集不一致）"
    elif len(rows) > limit:
        status, reason = "broken", f"成员数 {len(rows)} 超上限 {limit}（巨型伪组护栏）"
    else:
        status, reason = "active", ""
    row = (
        await session.execute(
            text("UPDATE app.variant_group SET status = :st WHERE id = :g RETURNING team_id"),
            {"st": status, "g": group_id},
        )
    ).one()
    if status == "broken":
        await notify(
            session,
            team_id=int(row.team_id),
            severity="warn",
            category="catalog",
            title=f"变体组 #{group_id} 置 broken：{reason}",
            body="broken 组 spec 构建拒绝（001 §03）；修复成员/维度后归组任务自动回 active",
            object_type="variant_group",
            object_id=str(group_id),
            dedupe_key=f"variant_broken:{group_id}",
        )
    return status


async def _find_or_create_group(
    session: AsyncSession, *, team_id: int, parent_ref: str, theme: str | None
) -> int:
    existing = (
        await session.execute(
            text(
                "SELECT id FROM app.variant_group"
                " WHERE team_id = :t AND source_parent_ref = :p"
                " ORDER BY id LIMIT 1"
            ),
            {"t": team_id, "p": parent_ref},
        )
    ).scalar_one_or_none()
    if existing is not None:
        return int(existing)
    return int(
        (
            await session.execute(
                text(
                    "INSERT INTO app.variant_group (team_id, source_parent_ref, variation_theme)"
                    " VALUES (:t, :p, :th) RETURNING id"
                ),
                {"t": team_id, "p": parent_ref, "th": theme},
            )
        ).scalar_one()
    )


async def sync_team(session: AsyncSession, team_id: int, *, batch: int = 500) -> dict[str, Any]:
    """单团队自动归组：扫未归组且带可信 twister 素材的 product。"""
    limit = await max_group_size(session, team_id)
    candidates = (
        await session.execute(
            text(
                "SELECT id, source_ref, attrs ->> 'parent_asin' AS parent,"
                " attrs ->> 'variant_attributes' AS vattrs"
                " FROM app.product"
                " WHERE team_id = :t AND variant_group_id IS NULL"
                "   AND coalesce(attrs ->> 'variant_attributes', '') <> ''"
                "   AND coalesce(attrs ->> 'parent_asin', '') <> ''"
                "   AND attrs ->> 'parent_asin' <> source_ref"
                " ORDER BY id LIMIT :n"
            ),
            {"t": team_id, "n": batch},
        )
    ).all()
    stats = {"scanned": len(candidates), "grouped": 0, "groups_touched": 0, "broken": 0}
    by_parent: dict[str, list[Any]] = {}
    for c in candidates:
        by_parent.setdefault(str(c.parent), []).append(c)
    for parent_ref, members in by_parent.items():
        theme = ",".join(sorted(parse_variant_attrs(members[0].vattrs).keys())) or None
        group_id = await _find_or_create_group(
            session, team_id=team_id, parent_ref=parent_ref, theme=theme
        )
        for m in members:
            attrs = parse_variant_attrs(m.vattrs)
            await session.execute(
                text(
                    "INSERT INTO app.variant_member (group_id, product_id, variant_attrs)"
                    " VALUES (:g, :p, cast(:va AS jsonb))"
                    " ON CONFLICT (group_id, product_id) DO UPDATE SET"
                    "   variant_attrs = excluded.variant_attrs"
                ),
                {"g": group_id, "p": int(m.id), "va": json.dumps(attrs, ensure_ascii=False)},
            )
            await session.execute(
                text("UPDATE app.product SET variant_group_id = :g WHERE id = :p"),
                {"g": group_id, "p": int(m.id)},
            )
            stats["grouped"] += 1
        stats["groups_touched"] += 1
        if await reassess_group(session, group_id, limit=limit) == "broken":
            stats["broken"] += 1
    return stats


async def set_members(
    session: AsyncSession,
    *,
    group_id: int,
    team_id: int,
    members: list[dict[str, Any]],
) -> dict[str, Any]:
    """全量设置组成员（契约 PUT /variant-groups/{id}/members；人工修正入口）。"""
    group = (
        await session.execute(
            text("SELECT id, team_id FROM app.variant_group WHERE id = :g FOR UPDATE"),
            {"g": group_id},
        )
    ).one_or_none()
    if group is None or int(group.team_id) != team_id:
        raise BusinessError("VARIANT_GROUP_NOT_FOUND", "变体组不存在")
    if sum(1 for m in members if m.get("is_primary")) > 1:
        raise BusinessError("VARIANT_PRIMARY_CONFLICT", "每组至多一个主变体成员")
    want_ids = [int(m["product_id"]) for m in members]
    if len(set(want_ids)) != len(want_ids):
        raise BusinessError("VARIANT_MEMBER_DUPLICATED", "成员列表存在重复 product_id")
    for pid in want_ids:
        owner = (
            await session.execute(
                text("SELECT team_id, variant_group_id FROM app.product WHERE id = :p"),
                {"p": pid},
            )
        ).one_or_none()
        if owner is None or int(owner.team_id) != team_id:
            raise BusinessError("PRODUCT_NOT_FOUND", f"产品不存在：{pid}")
        if owner.variant_group_id is not None and int(owner.variant_group_id) != group_id:
            raise BusinessError(
                "VARIANT_MEMBER_TAKEN",
                f"产品 {pid} 已属组 {owner.variant_group_id}（一品最多属一组，001 §03）",
            )
    # 摘除不在名单内的旧成员（双向）
    await session.execute(
        text(
            "UPDATE app.product SET variant_group_id = NULL"
            " WHERE variant_group_id = :g AND id <> ALL(:keep)"
        ),
        {"g": group_id, "keep": want_ids or [0]},
    )
    await session.execute(
        text("DELETE FROM app.variant_member WHERE group_id = :g AND product_id <> ALL(:keep)"),
        {"g": group_id, "keep": want_ids or [0]},
    )
    for m in members:
        await session.execute(
            text(
                "INSERT INTO app.variant_member (group_id, product_id, variant_attrs, is_primary)"
                " VALUES (:g, :p, cast(:va AS jsonb), :prim)"
                " ON CONFLICT (group_id, product_id) DO UPDATE SET"
                "   variant_attrs = excluded.variant_attrs, is_primary = excluded.is_primary"
            ),
            {
                "g": group_id,
                "p": int(m["product_id"]),
                "va": json.dumps(m.get("variant_attrs") or {}, ensure_ascii=False),
                "prim": bool(m.get("is_primary")),
            },
        )
        await session.execute(
            text("UPDATE app.product SET variant_group_id = :g WHERE id = :p"),
            {"g": group_id, "p": int(m["product_id"])},
        )
    limit = await max_group_size(session, team_id)
    status = await reassess_group(session, group_id, limit=limit)
    return {"group_id": group_id, "members": len(members), "status": status}


async def run(sessions: async_sessionmaker[AsyncSession], config: dict[str, Any]) -> dict[str, Any]:
    """beat 任务本体：逐团队归组（团队间失败隔离）。"""
    batch = int(config.get("batch", 500))
    async with system_tx(sessions) as session:
        team_ids = [
            int(r.team_id)
            for r in await session.execute(
                text(
                    "SELECT DISTINCT team_id FROM app.product"
                    " WHERE variant_group_id IS NULL"
                    "   AND coalesce(attrs ->> 'variant_attributes', '') <> ''"
                    "   AND coalesce(attrs ->> 'parent_asin', '') <> ''"
                    "   AND attrs ->> 'parent_asin' <> source_ref"
                )
            )
        ]
    totals = {"teams": len(team_ids), "scanned": 0, "grouped": 0, "groups_touched": 0,
              "broken": 0, "failed": 0}  # fmt: skip
    for tid in team_ids:
        try:
            async with system_tx(sessions) as session:
                st = await sync_team(session, tid, batch=batch)
        except Exception as exc:
            totals["failed"] += 1
            log.warning("variant_sync.team_failed", team_id=tid, error=str(exc))
            continue
        for k in ("scanned", "grouped", "groups_touched", "broken"):
            totals[k] += int(st.get(k, 0))
    return totals
