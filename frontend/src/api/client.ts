// 类型化 API 客户端：类型源=契约 codegen（src/api/schema.d.ts，pnpm gen:api 重新生成）。
// 约定（002 契约 README）：Bearer 认证、401 时用 refresh 单次重试、统一错误信封。
import type { components } from './schema'

export type Schemas = components['schemas']
export type TokenPair = Schemas['TokenPair']
export type User = Schemas['User']
export type Role = Schemas['Role']
export type Permission = Schemas['Permission']
export type Team = Schemas['Team']
export type AuditLog = Schemas['AuditLog']
// R2-14 14b 主体删除回执（user/role/purchaser 三端点共用）
export type PrincipalDeleteResult = Schemas['PrincipalDeleteResult']
// R2-12 增量5 合规中心（契约 codegen 类型，禁页面手写响应 interface——008§2）
export type BlacklistEntry = Schemas['BlacklistEntry']
export type BlacklistAssertion = Schemas['BlacklistAssertion']
export type AssertionResult = Schemas['AssertionResult']
export type Trademark = Schemas['Trademark']
export type TroCase = Schemas['TroCase']
export type ImportJob = Schemas['ImportJob']
export type ImportErrorReport = Schemas['ImportErrorReport']
// R2-09 增量2 自动化档位面板（契约 codegen 类型，禁页面手写响应 interface——008§2）
export type AutomationPolicy = Schemas['AutomationPolicy']
// R2-09 增量3a 批量送审（同上：类型全部取自 codegen，页面不得手写这四个形状）
export type AuditResult = Schemas['AuditResult']
export type AuditBatchItem = Schemas['AuditBatchItem']
export type AuditBatchSkip = Schemas['AuditBatchSkip']
export type AuditBatchResult = Schemas['AuditBatchResult']
// R2-14 14a 产品删除回执（同上）
export type ProductDeleteResult = Schemas['ProductDeleteResult']
export type PageOf<T> = { items: T[]; total: number; page: number; size: number }

// ACCESS_KEY 导出给 AuthContext 监听跨标签页 token 变更（storage 事件按 key 过滤）
export const ACCESS_KEY = 'erp.access'
const REFRESH_KEY = 'erp.refresh'

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public detail?: unknown,
  ) {
    super(message)
  }
}

export const tokenStore = {
  get access() {
    return localStorage.getItem(ACCESS_KEY)
  },
  set(pair: TokenPair) {
    localStorage.setItem(ACCESS_KEY, pair.access_token ?? '')
    localStorage.setItem(REFRESH_KEY, pair.refresh_token ?? '')
  },
  clear() {
    localStorage.removeItem(ACCESS_KEY)
    localStorage.removeItem(REFRESH_KEY)
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY)
  },
}

const ACT_TEAM_KEY = 'erp.actTeam'

export const actTeamStore = {
  get(): string | null {
    return localStorage.getItem(ACT_TEAM_KEY)
  },
  set(teamId: number | null) {
    if (teamId == null) localStorage.removeItem(ACT_TEAM_KEY)
    else localStorage.setItem(ACT_TEAM_KEY, String(teamId))
  },
}

async function rawRequest(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const token = tokenStore.access
  if (token) headers.set('Authorization', `Bearer ${token}`)
  // 超管代表团队操作：后端仅对超管生效，普通用户带上也会被忽略
  const actTeam = actTeamStore.get()
  if (actTeam) headers.set('X-Act-Team', actTeam)
  return fetch(`/api/v1${path}`, { ...init, headers })
}

async function doRefresh(): Promise<boolean> {
  const refresh = tokenStore.refresh
  if (!refresh) return false
  const resp = await fetch('/api/v1/auth/refresh', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ refresh_token: refresh }),
  })
  if (!resp.ok) return false
  tokenStore.set((await resp.json()) as TokenPair)
  return true
}

// 单飞（FE-0716 防御加固）：access 过期瞬间往往多请求并发 401（页面请求 + 通知轮询），
// 各自发 refresh 会互相竞争写 tokenStore；后端 refresh 若改为单次使用轮换语义，
// 竞争失败方会误判凭证失效把用户踢回登录页。并发期间共享同一个 refresh Promise。
let refreshInFlight: Promise<boolean> | null = null

function tryRefresh(): Promise<boolean> {
  refreshInFlight ??= doRefresh().finally(() => {
    refreshInFlight = null
  })
  return refreshInFlight
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let resp = await rawRequest(path, init)
  if (resp.status === 401 && (await tryRefresh())) {
    resp = await rawRequest(path, init)
  }
  if (resp.status === 401) {
    tokenStore.clear()
    if (!location.pathname.startsWith('/login')) location.assign('/login')
  }
  if (resp.status === 204) return undefined as T
  const body = await resp.json().catch(() => null)
  if (!resp.ok) {
    const err = body?.error ?? {}
    throw new ApiError(resp.status, err.code ?? 'UNKNOWN', err.message ?? '请求失败', err.detail)
  }
  return body as T
}

// ───────────────────────── 幂等键 ─────────────────────────
// 随机串。安全上下文（HTTPS / localhost）之外受限的是 `crypto.randomUUID` 与
// `crypto.subtle`；**`crypto.getRandomValues` 在非安全上下文同样可用**（早先这里
// 写成「只在安全上下文可用」是错的，2026-07-28 审查 F7 指出）。下面的 Math.random
// 分支因此实际上极少走到，但保留：本系统按 http://内网IP 部署（frontend/nginx.conf
// 只听 80），一旦哪天 crypto 整体缺失，绝不能因为取不到它就抛——那会让所有写操作
// 在内网部署下当场崩。幂等 nonce 只需「不同尝试不撞车」，不承担任何安全职责。
function randomHex(bytes: number): string {
  const buf = new Uint8Array(bytes)
  if (typeof crypto !== 'undefined' && typeof crypto.getRandomValues === 'function') {
    crypto.getRandomValues(buf)
  } else {
    for (let i = 0; i < bytes; i += 1) buf[i] = Math.floor(Math.random() * 256)
  }
  return Array.from(buf, (b) => b.toString(16).padStart(2, '0')).join('')
}

function hexOf(buf: ArrayBuffer): string {
  return Array.from(new Uint8Array(buf), (b) => b.toString(16).padStart(2, '0')).join('')
}

// 载荷指纹。`crypto.subtle` **只在安全上下文存在**，而本系统按 http://内网IP 部署
// ——所以**生产路径走的是下面那半（五槽 FNV-1a），SHA-256 只在 localhost/HTTPS
// 下生效**。别把它读成「SHA-256 为主、FNV 是边角兜底」（早先的措辞给人这个印象，
// 2026-07-28 审查 F7）。FNV 那半是 160 bit（恰好 40 个十六进制字符，与 SHA-256
// 截断后等长——键的形制不因运行环境而变），撞车概率可忽略。
// 这里的哈希只是「同一批载荷得同一串」的指纹，不是安全原语。真撞了的后果是
// **409 IDEMPOTENCY_CONFLICT 而不是静默重放**：后端 `run_idempotent` 除了比 key
// 还比 `payload_hash`，同键异载荷直接拒——即「撞车会当场报错」，不会出现「后一批
// 被当成重放而根本不执行」。故不用单槽 32 bit，但也不必为此换算法。
async function payloadHash(input: string): Promise<string> {
  const subtle = typeof crypto !== 'undefined' ? crypto.subtle : undefined
  if (subtle) return hexOf(await subtle.digest('SHA-256', new TextEncoder().encode(input)))
  return [0x811c9dc5, 0x01000193, 0x9dc5811c, 0xdeadbeef, 0x7f4a7c15]
    .map((seed) => {
      let h = seed >>> 0
      for (let i = 0; i < input.length; i += 1) {
        h = Math.imul((h ^ input.charCodeAt(i)) >>> 0, 0x01000193) >>> 0
      }
      return h.toString(16).padStart(8, '0')
    })
    .join('')
}

const IDEM_NONCE_PREFIX = 'erp.idem.'

export interface IdemTicket {
  /** 传给 `api.post(..., { idemKey })` 的键 */
  key: string
  /** 收到**非重放**的终局响应后调用：作废 nonce，下次同样的载荷即是一次全新请求 */
  clear: () => void
}

/**
 * **稳定**幂等键：同一批载荷在同一会话内恒得同一个 key。承重件，不是优化。
 *
 * 为什么必须稳定：`frontend/nginx.conf` 的 `location /api/` 置 `proxy_read_timeout 120s`，
 * 而批量送审是**同步**端点——网关掐断连接回 504 时后端仍在跑、钱仍在花。用户看到失败
 * 必然再点一次：那一次若带**新键**，后端把它当全新请求整批重跑，同一批产品付两遍
 * LLM 的钱。带**同键**则命中 `core/idempotency.py::run_idempotent`——还在跑 →
 * 409 IDEMPOTENCY_IN_PROGRESS（明确告诉他等），已跑完 → 返回上次结果并标
 * `idempotent_replay`（一分钱没花）。`api.post` 默认的每调用一个新 UUID 恰好是**反面**。
 *
 * nonce 把「同一批产品的**这一次**尝试」与「以后想再审一次同一批」区分开：拿到非重放的
 * 终局响应即 `clear()`，下次同样的勾选就是一次全新的、本就该花钱的送审。
 *
 * ⚠️ **长度不变量**：`scope('ab')` 2 + ':' 1 + hash 40 + ':' 1 + nonce 16 = **60**，
 * 后端 `audit/router.py::IdemKey` 是 `max_length=64`。改动任何一段都要重算这个和——
 * 超了会落进 FastAPI 默认校验信封（无 `error.code` 的 422），前端只显示「请求失败」，
 * 排查成本极高。
 *
 * @param scope 端点短标识（如 'ab' = audit batch），同时是 sessionStorage 命名空间
 * @param canonical 载荷的**规范串**：同一批必须逐字相同（调用方负责排序/去重）
 */
export async function stableIdemKey(scope: string, canonical: string): Promise<IdemTicket> {
  const hash = (await payloadHash(canonical)).slice(0, 40)
  const slot = `${IDEM_NONCE_PREFIX}${scope}.${hash}`
  let nonce = sessionStorage.getItem(slot)
  if (!nonce) {
    nonce = randomHex(8)
    sessionStorage.setItem(slot, nonce)
  }
  return { key: `${scope}:${hash}:${nonce}`, clear: () => sessionStorage.removeItem(slot) }
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  // Idempotency-Key：契约必填的写操作幂等头（服务端 24h 去重）。默认每次调用生成一次；
  // 401 刷新后的重放复用同一 init => 同键，网络层自动重试不会造成重复执行。
  // `opts.idemKey` 给**跨用户重试**也必须同键的场景用（见 stableIdemKey 的头注）：
  // 默认的一次一新键在同步长请求上是重复扣费的直接成因，不能靠调用方自觉。
  post: <T>(path: string, data?: unknown, opts?: { idemKey?: string }) =>
    request<T>(path, {
      method: 'POST',
      headers: { 'Idempotency-Key': opts?.idemKey ?? randomHex(16) },
      body: data === undefined ? undefined : JSON.stringify(data),
    }),
  put: <T>(path: string, data: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(data) }),
  patch: <T>(path: string, data: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(data) }),
  // **不带 Idempotency-Key**：契约 002 §6 的幂等头针对批量提交类与渠道写路径，
  // DELETE 两者都不是；重放语义天然清楚（第二次回 404，而资源确已删除）。
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

export async function login(username: string, password: string): Promise<void> {
  const pair = await request<TokenPair>('/auth/login', {
    method: 'POST',
    body: JSON.stringify({ username, password }),
  })
  tokenStore.set(pair)
}

export async function logout(): Promise<void> {
  try {
    await api.post('/auth/logout')
  } finally {
    tokenStore.clear()
  }
}
