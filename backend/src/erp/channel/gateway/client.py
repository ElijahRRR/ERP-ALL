"""Walmart 渠道网关（异步移植版）。

移植源：erpAPI/walmart_client.py（生产实战版，机制对照表见
.agent/evidence/R1-07/archaeology.md）。全部渠道调用的唯一出口——
业务代码禁止绕过本网关直连渠道 API（CLAUDE.md 禁区）。

三模式（验证纪律，D-Q37）：
  dry_run   构造完整请求但不发包，返回请求快照（证据）——默认模式
  live_test 真实发包，但仅允许 is_test 店铺（A152）
  live      真实发包，需 system_config `channel.live_enabled=true`（Owner 放量开关）
模式来源：system_config `channel.gateway_mode`（运营可切，默认 dry_run）。

实战自愈机制（源仓逐条移植）：
  - 按代理维度池化 AsyncClient（HTTP/2 + 连接级重试），半死连接主动剔除重建；
  - token 900s 缓存提前 60s 过期 + 双检锁；401 就地刷新重试 1 次；
  - 429/5xx opt-in 退避（写路径默认不自动重试——feed 类走上层 verify-back）。
"""

import asyncio
import base64
import contextlib
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import quote

import httpx
import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.channel.gateway.rate_limiter import parse_retry_after, registry
from erp.channel.service import decrypt_credential
from erp.core.errors import BusinessError
from erp.core.settings import get_settings

log = structlog.get_logger()


GatewayMode = Literal["dry_run", "live_test", "live"]

# 防 SOCKS hang（源仓 2026-05-12 实战：SSL read 卡 2.5h）——async 侧用显式 Timeout 全覆盖
_TIMEOUT = httpx.Timeout(30.0, connect=15.0)


@dataclass
class GatewayResponse:
    status: int | None
    headers: dict[str, str]
    data: dict[str, Any] | None
    dry_run: bool = False
    request_snapshot: dict[str, Any] | None = None


@dataclass
class _StoreContext:
    store_id: int
    code: str
    is_test: bool
    client_id: str
    client_secret: str
    proxy_url: str | None
    token: str | None = None
    token_expires_at: float = 0.0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class WalmartGateway:
    """按进程单例使用（api/worker 各自持有）。"""

    def __init__(self) -> None:
        self._contexts: dict[int, _StoreContext] = {}
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._transport_factory = self._default_transport
        self._loaded_at: dict[int, float] = {}

    # ── 上下文与连接 ──

    async def _context(self, session: AsyncSession, store_id: int) -> _StoreContext:
        ctx = self._contexts.get(store_id)
        # 凭证/代理 5 分钟重载一次（前台改凭证后网关及时跟进）
        if ctx is not None and time.monotonic() - self._loaded_at.get(store_id, 0) < 300:
            return ctx
        row = (
            await session.execute(
                text(
                    "SELECT s.id, s.code, s.is_test, p.kind, p.host, p.port, p.username,"
                    " CASE WHEN p.password_encrypted IS NULL THEN NULL"
                    "      ELSE pgp_sym_decrypt(p.password_encrypted, :key) END AS proxy_pw"
                    " FROM app.store s LEFT JOIN app.proxy p ON p.id = s.proxy_id"
                    " WHERE s.id = :sid"
                ),
                {"sid": store_id, "key": get_settings().credential_key},
            )
        ).first()
        if row is None:
            raise BusinessError("STORE_NOT_FOUND", "店铺不存在")
        client_id, client_secret = await decrypt_credential(session, store_id)
        proxy_url = None
        if row.host:
            auth = ""
            if row.username and row.proxy_pw:
                auth = f"{quote(row.username, safe='')}:{quote(row.proxy_pw, safe='')}@"
            proxy_url = f"{row.kind}://{auth}{row.host}:{row.port}"
        old = self._contexts.get(store_id)
        ctx = _StoreContext(
            store_id=store_id,
            code=row.code,
            is_test=row.is_test,
            client_id=client_id,
            client_secret=client_secret,
            proxy_url=proxy_url,
        )
        if old is not None and old.client_id == client_id:
            ctx.token, ctx.token_expires_at = old.token, old.token_expires_at
        self._contexts[store_id] = ctx
        self._loaded_at[store_id] = time.monotonic()
        return ctx

    def _default_transport(self, proxy: str | None) -> httpx.AsyncBaseTransport:
        return httpx.AsyncHTTPTransport(
            proxy=proxy,
            retries=2,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=50),
        )

    def _client(self, proxy: str | None) -> httpx.AsyncClient:
        key = proxy or ""
        client = self._clients.get(key)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(transport=self._transport_factory(proxy), timeout=_TIMEOUT)
            self._clients[key] = client
        return client

    async def _invalidate_client(self, proxy: str | None) -> None:
        """半死连接自愈：剔除并关闭该代理的连接，下次请求建全新连接。（源仓机制）"""
        client = self._clients.pop(proxy or "", None)
        if client is not None:
            with contextlib.suppress(Exception):
                await client.aclose()

    # ── token（900s 缓存-60s + 双检锁 + 401 自愈由 request 驱动）──

    async def _token(self, ctx: _StoreContext, *, force_refresh: bool = False) -> str:
        if not force_refresh and ctx.token and time.time() < ctx.token_expires_at:
            return ctx.token
        async with ctx.lock:
            if not force_refresh and ctx.token and time.time() < ctx.token_expires_at:
                return ctx.token
            cred = base64.b64encode(f"{ctx.client_id}:{ctx.client_secret}".encode()).decode()
            client = self._client(ctx.proxy_url)
            resp = await client.post(
                f"{get_settings().channel_base_url}/v3/token",
                headers={
                    "Authorization": f"Basic {cred}",
                    "WM_SVC.NAME": "Walmart Marketplace",
                    "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
                    "WM_CONSUMER.ID": ctx.client_id,
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                data={"grant_type": "client_credentials"},
            )
            resp.raise_for_status()
            payload = resp.json()
            ctx.token = payload["access_token"]
            ctx.token_expires_at = time.time() + int(payload.get("expires_in", 900)) - 60
            log.info("gateway.token_refreshed", store=ctx.code)
            return ctx.token

    @staticmethod
    def _headers(token: str, client_id: str) -> dict[str, str]:
        """五个必填头（源仓 make_headers 原样移植）。"""
        return {
            "Authorization": f"Bearer {token}",
            "WM_SVC.NAME": "Walmart Marketplace",
            "WM_QOS.CORRELATION_ID": str(uuid.uuid4()),
            "WM_CONSUMER.ID": client_id,
            "WM_SEC.ACCESS_TOKEN": token,
            "Accept": "application/json",
        }

    # ── 模式闸 ──

    async def _resolve_mode(self, session: AsyncSession) -> GatewayMode:
        row = (
            await session.execute(
                text(
                    "SELECT value #>> '{}' FROM app.system_config"
                    " WHERE key = 'channel.gateway_mode'"
                )
            )
        ).first()
        mode = (row[0] if row else None) or "dry_run"
        if mode not in ("dry_run", "live_test", "live"):
            mode = "dry_run"
        return mode  # type: ignore[return-value]

    async def _enforce_mode(
        self, session: AsyncSession, ctx: _StoreContext, mode: GatewayMode
    ) -> None:
        if mode == "live_test" and not ctx.is_test:
            raise BusinessError(
                "GATEWAY_STORE_NOT_TEST",
                f"当前 live_test 模式仅允许测试店（{ctx.code} 非 is_test）",
            )
        if mode == "live":
            row = (
                await session.execute(
                    text(
                        "SELECT value #>> '{}' FROM app.system_config"
                        " WHERE key = 'channel.live_enabled'"
                    )
                )
            ).first()
            if not row or row[0] != "true":
                raise BusinessError(
                    "GATEWAY_LIVE_DISABLED", "live 模式未放量（channel.live_enabled，Owner 开关）"
                )

    # ── 请求主路径 ──

    async def prepare(
        self,
        session: AsyncSession,
        store_id: int,
        *,
        mode_override: GatewayMode | None = None,
    ) -> tuple[_StoreContext, GatewayMode]:
        """解析店铺上下文 + 模式闸（全部 DB 读集中于此，供三段式在事务内预取）。

        RS-03b：写路径执行器在短事务里 prepare，随后用 request_prepared 发包——
        HTTP 期间零 DB 会话/零事务/零行锁。模式闸拒绝（live 未放量等）在这里抛出，
        调用方可借事务回滚保持原子性。
        """
        ctx = await self._context(session, store_id)
        mode = mode_override or await self._resolve_mode(session)
        await self._enforce_mode(session, ctx, mode)
        return ctx, mode

    async def request(
        self,
        session: AsyncSession,
        store_id: int,
        method: str,
        path: str,
        *,
        endpoint_key: str | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        max_retries: int = 0,
        mode_override: GatewayMode | None = None,
    ) -> GatewayResponse:
        """渠道调用唯一入口。endpoint_key 用于限流桶（缺省 = "METHOD path"）。"""
        ctx, mode = await self.prepare(session, store_id, mode_override=mode_override)
        return await self.request_prepared(
            ctx,
            mode,
            method,
            path,
            endpoint_key=endpoint_key,
            json_body=json_body,
            params=params,
            max_retries=max_retries,
        )

    async def request_prepared(
        self,
        ctx: _StoreContext,
        mode: GatewayMode,
        method: str,
        path: str,
        *,
        endpoint_key: str | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        max_retries: int = 0,
        gate_max_wait: float | None = None,
    ) -> GatewayResponse:
        """已 prepare 上下文的纯网络发包（无 session——RS-03b 三段式 HTTP 段）。

        gate_max_wait：限流闸最长等待秒数（None=端点默认 period×1.1）。同步批量
        写路径（如 price_push）用小值——配额耗尽时快速拒绝（GATEWAY_RATE_EXCEEDED，
        零字节出门），而非在请求内长睡等 token。
        """
        method = method.upper()
        url = f"{get_settings().channel_base_url}{path}"
        ep = endpoint_key or f"{method} {path.split('?', maxsplit=1)[0]}"

        if mode == "dry_run":
            snapshot = {
                "mode": "dry_run",
                "store": ctx.code,
                "method": method,
                "url": url,
                "endpoint_key": ep,
                "params": params,
                "json_body": json_body,
                "proxy": "<bound>" if ctx.proxy_url else None,  # 不泄漏代理地址
                "headers": [
                    "Authorization",
                    "WM_SVC.NAME",
                    "WM_QOS.CORRELATION_ID",
                    "WM_CONSUMER.ID",
                    "WM_SEC.ACCESS_TOKEN",
                    "Accept",
                ],
            }
            log.info("gateway.dry_run", **{k: v for k, v in snapshot.items() if k != "json_body"})
            return GatewayResponse(
                status=None, headers={}, data=None, dry_run=True, request_snapshot=snapshot
            )

        # 真实发包：限流闸 → token → 请求（401 自愈 / 429 5xx opt-in 退避 / 连接自愈）
        waited = await registry.gate(ctx.code, ep, max_wait=gate_max_wait)
        if waited < 0:
            raise BusinessError("GATEWAY_RATE_EXCEEDED", f"限流窗口内无法取得配额：{ep}")

        token = await self._token(ctx)
        attempt = 0
        refreshed_401 = False
        while True:
            client = self._client(ctx.proxy_url)
            try:
                resp = await client.request(
                    method,
                    url,
                    headers=self._headers(token, ctx.client_id),
                    json=json_body,
                    params=params,
                )
            except (httpx.TransportError, httpx.ProxyError) as exc:
                await self._invalidate_client(ctx.proxy_url)
                log.warning("gateway.transport_error", store=ctx.code, url=url, error=str(exc))
                if attempt < max_retries:
                    attempt += 1
                    await asyncio.sleep(min(2**attempt, 10))
                    continue
                return GatewayResponse(status=None, headers={}, data=None)

            status = resp.status_code
            headers = {k.lower(): v for k, v in resp.headers.items()}
            registry.update_from_walmart_headers(ctx.code, ep, headers)

            if status == 401 and not refreshed_401:
                token = await self._token(ctx, force_refresh=True)
                refreshed_401 = True
                log.warning("gateway.401_token_refreshed", store=ctx.code, url=url)
                continue

            if attempt < max_retries:
                if status == 429:
                    wait = parse_retry_after(headers)
                    log.warning("gateway.429_backoff", store=ctx.code, url=url, wait_s=wait)
                    await asyncio.sleep(wait)
                    attempt += 1
                    continue
                if 500 <= status < 600:
                    wait = min(2**attempt, 10)
                    log.warning("gateway.5xx_backoff", store=ctx.code, url=url, status=status)
                    await asyncio.sleep(wait)
                    attempt += 1
                    continue

            data: dict[str, Any] | None = None
            if 200 <= status < 300 and status != 204 and resp.content:
                try:
                    data = resp.json()
                except Exception as exc:
                    log.warning(
                        "gateway.json_decode_failed", store=ctx.code, url=url, error=str(exc)
                    )
            elif status >= 300:
                log.info("gateway.non_2xx", store=ctx.code, method=method, url=url, status=status)
            return GatewayResponse(status=status, headers=headers, data=data)


gateway = WalmartGateway()
