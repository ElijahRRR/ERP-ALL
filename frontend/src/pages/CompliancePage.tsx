import { Empty, Tabs, type TabsProps } from 'antd'

import { useAuth } from '@/auth/AuthContext'

import BlacklistTab from './compliance/BlacklistTab'
import ImportJobsTab from './compliance/ImportJobsTab'
import TrademarkTab from './compliance/TrademarkTab'
import TroTab from './compliance/TroTab'

// 合规中心（R2-12 增量5，收口验收⑤）：断言账本管理 + 商标/TRO 查询 + 导入进度。
// 每个 Tab 按各自权限点门控（008§3 权限三处：路由 + 菜单 + 页内）。
export default function CompliancePage() {
  const { has } = useAuth()
  const items = (
    [
      has('compliance.blacklist_read') && {
        key: 'blacklist',
        label: '黑名单账本',
        children: <BlacklistTab />,
      },
      has('compliance.trademark_read') && {
        key: 'trademark',
        label: '商标库',
        children: <TrademarkTab />,
      },
      has('compliance.tro_read') && { key: 'tro', label: 'TRO 案件', children: <TroTab /> },
      has('compliance.import_read') && {
        key: 'import',
        label: '导入作业',
        children: <ImportJobsTab />,
      },
    ] as (false | NonNullable<TabsProps['items']>[number])[]
  ).filter(Boolean) as NonNullable<TabsProps['items']>

  if (!items.length) return <Empty description="无合规查看权限" />
  return <Tabs items={items} />
}
