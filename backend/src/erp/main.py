"""API entrypoint（运行角色之一；worker/beat 有各自 entrypoint，共享同一代码库）。"""

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from erp import __version__
from erp.core.logging import setup_logging
from erp.core.settings import get_settings

log = structlog.get_logger()


class BusinessError(Exception):
    """业务规则拒绝（HTTP 422）。code 使用大写蛇形错误码，进错误信封。"""

    def __init__(self, code: str, message: str, detail: dict[str, Any] | None = None):
        self.code = code
        self.message = message
        self.detail = detail or {}
        super().__init__(message)


def create_app() -> FastAPI:
    setup_logging()
    settings = get_settings()
    app = FastAPI(
        title="ERP-ALL API",
        version=__version__,
        docs_url="/api/docs" if settings.env != "prod" else None,
    )

    @app.exception_handler(BusinessError)
    async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
        # 统一错误信封（002 契约 §全局约定 4）
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "detail": exc.detail,
                    "request_id": getattr(request.state, "request_id", None),
                }
            },
        )

    @app.get("/healthz", tags=["ops"])
    async def healthz() -> dict[str, str]:
        """存活探针：进程在即 200，不查依赖。"""
        return {"status": "ok", "version": __version__}

    return app


app = create_app()
