"""引擎处置逻辑测试：注入假 session/parser/client，无 curl_cffi、无网络。

覆盖 process_task 的关键分支与协议回传（租约 attempt 必须原样带回；成功走 payload
适配；被封/耗尽 → 失败回传）+ 下线归还未完成租约。
"""

from typing import Any

import pytest

from erp_worker import config
from erp_worker.engine import ScrapeEngine


class FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "<html>ok</html>") -> None:
        self.status_code = status_code
        self.text = text
        self.content = text.encode()
        self.url = "https://www.amazon.com/dp/X"


class FakeSession:
    """按脚本返回响应；记录被调用的 asin。"""

    def __init__(self, *, blocked: bool = False, is_404: bool = False) -> None:
        self._blocked = blocked
        self._is_404 = is_404
        self.fetched: list[str] = []

    def is_ready(self) -> bool:
        return True

    async def fetch_product_page(self, asin: str, max_recv_speed: int = 0) -> FakeResponse:
        self.fetched.append(asin)
        return FakeResponse(status_code=404 if self._is_404 else 200)

    def is_blocked(self, resp: FakeResponse) -> bool:
        return self._blocked

    def is_captcha(self, resp: FakeResponse) -> bool:
        return False

    def is_404(self, resp: FakeResponse) -> bool:
        return self._is_404

    async def solve_captcha(self, resp: FakeResponse) -> bool:
        return False

    async def close(self) -> None:
        return None


class FakeParser:
    def __init__(self, result: dict[str, Any]) -> None:
        self._result = result

    def parse_product(self, html: str, asin: str, zip_code: str) -> dict[str, Any]:
        return dict(self._result)

    def _default_result(self, asin: str, zip_code: str) -> dict[str, Any]:
        return {"asin": asin, "title": "N/A", "brand": "N/A", "current_price": "N/A"}


class FakeClient:
    """记录 submit_result / release_tasks 调用。"""

    def __init__(self) -> None:
        self.results: list[dict[str, Any]] = []
        self.released: list[list[dict[str, int]]] = []

    async def submit_result(self, **kwargs: Any) -> dict[str, Any]:
        self.results.append(kwargs)
        return {"accepted": True, "stale": False, "product": {"master_sku": "M0000001"}}

    async def release_tasks(self, tasks: list[dict[str, int]]) -> int:
        self.released.append(tasks)
        return len(tasks)

    async def sync(self, **kwargs: Any) -> dict[str, Any]:
        return {"settings": {}, "draining": False}


def _engine(client: FakeClient, session: FakeSession, parser: FakeParser) -> ScrapeEngine:
    eng = ScrapeEngine(client)  # type: ignore[arg-type]
    eng._session = session  # type: ignore[assignment]
    eng.parser = parser  # type: ignore[assignment]
    eng._session_ready.set()

    async def _noop_rotate(reason: str = "") -> None:
        return None

    eng._rotate_session = _noop_rotate  # type: ignore[assignment]
    eng._wait_for_rotation_before_retry = lambda *a, **k: _noop_rotate()  # type: ignore[assignment]
    return eng


_GOOD = {
    "asin": "B0GOOD0001",
    "title": "Good Product",
    "brand": "Acme",
    "current_price": "$9.99",
    "buybox_price": "$9.99",
    "stock_status": "In Stock",
    "category_tree": "Home > X",
    "category_ids": "1 > 2",
    "image_urls": "https://img/a.jpg",
    "bullet_points": "a\nb",
    "_page_asin": "B0GOOD0001",
}

_TASK = {"task_id": 55, "target_ref": "B0GOOD0001", "attempt": 3, "max_attempts": 3}


async def test_success_reports_with_matching_lease_and_payload() -> None:
    client, session = FakeClient(), FakeSession()
    eng = _engine(client, session, FakeParser(_GOOD))
    await eng._process_task(_TASK)
    assert len(client.results) == 1
    call = client.results[0]
    assert call["task_id"] == 55
    assert call["attempt"] == 3  # 租约纪元原样带回
    assert call["success"] is True
    assert call["payload"]["title"] == "Good Product"
    assert call["payload"]["brand"] == "Acme"
    assert call["payload"]["images"] == ["https://img/a.jpg"]
    assert eng._stats["success"] == 1
    assert eng._stats["accepted"] == 1


async def test_blocked_reports_failure_and_returns_immediately() -> None:
    client, session = FakeClient(), FakeSession(blocked=True)
    eng = _engine(client, session, FakeParser(_GOOD))
    await eng._process_task(_TASK)
    assert len(client.results) == 1
    call = client.results[0]
    assert call["success"] is False
    assert call["error_type"] == "blocked"
    assert call["attempt"] == 3
    assert eng._stats["blocked"] == 1


async def test_variant_offset_terminal_failure() -> None:
    client, session = FakeClient(), FakeSession()
    offset = dict(_GOOD, _page_asin="B0OTHER999")
    eng = _engine(client, session, FakeParser(offset))
    await eng._process_task(_TASK)
    call = client.results[0]
    assert call["success"] is False
    assert call["error_type"] == "variant_offset"


async def test_404_reports_placeholder_success() -> None:
    client, session = FakeClient(), FakeSession(is_404=True)
    eng = _engine(client, session, FakeParser(_GOOD))
    await eng._process_task(_TASK)
    call = client.results[0]
    assert call["success"] is True
    assert call["payload"]["title"] == "[商品不存在]"


async def test_empty_title_exhausts_retries_then_fails() -> None:
    client, session = FakeClient(), FakeSession()
    eng = _engine(client, session, FakeParser(dict(_GOOD, title="")))
    eng._max_retries = 2
    await eng._process_task(_TASK)
    # 只在最终耗尽后回传一次失败
    assert len(client.results) == 1
    assert client.results[0]["success"] is False
    assert client.results[0]["error_type"] == "parse_error"


async def test_cleanup_releases_outstanding_leases() -> None:
    client = FakeClient()
    eng = _engine(client, FakeSession(), FakeParser(_GOOD))
    eng._leased = {101: 1, 102: 2}
    await eng._cleanup()
    assert client.released == [[{"task_id": 101, "attempt": 1}, {"task_id": 102, "attempt": 2}]]


@pytest.mark.parametrize("degraded_price", ["N/A", "0"])
async def test_degraded_page_retries(degraded_price: str) -> None:
    client, session = FakeClient(), FakeSession()
    degraded = {
        "asin": "B0DEG",
        "title": "Has Title",
        "brand": "N/A",
        "current_price": degraded_price,
        "buybox_price": "N/A",
        "stock_status": "N/A",
        "_page_asin": "B0DEG",
    }
    eng = _engine(client, session, FakeParser(degraded))
    eng._max_retries = 1
    await eng._process_task({"task_id": 1, "target_ref": "B0DEG", "attempt": 1, "max_attempts": 3})
    # 降级页耗尽后失败回传
    assert client.results[0]["success"] is False
    assert client.results[0]["error_type"] == "parse_error"


class TestApplySettings:
    """A152 实测回归：request_timeout 写死 15s 全量超时——settings 下发必须可覆盖。"""

    def test_request_timeout_and_bandwidth_wired(self) -> None:
        eng = ScrapeEngine(FakeClient())  # type: ignore[arg-type]
        old_t, old_b = config.REQUEST_TIMEOUT, config.PROXY_BANDWIDTH_MBPS
        try:
            needs_rotate = eng._apply_settings(
                {"request_timeout": old_t + 30, "proxy_bandwidth_mbps": 8}
            )
            assert needs_rotate is True  # 超时固化在 session 创建时 → 变更需轮换
            assert config.REQUEST_TIMEOUT == old_t + 30
            assert config.PROXY_BANDWIDTH_MBPS == 8.0
        finally:
            config.REQUEST_TIMEOUT, config.PROXY_BANDWIDTH_MBPS = old_t, old_b

    def test_same_or_invalid_timeout_no_rotate(self) -> None:
        eng = ScrapeEngine(FakeClient())  # type: ignore[arg-type]
        old_t = config.REQUEST_TIMEOUT
        try:
            assert eng._apply_settings({"request_timeout": old_t}) is False  # 同值不折腾
            assert eng._apply_settings({"request_timeout": 0}) is False  # 非法值忽略
            assert eng._apply_settings({"request_timeout": -5}) is False
            assert config.REQUEST_TIMEOUT == old_t
            assert eng._apply_settings({}) is False  # 未下发保持现状
        finally:
            config.REQUEST_TIMEOUT = old_t


class TestProxyPolicy:
    """2026-07-15 真机事故回归：容器不带 PROXY_URL 重建 → 空代理静默直连 →
    全量超时装成"采集坏了"。fail-closed：代理缺失拒绝启动，显式 --allow-direct 才放行。"""

    def test_missing_proxy_refuses_start(self) -> None:
        eng = ScrapeEngine(FakeClient())  # type: ignore[arg-type]
        old = config.PROXY_URL
        try:
            config.PROXY_URL = ""
            with pytest.raises(RuntimeError, match="PROXY_REQUIRED"):
                eng._enforce_proxy_policy()
        finally:
            config.PROXY_URL = old

    def test_proxy_present_or_explicit_direct_passes(self) -> None:
        old = config.PROXY_URL
        try:
            config.PROXY_URL = "http://u:p@127.0.0.1:1080"
            ScrapeEngine(FakeClient())._enforce_proxy_policy()  # type: ignore[arg-type]
            config.PROXY_URL = ""
            eng = ScrapeEngine(FakeClient(), allow_direct=True)  # type: ignore[arg-type]
            eng._enforce_proxy_policy()  # 显式直连（开发档）不拦
        finally:
            config.PROXY_URL = old

    def test_settings_delivered_proxy_is_dns_resolved(self) -> None:
        """ERP 下发 proxy_url 必须与启动路径同规格走域名预解析（c-ares 坑）。"""
        eng = ScrapeEngine(FakeClient())  # type: ignore[arg-type]
        old = config.PROXY_URL
        try:
            eng._apply_settings({"proxy_url": "http://u:p@127.0.0.1:9999"})  # IP 直通
            assert eng.proxy_manager._proxy_url == "http://u:p@127.0.0.1:9999"
            assert config.PROXY_URL == "http://u:p@127.0.0.1:9999"
        finally:
            config.PROXY_URL = old


async def test_variant_degraded_retries_then_ingests_with_marker() -> None:
    """变体降级页（R2-11 真机）：家族信号在但 twister 全空 → 换出口重试，耗尽带标记入库。"""
    degraded = {
        **_GOOD,
        "parent_asin": "B0PARENT01",  # 家族信号在（≠自身）
        "variant_attributes": "",
        "variation_asins": "",
    }
    client = FakeClient()
    session = FakeSession()
    eng = _engine(client, session, FakeParser(degraded))
    await eng._process_task(dict(_TASK))
    # 独立预算重试 VARIANT_DEGRADED_RETRIES 次后仍降级 → 入库且带标记
    assert len(session.fetched) == 1 + config.VARIANT_DEGRADED_RETRIES
    assert len(client.results) == 1 and client.results[0]["success"] is True
    attrs = client.results[0]["payload"]["attrs"]
    assert attrs.get("variant_degraded") == "1"
    assert attrs.get("parent_asin") == "B0PARENT01"


async def test_variant_page_full_data_no_retry() -> None:
    """twister 数据齐全的变体页：一次成功，无降级标记。"""
    full = {
        **_GOOD,
        "parent_asin": "B0PARENT01",
        "variant_attributes": "size_name=Queen",
        "variation_asins": "B0SIB00001,B0SIB00002",
    }
    client = FakeClient()
    session = FakeSession()
    eng = _engine(client, session, FakeParser(full))
    await eng._process_task(dict(_TASK))
    assert len(session.fetched) == 1
    attrs = client.results[0]["payload"]["attrs"]
    assert "variant_degraded" not in attrs
    assert attrs.get("variation_asins") == ["B0SIB00001", "B0SIB00002"]


async def test_non_variant_page_not_flagged() -> None:
    """非变体商品（parent 自指/缺失）：不触发降级判定。"""
    plain = {**_GOOD, "parent_asin": "B0GOOD0001"}
    client = FakeClient()
    session = FakeSession()
    eng = _engine(client, session, FakeParser(plain))
    await eng._process_task(dict(_TASK))
    assert len(session.fetched) == 1
    assert "variant_degraded" not in client.results[0]["payload"]["attrs"]
