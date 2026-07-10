"""结构化日志（structlog → JSON 行，dev 可切彩色控制台）。

约定：业务代码只用 structlog.get_logger()；request_id 由 API 中间件绑定，
渠道调用/任务运行等关键路径必须带上下文键（store_id/task_code/...）。
"""

import logging

import structlog

from erp.core.settings import get_settings


def setup_logging() -> None:
    settings = get_settings()
    renderer: structlog.types.Processor
    if settings.log_json:
        renderer = structlog.processors.JSONRenderer(ensure_ascii=False)
    else:
        renderer = structlog.dev.ConsoleRenderer()

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.DEBUG if settings.debug else logging.INFO
        ),
        cache_logger_on_first_use=True,
    )
