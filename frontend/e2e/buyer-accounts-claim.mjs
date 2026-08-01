/**
 * 待认领账号的认领 / 驳回流渲染判据（R2-13 身份模型更正）。
 *
 * **为什么非要真浏览器**：这一版新增的三条语义全都只在渲染后存在，tsc / eslint /
 * 后端测试**一条都看不见**——
 *
 * 1. **认领表单的「站点」不给默认值**。首见自动登记时服务端只知道一个 customerId，
 *    站点无从得知（customerId 不含站点信息）。表单若像建号表单那样默认填
 *    `amazon_com`，运营会顺手点过去——结果是美国号收到加拿大单，那正是库层
 *    `ck_buyer_account_claimed` 想挡而挡不到的一类错（值填了，只是填错了）。
 *    这条是 antd `initialValues` 的一行差别，改坏了后端全绿。
 * 2. **待认领行的操作是「认领 / 驳回」而不是「编辑」**。用 AccountForm 去改一条
 *    label/site 皆空的行，必填规则会把运营卡在原地，而错因（「这行要走认领」）
 *    在界面上一个字都没有。
 * 3. **驳回必须过二次确认，且文案要写明是不可撤销的终态**。驳回后该 customerId
 *    被唯一索引永久占住、此后不再自动登记——一个点下去就没有回头路的操作，
 *    只由后端 409 兜底是不够的：后端根本不会拒绝这次驳回，它是合法转移。
 *
 * **判据自身可红性已实测**（不是「写了就算」）：给 `ClaimForm` 的 `<Form>` 加回
 * `initialValues={{ site: 'amazon_com' }}`，第 ② 条当场红；把待认领行的操作列
 * 换回统一的「编辑」按钮，第 ① 条当场红。
 *
 * 不连后端：`/api/v1/me` 与 `/api/v1/buyer-accounts*` 全用路由桩，故它验的是
 * **页面对给定契约数据的渲染与发出的请求**，不验后端——后端那半由
 * `tests/db/test_r2_13b_dispatch.py::TestBuyerAccountCrud` 覆盖。
 *
 * 用法（需先 `pnpm build`，并起**绑 IPv4** 的预览服务，理由同 automation-panel.mjs）：
 *   pnpm preview --port 4173 --strictPort --host 127.0.0.1 &
 *   node e2e/buyer-accounts-claim.mjs [baseURL]
 */

import { chromium } from 'playwright'

const BASE = process.argv[2] ?? 'http://127.0.0.1:4173'

/** 契约字段齐全的一行（对齐 openapi BuyerAccount）。
 *
 * ⚠️ **契约加字段时必须同步本工厂**：桩数据与 openapi 漂开时本脚本**不会红**
 * ——它验的是渲染，不验契约。契约那半由 `pnpm gen:api` 新鲜度门禁 + 后端
 * `tests/db` 覆盖，两者合起来才闭合。
 */
function account(over = {}) {
  return {
    id: 1,
    label: '指纹浏览器 07 号窗口',
    site: 'amazon_com',
    external_customer_id: 'A1CLAIMED0001',
    status: 'active',
    daily_cap: null,
    last_seen_at: new Date().toISOString(),
    note: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...over,
  }
}

// 待认领行：label / site **皆为 null**（库层只在转 active 时才强制齐全）
const PENDING = account({
  id: 2,
  label: null,
  site: null,
  external_customer_id: 'A1PENDING0002',
  status: 'pending_claim',
})
const ITEMS = [account(), PENDING]

const ME = {
  user: { id: 1, username: 'e2e', display_name: 'e2e', is_super: false, team_id: 1 },
  permissions: ['procurement.buyer_account_read', 'procurement.buyer_account_admin'],
}

/**
 * 按钮定位器。**必须容忍字间空格**：antd 对**恰好两个中文字**的按钮会自动插一个空格
 * （`autoInsertSpace`），DOM 实际是 `<span>认 领</span>`，可及名因此是「认 领」而不是
 * 「认领」——直接 `{ name: '认领' }` 会恒定超时，且报错只说「找不到元素」，
 * 极易被误读成「按钮没渲染出来」（本脚本首版就栽在这里，实测排查了一轮）。
 */
function btn(scope, label) {
  return scope.getByRole('button', { name: new RegExp(label.split('').join('\\s*')) })
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

const browser = await chromium.launch(
  process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {},
)
const page = await browser.newPage()
const patches = []

await page.route('**/api/v1/me', (r) => r.fulfill({ json: ME }))
await page.route('**/api/v1/buyer-accounts?**', (r) => {
  const url = new URL(r.request().url())
  // 待认领计数那一发（size=1，只取 total）与列表那一发共用端点，按 status 参数区分
  if (url.searchParams.get('status') === 'pending_claim' && url.searchParams.get('size') === '1') {
    return r.fulfill({ json: { items: [PENDING], total: 1, page: 1, size: 1 } })
  }
  return r.fulfill({ json: { items: ITEMS, total: ITEMS.length, page: 1, size: 20 } })
})
await page.route('**/api/v1/buyer-accounts/*', (r) => {
  patches.push({ url: r.request().url(), body: r.request().postDataJSON() })
  return r.fulfill({ json: { id: 2 } })
})

await page.addInitScript(() => localStorage.setItem('erp.access', 'e2e-stub-token'))
await page.goto(`${BASE}/buyer-accounts`)
await page.getByText('A1PENDING0002').first().waitFor({ timeout: 15_000 })

const pendingRow = page.locator('tbody tr').filter({ hasText: 'A1PENDING0002' })
const claimedRow = page.locator('tbody tr').filter({ hasText: 'A1CLAIMED0001' })

// ① 待认领行的操作是「认领 / 驳回」，不是「编辑」
check('待认领行有「认领」按钮', (await btn(pendingRow, '认领').count()) === 1)
check('待认领行有「驳回」按钮', (await btn(pendingRow, '驳回').count()) === 1)
check(
  '待认领行**没有**「编辑」按钮',
  (await btn(pendingRow, '编辑').count()) === 0,
)
// 反面：已认领行仍是「编辑」——防①靠「把所有行的按钮都删光」蒙混
check(
  '已认领行仍是「编辑」',
  (await btn(claimedRow, '编辑').count()) === 1,
)

// ② **主判据**：认领弹窗的「站点」无预选值。默认一个站点 = 让运营顺手点过去，
//    而站点填错的后果是这个号收到它买不了的单。
await btn(pendingRow, '认领').click()
const dialog = page.locator('.ant-modal-content').filter({ hasText: '正在认领 customerId' })
await dialog.waitFor({ timeout: 10_000 })
const siteText = (await dialog.locator('.ant-select-selector').first().innerText()).trim()
check('认领表单：站点无预选值', siteText === '请选择该号实际登录的站点', `实得「${siteText}」`)

// ③ 缺站点不得提交：必填规则挡在前面，一个 PATCH 都不能发出去
await dialog.getByLabel('账号名').fill('指纹浏览器 09 号窗口')
await btn(dialog, '认领并启用').click()
await page.waitForTimeout(400)
check('缺站点时不发 PATCH', patches.length === 0, `实得 ${patches.length} 个 PATCH`)

// ④ 补齐站点后，PATCH 必须整对提交 status=active + label + site
await dialog.locator('.ant-select-selector').first().click()
await page.locator('.ant-select-item-option').filter({ hasText: '美国站' }).first().click()
await btn(dialog, '认领并启用').click()
await page.waitForTimeout(600)
const claim = patches.find((p) => p.body?.status === 'active')
check('认领发出 PATCH', !!claim, `实得 ${JSON.stringify(patches)}`)
check(
  '认领整对提交 status+label+site',
  claim?.body?.label === '指纹浏览器 09 号窗口' && claim?.body?.site === 'amazon_com',
  JSON.stringify(claim?.body),
)

// ⑤ 驳回必须过二次确认，且文案写明是不可撤销的终态
await page.getByText('A1PENDING0002').first().waitFor({ timeout: 10_000 })
await btn(pendingRow, '驳回').click()
const confirm = page.locator('.ant-modal-confirm')
await confirm.waitFor({ timeout: 10_000 })
const confirmText = await confirm.innerText()
check('驳回有二次确认', confirmText.includes('确认驳回'))
check(
  '驳回文案写明终态且不可撤销',
  confirmText.includes('不可撤销') && confirmText.includes('永久占用'),
  confirmText.replace(/\s+/g, ' ').slice(0, 160),
)
await btn(confirm, '确认驳回').click()
await page.waitForTimeout(600)
check(
  '驳回发出 PATCH status=rejected',
  patches.some((p) => p.body?.status === 'rejected'),
  JSON.stringify(patches.map((p) => p.body)),
)

await browser.close()

if (failures.length) {
  console.error(`\n认领 / 驳回流渲染判据失败 ${failures.length} 条：${failures.join('、')}`)
  process.exit(1)
}
console.log('\n认领 / 驳回流渲染判据全过')
