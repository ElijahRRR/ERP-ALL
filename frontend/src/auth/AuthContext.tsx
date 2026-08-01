import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'

import { ACCESS_KEY, api, tokenStore } from '@/api/client'

export interface Me {
  user: {
    id: number
    username: string
    display_name: string
    is_super: boolean
    team_id: number | null
  }
  permissions: string[]
}

interface AuthState {
  me: Me | null
  loading: boolean
  reload: () => Promise<void>
  has: (permission: string) => boolean
}

const AuthContext = createContext<AuthState>({
  me: null,
  loading: true,
  reload: async () => {},
  has: () => false,
})

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null>(null)
  const [loading, setLoading] = useState(true)
  // 竞态守卫：并发 reload 只允许最新一次落地，防止慢响应回填旧身份
  const seqRef = useRef(0)

  const reload = useCallback(async () => {
    const seq = ++seqRef.current
    // D-Q73 17a：无本地凭证也先问一次 /me——单人模式下后端对无凭证请求注入固定
    // 超管身份（client 无 token 时不带 Authorization 头），/me 直接 200，免登录达页；
    // 开关关着时该请求 401 落回 me=null → 登录页照常（验收⑤：休眠可逆）。
    setLoading(true)
    try {
      const next = await api.get<Me>('/me')
      if (seq === seqRef.current) setMe(next)
    } catch {
      if (seq === seqRef.current) setMe(null)
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
    // 跨标签页身份一致性（FE-0716 防御加固）：token 存 localStorage，另一标签
    // 登录/登出会改写共享凭证；本标签的请求层每次都读最新 token，但 me 状态不会
    // 自己更新——出现"侧栏按 A 用户渲染、请求实际带 B 用户 token"的撕裂态。
    // 监听 storage 事件（仅其他标签的写入会触发），立即按新 token 重取 /me 对齐。
    const onStorage = (e: StorageEvent) => {
      if (e.key === null || e.key === ACCESS_KEY) void reload()
    }
    window.addEventListener('storage', onStorage)
    return () => window.removeEventListener('storage', onStorage)
  }, [reload])

  const has = useCallback(
    (permission: string) =>
      !!me && (me.user.is_super || me.permissions.includes(permission)),
    [me],
  )

  return (
    <AuthContext.Provider value={{ me, loading, reload, has }}>{children}</AuthContext.Provider>
  )
}

export function useAuth(): AuthState {
  return useContext(AuthContext)
}
