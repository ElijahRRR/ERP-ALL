"""变体归组服务（R2-11 增量1；D-Q2/D-Q63，001 §03 图纸语义）。

归组协议（.agent/evidence/R2-11/archaeology.md §2/§3）：
- 素材 = product.attrs（采集入库）：parent_asin（parser 无父体时填自身，归组必须排除
  parent==source_ref 的自指）、variant_attributes（twister "k=v; k=v" 串）——**只信
  twister 来源**（variant_attributes 为空不归组：旧全页正则 variation_asins 是
  「巨型伪组」根因，BR-ASC-003 考古坑账）；
- 组身份 = 家族标识集连通分量（增量2.5 修复：parent ∪ twister 兄弟列表 ∪ 自身 相交即同组，
  组键存分量 min——真机实证同家族各页 parentAsin 可不一致，单靠 parent 会裂组）；
  一品最多属一组（variant_member.product_id UNIQUE）；
- 双向关联同事务维护：variant_member 行 + product.variant_group_id；
- broken 判定 v1（D-Q63 配套口径，判定随每次归组/成员变更重跑，冲突消除自动回 active）：
  成员数 < 2 / 组内维度键集不一致 / 成员数 > variant.max_group_size
  （team_config > system_config > 默认 10——旧仓巨型组阈值经验值）；
- anchor_store_id 本服务不写：首次上架时由 listing 管道锁定（增量2，BR-LST-013）；
- match 模式豁免归组属上架期语义（增量2 spec 段处理），归组本身渠道无关。
"""

import json
from collections.abc import Sequence
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


def full_set(source_ref: str, parent: str | None, variation_asins: list[str] | None) -> set[str]:
    """家族标识集 = {自身} ∪ {parent(≠自身时)} ∪ twister 兄弟列表（旧仓 full_set 语义）。

    真机实证（R2-11 增量2.5 修复）：同家族各变体页抓到的 parentAsin 可不一致（旧仓早有
    记载"parent_asin 不能当组 ID"），单靠 parent 归组会把一家裂成多组；twister 兄弟列表
    才是家族权威信号。归组按标识集相交做连通分量，组键取分量标识集 min（确定性）。
    """
    ids = {source_ref}
    if parent and parent != source_ref:
        ids.add(parent)
    ids.update(a for a in (variation_asins or []) if a)
    return ids


def _components(candidates: Sequence[Any]) -> list[list[Any]]:
    """按 full_set 相交把候选划成连通分量（批内并查集，标识为节点）。"""
    parent_of: dict[str, str] = {}

    def find(x: str) -> str:
        while parent_of.setdefault(x, x) != x:
            parent_of[x] = parent_of[parent_of[x]]
            x = parent_of[x]
        return x

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent_of[ra] = rb

    sets = []
    for c in candidates:
        fam = list(c.family) if isinstance(c.family, list) else []
        ids = full_set(str(c.source_ref), c.parent, fam)
        sets.append(ids)
        anchor_id = next(iter(ids))
        for i in ids:
            union(anchor_id, i)
    by_root: dict[str, list[Any]] = {}
    for c, ids in zip(candidates, sets, strict=True):
        by_root.setdefault(find(next(iter(ids))), []).append(c)
    return list(by_root.values())


async def _find_group_by_identifiers(
    session: AsyncSession, *, team_id: int, identifiers: list[str]
) -> int | None:
    """双向找组：组键命中 或 既有成员的 source_ref 命中，任一相交即视为同家族。

    覆盖跨批到达的兄弟：后来者家族列表含先入库成员的 ASIN，即使组键（历史 min）与
    后来者视角的 min 不同也能归入同组。
    """
    row = (
        await session.execute(
            text(
                "SELECT g.id FROM app.variant_group g"
                " WHERE g.team_id = :t AND ("
                "   g.source_parent_ref = ANY(:ids)"
                "   OR EXISTS (SELECT 1 FROM app.variant_member m"
                "               JOIN app.product p ON p.id = m.product_id"
                "               WHERE m.group_id = g.id AND p.source_ref = ANY(:ids))"
                " ) ORDER BY g.id LIMIT 1"
            ),
            {"t": team_id, "ids": identifiers},
        )
    ).scalar_one_or_none()
    return int(row) if row is not None else None


async def _dissolve_orphan_singletons(session: AsyncSession, team_id: int) -> int:
    """解散"错裂"的 broken 单员组：其成员的家族标识集与**其它组**相交（键或成员 ASIN）。

    真孤品单员组（与谁都不相交）保持原样——避免每轮重建换组号、broken 通知随新 id 刷屏。
    仅动 broken + 单成员 + 未锁 anchor 的组（自动归组产物；有上架历史的组不碰）。
    """
    rows = (
        await session.execute(
            text(
                "SELECT g.id AS gid, p.id AS pid, p.source_ref,"
                " p.attrs ->> 'parent_asin' AS parent, p.attrs -> 'variation_asins' AS family"
                " FROM app.variant_group g"
                " JOIN app.variant_member m ON m.group_id = g.id"
                " JOIN app.product p ON p.id = m.product_id"
                " WHERE g.team_id = :t AND g.status = 'broken' AND g.anchor_store_id IS NULL"
                "   AND (SELECT count(*) FROM app.variant_member m2"
                "         WHERE m2.group_id = g.id) = 1"
            ),
            {"t": team_id},
        )
    ).all()
    dissolved = 0
    for r in rows:
        fam = list(r.family) if isinstance(r.family, list) else []
        ids = sorted(full_set(str(r.source_ref), r.parent, fam))
        intersects = (
            await session.execute(
                text(
                    "SELECT 1 FROM app.variant_group g"
                    " WHERE g.team_id = :t AND g.id <> :g AND ("
                    "   g.source_parent_ref = ANY(:ids)"
                    "   OR EXISTS (SELECT 1 FROM app.variant_member m"
                    "               JOIN app.product p ON p.id = m.product_id"
                    "               WHERE m.group_id = g.id AND p.source_ref = ANY(:ids))"
                    " ) LIMIT 1"
                ),
                {"t": team_id, "g": int(r.gid), "ids": ids},
            )
        ).first()
        if intersects is None:
            continue
        await session.execute(
            text("DELETE FROM app.variant_member WHERE group_id = :g"), {"g": int(r.gid)}
        )
        await session.execute(
            text("UPDATE app.product SET variant_group_id = NULL WHERE id = :p"),
            {"p": int(r.pid)},
        )
        await session.execute(
            text("DELETE FROM app.variant_group WHERE id = :g"), {"g": int(r.gid)}
        )
        dissolved += 1
    return dissolved


async def sync_team(session: AsyncSession, team_id: int, *, batch: int = 500) -> dict[str, Any]:
    """单团队自动归组：候选按家族标识集连通分量归组（增量2.5：不再单靠 parent_asin）。"""
    limit = await max_group_size(session, team_id)
    dissolved = await _dissolve_orphan_singletons(session, team_id)
    candidates = (
        await session.execute(
            text(
                "SELECT id, source_ref, attrs ->> 'parent_asin' AS parent,"
                " attrs ->> 'variant_attributes' AS vattrs,"
                " attrs -> 'variation_asins' AS family"
                " FROM app.product"
                " WHERE team_id = :t AND variant_group_id IS NULL"
                "   AND coalesce(attrs ->> 'variant_attributes', '') <> ''"
                "   AND (attrs ->> 'parent_asin' <> source_ref"
                "        OR jsonb_array_length(coalesce(attrs -> 'variation_asins',"
                "                                       '[]'::jsonb)) > 0)"
                " ORDER BY id LIMIT :n"
            ),
            {"t": team_id, "n": batch},
        )
    ).all()
    stats = {"scanned": len(candidates), "dissolved": dissolved, "grouped": 0,
             "groups_touched": 0, "broken": 0}  # fmt: skip
    for members in _components(candidates):
        identifiers: set[str] = set()
        for c in members:
            fam = list(c.family) if isinstance(c.family, list) else []
            identifiers |= full_set(str(c.source_ref), c.parent, fam)
        group_id = await _find_group_by_identifiers(
            session, team_id=team_id, identifiers=sorted(identifiers)
        )
        if group_id is None:
            theme = ",".join(sorted(parse_variant_attrs(members[0].vattrs).keys())) or None
            group_id = await _find_or_create_group(
                session, team_id=team_id, parent_ref=min(identifiers), theme=theme
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


async def load_build_context(session: AsyncSession, product_id: int) -> dict[str, Any] | None:
    """spec 构建器变体段只读上下文（增量2；D-Q63）。

    product.variant_group_id 为空 → None（未分组：构建器走非变体路径，指纹/缓存不变）；
    否则一次查询取组 + 本品成员行 + 组内全体成员 id：
      {group_id, group_ref="VG{id}"（D-Q63② 渠道中立引用，不撞 ASIN 也不撞渠道既有值）,
       status, anchor_store_id, variation_theme,
       member_product_ids: [成员 product_id 升序], variant_attrs: 本品成员行维度值 dict}。
    """
    row = (
        await session.execute(
            text(
                "SELECT g.id AS group_id, g.status AS status,"
                " g.anchor_store_id AS anchor_store_id, g.variation_theme AS variation_theme,"
                " m.variant_attrs AS variant_attrs,"
                " (SELECT array_agg(m2.product_id ORDER BY m2.product_id)"
                "    FROM app.variant_member m2 WHERE m2.group_id = g.id) AS member_ids"
                " FROM app.product p"
                " JOIN app.variant_group g ON g.id = p.variant_group_id"
                " LEFT JOIN app.variant_member m ON m.group_id = g.id AND m.product_id = p.id"
                " WHERE p.id = :pid"
            ),
            {"pid": product_id},
        )
    ).one_or_none()
    if row is None:
        return None
    return {
        "group_id": int(row.group_id),
        "group_ref": f"VG{int(row.group_id)}",
        "status": str(row.status),
        "anchor_store_id": int(row.anchor_store_id) if row.anchor_store_id is not None else None,
        "variation_theme": row.variation_theme,
        "member_product_ids": [int(x) for x in (row.member_ids or [])],
        "variant_attrs": dict(row.variant_attrs) if row.variant_attrs else {},
    }


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
                    "   AND (attrs ->> 'parent_asin' <> source_ref"
                    "        OR jsonb_array_length(coalesce(attrs -> 'variation_asins',"
                    "                                       '[]'::jsonb)) > 0)"
                    " UNION"  # 键过时的 broken 单员组所在团队也要扫（成员已入组，仅上句扫不到）
                    " SELECT DISTINCT team_id FROM app.variant_group"
                    " WHERE status = 'broken' AND anchor_store_id IS NULL"
                )
            )
        ]
    totals = {"teams": len(team_ids), "scanned": 0, "dissolved": 0, "grouped": 0,
              "groups_touched": 0, "broken": 0, "failed": 0}  # fmt: skip
    for tid in team_ids:
        try:
            async with system_tx(sessions) as session:
                st = await sync_team(session, tid, batch=batch)
        except Exception as exc:
            totals["failed"] += 1
            log.warning("variant_sync.team_failed", team_id=tid, error=str(exc))
            continue
        for k in ("scanned", "dissolved", "grouped", "groups_touched", "broken"):
            totals[k] += int(st.get(k, 0))
    return totals
