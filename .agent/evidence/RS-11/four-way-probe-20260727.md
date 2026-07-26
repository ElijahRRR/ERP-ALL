# RS-11 契约四向一致性：首次全量探测（2026-07-27）

> 只读探测，未改任何实现（唯一例外：`core/authn.py` 加一行 `_check.erp_permission = permission`
> 使权限码可内省——不改行为）。门禁代码下轮落。

## 判据来源

RS-11 的 `acceptance` 原文：

> **CI自动四向校验：OpenAPI operation ↔ 实际路由 ↔ x-permission ↔ permission seed**；
> superseded_by 标注；NOT VALID→VALIDATE 大表 CHECK 纪律入 00-conventions；前端调用覆盖先做静态清单

本文只做第一句（四向校验）的探测。后三项归属见 §5。

## 1 四个面怎么取

| 面 | 来源 | 取法 |
|---|---|---|
| 契约 operation | `specs/002-api-contract/openapi-v0.yaml` | yaml 解析 `paths.*.{get,post,put,patch,delete}` |
| `x-permission` | 同上 | operation 的 `x-permission` 扩展字段 |
| 实际路由 | `erp.main.create_app()` | **须递归 `_IncludedRouter.original_router.routes`**——FastAPI 把 `include_router` 的结果包成该内部类，直接遍历 `app.routes` 只能拿到 5 条 |
| 路由权限码 | 同上 | 依赖函数上的 `erp_permission` 属性（本轮新加）。**刻意不反查 `__closure__`**——那依赖闭包变量顺序，改个形参就静默失效 |
| permission seed | 迁移建的 `app.permission` 表 | `SELECT code FROM app.permission` |

路径归一：契约用 `{teamId}` 而代码用 `{team_id}`，匹配前把占位符统一成 `{}`。
（命名风格不一致本身是另一处小漂移，见 §4。）

## 2 探测结果

**契约 118 operation（106 带 x-permission） | 实际路由 112（97 带权限） | 种子权限码 53**

| 检查 | 结果 | 判定 |
|---|---|---|
| **A** 代码里 `require_permission` 的码 → 是否都在种子里 | **0 例外** | ✅ |
| **B** 契约 `x-permission` → 是否都在种子里 | **0 例外** | ✅ |
| **E** 两边都有的 operation，权限码是否一致 | **0 不一致** | ✅ |
| **C** 契约有、路由无 | **15** | 见 §3 |
| **D** 路由有、契约无 | **9** | 见 §3 |

> A/B/E 全清，说明 `require_permission` docstring 那句「权限码与 002 契约 x-permission **一字不差**」
> **经得起全量检验**——此前只是口头约定、从无强制手段，这是第一次被机器验证。

## 3 C/D 两类漂移的分类（关键：不是所有差异都算错）

### C 契约有而路由无（15）——**两种性质，必须分开**

| 归属 | 条目 | 性质 |
|---|---|---|
| **Portal（6 条）** | `POST /portal/auth/login`、`GET /portal/procurement-orders`(+`/{}`)、`POST .../claim`、`.../backfill`、`.../exception` | **前置声明，非漂移**——门户 router 全仓未挂载，属 **R2-10 采购方门户对外【L2】**（status=`todo`）。D-Q50 双入口的外侧尚未开工 |
| **Catalog（5 条）** | `GET/PATCH /category-map`、`GET/POST /products/{}/sources`、`PATCH /products/{}` | 待核：类目映射与货源录入端点。与阶段一查出的 `catalog.source_write`（货源录入）、`catalog.category_write`（类目映射修正）两个权限码呼应——**权限码已种、端点未建** |
| **Listing（4 条）** | `GET/PATCH /listing-errors`、`GET/POST /maintenance-tasks` | 待核：错误字典与维护任务端点。同样与 `listing.error_admin`（错误字典维护）权限码呼应 |

**这解释了阶段一那个发现的另一半**：`catalog.source_write` / `catalog.category_write` /
`listing.error_admin` 三个权限码之所以「无角色可达」，是因为**它们的端点本来就还没建**——
契约先声明、代码后实现，权限码随契约种下但功能未上线。0039 已把它们授给团管，属**提前授权**，
不影响正确性（端点建成即可用），但值得在 R2-08/后续工单里注意。

### D 路由有而契约无（9）——**真欠账**

| 条目 | 说明 |
|---|---|
| `GET /notifications`、`GET /notifications/unread-count`、`POST /notifications/read-all`、`POST /notifications/{}/read` | 通知中心四个端点，**前端在用**（通知铃/通知中心页），契约里没有 |
| `POST /worker/v1/register`、`/sync`、`GET /tasks/pull`、`POST /tasks/release`、`/tasks/result` | 采集 worker 五个端点。**路径形态可疑**：`worker_router` 自带 `prefix="/worker/v1"`（`scrape/router.py:26`）又被挂在 `/api/v1` 下，叠成 `/api/v1/worker/v1/...` 的**双版本号**路径 |

**这 9 条是契约的真实欠账**——代码先行、契约没跟上，正是 RS-11 要清的漂移。

## 4 顺带发现的两处小漂移

1. **路径参数命名风格不一致**：契约用 camelCase（`{teamId}`、`{jobId}`），代码用 snake_case
   （`{team_id}`、`{job_id}`）。功能等价，但 codegen 出的前端类型会带契约的命名，
   与后端日志/错误信息里的命名对不上，排查时容易多绕一层。
2. **`/api/v1/worker/v1/...` 双版本号**：router 自带前缀 + 挂载前缀叠加所致。改路径是破坏性的
   （采集 worker 已在跑），**不建议现在动**；但契约补登记时应如实写这个路径，别写成
   `/worker/v1/...` 造成新的对不上。

## 5 RS-11 其余三项的归属

| 子项 | 归属 | 状态 |
|---|---|---|
| 契约四向一致性检查 | **云端 AI（我）** | 本轮探测完成，门禁代码下轮落 |
| `superseded_by` 标注、D-Q→文档→工单追踪列 | 动 `DECISION-FORM.md`＝**宪法**（铁律 1） | **需 Owner 批准**后由规划/审查 AI 落笔 |
| NOT VALID→VALIDATE 大表 CHECK 纪律入 `00-conventions` | `specs/001-domain-model/` ＝ 图纸 | **归规划/审查 AI** |
| 001 财务域 immutable ledger 图纸修 | — | ✅ **已由审计侧 `421f83d` 完成，可核销** |

## 6 门禁设计要点（下轮实现时照此）

1. **A/B/E 三条设为硬断言**——现状全清，任何新增违例即真漏洞。
2. **C 需要「前置声明白名单」**：契约超前于未建工单是**正常**的（Portal 属 R2-10 未开工），
   不能判红。白名单每条须写明**归属工单**，且加一条反向不变量：**工单一旦 accepted，
   其白名单条目必须清空**——否则白名单会变成永久豁免。
3. **D 设为硬断言**（现状 9 例外先入白名单并登记为欠账），新增路由必须同时登记契约。
4. 路径归一化必须做（占位符统一），否则命名风格差异会造成满屏假阳性。
5. **不要用 `__closure__` 反查权限码**——已在 `authn.py` 注释里写明理由。
