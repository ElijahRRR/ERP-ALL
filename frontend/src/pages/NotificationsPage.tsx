import { Button, Space, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type PageOf } from '@/api/client'
import type { Notification } from '@/components/NotificationBell'

const SEVERITY_COLOR = { info: 'blue', warn: 'orange', critical: 'red' } as const

export default function NotificationsPage() {
  const [data, setData] = useState<PageOf<Notification> | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [unreadOnly, setUnreadOnly] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(
        await api.get<PageOf<Notification>>(
          `/notifications?page=${page}&size=20&unread_only=${unreadOnly}`,
        ),
      )
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page, unreadOnly])

  useEffect(() => {
    void load()
  }, [load])

  async function markRead(id: number) {
    await api.post(`/notifications/${id}/read`)
    void load()
  }

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Button
          type={unreadOnly ? 'primary' : 'default'}
          onClick={() => {
            setPage(1)
            setUnreadOnly(!unreadOnly)
          }}
        >
          只看未读
        </Button>
        <Button
          onClick={async () => {
            await api.post('/notifications/read-all')
            void load()
          }}
        >
          全部已读
        </Button>
      </Space>
      <Table<Notification>
        rowKey="id"
        loading={loading}
        dataSource={data?.items ?? []}
        pagination={{
          current: page,
          pageSize: 20,
          total: data?.total ?? 0,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
        columns={[
          {
            title: '级别',
            dataIndex: 'severity',
            width: 90,
            render: (v: Notification['severity']) => <Tag color={SEVERITY_COLOR[v]}>{v}</Tag>,
          },
          { title: '分类', dataIndex: 'category', width: 140 },
          {
            title: '内容',
            render: (_, n) => (
              <span style={{ fontWeight: n.read_at ? 400 : 600 }}>
                {n.title}
                {n.body && <div style={{ color: '#888', fontSize: 12 }}>{n.body}</div>}
              </span>
            ),
          },
          {
            title: '时间',
            dataIndex: 'created_at',
            width: 180,
            render: (v: string) => new Date(v).toLocaleString('zh-CN'),
          },
          {
            title: '状态',
            width: 110,
            render: (_, n) =>
              n.read_at ? (
                <Tag>已读</Tag>
              ) : (
                <Button size="small" onClick={() => void markRead(n.id)}>
                  标记已读
                </Button>
              ),
          },
        ]}
      />
    </>
  )
}
