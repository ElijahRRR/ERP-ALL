FROM python:3.12-slim AS base

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

# 运行角色由 compose/部署覆盖 command 决定：
#   api:    uvicorn erp.main:app --host 0.0.0.0 --port 8000
#   worker: (R1-09 起) python -m erp.worker
#   beat:   (R1-06 起) python -m erp.beat
CMD ["uvicorn", "erp.main:app", "--host", "0.0.0.0", "--port", "8000"]
