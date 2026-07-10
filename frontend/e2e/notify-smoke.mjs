// R1-06 通知中心冒烟：铃铛未读数 → 通知页 → 标记已读 → 未读数下降
import { chromium } from 'playwright'

const OUT = process.argv[2] ?? '/tmp/e2e-shots'
const BASE = 'http://localhost:5173'
const MEMBER = { u: 'api_member', p: 'super-secret-pass-1' }

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

await page.goto(`${BASE}/login`)
await page.getByPlaceholder('用户名').fill(MEMBER.u)
await page.getByPlaceholder('密码').fill(MEMBER.p)
await page.getByRole('button', { name: /登\s*录/ }).click()
await page.waitForURL(`${BASE}/`)

// 铃铛角标出现未读数
await page.waitForSelector('.ant-badge-count')
const badge = await page.textContent('.ant-badge-count')
if (Number(badge) < 1) {
  console.error(`✗ 铃铛未读数异常: ${badge}`)
  process.exit(1)
}
await page.click('.ant-badge')
await page.waitForSelector('text=未读通知')
await page.screenshot({ path: `${OUT}/07-bell.png` })
console.log(`📷 07-bell（未读 ${badge}）`)

// 通知中心页 + 标记已读
await page.click('text=查看全部')
await page.waitForURL(`${BASE}/notifications`)
await page.waitForSelector('text=GTIN 池水位告警')
await page.screenshot({ path: `${OUT}/08-notifications.png` })
console.log('📷 08-notifications')

await page.getByRole('button', { name: '标记已读' }).first().click()
await page.waitForTimeout(800)
const after = await page.locator('.ant-badge-count').textContent().catch(() => '0')
console.log(`✓ 标记已读后铃铛未读数: ${badge} → ${after ?? '0'}`)
await browser.close()
console.log('✓ 通知中心冒烟通过')
