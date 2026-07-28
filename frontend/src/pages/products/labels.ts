// 产品域展示文案与呈现常量（**不是业务规则**）。范式同 pages/automation/labels.ts：
// 本表只负责把 code 翻成中文，查不到时页面回落显示原始 code——新增码忘了补中文
// 只会显示英文，不会漏行、不会静默吞掉。

/** product.status → Tag 颜色（口径同 001 图纸的产品状态机） */
export const STATUS_COLOR: Record<string, string> = {
  ingested: 'default',
  auditing: 'processing',
  audit_passed: 'green',
  audit_rejected: 'red',
  needs_review: 'orange',
  sourcing: 'gold',
  ready: 'cyan',
  listed: 'blue',
  retired: 'default',
}

/** product.status → 中文（008§6：不给运营看裸英文码） */
export const STATUS_LABEL: Record<string, string> = {
  ingested: '已采集',
  auditing: '审核中',
  audit_passed: '审核通过',
  audit_rejected: '审核拒绝',
  needs_review: '待人工复核',
  sourcing: '寻源中',
  ready: '待上架',
  listed: '已上架',
  retired: '已下架',
}

export function statusText(s: string): string {
  return STATUS_LABEL[s] ?? s
}

/**
 * 可送审的产品状态。
 *
 * ⚠️ **权威在后端**（`audit/service.py` 判 `AUDIT_STATE_INVALID`），本常量只决定
 * 按钮显不显示、批量时哪几件算「有效」。两边口径必须一字不差——今天单品按钮用的
 * 就是这四个状态，批量若多算一个，用户勾了却在回执里看到「跳过」，会以为系统丢单。
 * 真正的闸永远是后端：即便这里放行了，后端照样把它扔进 `skipped[]`。
 */
export const AUDIT_ELIGIBLE_STATUSES = [
  'ingested',
  'audit_rejected',
  'audit_passed',
  'needs_review',
] as const

export function isAuditEligible(status: string): boolean {
  return (AUDIT_ELIGIBLE_STATUSES as readonly string[]).includes(status)
}

/** 审核判定 → 中文 + 颜色。needs_review **独立一档橙色**，绝不并进「通过」的绿字：
 *  它是「判不下来、要人来看」，把它算作成功会让复核队列在面板上凭空消失。 */
export const VERDICT_LABEL: Record<string, { text: string; color: string }> = {
  pass: { text: '通过', color: 'green' },
  reject: { text: '拒绝', color: 'red' },
  needs_review: { text: '待人工复核', color: 'orange' },
}

/**
 * 批量送审逐条错误码 → 中文释义 + 处置建议（008§6：不显示裸错误码）。
 * 取值来源：`audit/batch.py` 与 `audit/service.py` 实际抛出的码，以及
 * `core/idempotency.py` 的两个 409。
 */
export const AUDIT_CODE_LABEL: Record<string, { text: string; hint: string }> = {
  AUDIT_PRODUCT_NOT_FOUND: {
    text: '产品不存在或不属于当前团队',
    hint: '列表可能已过期（产品被删/被转团队）。刷新后重试；若刷新后仍在，报运维。',
  },
  AUDIT_STATE_INVALID: {
    text: '当前状态不可送审',
    hint: '仅「已采集 / 审核通过 / 审核拒绝 / 待人工复核」可送审；审核中的请等它跑完。',
  },
  AUDIT_ITEM_FAILED: {
    // ⚠️ 措辞必须诚实：这一档的产品**停在「审核中」**（后端 tx1 已把它置 auditing，
    // tx2 才崩），而「审核中」不在 AUDIT_ELIGIBLE_STATUSES 里——本页不给它重审入口。
    // 早先这里写的是「单独重试它即可」，是一句**做不到的话**（2026-07-28 审查 F2）。
    // 自愈路径见 `.agent/evidence/R2-09/runbook-increment3a.md`：10 分钟后该品的
    // 悬挂 run 会被下一次送审的懒清扫判 failed；把它从「审核中」拉回来目前需要运维
    // 改状态，故让运维介入而不是让运营空点按钮。
    text: '审核中途异常中断',
    hint: '这一条**可能已经产生费用**（已开审才会进本档），且会停留在「审核中」。请把产品 ID 报运维按 runbook 处置，不要整批重发。',
  },
  AUDIT_LEVELS_INVALID: {
    text: '审核层级参数非法',
    hint: '层级只认小写 l0/l1/l2/l3/l4。本页不发送该参数，出现即是客户端或脚本传错，报运维。',
  },
  AUDIT_TEAM_REQUIRED: {
    text: '超管未指定团队',
    hint: '批量送审必须落到具体团队：用右上角团队切换选定团队后再送。',
  },
  AUDIT_L4_DISABLED: {
    text: 'L4 视觉审核未开放',
    hint: 'R2 阶段只跑 L0/L2/L3。',
  },
  IDEMPOTENCY_IN_PROGRESS: {
    text: '上一次同批请求仍在处理中',
    hint: '稍候再点同一批（键是稳定的，重点不会重复扣费——命中的是上次那一批的结果）。',
  },
  IDEMPOTENCY_CONFLICT: {
    text: '同一幂等键被不同载荷使用',
    hint: '通常是并发操作撞键。换一批（或改动勾选）后重试；反复出现请报运维。',
  },
  // 兜底一档：没拿回可识别结果（网关 120s 掐断、断网、上游 5xx）。
  // **这一档最危险且最常见**——nginx 的 504 页面不带 002 错误信封，`client.ts`
  // 只能把它回落成 code='UNKNOWN'、message='请求失败'；若不单独归类，用户在最需要
  // 「别乱点」提示的时刻，看到的却是标题「UNKNOWN」这一个裸码。
  NO_RESULT: {
    text: '请求未拿到结果（网关超时或服务异常）',
    hint: '后端很可能仍在执行这一批。等一会儿原样重试同一批：重试命中的是同一次执行的结果，不会重复扣费；千万不要改动勾选后再试。',
  },
}

export function codeText(code: string): string {
  return AUDIT_CODE_LABEL[code]?.text ?? code
}

/** price_snapshot 字段中文名（采集自 Amazon） */
export const PRICE_LABELS: Record<string, string> = {
  current_price: '当前价',
  buybox_price: 'BuyBox 价',
  original_price: '原价',
  buybox_shipping: '运费',
  is_fba: 'FBA 发货',
  total_price: '总价',
}

/** attrs 字段中文名（bullets 单独渲染，其余按此表；未列出的原样显示 key） */
export const ATTR_LABELS: Record<string, string> = {
  model_number: '型号',
  manufacturer: '制造商',
  part_number: '部件号',
  country_of_origin: '原产国',
  is_customized: '是否定制',
  product_type: '商品类型',
  stock_status: '库存状态',
  stock_count: '库存数',
  delivery_date: '配送日期',
  delivery_time: '配送时长',
  rating: '评分',
  review_count: '评论数',
  seller_name: '卖家',
  seller_id: '卖家 ID',
  best_sellers_rank: '畅销排名',
  first_available_date: '上架日期',
  package_dimensions: '包装尺寸',
  package_weight: '包装重量',
  item_dimensions: '商品尺寸',
  item_weight: '商品重量',
  upc_list: 'UPC',
  ean_list: 'EAN',
  variation_asins: '变体 ASIN',
  parent_asin: '父体 ASIN',
  long_description: '长描述',
  crawl_time: '采集时间',
  product_url: '商品链接',
  zip_code: '配送邮编',
  site: '站点',
}

export function attrText(v: unknown): string {
  if (Array.isArray(v)) return v.join('、')
  return String(v ?? '')
}
