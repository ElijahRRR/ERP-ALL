# workers/ — 本地采集 worker（Amazon 商品详情）

采集 worker 在 **Owner 本地机器** 运行、出站拨入云端/部署机 api 领任务（D-Q47），
独立打包、独立发版。移植源：amazon-scraper-v3（D-Q42），考古对照见
[`.agent/evidence/R2-01/archaeology.md`](../.agent/evidence/R2-01/archaeology.md)。
拨入协议（注册/心跳/领任务/回传）规格见 `specs/002-api-contract/worker-protocol.md`。

## 它做什么

从 ERP `/worker/v1` 领 `product_detail` 任务 → curl_cffi 模拟浏览器 TLS 指纹真抓
Amazon 商品页 → 多层 fallback 解析（标题/品牌/图片/五点/价格/类目/…）→ 结构化
回传入产品库。内置 AIMD 自适应并发、session 指纹轮换、被封/验证码/降级页分级处置。

> **安全红线**：worker 抓的是 Amazon（选品来源），**从不碰 Walmart API**。
> 每台 worker 绑定固定 TPS 代理出口，反关联。凭证只进本机，永不入库/入 git。

## 前置

- 一个 **TPS 代理**（每请求自动换出口 IP，帐密制 `http://user:pwd@host:port`）——同旧系统，Owner 提供。
- 部署机上 `system_config.scrape.worker_enroll_token` 已设置（注册令牌，见下）。
- 能出站访问部署机 api（同机=`http://api:8000`，跨机=`http://<部署机IP>:8000`）。

### 设置注册令牌（部署机，一次性）

```bash
# 在部署机上，给 worker 注册用的令牌（自定义高熵字符串）
docker compose -f infra/docker-compose.yml exec db \
  psql -U postgres -d erp_all -c \
  "INSERT INTO app.system_config(key,value) VALUES('scrape.worker_enroll_token', to_jsonb('随机长字符串'::text))
   ON CONFLICT (key) DO UPDATE SET value = excluded.value;"
```

## 跑法 A：Docker（推荐，与部署机同机）

```bash
# 在部署机仓库根，把令牌与代理传进去（不落 git）
ERP_WORKER_ENROLL_TOKEN='上一步的令牌' \
PROXY_URL='http://user:pwd@tps-host:port' \
  docker compose -f infra/docker-compose.yml --profile scraper up -d --build scraper

docker compose -f infra/docker-compose.yml logs -f scraper   # 看采集日志
```

机器令牌存 `scraper_state` 卷，重启复用同一节点身份。

## 跑法 B：本机 Python（跨机 / 不想上 Docker）

```bash
cd workers
uv sync                      # 装依赖（curl_cffi/selectolax/lxml/httpx）
uv run python -m erp_worker.run \
  --server http://<部署机IP>:8000 \
  --enroll-token '令牌' \
  --proxy http://user:pwd@tps-host:port
```

参数也可用环境变量：`ERP_SERVER` / `ERP_WORKER_ENROLL_TOKEN` / `PROXY_URL` /
`ERP_WORKER_NODE_KEY`。`node-key` 缺省 = `erp-<主机名>`，重启复用（令牌存 `~/.erp_worker/`）。

## 开发

```bash
cd workers
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
uv run pytest
```

代码分三层（详见 archaeology）：
- **vendored 逐字移植**：`parser/session/adaptive/metrics/proxy`（ruff/mypy 排除，保持对齐上游，勿手改）
- **协议胶水**：`erp_client`（`/worker/v1` 客户端）、`payload`（parser dict → product payload）
- **编排壳**：`engine`（流水线+分级处置）、`run`（CLI）、`config`（调参）

沙盒不真抓 Amazon（宪法禁）；测试全离线：payload 适配 / 协议 MockTransport / 引擎分级处置。
真抓验收在 Owner 机器执行（见 R2-01 runbook）。
