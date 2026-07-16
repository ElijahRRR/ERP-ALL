# 常用入口（详见 specs/003-r1-plan）
COMPOSE = docker compose -f infra/docker-compose.yml

up:            ## 起全栈（db+redis+migrate+api+beat+frontend 生产静态伺服）
	$(COMPOSE) up -d --build db redis migrate api beat frontend

fe-dev:        ## 本地开发用 vite HMR（与生产 frontend 共用 5173，先停后起）
	$(COMPOSE) stop frontend
	$(COMPOSE) --profile dev up -d frontend-dev

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f --tail=100

lint:
	cd backend && uv run ruff check . && uv run ruff format --check . && uv run mypy

test:
	cd backend && uv run pytest

fe-build:
	cd frontend && pnpm install && pnpm build

.PHONY: up fe-dev down logs lint test fe-build
