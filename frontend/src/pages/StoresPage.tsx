import { Button, Descriptions, Drawer, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tabs, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type PageOf } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'

interface Store {
  id: number
  channel_id: number
  code: string
  name: string
  status: 'active' | 'paused' | 'suspended' | 'closed'
  operating_mode: string
  dedup_exempt: boolean
  is_test: boolean
  proxy_id: number | null
  payment_label: string | null
  notes: string | null
}

interface ProxyItem {
  id: number
  kind: string
  host: string
  port: number
  status: string
  bound_store_code: string | null
}

const STATUS_COLOR = { active: 'green', paused: 'orange', suspended: 'red', closed: 'default' } as const

export default function StoresPage() {
  const { has } = useAuth()
  const [data, setData] = useState<PageOf<Store> | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [createOpen, setCreateOpen] = useState(false)
  const [detail, setDetail] = useState<Store | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await api.get<PageOf<Store>>(`/stores?page=${page}&size=50`))
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
      await api.post('/stores', { ...values, channel_id: 1 })
      message.success('店铺已创建')
      setCreateOpen(false)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '创建失败')
    }
  }

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        {has('channel.store_write') && (
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建店铺
          </Button>
        )}
      </Space>
      <Table<Store>
        rowKey="id"
        loading={loading}
        dataSource={data?.items ?? []}
        pagination={{ current: page, pageSize: 50, total: data?.total ?? 0, onChange: setPage }}
        columns={[
          {
            title: '编号',
            dataIndex: 'code',
            render: (v: string, s) => (
              <>
                {v} {s.is_test && <Tag color="purple">测试店</Tag>}
              </>
            ),
          },
          { title: '名称', dataIndex: 'name' },
          {
            title: '状态',
            dataIndex: 'status',
            render: (v: Store['status']) => <Tag color={STATUS_COLOR[v]}>{v}</Tag>,
          },
          { title: '模式', dataIndex: 'operating_mode' },
          {
            title: '出口代理',
            dataIndex: 'proxy_id',
            render: (v: number | null) => (v ? `#${v}` : <Tag color="red">未绑定</Tag>),
          },
          { title: '收款', dataIndex: 'payment_label', render: (v: string | null) => v ?? '—' },
          {
            title: '操作',
            render: (_, s) => (
              <Button size="small" onClick={() => setDetail(s)}>
                管理
              </Button>
            ),
          },
        ]}
      />

      <Modal title="新建店铺" open={createOpen} onCancel={() => setCreateOpen(false)} footer={null} destroyOnClose>
        <Form layout="vertical" onFinish={onCreate} initialValues={{ operating_mode: 'build' }}>
          <Form.Item label="店铺编号" name="code" rules={[{ required: true }]}>
            <Input placeholder="如 A152" />
          </Form.Item>
          <Form.Item label="店铺名称" name="name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="运营模式" name="operating_mode">
            <Select
              options={[
                { value: 'build', label: '建品 build' },
                { value: 'match', label: '跟卖 match' },
                { value: 'mixed', label: '混合 mixed' },
              ]}
            />
          </Form.Item>
          <Form.Item label="收款标签" name="payment_label">
            <Input placeholder="PingPong" />
          </Form.Item>
          <Form.Item label="测试店（渠道写路径灰度专用）" name="is_test" valuePropName="checked">
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            创建
          </Button>
        </Form>
      </Modal>

      {detail && (
        <StoreDrawer store={detail} onClose={() => setDetail(null)} onChanged={() => void load()} />
      )}
    </>
  )
}

function StoreDrawer({
  store,
  onClose,
  onChanged,
}: {
  store: Store
  onClose: () => void
  onChanged: () => void
}) {
  const { has } = useAuth()
  return (
    <Drawer title={`${store.code} — ${store.name}`} open width={560} onClose={onClose}>
      <Tabs
        items={[
          {
            key: 'credential',
            label: '渠道凭证',
            children: <CredentialTab storeId={store.id} canWrite={has('channel.credential_write')} />,
          },
          {
            key: 'proxy',
            label: '出口代理',
            children: (
              <ProxyTab store={store} canWrite={has('channel.store_write')} onChanged={onChanged} />
            ),
          },
          {
            key: 'quota',
            label: '配额',
            children: <QuotaTab storeId={store.id} canWrite={has('channel.quota_write')} />,
          },
        ]}
      />
    </Drawer>
  )
}

function CredentialTab({ storeId, canWrite }: { storeId: number; canWrite: boolean }) {
  const [status, setStatus] = useState<{
    configured: boolean
    client_id_masked: string | null
    updated_at: string | null
  } | null>(null)

  const load = useCallback(async () => {
    setStatus(await api.get(`/stores/${storeId}/credential`))
  }, [storeId])

  useEffect(() => {
    void load()
  }, [load])

  async function onSave(values: { client_id: string; client_secret: string }) {
    try {
      await api.put(`/stores/${storeId}/credential`, values)
      message.success('凭证已加密保存')
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '保存失败')
    }
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="large">
      <Descriptions column={1} size="small" bordered>
        <Descriptions.Item label="状态">
          {status?.configured ? <Tag color="green">已配置</Tag> : <Tag color="red">未配置</Tag>}
        </Descriptions.Item>
        <Descriptions.Item label="Client ID">{status?.client_id_masked ?? '—'}</Descriptions.Item>
        <Descriptions.Item label="更新时间">
          {status?.updated_at ? new Date(status.updated_at).toLocaleString('zh-CN') : '—'}
        </Descriptions.Item>
      </Descriptions>
      {canWrite && (
        <Form layout="vertical" onFinish={onSave}>
          <Form.Item label="Client ID" name="client_id" rules={[{ required: true, min: 4 }]}>
            <Input autoComplete="off" />
          </Form.Item>
          <Form.Item label="Client Secret" name="client_secret" rules={[{ required: true, min: 8 }]}>
            <Input.Password autoComplete="new-password" placeholder="保存后不再回显" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            加密保存
          </Button>
        </Form>
      )}
    </Space>
  )
}

function ProxyTab({
  store,
  canWrite,
  onChanged,
}: {
  store: Store
  canWrite: boolean
  onChanged: () => void
}) {
  const [proxies, setProxies] = useState<ProxyItem[]>([])
  const [selected, setSelected] = useState<number | undefined>(store.proxy_id ?? undefined)

  useEffect(() => {
    void api.get<PageOf<ProxyItem>>('/proxies?size=200').then((p) => setProxies(p.items))
  }, [])

  async function onBind() {
    if (!selected) return
    try {
      await api.put(`/stores/${store.id}/proxy`, { proxy_id: selected })
      message.success('代理已换绑')
      onChanged()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '换绑失败')
    }
  }

  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <div>
        当前绑定：{store.proxy_id ? `#${store.proxy_id}` : '未绑定'}
        <div style={{ color: '#888', fontSize: 12 }}>
          防关联铁律：一个出口 IP 只能绑一家店，被占用的代理会被拒绝。
        </div>
      </div>
      {canWrite && (
        <Space.Compact style={{ width: '100%' }}>
          <Select
            style={{ flex: 1 }}
            value={selected}
            onChange={setSelected}
            placeholder="选择代理"
            options={proxies.map((p) => ({
              value: p.id,
              label: `#${p.id} ${p.kind}://${p.host}:${p.port}${p.bound_store_code ? `（已绑 ${p.bound_store_code}）` : ''}`,
              disabled: p.bound_store_code != null && p.id !== store.proxy_id,
            }))}
          />
          <Button type="primary" onClick={() => void onBind()}>
            换绑
          </Button>
        </Space.Compact>
      )}
    </Space>
  )
}

const QUOTA_LABEL: Record<string, string> = {
  listing_create: '上架 / 日',
  listing_delete: '下架 / 日',
  maintenance: '维护 / 日',
}

function QuotaTab({ storeId, canWrite }: { storeId: number; canWrite: boolean }) {
  const [configs, setConfigs] = useState<
    { quota_kind: string; daily_limit: number; enabled: boolean }[]
  >([])
  const [usage, setUsage] = useState<
    { quota_kind: string; window_date: string; used: number; daily_limit: number | null }[]
  >([])

  const load = useCallback(async () => {
    setConfigs(await api.get(`/stores/${storeId}/quota-configs`))
    setUsage(await api.get(`/stores/${storeId}/quota-usage`))
  }, [storeId])

  useEffect(() => {
    void load()
  }, [load])

  async function onSave() {
    try {
      await api.put(`/stores/${storeId}/quota-configs`, configs)
      message.success('配额已保存')
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '保存失败')
    }
  }

  const merged = Object.keys(QUOTA_LABEL).map(
    (kind) =>
      configs.find((c) => c.quota_kind === kind) ?? {
        quota_kind: kind,
        daily_limit: 0,
        enabled: false,
      },
  )

  return (
    <Space direction="vertical" style={{ width: '100%' }} size="large">
      {merged.map((c) => (
        <Space key={c.quota_kind}>
          <span style={{ display: 'inline-block', width: 90 }}>{QUOTA_LABEL[c.quota_kind]}</span>
          <InputNumber
            min={0}
            value={c.daily_limit}
            disabled={!canWrite}
            onChange={(v) =>
              setConfigs((prev) => {
                const next = prev.filter((p) => p.quota_kind !== c.quota_kind)
                return [...next, { ...c, daily_limit: v ?? 0, enabled: true }]
              })
            }
          />
          <Switch
            checked={c.enabled}
            disabled={!canWrite}
            checkedChildren="启用"
            unCheckedChildren="停用"
            onChange={(en) =>
              setConfigs((prev) => {
                const next = prev.filter((p) => p.quota_kind !== c.quota_kind)
                return [...next, { ...c, enabled: en }]
              })
            }
          />
          <span style={{ color: '#888' }}>
            今日已用 {usage.find((u) => u.quota_kind === c.quota_kind)?.used ?? 0}
          </span>
        </Space>
      ))}
      {canWrite && (
        <Button type="primary" onClick={() => void onSave()}>
          保存配额
        </Button>
      )}
    </Space>
  )
}
