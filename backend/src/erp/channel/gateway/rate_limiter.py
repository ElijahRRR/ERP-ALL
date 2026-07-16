"""GCRA 限流器（异步移植版）。

移植源：erpAPI/erp-core/backend/app/services/rate_limiter.py（考古对照表见
.agent/evidence/R1-07/archaeology.md）。限流表保留源仓实战校正：
**MP_ITEM Feed = 10/hour（不是官方表的 20/min，2026-05-07 实测校正）**。

- 维度：(store, endpoint) 每桶独立 GCRA。
- 自适应：调用后回填 walmart 响应头（x-current-token-count / X-Next-Replenishment-Time），
  配额耗尽时把恢复时间推到 walmart 给的时刻，杜绝提前撞墙。
- 进程内实现；多 worker 部署时换 Redis backend（接口已按此预留，R2 接）。
完整 170 端点官方配额表：docs/walmart_rate_limits.tsv（未列入下表的走 _default）。
"""

import asyncio
import contextlib
import time
from dataclasses import dataclass
from datetime import datetime

import structlog

log = structlog.get_logger()


@dataclass(frozen=True)
class RateLimit:
    limit: int
    period: float  # seconds


WALMART_ENDPOINT_LIMITS: dict[str, RateLimit] = {
    # 高风险（必须严控）
    # R2-06 修正（考古口径3，官方 rate-limiting 页 2026-07-16 在线实抓）：
    # ① 旧键 "feeds:PRICE_AND_PROMOTION" 缺 "POST /v3/feeds:" 前缀，与网关 endpoint_key
    #    口径错配——永远匹配不上、一直落 _default，限额被架空；
    # ② 官方现行限额已从 6/day 放宽为 10/hour（店铺级，与 legacy price feed、
    #    批量促销 feed 三入口共享池）。本地 TSV:125 为过时记载。
    "POST /v3/feeds:PRICE_AND_PROMOTION": RateLimit(10, 3600),
    "PUT /v3/price": RateLimit(100, 3600),
    "GET /v3/items?q": RateLimit(60, 60),
    "GET /v3/items": RateLimit(300, 60),
    "GET /v3/items/catalog/search": RateLimit(200, 60),
    "GET /v3/insights/performance/*": RateLimit(1, 60),
    "GET /v3/feeds": RateLimit(5000, 60),
    "GET /v3/inventory": RateLimit(200, 60),
    "PUT /v3/inventory": RateLimit(200, 60),
    # 实测校正（源仓 2026-05-07）：MP_ITEM 10/hour，非官方表 20/min
    "POST /v3/feeds:MP_ITEM": RateLimit(10, 3600),
    "POST /v3/feeds:MP_ITEM_MATCH": RateLimit(10, 3600),
    "POST /v3/feeds:MP_MAINTENANCE": RateLimit(10, 3600),
    "POST /v3/feeds:RETIRE_ITEM": RateLimit(20, 60),
    "POST /v3/feeds:MP_INVENTORY": RateLimit(50, 3600),
    "POST /v3/token": RateLimit(30, 60),
    # 订单域（官方 rate-limiting 表：GET 5000/min；写操作各 60/min）
    "GET /v3/orders": RateLimit(5000, 60),
    # 售后域（官方表：GET 50/min 店铺级——旧仓 1.3s/页手工节流的官方依据；退款 60/min）
    "GET /v3/returns": RateLimit(50, 60),
    "POST /v3/returns:refund": RateLimit(60, 60),
    "POST /v3/orders:acknowledge": RateLimit(60, 60),
    "POST /v3/orders:shipping": RateLimit(60, 60),
    "_default": RateLimit(120, 60),
}


class GCRA:
    """单桶 GCRA（异步版；算法与源实现逐行对应）。"""

    def __init__(self, limit: int, period: float):
        self.limit = limit
        self.period = period
        self.emission_interval = period / limit
        self.tat = 0.0  # theoretical arrival time (monotonic)
        self.next_avail_monotonic = 0.0  # walmart 响应头自适应推后
        self._lock = asyncio.Lock()

    async def acquire(self, max_wait: float) -> tuple[bool, float]:
        """请求一个 token → (是否放行, 需等待秒数)。超过 max_wait 直接拒绝不等。"""
        async with self._lock:
            now = time.monotonic()
            tat = max(self.tat, now, self.next_avail_monotonic)
            allow_at = tat - (self.period - self.emission_interval)
            wait = allow_at - now
            if wait > max_wait:
                return False, wait
            self.tat = tat + self.emission_interval
        if wait > 0:
            await asyncio.sleep(wait)
        return True, max(wait, 0.0)

    def update_from_walmart_headers(self, headers: dict[str, str]) -> None:
        """配额耗尽（x-current-token-count=0）时按 X-Next-Replenishment-Time 推后。"""
        if not headers:
            return
        tk: int | None = None
        nra: str | None = None
        for k, v in headers.items():
            lk = k.lower()
            if lk == "x-current-token-count":
                with contextlib.suppress(TypeError, ValueError):
                    tk = int(v)
            elif lk == "x-next-replenishment-time":
                nra = v
        if tk != 0 or not nra:
            return
        try:
            s = nra.rstrip("Z")
            try:
                dt = datetime.fromisoformat(s.replace("T", " ", 1))
            except ValueError:
                dt = datetime.strptime(s.split(".")[0], "%Y-%m-%dT%H:%M:%S")
            delta = dt.timestamp() - time.time()
            if delta > 0:
                new_target = time.monotonic() + delta + 0.5
                if new_target > self.next_avail_monotonic:
                    self.next_avail_monotonic = new_target
                    log.info("gateway.rate_adaptive", pushed_back_s=round(delta, 1))
        except Exception as exc:
            log.debug("gateway.rate_header_parse_failed", raw=nra, error=str(exc))


class RateLimiterRegistry:
    """(store, endpoint) → GCRA。端点匹配：精确 → 通配前缀 → _default。"""

    def __init__(self) -> None:
        self._buckets: dict[str, GCRA] = {}

    def get(self, store_key: str, endpoint: str) -> GCRA:
        key = f"{store_key}::{endpoint}"
        bucket = self._buckets.get(key)
        if bucket is None:
            limit = WALMART_ENDPOINT_LIMITS.get(endpoint)
            if limit is None:
                for pat, candidate in WALMART_ENDPOINT_LIMITS.items():
                    if pat.endswith("*") and endpoint.startswith(pat[:-1]):
                        limit = candidate
                        break
            if limit is None:
                limit = WALMART_ENDPOINT_LIMITS["_default"]
            bucket = GCRA(limit.limit, limit.period)
            self._buckets[key] = bucket
        return bucket

    async def gate(self, store_key: str, endpoint: str, max_wait: float | None = None) -> float:
        """等待至允许调用 → 实际等待秒数；超出 max_wait → -1。

        max_wait 默认 = endpoint period×1.1（至少 60s）——源仓校正：
        MP_ITEM 这类 10/hour 端点用固定 65s 会立即失败。
        """
        bucket = self.get(store_key, endpoint)
        if max_wait is None:
            max_wait = max(60.0, bucket.period * 1.1)
        ok, waited = await bucket.acquire(max_wait=max_wait)
        if not ok:
            log.warning(
                "gateway.rate_exceeded",
                store=store_key,
                endpoint=endpoint,
                would_wait_s=round(waited, 1),
            )
            return -1
        if waited > 1:
            log.info(
                "gateway.rate_gated", store=store_key, endpoint=endpoint, waited_s=round(waited, 2)
            )
        return waited

    def update_from_walmart_headers(
        self, store_key: str, endpoint: str, headers: dict[str, str]
    ) -> None:
        self.get(store_key, endpoint).update_from_walmart_headers(headers)


registry = RateLimiterRegistry()


def parse_retry_after(headers: dict[str, str]) -> float:
    """Retry-After > X-Next-Replenishment-Time(epoch ms/ISO) > 60s；上限 300s。（源仓原样移植）"""
    ra = headers.get("retry-after")
    if ra:
        try:
            return min(float(ra), 300.0)
        except (TypeError, ValueError):
            pass
    nrt = headers.get("x-next-replenishment-time")
    if nrt:
        try:
            ts = float(nrt)
            if ts > 1e12:  # epoch ms
                ts = ts / 1000.0
            wait = ts - time.time()
            if 0 < wait < 300:
                return wait
        except (TypeError, ValueError):
            try:
                dt = datetime.fromisoformat(nrt.replace("Z", "+00:00"))
                wait = dt.timestamp() - time.time()
                if 0 < wait < 300:
                    return wait
            except Exception:
                pass
    return 60.0
