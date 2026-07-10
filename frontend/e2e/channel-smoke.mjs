// R1-08 冒烟：店铺列表 → 管理抽屉（凭证掩码/代理/配额）→ 代理页
import { chromium } from 'playwright'

const OUT = process.argv[2] ?? '/tmp/e2e-shots'
const BASE = 'http://localhost:5173'

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

await page.goto(`${BASE}/login`)
await page.getByPlaceholder('用户名').fill('channel_admin')
await page.getByPlaceholder('密码').fill('super-secret-pass-1')
await page.getByRole('button', { name: /登\s*录/ }).click()
await page.waitForURL(`${BASE}/`)

await page.click('text=店铺管理')
await page.waitForSelector('text=A152')
await page.screenshot({ path: `${OUT}/09-stores.png` })
console.log('📷 09-stores')

await page.getByRole('button', { name: /管\s*理/ }).first().click()
await page.waitForSelector('text=渠道凭证')
await page.waitForSelector('text=abcd****')
await page.screenshot({ path: `${OUT}/10-store-credential.png` })
console.log('📷 10-store-credential（掩码回显）')

await page.click('.ant-tabs-tab:has-text("配额")')
await page.waitForSelector('text=今日已用')
await page.screenshot({ path: `${OUT}/11-store-quota.png` })
console.log('📷 11-store-quota')
await page.keyboard.press('Escape')

await page.click('text=代理管理')
await page.waitForSelector('text=socks5://192.0.2.10:1080')
await page.screenshot({ path: `${OUT}/12-proxies.png` })
console.log('📷 12-proxies')

await browser.close()
console.log('✓ channel 冒烟通过')
