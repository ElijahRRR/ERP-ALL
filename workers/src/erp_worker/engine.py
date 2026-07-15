"""采集引擎（移植 amazon-scraper-v3 worker/engine.py 的核心流水线，拨入 ERP 协议）。

保真移植的能力（源仓运维沉淀，逐字保留语义）：
  - 流水线：task_feeder → 优先级队列 → worker_pool(自适应并发) → 结果回传
  - AIMD 自适应并发（adaptive.py）+ 令牌桶 QPS（TokenBucket）
  - curl_cffi TLS 指纹会话 + 每 N 次成功主动轮换 + 热备 session 瞬切（session.py）
  - 被封/验证码/降级页/空标题/variant 偏移 的分级处置与 session 轮换（process_task）
  - 心跳上报指标 + 设置下发（settings_sync）

对齐 ERP 拨入协议的改造（vs v3 /api/*）：
  - server 通信层换成 ErpWorkerClient(/worker/v1)：register/pull/result/release/sync
  - 任务字段 target_ref=asin、attempt=租约纪元（回传必须原样带回做租约校验）
  - 采集结果经 payload.to_product_payload 转成 ERP product 入库结构

R1 协议不承载、故本引擎剔除的 v3 机制（挂后续工单）：
  - 截图子进程存证 / 卖家店铺发现(discover_seller) / per-ASIN 邮编切换 / 批次进度门控
"""

import asyncio
import logging
import random
import time
from typing import Any

from erp_worker import config
from erp_worker.adaptive import AdaptiveController, TokenBucket
from erp_worker.erp_client import ErpWorkerClient
from erp_worker.metrics import MetricsCollector
from erp_worker.parser import AmazonParser
from erp_worker.payload import to_product_payload
from erp_worker.proxy import get_proxy_manager
from erp_worker.session import AmazonSession

logger = logging.getLogger(__name__)

WORKER_VERSION = "r2-01"


class ScrapeEngine:
    """拨入 ERP 的 Amazon 采集引擎（TPS 模式：全局单 session，自适应并发）。"""

    def __init__(
        self,
        client: ErpWorkerClient,
        *,
        zip_code: str | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        self.client = client
        self.zip_code = zip_code or config.DEFAULT_ZIP_CODE

        self.proxy_manager = get_proxy_manager()
        self.parser = AmazonParser()
        self._session: AmazonSession | None = None
        self._rate_limiter = TokenBucket()
        self._metrics = MetricsCollector()
        self._controller = AdaptiveController(
            initial=config.INITIAL_CONCURRENCY,
            min_c=config.MIN_CONCURRENCY,
            max_c=max_concurrency or config.MAX_CONCURRENCY,
            metrics=self._metrics,
        )

        self._running = False
        self._draining = False
        self._max_retries = config.MAX_RETRIES

        # 优先级队列：(priority, seq, task)；priority 越小越先
        self._task_queue: asyncio.PriorityQueue[tuple[int, int, dict[str, Any] | None]] | None = (
            None
        )
        self._task_seq = 0
        self._queue_size = config.TASK_QUEUE_SIZE
        self._prefetch_threshold = config.TASK_PREFETCH_THRESHOLD

        # session 轮换控制
        self._success_since_rotate = 0
        self._rotate_every = config.SESSION_ROTATE_EVERY
        self._rotate_lock = asyncio.Lock()
        self._last_rotate_time = 0.0
        self._session_ready = asyncio.Event()
        self._rotation_epoch = 0
        self._rotation_grace_until = 0.0
        self._empty_title_count = 0
        self._empty_title_rotate_threshold = 15

        # 热备 session
        self._standby_session: AmazonSession | None = None
        self._standby_warming = False

        self._worker_tasks: list[asyncio.Task[None]] = []
        self._active_task_count = 0
        self._shutdown_event = asyncio.Event()
        self._settings_version = 0
        self._leased: dict[int, int] = {}  # task_id -> attempt（下线时归还未完成任务）

        self._stats = {"total": 0, "success": 0, "failed": 0, "blocked": 0, "accepted": 0}

    # ── 生命周期 ──

    async def start(self) -> None:
        logger.info(
            "Worker 启动 | 初始并发=%s | 邮编=%s",
            self._controller.current_concurrency,
            self.zip_code,
        )
        self._running = True
        self._task_queue = asyncio.PriorityQueue(maxsize=self._queue_size)

        await self._pull_initial_settings()
        await self._init_session()
        await self._controller.start()
        try:
            await asyncio.gather(
                self._task_feeder(),
                self._worker_pool(),
                self._settings_sync(),
                self._standby_warmer(),
            )
        except asyncio.CancelledError:
            pass
        await self._cleanup()
        self._print_stats()

    async def stop(self) -> None:
        logger.info("收到停止信号，优雅下线中...")
        self._running = False
        self._shutdown_event.set()
        try:
            await self._controller.stop()
        except Exception:  # noqa: BLE001
            pass
        # 唤醒所有 worker（哨兵 priority=-1 最先取出）
        if self._task_queue is not None:
            for _ in range(self._controller._max):
                try:
                    self._task_seq += 1
                    self._task_queue.put_nowait((-1, self._task_seq, None))
                except asyncio.QueueFull:
                    break

    async def _cleanup(self) -> None:
        # 归还本机未完成的租约，缩短 server 回收等待
        pending = [{"task_id": tid, "attempt": att} for tid, att in self._leased.items()]
        if pending:
            released = await self.client.release_tasks(pending)
            logger.info("下线归还 %s 个未完成任务", released)
        if self._session:
            await self._session.close()
        if self._standby_session:
            await self._standby_session.close()

    def _print_stats(self) -> None:
        s = self._stats
        logger.info(
            "Worker 停止 | 总:%s 成功:%s 失败:%s 入库:%s 被封:%s",
            s["total"],
            s["success"],
            s["failed"],
            s["accepted"],
            s["blocked"],
        )

    # ── 设置同步 ──

    async def _pull_initial_settings(self) -> None:
        result = await self.client.sync(
            metrics={"phase": "startup"}, window_state={}, version=WORKER_VERSION
        )
        if result:
            self._apply_settings(result.get("settings") or {})
            self._draining = bool(result.get("draining"))

    def _apply_settings(self, s: dict[str, Any]) -> bool:
        """应用 ERP 下发的 scrape.worker_settings（代理/并发/QPS/超时/带宽/轮换）。

        → True=需要轮换 session 才能生效的参数发生了变更（request_timeout 在
        session 创建时固化——A152 实测 15s 写死全量超时的修复点）。
        """
        changes: list[str] = []
        needs_rotate = False
        proxy_url = s.get("proxy_url")
        if proxy_url and proxy_url != config.PROXY_URL:
            config.PROXY_URL = proxy_url
            self.proxy_manager._proxy_url = proxy_url
            changes.append("proxy")
        for key, attr in (("max_concurrency", "_max"), ("min_concurrency", "_min")):
            val = s.get(key)
            if isinstance(val, int) and val != getattr(self._controller, attr):
                setattr(self._controller, attr, val)
                changes.append(f"{key}={val}")
        rate = s.get("token_bucket_rate")
        if isinstance(rate, (int, float)) and rate != self._rate_limiter.rate:
            self._rate_limiter.rate = float(rate)
            changes.append(f"qps={rate}")
        timeout = s.get("request_timeout")
        if (
            isinstance(timeout, (int, float))
            and timeout > 0
            and int(timeout) != config.REQUEST_TIMEOUT
        ):
            config.REQUEST_TIMEOUT = int(timeout)
            changes.append(f"request_timeout={int(timeout)}s")
            needs_rotate = True
        bandwidth = s.get("proxy_bandwidth_mbps")
        if (
            isinstance(bandwidth, (int, float))
            and bandwidth >= 0
            and float(bandwidth) != config.PROXY_BANDWIDTH_MBPS
        ):
            config.PROXY_BANDWIDTH_MBPS = float(bandwidth)
            changes.append(f"proxy_bandwidth={bandwidth}Mbps")
        retries = s.get("max_retries")
        if isinstance(retries, int) and retries > 0:
            self._max_retries = retries
        rotate = s.get("session_rotate_every")
        if isinstance(rotate, int) and rotate > 0:
            self._rotate_every = rotate
        if changes:
            logger.info("设置同步: %s", ", ".join(changes))
        return needs_rotate

    async def _settings_sync(self) -> None:
        while self._running:
            try:
                await asyncio.wait_for(self._shutdown_event.wait(), timeout=30)
                break
            except TimeoutError:
                pass
            if not self._running:
                break
            snap = self._metrics.snapshot()
            metrics = {
                "total": snap["total"],
                "success_rate": snap["success_rate"],
                "block_rate": snap["block_rate"],
                "latency_p50": snap["latency_p50"],
                "latency_p95": snap["latency_p95"],
                "inflight": snap["inflight"],
                "accepted": self._stats["accepted"],
                "failed": self._stats["failed"],
            }
            window = {
                "concurrency": self._controller.current_concurrency,
                "queue_size": self._task_queue.qsize() if self._task_queue else 0,
            }
            result = await self.client.sync(
                metrics=metrics, window_state=window, version=WORKER_VERSION
            )
            if result:
                needs_rotate = self._apply_settings(result.get("settings") or {})
                if needs_rotate and self._session is not None:
                    # request_timeout 固化在 session 创建时——立即轮换使新值生效。
                    # 热备是旧超时建的：丢弃走冷轮换；清节流戳保证本次轮换必执行
                    if self._standby_session is not None:
                        stale_standby = self._standby_session
                        self._standby_session = None
                        asyncio.create_task(self._delayed_close(stale_standby))
                    self._last_rotate_time = 0.0
                    await self._rotate_session(reason="超时参数变更")
                if result.get("draining") and not self._draining:
                    logger.info("server 指示下线（draining），停止领新任务")
                    self._draining = True

    # ── session 生命周期 ──

    async def _init_session(self) -> None:
        self._session_ready.clear()
        session = await self._create_session_with_retry()
        self._session = session
        self._success_since_rotate = 0
        self._session_ready.set()
        if session is None:
            logger.error("session 初始化失败，将在处理任务时继续重试")

    async def _create_session_with_retry(
        self, max_attempts: int = 3, delay: float = 5
    ) -> AmazonSession | None:
        for attempt in range(max_attempts):
            if self._shutdown_event.is_set():
                return None
            session = AmazonSession(self.proxy_manager, self.zip_code)
            if await session.initialize():
                return session
            await session.close()
            if attempt < max_attempts - 1:
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=delay)
                    return None
                except TimeoutError:
                    pass
        return None

    async def _rotate_session(self, reason: str = "主动轮换") -> None:
        async with self._rotate_lock:
            now = time.monotonic()
            if now - self._last_rotate_time < 5:
                return
            logger.info("session 轮换：%s", reason)
            self._session_ready.clear()
            old = self._session
            self._session = None
            if self._standby_session and self._standby_session.is_ready():
                self._session = self._standby_session
                self._standby_session = None
                self._finish_rotation(old)
                logger.info("session 轮换成功（热备瞬切）")
                await self.proxy_manager.report_blocked()
                return
            if old:
                asyncio.create_task(self._delayed_close(old))
            await self.proxy_manager.report_blocked()
            await asyncio.sleep(1)
            self._session = await self._create_session_with_retry(delay=3)
            self._finish_rotation(None)
            logger.info("session 冷轮换%s", "成功" if self._session else "失败")

    def _finish_rotation(self, old: AmazonSession | None) -> None:
        self._success_since_rotate = 0
        self._empty_title_count = 0
        self._last_rotate_time = time.monotonic()
        self._rotation_epoch += 1
        self._rotation_grace_until = time.monotonic() + 3.0
        self._session_ready.set()
        if old:
            asyncio.create_task(self._delayed_close(old))

    async def _delayed_close(self, session: AmazonSession, delay: float = 5) -> None:
        try:
            await asyncio.sleep(delay)
            await session.close()
        except Exception:  # noqa: BLE001
            pass

    async def _wait_for_rotation_before_retry(self, asin: str, reason: str = "") -> None:
        self._empty_title_count += 1
        epoch = self._rotation_epoch
        if time.monotonic() < self._rotation_grace_until:
            await self._session_ready.wait()
            return
        if self._empty_title_count >= self._empty_title_rotate_threshold:
            await self._rotate_session(reason=f"空标题累计 {self._empty_title_count} 次")
        else:
            await self._rotate_session(reason=reason)
        if self._rotation_epoch == epoch:
            for _ in range(30):
                await asyncio.sleep(0.5)
                if self._rotation_epoch != epoch or not self._running:
                    break
        await self._session_ready.wait()

    async def _standby_warmer(self) -> None:
        await self._session_ready.wait()
        await asyncio.sleep(3)
        while self._running:
            try:
                if self._standby_session is None or not self._standby_session.is_ready():
                    if not self._standby_warming:
                        self._standby_warming = True
                        standby = await self._create_session_with_retry(max_attempts=1, delay=0)
                        if standby:
                            old = self._standby_session
                            self._standby_session = standby
                            if old:
                                await old.close()
                            logger.info("热备 session 就绪")
                        self._standby_warming = False
                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=5)
                    break
                except TimeoutError:
                    pass
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.debug("热备预热异常: %s", e)
                self._standby_warming = False

    # ── 流水线 ──

    async def _task_feeder(self) -> None:
        assert self._task_queue is not None
        empty_streak = 0
        while self._running:
            try:
                if self._draining:
                    await asyncio.sleep(2)
                    continue
                qsize = self._task_queue.qsize()
                threshold = int(self._queue_size * self._prefetch_threshold)
                if qsize >= threshold:
                    await asyncio.sleep(1)
                    continue
                fetch = max(5, min(self._controller.current_concurrency, self._queue_size - qsize))
                tasks = await self.client.pull_tasks(fetch)
                if tasks is None:
                    await asyncio.sleep(1)  # 服务器错误 → 快速重试
                    continue
                if not tasks:
                    empty_streak += 1
                    await asyncio.sleep(min(2 * empty_streak, 5))
                    continue
                empty_streak = 0
                for task in tasks:
                    self._leased[task["task_id"]] = task["attempt"]
                    self._task_seq += 1
                    await self._task_queue.put((0, self._task_seq, task))
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.error("任务补给异常: %s", e)
                await asyncio.sleep(1)

    async def _worker_pool(self) -> None:
        initial = self._controller.current_concurrency
        for i in range(initial):
            self._worker_tasks.append(asyncio.create_task(self._worker_loop(i)))
        while self._running:
            await asyncio.sleep(2)
            target = self._controller.current_concurrency
            current = len([t for t in self._worker_tasks if not t.done()])
            if target > current:
                for _ in range(target - current):
                    idx = len(self._worker_tasks)
                    self._worker_tasks.append(asyncio.create_task(self._worker_loop(idx)))
            self._worker_tasks = [t for t in self._worker_tasks if not t.done()]
        if self._worker_tasks:
            await asyncio.gather(*self._worker_tasks, return_exceptions=True)

    async def _worker_loop(self, idx: int) -> None:
        assert self._task_queue is not None
        while self._running:
            try:
                try:
                    item = await asyncio.wait_for(self._task_queue.get(), timeout=5.0)
                except TimeoutError:
                    continue
                task = item[2]
                if task is None:
                    break
                self._active_task_count += 1
                try:
                    await self._process_task(task)
                finally:
                    self._active_task_count = max(0, self._active_task_count - 1)
                    self._leased.pop(task["task_id"], None)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001
                logger.error("worker-%s 未捕获异常: %s", idx, e)
                await asyncio.sleep(1)

    def _calc_recv_speed(self) -> int:
        bw = config.PROXY_BANDWIDTH_MBPS
        if bw <= 0:
            return 0
        return int(bw * 1_000_000 / 8) // max(1, self._metrics.inflight)

    async def _apply_jitter(self) -> None:
        qps = self._rate_limiter.rate if self._rate_limiter else 5.0
        await asyncio.sleep(random.uniform(0, min(0.3, 0.5 / qps)))

    async def _process_task(self, task: dict[str, Any]) -> None:
        """处理单个 product_detail 任务（保真 v3 分级处置：被封/404/降级/偏移）。"""
        asin = task["target_ref"]
        task_id = task["task_id"]
        attempt_lease = task["attempt"]
        max_retries = self._max_retries
        last_error_type = "network"
        last_error_detail = ""
        local_attempt = 0

        while local_attempt < max_retries:
            try:
                if not self._session_ready.is_set():
                    try:
                        await asyncio.wait_for(self._session_ready.wait(), timeout=30)
                    except TimeoutError:
                        local_attempt += 1
                        continue
                if self._session is None or not self._session.is_ready():
                    local_attempt += 1
                    await asyncio.sleep(2)
                    continue
                session = self._session

                if self._rate_limiter:
                    await self._rate_limiter.acquire()
                await self._controller.acquire()
                await self._apply_jitter()
                recv_speed = self._calc_recv_speed()
                req_start = time.time()
                try:
                    resp = await session.fetch_product_page(asin, max_recv_speed=recv_speed)
                    resp_bytes = len(resp.content) if resp and hasattr(resp, "content") else 0
                finally:
                    req_elapsed = time.time() - req_start
                    self._controller.release()

                if resp is None:
                    self._controller.record_result(req_elapsed, False, False, 0)
                    local_attempt += 1
                    last_error_type = "timeout"
                    await asyncio.sleep(2)
                    continue

                if session.is_blocked(resp):
                    if session.is_captcha(resp) and await session.solve_captcha(resp):
                        continue
                    self._controller.record_result(req_elapsed, False, True, resp_bytes)
                    self._stats["blocked"] += 1
                    await self._rotate_session(reason="被封锁")
                    await self._report_failure(
                        task_id, attempt_lease, "blocked", f"HTTP {resp.status_code}"
                    )
                    return

                if session.is_404(resp):
                    self._controller.record_result(req_elapsed, True, False, resp_bytes)
                    result = self.parser._default_result(asin, self.zip_code)
                    result["title"] = "[商品不存在]"
                    await self._report_success(task_id, attempt_lease, result, asin)
                    return

                result = self.parser.parse_product(resp.text, asin, self.zip_code)
                title = result.get("title", "")

                if title == "[验证码拦截]":
                    if session.is_captcha(resp) and await session.solve_captcha(resp):
                        continue
                    self._controller.record_result(req_elapsed, False, True, resp_bytes)
                    self._stats["blocked"] += 1
                    local_attempt += 1
                    last_error_type, last_error_detail = "captcha", "validateCaptcha"
                    await self._rotate_session(reason="页面拦截")
                    continue
                if title == "[API封锁]":
                    self._controller.record_result(req_elapsed, False, True, resp_bytes)
                    self._stats["blocked"] += 1
                    local_attempt += 1
                    last_error_type, last_error_detail = "blocked", "api-services-support"
                    await self._rotate_session(reason="页面拦截")
                    continue
                if title in ("[页面为空]", "[HTML解析失败]", "", "N/A") or not title:
                    self._controller.record_result(req_elapsed, False, False, resp_bytes)
                    local_attempt += 1
                    last_error_type, last_error_detail = "parse_error", title or "标题为空"
                    await self._wait_for_rotation_before_retry(asin, reason=last_error_detail)
                    continue

                # variant 偏移：请求 A 拿到兄弟 variant B → 直接终态失败（不轮换不本地重试）
                page_asin = result.get("_page_asin")
                if page_asin and page_asin.upper() != asin.upper():
                    self._controller.record_result(req_elapsed, False, False, resp_bytes)
                    await self._report_failure(
                        task_id, attempt_lease, "variant_offset", f"page={page_asin}"
                    )
                    return

                # 核心字段全缺 → 降级页，轮换重试
                na = {"", "N/A", "N/a", "n/a", "None", None, "0"}
                nfo = result.get("current_price") == "No Featured Offer"
                unavail = result.get("current_price") == "不可售"
                degraded = (
                    all(
                        result.get(f) in na
                        for f in ("current_price", "buybox_price", "stock_status", "brand")
                    )
                    and not nfo
                    and not unavail
                )
                if degraded:
                    self._controller.record_result(req_elapsed, False, False, resp_bytes)
                    local_attempt += 1
                    last_error_type, last_error_detail = "parse_error", "核心字段全缺"
                    await self._wait_for_rotation_before_retry(asin, reason="核心字段缺失")
                    continue

                # 成功
                self._controller.record_result(req_elapsed, True, False, resp_bytes)
                await self._report_success(task_id, attempt_lease, result, asin)
                self._success_since_rotate += 1
                if self._success_since_rotate >= self._rotate_every:
                    await self._rotate_session(
                        reason=f"主动轮换（{self._success_since_rotate} 次）"
                    )
                return

            except Exception as e:  # noqa: BLE001
                local_attempt += 1
                name = type(e).__name__
                last_error_type = "timeout" if "timeout" in name.lower() else "network"
                last_error_detail = f"{name}: {str(e)[:200]}"
                logger.error("ASIN %s 异常 (%s/%s): %s", asin, local_attempt, max_retries, e)
                await asyncio.sleep(2)

        await self._report_failure(task_id, attempt_lease, last_error_type, last_error_detail)

    async def _report_success(
        self, task_id: int, attempt: int, result: dict[str, Any], asin: str
    ) -> None:
        payload = to_product_payload(result)
        res = await self.client.submit_result(
            task_id=task_id, attempt=attempt, success=True, payload=payload
        )
        self._stats["total"] += 1
        if res is None:
            self._stats["failed"] += 1
            logger.warning("ASIN %s 回传失败（server 将超时回收）", asin)
            return
        if res.get("accepted"):
            self._stats["success"] += 1
            if res.get("product"):
                self._stats["accepted"] += 1
            sku = (res.get("product") or {}).get("master_sku", "-")
            logger.info("OK %s → %s | %s", asin, sku, (payload.get("title") or "")[:40])
        else:
            logger.info("ASIN %s 结果被拒（stale 租约失效）", asin)

    async def _report_failure(
        self, task_id: int, attempt: int, error_type: str, error_detail: str
    ) -> None:
        await self.client.submit_result(
            task_id=task_id,
            attempt=attempt,
            success=False,
            error_type=error_type,
            error_detail=error_detail,
        )
        self._stats["total"] += 1
        self._stats["failed"] += 1
