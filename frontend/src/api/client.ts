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

export const api = {
  get: <T>(path: string) => request<T>(path),
  // Idempotency-Key：契约必填的写操作幂等头（服务端 24h 去重）。每次调用生成一次；
  // 401 刷新后的重放复用同一 init => 同键，网络层自动重试不会造成重复执行。
  post: <T>(path: string, data?: unknown) =>
    request<T>(path, {
      method: 'POST',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: data === undefined ? undefined : JSON.stringify(data),
    }),
  put: <T>(path: string, data: unknown) =>
    request<T>(path, { method: 'PUT', body: JSON.stringify(data) }),
  patch: <T>(path: string, data: unknown) =>
    request<T>(path, { method: 'PATCH', body: JSON.stringify(data) }),
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
