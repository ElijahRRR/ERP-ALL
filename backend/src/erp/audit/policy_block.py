"""L3 静态 37 政策 prompt 块加载器（版本失效内存缓存）。

源仓 l3_llm._format_full_policy_block 语义：37 条政策全清单静态拼在 L3 system prompt
末尾，所有产品共享同一份 → 吃 provider prefix cache（DeepSeek 前缀完全匹配），命中率
~63%→~95%。**绝不能按品类动态拼**（动态段=每产品 system 不同=cache miss=成本爆炸）。

空表 → 返回空块 + 静态候选类目，L3 退回单策略行为，不报错（数据待 Owner 导）。
版本键 = count + max(updated_at)，政策一变缓存自动重建（与 blacklist_index 同款）。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 通用两类始终在候选（源仓 _ALWAYS_INCLUDE）；其余 = 政策表 category_en
_STATIC_CATEGORIES = {"intellectual property", "brand_misuse", "none"}

# 每条政策压缩上限（源仓 ~240 字；37 条 ≈ 5500 字 ≈ 4-5K token，可控）
_PROHIBITED_MAX = 240

# 缓存：(version, block, categories)。单进程 asyncio，重复重建无害，绝不返回过期
_CACHE: dict[str, Any] = {"version": None, "block": "", "categories": _STATIC_CATEGORIES}


async def _version(session: AsyncSession) -> str:
    val = (
        await session.execute(
            text(
                "SELECT count(*)::text || ':' || COALESCE(max(updated_at)::text, '')"
                " FROM refdata.prohibited_policy"
            )
        )
    ).scalar_one()
    return str(val)


def _summarize(text_val: str | None, max_chars: int = _PROHIBITED_MAX) -> str:
    """prohibited_items 二次摘要：按 • / 换行拆条，取前若干条到字数上限（源仓同款）。"""
    if not text_val:
        return ""
    cleaned = text_val.replace("\r\n", "\n").replace("\r", "\n")
    bullets = [b.strip() for b in cleaned.replace("•", "\n").split("\n") if b.strip()]
    if not bullets:
        return text_val[:max_chars]
    out: list[str] = []
    total = 0
    for b in bullets:
        one = b[:80]
        if total + len(one) + 3 > max_chars:
            if not out:
                out.append(one[: max_chars - 3])
            break
        out.append(one)
        total += len(one) + 3
    return " / ".join(out)


async def _rebuild(session: AsyncSession) -> tuple[str, set[str]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT category_en, category_zh, overall_status, prohibited_items,"
                    " zh_seller_risk FROM refdata.prohibited_policy"
                    " ORDER BY seq NULLS LAST, category_en"
                )
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        return "", set(_STATIC_CATEGORIES)
    lines = ["# 沃尔玛禁售政策全清单（判 reject 时与下列做语义匹配，reason_category 取命中项）"]
    categories = set(_STATIC_CATEGORIES)
    for r in rows:
        cat_en = r["category_en"]
        categories.add(cat_en.lower())
        header = f"## {cat_en}"
        if r["category_zh"]:
            header += f"（{r['category_zh']}）"
        if r["zh_seller_risk"]:
            header += f" — 风险:{r['zh_seller_risk']}"
        lines.append(header)
        if r["overall_status"]:
            lines.append(f"状态: {str(r['overall_status'])[:50]}")
        summary = _summarize(r["prohibited_items"])
        if summary:
            lines.append(f"禁: {summary}")
        lines.append("")
    return "\n".join(lines), categories


async def _ensure(session: AsyncSession) -> None:
    version = await _version(session)
    if _CACHE["version"] != version:
        block, categories = await _rebuild(session)
        _CACHE.update(version=version, block=block, categories=categories)


async def load_policy_block(session: AsyncSession) -> str:
    """37 条政策静态块（拼 L3 system prompt 末尾）；空表 → 空串。"""
    await _ensure(session)
    return str(_CACHE["block"])


async def valid_reason_categories(session: AsyncSession) -> set[str]:
    """L3 reason_category 合法候选（小写）= 静态两类 + 政策表 category_en；空表 → 静态。"""
    await _ensure(session)
    cats: set[str] = _CACHE["categories"]
    return set(cats)


def clear_cache() -> None:
    """测试隔离用：清缓存强制下次重建。"""
    _CACHE.update(version=None, block="", categories=_STATIC_CATEGORIES)
