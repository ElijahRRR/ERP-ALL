"""黑名单断言账本（RS-04D，评审 B5①；D-Q65 配套口径）。

模型：
- 一主体（team 作用域 × domain × subject_norm）N 条源断言并存；来源
  import/manual/tro_sync/trademark_sync/error_recycle；verdict block/allow
  （allow=人工白名单裁决，DDL 强制仅 manual 源）。
- 状态机 append-only：pending（候选，报错回收人工闸 D-Q65②）→ active（人工确认/
  自动源直挂）→ revoked（撤销留行不删，decided_/revoked_ 系列列留痕）。
- canonical = blacklist_* 六表的 active 行 = 有效断言投影：
  存在 active manual allow → 主体不拉黑（人工裁决压一切自动源；撤销 allow 即回滚）；
  否则存在任一 active block → 拉黑，canonical.source 取最高优先级 block 源
  （manual > tro_sync > trademark_sync > error_recycle > import）；
  否则 → 摘除（status='removed'，不删行——与 001 §04 语义一致）。
- L0/L2 读侧不改：仍读 blacklist_* active 投影；投影写入触发 0014 统计触发器
  bump dataset_revision('blacklist') 使缓存失效，契约不变。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.errors import BusinessError

SOURCES = ("import", "manual", "tro_sync", "trademark_sync", "error_recycle")
# canonical 主导源优先级（D-Q65 P1：人工最高，自动源按证据强度排）
SOURCE_PRIORITY = ("manual", "tro_sync", "trademark_sync", "error_recycle", "import")

# domain → (canonical 表, 主体列, 展示列)
TABLE_BY_DOMAIN: dict[str, tuple[str, str, str | None]] = {
    "brand": ("blacklist_brand", "brand_norm", "brand_display"),
    "seller": ("blacklist_seller", "seller_ref", "seller_name"),
    "asin": ("blacklist_asin", "asin", None),
    "category": ("blacklist_category", "category_ref", None),
    "address": ("blacklist_address", "street_norm", None),
    "zip": ("blacklist_zip", "zip5", None),
}

_ON_CONFLICT_LIVE = (
    " ON CONFLICT (COALESCE(team_id, 0), domain, subject_norm, source, verdict,"
    " COALESCE(source_ref, '')) WHERE status IN ('pending','active') DO NOTHING"
)


def _table(domain: str) -> tuple[str, str, str | None]:
    if domain not in TABLE_BY_DOMAIN:
        raise BusinessError("BLACKLIST_DOMAIN_INVALID", f"未知黑名单域：{domain}")
    return TABLE_BY_DOMAIN[domain]


async def record_assertion(
    session: AsyncSession,
    *,
    team_id: int | None,
    domain: str,
    subject_norm: str,
    subject_display: str | None = None,
    source: str,
    source_ref: str | None = None,
    reason: str | None = None,
    verdict: str = "block",
    status: str = "active",
    created_by: int | None = None,
) -> dict[str, Any]:
    """记一条源断言（幂等：同 作用域×域×主体×源×verdict×source_ref 的在册断言唯一）。

    status='active' 立即投影 canonical；status='pending'（候选）不投影，等
    decide_assertion 人工确认。→ {assertion_id|None, created, canonical}。
    """
    _table(domain)
    if source not in SOURCES:
        raise BusinessError("BLACKLIST_SOURCE_INVALID", f"未知断言来源：{source}")
    if status not in ("pending", "active"):
        raise BusinessError("BLACKLIST_STATUS_INVALID", f"新断言状态非法：{status}")
    row = (
        await session.execute(
            text(
                "INSERT INTO app.blacklist_assertion"
                " (team_id, domain, subject_norm, subject_display, verdict, source,"
                "  source_ref, reason, status, created_by)"
                " VALUES (:t, :d, :s, :disp, :v, :src, :ref, :reason, :st, :by)"
                + _ON_CONFLICT_LIVE
                + " RETURNING id"
            ),
            {
                "t": team_id,
                "d": domain,
                "s": subject_norm,
                "disp": subject_display,
                "v": verdict,
                "src": source,
                "ref": source_ref,
                "reason": reason,
                "st": status,
                "by": created_by,
            },
        )
    ).scalar_one_or_none()
    canonical = "unchanged"
    if status == "active":
        canonical = await project_subject(
            session, team_id=team_id, domain=domain, subject_norm=subject_norm
        )
    return {"assertion_id": row, "created": row is not None, "canonical": canonical}


async def revoke_assertion(
    session: AsyncSession,
    *,
    assertion_id: int,
    team_id: int | None,
    actor_id: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """撤销一条断言（active/pending → revoked，留行）并重投影主体。

    B5① 验收②语义：撤销一源后主体是否仍拉黑由其余有效断言决定，不误删。
    """
    row = (
        await session.execute(
            text(
                "UPDATE app.blacklist_assertion"
                " SET status = 'revoked', revoked_by = :by, revoked_at = now(),"
                "     revoke_reason = :reason"
                " WHERE id = :id AND status IN ('pending','active')"
                "   AND team_id IS NOT DISTINCT FROM :t"
                " RETURNING domain, subject_norm"
            ),
            {"id": assertion_id, "by": actor_id, "reason": reason, "t": team_id},
        )
    ).one_or_none()
    if row is None:
        raise BusinessError(
            "BLACKLIST_ASSERTION_NOT_FOUND", "断言不存在、已撤销或作用域不符", http_status=409
        )
    canonical = await project_subject(
        session, team_id=team_id, domain=str(row.domain), subject_norm=str(row.subject_norm)
    )
    return {"assertion_id": assertion_id, "canonical": canonical}


async def decide_assertion(
    session: AsyncSession,
    *,
    assertion_id: int,
    team_id: int | None,
    approve: bool,
    actor_id: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """人工裁决候选断言（pending → active/revoked）并重投影（D-Q65② 人工闸）。"""
    if approve:
        sql = (
            "UPDATE app.blacklist_assertion"
            " SET status = 'active', decided_by = :by, decided_at = now()"
            " WHERE id = :id AND status = 'pending'"
            "   AND team_id IS NOT DISTINCT FROM :t"
            " RETURNING domain, subject_norm"
        )
        params: dict[str, Any] = {"id": assertion_id, "by": actor_id, "t": team_id}
    else:
        sql = (
            "UPDATE app.blacklist_assertion"
            " SET status = 'revoked', decided_by = :by, decided_at = now(),"
            "     revoked_by = :by, revoked_at = now(), revoke_reason = :reason"
            " WHERE id = :id AND status = 'pending'"
            "   AND team_id IS NOT DISTINCT FROM :t"
            " RETURNING domain, subject_norm"
        )
        params = {"id": assertion_id, "by": actor_id, "reason": reason, "t": team_id}
    row = (await session.execute(text(sql), params)).one_or_none()
    if row is None:
        raise BusinessError(
            "BLACKLIST_ASSERTION_NOT_PENDING", "候选断言不存在或已裁决", http_status=409
        )
    canonical = await project_subject(
        session, team_id=team_id, domain=str(row.domain), subject_norm=str(row.subject_norm)
    )
    return {"assertion_id": assertion_id, "approved": approve, "canonical": canonical}


async def revoke_by_source_ref(
    session: AsyncSession,
    *,
    team_id: int | None,
    domain: str,
    source: str,
    source_ref: str,
    actor_id: int | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """按 来源×source_ref 批量撤销在册断言并逐主体重投影。

    TRO 链（D-Q65①）：案件 dismissed/settled 时撤销其派生的全部 tro_sync 断言，
    主体是否仍拉黑由余源决定（B5① 验收②语义）。无在册断言 → revoked=0（幂等）。
    """
    _table(domain)
    if source not in SOURCES:
        raise BusinessError("BLACKLIST_SOURCE_INVALID", f"未知断言来源：{source}")
    subjects = (
        (
            await session.execute(
                text(
                    "UPDATE app.blacklist_assertion"
                    " SET status = 'revoked', revoked_by = :by, revoked_at = now(),"
                    "     revoke_reason = :reason"
                    " WHERE domain = :d AND source = :src AND source_ref = :ref"
                    "   AND status IN ('pending','active')"
                    "   AND team_id IS NOT DISTINCT FROM :t"
                    " RETURNING subject_norm"
                ),
                {
                    "d": domain,
                    "src": source,
                    "ref": source_ref,
                    "by": actor_id,
                    "reason": reason,
                    "t": team_id,
                },
            )
        )
        .scalars()
        .all()
    )
    for subj in subjects:
        await project_subject(session, team_id=team_id, domain=domain, subject_norm=str(subj))
    return {"revoked": len(subjects)}


async def project_subject(
    session: AsyncSession, *, team_id: int | None, domain: str, subject_norm: str
) -> str:
    """单主体投影：有效断言 → canonical（blacklist_* active 行）。→ 'active'|'removed'。"""
    table, subj_col, disp_col = _table(domain)
    rows = (
        await session.execute(
            text(
                "SELECT verdict, source, reason, subject_display"
                " FROM app.blacklist_assertion"
                " WHERE domain = :d AND subject_norm = :s AND status = 'active'"
                "   AND team_id IS NOT DISTINCT FROM :t"
            ),
            {"d": domain, "s": subject_norm, "t": team_id},
        )
    ).all()
    allow = any(r.verdict == "allow" for r in rows)
    blocks = [r for r in rows if r.verdict == "block"]
    if allow or not blocks:
        await session.execute(
            text(
                f"UPDATE app.{table} SET status = 'removed', removed_at = now()"
                f" WHERE COALESCE(team_id, 0) = COALESCE(:t, 0) AND {subj_col} = :s"
                "   AND status = 'active'"
            ),
            {"t": team_id, "s": subject_norm},
        )
        return "removed"
    lead = min(blocks, key=lambda r: SOURCE_PRIORITY.index(str(r.source)))
    cols = ["team_id", subj_col, "reason", "source"]
    vals = [":t", ":s", ":reason", ":src"]
    params: dict[str, Any] = {
        "t": team_id, "s": subject_norm, "reason": lead.reason, "src": lead.source,
    }  # fmt: skip
    if disp_col:
        cols.insert(2, disp_col)
        vals.insert(2, ":disp")
        params["disp"] = next((r.subject_display for r in blocks if r.subject_display), None)
    await session.execute(
        text(
            f"INSERT INTO app.{table} ({', '.join(cols)}) VALUES ({', '.join(vals)})"
            f" ON CONFLICT (COALESCE(team_id, 0), {subj_col}) WHERE status = 'active'"
            f" DO UPDATE SET source = excluded.source,"
            f"   reason = COALESCE(excluded.reason, app.{table}.reason)"
        ),
        params,
    )
    return "active"


async def rebuild_canonical(
    session: AsyncSession, *, domain: str, team_id: int | None = None
) -> dict[str, int]:
    """全量重建 canonical（B5① 验收④）：按有效断言重投影域内全部主体。

    覆盖两向差异：①断言应拉黑而 canonical 缺/removed → 补 active；②canonical active
    而断言不支持（无 block / 被 allow 压制）→ 摘除。幂等：连续两跑第二跑零变更。
    team_id 传 None 时重建全部作用域（含全局与各团队）。
    """
    table, subj_col, _ = _table(domain)
    scope = "" if team_id is None else " AND a.team_id IS NOT DISTINCT FROM :t"
    scope_c = "" if team_id is None else " AND c.team_id IS NOT DISTINCT FROM :t"
    params: dict[str, Any] = {"d": domain}
    if team_id is not None:
        params["t"] = team_id
    subjects = (
        await session.execute(
            text(
                "SELECT DISTINCT team_id, subject_norm FROM app.blacklist_assertion a"
                " WHERE a.domain = :d"
                + scope
                + f" UNION SELECT team_id, {subj_col} FROM app.{table} c"
                "  WHERE c.status = 'active'" + scope_c
            ),
            params,
        )
    ).all()
    stats = {"checked": len(subjects), "active": 0, "removed": 0, "changed": 0}
    for s in subjects:
        before = (
            await session.execute(
                text(
                    f"SELECT status FROM app.{table}"
                    f" WHERE COALESCE(team_id, 0) = COALESCE(:t, 0) AND {subj_col} = :s"
                    " ORDER BY (status = 'active') DESC LIMIT 1"
                ),
                {"t": s.team_id, "s": s.subject_norm},
            )
        ).scalar_one_or_none()
        after = await project_subject(
            session, team_id=s.team_id, domain=domain, subject_norm=str(s.subject_norm)
        )
        stats[after] += 1
        if (before == "active") != (after == "active"):
            stats["changed"] += 1
    return stats
