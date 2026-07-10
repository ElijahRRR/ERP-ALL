"""R1-07 验收：三模式闸 / token 复用与 401 自愈 / 五头 / GCRA / 退避解析 / dry-run 证据。

全部离线：真实发包用 httpx.MockTransport 替身——沙盒环境严禁触碰真渠道
（每店绑定固定出口 IP，从沙盒出去即关联风险，宪法级禁区）。
"""

import asyncio
import json
import time
from pathlib import Path

import httpx
import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from erp.channel.gateway.client import WalmartGateway
from erp.channel.gateway.rate_limiter import GCRA, parse_retry_after
from erp.core.db import system_tx
from erp.core.errors import BusinessError

from .conftest import APP_URL
from .test_channel_api import seeded  # noqa: F401 复用店铺/凭证种子（A152 is_test + A153 非测试店）


def _sessions():  # type: ignore[no-untyped-def]
    return async_sessionmaker(create_async_engine(APP_URL), expire_on_commit=False)


@pytest.fixture()
def store_ids(migrated_db: str, seeded: dict[str, int]) -> dict[str, int]:
    """自给自足：直建 A152(is_test)/A153 两店 + 凭证（不依赖其他测试模块的执行顺序）。"""
    from erp.core.settings import get_settings

    key = get_settings().credential_key
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        channel_id = conn.execute(
            "SELECT id FROM app.channel WHERE code = 'walmart_us'"
        ).fetchone()[0]
        ids: dict[str, int] = {}
        for code, is_test in (("A152", True), ("A153", False)):
            row = conn.execute(
                "SELECT id FROM app.store WHERE channel_id = %s AND code = %s",
                (channel_id, code),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
                    " VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    (seeded["team"], channel_id, code, f"网关测试店{code}", is_test),
                )
                row = row.fetchone()
            ids[code] = row[0]
            conn.execute(
                """
                INSERT INTO app.store_credential (store_id, client_id, client_secret_encrypted)
                VALUES (%s, %s, pgp_sym_encrypt(%s, %s))
                ON CONFLICT (store_id) DO NOTHING
                """,
                (ids[code], f"{code.lower()}-client-00", f"{code.lower()}-secret-000", key),
            )
        conn.execute("DELETE FROM app.system_config WHERE key LIKE 'channel.%%'")
    return ids


class _FakeWalmart:
    """MockTransport 替身：/v3/token 发 token；业务路径校验五头并按脚本响应。"""

    def __init__(self) -> None:
        self.token_calls = 0
        self.api_calls: list[httpx.Request] = []
        self.script: list[httpx.Response] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/token":
            self.token_calls += 1
            assert request.headers["Authorization"].startswith("Basic ")
            return httpx.Response(
                200, json={"access_token": f"tok-{self.token_calls}", "expires_in": 900}
            )
        self.api_calls.append(request)
        # 五个必填头逐一断言（make_headers 移植正确性）
        for h in (
            "Authorization",
            "WM_SVC.NAME",
            "WM_QOS.CORRELATION_ID",
            "WM_CONSUMER.ID",
            "WM_SEC.ACCESS_TOKEN",
        ):
            assert request.headers.get(h), f"缺少必填头 {h}"
        if self.script:
            return self.script.pop(0)
        return httpx.Response(200, json={"ok": True})


def _gateway_with(fake: _FakeWalmart) -> WalmartGateway:
    gw = WalmartGateway()
    gw._transport_factory = lambda proxy: httpx.MockTransport(fake.handler)
    return gw


class TestModes:
    def test_default_dry_run_produces_snapshot(self, store_ids: dict[str, int]) -> None:
        gw = _gateway_with(_FakeWalmart())

        async def run():  # type: ignore[no-untyped-def]
            async with system_tx(_sessions()) as s:
                return await gw.request(s, store_ids["A152"], "GET", "/v3/items")

        resp = asyncio.run(run())
        assert resp.dry_run is True
        snap = resp.request_snapshot
        assert snap["method"] == "GET"
        assert snap["url"].endswith("/v3/items")
        assert snap["store"] == "A152"

    def test_live_test_blocks_non_test_store(self, store_ids: dict[str, int]) -> None:
        gw = _gateway_with(_FakeWalmart())

        async def run():  # type: ignore[no-untyped-def]
            async with system_tx(_sessions()) as s:
                await s.execute(
                    text(
                        "INSERT INTO app.system_config (key, value) VALUES"
                        " ('channel.gateway_mode', '\"live_test\"'::jsonb)"
                        " ON CONFLICT (key) DO UPDATE SET value = excluded.value"
                    )
                )
            async with system_tx(_sessions()) as s:
                return await gw.request(s, store_ids["A153"], "GET", "/v3/items")

        with pytest.raises(BusinessError) as ei:
            asyncio.run(run())
        assert ei.value.code == "GATEWAY_STORE_NOT_TEST"

    def test_live_requires_owner_switch(self, store_ids: dict[str, int]) -> None:
        gw = _gateway_with(_FakeWalmart())

        async def run():  # type: ignore[no-untyped-def]
            async with system_tx(_sessions()) as s:
                return await gw.request(
                    s, store_ids["A152"], "GET", "/v3/items", mode_override="live"
                )

        with pytest.raises(BusinessError) as ei:
            asyncio.run(run())
        assert ei.value.code == "GATEWAY_LIVE_DISABLED"

    def test_live_test_on_test_store_sends(self, store_ids: dict[str, int]) -> None:
        fake = _FakeWalmart()
        gw = _gateway_with(fake)

        async def run():  # type: ignore[no-untyped-def]
            async with system_tx(_sessions()) as s:
                return await gw.request(
                    s, store_ids["A152"], "GET", "/v3/items", mode_override="live_test"
                )

        resp = asyncio.run(run())
        assert resp.status == 200
        assert resp.data == {"ok": True}
        assert fake.token_calls == 1
        assert len(fake.api_calls) == 1


class TestTokenLifecycle:
    def test_token_reused_within_ttl(self, store_ids: dict[str, int]) -> None:
        fake = _FakeWalmart()
        gw = _gateway_with(fake)

        async def run():  # type: ignore[no-untyped-def]
            for _ in range(3):
                async with system_tx(_sessions()) as s:
                    await gw.request(
                        s, store_ids["A152"], "GET", "/v3/items", mode_override="live_test"
                    )

        asyncio.run(run())
        assert fake.token_calls == 1  # 900s 内三次调用只换一次 token
        assert len(fake.api_calls) == 3

    def test_401_self_heal_refreshes_once(self, store_ids: dict[str, int]) -> None:
        fake = _FakeWalmart()
        fake.script = [httpx.Response(401, json={"error": "expired"})]
        gw = _gateway_with(fake)

        async def run():  # type: ignore[no-untyped-def]
            async with system_tx(_sessions()) as s:
                return await gw.request(
                    s, store_ids["A152"], "GET", "/v3/items", mode_override="live_test"
                )

        resp = asyncio.run(run())
        assert resp.status == 200  # 401 → 刷新 token → 重试成功
        assert fake.token_calls == 2
        assert fake.api_calls[1].headers["WM_SEC.ACCESS_TOKEN"] == "tok-2"

    def test_429_backoff_retry(self, store_ids: dict[str, int]) -> None:
        fake = _FakeWalmart()
        fake.script = [
            httpx.Response(429, headers={"retry-after": "0.1"}, json={}),
        ]
        gw = _gateway_with(fake)

        async def run():  # type: ignore[no-untyped-def]
            async with system_tx(_sessions()) as s:
                return await gw.request(
                    s,
                    store_ids["A152"],
                    "GET",
                    "/v3/items",
                    mode_override="live_test",
                    max_retries=1,
                )

        t0 = time.monotonic()
        resp = asyncio.run(run())
        assert resp.status == 200
        assert time.monotonic() - t0 >= 0.1  # 按 Retry-After 退避后重试
        assert len(fake.api_calls) == 2


class TestGcra:
    def test_gcra_throttles(self) -> None:
        async def run() -> tuple[bool, bool, bool]:
            bucket = GCRA(limit=2, period=0.4)  # 0.2s 放行间隔
            ok1, _ = await bucket.acquire(max_wait=0)
            ok2, _ = await bucket.acquire(max_wait=0.05)
            ok3, _w3 = await bucket.acquire(max_wait=0.05)  # 第三个应超出 0.05s 容忍
            return ok1, ok2, ok3

        ok1, _ok2, ok3 = asyncio.run(run())
        assert ok1 is True
        assert ok3 is False  # 限流生效

    def test_adaptive_pushback_from_headers(self) -> None:
        bucket = GCRA(limit=100, period=60)
        future = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() + 30))
        bucket.update_from_walmart_headers(
            {"x-current-token-count": "0", "x-next-replenishment-time": future}
        )
        assert bucket.next_avail_monotonic > time.monotonic() + 25  # 推后到 walmart 给的时刻

    def test_parse_retry_after_priority(self) -> None:
        assert parse_retry_after({"retry-after": "12"}) == 12.0
        assert parse_retry_after({}) == 60.0
        assert parse_retry_after({"retry-after": "9999"}) == 300.0  # 上限


class TestDryRunEvidence:
    def test_snapshot_written_to_evidence(self, store_ids: dict[str, int]) -> None:
        """dry-run 快照落证据文件（R1-07 验收产物）。"""
        gw = _gateway_with(_FakeWalmart())

        async def run():  # type: ignore[no-untyped-def]
            async with system_tx(_sessions()) as s:
                return await gw.request(
                    s,
                    store_ids["A152"],
                    "POST",
                    "/v3/feeds",
                    endpoint_key="POST /v3/feeds:MP_ITEM",
                    params={"feedType": "MP_ITEM"},
                    json_body={"MPItemFeedHeader": {"version": "5.0"}},
                )

        resp = asyncio.run(run())
        # 证据路径相对仓库根定位（CI/沙盒/本机 checkout 位置各异，禁止写死绝对路径——CI 踩坑）
        repo_root = Path(__file__).resolve().parents[3]
        out = repo_root / ".agent" / "evidence" / "R1-07" / "dry-run-snapshot.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(resp.request_snapshot, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        assert resp.dry_run and resp.request_snapshot["endpoint_key"] == "POST /v3/feeds:MP_ITEM"
