// R1-12 E2E 全链演示：采集→审核→分配→提交→轮询→live + 失败路径三演示
// 前置：backend 以 ERP_LLM_API_BASE=http://localhost:9801/v1 ERP_LLM_API_KEY=dummy
//       ERP_CHANNEL_BASE_URL=http://localhost:9802 启动；seed-full-chain.sql 已灌
// 用法：node e2e/full-chain.mjs [截图目录]
import { createServer } from 'node:http'
import { chromium } from 'playwright'

const OUT = process.argv[2] ?? '/tmp/e2e-full-chain'
const BASE = 'http://localhost:5173'
const API = 'http://localhost:8000/api/v1'

// ── mock LLM（9801）：user prompt 含 dyson → reject，否则 pass ──
const llmSrv = createServer((req, res) => {
  let body = ''
  req.on('data', (c) => (body += c))
  req.on('end', () => {
    const reject = body.toLowerCase().includes('compatible for dyson')
    const verdict = reject
      ? {
          verdict: 'reject',
          reason_category: 'Intellectual Property',
          reason_text: 'compatible for Dyson 语法铁证，未授权引用品牌',
          signals: {},
          blacklist_brand_verdict: [
            { brand: 'dyson', is_real_brand: true, evidence: 'compatible for 语法' },
          ],
          llm_confidence: 'high',
        }
      : {
          verdict: 'pass',
          reason_category: 'none',
          reason_text: '',
          signals: {},
          blacklist_brand_verdict: [],
          llm_confidence: 'high',
        }
    res.writeHead(200, { 'content-type': 'application/json' })
    res.end(
      JSON.stringify({
        choices: [{ message: { content: JSON.stringify(verdict) } }],
        usage: { prompt_tokens: 900, completion_tokens: 150 },
      }),
    )
  })
})
llmSrv.listen(9801)

// ── mock 渠道（9802）：feed 依次编号；奇数 feed 全成、偶数 feed 全败（演示错误路径）──
const feeds = new Map() // feedId -> {skus, ok}
let feedSeq = 0
const chanSrv = createServer((req, res) => {
  let body = ''
  req.on('data', (c) => (body += c))
  req.on('end', () => {
    const url = new URL(req.url, 'http://x')
    const send = (obj) => {
      res.writeHead(200, { 'content-type': 'application/json' })
      res.end(JSON.stringify(obj))
    }
    if (url.pathname === '/v3/token') return send({ access_token: 'tok', expires_in: 900 })
    if (req.method === 'POST' && url.pathname === '/v3/feeds') {
      feedSeq += 1
      const fid = `E2E-FEED-${feedSeq}`
      const payload = JSON.parse(body)
      const skus = (payload.MPItem ?? []).map((i) => i.Orderable.sku)
      feeds.set(fid, { skus, ok: feedSeq % 2 === 1 })
      return send({ feedId: fid })
    }
    const m = url.pathname.match(/^\/v3\/feeds\/(E2E-FEED-\d+)$/)
    if (m) {
      const f = feeds.get(m[1])
      return send({
        feedStatus: 'PROCESSED',
        summary: { itemsSucceeded: 999 }, // headline 故意不可信
        itemDetails: {
          itemIngestionStatus: f.skus.map((sku) =>
            f.ok
              ? { sku, ingestionStatus: 'SUCCESS', wpid: `WP-${sku}` }
              : {
                  sku,
                  ingestionStatus: 'DATA_ERROR',
                  ingestionErrors: {
                    ingestionError: [{ code: 'WM_E2E_DEMO', description: 'demo attribute error' }],
                  },
                },
          ),
        },
      })
    }
    send({ ok: true })
  })
})
chanSrv.listen(9802)

// ── worker 拨入模拟 ──
async function workerRun(payloadByAsin) {
  const nodeKey = `e2e-worker-${Date.now()}`
  const reg = await (
    await fetch(`${API}/worker/v1/register`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        node_key: nodeKey,
        kind: 'scraper_amazon',
        enroll_token: 'e2e-enroll-token',
      }),
    })
  ).json()
  const H = {
    'content-type': 'application/json',
    'X-Node-Key': nodeKey,
    'X-Node-Token': reg.node_token,
  }
  const pull = await (await fetch(`${API}/worker/v1/tasks/pull?count=10`, { headers: H })).json()
  for (const t of pull.tasks) {
    await fetch(`${API}/worker/v1/tasks/result`, {
      method: 'POST',
      headers: H,
      body: JSON.stringify({
        task_id: t.task_id,
        attempt: t.attempt,
        success: true,
        payload: payloadByAsin[t.target_ref],
      }),
    })
  }
  return pull.tasks.length
}

const browser = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' })
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } })
const shot = async (name) => {
  await page.screenshot({ path: `${OUT}/${name}.png` })
  console.log(`📷 ${name}`)
}

// 0. 登录
await page.goto(`${BASE}/login`)
await page.getByPlaceholder('用户名').fill('e2e_demo')
await page.getByPlaceholder('密码').fill('super-secret-pass-1')
await page.getByRole('button', { name: /登\s*录/ }).click()
await page.waitForURL(`${BASE}/`)

// 1. 采集作业：4 个 ASIN（正常品/侵权品/配额演示品/feed错误演示品）
await page.click('text=采集作业')
await page.getByRole('button', { name: /新建采集/ }).click()
await page
  .getByLabel(/ASIN 列表/)
  .fill('B0E2EDEMO1 B0E2EDEMO2 B0E2EDEMO3 B0E2EDEMO4')
await page.getByRole('button', { name: /创\s*建/ }).click()
await page.waitForSelector('text=product_detail')
await shot('01-scrape-job-created')

// 2. worker 拨入回传
const done = await workerRun({
  B0E2EDEMO1: {
    title: 'Ceramic Espresso Cup Set of 2',
    brand: 'N/A',
    price_snapshot: { list: 15.99 },
    attrs: { wpt: 'Drinkware', bullets: ['4oz', 'dishwasher safe'], description: 'plain cups' },
  },
  B0E2EDEMO2: {
    title: 'Filter Compatible for Dyson V6 Vacuum',
    brand: 'Unbranded',
    price_snapshot: { list: 9.99 },
    attrs: { wpt: 'Drinkware', bullets: ['replacement filter'], description: 'fits v6' },
  },
  B0E2EDEMO3: {
    title: 'Bamboo Coaster Plain 4 Pack',
    brand: 'N/A',
    price_snapshot: { list: 8.99 },
    attrs: { wpt: 'Drinkware', bullets: ['natural bamboo'], description: 'plain coasters' },
  },
  B0E2EDEMO4: {
    title: 'Glass Water Bottle Simple 500ml',
    brand: 'N/A',
    price_snapshot: { list: 12.99 },
    attrs: { wpt: 'Drinkware', bullets: ['borosilicate'], description: 'plain bottle' },
  },
})
console.log(`✓ worker 回传 ${done} 个任务`)
await page.reload()
await page.waitForSelector('text=4成', { timeout: 15000 }).catch(() => {})
await shot('02-scrape-job-done')

// 3. 产品库：审核（1=pass、2=LLM reject 失败路径①）
await page.click('text=产品库')
await page.waitForSelector('text=B0E2EDEMO1')
for (const asin of ['B0E2EDEMO1', 'B0E2EDEMO2', 'B0E2EDEMO3', 'B0E2EDEMO4']) {
  const btn = page
    .locator('tr', { hasText: asin })
    .getByRole('button', { name: /^审\s*核$/ })
  if (!(await btn.isVisible().catch(() => false))) continue
  await btn.click()
  await page.waitForSelector('.ant-message-notice', { timeout: 20000 })
  await page.waitForTimeout(1000)
  await page.reload()
  await page.waitForSelector('text=B0E2EDEMO1')
}
await page.reload()
await page.waitForSelector('text=audit_')
await shot('03-products-audited')

// 审核详情抽屉（拒绝品的 LLM 证据）
const rejectedRow = page.locator('tr', { hasText: 'B0E2EDEMO2' })
await rejectedRow.getByRole('button', { name: /审核详情/ }).click()
await page.waitForSelector('text=llm_intellectual_property')
await page.waitForTimeout(500)
await shot('04-audit-reject-detail')
await page.locator('.ant-drawer-close').click()
await page.locator('.ant-drawer-content').waitFor({ state: 'hidden' })

// 4. GTIN 导入（API，UI 页 R2）
const login = await (
  await fetch(`${API}/auth/login`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ username: 'e2e_demo', password: 'super-secret-pass-1' }),
  })
).json()
const AUTH = {
  'content-type': 'application/json',
  authorization: `Bearer ${login.access_token}`,
}
const ean13 = (seed) => {
  const body = `69${String(seed).padStart(10, '0')}`
  const total = [...body].reduce((a, c, i) => a + Number(c) * (i % 2 ? 3 : 1), 0)
  return body + String((10 - (total % 10)) % 10)
}
await fetch(`${API}/gtin-pool/import`, {
  method: 'POST',
  headers: AUTH,
  body: JSON.stringify({ gtins: Array.from({ length: 20 }, (_, i) => ean13(9000 + i)) }),
})

// 5. 分配上架（pass 品 B0E2EDEMO1）→ 提交 → 轮询 → live
async function allocateViaUi(asin) {
  const row = page.locator('tr', { hasText: asin })
  await row.getByRole('button', { name: /分配上架/ }).click()
  await page.locator('.ant-modal .ant-select').first().click()
  await page.locator('.ant-select-item', { hasText: 'A152E' }).click()
  await page.locator('.ant-modal button[type="submit"]').click()
  await page.waitForSelector('.ant-message-notice')
  await page.waitForTimeout(500)
}
await allocateViaUi('B0E2EDEMO1')
await shot('05-allocated')

await page.click('text=上架管理')
await page.waitForSelector('text=draft')
await page.locator('.ant-table-row .ant-checkbox-input').first().check()
await page.getByRole('button', { name: /提交上架/ }).click()
await page.waitForSelector('.ant-message-notice')
await shot('06-submitted')

await page.click('.ant-tabs-tab:has-text("Feed 提交")')
await page.waitForSelector('text=E2E-FEED-1')
await page.getByRole('button', { name: /轮\s*询/ }).first().click()
await page.waitForSelector('.ant-message-notice')
await page.waitForTimeout(500)
await shot('07-feed-processed')

await page.click('.ant-tabs-tab:has-text("刊登")')
await page.waitForSelector('text=live')
const liveRow = page.locator('tr', { hasText: 'live' }).first()
await liveRow.getByRole('button', { name: /历\s*史/ }).click()
await page.waitForSelector('text=published')
await page.waitForTimeout(500)
await shot('08-live-state-history')
await page.locator('.ant-drawer-close').click()
await page.locator('.ant-drawer-content').waitFor({ state: 'hidden' })

// 6. 铺垫失败路径③：第 3 品分配+提交（feed2=偶数→mock 会判败；先不轮询）
await page.click('text=产品库')
await allocateViaUi('B0E2EDEMO3')
await page.click('text=上架管理')
await page.locator('.ant-table-row .ant-checkbox-input').first().check()
await page.getByRole('button', { name: /提交上架/ }).click()
await page.waitForSelector('.ant-message-notice')
await page.waitForTimeout(500)

// 7. 失败路径②：配额耗尽（daily_limit=2 已用完 → 第 4 品提交被拒）
await page.click('text=产品库')
await allocateViaUi('B0E2EDEMO4')
await page.click('text=上架管理')
await page.locator('.ant-table-row .ant-checkbox-input').first().check()
await page.getByRole('button', { name: /提交上架/ }).click()
await page.waitForSelector('text=ERP_QUOTA_EXHAUSTED')
await shot('09-quota-exhausted')

// 8. 失败路径③：轮询 feed2 → item 错误 → error catalog + listing failed
await page.click('.ant-tabs-tab:has-text("Feed 提交")')
await page.waitForSelector('text=E2E-FEED-2')
await page.getByRole('button', { name: /轮\s*询/ }).first().click()
await page.waitForTimeout(800)
await page.click('.ant-tabs-tab:has-text("刊登")')
await page.waitForSelector('text=WM_E2E_DEMO')
await shot('10-feed-error-listing-failed')

// 通知铃铛：feed 失败告警
await page.click('text=通知中心')
await page.waitForSelector('text=上架 Feed')
await shot('11-notification-feed-error')

await browser.close()
llmSrv.close()
chanSrv.close()
console.log('✓ R1-12 全链演示通过（截图见 ' + OUT + '）')
