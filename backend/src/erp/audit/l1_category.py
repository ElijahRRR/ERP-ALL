"""L1 类目判定（001 §03 category_map / §05 audit / D-Q55）。

**L1-a（同步核心，本模块）**：映射表【直判】gate——product 的 Amazon 类目在
`refdata.category_map` 精确命中（INNER JOIN `refdata.pt_meta` 滤废弃 PT）：
- 存在「通过全部类目硬拒谓词的 WPT」→ **pass**（0 LLM，带出 resolved wpt）
- 命中但候选【全部】被类目硬拒 → **reject**（reject_level=l1）。硬拒谓词 =
  zh_seller_forbidden（map/pt）+ 源仓 L2 R0/R1/R3a 保真移植（R2-02 对照审查补齐，
  源仓 R1 gate 实测拒 1223/7008 PT——此前未移植是 round-2 reject->pass 45 的主因）：
  R0 八大 walmart_category 硬禁 / R1 access_state+zh_can_do 双白名单 /
  R3a requirements 硬认证关键词
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

from erp.audit import nrtl, zh_forbidden

# category_map 里 unmapped 的合法业务标记（非真实 PT，直判需排除）
_UNMAPPED_MARKER = "无对应Walmart PT"

# ── 类目硬拒谓词（源仓 l2_rules R0/R1/R3a 保真移植，R2-02 对照审查补齐）──
# 源仓这三条在 L2 执行（吃 L1 判出的 PT）；本系统 L1-a 直判时已拿到 pt_meta 行，
# 就地判定（reject_level=l1）。rule_code 保留源仓名，报表/对拍口径对齐。

# R1 类目准入 gate（源仓实测 1223/7008 PT 被拒）：双白名单，任一不满足即拒
_ACCESS_WHITELIST = {"普通商品", "附条件允许"}

# R0 中国搬运卖家全类目硬禁（源仓 2026-04-28 用户指定，8 大 walmart_category）
_MEGA_FORBIDDEN_CATEGORIES = {
    "Vehicles", "Automotive", "Electronics", "Fashion",
    "Food & Beverage", "Health & Personal Care", "Beauty", "Baby",
}  # fmt: skip

# R3a 硬合规关键词（pt_meta.requirements 含任一 → 需实验室证书/官方注册，搬运做不了）
_HARD_CERT_KWS = (
    "510(k)", "mocra", "fifra", "cpsia", "aafco", "nrtl",
    "fda", "epa", "cpc", "gcc", "nsf", "atf", "dea", "fcc", "etl", "csa", "ul",
)  # fmt: skip

# 直判：product 的 category_path / amazon_leaf_id 精确命中 category_map
# （路径列 / 叶子名列 / browse node ID 列），INNER JOIN pt_meta 滤废弃 PT，
# 排除 unmapped 标记。rank_no 升序=PT 内候选优先级。
_DIRECT_SQL = text(
    "SELECT cm.walmart_product_type AS wpt, cm.confidence,"
    "       cm.zh_seller_forbidden AS map_forbidden, cm.requires_certificate,"
    "       cm.rank_no, cm.match_type,"
    "       pm.zh_seller_forbidden AS pt_forbidden, pm.walmart_category, pm.access_state,"
    "       pm.zh_can_do, pm.requirements,"
    "       COALESCE(ps.has_real_cert, false) AS has_real_cert"
    " FROM refdata.category_map cm"
    " JOIN refdata.pt_meta pm ON pm.walmart_product_type = cm.walmart_product_type"
    " LEFT JOIN refdata.pt_spec ps ON ps.walmart_product_type = cm.walmart_product_type"
    " WHERE (cm.amazon_category = ANY(:keys) OR cm.amazon_leaf = ANY(:keys)"
    "        OR cm.browse_node_id = ANY(:keys))"
    "   AND cm.walmart_product_type <> :unmapped"
    " ORDER BY cm.rank_no NULLS LAST, cm.walmart_product_type"
)


def candidate_block_reason(row: dict[str, Any]) -> str | None:  # noqa: PLR0911 谓词链逐条返回
    """单候选 PT 的类目硬拒原因（None=可售）。源仓 R0→R1→R3a 顺序保真。

    - l1_category_forbidden        map/pt 任一维度 zh_seller_forbidden（本系统原有）
    - zh_seller_mega_cat_forbidden R0：walmart_category ∈ 8 大硬禁
    - cat_access_blocked           R1：access_state 不在 {普通商品, 附条件允许}（含空）
    - cat_zh_blocked               R1：zh_can_do 非 '是' 且非 '需评估*'（含空）
    - cat_requires_cert_hard       R3a：requirements 含硬认证关键词
    """
    if row["map_forbidden"] or row["pt_forbidden"]:
        return "l1_category_forbidden"
    if zh_forbidden.check_excluded(category_path=None, title=None, walmart_pt=row["wpt"]):
        return "l1_excluded_category"  # seed excluded：PT 名子串（3C/服饰/汽配…）
    if (row["walmart_category"] or "").strip() in _MEGA_FORBIDDEN_CATEGORIES:
        return "zh_seller_mega_cat_forbidden"
    if (row["access_state"] or "").strip() not in _ACCESS_WHITELIST:
        return "cat_access_blocked"
    zh = (row["zh_can_do"] or "").strip()
    if not (zh == "是" or zh.startswith("需评估")):
        return "cat_zh_blocked"
    if zh_forbidden.match_mega(row["wpt"], row["walmart_category"]):
        return "forbidden_mega_cat"  # R2 seed yaml：18 细粒度禁售大类（词边界）
    req_low = (row["requirements"] or "").lower()
    if req_low and any(kw in req_low for kw in _HARD_CERT_KWS):
        return "cat_requires_cert_hard"
    # R3b（官方 spec 层，源仓第 2 数据源）：has_real_cert 且 PT 为整机 → NRTL 硬拒；
    # 小件降级不拒（源仓 R3b small_part 软证据）。pt_spec 未导入时 COALESCE false 短路。
    if row.get("has_real_cert") and nrtl.classify_nrtl_pt(row["wpt"]) == "whole_unit":
        return "cat_requires_cert_hard"
    return None


async def run_l1(session: AsyncSession, product: dict[str, Any]) -> dict[str, Any]:
    """L1-a 同步类目 gate。→ {verdict, wpt, rule_code, is_hard, evidence}。

    verdict ∈ {pass, reject}；reject 时 service 置 reject_level=l1。
    wpt 仅直判命中可售时非空（resolved Walmart Product Type，供后续上架/L2 复用）。
    unmapped / 无类目 → verdict=pass + 软标记（审核不因缺图阻塞）。
    """
    # seed excluded 预拦截（源仓 L1 _check_seed_excluded）：Amazon 路径/title 子串
    # 命中 3C/服饰/汽配/带电 等 → 直接拒。放最前——无类目/无直判商品也能拦。
    excl = zh_forbidden.check_excluded(
        category_path=product.get("category_path"), title=product.get("title")
    )
    if excl:
        return {
            "verdict": "reject",
            "wpt": None,
            "rule_code": "l1_excluded_category",
            "is_hard": True,
            "evidence": {
                "reason": excl,
                "category_path": product.get("category_path"),
                "detail": "seed excluded 预拦截（源仓 L1 排除规则）",
            },
        }

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

    blocked: list[tuple[dict[str, Any], str]] = []
    sellable: list[dict[str, Any]] = []
    for r in rows:
        reason = candidate_block_reason(dict(r))
        if reason is None:
            sellable.append(dict(r))
        else:
            blocked.append((dict(r), reason))

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

    # 命中但候选全部被类目硬拒（源仓 R0/R1/R3a + zh_seller_forbidden）→ 该类目不可售。
    # rule_code 取首位（rank 最优）候选的拒因；全部候选拒因入 evidence 供报表/对拍。
    return {
        "verdict": "reject",
        "wpt": None,
        "rule_code": blocked[0][1],
        "is_hard": True,
        "evidence": {
            "keys": keys,
            "candidates": [
                {
                    "wpt": r["wpt"],
                    "block": reason,
                    "walmart_category": r["walmart_category"],
                    "access_state": r["access_state"],
                    "zh_can_do": r["zh_can_do"],
                }
                for r, reason in blocked[:10]
            ],
            "detail": "映射命中类目候选全部被类目硬拒（源仓 R0/R1/R3a gate）",
        },
    }
