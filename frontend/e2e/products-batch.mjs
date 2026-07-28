/**
 * 批量送审渲染/行为判据（R2-09 增量3a 前端）。
 *
 * **已接入 CI**：`.github/workflows/ci.yml` 的 frontend job 里紧挨
 * `node e2e/automation-panel.mjs` 跑本脚本。不接 CI 的测试文件比没有更糟——它会让
 * 后来的审查者误以为这块有覆盖，而它一次都不会跑（2026-07-28 审查 N1/F3/F6 三个
 * 镜头同时点名，故合并前接上）。**改动 CI 那一行前先想清楚这里守的是什么**。
 *
 * **为什么非要真浏览器**：本页要守的三条不变量里，两条 tsc/eslint 都看不见——
 *   1. 幂等键的**稳定性**是跨两次点击的运行时行为，类型系统管不着；
 *   2. 网关 504 返回的是 HTML 错误页而**不是 002 错误信封**，`client.ts` 只能回落成
 *      `code='UNKNOWN'`——本脚本首跑就是靠这条抓到「用户在最危险的时刻看到裸码
 *      UNKNOWN」的缺陷（已修为 NO_RESULT 兜底档）。
 *
 * **可红性已实测**（不是「写了就算」）：本脚本首跑时代码尚未有 NO_RESULT 兜底档，
 * 第 ⑤ 组等待「请求未拿到结果（网关超时或服务异常）」那一步当场超时变红——彼时页面
 * 显示的是标题「UNKNOWN」+ 正文「请求失败」。修完才转绿。
 *
 * 不连后端：`/api/v1/me`、`/api/v1/products`、`/api/v1/products/audit` 全用路由桩，
 * 因此它验的是**前端对给定契约数据的渲染与请求形制**，不验后端——后端那半由
 * `backend/tests/db/test_audit_batch.py` 覆盖。
 *
 * 用法（需先 `pnpm build`，并起**绑 IPv4** 的预览服务，理由同 automation-panel.mjs）：
 *   pnpm preview --port 4173 --strictPort --host 127.0.0.1 &
 *   node e2e/products-batch.mjs [baseURL]
 */

import { chromium } from 'playwright'

const BASE = process.argv[2] ?? 'http://127.0.0.1:4173'

/** 只有 audit.run + audit.read，**没有** listing.allocate。
 *  这正是本次修的回归：rowSelection 此前只认 listing.allocate，纯审核员连勾选框都没有。 */
const ME = {
  user: { id: 1, username: 'auditor', display_name: '审核员', is_super: false, team_id: 1 },
  permissions: ['catalog.product_read', 'audit.run', 'audit.read'],
}

const p = (id, sku, status, runId = null) => ({
  id,
  master_sku: sku,
  source_ref: `B${id}`,
  title: `商品${id}`,
  brand: 'X',
  status,
  latest_audit_run_id: runId,
})

const PRODUCTS = {
  items: [
    p(11, 'M11', 'ingested'),
    p(7, 'M07', 'needs_review', 5),
    p(23, 'M23', 'auditing'), // 送审不合格
    p(4, 'M04', 'listed', 2), // 送审不合格、分配也不合格
  ],
  total: 4,
  page: 1,
  size: 20,
}

const failures = []
function check(name, ok, detail = '') {
  console.log(ok ? `  ✓ ${name}` : `  ✗ ${name}${detail ? ` — ${detail}` : ''}`)
  if (!ok) failures.push(name)
}

const launch = () =>
  chromium.launch(process.env.PW_CHROMIUM ? { executablePath: process.env.PW_CHROMIUM } : {})

// antd 会在**恰好两个** CJK 字符的按钮里插一个空格（「关闭」→「关 闭」），
// 精确文本匹配会平白落空——一律用宽松正则。
const CLOSE = /关\s*闭/

// ───────────────────── 第一组：全流程 ─────────────────────
{
  const browser = await launch()
  const page = await browser.newPage()
  const posts = []
  let mode = 'ok'

  await page.route('**/api/v1/me', (r) => r.fulfill({ json: ME }))
  await page.route('**/api/v1/products?**', (r) => r.fulfill({ json: PRODUCTS }))
  await page.route('**/api/v1/products/audit', (r) => {
    posts.push({ key: r.request().headers()['idempotency-key'], body: r.request().postDataJSON() })
    // 网关超时的真形：**HTML 错误页，没有 002 错误信封**（不是 {"error":{...}}）
    if (mode === 'boom') return r.fulfill({ status: 504, body: '<html>gateway timeout</html>' })
    const ids = r.request().postDataJSON().product_ids
    return r.fulfill({
      json: {
        audited: [
          { product_id: ids[0], run_id: 101, verdict: 'pass', reject_level: null, llm_cost_usd: 0.0012, product_status: 'audit_passed' },
          { product_id: ids[ids.length - 1], run_id: 102, verdict: 'needs_review', reject_level: null, llm_cost_usd: 0.0003, product_status: 'needs_review' },
        ],
        skipped: [{ product_id: 999, code: 'AUDIT_STATE_INVALID', message: '产品状态 auditing 不可发起审核' }],
        failed: [],
        remaining: [1234],
        verdict_counts: { pass: 1, reject: 0, needs_review: 1 },
        total_llm_cost_usd: 0.0015,
      },
    })
  })

  await page.addInitScript(() => localStorage.setItem('erp.access', 'e2e-stub-token'))
  await page.goto(`${BASE}/products`)
  await page.getByText('M11').first().waitFor({ timeout: 15_000 })

  const rowOf = (sku) => page.locator('tbody tr').filter({ hasText: sku })
  const disabledOf = (sku) => rowOf(sku).locator('.ant-checkbox-wrapper-disabled').count()

  // ① 纯审核员也要看得见勾选框
  check('只有 audit.run 也渲染出勾选框', (await page.locator('tbody .ant-checkbox-input').count()) > 0)
  // ② 对该用户一件都做不了的行才禁用
  check('auditing 行禁用勾选', (await disabledOf('M23')) === 1)
  check('listed 行禁用勾选', (await disabledOf('M04')) === 1)
  check('ingested 行可勾选', (await disabledOf('M11')) === 0)

  // ③ 按钮显示**有效数**而非勾选数
  await rowOf('M11').locator('.ant-checkbox-input').check()
  await rowOf('M07').locator('.ant-checkbox-input').check()
  const btn = page.getByRole('button', { name: /批量送审/ })
  check('按钮显示有效数', (await btn.textContent())?.includes('（2/已选 2）'), await btn.textContent())

  // ④ 二次确认明示件数与费用
  await btn.click()
  const dlg = page.locator('.ant-modal-content')
  await dlg.getByText('本批 2 件').waitFor({ timeout: 5000 })
  check('确认框明示件数与费用', (await dlg.textContent())?.includes('将发起最多 2 次 LLM 审核，可能产生费用'))

  // ⑤ 先造一次网关超时再重试：**必须同键**（不同键 = 整批重跑 = 钱翻倍）
  mode = 'boom'
  await dlg.getByRole('button', { name: /确认送审/ }).click()
  // 标题必须是人话（否则用户在最危险的时刻只看到裸码 UNKNOWN），
  // 同时原始码不许丢——运维要靠它定位。
  await dlg.getByText('请求未拿到结果（网关超时或服务异常）').first().waitFor({ timeout: 5000 })
  check('504 无信封时归入兜底档且原始码不丢', (await dlg.textContent()).includes('HTTP 504 · UNKNOWN'))
  mode = 'ok'
  await dlg.getByRole('button', { name: /确认送审/ }).click()
  await dlg.getByText('通过').first().waitFor({ timeout: 5000 })
  check('504 后重试复用同一幂等键', posts.length === 2 && posts[0].key === posts[1].key, JSON.stringify(posts.map((x) => x.key)))
  check('幂等键形制 ab:<40>:<16>', /^ab:[0-9a-f]{40}:[0-9a-f]{16}$/.test(posts[0].key), posts[0].key)
  check('幂等键长度 ≤ 后端 Header max_length=64', posts[0].key.length <= 64, String(posts[0].key.length))
  // 载荷顺序必须与算键用的规范串同源，否则「同批不同勾选顺序」会撞成 409 同键异载荷
  check('product_ids 去重升序下发', JSON.stringify(posts[0].body.product_ids) === '[7,11]', JSON.stringify(posts[0].body))
  check('不下发 trigger_kind（服务端恒写 batch）', !('trigger_kind' in posts[0].body), JSON.stringify(posts[0].body))

  // ⑥ 回执四桶全量列出，且 needs_review 不并进绿字
  const txt = await dlg.textContent()
  check('回执列出 audited（含 run_id 证据链）', txt.includes('101') && txt.includes('102'))
  check('回执列出 skipped 且是中文释义不是裸码', txt.includes('当前状态不可送审'))
  check('回执列出 remaining', txt.includes('1234') && txt.includes('未开审'))
  check('needs_review 单列一档', txt.includes('待人工复核'))
  check('总花费透出', txt.includes('0.001500'))
  // 桩故意造成 2+1+0+1=4 ≠ 提交 2 件：结构不变量不平必须当场报警而不是藏起来
  check('结构不变量不平时报警', txt.includes('对账不平'))

  // ⑦ 续跑剩余：新载荷 → 新键（真的重跑，不是重放）
  await dlg.getByRole('button', { name: /继续送审剩余 1 件/ }).click()
  await dlg.getByRole('button', { name: /确认送审（1 件）/ }).click()
  await dlg.getByText('通过').first().waitFor({ timeout: 5000 })
  check('续跑用新的幂等键', posts.length === 3 && posts[2].key !== posts[0].key, JSON.stringify(posts.map((x) => x.key)))
  check('续跑载荷 = remaining', JSON.stringify(posts[2].body.product_ids) === '[1234]', JSON.stringify(posts[2].body))

  await browser.close()
}

// ───────────────────── 第二组：nonce 生命周期 ─────────────────────
// 「重放保留 / 非重放作废」这条决定了「再点一次到底花不花钱」，是本页最贵的一条语义。
{
  const browser = await launch()
  const page = await browser.newPage()
  const keys = []
  let replay = false

  const ONE = { items: [p(11, 'M11', 'ingested')], total: 1, page: 1, size: 20 }
  await page.route('**/api/v1/me', (r) => r.fulfill({ json: ME }))
  await page.route('**/api/v1/products?**', (r) => r.fulfill({ json: ONE }))
  await page.route('**/api/v1/products/audit', (r) => {
    keys.push(r.request().headers()['idempotency-key'])
    return r.fulfill({
      json: {
        audited: [{ product_id: 11, run_id: 1, verdict: 'pass', reject_level: null, llm_cost_usd: 0, product_status: 'audit_passed' }],
        skipped: [], failed: [], remaining: [],
        verdict_counts: { pass: 1, reject: 0, needs_review: 0 },
        total_llm_cost_usd: 0,
        ...(replay ? { idempotent_replay: true } : {}),
      },
    })
  })
  await page.addInitScript(() => localStorage.setItem('erp.access', 'e2e-stub-token'))
  await page.goto(`${BASE}/products`)
  await page.getByText('M11').first().waitFor({ timeout: 15_000 })

  const dlg = page.locator('.ant-modal-content')
  async function sendOnce() {
    await page.locator('tbody .ant-checkbox-input').first().check()
    await page.getByRole('button', { name: /批量送审/ }).click()
    await dlg.getByRole('button', { name: /确认送审/ }).click()
    await dlg.getByText('通过').first().waitFor({ timeout: 5000 })
    await dlg.getByRole('button', { name: CLOSE }).click()
    await page.waitForTimeout(300)
  }

  // 重放响应 = 这一批其实早跑完了：nonce 必须**保留**，再点仍是同键（继续免费拿结果）
  replay = true
  await sendOnce()
  await sendOnce()
  check('重放响应后保留 nonce（同批同键）', keys[0] === keys[1], JSON.stringify(keys))

  // 非重放终局响应 = 这一批刚刚真的跑完：nonce 必须**作废**，
  // 否则用户此后永远无法对同一批发起第二次真实重审（会一直被当成重放）
  replay = false
  await sendOnce()
  await sendOnce()
  check('非重放终局响应后作废 nonce（同批换键）', keys[2] !== keys[3], JSON.stringify(keys.slice(2)))
  check('作废发生在响应之后而非之前', keys[2] === keys[0], JSON.stringify(keys))

  // 重放的死结出口：保留 nonce 是安全侧的默认，但没有出口时「同一批 24h 内再也
  // 审不动」（政策修好想重跑也不行）。「重新审一遍」按钮必须换出新键。
  replay = true
  await sendOnce()
  const before = keys.length
  await page.locator('tbody .ant-checkbox-input').first().check()
  await page.getByRole('button', { name: /批量送审/ }).click()
  await dlg.getByRole('button', { name: /确认送审/ }).click()
  await dlg.getByText('通过').first().waitFor({ timeout: 5000 })
  check('重放告警给出「重新审一遍」出口', await dlg.getByRole('button', { name: /重新审一遍/ }).isVisible())
  await dlg.getByRole('button', { name: /重新审一遍/ }).click()
  await dlg.getByRole('button', { name: /确认送审/ }).click()
  await dlg.getByText('通过').first().waitFor({ timeout: 5000 })
  // 先确认「不点按钮就仍是同键」（默认仍安全），再确认「点了就换键」（出口有效）
  check('不点出口时仍复用同键', keys[before] === keys[before - 1], JSON.stringify(keys.slice(before - 1)))
  check('点「重新审一遍」后换新键（真的重跑）', keys[before + 1] !== keys[before], JSON.stringify(keys.slice(before - 1)))

  await browser.close()
}

if (failures.length) {
  console.error(`\n批量送审判据失败 ${failures.length} 条：${failures.join('、')}`)
  process.exit(1)
}
console.log('\n批量送审判据全过')
