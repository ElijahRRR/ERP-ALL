"""erp.pricing —— 定价域（R2-06）。

engine：纯函数定价引擎（cost_plus / manual 守护 / 阈值判定），无 IO 无 DB；
service / router 随后续增量加入。业务参数（区间倍数、min_price、阈值）一律
由调用方从 pricing_strategy.params / 配置中心传入，本包不写死。
"""

from erp.pricing.engine import (
    PriceResult,
    compute_price,
    compute_price_clamped,
    exceeds_confirm_threshold,
    guard_manual_price,
    parse_money,
    parse_multiplier,
    price_changed,
    source_total,
)

__all__ = [
    "PriceResult",
    "compute_price",
    "compute_price_clamped",
    "exceeds_confirm_threshold",
    "guard_manual_price",
    "parse_money",
    "parse_multiplier",
    "price_changed",
    "source_total",
]
