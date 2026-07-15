# 常用入口（详见 specs/003-r1-plan）
COMPOSE = docker compose -f infra/docker-compose.yml

up:            ## 起全栈（db+redis+migrate+api+beat）
	$(COMPOSE) up -d --build db redis migrate api beat

up-full:       ## 含前端 dev server
	$(COMPOSE) --profile dev up -d --build

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

.PHONY: up up-full down logs lint test fe-build
