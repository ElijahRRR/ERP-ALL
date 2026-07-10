import { BellOutlined } from '@ant-design/icons'
import { Badge, Button, Empty, List, Popover, Tag } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'

import { api, type PageOf } from '@/api/client'

export interface Notification {
  id: number
  severity: 'info' | 'warn' | 'critical'
  category: string
  title: string
  body: string | null
  created_at: string
  read_at: string | null
}

const SEVERITY_COLOR = { info: 'blue', warn: 'orange', critical: 'red' } as const

export default function NotificationBell() {
  const [count, setCount] = useState(0)
  const [recent, setRecent] = useState<Notification[]>([])
  const [open, setOpen] = useState(false)
  const navigate = useNavigate()

  const poll = useCallback(async () => {
    try {
      const { count } = await api.get<{ count: number }>('/notifications/unread-count')
      setCount(count)
    } catch {
      /* 轮询失败静默，下轮再试 */
    }
  }, [])

  useEffect(() => {
    void poll()
    const timer = setInterval(() => void poll(), 30_000)
    return () => clearInterval(timer)
  }, [poll])

  async function loadRecent() {
    const page = await api.get<PageOf<Notification>>('/notifications?size=5&unread_only=true')
    setRecent(page.items)
  }

  async function readAll() {
    await api.post('/notifications/read-all')
    setCount(0)
    setRecent([])
  }

  return (
    <Popover
      open={open}
      onOpenChange={(v) => {
        setOpen(v)
        if (v) void loadRecent()
      }}
      placement="bottomRight"
      title="未读通知"
      content={
        <div style={{ width: 320 }}>
          {recent.length === 0 ? (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有未读通知" />
          ) : (
            <List
              size="small"
              dataSource={recent}
              renderItem={(n) => (
                <List.Item>
                  <List.Item.Meta
                    title={
                      <>
                        <Tag color={SEVERITY_COLOR[n.severity]}>{n.severity}</Tag>
                        {n.title}
                      </>
                    }
                    description={new Date(n.created_at).toLocaleString('zh-CN')}
                  />
                </List.Item>
              )}
            />
          )}
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 8 }}>
            <Button
              size="small"
              type="link"
              onClick={() => {
                setOpen(false)
                navigate('/notifications')
              }}
            >
              查看全部
            </Button>
            <Button size="small" onClick={() => void readAll()} disabled={count === 0}>
              全部已读
            </Button>
          </div>
        </div>
      }
    >
      <Badge count={count} size="small" data-testid="notify-badge">
        <BellOutlined style={{ fontSize: 18, cursor: 'pointer' }} />
      </Badge>
    </Popover>
  )
}
