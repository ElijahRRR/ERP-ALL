# 008 前端开发规范（页面框架 / 组件 / 命名 / 新增功能上车协议）

> **定位（Owner 2026-07-24 澄清后明确）**：当前 13 页是开发期功能件，正式前端
> 产出在 FE-DESIGN（Claude Design，Owner 触发制，007 有案）。**本规范不打磨
> 临时页面的门面**——视觉/组件美化一概不管；它约束的是**会活到正式前端的层**
> （数据层 client.ts、契约 codegen、权限注册、对账可见性、上车协议）和**过渡期
> 最低纪律**（新增页面不添新债，否则 FE-DESIGN 时换壳成本被债务放大）。
>
> 背景：Owner 2026-07-24 关切"后期加功能胡乱硬塞，功能实现了但对账和运维困难"。
> 本规范由审计工作区依**现状调研**成文（固化既成好模式 + 堵已发现裂缝，非凭空立法）。
> 现状基础（2026-07-24 核实）：CI 已卡 eslint + tsc + vite build；13/13 页面全部
> 走统一类型化客户端 `api/client.ts`（零散落 fetch/axios）；契约 codegen
> `pnpm gen:api`（openapi-v0.yaml → schema.d.ts）；前端测试 0 个。

## 1 目录与命名（固化既成模式）

- `pages/XxxPage.tsx`：路由级页面，PascalCase + `Page` 后缀，一页一文件；
  页面超 ~400 行必须拆子组件进 `pages/xxx/` 子目录，禁止单文件膨胀；
- `components/`：跨页共享组件（PascalCase）；`layout/` 布局；`auth/` 会话；
  `api/` 唯一数据层（`client.ts` + codegen 产物 `schema.d.ts`）；
- hooks `useXxx`；常量 `UPPER_SNAKE`；事件处理函数 `handleXxx`，回调 props `onXxx`。

## 2 数据层纪律（防硬塞第一道闸）

1. 所有请求必须走 `api/client.ts`（统一 Bearer/刷新重试/ApiError 错误信封）。
   页面/组件内禁止 `fetch`/`axios`。现状达标 13/13，保持。
2. **响应类型必须取自 `schema.d.ts`（契约 codegen），禁止页面手写 interface。**
   ⚠️ 审计已发现现行违例（**FE-DEBT-01**）：StoresPage 等页内联手写 `Store`/
   `ProxyItem` 等响应类型——与契约无编译期绑定，后端改字段前端不报错，属
   "悄悄漂移"裂缝。处置：新代码即日禁止；存量页**随下一次触碰该页时替换**，
   FE-DESIGN 启动前清零。
3. 契约变更流程只有一条：改 `openapi-v0.yaml` → `pnpm gen:api` → tsc 编译期
   暴露不兼容。禁止先改前端再补契约。

## 3 页面标准框架（每页必备四件）

1. **权限**：路由 + 菜单 + `useAuth().has(权限点)` 三处注册；写操作按钮同样门控；
2. **三态齐全**：loading / error（透出 `ApiError.message`，禁止吞错误）/ 空态；
3. **分页**：列表统一 `PageOf<T>` 服务端分页，禁止一次拉全量；
4. **业务规则零前端**：校验以后端为权威，前端只做输入体验；操作留痕靠后端
   `audit_log`（八域已接），前端不自造日志。

## 4 组件规范

- 展示组件不发请求（数据经 props 传入）；允许发请求的只有页面容器和显式数据
  组件（现状仅 NotificationBell / TeamSwitcher，新增须在 PR 里说明理由）；
- props 显式类型，禁 `any`（eslint 已卡）；
- 复用 ≥2 页才提升进 `components/`，否则留在页面内——防过度抽象。

## 5 新增功能上车清单（防"胡乱硬塞"协议）

新页面/新功能的 PR 必须逐项齐备，审计侧按此清单做 PR 检查，违例记 FE-DEBT
编号追踪：

1. 契约先行（openapi 更新 + gen:api 产物同 PR）；
2. 路由 + 菜单 + 权限点三处注册；
3. 三态 + 服务端分页齐全；
4. 类型全部来自 codegen（零手写响应类型）；
5. 行为有运维面的，域 runbook 同 PR 附上（范例：R2-11 变体组运维 runbook）；
6. CI lint + tsc + build 绿。

## 6 对账与运维可见性底线（Owner 关切直译）

- 凡有"渠道↔本地"对账语义的域（feed / 结算 / 退货 / 全店 SKU 对账），页面
  **必须暴露对账状态列与未匹配项入口**，禁止只展示成功面；
- 错误必须透出 `listing_error_catalog` 的分类与处置建议，不显示裸错误码；
- 后台任务（beat / 维护任务 / 导入作业）状态页面可见——运维不靠查库。

## 7 测试底线（务实分级，不铺表面覆盖）

- 现状 0。底线（**FE-TEST-01**，FE-DESIGN 启动前完成）：`api/client.ts`
  （token 刷新 / 错误信封 / 401 单次重试）+ ≥1 个交互密集页关键流的单测先行；
- FE-DESIGN 重写视觉层时新组件带测试进场，存量页随替随补——不给旧壳补妆。

## 8 生效与执行

本文自入库起对所有前端 PR 生效；开发侧异议走批注回传（同 007 通道），不直接
改本文。执法三层：CI（lint/tsc/build）→ 审计侧按 §5 清单查 PR → FE-DEBT
台账追踪清偿。
