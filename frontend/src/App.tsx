import { BrowserRouter, Route, Routes } from 'react-router-dom'

import { AuthProvider } from '@/auth/AuthContext'
import AppLayout from '@/layout/AppLayout'
import AuditPage from '@/pages/AuditPage'
import HomePage from '@/pages/HomePage'
import LoginPage from '@/pages/LoginPage'
import NotificationsPage from '@/pages/NotificationsPage'
import ProxiesPage from '@/pages/ProxiesPage'
import RolesPage from '@/pages/RolesPage'
import StoresPage from '@/pages/StoresPage'
import UsersPage from '@/pages/UsersPage'

export default function App() {
  return (
    <AuthProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route element={<AppLayout />}>
            <Route index element={<HomePage />} />
            <Route path="/stores" element={<StoresPage />} />
            <Route path="/proxies" element={<ProxiesPage />} />
            <Route path="/users" element={<UsersPage />} />
            <Route path="/notifications" element={<NotificationsPage />} />
            <Route path="/roles" element={<RolesPage />} />
            <Route path="/audit" element={<AuditPage />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </AuthProvider>
  )
}
