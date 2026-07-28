// 展示文案映射（**不是业务参数**：flow 集合与合法档位一律由 API 驱动，这里只放中文标签）。
// 权威是 specs/001-domain-model/09-platform.md 的注册清单；本表只负责把 code 翻成中文，
// 查不到时页面回落显示原始 code（新增 flow 忘了补中文只会显示英文码，不会漏行）。
export const FLOW_LABEL: Record<string, string> = {
  scrape_to_audit: '采集→送审',
  audit_to_listing: '审核→上架',
  listing_dispatch: '上架派发',
  pricing_watch: '盯价改价',
  order_block: '订单闸（四检拦截）',
  compliance_block: '合规闸（黑名单拦截）',
  refund: '退款执行',
  cancel: '取消执行',
  purchase_execute: '采购下发',
  maintenance_run: '维护任务执行',
}

export const MODE_LABEL: Record<string, string> = {
  manual: '人工',
  semi: '半自动',
  auto: '全自动',
}

export const MODE_TAG_COLOR: Record<string, string> = {
  manual: 'default',
  semi: 'blue',
  auto: 'green',
}

export const EVALUATION_LABEL: Record<string, { color: string; label: string; tip: string }> = {
  realtime: { color: 'geekblue', label: '实时求值', tip: '切档对下一次决策即生效' },
  snapshot: {
    color: 'purple',
    label: '创建快照',
    tip: '档位在请求创建时固化，切档不影响在途请求',
  },
}

// 「哪些是闸类」不再在前端硬编码：由 API 的 `gate` 字段（服务端从 FlowSpec 派生）驱动。
// 此前这里有一个 GATE_FLOWS 硬编码清单——§09 改判闸类时它不会跟着变，确认框和红字
// 会静默失灵（审查 2026-07-28 F4），已删。本文件只放展示文案。
