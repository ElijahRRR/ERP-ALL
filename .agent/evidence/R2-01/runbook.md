# R2-01 采集引擎 L1 验收手册

> 数据真实性等级 **L1（真实只读）**：worker 真抓 Amazon，只读入产品库，不写任何渠道。
> 需要 Owner 提供 **TPS 代理** + 部署机就绪。沙盒不真抓（宪法禁），故此单在 Owner 机器验收。

## 前置（各一次）

1. **设注册令牌**（部署机）：
   ```bash
   docker compose -f infra/docker-compose.yml exec db psql -U postgres -d erp_all -c \
     "INSERT INTO app.system_config(key,value) VALUES('scrape.worker_enroll_token', to_jsonb('自定义令牌'::text))
      ON CONFLICT (key) DO UPDATE SET value = excluded.value;"
   ```
2. **备好 TPS 代理地址**：`http://user:pwd@host:port`（同旧系统的代理）。

## 步骤

### ① 建采集作业（前端）
顶栏切到作用团队 → 采集作业 → 新建 → 填 **≥10 个真实 ASIN**（选品意向里的真货）→ 提交。
作业应为 pending（此前无 worker 时会卡在这——现在有 worker 了）。

### ② 起 worker（部署机）
```bash
ERP_WORKER_ENROLL_TOKEN='①的令牌' PROXY_URL='你的TPS代理' \
  docker compose -f infra/docker-compose.yml --profile scraper up -d --build scraper
docker compose -f infra/docker-compose.yml logs -f scraper
```
日志应见：`注册成功` →（心跳）→ `OK B0XXXX → M000000N | 标题…` 逐条刷出。

**链路调参（2026-07-15 A152 实测教训：默认 15s 超时按源仓直连口径调校，
TPS 代理链路整页抓取常态更慢 → 全量超时）**。两种下发方式任选：

- 环境变量（起 worker 时带上）：`REQUEST_TIMEOUT=45 PROXY_BANDWIDTH_MBPS=<套餐Mbps>`
- 配置中心（运行期热生效，worker 30s 内收到并自动轮换 session）：
  ```sql
  INSERT INTO app.system_config(key, value) VALUES('scrape.worker_settings',
    '{"request_timeout": 45, "proxy_bandwidth_mbps": 10}'::jsonb)
  ON CONFLICT (key) DO UPDATE SET value = excluded.value;
  ```
  支持键：`proxy_url` / `request_timeout`（秒）/ `proxy_bandwidth_mbps`（0=不限）/
  `max_concurrency` / `min_concurrency` / `token_bucket_rate`（QPS）/
  `max_retries` / `session_rotate_every`。
  声明真实代理带宽后，AIMD 会在用量达 80% 软顶时自动背压，避免并发把链路挤到超时。

### ③ 看产品库（前端）
产品库页应出现 ≥10 条真实产品。**点标题或点「详情」按钮**打开产品详情抽屉，确认字段完整：
- **标题**（真实商品名，非 Demo）· **品牌** · **类目**
- **图片**（画廊，可点击放大）
- **价格**（当前价/BuyBox/原价/运费/FBA）
- **五点描述**（bullets 列表）
- **其他采集字段**（型号/制造商/评分/评论数/卖家/库存/UPC 等）

> 详情抽屉随 e7bc6a0 补齐——此前表格只有 SKU/ASIN/标题/品牌/状态，无处点开（数据一直在库里）。

### ④ 与旧系统对照（判据核心）
取其中 3-5 个 ASIN，在旧系统（amazon-scraper-v3）同样抓一遍，比对标题/品牌/价格/类目
是否一致（价格允许时间差微小波动）。**一致 = 解析保真**。

### ⑤ 失败任务回收（判据核心）
故意混 1-2 个不存在或已下架的 ASIN。观察：
- 不存在 → 产品标记 `[商品不存在]` 或任务 dead（达重试上限），作业计数 failed +1；
- worker 中途 `docker stop scraper` 再 `up` → 未完成任务被 server 回收重派（不永久卡 dispatched）。

## 通过判据（L1）

- [ ] ≥10 个真实 ASIN 入产品库，五要素字段完整
- [ ] 抽样与旧系统对照字段一致
- [ ] 失败/下架 ASIN 正确终态；worker 断连后任务被回收重派

三项全过 → 回复「R2-01 验收通过」，随即启动 **R2-02 审核弹药灌入**。

## 沙盒侧已交付（本单开发证据）

- 26 离线单测全绿：`payload` 适配（parser dict→product payload）/ `erp_client` 协议
  （httpx MockTransport：注册令牌持久化+复用、领任务认证头、租约回传形态、stale 透传、
  服务器错误→None）/ `engine` 分级处置（成功租约回传、被封、variant 偏移终态、404 占位、
  空标题耗尽、降级页、下线归还）。
- CLI 可启动（`python -m erp_worker.run --help`）+ parser 离线解析冒烟通过。
- ruff / ruff format / mypy 全绿；新增 CI `workers` job。
