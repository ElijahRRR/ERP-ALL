/**
 * 档位面板渲染判据（R2-09 增量2；部署机 UI C-unset 抓到缺陷后补）。
 *
 * **为什么非要真浏览器**：这个缺陷（未配置行显示成「已选中人工」）不在我们的代码里，
 * 而在 `<Segmented value={undefined}>` 的回落语义里——rc-segmented 对 undefined
 * 回落到第一个选项。tsc / eslint / 后端测试**全都看不见它**，它只在渲染后存在。
 * 第三闸的人工目验抓住了它，但人工目验不可复跑、也不会在下次改动时报警。
 *
 * **判据自身可红性已实测**（不是「写了就算」）：把 AutomationPage 的
 * `value={row.mode ?? ''}` 改回 `?? undefined`，本脚本第 1 条断言即红。
 *
 * 不连后端：`/api/v1/me` 与 `/api/v1/automation-policies` 全用路由桩，
 * 因此它验的是**面板对给定契约数据的渲染**，不验后端——后端那半由 tests/db 覆盖。
 *
 * 用法（需先 `pnpm build` 并起 `pnpm preview`）：
 *   node e2e/automation-panel.mjs [baseURL]
 */

import { chromium } from 'playwright'

const BASE = process.argv[2] ?? 'http://127.0.0.1:4173'

/** 契约字段齐全的一行（对齐 openapi AutomationPolicy 的 required 13 项）。 */
function row(flow, over = {}) {
  return {
    flow_code: flow,
    legal_modes: ['manual', 'semi', 'auto'],
    evaluation: 'realtime',
    state: 'unset',
    mode: null,
    enabled: null,
    effective_mode: 'manual',
    illegal_for_flow: false,
    gate: false,
    wired: false,
    config: null,
    updated_at: null,
    updated_by: null,
    ...over,
  }
}

const POLICIES = [
  row('pricing_watch'), // 未配置：本脚本的主角
  row('scrape_to_audit', {
    state: 'configured',
    mode: 'semi',
    enabled: true,
    effective_mode: 'semi',
  }),
  row('order_block', {
    legal_modes: ['manual', 'auto'],
    gate: true,
    wired: true,
    state: 'configured',
    mode: 'auto',
    enabled: true,
    effective_mode: 'auto',
  }),
]

const ME = {
  user: { id: 1, username: 'e2e', display_name: 'e2e', is_super: false, team_id: 1 },
  permissions: ['automation.read', 'automation.write'],
}

const failures = []
function check(name, ok, detail = '') {
  if (ok) {
    console.log(`  ✓ ${name}`)
  } else {
    console.log(`  ✗ ${name}${detail ? ` — ${detail}` : ''}`)
    failures.push(name)
  }
}

// 环境若预装了 Chromium（沙箱 /opt/pw-browsers、CI 自行 install），用 PW_CHROMIUM
// 指过去即可，免得每次跑都下载一份浏览器。
const browser = await chromium.launch(
  process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {},
)
const page = await browser.newPage()
const puts = []

await page.route('**/api/v1/me', (r) => r.fulfill({ json: ME }))
await page.route('**/api/v1/automation-policies', (r) => r.fulfill({ json: POLICIES }))
await page.route('**/api/v1/automation-policies/*', (r) => {
  puts.push({ url: r.request().url(), body: r.request().postDataJSON() })
  return r.fulfill({ json: POLICIES[0] })
})

await page.addInitScript(() => localStorage.setItem('erp.access', 'e2e-stub-token'))
await page.goto(`${BASE}/automation`)
await page.getByText('盯价改价').first().waitFor({ timeout: 15_000 })

const rowOf = (label) => page.locator('tbody tr').filter({ hasText: label })

// ① 主判据：未配置行的档位选择器**无选中项**。三态（unset/disabled/configured）
//    必须在控件上也分得开——压成「选中人工」就等于面板在说「有人配过人工」。
const unsetSelected = await rowOf('盯价改价').locator('.ant-segmented-item-selected').count()
check('未配置行：档位选择器无选中项', unsetSelected === 0, `实得 ${unsetSelected} 个选中项`)

// ② 已配置行必须正常显示选中项——防「①靠把所有行都弄成无选中来蒙混」。
const configuredSelected = await rowOf('采集→送审')
  .locator('.ant-segmented-item-selected')
  .textContent()
  .catch(() => null)
check('已配置行：选中项为存储档位', configuredSelected?.includes('半自动'), `实得 ${configuredSelected}`)

// ③ 连带症：未配置行上点「人工」必须真的发出 PUT。控件若把人工显示成已选中，
//    点击不产生 change 事件（radio 语义），这条路就是死的——闸类 flow 只有
//    manual/auto 两档，唯一绕法是先开 auto，即「先打开拦截再关回去」。
await rowOf('盯价改价').getByText('人工', { exact: true }).click()
await page.waitForTimeout(500)
const put = puts.find((p) => p.url.endsWith('/pricing_watch'))
check('未配置行点「人工」发出 PUT', !!put, `实得 ${puts.length} 个 PUT`)
check(
  'PUT 整对提交 mode+enabled',
  put?.body?.mode === 'manual' && put?.body?.enabled === true,
  JSON.stringify(put?.body),
)

await browser.close()

if (failures.length) {
  console.error(`\n档位面板渲染判据失败 ${failures.length} 条：${failures.join('、')}`)
  process.exit(1)
}
console.log('\n档位面板渲染判据全过')
