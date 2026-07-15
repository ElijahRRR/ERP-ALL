"""GTIN 池：导入 / 占用 / 释放 / 终身绑定（specs/001 §03 gtin_pool 状态机）。

并发协议：单语句 UPDATE…WHERE id=(SELECT…FOR UPDATE SKIP LOCKED) 防双占。
used 永不回收（防跨店重用关联——宪法级）；held→free 仅限上架终态失败。
"""

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.errors import BusinessError

_GTIN_RE = re.compile(r"^[0-9]{12,13}$")
UPC_A_LEN = 12


def _checksum_ok(gtin: str) -> bool:
    """GS1 校验位（UPC-A 12 / EAN-13 13 通用算法）。"""
    digits = [int(c) for c in gtin]
    check = digits.pop()
    total = sum(d * (3 if (len(digits) - 1 - i) % 2 == 0 else 1) for i, d in enumerate(digits))
    return (10 - total % 10) % 10 == check


async def import_batch(
    session: AsyncSession,
    *,
    team_id: int,
    gtins: list[str],
    source: str = "generator_import",
    created_by: int | None = None,
) -> dict[str, Any]:
    """批量导入（重复/格式/校验位不合格逐条报告，不整批失败）。"""
    imported, rejected = 0, []
    for raw in dict.fromkeys(g.strip() for g in gtins):
        if not _GTIN_RE.match(raw):
            rejected.append({"gtin": raw, "code": "GTIN_FORMAT"})
            continue
        if not _checksum_ok(raw):
            rejected.append({"gtin": raw, "code": "GTIN_CHECKSUM"})
            continue
        kind = "upc_a" if len(raw) == UPC_A_LEN else "ean_13"
        row = await session.execute(
            text(
                "INSERT INTO app.gtin_pool (team_id, gtin, gtin_kind, source, created_by)"
                " VALUES (:t, :g, :k, :s, :u)"
                " ON CONFLICT (gtin) DO NOTHING RETURNING id"
            ),
            {"t": team_id, "g": raw, "k": kind, "s": source, "u": created_by},
        )
        if row.scalar_one_or_none() is None:
            rejected.append({"gtin": raw, "code": "GTIN_DUPLICATE"})
        else:
            imported += 1
    return {"imported": imported, "rejected": rejected}


# 占号优先序默认值：真实购入 UPC 优先于生成号段（可被配置覆盖，见 _kind_preference）
_DEFAULT_KIND_PREFERENCE = ("upc_a", "ean_13")


async def _kind_preference(session: AsyncSession, team_id: int) -> list[str]:
    """占号优先序：team_config > system_config > 默认（键 gtin.kind_preference，JSON 数组）。"""
    for sql, params in (
        (
            "SELECT value FROM app.team_config WHERE team_id = :t AND key = 'gtin.kind_preference'",
            {"t": team_id},
        ),
        ("SELECT value FROM app.system_config WHERE key = 'gtin.kind_preference'", {}),
    ):
        row = (await session.execute(text(sql), params)).first()
        if row is not None and isinstance(row[0], list) and row[0]:
            return [str(k) for k in row[0]]
    return list(_DEFAULT_KIND_PREFERENCE)


async def hold_one(
    session: AsyncSession, *, team_id: int, listing_id: int, kind: str | None = None
) -> str:
    """占用一枚 free GTIN（单语句防双占）。

    kind=None（常规路径）按优先序逐池尝试——真机教训：写死 ean_13 时导入的
    真实 UPC（upc_a 池）永远取不到。全部池空 → GTIN_POOL_EMPTY。
    """
    kinds = [kind] if kind else await _kind_preference(session, team_id)
    for k in kinds:
        gtin = (
            await session.execute(
                text(
                    "UPDATE app.gtin_pool SET status = 'held', held_listing_id = :l,"
                    " held_at = now()"
                    " WHERE id = (SELECT id FROM app.gtin_pool"
                    "             WHERE team_id = :t AND gtin_kind = :k AND status = 'free'"
                    "             LIMIT 1 FOR UPDATE SKIP LOCKED)"
                    " RETURNING gtin"
                ),
                {"l": listing_id, "t": team_id, "k": k},
            )
        ).scalar_one_or_none()
        if gtin is not None:
            return str(gtin)
    raise BusinessError("GTIN_POOL_EMPTY", f"GTIN 池（{'/'.join(kinds)}）无可用号码")


async def mark_used(session: AsyncSession, listing_id: int) -> None:
    """listing 首次 published → 终身绑定。"""
    await session.execute(
        text(
            "UPDATE app.gtin_pool SET status = 'used', used_listing_id = held_listing_id,"
            " used_at = now()"
            " WHERE held_listing_id = :l AND status = 'held'"
        ),
        {"l": listing_id},
    )


async def release(session: AsyncSession, listing_id: int) -> None:
    """上架终态失败 → 归还池（仅 held；used 永不回收）。"""
    await session.execute(
        text(
            "UPDATE app.gtin_pool SET status = 'free', held_listing_id = NULL, held_at = NULL"
            " WHERE held_listing_id = :l AND status = 'held'"
        ),
        {"l": listing_id},
    )
