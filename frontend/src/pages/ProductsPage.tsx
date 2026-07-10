import {
  Button,
  Descriptions,
  Drawer,
  Form,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  message,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type PageOf } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'

interface Product {
  id: number
  master_sku: string
  source_ref: string
  title: string
  brand: string | null
  status: string
  latest_audit_run_id: number | null
}

interface AuditHit {
  level: string
  rule_code: string
  is_hard: boolean
  evidence: Record<string, unknown>
}

interface AuditRunDetail {
  id: number
  verdict: string | null
  reject_level: string | null
  llm_cost_usd: number
  cache_hit_rate: number | null
  duration_ms: number | null
  hits: AuditHit[]
}

interface Store {
  id: number
  code: string
  name: string
}

const STATUS_COLOR: Record<string, string> = {
  ingested: 'default',
  auditing: 'processing',
  audit_passed: 'green',
  audit_rejected: 'red',
  sourcing: 'gold',
  ready: 'cyan',
  listed: 'blue',
  retired: 'default',
}

export default function ProductsPage() {
  const { has } = useAuth()
  const [data, setData] = useState<PageOf<Product> | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState<string | undefined>()
  const [auditDetail, setAuditDetail] = useState<AuditRunDetail | null>(null)
  const [allocateFor, setAllocateFor] = useState<Product | null>(null)
  const [stores, setStores] = useState<Store[]>([])

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const qs = status ? `&status=${status}` : ''
      setData(await api.get<PageOf<Product>>(`/products?page=${page}&size=20${qs}`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page, status])

  useEffect(() => {
    void load()
  }, [load])

  async function onAudit(p: Product) {
    try {
      const r = await api.post<{ verdict: string; run_id: number }>(`/products/${p.id}/audit`, {})
      message.success(`审核完成：${r.verdict === 'pass' ? '✅ 通过' : '❌ 拒绝'}`)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '审核失败')
    }
  }

  async function showAudit(runId: number) {
    try {
      setAuditDetail(await api.get<AuditRunDetail>(`/audit-runs/${runId}`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    }
  }

  async function openAllocate(p: Product) {
    setAllocateFor(p)
    try {
      const r = await api.get<PageOf<Store>>(`/stores?page=1&size=100`)
      setStores(r.items)
    } catch {
      /* 列表失败时下拉为空 */
    }
  }

  async function onAllocate(values: { store_id: number; offer_mode: string }) {
    if (!allocateFor) return
    try {
      const r = await api.post<{
        created: unknown[]
        rejected: { code: string; message: string }[]
      }>(`/listings/allocate`, { product_ids: [allocateFor.id], ...values })
      if (r.created.length) {
        message.success('已分配为上架草稿（前往上架管理提交）')
      } else {
        message.warning(`分配被拒：${r.rejected[0]?.code} ${r.rejected[0]?.message ?? ''}`)
      }
      setAllocateFor(null)
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '分配失败')
    }
  }

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        <Select
          allowClear
          placeholder="按状态筛选"
          style={{ width: 160 }}
          value={status}
          onChange={setStatus}
          options={Object.keys(STATUS_COLOR).map((s) => ({ value: s, label: s }))}
        />
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      <Table<Product>
        rowKey="id"
        loading={loading}
        dataSource={data?.items}
        pagination={{ current: page, total: data?.total, pageSize: 20, onChange: setPage }}
        columns={[
          { title: 'SKU', dataIndex: 'master_sku', width: 110 },
          { title: 'ASIN', dataIndex: 'source_ref', width: 130 },
          { title: '标题', dataIndex: 'title', ellipsis: true },
          { title: '品牌', dataIndex: 'brand', width: 120 },
          {
            title: '状态',
            dataIndex: 'status',
            width: 130,
            render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag>,
          },
          {
            title: '操作',
            key: 'op',
            width: 260,
            render: (_, p) => (
              <Space>
                {has('audit.run') && ['ingested', 'audit_rejected'].includes(p.status) && (
                  <Button size="small" type="primary" onClick={() => void onAudit(p)}>
                    审核
                  </Button>
                )}
                {has('audit.read') && p.latest_audit_run_id && (
                  <Button size="small" onClick={() => void showAudit(p.latest_audit_run_id!)}>
                    审核详情
                  </Button>
                )}
                {has('listing.allocate') && ['audit_passed', 'ready'].includes(p.status) && (
                  <Button size="small" onClick={() => void openAllocate(p)}>
                    分配上架
                  </Button>
                )}
              </Space>
            ),
          },
        ]}
      />
      <Drawer
        title={`审核运行 #${auditDetail?.id ?? ''}`}
        open={!!auditDetail}
        onClose={() => setAuditDetail(null)}
        width={560}
      >
        {auditDetail && (
          <>
            <Descriptions column={2} size="small" bordered style={{ marginBottom: 16 }}>
              <Descriptions.Item label="判定">
                <Tag color={auditDetail.verdict === 'pass' ? 'green' : 'red'}>
                  {auditDetail.verdict}
                </Tag>
              </Descriptions.Item>
              <Descriptions.Item label="否决层">
                {auditDetail.reject_level ?? '—'}
              </Descriptions.Item>
              <Descriptions.Item label="LLM 成本">
                ${Number(auditDetail.llm_cost_usd).toFixed(6)}
              </Descriptions.Item>
              <Descriptions.Item label="缓存命中率">
                {auditDetail.cache_hit_rate ?? '—'}
              </Descriptions.Item>
            </Descriptions>
            <Table<AuditHit>
              rowKey={(h) => `${h.level}-${h.rule_code}`}
              size="small"
              dataSource={auditDetail.hits}
              pagination={false}
              columns={[
                { title: '层', dataIndex: 'level', width: 60 },
                { title: '规则', dataIndex: 'rule_code' },
                {
                  title: '硬拒',
                  dataIndex: 'is_hard',
                  width: 70,
                  render: (v: boolean) => (v ? <Tag color="red">是</Tag> : <Tag>否</Tag>),
                },
                {
                  title: '证据',
                  dataIndex: 'evidence',
                  ellipsis: true,
                  render: (e: Record<string, unknown>) => JSON.stringify(e),
                },
              ]}
            />
          </>
        )}
      </Drawer>
      <Modal
        title={`分配上架：${allocateFor?.master_sku ?? ''}`}
        open={!!allocateFor}
        onCancel={() => setAllocateFor(null)}
        footer={null}
        destroyOnHidden
      >
        <Form layout="vertical" onFinish={onAllocate} initialValues={{ offer_mode: 'build' }}>
          <Form.Item name="store_id" label="店铺" rules={[{ required: true }]}>
            <Select
              options={stores.map((s) => ({ value: s.id, label: `${s.code} ${s.name}` }))}
            />
          </Form.Item>
          <Form.Item name="offer_mode" label="模式" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'build', label: 'build 自建（占用 GTIN）' },
                { value: 'match', label: 'match 跟卖' },
              ]}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            分配
          </Button>
        </Form>
      </Modal>
    </>
  )
}
