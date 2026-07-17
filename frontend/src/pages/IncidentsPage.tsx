import { Button, Card, Form, Input, Modal, Select, Space, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type PageOf, type Schemas } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'

// 契约类型（schema.d.ts codegen）
type StoreIncident = Schemas['StoreIncident']
type BrandAssignment = Schemas['BrandAssignment']

interface StoreOpt {
  id: number
  code: string
  name: string
}

const INCIDENT_KIND: Record<string, { label: string; color: string }> = {
  suspension: { label: '封店', color: 'red' },
  warning: { label: '警告', color: 'orange' },
  listing_block: { label: 'Listing 拦截', color: 'gold' },
  other: { label: '其他', color: 'default' },
}

const INCIDENT_STATUS: Record<string, { label: string; color: string }> = {
  open: { label: '待处理', color: 'red' },
  observing: { label: '观察放款', color: 'orange' },
  appealing: { label: '申诉中', color: 'processing' },
  resolved: { label: '已解决', color: 'green' },
  closed: { label: '已关闭', color: 'default' },
}

const BRAND_STATUS: Record<string, { label: string; color: string }> = {
  occupied: { label: '占用中', color: 'blue' },
  released: { label: '已释放', color: 'default' },
}

const RELEASE_REASON: Record<string, string> = {
  suspension: '封店释放',
  manual: '手工释放',
  store_closed: '店铺关闭',
}

// transition 端点入参枚举（契约 to_status）；按封店工作流单调推进，不允许回退
const STATUS_ORDER = ['open', 'observing', 'appealing', 'resolved', 'closed']
function nextStatuses(current?: string): string[] {
  const idx = STATUS_ORDER.indexOf(current ?? 'open')
  return idx < 0 ? [] : STATUS_ORDER.slice(idx + 1)
}

function fmt(v?: string | null): string {
  return v ? new Date(v).toLocaleString('zh-CN') : '—'
}

// datetime-local 输入为本地时区的 YYYY-MM-DDTHH:mm（不含时区），提交前转 ISO
function localNow(): string {
  const d = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function tag(map: Record<string, { label: string; color: string }>, s?: string) {
  if (!s) return '—'
  const m = map[s]
  return <Tag color={m?.color}>{m ? m.label : s}</Tag>
}

export default function IncidentsPage() {
  const [stores, setStores] = useState<StoreOpt[]>([])

  useEffect(() => {
    void api
      .get<PageOf<StoreOpt>>('/stores?page=1&size=200')
      .then((r) => setStores(r.items))
      .catch(() => {
        /* 无店铺读权限时下拉/名称回退到 store_id */
      })
  }, [])

  return (
    <Space direction="vertical" size="large" style={{ width: '100%' }}>
      <IncidentSection stores={stores} />
      <BrandSection stores={stores} />
    </Space>
  )
}

function IncidentSection({ stores }: { stores: StoreOpt[] }) {
  const { has } = useAuth()
  const [data, setData] = useState<PageOf<StoreIncident> | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState<string | undefined>()
  const [kind, setKind] = useState<string | undefined>()
  const [registerOpen, setRegisterOpen] = useState(false)
  const [transition, setTransition] = useState<StoreIncident | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      // status 走服务端过滤（契约支持）；kind 契约三端点形状冻结、未暴露查询参数，仅对当前页客户端过滤
      const qs = [`page=${page}`, 'size=20', status ? `status=${status}` : '']
        .filter(Boolean)
        .join('&')
      setData(await api.get<PageOf<StoreIncident>>(`/store-incidents?${qs}`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page, status])

  useEffect(() => {
    void load()
  }, [load])

  const storeName = (id?: number) => {
    const s = stores.find((x) => x.id === id)
    return s ? `${s.code} ${s.name}` : id != null ? `#${id}` : '—'
  }

  const rows = (data?.items ?? []).filter((i) => !kind || i.incident_kind === kind)

  return (
    <Card
      title="店铺事件"
      extra={
        has('channel.incident_write') ? (
          <Button type="primary" onClick={() => setRegisterOpen(true)}>
            登记事件
          </Button>
        ) : null
      }
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          allowClear
          placeholder="按状态筛选"
          style={{ width: 160 }}
          value={status}
          onChange={(v) => {
            setStatus(v)
            setPage(1)
          }}
          options={Object.entries(INCIDENT_STATUS).map(([k, v]) => ({ value: k, label: v.label }))}
        />
        <Select
          allowClear
          placeholder="按类型筛选（本页）"
          style={{ width: 180 }}
          value={kind}
          onChange={setKind}
          options={Object.entries(INCIDENT_KIND).map(([k, v]) => ({ value: k, label: v.label }))}
        />
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      <Table<StoreIncident>
        rowKey={(r) => r.id ?? 0}
        loading={loading}
        dataSource={rows}
        pagination={{ current: page, pageSize: 20, total: data?.total ?? 0, onChange: setPage }}
        columns={[
          { title: '店铺', dataIndex: 'store_id', render: (v?: number) => storeName(v) },
          {
            title: '类型',
            dataIndex: 'incident_kind',
            width: 120,
            render: (v?: string) => tag(INCIDENT_KIND, v),
          },
          {
            title: '状态',
            dataIndex: 'status',
            width: 110,
            render: (v?: string) => tag(INCIDENT_STATUS, v),
          },
          { title: '发生时间', dataIndex: 'occurred_at', width: 170, render: fmt },
          { title: '品牌释放', dataIndex: 'brand_released_at', width: 170, render: fmt },
          { title: 'SKU 释放', dataIndex: 'sku_released_at', width: 170, render: fmt },
          {
            title: '原因',
            dataIndex: 'reason',
            ellipsis: true,
            render: (v?: string | null) => v ?? '—',
          },
          {
            title: '操作',
            key: 'op',
            width: 110,
            render: (_, r) =>
              has('channel.incident_write') && nextStatuses(r.status).length > 0 ? (
                <Button size="small" onClick={() => setTransition(r)}>
                  推进状态
                </Button>
              ) : null,
          },
        ]}
      />

      {registerOpen && (
        <RegisterModal
          stores={stores}
          onClose={() => setRegisterOpen(false)}
          onDone={() => {
            setRegisterOpen(false)
            void load()
          }}
        />
      )}
      {transition && (
        <TransitionModal
          incident={transition}
          storeName={storeName(transition.store_id)}
          onClose={() => setTransition(null)}
          onDone={() => {
            setTransition(null)
            void load()
          }}
        />
      )}
    </Card>
  )
}

function RegisterModal({
  stores,
  onClose,
  onDone,
}: {
  stores: StoreOpt[]
  onClose: () => void
  onDone: () => void
}) {
  const [form] = Form.useForm()
  const kind = Form.useWatch('incident_kind', form)
  const [submitting, setSubmitting] = useState(false)

  async function onFinish(values: Record<string, unknown>) {
    setSubmitting(true)
    try {
      await api.post('/store-incidents', {
        store_id: values.store_id,
        incident_kind: values.incident_kind,
        occurred_at: new Date(String(values.occurred_at)).toISOString(),
        reason: values.reason || undefined,
      })
      message.success('事件已登记')
      onDone()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '登记失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal title="登记店铺事件" open onCancel={onClose} footer={null} destroyOnHidden>
      <Form
        form={form}
        layout="vertical"
        onFinish={onFinish}
        initialValues={{ incident_kind: 'suspension', occurred_at: localNow() }}
      >
        <Form.Item label="店铺" name="store_id" rules={[{ required: true, message: '请选择店铺' }]}>
          <Select
            showSearch
            optionFilterProp="label"
            placeholder="选择店铺"
            options={stores.map((s) => ({ value: s.id, label: `${s.code} ${s.name}` }))}
          />
        </Form.Item>
        <Form.Item label="事件类型" name="incident_kind" rules={[{ required: true }]}>
          <Select
            options={Object.entries(INCIDENT_KIND).map(([k, v]) => ({ value: k, label: v.label }))}
          />
        </Form.Item>
        {kind === 'suspension' && (
          <div style={{ color: '#cf1322', marginBottom: 16, fontSize: 13 }}>
            登记封店事件将立即把店铺置为 suspended，并批量释放该店名下的品牌占用（occupied→released）。
          </div>
        )}
        <Form.Item
          label="发生时间"
          name="occurred_at"
          rules={[{ required: true, message: '请选择发生时间' }]}
        >
          <Input type="datetime-local" />
        </Form.Item>
        <Form.Item label="原因" name="reason">
          <Input.TextArea rows={3} placeholder="封店原因 / 备注（可选）" />
        </Form.Item>
        <Button type="primary" htmlType="submit" block loading={submitting}>
          提交登记
        </Button>
      </Form>
    </Modal>
  )
}

function TransitionModal({
  incident,
  storeName,
  onClose,
  onDone,
}: {
  incident: StoreIncident
  storeName: string
  onClose: () => void
  onDone: () => void
}) {
  const options = nextStatuses(incident.status)
  const [target, setTarget] = useState<string | undefined>(options[0])
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit() {
    if (!target) return
    setSubmitting(true)
    try {
      await api.post(`/store-incidents/${incident.id}/transition`, {
        to_status: target,
        notes: notes || undefined,
      })
      message.success('状态已推进')
      onDone()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '操作失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      title={`推进事件状态 — ${storeName}`}
      open
      onCancel={onClose}
      onOk={() => void onSubmit()}
      okText="确认推进"
      okButtonProps={{ loading: submitting, disabled: !target }}
      destroyOnHidden
    >
      <Space direction="vertical" style={{ width: '100%' }}>
        <div>当前状态：{tag(INCIDENT_STATUS, incident.status)}</div>
        <Select
          style={{ width: '100%' }}
          value={target}
          onChange={setTarget}
          placeholder="目标状态"
          options={options.map((s) => ({ value: s, label: INCIDENT_STATUS[s]?.label ?? s }))}
        />
        {target === 'resolved' && (
          <div style={{ color: '#faad14', fontSize: 13 }}>
            推进到「已解决」后，店铺将恢复为 active（人工确认动作）。
          </div>
        )}
        <Input.TextArea
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="备注（可选，写入事件记录）"
        />
      </Space>
    </Modal>
  )
}

function BrandSection({ stores }: { stores: StoreOpt[] }) {
  const { has } = useAuth()
  const canRead = has('catalog.brand_read')
  const [data, setData] = useState<PageOf<BrandAssignment> | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [storeId, setStoreId] = useState<number | undefined>()
  const [status, setStatus] = useState<string | undefined>()
  const [release, setRelease] = useState<BrandAssignment | null>(null)

  const load = useCallback(async () => {
    if (!canRead) return
    setLoading(true)
    try {
      const qs = [
        `page=${page}`,
        'size=20',
        storeId != null ? `store_id=${storeId}` : '',
        status ? `status=${status}` : '',
      ]
        .filter(Boolean)
        .join('&')
      setData(await api.get<PageOf<BrandAssignment>>(`/brand-assignments?${qs}`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [canRead, page, storeId, status])

  useEffect(() => {
    void load()
  }, [load])

  if (!canRead) {
    return (
      <Card title="品牌占用">
        <span style={{ color: '#888' }}>无品牌占用查看权限（catalog.brand_read）。</span>
      </Card>
    )
  }

  return (
    <Card title="品牌占用">
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          allowClear
          showSearch
          optionFilterProp="label"
          placeholder="按店铺筛选"
          style={{ width: 200 }}
          value={storeId}
          onChange={(v) => {
            setStoreId(v)
            setPage(1)
          }}
          options={stores.map((s) => ({ value: s.id, label: `${s.code} ${s.name}` }))}
        />
        <Select
          allowClear
          placeholder="按状态筛选"
          style={{ width: 160 }}
          value={status}
          onChange={(v) => {
            setStatus(v)
            setPage(1)
          }}
          options={Object.entries(BRAND_STATUS).map(([k, v]) => ({ value: k, label: v.label }))}
        />
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      <Table<BrandAssignment>
        rowKey={(r) => r.id ?? 0}
        loading={loading}
        dataSource={data?.items ?? []}
        pagination={{ current: page, pageSize: 20, total: data?.total ?? 0, onChange: setPage }}
        columns={[
          {
            title: '品牌',
            dataIndex: 'brand_display',
            render: (v?: string, r?: BrandAssignment) => (
              <>
                {v ?? r?.brand_norm ?? '—'}
                {r?.brand_norm && r.brand_norm !== v && (
                  <div style={{ color: '#888', fontSize: 12 }}>{r.brand_norm}</div>
                )}
              </>
            ),
          },
          {
            title: '店铺',
            dataIndex: 'store_name',
            render: (v?: string | null, r?: BrandAssignment) =>
              v ?? (r?.store_id != null ? `#${r.store_id}` : '—'),
          },
          {
            title: '状态',
            dataIndex: 'status',
            width: 100,
            render: (v?: string) => tag(BRAND_STATUS, v),
          },
          { title: '占用时间', dataIndex: 'assigned_at', width: 170, render: fmt },
          { title: '释放时间', dataIndex: 'released_at', width: 170, render: fmt },
          {
            title: '释放原因',
            dataIndex: 'release_reason',
            width: 110,
            render: (v?: string | null) => (v ? (RELEASE_REASON[v] ?? v) : '—'),
          },
          {
            title: '关联事件',
            dataIndex: 'incident_id',
            width: 100,
            render: (v?: number | null) => (v != null ? `#${v}` : '—'),
          },
          {
            title: '操作',
            key: 'op',
            width: 110,
            render: (_, r) =>
              has('catalog.brand_release') && r.status === 'occupied' ? (
                <Button size="small" danger onClick={() => setRelease(r)}>
                  手工释放
                </Button>
              ) : null,
          },
        ]}
      />
      {release && (
        <ReleaseModal
          assignment={release}
          onClose={() => setRelease(null)}
          onDone={() => {
            setRelease(null)
            void load()
          }}
        />
      )}
    </Card>
  )
}

function ReleaseModal({
  assignment,
  onClose,
  onDone,
}: {
  assignment: BrandAssignment
  onClose: () => void
  onDone: () => void
}) {
  const [notes, setNotes] = useState('')
  const [submitting, setSubmitting] = useState(false)

  async function onSubmit() {
    setSubmitting(true)
    try {
      await api.post(`/brand-assignments/${assignment.id}/release`, {
        notes: notes || undefined,
      })
      message.success('品牌占用已释放')
      onDone()
    } catch (e) {
      if (e instanceof ApiError && e.code === 'BRAND_NOT_OCCUPIED') {
        message.error('该品牌占用已不在占用状态，无法释放')
      } else {
        message.error(e instanceof ApiError ? e.message : '释放失败')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      title="手工释放品牌占用"
      open
      onCancel={onClose}
      onOk={() => void onSubmit()}
      okText="确认释放"
      okButtonProps={{ danger: true, loading: submitting }}
      destroyOnHidden
    >
      <Space direction="vertical" style={{ width: '100%' }}>
        <div>
          确认释放品牌 <strong>{assignment.brand_display ?? assignment.brand_norm}</strong> 在店铺{' '}
          <strong>{assignment.store_name ?? `#${assignment.store_id}`}</strong> 的占用？
        </div>
        <div style={{ color: '#888', fontSize: 12 }}>
          释放后 occupied→released（release_reason=manual），该品牌可再分配到其他店铺。
        </div>
        <Input.TextArea
          rows={2}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="备注（可选）"
        />
      </Space>
    </Modal>
  )
}
