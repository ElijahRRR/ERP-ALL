import { createContext, useCallback, useContext, useEffect, useState } from 'react'

import { api, tokenStore } from '@/api/client'

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

  const reload = useCallback(async () => {
    if (!tokenStore.access) {
      setMe(null)
      setLoading(false)
      return
    }
    setLoading(true)
    try {
      setMe(await api.get<Me>('/me'))
    } catch {
      setMe(null)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void reload()
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
