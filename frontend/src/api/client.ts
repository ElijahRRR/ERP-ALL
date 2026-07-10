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

const ACCESS_KEY = 'erp.access'
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

async function rawRequest(path: string, init: RequestInit): Promise<Response> {
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const token = tokenStore.access
  if (token) headers.set('Authorization', `Bearer ${token}`)
  return fetch(`/api/v1${path}`, { ...init, headers })
}

async function tryRefresh(): Promise<boolean> {
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
  post: <T>(path: string, data?: unknown) =>
    request<T>(path, { method: 'POST', body: data === undefined ? undefined : JSON.stringify(data) }),
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
