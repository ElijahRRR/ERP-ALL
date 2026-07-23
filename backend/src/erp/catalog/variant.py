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
- broken 判定 v2（D-Q64③，判定随每次归组/成员变更重跑，冲突消除自动回 active）：
  仅真错误置 broken——维度值缺失 / 组内维度键集不一致；成员数 > variant.max_group_size
  （team_config > system_config > 默认 10）只发 oversize warn 观察标记不阻断；
  单员组为正常在场状态（尚未凑齐的家族）；
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
MAX_BATCH_MEMBERS_KEY = "variant.max_batch_members"
DEFAULT_MAX_BATCH_MEMBERS = 200


def parse_variant_attrs(raw: str | None) -> dict[str, str]:
    """twister 串 "color_name=Red; size_name=L" → dict（键保持 Amazon snake_case 原样）。"""
    out: dict[str, str] = {}
    for part in (raw or "").split(";"):
        k, sep, v = part.strip().partition("=")
        if sep and k.strip() and v.strip():
            out[k.strip()] = v.strip()
    return out


async def _config_int(session: AsyncSession, team_id: int, key: str, default: int) -> int:
    """整数配置读取：team_config > system_config > 代码默认（铁律5 配置中心）。"""
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
            {"t": team_id, "k": key},
        )
    ).scalar_one_or_none()
    return int(raw) if raw is not None else default


async def max_group_size(session: AsyncSession, team_id: int) -> int:
    """oversize 观察阈值（D-Q64③：超限仅 warn 不阻断）：team>system>默认 10。"""
    return await _config_int(session, team_id, MAX_GROUP_SIZE_KEY, DEFAULT_MAX_GROUP_SIZE)


async def max_batch_members(session: AsyncSession, team_id: int) -> int:
    """单批提交成员数上限（D-Q64③ feed 体积保护）：team>system>默认 200。"""
    return await _config_int(session, team_id, MAX_BATCH_MEMBERS_KEY, DEFAULT_MAX_BATCH_MEMBERS)


THEME_MAP_KEY = "variant.theme_map"
# 维度键 → Walmart Visible 属性名默认表（旧仓 remap 语义子集，只保直映射档）
DEFAULT_THEME_MAP: dict[str, str] = {
    "color_name": "color",
    "size_name": "size",
    "style_name": "style",
    "pattern_name": "pattern",
    "material_type": "material",
}


async def theme_map(session: AsyncSession) -> dict[str, str]:
    """维度键→Walmart 属性名映射：默认表并入 system_config variant.theme_map（铁律5）。

    config 键覆盖/新增，缺省键保留默认兜底——即便配置误删 color 映射，默认仍在
    （fail-closed 底线）。构建器（spec._theme_map 委托至此）与归组期预警共用本表，
    表迁居 catalog 域避免 spec↔variant 反向依赖（二期 B 重构）。
    """
    cfg = dict(DEFAULT_THEME_MAP)
    row = (
        await session.execute(
            text("SELECT value FROM app.system_config WHERE key = :k"), {"k": THEME_MAP_KEY}
        )
    ).scalar_one_or_none()
    if isinstance(row, str):
        try:
            row = json.loads(row)
        except ValueError:
            row = None
    if isinstance(row, dict):
        cfg.update({str(k): str(v) for k, v in row.items()})
    return cfg


async def reassess_group(session: AsyncSession, group_id: int, *, limit: int) -> str:
    """broken 判定 v2（D-Q64③）：仅真错误置 broken——维度值缺失 / 维度键集不一致。

    v1 的「成员<2」与「超上限」退场：单员组是尚未凑齐的正常家族（可散品上架或等
    兄弟入库）；巨型组 ≥limit 是旧仓伪组 hack 遗留（伪组根因已被「只信 twister」根治，
    真实大家族几十~几千员合法）——超上限只发 oversize warn 观察标记，状态仍 active。
    维度值缺失：成员 variant_attrs 为空 dict（人工 PUT 可造出）在构建期必然 fail-closed
    ——提前到判定期置 broken。返回归位后的状态。
    """
    rows = (
        await session.execute(
            text("SELECT variant_attrs FROM app.variant_member WHERE group_id = :g"),
            {"g": group_id},
        )
    ).all()
    key_sets = {frozenset(dict(r.variant_attrs).keys()) for r in rows}
    empty = sum(1 for r in rows if not dict(r.variant_attrs))
    if rows and empty:
        status, reason = "broken", f"成员维度值缺失（{empty} 名成员 variant_attrs 为空）"
    elif len(key_sets) > 1:
        status, reason = "broken", "主题冲突（组内维度键集不一致）"
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
            body="broken 组成组上架拒绝（D-Q64；散品模式不受限）；修复维度后归组任务自动回 active",
            object_type="variant_group",
            object_id=str(group_id),
            dedupe_key=f"variant_broken:{group_id}",
        )
        return status
    if len(rows) > limit:  # oversize 观察标记（D-Q64③：不阻断，仅提示复核）
        await notify(
            session,
            team_id=int(row.team_id),
            severity="warn",
            category="catalog",
            title=f"变体组 #{group_id} 成员数 {len(rows)} 超观察阈值 {limit}（oversize）",
            body="D-Q64：大家族合法、不置 broken；建议抽查维度值确认非伪组。"
            "单批提交上限由 variant.max_batch_members 另行保护",
            object_type="variant_group",
            object_id=str(group_id),
            dedupe_key=f"variant_oversize:{group_id}",
        )
    # 维度键映射预警（D-Q64① 前移：原先构建/补挂期 fail-closed 才发现——组 8 item_shape
    # 真机先例；现归组/判定期即提示，不阻断）
    tm = await theme_map(session)
    unknown = sorted({k for r in rows for k in dict(r.variant_attrs) if k not in tm})
    if unknown:
        await notify(
            session,
            team_id=int(row.team_id),
            severity="warn",
            category="catalog",
            title=f"变体组 #{group_id} 维度键未入映射表：{', '.join(unknown)}",
            body="构建时将以键名直查目标 PT 字段集，无同名字段会 fail-closed 拒绝；"
            "如需映射请更新 system_config variant.theme_map（item_shape→size 为真机先例）",
            object_type="variant_group",
            object_id=str(group_id),
            dedupe_key=f"variant_dimkey:{group_id}",
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


async def _find_groups_by_identifiers(
    session: AsyncSession, *, team_id: int, identifiers: list[str]
) -> list[Any]:
    """双向找组（复数）：组键命中 或 既有成员的 source_ref 命中的全部组（id 升序）。

    覆盖跨批到达的兄弟：后来者家族列表含先入库成员的 ASIN，即使组键（历史 min）与
    后来者视角的 min 不同也能归入同组。命中多组 = 同族历史分裂（桥接成员首次揭示两个
    分批建成、彼此标识不相交的组同属一家）——由 sync_team 合并或告警（检修增补；
    旧版 LIMIT 1 只取最小组，分裂组永不收敛）。
    """
    return list(
        (
            await session.execute(
                text(
                    "SELECT g.id, g.anchor_store_id FROM app.variant_group g"
                    " WHERE g.team_id = :t AND ("
                    "   g.source_parent_ref = ANY(:ids)"
                    "   OR EXISTS (SELECT 1 FROM app.variant_member m"
                    "               JOIN app.product p ON p.id = m.product_id"
                    "               WHERE m.group_id = g.id AND p.source_ref = ANY(:ids))"
                    " ) ORDER BY g.id"
                ),
                {"t": team_id, "ids": identifiers},
            )
        ).all()
    )


async def _merge_groups(session: AsyncSession, *, target_id: int, source_ids: list[int]) -> None:
    """source 组全体成员并入 target 并删除 source（同族历史分裂合并，检修增补）。

    仅未锚定的 source 可并（调用方过滤，锚定组不可被解散——BR-LST-013）。成员行走
    UPDATE 搬迁（variant_member.product_id 全表唯一=一品一组 DB 铁闸，先插后删会撞键）；
    is_primary 不带过去（两组各自主变体并存会撞"每组至多一主"语义；isPrimaryVariant
    本就不出门，D-Q63）。合并后由调用方 reassess 归位状态。
    """
    for sid in source_ids:
        await session.execute(
            text(
                "UPDATE app.variant_member SET group_id = :tg, is_primary = false"
                " WHERE group_id = :src"
            ),
            {"tg": target_id, "src": sid},
        )
        await session.execute(
            text("UPDATE app.product SET variant_group_id = :tg WHERE variant_group_id = :src"),
            {"tg": target_id, "src": sid},
        )
        await session.execute(text("DELETE FROM app.variant_group WHERE id = :src"), {"src": sid})


async def _dissolve_orphan_singletons(session: AsyncSession, team_id: int) -> int:
    """解散"错裂"的单员组：其成员的家族标识集与**其它组**相交（键或成员 ASIN）。

    真孤品单员组（与谁都不相交）保持原样——避免每轮重建换组号、通知随新 id 刷屏。
    仅动 单成员 + 未锁 anchor 的组（自动归组产物；有上架历史的组不碰）。
    D-Q64 后单员组不再 broken（正常在场状态），故不看 status，只看成员数与 anchor。
    """
    rows = (
        await session.execute(
            text(
                "SELECT g.id AS gid, p.id AS pid, p.source_ref,"
                " p.attrs ->> 'parent_asin' AS parent, p.attrs -> 'variation_asins' AS family"
                " FROM app.variant_group g"
                " JOIN app.variant_member m ON m.group_id = g.id"
                " JOIN app.product p ON p.id = m.product_id"
                " WHERE g.team_id = :t AND g.anchor_store_id IS NULL"
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


async def _place_component(
    session: AsyncSession,
    *,
    team_id: int,
    members: Sequence[Any],
    limit: int,
    stats: dict[str, Any],
) -> str:
    """把一个连通分量安置进组：双向找组 → 历史分裂合并/锚定冲突告警 → 挂员 → 判定。

    sync_team（beat 兜底）与 sync_product（入库实时钩子，D-Q64①）共用本体。
    返回安置后组状态。members 行需含 id/source_ref/parent/vattrs/family 列。
    """
    identifiers: set[str] = set()
    for c in members:
        fam = list(c.family) if isinstance(c.family, list) else []
        identifiers |= full_set(str(c.source_ref), c.parent, fam)
    hits = await _find_groups_by_identifiers(
        session, team_id=team_id, identifiers=sorted(identifiers)
    )
    anchored = [h for h in hits if h.anchor_store_id is not None]
    if not hits:
        theme = ",".join(sorted(parse_variant_attrs(members[0].vattrs).keys())) or None
        group_id = await _find_or_create_group(
            session, team_id=team_id, parent_ref=min(identifiers), theme=theme
        )
    elif len(anchored) > 1:
        # 同族分裂于多个已锚定组（桥接成员首次揭示）：anchor 不自动转移（BR-LST-013）
        # → 不合并；新成员并入最小 id 锚定组（确定性），warn 请人工处置
        group_id = int(anchored[0].id)
        others = ",".join(f"#{int(h.id)}" for h in hits if int(h.id) != group_id)
        await notify(
            session,
            team_id=team_id,
            severity="warn",
            category="catalog",
            title=f"同族变体组分裂于多个已锚定组：#{group_id} 与 {others}",
            body="多组均已锚定店铺，不可自动合并（BR-LST-013 anchor 不自动转移）；"
            "请先解锁 anchor（POST /variant-groups/{id}/anchor/release，须组在锚定店"
            "无在途/在架成员）或人工摘员后重归组",
            object_type="variant_group",
            object_id=str(group_id),
            dedupe_key=f"variant_split_anchored:{group_id}",
        )
    else:
        # 至多一个锚定组：目标=锚定组（有则）或最小 id 组；其余（必未锚定）并入
        group_id = int((anchored or hits)[0].id)
        sources = [int(h.id) for h in hits if int(h.id) != group_id]
        if sources:
            await _merge_groups(session, target_id=group_id, source_ids=sources)
            stats["merged"] += len(sources)
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
    status = await reassess_group(session, group_id, limit=limit)
    if status == "broken":
        stats["broken"] += 1
    return status


async def sync_product(session: AsyncSession, team_id: int, product_id: int) -> str | None:
    """单品实时归组（D-Q64①）：采集入库钩子同事务调用，素材落地即归组。

    仅动 未分组 + twister 素材齐备（variant_attributes 非空且非自指/有兄弟列表）的
    产品——与 sync_team 候选口径完全一致；安置语义（双向找组/历史分裂合并/锚定冲突
    告警/判定 v2）经 _place_component 与 beat 共享。无归组动作返回 None。
    beat variant_group_sync 降为兜底收敛（跨批解散重归/broken 复评/漏网扫描）。
    """
    row = (
        await session.execute(
            text(
                "SELECT id, source_ref, attrs ->> 'parent_asin' AS parent,"
                " attrs ->> 'variant_attributes' AS vattrs,"
                " attrs -> 'variation_asins' AS family"
                " FROM app.product"
                " WHERE id = :p AND team_id = :t AND variant_group_id IS NULL"
                "   AND coalesce(attrs ->> 'variant_attributes', '') <> ''"
                "   AND (attrs ->> 'parent_asin' <> source_ref"
                "        OR jsonb_array_length(coalesce(attrs -> 'variation_asins',"
                "                                       '[]'::jsonb)) > 0)"
            ),
            {"p": product_id, "t": team_id},
        )
    ).one_or_none()
    if row is None:
        return None
    limit = await max_group_size(session, team_id)
    stats: dict[str, Any] = {"grouped": 0, "groups_touched": 0, "broken": 0, "merged": 0}
    return await _place_component(session, team_id=team_id, members=[row], limit=limit, stats=stats)


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
             "groups_touched": 0, "broken": 0, "merged": 0, "healed": 0}  # fmt: skip
    for members in _components(candidates):
        await _place_component(session, team_id=team_id, members=members, limit=limit,
                               stats=stats)  # fmt: skip
    # broken 复评（D-Q64 兜底收敛）：v1 时代误置 broken 的组（成员<2/超上限）自动归位
    # active；真错误组维持 broken（通知 dedupe 不刷屏）。本轮已触组重评一次无害（幂等）。
    broken_ids = [
        int(r.id)
        for r in await session.execute(
            text("SELECT id FROM app.variant_group WHERE team_id = :t AND status = 'broken'"),
            {"t": team_id},
        )
    ]
    for gid in broken_ids:
        if await reassess_group(session, gid, limit=limit) == "active":
            stats["healed"] += 1
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


ON_CHANNEL_STATUSES = ("queued", "submitted", "published", "live")


async def release_anchor(session: AsyncSession, *, group_id: int, team_id: int) -> dict[str, Any]:
    """anchor 解锁通道（检修：挂账「首发即败人工解锁口径」清偿，替代 runbook 手工 SQL）。

    仅服务「组首发即被渠道整体驳回、想换店重投」场景；BR-LST-013 锁定语义不破——
    fail-closed：组在锚定店仍有在途/在架成员 listing（queued/submitted/published/live，
    与提交守卫「在场」口径同源）→ VARIANT_ANCHOR_IN_USE 拒绝；未锚定 →
    VARIANT_ANCHOR_NOT_SET。FOR UPDATE 持组行锁：与提交路径的原子锁 UPDATE 串行化，
    不给"边解锁边入列"留窗。审计留痕由 router 层落。
    """
    group = (
        await session.execute(
            text(
                "SELECT id, team_id, anchor_store_id FROM app.variant_group"
                " WHERE id = :g FOR UPDATE"
            ),
            {"g": group_id},
        )
    ).one_or_none()
    if group is None or int(group.team_id) != team_id:
        raise BusinessError("VARIANT_GROUP_NOT_FOUND", "变体组不存在")
    if group.anchor_store_id is None:
        raise BusinessError(
            "VARIANT_ANCHOR_NOT_SET", "变体组未锚定任何店铺，无需解锁", http_status=409
        )
    anchor_store = int(group.anchor_store_id)
    in_flight = (
        await session.execute(
            text(
                "SELECT l.id, l.status FROM app.listing l"
                " JOIN app.variant_member m ON m.product_id = l.product_id"
                " WHERE m.group_id = :g AND l.store_id = :s AND l.status = ANY(:sts)"
                " ORDER BY l.id LIMIT 5"
            ),
            {"g": group_id, "s": anchor_store, "sts": list(ON_CHANNEL_STATUSES)},
        )
    ).all()
    if in_flight:
        detail = ", ".join(f"listing #{int(r.id)}({r.status})" for r in in_flight)
        raise BusinessError(
            "VARIANT_ANCHOR_IN_USE",
            f"锚定店仍有在途/在架成员（{detail}），不可解锁；请先撤除/下架整组",
            http_status=409,
        )
    await session.execute(
        text("UPDATE app.variant_group SET anchor_store_id = NULL WHERE id = :g"),
        {"g": group_id},
    )
    return {"group_id": group_id, "released_store_id": anchor_store}


async def load_build_contexts(
    session: AsyncSession, product_ids: Sequence[int]
) -> dict[int, dict[str, Any]]:
    """spec 构建器变体段只读上下文——批量版（检修：挂账「组上下文批量化」清偿）。

    一次查询取全部已分组产品的组 + 本品成员行 + 组内全体成员 id，键=product_id：
      {group_id, group_ref="VG{id}"（D-Q63② 渠道中立引用，不撞 ASIN 也不撞渠道既有值）,
       status, anchor_store_id, variation_theme,
       member_product_ids: [成员 product_id 升序], variant_attrs: 本品成员行维度值 dict}。
    未分组产品不出现在返回键中——调用方 .get(pid) 得 None，语义同单品版
    （构建器走非变体路径，指纹/缓存不变）。
    """
    if not product_ids:
        return {}
    rows = (
        await session.execute(
            text(
                "SELECT p.id AS pid, g.id AS group_id, g.status AS status,"
                " g.anchor_store_id AS anchor_store_id, g.variation_theme AS variation_theme,"
                " m.variant_attrs AS variant_attrs,"
                " (SELECT array_agg(m2.product_id ORDER BY m2.product_id)"
                "    FROM app.variant_member m2 WHERE m2.group_id = g.id) AS member_ids"
                " FROM app.product p"
                " JOIN app.variant_group g ON g.id = p.variant_group_id"
                " LEFT JOIN app.variant_member m ON m.group_id = g.id AND m.product_id = p.id"
                " WHERE p.id = ANY(:pids)"
            ),
            {"pids": [int(p) for p in product_ids]},
        )
    ).all()
    return {
        int(r.pid): {
            "group_id": int(r.group_id),
            "group_ref": f"VG{int(r.group_id)}",
            "status": str(r.status),
            "anchor_store_id": int(r.anchor_store_id) if r.anchor_store_id is not None else None,
            "variation_theme": r.variation_theme,
            "member_product_ids": [int(x) for x in (r.member_ids or [])],
            "variant_attrs": dict(r.variant_attrs) if r.variant_attrs else {},
        }
        for r in rows
    }


async def load_build_context(session: AsyncSession, product_id: int) -> dict[str, Any] | None:
    """单品版（增量2；D-Q63）——委托批量版，口径见 load_build_contexts。"""
    return (await load_build_contexts(session, [product_id])).get(product_id)


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
                    " UNION"  # broken 复评 + 未锚定单员组解散重归 也要扫到（仅上句扫不到）
                    " SELECT DISTINCT g.team_id FROM app.variant_group g"
                    " WHERE g.status = 'broken'"
                    "    OR (g.anchor_store_id IS NULL AND"
                    "        (SELECT count(*) FROM app.variant_member m"
                    "          WHERE m.group_id = g.id) = 1)"
                )
            )
        ]
    totals = {"teams": len(team_ids), "scanned": 0, "dissolved": 0, "grouped": 0,
              "groups_touched": 0, "broken": 0, "merged": 0, "healed": 0,
              "failed": 0}  # fmt: skip
    for tid in team_ids:
        try:
            async with system_tx(sessions) as session:
                st = await sync_team(session, tid, batch=batch)
        except Exception as exc:
            totals["failed"] += 1
            log.warning("variant_sync.team_failed", team_id=tid, error=str(exc))
            continue
        for k in ("scanned", "dissolved", "grouped", "groups_touched", "broken", "merged",
                  "healed"):  # fmt: skip
            totals[k] += int(st.get(k, 0))
    return totals
