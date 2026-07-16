"""定价引擎纯函数（R2-06 增量 1）——cost_plus 公式 / 解析防御 / min_price 守护。

保真母本：erpAPI/auto_listing/pricing.py（逐函数对照，源行号见各函数 docstring）；
口径裁定：.agent/evidence/R2-06/archaeology.md——
- BR-PR-002 成本价 current_price 优先（2026-05-14 校正，buybox 优先为移植失真不带）
- min_price 硬底线必填 fail-closed（001/06:173，考古口径 8）
- BR-PR-006 价差 < $0.01 跳过；BR-PR-008 30% 变动需 force 确认（考古口径 9 参数化）

本模块只做纯计算：无 IO / 无 DB / 无配置读取。区间倍数、min_price 等业务参数一律由
调用方（service 层）从 pricing_strategy.params（配置中心口径，D-Q11/23）传入。
计算明细（PriceResult.detail）进 price_history.detail，须 JSON 可序列化（001/06:179 契约）。
"""

import re
from dataclasses import dataclass, field
from typing import Any

# 'N/A' 类：无法计价（源 pricing.py:64）
_NA_TOKENS = frozenset({"N/A", "NO FEATURED OFFER", "NONE", "NULL"})
# 'Free'/免运类：运费为 0（源 pricing.py:66；中文文案为防御性扩展）
_FREE_TOKENS = frozenset({"FREE", "FREE SHIPPING", "免运", "免运费", "包邮"})
_MONEY_RE = re.compile(r"(\d+(?:\.\d+)?)")
_BAND_ARITY = 3  # 区间条目 = [low, high, multiplier]
_EPS = 1e-9  # 金额级浮点误差补偿（19.99-19.98 二进制下 ≈ 0.00999…）


@dataclass(frozen=True, slots=True)
class PriceResult:
    """定价结果。detail 进 price_history.detail（可追溯，001/06:179 契约）。

    - ok=True：price 为出价（round 2 位），detail 含 band/multiplier/total/formula；
    - ok=False：不出价，price=None，reason ∈
      {out_of_band, below_min_price, min_price_required, no_bands}。
      no_bands 为防御项：该履约类型无任何可解析区间（母本「没有 FBA/FBM 倍数配置」语义）。
    """

    ok: bool
    price: float | None = None
    reason: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


def parse_multiplier(value: object) -> float | None:
    """解析区间倍数值。保真 pricing.py:31-54。

    ⭐ 2026-06-11 事故修复语义：定价表倍数列是百分比格式，+cells-get 返回格式化显示值
    '275%' → 旧代码 float('275%') 抛 ValueError 被静默跳过 → 所有店「没有 FBA/FBM
    倍数配置」整批误淘汰。本函数对不可解析值一律返回 None（调用方跳过该段），绝不抛异常。

    支持：2.75（数字）/ '2.75' / '275%' / '1,000%'（千分位防御）→ 倍数 float。
    """
    if value is None or value == "":
        return None
    if isinstance(value, int | float):
        return float(value)
    s = str(value).strip().replace(",", "")
    if not s:
        return None
    is_pct = s.endswith("%")
    if is_pct:
        s = s[:-1].strip()
    try:
        f = float(s)
    except ValueError:
        return None
    return f / 100.0 if is_pct else f


def parse_money(value: object) -> float | None:
    """解析金额值。保真 pricing.py:57-69。

    '$25.99' / '25.99' / 25.99 → float；'Free'/'Free Shipping'（免运类）→ 0.0；
    'N/A'/'No Featured Offer'/'None'/空 → None（无法计价）；千分位 '1,299.99' 防御。
    """
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    s = str(value).strip()
    if not s or s.upper() in _NA_TOKENS:
        return None
    if s.upper() in _FREE_TOKENS:
        return 0.0
    m = _MONEY_RE.search(s.replace(",", ""))
    return float(m.group(1)) if m else None


def source_total(price_snapshot: dict[str, Any]) -> float | None:
    """成本总价 = 源商品价 + 运费；无法计算返回 None。保真 pricing.py:72-84。

    兼容两种 price_snapshot 形态：
    - 新系统现状（全库仅 'list' 键）：'list' 直接用，视作已含运费的源价；
    - 旧仓 DMIT 形态：current_price 优先，缺（None）才用 buybox_price
      （BR-PR-002，2026-05-14 校正）+ buybox_shipping（缺视 0 免运）。

    与母本差异：优先级判定用显式 is None，不带 `a or b` 的「数字 0 误判空」旧债
    （current_price=0 时不再错误回落到 buybox）。
    """
    if "list" in price_snapshot:
        listed = parse_money(price_snapshot.get("list"))
        if listed is not None:
            return round(listed, 2)
    price = parse_money(price_snapshot.get("current_price"))
    if price is None:
        price = parse_money(price_snapshot.get("buybox_price"))
    if price is None:
        return None
    shipping = parse_money(price_snapshot.get("buybox_shipping"))
    if shipping is None:
        shipping = 0.0  # 没显示运费，视作免运（源 pricing.py:83）
    return round(price + shipping, 2)


def _normalized_bands(params: dict[str, Any], fulfillment: str) -> list[tuple[float, float, float]]:
    """从 params.bands 取该履约类型的区间表，逐条防御解析。

    条目 = [low, high, multiplier]，任意段数；倍数经 parse_multiplier（'275%' 亦可），
    不可解析的条目跳过不抛异常（2026-06-11 事故语义，同母本 load_pricing_rules:213-216）。
    """
    groups = params.get("bands")
    if not isinstance(groups, dict):
        return []
    entries = groups.get(fulfillment)
    if not isinstance(entries, list | tuple):
        return []
    bands: list[tuple[float, float, float]] = []
    for entry in entries:
        if not isinstance(entry, list | tuple) or len(entry) != _BAND_ARITY:
            continue
        low, high = parse_money(entry[0]), parse_money(entry[1])
        mul = parse_multiplier(entry[2])
        if low is None or high is None or mul is None:
            continue
        bands.append((low, high, mul))
    return bands


def _cost_plus_detail(
    *, total: float, fulfillment: str, low: float, high: float, mul: float, price: float
) -> dict[str, Any]:
    """cost_plus 成功出价的计算明细（进 price_history.detail，公式串同母本 :247）。"""
    return {
        "algo": "cost_plus",
        "total": total,
        "fulfillment": fulfillment,
        "band": [low, high],
        "multiplier": mul,
        "price": price,
        "formula": f"{fulfillment} ${total:.2f} × {mul} = ${price:.2f} (band {low}-{high})",
    }


def compute_price(total: float, *, fulfillment: str, params: dict[str, Any]) -> PriceResult:
    """cost_plus 核心：查区间表出价——上架/改价链唯一入口。保真 pricing.py:221-250。

    公式：low <= total < high（低含高不含）→ price = round(total × multiplier, 2)。
    params 形如 {"bands": {"FBA": [[0,30,2.75],…], "FBM": […]}, "min_price": 6.99}
    （区间数组任意段数，倍数经 parse_multiplier 防御解析）。

    不出价（fail-closed，ok=False / price=None）：
    - 区间外 → reason='out_of_band'（上架链硬边界，母本 :249-250）；
    - 算出价 < min_price → reason='below_min_price'（考古口径 8，detail 保留被拒价格）；
    - params 缺 min_price → reason='min_price_required'（001/06:173 硬底线必填）；
    - 该履约类型无可解析区间 → reason='no_bands'（母本「没有倍数配置」语义）。
    """
    ftype = fulfillment.strip().upper()
    min_price = parse_money(params.get("min_price"))
    if min_price is None:
        return PriceResult(
            ok=False,
            reason="min_price_required",
            detail={"algo": "cost_plus", "total": total, "fulfillment": ftype},
        )
    bands = _normalized_bands(params, ftype)
    if not bands:
        return PriceResult(
            ok=False,
            reason="no_bands",
            detail={"algo": "cost_plus", "total": total, "fulfillment": ftype},
        )
    for low, high, mul in bands:
        if low <= total < high:
            price = round(total * mul, 2)
            detail = _cost_plus_detail(
                total=total, fulfillment=ftype, low=low, high=high, mul=mul, price=price
            )
            detail["min_price"] = min_price
            if price < min_price:
                return PriceResult(ok=False, reason="below_min_price", detail=detail)
            return PriceResult(ok=True, price=price, detail=detail)
    band_str = " / ".join(f"{low}-{high}" for low, high, _ in bands)
    return PriceResult(
        ok=False,
        reason="out_of_band",
        detail={
            "algo": "cost_plus",
            "total": total,
            "fulfillment": ftype,
            "bands": [[low, high] for low, high, _ in bands],
            "note": f"总价 ${total:.2f} 不在 {ftype} 区间 ({band_str})",
        },
    )


def compute_price_clamped(total: float, *, fulfillment: str, params: dict[str, Any]) -> PriceResult:
    """compute_price 的 clamp 版：区间外用最近区间倍数照常出价。保真 pricing.py:253-302。

    ⭐ 母本「展示与上架分离」双函数分工：本函数仅供预览/展示（重定价预览、在线产品
    总表类页面）；上架与改价链禁用本函数，必须走 compute_price()（区间外仍不出价）。

    - 区间内 → 同 compute_price，detail.out_of_band=False；
    - 低于最低区间下界 → 用最低区间倍数，detail.out_of_band=True / clamp='low'；
    - 其余区间外（≥ 最高区间上界；区间空隙同母本归入此支）→ 用最高区间倍数，clamp='high'；
    - 无可解析区间 → 不出价 reason='no_bands'（clamp 无从谈起）。
    min_price 在本函数不拦截出价（展示用），仅在 detail.below_min_price 打标供前端提示。
    """
    ftype = fulfillment.strip().upper()
    bands = _normalized_bands(params, ftype)
    if not bands:
        return PriceResult(
            ok=False,
            reason="no_bands",
            detail={"algo": "cost_plus", "total": total, "fulfillment": ftype},
        )
    min_price = parse_money(params.get("min_price"))
    for low, high, mul in bands:
        if low <= total < high:
            price = round(total * mul, 2)
            detail = _cost_plus_detail(
                total=total, fulfillment=ftype, low=low, high=high, mul=mul, price=price
            )
            detail["out_of_band"] = False
            _flag_below_min(detail, price=price, min_price=min_price)
            return PriceResult(ok=True, price=price, detail=detail)
    ordered = sorted(bands, key=lambda b: b[0])
    if total < ordered[0][0]:
        low, high, mul = ordered[0]
        clamp = "low"
        note = f"区间外(低): {ftype} ${total:.2f} < ${ordered[0][0]:.2f} → 用 {low}-{high} × {mul}"
    else:
        low, high, mul = ordered[-1]
        clamp = "high"
        note = f"区间外(高): {ftype} ${total:.2f} ≥ ${ordered[-1][1]:.2f} → 用 {low}-{high} × {mul}"
    price = round(total * mul, 2)
    detail = _cost_plus_detail(
        total=total, fulfillment=ftype, low=low, high=high, mul=mul, price=price
    )
    detail["out_of_band"] = True
    detail["clamp"] = clamp
    detail["formula"] = f"{note} = ${price:.2f}"
    _flag_below_min(detail, price=price, min_price=min_price)
    return PriceResult(ok=True, price=price, detail=detail)


def _flag_below_min(detail: dict[str, Any], *, price: float, min_price: float | None) -> None:
    """展示链的 min_price 打标（不拦截出价，仅供前端标黄提示）。"""
    if min_price is not None:
        detail["min_price"] = min_price
        detail["below_min_price"] = price < min_price


def price_changed(old: float | None, new: float, *, threshold: float = 0.01) -> bool:
    """BR-PR-006：价差 < threshold（默认 $0.01）视为未变，同步链跳过省配额。

    源仓 sync_price_inventory.py 语义。old=None（无历史价，首次定价）视为变化。
    ±1e-9 补偿浮点误差：10.01-10.00 二进制下可能算出 0.00999…，不能因此误判未变。
    """
    if old is None:
        return True
    return abs(new - old) + _EPS >= threshold


def exceeds_confirm_threshold(old: float | None, new: float, *, pct: float = 0.30) -> bool:
    """BR-PR-008（考古口径 9 参数化落地）：改价幅度超过 pct（默认 30%）需 force 二次确认。

    保真 erp-core listings.py:855-874 语义。old 为 None/0（首次定价）返回 False——
    不需确认。严格「超过」：恰好 30% 不触发（浮点误差 ±1e-9 内按相等处理）。
    """
    if old is None or old <= 0:
        return False
    return abs(new - old) / old > pct + _EPS


def guard_manual_price(price: float, params: dict[str, Any]) -> PriceResult:
    """match/manual 模式守护：人工指定价不经 compute_price，只过 min_price 硬底线。

    algo_code='manual'（D-Q23 match 现行 = 人工指定价）：价格由运营给定，引擎不改写
    数值（仅 round 2 位），但 min_price fail-closed 口径与 cost_plus 完全一致：
    缺 min_price → 'min_price_required'；price < min_price → 'below_min_price'。
    """
    p = round(price, 2)
    detail: dict[str, Any] = {"algo": "manual", "price": p}
    min_price = parse_money(params.get("min_price"))
    if min_price is None:
        return PriceResult(ok=False, reason="min_price_required", detail=detail)
    detail["min_price"] = min_price
    if p < min_price:
        return PriceResult(ok=False, reason="below_min_price", detail=detail)
    detail["formula"] = f"manual ${p:.2f}（人工指定价，过 min_price 守护）"
    return PriceResult(ok=True, price=p, detail=detail)
