import {
  AuditOutlined,
  BellOutlined,
  DashboardOutlined,
  LogoutOutlined,
  SafetyOutlined,
  TeamOutlined,
} from '@ant-design/icons'
import { Dropdown, Layout, Menu, Spin } from 'antd'
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { logout } from '@/api/client'
import NotificationBell from '@/components/NotificationBell'
import { useAuth } from '@/auth/AuthContext'

// 菜单项 → 所需权限点（无权限点的菜单所有人可见）
const MENU = [
  { key: '/', icon: <DashboardOutlined />, label: '工作台', permission: null },
  { key: '/users', icon: <TeamOutlined />, label: '成员管理', permission: 'identity.user_read' },
  { key: '/roles', icon: <SafetyOutlined />, label: '角色权限', permission: 'identity.user_read' },
  { key: '/notifications', icon: <BellOutlined />, label: '通知中心', permission: null },
  { key: '/audit', icon: <AuditOutlined />, label: '审计日志', permission: 'identity.audit_read' },
]

export default function AppLayout() {
  const { me, loading, has } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  if (loading) {
    return <Spin size="large" style={{ display: 'block', margin: '40vh auto' }} />
  }
  if (!me) {
    return <Navigate to="/login" replace />
  }

  const items = MENU.filter((m) => !m.permission || has(m.permission)).map(
    ({ key, icon, label }) => ({ key, icon, label }),
  )

  return (
    <Layout style={{ minHeight: '100vh' }}>
      <Layout.Sider theme="dark" breakpoint="lg">
        <div
          style={{
            color: '#fff',
            fontWeight: 700,
            fontSize: 18,
            textAlign: 'center',
            padding: '16px 0',
          }}
        >
          ERP-ALL
        </div>
        <Menu
          theme="dark"
          mode="inline"
          selectedKeys={[location.pathname]}
          items={items}
          onClick={({ key }) => navigate(key)}
        />
      </Layout.Sider>
      <Layout>
        <Layout.Header
          style={{
            background: '#fff',
            display: 'flex',
            justifyContent: 'flex-end',
            alignItems: 'center',
            paddingInline: 24,
            gap: 24,
          }}
        >
          <NotificationBell />
          <Dropdown
            menu={{
              items: [
                {
                  key: 'logout',
                  icon: <LogoutOutlined />,
                  label: '退出登录',
                  onClick: async () => {
                    await logout()
                    navigate('/login', { replace: true })
                  },
                },
              ],
            }}
          >
            <span style={{ cursor: 'pointer' }} data-testid="current-user">
              {me.user.display_name}
              {me.user.is_super ? '（超管）' : ''}
            </span>
          </Dropdown>
        </Layout.Header>
        <Layout.Content style={{ margin: 24 }}>
          <Outlet />
        </Layout.Content>
      </Layout>
    </Layout>
  )
}
