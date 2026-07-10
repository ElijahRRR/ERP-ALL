# 002 API 契约草案 v0（EA-002）

- 日期：2026-07-10 · 作者：ar 帽 · 状态：**草案**（EA-001 验收后随其修订并冻结为 v1）
- 源：specs/001-domain-model/（表→资源映射）+ PRD §5 四条端到端流程
- 范围：五个核心上下文先行 —— identity / channel / catalog / listing / order（含采购门户）。audit/compliance/finance/platform 契约随 R2 对应迭代补齐。
- 文件：[`openapi-v0.yaml`](openapi-v0.yaml)（OpenAPI 3.1，可直接喂 codegen / swagger-ui）

## 全局约定（fe/be 共同遵守，违约 CR 打回）

1. **基路径与版本**：内部 `/api/v1`；采购门户 `/portal/v1`（独立路由树 + 独立 JWT audience，D-Q50③；两者在同一 YAML 内以 tag=Portal 区分）。
2. **认证**：Bearer JWT。内部 `audience=erp`，门户 `audience=portal`；互不接受。刷新走 `/auth/refresh`。
3. **团队作用域隐式**：所有列表/详情自动限定当前用户 team（服务层+RLS 双保险）；**API 不接受 team_id 参数**（超管跨团队用 `X-Act-Team` 头，仅 is_super 生效并记 audit_log）。
4. **错误信封**（唯一格式）：
   ```json
   {"error": {"code": "LISTING_QUOTA_EXCEEDED", "message": "今日上架配额已用完", "detail": {...}, "request_id": "..."}}
   ```
   HTTP 语义：400 参数 / 401 未认证 / 403 无权限点 / 404 不存在或不属于本团队（**不区分**，防探测）/ 409 状态冲突 / 422 业务规则拒绝 / 429 限流。
5. **分页**（统一 offset 型）：`?page=1&size=50`（size≤200）→ `{"items": [...], "total": n, "page": 1, "size": 50}`。大表（feed_item 等）另提供 `created_after` 游标参数。
6. **写操作幂等**：批量提交类接口必带 `Idempotency-Key` 头（服务端 24h 去重）；渠道写路径（submit/delist/ship/refund-execute）全部异步——返回 202 + 任务/feed 引用，前端轮询或收通知。
7. **枚举取值**：与 001 数据字典的 CHECK 清单一字不差；sys_dict 类取值由 `GET /dicts/{type}` 下发，前端不硬编码。
8. **时间**：API 一律 ISO8601 UTC；前端本地化展示。
9. **权限点标注**：每个 operation 的 `x-permission` 字段声明所需权限码（permission.code），网关中间件按此校验——契约即权限矩阵。

## 端点清单（v0 覆盖面）

| 上下文 | 资源/动作 | 说明 |
|---|---|---|
| auth | login / refresh / logout / me | 内部与门户各一套 |
| identity | teams（超管）/ users / roles(+permissions 绑定) / permissions / audit-logs | |
| channel | stores(+credential 写入/状态、proxy 绑定、quota 配置与用量) / proxies / store-incidents | |
| catalog | products / variant-groups / gtin-pool(统计+清单) / import-jobs / category-map / product-sources / brand-assignments | |
| listing | listings（allocate/submit/delist/retry 动作）/ feeds(+items) / pricing-strategies / maintenance-tasks / listing-errors | |
| order | orders(+lines/checks) / procurement-orders（assign/claim/backfill/exception）/ purchasers / ship | |
| portal | 门户 login / 我的执行单 list+detail / claim / backfill / exception | 白名单式最小面 |

## 有意不做的（防蔓延）

- 无通用 CRUD 生成：只开业务需要的动作端点；listing/order 的状态迁移全部走**动作端点**（`POST /listings/{id}/delist`），不开放裸 PATCH status。
- 凭证类（store credential / proxy password / mailbox password）只写不读：GET 只回「已配置+更新时间+掩码」。
- v0 不含：audit 工作台、compliance 管理、finance 报表、mail、notification 中心、scraping 管理——随 R2 迭代逐个补 tag，遵守本 README 全局约定。

## 待 EA-001 验收联动的点

- listing 去重豁免的报错码（`LISTING_DUP_IN_TEAM`）依赖开放点 4 的确认。
- import-jobs 的 domain 枚举与 001 §import_job 保持同步。
