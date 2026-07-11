# ERP 采集 worker 镜像（本地机器运行，出站拨入 ERP api）。
# 移植自 amazon-scraper-v3；curl_cffi/selectolax 有 manylinux wheel，slim 即可。
FROM python:3.12-slim

# uv：与 backend 同源的依赖管理
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

# 先装依赖（利用层缓存），再拷源码
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev
COPY src ./src
RUN uv sync --frozen --no-dev

# 机器令牌持久化目录（挂卷保留，重启复用节点身份）
VOLUME ["/root/.erp_worker"]

ENTRYPOINT ["python", "-m", "erp_worker.run"]
