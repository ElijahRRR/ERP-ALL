# 常用入口（详见 specs/003-r1-plan）
#
# 自 RS-02a 起 compose 的口令全部来自 infra/.env（先 `cp infra/.env.example infra/.env`
# 并填值）。这里**不写** --env-file：实测（2026-07-27，docker compose v2）无论从仓库根、
# infra/ 内还是任意目录带绝对路径跑，compose 都按「compose 文件所在目录」找 .env，
# 三种跑法都能取到；反倒是加了 --env-file 会把命令绑死在某个 cwd 上。
# 文件缺失时 compose 直接报「required variable ... is missing a value」并退出——fail-closed。
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
