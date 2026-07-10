import { Input, Space, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type AuditLog, type PageOf } from '@/api/client'

const ACTOR_COLOR: Record<string, string> = { user: 'blue', portal: 'orange', system: 'default' }

export default function AuditPage() {
  const [data, setData] = useState<PageOf<AuditLog> | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [action, setAction] = useState('')
  const [objectType, setObjectType] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const params = new URLSearchParams({ page: String(page), size: '50' })
      if (action) params.set('action', action)
      if (objectType) params.set('object_type', objectType)
      setData(await api.get<PageOf<AuditLog>>(`/audit-logs?${params}`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page, action, objectType])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Input.Search
          placeholder="按动作过滤（如 identity.user_create）"
          allowClear
          style={{ width: 320 }}
          onSearch={(v) => {
            setPage(1)
            setAction(v)
          }}
        />
        <Input.Search
          placeholder="按对象类型过滤（如 app_user）"
          allowClear
          style={{ width: 260 }}
          onSearch={(v) => {
            setPage(1)
            setObjectType(v)
          }}
        />
      </Space>
      <Table<AuditLog>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={data?.items ?? []}
        pagination={{
          current: page,
          pageSize: 50,
          total: data?.total ?? 0,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 条`,
        }}
        expandable={{
          expandedRowRender: (log) => (
            <pre style={{ margin: 0, fontSize: 12, overflowX: 'auto' }}>
              {JSON.stringify({ before: log.before, after: log.after }, null, 2)}
            </pre>
          ),
        }}
        columns={[
          {
            title: '时间',
            dataIndex: 'occurred_at',
            width: 180,
            render: (v: string) => new Date(v).toLocaleString('zh-CN'),
          },
          {
            title: '主体',
            width: 120,
            render: (_, log) => (
              <Tag color={ACTOR_COLOR[log.actor_type ?? 'system']}>
                {log.actor_type}#{log.actor_id ?? '-'}
              </Tag>
            ),
          },
          { title: '动作', dataIndex: 'action' },
          {
            title: '对象',
            render: (_, log) => `${log.object_type}#${log.object_id}`,
          },
          { title: 'IP', dataIndex: 'ip', width: 140, render: (v: string | null) => v ?? '—' },
        ]}
      />
    </>
  )
}
