import {
  Button,
  Descriptions,
  Divider,
  Drawer,
  Empty,
  Form,
  Image,
  List,
  Modal,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  Typography,
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

interface ProductDetail {
  id: number
  master_sku: string
  source_channel: string
  source_ref: string
  title: string
  brand: string | null
  category_path: string | null
  images: string[] | null
  attrs: Record<string, unknown> | null
  price_snapshot: Record<string, unknown> | null
  status: string
  created_at: string
  updated_at: string
}

// price_snapshot 字段中文名（采集自 Amazon）
const PRICE_LABELS: Record<string, string> = {
  current_price: '当前价',
  buybox_price: 'BuyBox 价',
  original_price: '原价',
  buybox_shipping: '运费',
  is_fba: 'FBA 发货',
  total_price: '总价',
}

// attrs 字段中文名（bullets 单独渲染，其余按此表；未列出的原样显示 key）
const ATTR_LABELS: Record<string, string> = {
  model_number: '型号',
  manufacturer: '制造商',
  part_number: '部件号',
  country_of_origin: '原产国',
  is_customized: '是否定制',
  product_type: '商品类型',
  stock_status: '库存状态',
  stock_count: '库存数',
  delivery_date: '配送日期',
  delivery_time: '配送时长',
  rating: '评分',
  review_count: '评论数',
  seller_name: '卖家',
  seller_id: '卖家 ID',
  best_sellers_rank: '畅销排名',
  first_available_date: '上架日期',
  package_dimensions: '包装尺寸',
  package_weight: '包装重量',
  item_dimensions: '商品尺寸',
  item_weight: '商品重量',
  upc_list: 'UPC',
  ean_list: 'EAN',
  variation_asins: '变体 ASIN',
  parent_asin: '父体 ASIN',
  long_description: '长描述',
  crawl_time: '采集时间',
  product_url: '商品链接',
  zip_code: '配送邮编',
  site: '站点',
}

function attrText(v: unknown): string {
  if (Array.isArray(v)) return v.join('、')
  return String(v ?? '')
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
  const [detail, setDetail] = useState<ProductDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)

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
      const r = await api.post<{ verdict: string; run_id: number }>(`/products/${p.id}/audit`, {
        trigger_kind: p.status === 'ingested' ? 'manual' : 're_audit',
      })
      message.success(`审核完成：${r.verdict === 'pass' ? '✅ 通过' : '❌ 拒绝'}`)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '审核失败')
    }
  }

  async function showDetail(id: number) {
    setDetailLoading(true)
    setDetail(null)
    try {
      setDetail(await api.get<ProductDetail>(`/products/${id}`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载详情失败')
    } finally {
      setDetailLoading(false)
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
          {
            title: '标题',
            dataIndex: 'title',
            ellipsis: true,
            render: (t: string, p) => (
              <Typography.Link onClick={() => void showDetail(p.id)}>{t}</Typography.Link>
            ),
          },
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
            width: 320,
            render: (_, p) => (
              <Space>
                <Button size="small" onClick={() => void showDetail(p.id)}>
                  详情
                </Button>
                {has('audit.run') &&
                  ['ingested', 'audit_rejected', 'audit_passed'].includes(p.status) && (
                    <Button size="small" type="primary" onClick={() => void onAudit(p)}>
                      {p.status === 'ingested' ? '审核' : '重审'}
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
      <Drawer
        title={`产品详情 ${detail?.master_sku ?? ''}`}
        open={detailLoading || !!detail}
        onClose={() => setDetail(null)}
        width={640}
      >
        {detailLoading && <Spin />}
        {detail && (
          <>
            {detail.images && detail.images.length > 0 ? (
              <Image.PreviewGroup>
                <Space wrap size={8} style={{ marginBottom: 16 }}>
                  {detail.images.map((src) => (
                    <Image key={src} src={src} width={96} height={96} style={{ objectFit: 'contain' }} />
                  ))}
                </Space>
              </Image.PreviewGroup>
            ) : (
              <Empty description="无图片" style={{ marginBottom: 16 }} />
            )}

            <Descriptions column={1} size="small" bordered>
              <Descriptions.Item label="标题">{detail.title}</Descriptions.Item>
              <Descriptions.Item label="品牌">{detail.brand ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="类目">{detail.category_path ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="ASIN">
                {detail.source_channel === 'amazon' ? (
                  <a
                    href={`https://www.amazon.com/dp/${detail.source_ref}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    {detail.source_ref}
                  </a>
                ) : (
                  detail.source_ref
                )}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={STATUS_COLOR[detail.status]}>{detail.status}</Tag>
              </Descriptions.Item>
            </Descriptions>

            {detail.price_snapshot && Object.keys(detail.price_snapshot).length > 0 && (
              <>
                <Divider orientation="left">价格</Divider>
                <Descriptions column={2} size="small" bordered>
                  {Object.entries(detail.price_snapshot).map(([k, v]) => (
                    <Descriptions.Item key={k} label={PRICE_LABELS[k] ?? k}>
                      {attrText(v)}
                    </Descriptions.Item>
                  ))}
                </Descriptions>
              </>
            )}

            {Array.isArray(detail.attrs?.bullets) &&
              (detail.attrs!.bullets as string[]).length > 0 && (
                <>
                  <Divider orientation="left">五点描述</Divider>
                  <List
                    size="small"
                    dataSource={detail.attrs!.bullets as string[]}
                    renderItem={(b) => <List.Item>{b}</List.Item>}
                  />
                </>
              )}

            {detail.attrs &&
              Object.keys(detail.attrs).filter((k) => k !== 'bullets').length > 0 && (
                <>
                  <Divider orientation="left">其他采集字段</Divider>
                  <Descriptions column={1} size="small" bordered>
                    {Object.entries(detail.attrs)
                      .filter(([k]) => k !== 'bullets')
                      .map(([k, v]) => (
                        <Descriptions.Item key={k} label={ATTR_LABELS[k] ?? k}>
                          {attrText(v)}
                        </Descriptions.Item>
                      ))}
                  </Descriptions>
                </>
              )}
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
