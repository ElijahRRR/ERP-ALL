"""L3 静态 37 政策 prompt 块加载器（版本失效内存缓存）。

源仓 l3_llm._format_full_policy_block 语义：37 条政策全清单静态拼在 L3 system prompt
末尾，所有产品共享同一份 → 吃 provider prefix cache（DeepSeek 前缀完全匹配），命中率
~63%→~95%。**绝不能按品类动态拼**（动态段=每产品 system 不同=cache miss=成本爆炸）。

空表 → 返回空块 + 静态候选类目，L3 退回单策略行为，不报错（数据待 Owner 导）。
版本键 = refdata.dataset_revision('prohibited_policy')（0014 触发器事务内递增，任何
写入方都 bump），政策一变缓存自动重建（与 blacklist_index 同款）。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# 通用两类始终在候选（源仓 _ALWAYS_INCLUDE）；其余 = 政策表 category_en
_STATIC_CATEGORIES = {"intellectual property", "brand_misuse", "none"}
_STATIC_DISPLAY = ["Intellectual Property", "brand_misuse", "none"]

# 缓存：(version, block, categories, display)。单进程 asyncio，重复重建无害，绝不返回过期
_CACHE: dict[str, Any] = {
    "version": None,
    "block": "",
    "categories": _STATIC_CATEGORIES,
    "display": _STATIC_DISPLAY,
}


async def _version(session: AsyncSession) -> str:
    # dataset_revision('prohibited_policy')：0014 统计触发器事务内递增，任何写入方都 bump
    # （替代 count+max(updated_at)，评审 round-1 B5② 采纳）
    val = (
        await session.execute(
            text(
                "SELECT COALESCE((SELECT revision FROM refdata.dataset_revision"
                " WHERE dataset = 'prohibited_policy'), 0)::text"
            )
        )
    ).scalar_one()
    return str(val)


async def _rebuild(session: AsyncSession) -> tuple[str, set[str], list[str]]:
    """→ (政策块, 合法类目小写集, 候选类目显示序列)。块格式=源仓
    _format_full_policy_block 逐字（round-8 对齐）：## {seq}. {en} ({zh}) /
    状态|中国卖家 / 禁 / 高风险备注。块顶标题由 pipeline.POLICY_BLOCK_HEADER 提供。"""
    rows = (
        (
            await session.execute(
                text(
                    "SELECT seq, category_en, category_zh, overall_status, prohibited_items,"
                    " zh_seller_risk, zh_seller_notes FROM refdata.prohibited_policy"
                    " ORDER BY seq NULLS LAST, category_en"
                )
            )
        )
        .mappings()
        .all()
    )
    if not rows:
        return "", set(_STATIC_CATEGORIES), list(_STATIC_DISPLAY)
    parts: list[str] = []
    categories = set(_STATIC_CATEGORIES)
    display: list[str] = []
    for i, r in enumerate(rows, start=1):
        cat_en = (r["category_en"] or "").strip()
        cat_zh = (r["category_zh"] or "").strip()
        status = (r["overall_status"] or "").strip().replace("\n", " ")[:50]
        risk = (r["zh_seller_risk"] or "").strip().replace("\n", " ")[:30]
        prohib = (r["prohibited_items"] or "").replace("\n", " ").replace("•", "/").strip()[:240]
        notes = (r["zh_seller_notes"] or "").replace("\n", " ").strip()[:80]
        categories.add(cat_en.lower())
        display.append(cat_en)

        line = f"## {r['seq'] or i}. {cat_en} ({cat_zh})"
        if status:
            line += f"\n状态: {status}"
            if risk:
                line += f" | 中国卖家:{risk}"
        if prohib:
            line += f"\n禁: {prohib}"
        if notes and "红" in risk:  # 仅高风险政策保留卖家备注（源仓：节省 token）
            line += f"\n备注: {notes}"
        parts.append(line)
    display += ["brand_misuse", "none"]
    return "\n\n".join(parts), categories, display


async def _ensure(session: AsyncSession) -> None:
    version = await _version(session)
    if _CACHE["version"] != version:
        block, categories, display = await _rebuild(session)
        _CACHE.update(version=version, block=block, categories=categories, display=display)


async def load_policy_block(session: AsyncSession) -> str:
    """37 条政策静态块（拼 L3 system prompt 末尾）；空表 → 空串。"""
    await _ensure(session)
    return str(_CACHE["block"])


async def reason_categories_block(session: AsyncSession) -> str:
    """候选 reason_category 显示清单（源仓 _get_full_reason_categories 格式：
    '  - Cat' 每行；政策表 category_en 按 seq 序 + brand_misuse + none）。"""
    await _ensure(session)
    display: list[str] = _CACHE["display"]
    return "\n".join(f"  - {c}" for c in display)


async def valid_reason_categories(session: AsyncSession) -> set[str]:
    """L3 reason_category 合法候选（小写）= 静态两类 + 政策表 category_en；空表 → 静态。"""
    await _ensure(session)
    cats: set[str] = _CACHE["categories"]
    return set(cats)


def clear_cache() -> None:
    """测试隔离用：清缓存强制下次重建。"""
    _CACHE.update(version=None, block="", categories=_STATIC_CATEGORIES, display=_STATIC_DISPLAY)
