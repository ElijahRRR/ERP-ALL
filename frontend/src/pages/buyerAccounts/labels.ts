// 买家账号池 + 采购插件实例的枚举中文名与展示助手（R2-13 13b）。
// 枚举取值以契约 `openapi-v0.yaml` 的 BuyerAccount / PluginInstance 为准，这里只做中文映射；
// 值本身不在前端定义、不在前端校验——业务规则以后端为权威（008 §3.4）。
//
// 本模块被 `BuyerAccountsPage` 与 `PluginInstancesPage` 两页共用：实例页要显示「这台机器
// 最近登的号认领了没有」，用的就是下面这张账号状态表（图纸 07:283 的排障场景）。
// 两页同域，故留在本目录而不上提 `components/`（008 §4 讲的是**组件**的提升门槛）。

export const SITE_LABEL: Record<string, string> = {
  amazon_com: '美国站 amazon.com',
  amazon_ca: '加拿大站 amazon.ca',
  // 日本站本轮不做，但取值与整条代码路径保留（铁律 9：不做 ≠ 删掉）
  amazon_co_jp: '日本站 amazon.co.jp（本轮不做）',
}

export const ACCOUNT_STATUS: Record<string, { color: string; label: string }> = {
  // **发现态**（不是运营可选态）：插件带上来一个本团队没见过的 customerId，服务端自动
  // 落一条待认领行。**未认领一律不派单**——它是「系统发现了一台机器在用这个号，
  // 但还没人确认这号是我们的」。运营的出口只有两个：认领（补名称+站点转 active）或驳回。
  pending_claim: { color: 'orange', label: '待认领' },
  active: { color: 'green', label: '启用' },
  // 三种非 active 的共同后果：**不再派新任务**；但回填/异常/物流三条写路径照常通
  // （`plugin/auth.py` 头注：钱可能已经花出去了，账号被停就收不回回填）。
  paused: { color: 'orange', label: '暂停派单' },
  blocked: { color: 'red', label: '已风控' },
  retired: { color: 'default', label: '已退役' },
  // **终态**。驳回不删行是刻意的（本表无 DELETE 授权）：该 customerId 被唯一索引永久
  // 占住，同一个伪造 id 再灌只会解析到这一行——零新增行、零新通知。粘性即治理。
  rejected: { color: 'default', label: '已驳回' },
}

export const EXEC_MODE: Record<string, { color: string; label: string; hint: string }> = {
  dry_run: { color: 'default', label: '演练 dry_run', hint: '不进结账页，一个金额列都不写' },
  stop_before_payment: {
    color: 'blue',
    label: '付款前停 stop_before_payment',
    hint: '走到付款前停下并回报金额，不会花钱——不花钱验收的主力档',
  },
  live: { color: 'red', label: '实盘 live', hint: '完整下单，真实花钱' },
}

/** 绝对时间（仓内既有口径：`new Date(v).toLocaleString('zh-CN')`） */
export function fmtTime(v?: string | null): string {
  return v ? new Date(v).toLocaleString('zh-CN') : '—'
}

/** 掉线判据：超过这么久没拉过任务就当疑似掉线（只是展示口径，不影响派发） */
const STALE_MS = 24 * 60 * 60 * 1000

/**
 * `last_seen_at` 的相对时间展示。
 *
 * 这一列是运维面唯一能看出「插件是不是还活着」的信号——买家账号本身没有心跳，
 * `last_seen_at` 由两个拉取端点写。`stale` 为真时页面要把它标灰并写「疑似掉线」，
 * 否则一个挂掉的指纹浏览器可以静默不干活，而运营只会看到「今天没单」。
 */
export function lastSeen(v?: string | null): { text: string; stale: boolean } {
  if (!v) return { text: '从未上线', stale: true }
  const ts = new Date(v).getTime()
  if (Number.isNaN(ts)) return { text: v, stale: false }
  const diff = Date.now() - ts
  if (diff < 0) return { text: '刚刚', stale: false }
  const mins = Math.floor(diff / 60000)
  if (mins < 1) return { text: '刚刚', stale: false }
  if (mins < 60) return { text: `${mins} 分钟前`, stale: false }
  const hours = Math.floor(mins / 60)
  if (hours < 24) return { text: `${hours} 小时前`, stale: diff >= STALE_MS }
  return { text: `${Math.floor(hours / 24)} 天前`, stale: true }
}
