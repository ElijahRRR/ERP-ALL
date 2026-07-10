import { Button, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type PageOf } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'

interface Proxy {
  id: number
  kind: string
  host: string
  port: number
  username: string | null
  vendor: string | null
  status: 'active' | 'expired' | 'disabled'
  expires_at: string | null
  bound_store_code: string | null
}

const STATUS_COLOR = { active: 'green', expired: 'red', disabled: 'default' } as const

export default function ProxiesPage() {
  const { has } = useAuth()
  const [data, setData] = useState<PageOf<Proxy> | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [createOpen, setCreateOpen] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await api.get<PageOf<Proxy>>(`/proxies?page=${page}&size=50`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page])

  useEffect(() => {
    void load()
  }, [load])

  async function onCreate(values: Record<string, unknown>) {
    try {
      await api.post('/proxies', values)
      message.success('代理已登记')
      setCreateOpen(false)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '创建失败')
    }
  }

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        {has('channel.proxy_write') && (
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            登记代理
          </Button>
        )}
      </Space>
      <Table<Proxy>
        rowKey="id"
        loading={loading}
        dataSource={data?.items ?? []}
        pagination={{ current: page, pageSize: 50, total: data?.total ?? 0, onChange: setPage }}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 70 },
          {
            title: '地址',
            render: (_, p) => `${p.kind}://${p.host}:${p.port}`,
          },
          { title: '账号', dataIndex: 'username', render: (v: string | null) => v ?? '—' },
          { title: '供应商', dataIndex: 'vendor', render: (v: string | null) => v ?? '—' },
          {
            title: '状态',
            dataIndex: 'status',
            render: (v: Proxy['status']) => <Tag color={STATUS_COLOR[v]}>{v}</Tag>,
          },
          {
            title: '到期',
            dataIndex: 'expires_at',
            render: (v: string | null) => v ?? '—',
          },
          {
            title: '绑定店铺',
            dataIndex: 'bound_store_code',
            render: (v: string | null) =>
              v ? <Tag color="blue">{v}</Tag> : <span style={{ color: '#888' }}>空闲</span>,
          },
        ]}
      />

      <Modal title="登记代理" open={createOpen} onCancel={() => setCreateOpen(false)} footer={null} destroyOnClose>
        <Form layout="vertical" onFinish={onCreate} initialValues={{ kind: 'socks5' }}>
          <Form.Item label="类型" name="kind">
            <Select options={[{ value: 'socks5' }, { value: 'http' }]} />
          </Form.Item>
          <Form.Item label="IP / 域名" name="host" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="端口" name="port" rules={[{ required: true }]}>
            <InputNumber min={1} max={65535} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="登录账号" name="username">
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item label="登录密码" name="password">
            <Input.Password autoComplete="new-password" placeholder="加密存储，不再回显" />
          </Form.Item>
          <Form.Item label="供应商" name="vendor">
            <Input placeholder="如 易路" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            登记
          </Button>
        </Form>
      </Modal>
    </>
  )
}
