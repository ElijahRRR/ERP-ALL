"""L1 类目判定（001 §03 category_map / §05 audit / D-Q55）。

**L1-a（同步核心，本模块）**：映射表【直判】gate——product 的 Amazon 类目在
`refdata.category_map` 精确命中（INNER JOIN `refdata.pt_meta` 滤废弃 PT）：
- 存在「非禁做的有效 WPT」→ **pass**（0 LLM，带出 resolved wpt 供上架/L2 Nice 复用）
- 命中但候选【全部】zh_seller_forbidden（map 或 pt 维度）→ **reject**（reject_level=l1）
- 无直判命中 / 无类目信息 → **needs_review**（fail-closed，待 L1-b 复排或人工）

**L1-b（后置增强，另单）**：无直判命中时召回候选 + LLM 语义复排（D-Q55，非嵌入），
把本增量落到 needs_review 的无直判品在事务外复排定夺（RS-03a 同款分段结构）。

不变量：`INNER JOIN pt_meta` 是硬约束——category_map 里残留已下线 PT，直接用会
映射到不可上架类目（源仓 2026-05-09 教训）。`'无对应Walmart PT'` 为合法 unmapped
标记，排除。sellability = 存在任一非禁做有效候选（一个可售通道即算可售）。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# category_map 里 unmapped 的合法业务标记（非真实 PT，直判需排除）
_UNMAPPED_MARKER = "无对应Walmart PT"

# 直判：product 的 category_path / amazon_leaf_id 精确命中 category_map（路径列或叶子列），
# INNER JOIN pt_meta 滤废弃 PT，排除 unmapped 标记。rank_no 升序=PT 内候选优先级。
_DIRECT_SQL = text(
    "SELECT cm.walmart_product_type AS wpt, cm.confidence,"
    "       cm.zh_seller_forbidden AS map_forbidden, cm.requires_certificate,"
    "       cm.rank_no, cm.match_type,"
    "       pm.zh_seller_forbidden AS pt_forbidden, pm.walmart_category, pm.access_state"
    " FROM refdata.category_map cm"
    " JOIN refdata.pt_meta pm ON pm.walmart_product_type = cm.walmart_product_type"
    " WHERE (cm.amazon_category = ANY(:keys) OR cm.amazon_leaf = ANY(:keys))"
    "   AND cm.walmart_product_type <> :unmapped"
    " ORDER BY cm.rank_no NULLS LAST, cm.walmart_product_type"
)


async def run_l1(session: AsyncSession, product: dict[str, Any]) -> dict[str, Any]:
    """L1-a 同步类目 gate。→ {verdict, wpt, rule_code, evidence}。

    verdict ∈ {pass, reject, needs_review}；reject 时 service 置 reject_level=l1。
    wpt 仅 pass 时非空（resolved Walmart Product Type，供后续上架/L2 复用）。
    """
    keys = [
        str(k)
        for k in (product.get("category_path"), product.get("amazon_leaf_id"))
        if k is not None and str(k).strip()
    ]
    if not keys:
        return {
            "verdict": "needs_review",
            "wpt": None,
            "rule_code": "l1_no_category",
            "evidence": {"detail": "产品无 category_path/amazon_leaf_id，无法判定类目"},
        }

    rows = (
        (await session.execute(_DIRECT_SQL, {"keys": keys, "unmapped": _UNMAPPED_MARKER}))
        .mappings()
        .all()
    )
    if not rows:
        return {
            "verdict": "needs_review",
            "wpt": None,
            "rule_code": "l1_unmapped",
            "evidence": {"keys": keys, "detail": "category_map 无直判命中，待 L1-b 复排/人工"},
        }

    sellable = [r for r in rows if not r["map_forbidden"] and not r["pt_forbidden"]]
    if sellable:
        best = sellable[0]
        return {
            "verdict": "pass",
            "wpt": best["wpt"],
            "rule_code": "l1_category_mapped",
            "evidence": {
                "wpt": best["wpt"],
                "walmart_category": best["walmart_category"],
                "confidence": best["confidence"],
                "match_type": best["match_type"],
                "requires_certificate": best["requires_certificate"],
                "access_state": best["access_state"],
                "candidates": len(rows),
                "sellable": len(sellable),
            },
        }

    # 命中但候选全部禁做 → 该类目对中国搬运卖家不可售
    return {
        "verdict": "reject",
        "wpt": None,
        "rule_code": "l1_category_forbidden",
        "evidence": {
            "keys": keys,
            "forbidden_wpts": [r["wpt"] for r in rows][:10],
            "detail": "映射命中类目候选全部对中国搬运卖家禁做",
        },
    }
