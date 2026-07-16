"""API entrypoint（运行角色之一；worker/beat 有各自 entrypoint，共享同一代码库）。"""

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

from erp import __version__
from erp.audit.router import audit_router
from erp.catalog.router import catalog_router
from erp.channel.router import channel_router
from erp.compliance.router import compliance_router
from erp.core.authn import AuthError, PermissionDenied
from erp.core.config_service import get_config_service, run_invalidation_subscriber
from erp.core.errors import BusinessError
from erp.core.logging import setup_logging
from erp.core.settings import get_settings
from erp.identity.router import auth_router, identity_router
from erp.listing.router import listing_router
from erp.notify.router import notify_router
from erp.order.router import order_router
from erp.pricing.router import pricing_router
from erp.scrape.router import scrape_router, worker_router

log = structlog.get_logger()

__all__ = ["BusinessError", "app", "create_app"]


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    # 配置失效订阅（R2-04 pubsub 广播；fail-open——Redis 不可用只降级为 TTL 收敛）
    subscriber = asyncio.create_task(run_invalidation_subscriber(get_config_service()))
    try:
        yield
    finally:
        subscriber.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await subscriber


def create_app() -> FastAPI:
    setup_logging()
    settings = get_settings()
    app = FastAPI(
        title="ERP-ALL API",
        version=__version__,
        docs_url="/api/docs" if settings.env != "prod" else None,
        lifespan=_lifespan,
    )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request.state.request_id = uuid.uuid4().hex
        structlog.contextvars.bind_contextvars(request_id=request.state.request_id)
        try:
            response = await call_next(request)
        finally:
            structlog.contextvars.unbind_contextvars("request_id")
        response.headers["X-Request-Id"] = request.state.request_id
        return response

    def _envelope(
        request: Request, status: int, code: str, message: str, detail: Any
    ) -> JSONResponse:
        # 统一错误信封（002 契约 §全局约定 4）
        return JSONResponse(
            status_code=status,
            content={
                "error": {
                    "code": code,
                    "message": message,
                    "detail": detail,
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.exception_handler(BusinessError)
    async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
        return _envelope(request, exc.http_status, exc.code, exc.message, exc.detail)

    @app.exception_handler(AuthError)
    async def auth_error_handler(request: Request, exc: AuthError) -> JSONResponse:
        return _envelope(request, 401, "UNAUTHENTICATED", exc.message, {})

    @app.exception_handler(PermissionDenied)
    async def permission_denied_handler(request: Request, exc: PermissionDenied) -> JSONResponse:
        return _envelope(
            request, 403, "PERMISSION_DENIED", "缺少所需权限", {"permission": exc.permission}
        )

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        """存活探针：进程在即 200，不查依赖。"""
        return {"status": "ok", "version": __version__}

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(identity_router, prefix="/api/v1")
    app.include_router(notify_router, prefix="/api/v1")
    app.include_router(channel_router, prefix="/api/v1")
    app.include_router(scrape_router, prefix="/api/v1")
    app.include_router(worker_router, prefix="/api/v1")
    app.include_router(audit_router, prefix="/api/v1")
    app.include_router(listing_router, prefix="/api/v1")
    app.include_router(order_router, prefix="/api/v1")
    app.include_router(pricing_router, prefix="/api/v1")
    app.include_router(catalog_router, prefix="/api/v1")
    app.include_router(compliance_router, prefix="/api/v1")

    return app


app = create_app()
