import {
  ApiOutlined,
  AuditOutlined,
  BellOutlined,
  ChromeOutlined,
  CloudDownloadOutlined,
  DashboardOutlined,
  DollarOutlined,
  FileProtectOutlined,
  GlobalOutlined,
  LogoutOutlined,
  ProfileOutlined,
  RobotOutlined,
  ShopOutlined,
  ShoppingOutlined,
  ThunderboltOutlined,
  UserSwitchOutlined,
  WarningOutlined,
} from '@ant-design/icons'
import { Dropdown, Layout, Menu, Spin } from 'antd'
import { Navigate, Outlet, useLocation, useNavigate } from 'react-router-dom'

import { logout } from '@/api/client'
import NotificationBell from '@/components/NotificationBell'
import { useAuth } from '@/auth/AuthContext'

// 菜单项 → 所需权限点（无权限点的菜单所有人可见）
const MENU = [
  { key: '/', icon: <DashboardOutlined />, label: '工作台', permission: null },
  { key: '/scrape-jobs', icon: <CloudDownloadOutlined />, label: '采集作业', permission: 'scrape.job_read' },
  { key: '/products', icon: <ShoppingOutlined />, label: '产品库', permission: 'catalog.product_read' },
  { key: '/listings', icon: <ShopOutlined />, label: '上架管理', permission: 'listing.read' },
  { key: '/pricing', icon: <DollarOutlined />, label: '定价策略', permission: 'pricing.read' },
  { key: '/automation', icon: <ThunderboltOutlined />, label: '自动化档位', permission: 'automation.read' },
  { key: '/orders', icon: <ProfileOutlined />, label: '订单管理', permission: 'order.read' },
  { key: '/purchasers', icon: <UserSwitchOutlined />, label: '采购方', permission: 'procurement.read' },
  { key: '/buyer-accounts', icon: <RobotOutlined />, label: '买家账号池', permission: 'procurement.buyer_account_read' },
  // 实例管理与账号池并列而不是嵌在里面：令牌绑「一台授权浏览器」，不绑买家账号
  // （图纸 07:288-340）。读权限沿用 buyer_account_read，签发/吊销另由页内按钮按
  // plugin_instance_admin 门控——看得见和发得出不是同一量级。
  { key: '/plugin-instances', icon: <ChromeOutlined />, label: '插件实例', permission: 'procurement.buyer_account_read' },
  { key: '/stores', icon: <GlobalOutlined />, label: '店铺管理', permission: 'channel.store_read' },
  { key: '/incidents', icon: <WarningOutlined />, label: '店铺事件', permission: 'channel.incident_read' },
  { key: '/compliance', icon: <FileProtectOutlined />, label: '合规中心', permission: 'compliance.blacklist_read' },
  { key: '/proxies', icon: <ApiOutlined />, label: '代理管理', permission: 'channel.proxy_read' },
  // D-Q73 17b：成员管理/角色权限入口摘除（路由保留，直敲 URL 仍可达——表层摘除、
  // 地基休眠；将来重启多人协作时把这两行放回来即可）。
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
          {/* D-Q73 17b：TeamSwitcher 摘除（单人单团队无切换语义；组件保留在
              components/ 未删，重启多团队时放回） */}
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
