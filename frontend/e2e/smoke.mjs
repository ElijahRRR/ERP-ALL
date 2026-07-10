// R1-05 冒烟 + 截图证据脚本（真实全栈：PG + FastAPI + Vite）
// 用法：node e2e/smoke.mjs <截图输出目录>
// 断言即验收：超管全流程可用；成员菜单按权限渲染；越权接口 403。
import { chromium } from 'playwright'

const OUT = process.argv[2] ?? '/tmp/e2e-shots'
const BASE = 'http://localhost:5173'
const SUPER = { u: 'api_super', p: 'super-secret-pass-1' }
const MEMBER = { u: 'api_member', p: 'super-secret-pass-1' }

function fail(msg) {
  console.error(`✗ ${msg}`)
  process.exit(1)
}

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })

async function shot(name) {
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: false })
  console.log(`📷 ${name}`)
}

async function login(u, p) {
  await page.goto(`${BASE}/login`)
  await page.getByPlaceholder('用户名').fill(u)
  await page.getByPlaceholder('密码').fill(p)
  await page.getByRole('button', { name: /登\s*录/ }).click()
  await page.waitForURL(`${BASE}/`)
}

// ── 登录页 ──
await page.goto(`${BASE}/login`)
await page.waitForSelector('text=跨境电商运营系统')
await shot('01-login')

// ── 超管全流程 ──
await login(SUPER.u, SUPER.p)
await page.waitForSelector('[data-testid="current-user"]')
const who = await page.textContent('[data-testid="current-user"]')
if (!who.includes('超管')) fail(`超管登录后身份异常: ${who}`)
await shot('02-home-super')

await page.click('text=成员管理')
await page.waitForSelector('text=api_member')
await shot('03-users')

await page.click('text=角色权限')
await page.waitForSelector('text=模板')
// 打开一个团队角色的权限编辑抽屉
await page.getByRole('button', { name: '设置权限' }).first().click()
await page.getByRole('button', { name: /保\s*存/ }).waitFor()
await shot('04-roles-permissions')
await page.keyboard.press('Escape')

await page.click('text=审计日志')
await page.waitForSelector('text=identity.team_create')
await shot('05-audit')

// 退出
await page.click('[data-testid="current-user"]')
await page.click('text=退出登录')
await page.waitForURL(`${BASE}/login`)

// ── 普通成员（上架员）：菜单按权限渲染 + 越权 403 ──
await login(MEMBER.u, MEMBER.p)
await page.waitForSelector('[data-testid="current-user"]')
const menuTexts = await page.locator('.ant-menu-item').allTextContents()
if (menuTexts.some((t) => t.includes('成员管理') || t.includes('审计日志')))
  fail(`成员菜单泄漏管理项: ${menuTexts}`)
await shot('06-home-member')

const resp = await page.evaluate(async () => {
  const r = await fetch('/api/v1/users', {
    headers: { Authorization: `Bearer ${localStorage.getItem('erp.access')}` },
  })
  return r.status
})
if (resp !== 403) fail(`成员直调 /users 应 403，实际 ${resp}`)
console.log('✓ 越权直调 /api/v1/users = 403')

await browser.close()
console.log('✓ E2E 冒烟全部通过')
