"""定价域服务层（R2-06 增量2）：策略解析 / 价格历史 / 单品出价。

- resolve_strategy：active 策略解析，store 级覆盖 > team 级默认（D-Q23；
  0027 活跃唯一索引保证每级至多一条）；
- record_price_history：app.price_history 唯一写入口（reason ∈
  strategy/manual/watchdog/initial，0027 CHECK 同步）；
- price_product / preview_product：engine 纯函数的产品级组装——上架/改价链走
  price_product（compute_price 严格版，区间外不出价 fail-closed，BR-PR-004）；
  预览链走 preview_product（compute_price_clamped 展示版 + 严格判定并列返回）。

业务参数（区间/min_price）一律来自 pricing_strategy.params；30% 确认阈值来自
配置中心 pricing.confirm_threshold_pct（team > system > 默认 0.30，考古口径 9）。
"""

import json
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.pricing import engine
from erp.pricing.engine import PriceResult

CONFIRM_THRESHOLD_KEY = "pricing.confirm_threshold_pct"
DEFAULT_CONFIRM_THRESHOLD = 0.30

# allocate 拒绝口径（rejected.code = 'PRICING_' + reason.upper()）的中文说明
REJECT_MESSAGES = {
    "out_of_band": "成本总价不在策略区间内，不出价（BR-PR-004 区间外不上架）",
    "below_min_price": "算出价低于策略 min_price 硬底线，不出价",
    "min_price_required": "策略缺 min_price 硬底线（fail-closed 不出价）",
    "no_source_price": "产品无可计价源价（price_snapshot 缺失或无法解析）",
    "no_bands": "策略无该履约类型的可解析区间，不出价",
    "manual_price_required": "manual 策略不自动出价，需人工经改价入口给价",
}


async def resolve_strategy(
    session: AsyncSession, *, team_id: int, store_id: int, offer_mode: str
) -> dict[str, Any] | None:
    """解析 (team×store×offer_mode) 的生效策略：store 级优先，缺则回落 team 级。"""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, store_id, offer_mode, name, algo_code, params, version"
                    " FROM app.pricing_strategy"
                    " WHERE team_id = :t AND offer_mode = :m AND status = 'active'"
                    "   AND (store_id = :s OR store_id IS NULL)"
                    " ORDER BY store_id NULLS LAST LIMIT 1"
                ),
                {"t": team_id, "m": offer_mode, "s": store_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    return dict(row) if row is not None else None


async def record_price_history(
    session: AsyncSession,
    *,
    listing_id: int,
    team_id: int,
    old_price: float | None,
    new_price: float,
    reason: str,
    strategy: Mapping[str, Any] | None = None,
    detail: dict[str, Any] | None = None,
    actor_id: int | None = None,
) -> None:
    """价格变更史唯一写入口（0027 月分区表；strategy_id/version 供计算明细回溯）。"""
    await session.execute(
        text(
            "INSERT INTO app.price_history"
            " (listing_id, team_id, old_price, new_price, reason,"
            "  strategy_id, strategy_version, detail, created_by)"
            " VALUES (:l, :t, :o, :n, :r, :sid, :sv, cast(:d AS jsonb), :by)"
        ),
        {
            "l": listing_id,
            "t": team_id,
            "o": old_price,
            "n": new_price,
            "r": reason,
            "sid": strategy["id"] if strategy else None,
            "sv": strategy["version"] if strategy else None,
            "d": json.dumps(detail or {}, ensure_ascii=False),
            "by": actor_id,
        },
    )


async def confirm_threshold(session: AsyncSession, team_id: int) -> float:
    """30% 确认阈值（BR-PR-008 参数化）：team_config > system_config > 默认 0.30。

    经请求会话直读（GUC 已就位，RLS 生效）——ConfigService 自管会话无团队上下文，
    读不到 team 级覆盖，故不经它。
    """
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
            {"t": team_id, "k": CONFIRM_THRESHOLD_KEY},
        )
    ).scalar_one_or_none()
    return float(raw) if raw is not None else DEFAULT_CONFIRM_THRESHOLD


def _fulfillment(product: Mapping[Any, Any], params: dict[str, Any]) -> str:
    """履约类型判定：attrs.fulfillment / fulfillment_type 含 'FBA' 判 FBA，
    否则取策略 params.default_fulfillment（缺省 FBM——自发货为常态）。"""
    attrs = product.get("attrs") or {}
    if isinstance(attrs, dict):
        raw = str(attrs.get("fulfillment") or attrs.get("fulfillment_type") or "")
        if "FBA" in raw.upper():
            return "FBA"
    return str(params.get("default_fulfillment", "FBM"))


def price_product(strategy: Mapping[str, Any], product: Mapping[Any, Any]) -> PriceResult:
    """按策略给单个产品出价（上架/改价链：compute_price 严格版，区间外不出价）。

    - manual 策略不自动出价（D-Q23 match 现行=人工指定价）→ manual_price_required；
    - 源价无法解析（price_snapshot 缺失/N/A）→ no_source_price。
    """
    if strategy["algo_code"] == "manual":
        return PriceResult(ok=False, reason="manual_price_required", detail={"algo": "manual"})
    params: dict[str, Any] = strategy["params"] or {}
    snap = product.get("price_snapshot") or {}
    total = engine.source_total(snap) if isinstance(snap, dict) else None
    if total is None:
        return PriceResult(ok=False, reason="no_source_price", detail={"algo": "cost_plus"})
    return engine.compute_price(total, fulfillment=_fulfillment(product, params), params=params)


def preview_product(strategy: Mapping[str, Any], product: Mapping[Any, Any]) -> dict[str, Any]:
    """预览试算（只读展示链）：clamp 版出价 + compute_price 严格判定并列返回。

    new_price/detail 取 compute_price_clamped（区间外仍给参考价，detail 带
    out_of_band/clamp 标记）；ok/reason 取 compute_price 严格口径——前端据此
    区分「可直接上架」与「仅供参考」。manual 策略 → reason='manual' 不试算。
    """
    if strategy["algo_code"] == "manual":
        return {"ok": False, "reason": "manual", "new_price": None, "detail": {"algo": "manual"}}
    params: dict[str, Any] = strategy["params"] or {}
    snap = product.get("price_snapshot") or {}
    total = engine.source_total(snap) if isinstance(snap, dict) else None
    if total is None:
        return {
            "ok": False,
            "reason": "no_source_price",
            "new_price": None,
            "detail": {"algo": "cost_plus"},
        }
    ftype = _fulfillment(product, params)
    clamped = engine.compute_price_clamped(total, fulfillment=ftype, params=params)
    strict = engine.compute_price(total, fulfillment=ftype, params=params)
    return {
        "ok": strict.ok,
        "reason": strict.reason,
        "new_price": clamped.price,
        "detail": clamped.detail,
    }
