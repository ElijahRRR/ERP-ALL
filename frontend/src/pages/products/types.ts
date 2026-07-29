// 产品页面内响应类型。
//
// ⚠️ **FE-DEBT-01 残留，交底如下**（008§2 要求响应类型一律取自 codegen）：本文件里的
// 四个 interface **暂时无法**换成 `Schemas['Product']` 等 codegen 类型，原因是契约侧
// 缺口而非前端偷懒——
//   1. `openapi-v0.yaml` 的 `Product` 全字段可选（`id?: number`），当成列表行类型用会
//      让每一处访问都要判空，反而把真实的必填语义抹掉；
//   2. 且它**没有** `latest_audit_run_id` / `price_snapshot` / `updated_at`，而这三个
//      字段本页在用（审核详情入口、价格块、详情抽屉）——照 codegen 类型写会直接编译不过；
//   3. `GET /audit-runs/{runId}` 在契约里只有 `{'200': {description: OK}}`，**没有
//      任何 schema**，`AuditRunDetail` 无从生成。
// 清偿路径：这三条都要改 `specs/002-api-contract/openapi-v0.yaml`（补 Product 缺字段 +
// 给 audit-run 详情立 schema）。本单**没有**代改：补 Product 与立 audit-run 详情
// schema 会牵动 catalog 域的响应形状与另外几个消费页，属独立一单的活，不塞进批量
// 送审这一单。（早先这里写的理由是「本次工单范围限定只碰 frontend/」——那句**是错
// 的**，本单同时改了 backend/ 与 specs/，2026-07-28 审查按「源码注释里的失实自陈比
// PR 正文更糟：正文随 PR 归档，注释会一直骗下一个读代码的人」点名，已如实改正。）
//
// 对照：**本次新增的批量送审四个形状全部来自 codegen**（`AuditBatchResult` 等，见
// `api/client.ts` 的导出），新代码零手写响应类型这条已守住；单品送审 201 的
// `AuditResult` 本单已补进契约并在 `ProductsPage` 换成 codegen 类型。

export interface Product {
  id: number
  master_sku: string
  source_ref: string
  title: string
  brand: string | null
  status: string
  latest_audit_run_id: number | null
}

export interface AuditHit {
  level: string
  rule_code: string
  is_hard: boolean
  evidence: Record<string, unknown>
}

export interface AuditRunDetail {
  id: number
  verdict: string | null
  reject_level: string | null
  llm_cost_usd: number
  cache_hit_rate: number | null
  duration_ms: number | null
  hits: AuditHit[]
}

export interface StoreOpt {
  id: number
  code: string
  name: string
}

export interface ProductDetail {
  id: number
  master_sku: string
  source_channel: string
  source_ref: string
  title: string
  brand: string | null
  category_path: string | null
  images: string[] | null
  attrs: Record<string, unknown> | null
  price_snapshot: Record<string, unknown> | null
  status: string
  created_at: string
  updated_at: string
}
