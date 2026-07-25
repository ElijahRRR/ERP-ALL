import { Button, Drawer, Form, Input, Modal, Segmented, Select, Space, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import {
  ApiError,
  api,
  type BlacklistAssertion,
  type BlacklistEntry,
  type PageOf,
} from '@/api/client'
import { useAuth } from '@/auth/AuthContext'

// 断言六域（001§04）；单一真相同后端 assertion.TABLE_BY_DOMAIN
const DOMAINS = [
  { value: 'brand', label: '品牌' },
  { value: 'seller', label: '卖家' },
  { value: 'asin', label: 'ASIN' },
  { value: 'category', label: '类目' },
  { value: 'address', label: '地址' },
  { value: 'zip', label: '邮编' },
]
const SOURCE_LABEL: Record<string, string> = {
  manual: '人工',
  tro_sync: 'TRO',
  trademark_sync: '商标',
  error_recycle: '报错回收',
  import: '导入',
}
const STATUS_COLOR: Record<string, string> = {
  active: 'green',
  pending: 'gold',
  revoked: 'default',
  removed: 'default',
}
const VERDICT_COLOR: Record<string, string> = { block: 'red', allow: 'blue' }

function fmt(v: string | null | undefined): string {
  return v ? new Date(v).toLocaleString('zh-CN') : ''
}

export default function BlacklistTab() {
  const { has } = useAuth()
  const canWrite = has('compliance.blacklist_write')
  const [domain, setDomain] = useState('brand')
  const [mode, setMode] = useState<'canonical' | 'pending'>('canonical')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<PageOf<BlacklistEntry> | null>(null)
  const [pending, setPending] = useState<PageOf<BlacklistAssertion> | null>(null)
  const [loading, setLoading] = useState(false)
  const [trace, setTrace] = useState<{ subject: string; items: BlacklistAssertion[] } | null>(null)
  const [recordOpen, setRecordOpen] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      if (mode === 'canonical') {
        const qs = new URLSearchParams({ domain, page: String(page), size: '20' })
        if (q) qs.set('q', q)
        setData(await api.get<PageOf<BlacklistEntry>>(`/blacklist?${qs.toString()}`))
      } else {
        setPending(
          await api.get<PageOf<BlacklistAssertion>>(
            `/blacklist/pending?domain=${domain}&page=${page}&size=20`,
          ),
        )
      }
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [domain, mode, q, page])

  useEffect(() => {
    void load()
  }, [load])

  const showTrace = useCallback(
    async (subject: string) => {
      try {
        const items = await api.get<BlacklistAssertion[]>(
          `/blacklist/trace?domain=${domain}&subject=${encodeURIComponent(subject)}`,
        )
        setTrace({ subject, items })
      } catch (e) {
        message.error(e instanceof ApiError ? e.message : '加载失败')
      }
    },
    [domain],
  )

  async function decide(id: number, approve: boolean) {
    try {
      await api.post(`/blacklist/assertions/${id}/decide`, { approve })
      message.success(approve ? '已通过' : '已驳回')
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '操作失败')
    }
  }

  async function revoke(id: number) {
    try {
      await api.post(`/blacklist/assertions/${id}/revoke`, {})
      message.success('已撤销')
      if (trace) void showTrace(trace.subject)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '操作失败')
    }
  }

  return (
    <>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          value={domain}
          onChange={(v) => {
            setDomain(v)
            setPage(1)
          }}
          options={DOMAINS}
          style={{ width: 120 }}
        />
        <Segmented
          options={[
            { label: '生效名单', value: 'canonical' },
            { label: '候选待裁决', value: 'pending' },
          ]}
          value={mode}
          onChange={(v) => {
            setMode(v as 'canonical' | 'pending')
            setPage(1)
          }}
        />
        {mode === 'canonical' && (
          <Input.Search
            allowClear
            placeholder="主体模糊查询"
            style={{ width: 220 }}
            onSearch={(v) => {
              setQ(v)
              setPage(1)
            }}
          />
        )}
        {canWrite && (
          <Button type="primary" onClick={() => setRecordOpen(true)}>
            登记断言
          </Button>
        )}
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      {mode === 'canonical' ? (
        <Table<BlacklistEntry>
          rowKey={(r) => `${r.team_id ?? 0}:${r.subject}`}
          loading={loading}
          dataSource={data?.items}
          pagination={{
            current: page,
            pageSize: 20,
            total: data?.total ?? 0,
            showSizeChanger: false,
            onChange: setPage,
          }}
          columns={[
            { title: '主体', dataIndex: 'subject' },
            { title: '展示名', dataIndex: 'display' },
            {
              title: '来源',
              dataIndex: 'source',
              width: 90,
              render: (s: string) => <Tag>{SOURCE_LABEL[s] ?? s}</Tag>,
            },
            {
              title: '作用域',
              dataIndex: 'team_id',
              width: 90,
              render: (t: number | null) => (t == null ? <Tag>全局</Tag> : `团队${t}`),
            },
            { title: '原因', dataIndex: 'reason', ellipsis: true },
            {
              title: '操作',
              key: 'op',
              width: 90,
              render: (_, r) => (
                <Button size="small" onClick={() => void showTrace(r.subject)}>
                  追溯
                </Button>
              ),
            },
          ]}
        />
      ) : (
        <Table<BlacklistAssertion>
          rowKey="id"
          loading={loading}
          dataSource={pending?.items}
          pagination={{
            current: page,
            pageSize: 20,
            total: pending?.total ?? 0,
            showSizeChanger: false,
            onChange: setPage,
          }}
          columns={[
            { title: '主体', dataIndex: 'subject_norm' },
            {
              title: '来源',
              dataIndex: 'source',
              width: 100,
              render: (s: string) => <Tag>{SOURCE_LABEL[s] ?? s}</Tag>,
            },
            {
              title: '裁决',
              dataIndex: 'verdict',
              width: 80,
              render: (v: string) => <Tag color={VERDICT_COLOR[v]}>{v}</Tag>,
            },
            { title: '原因', dataIndex: 'reason', ellipsis: true },
            { title: '登记时间', dataIndex: 'created_at', width: 170, render: fmt },
            {
              title: '人工闸',
              key: 'op',
              width: 160,
              render: (_, r) =>
                canWrite ? (
                  <Space>
                    <Button size="small" type="primary" onClick={() => void decide(r.id, true)}>
                      通过
                    </Button>
                    <Button size="small" danger onClick={() => void decide(r.id, false)}>
                      驳回
                    </Button>
                  </Space>
                ) : (
                  <span style={{ color: '#999' }}>无权</span>
                ),
            },
          ]}
        />
      )}
      <Drawer
        title={`断言追溯 · ${trace?.subject ?? ''}`}
        open={!!trace}
        onClose={() => setTrace(null)}
        width={560}
      >
        <Table<BlacklistAssertion>
          rowKey="id"
          size="small"
          pagination={false}
          dataSource={trace?.items}
          columns={[
            {
              title: '来源',
              dataIndex: 'source',
              width: 90,
              render: (s: string) => <Tag>{SOURCE_LABEL[s] ?? s}</Tag>,
            },
            {
              title: '裁决',
              dataIndex: 'verdict',
              width: 70,
              render: (v: string) => <Tag color={VERDICT_COLOR[v]}>{v}</Tag>,
            },
            {
              title: '状态',
              dataIndex: 'status',
              width: 80,
              render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag>,
            },
            { title: '出处', dataIndex: 'source_ref', ellipsis: true },
            {
              title: '操作',
              key: 'op',
              width: 70,
              render: (_, r) =>
                canWrite && (r.status === 'active' || r.status === 'pending') ? (
                  <Button size="small" danger onClick={() => void revoke(r.id)}>
                    撤销
                  </Button>
                ) : null,
            },
          ]}
        />
      </Drawer>
      {recordOpen && (
        <RecordModal
          domain={domain}
          onClose={() => setRecordOpen(false)}
          onDone={() => {
            setRecordOpen(false)
            void load()
          }}
        />
      )}
    </>
  )
}

function RecordModal({
  domain,
  onClose,
  onDone,
}: {
  domain: string
  onClose: () => void
  onDone: () => void
}) {
  async function onFinish(v: {
    subject_norm: string
    subject_display?: string
    verdict: string
    reason?: string
  }) {
    try {
      const r = await api.post<{ canonical?: string }>(`/blacklist/assertions`, { domain, ...v })
      message.success(`已登记（canonical: ${r?.canonical ?? '?'}）`)
      onDone()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '登记失败')
    }
  }
  return (
    <Modal title={`登记断言 · ${domain}`} open onCancel={onClose} footer={null} destroyOnHidden>
      <Form layout="vertical" onFinish={onFinish} initialValues={{ verdict: 'block' }}>
        <Form.Item
          label="主体（归一化）"
          name="subject_norm"
          rules={[{ required: true, message: '请输入主体' }]}
        >
          <Input placeholder="如品牌小写归一 nike" />
        </Form.Item>
        <Form.Item label="展示名" name="subject_display">
          <Input />
        </Form.Item>
        <Form.Item label="裁决" name="verdict">
          <Select
            options={[
              { value: 'block', label: 'block 拉黑' },
              { value: 'allow', label: 'allow 白名单（压自动源）' },
            ]}
          />
        </Form.Item>
        <Form.Item label="原因" name="reason">
          <Input.TextArea rows={2} />
        </Form.Item>
        <Button type="primary" htmlType="submit" block>
          提交
        </Button>
      </Form>
    </Modal>
  )
}
