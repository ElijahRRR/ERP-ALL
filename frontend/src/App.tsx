import { BrowserRouter, Route, Routes } from 'react-router-dom'

import { AuthProvider } from '@/auth/AuthContext'
import AppLayout from '@/layout/AppLayout'
import AuditPage from '@/pages/AuditPage'
import HomePage from '@/pages/HomePage'
import ListingsPage from '@/pages/ListingsPage'
import LoginPage from '@/pages/LoginPage'
import NotificationsPage from '@/pages/NotificationsPage'
import OrdersPage from '@/pages/OrdersPage'
import ProductsPage from '@/pages/ProductsPage'
import ProxiesPage from '@/pages/ProxiesPage'
import PurchasersPage from '@/pages/PurchasersPage'
import RolesPage from '@/pages/RolesPage'
import ScrapeJobsPage from '@/pages/ScrapeJobsPage'
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
            <Route path="/scrape-jobs" element={<ScrapeJobsPage />} />
            <Route path="/products" element={<ProductsPage />} />
            <Route path="/listings" element={<ListingsPage />} />
            <Route path="/orders" element={<OrdersPage />} />
            <Route path="/purchasers" element={<PurchasersPage />} />
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
