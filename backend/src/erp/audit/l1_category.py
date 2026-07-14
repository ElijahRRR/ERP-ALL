"""L1 类目判定（001 §03 category_map / §05 audit / D-Q55）。

**L1-a（同步核心，本模块）**：映射表【直判】gate——product 的 Amazon 类目在
`refdata.category_map` 精确命中（INNER JOIN `refdata.pt_meta` 滤废弃 PT）：
- 存在「非禁做的有效 WPT」→ **pass**（0 LLM，带出 resolved wpt 供上架/L2 Nice 复用）
- 命中但候选【全部】zh_seller_forbidden（map 或 pt 维度）→ **reject**（reject_level=l1）
- 无直判命中 / 无类目信息 → **软标记放行**（l1_unmapped / l1_no_category，is_hard=False，
  L2/L3 照跑）。R2-02 对拍 round-1 教训（42%，needs_review 86/200）：类目缺图是
  **数据缺口不是合规检查异常**——A4 fail-closed 适用于"检查本身失败"（LLM 输出非法），
  不适用于"可选补充信息缺失"。旧系统 parity：类目硬拒仅 R1/R2/R3，unmapped 不拦审核，
  L3 二值输出（archaeology:89）。缺 WPT 只阻上架（listing 前置），不阻合规判定。

**L1-b（l1_rerank.py）**：无直判命中的类目 → 祖先召回 + LLM 复排 → 写回 map，
之后 L1-a 直判即覆盖（0 LLM）。

不变量：`INNER JOIN pt_meta` 是硬约束——category_map 里残留已下线 PT，直接用会
映射到不可上架类目（源仓 2026-05-09 教训）。`'无对应Walmart PT'` 为合法 unmapped
标记，排除。sellability = 存在任一非禁做有效候选（一个可售通道即算可售）。
直判键 = category_path / amazon_leaf_id × map 的 amazon_category / amazon_leaf /
browse_node_id（旧系统 groundtruth 的 leaf 多为 browse node 数字 ID，0016 已导入该列）。
"""

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# category_map 里 unmapped 的合法业务标记（非真实 PT，直判需排除）
_UNMAPPED_MARKER = "无对应Walmart PT"

# 直判：product 的 category_path / amazon_leaf_id 精确命中 category_map
# （路径列 / 叶子名列 / browse node ID 列），INNER JOIN pt_meta 滤废弃 PT，
# 排除 unmapped 标记。rank_no 升序=PT 内候选优先级。
_DIRECT_SQL = text(
    "SELECT cm.walmart_product_type AS wpt, cm.confidence,"
    "       cm.zh_seller_forbidden AS map_forbidden, cm.requires_certificate,"
    "       cm.rank_no, cm.match_type,"
    "       pm.zh_seller_forbidden AS pt_forbidden, pm.walmart_category, pm.access_state"
    " FROM refdata.category_map cm"
    " JOIN refdata.pt_meta pm ON pm.walmart_product_type = cm.walmart_product_type"
    " WHERE (cm.amazon_category = ANY(:keys) OR cm.amazon_leaf = ANY(:keys)"
    "        OR cm.browse_node_id = ANY(:keys))"
    "   AND cm.walmart_product_type <> :unmapped"
    " ORDER BY cm.rank_no NULLS LAST, cm.walmart_product_type"
)


async def run_l1(session: AsyncSession, product: dict[str, Any]) -> dict[str, Any]:
    """L1-a 同步类目 gate。→ {verdict, wpt, rule_code, is_hard, evidence}。

    verdict ∈ {pass, reject}；reject 时 service 置 reject_level=l1。
    wpt 仅直判命中可售时非空（resolved Walmart Product Type，供后续上架/L2 复用）。
    unmapped / 无类目 → verdict=pass + 软标记（审核不因缺图阻塞）。
    """
    keys = [
        str(k)
        for k in (product.get("category_path"), product.get("amazon_leaf_id"))
        if k is not None and str(k).strip()
    ]
    if not keys:
        return {
            "verdict": "pass",
            "wpt": None,
            "rule_code": "l1_no_category",
            "is_hard": False,
            "evidence": {"detail": "产品无 category_path/amazon_leaf_id；软标记放行，缺图不阻审核"},
        }

    rows = (
        (await session.execute(_DIRECT_SQL, {"keys": keys, "unmapped": _UNMAPPED_MARKER}))
        .mappings()
        .all()
    )
    if not rows:
        return {
            "verdict": "pass",
            "wpt": None,
            "rule_code": "l1_unmapped",
            "is_hard": False,
            "evidence": {
                "keys": keys,
                "detail": "category_map 无直判命中；软标记放行，缺 WPT 只阻上架不阻审核",
            },
        }

    sellable = [r for r in rows if not r["map_forbidden"] and not r["pt_forbidden"]]
    if sellable:
        best = sellable[0]
        return {
            "verdict": "pass",
            "wpt": best["wpt"],
            "rule_code": "l1_category_mapped",
            "is_hard": False,
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

    # 命中但候选全部禁做 → 该类目对中国搬运卖家不可售（唯一的 L1 硬拒）
    return {
        "verdict": "reject",
        "wpt": None,
        "rule_code": "l1_category_forbidden",
        "is_hard": True,
        "evidence": {
            "keys": keys,
            "forbidden_wpts": [r["wpt"] for r in rows][:10],
            "detail": "映射命中类目候选全部对中国搬运卖家禁做",
        },
    }
