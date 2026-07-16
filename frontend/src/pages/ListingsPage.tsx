import {
  Button,
  Drawer,
  Form,
  InputNumber,
  Modal,
  Space,
  Table,
  Tabs,
  Tag,
  Timeline,
  message,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type PageOf } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'

interface Listing {
  id: number
  store_id: number
  product_id: number
  offer_mode: string
  channel_sku: string
  gtin: string | null
  status: string
  error_code: string | null
  is_locked: boolean
  wpid: string | null
  current_price: number | null
  current_inventory: number
}

interface StateHistory {
  from_status: string
  to_status: string
  reason_code: string | null
  actor_type: string
  occurred_at: string
}

// R2-06：GET /listings/{id} 新增 price_history（时间倒序，≤50 条）；旧后端无此字段
interface PriceHistory {
  old_price: number | null
  new_price: number
  reason: 'initial' | 'manual' | 'strategy' | 'watchdog'
  strategy_id: number | null
  strategy_version: number | null
  detail: { formula?: string } | null
  created_at: string
}

interface Feed {
  id: number
  store_id: number
  channel_feed_id: string | null
  feed_kind: string
  status: string
  item_count: number
  submitted_at: string | null
  created_at: string
}

const L_COLOR: Record<string, string> = {
  draft: 'default',
  queued: 'gold',
  submitted: 'processing',
  processing: 'processing',
  published: 'cyan',
  live: 'green',
  degraded: 'orange',
  delist_pending: 'orange',
  delisted: 'default',
  failed: 'red',
  retired: 'default',
}
// 改价（HF-0716）与提交同权限点 listing.submit；仅 draft/failed 且未渠道锁定可改
// draft/failed=本地直改（HF-0716 运营自救）；live/published=转定价管道三段式
// （R2-06 验收②缺陷修复：后端 PATCH 早已分流 live，前端门控漏放宽致 live 无改价按钮）
const REPRICEABLE = ['draft', 'failed', 'live', 'published']

const PRICE_REASON: Record<string, string> = {
  initial: '初始定价',
  manual: '手动改价',
  strategy: '策略重定价',
  watchdog: '盯价',
}

const F_COLOR: Record<string, string> = {
  building: 'default',
  submitting: 'processing',
  verify_pending: 'orange',
  submitted: 'processing',
  processing: 'processing',
  processed: 'green',
  partial: 'orange',
  error: 'red',
  lost: 'red',
}

export default function ListingsPage() {
  const { has } = useAuth()
  const [listings, setListings] = useState<PageOf<Listing> | null>(null)
  const [feeds, setFeeds] = useState<PageOf<Feed> | null>(null)
  const [loading, setLoading] = useState(false)
  const [selected, setSelected] = useState<number[]>([])
  const [history, setHistory] = useState<{
    id: number
    items: StateHistory[]
    prices: PriceHistory[]
  } | null>(null)
  const [reprice, setReprice] = useState<Listing | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setListings(await api.get<PageOf<Listing>>(`/listings?page=1&size=50`))
      setFeeds(await api.get<PageOf<Feed>>(`/feeds?page=1&size=50`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function onSubmit() {
    try {
      const r = await api.post<{
        queued: number
        feed_status?: string
        dry_run?: boolean
        skipped: { listing_id: number; code: string }[]
      }>(`/listings/submit`, { listing_ids: selected })
      if (r.dry_run) {
        message.info(`dry-run 模式：已生成提交快照（${r.queued} 项，未真实发包）`)
      } else if (r.queued > 0) {
        message.success(`已提交 ${r.queued} 项（feed: ${r.feed_status}）`)
      }
      if (r.skipped.length) {
        message.warning(`跳过 ${r.skipped.length} 项：${r.skipped.map((s) => s.code).join(', ')}`, 6)
      }
      setSelected([])
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '提交失败')
    }
  }

  async function showHistory(id: number) {
    try {
      const d = await api.get<{ state_history: StateHistory[]; price_history?: PriceHistory[] }>(
        `/listings/${id}`,
      )
      setHistory({ id, items: d.state_history, prices: d.price_history ?? [] })
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    }
  }

  async function op(path: string, ok: string) {
    try {
      await api.post(path, {})
      message.success(ok)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '操作失败')
    }
  }

  return (
    <>
      <Tabs
        items={[
          {
            key: 'listings',
            label: '刊登',
            children: (
              <>
                <Space style={{ marginBottom: 16 }}>
                  {has('listing.submit') && (
                    <Button type="primary" disabled={!selected.length} onClick={() => void onSubmit()}>
                      提交上架（{selected.length}）
                    </Button>
                  )}
                  <Button onClick={() => void load()}>刷新</Button>
                </Space>
                <Table<Listing>
                  rowKey="id"
                  loading={loading}
                  dataSource={listings?.items}
                  rowSelection={{
                    selectedRowKeys: selected,
                    onChange: (keys) => setSelected(keys as number[]),
                    getCheckboxProps: (r) => ({
                      disabled: !['draft', 'queued'].includes(r.status),
                    }),
                  }}
                  pagination={false}
                  columns={[
                    { title: 'ID', dataIndex: 'id', width: 70 },
                    { title: 'SKU', dataIndex: 'channel_sku', width: 110 },
                    { title: 'GTIN', dataIndex: 'gtin', width: 130 },
                    { title: '模式', dataIndex: 'offer_mode', width: 80 },
                    {
                      title: '状态',
                      dataIndex: 'status',
                      width: 120,
                      render: (s: string, r) => (
                        <Space size={4}>
                          <Tag color={L_COLOR[s]}>{s}</Tag>
                          {r.error_code && <Tag color="red">{r.error_code}</Tag>}
                        </Space>
                      ),
                    },
                    {
                      title: '价格',
                      dataIndex: 'current_price',
                      width: 110,
                      render: (v: number | null, r) => (
                        <Space size={4}>
                          {v}
                          {!v && REPRICEABLE.includes(r.status) && <Tag color="red">无价</Tag>}
                        </Space>
                      ),
                    },
                    { title: 'WPID', dataIndex: 'wpid', width: 130 },
                    {
                      title: '操作',
                      key: 'op',
                      render: (_, r) => (
                        <Space>
                          <Button size="small" onClick={() => void showHistory(r.id)}>
                            历史
                          </Button>
                          {has('listing.delist') && r.status === 'live' && (
                            <Button
                              size="small"
                              danger
                              onClick={() => void op(`/listings/${r.id}/delist`, '已下架')}
                            >
                              下架
                            </Button>
                          )}
                          {has('listing.submit') && r.status === 'failed' && (
                            <Button
                              size="small"
                              onClick={() => void op(`/listings/${r.id}/retry`, '已重投队列')}
                            >
                              重投
                            </Button>
                          )}
                          {has('listing.submit') &&
                            REPRICEABLE.includes(r.status) &&
                            !r.is_locked && (
                              <Button size="small" onClick={() => setReprice(r)}>
                                改价
                              </Button>
                            )}
                        </Space>
                      ),
                    },
                  ]}
                />
              </>
            ),
          },
          {
            key: 'feeds',
            label: 'Feed 提交',
            children: (
              <Table<Feed>
                rowKey="id"
                loading={loading}
                dataSource={feeds?.items}
                pagination={false}
                columns={[
                  { title: 'ID', dataIndex: 'id', width: 70 },
                  { title: '渠道 FeedId', dataIndex: 'channel_feed_id', width: 200 },
                  { title: '类型', dataIndex: 'feed_kind', width: 110 },
                  {
                    title: '状态',
                    dataIndex: 'status',
                    width: 130,
                    render: (s: string) => <Tag color={F_COLOR[s]}>{s}</Tag>,
                  },
                  { title: '条数', dataIndex: 'item_count', width: 70 },
                  { title: '提交时间', dataIndex: 'submitted_at', width: 180 },
                  {
                    title: '操作',
                    key: 'op',
                    render: (_, f) =>
                      has('listing.submit') && (
                        <Space>
                          {['submitted', 'processing'].includes(f.status) && (
                            <Button
                              size="small"
                              onClick={() => void op(`/feeds/${f.id}/poll`, '已轮询')}
                            >
                              轮询
                            </Button>
                          )}
                          {f.status === 'verify_pending' && (
                            <Button
                              size="small"
                              type="primary"
                              onClick={() => void op(`/feeds/${f.id}/verify-back`, '对账完成')}
                            >
                              对账归位
                            </Button>
                          )}
                        </Space>
                      ),
                  },
                ]}
              />
            ),
          },
        ]}
      />
      <Drawer
        title={`Listing #${history?.id ?? ''} 历史`}
        open={!!history}
        onClose={() => setHistory(null)}
        width={480}
      >
        <div style={{ fontWeight: 600, marginBottom: 8 }}>价格历史</div>
        {history?.prices.length ? (
          <Table<PriceHistory>
            rowKey={(_, i) => i ?? 0}
            size="small"
            dataSource={history.prices}
            pagination={false}
            style={{ marginBottom: 24 }}
            columns={[
              {
                title: '时间',
                dataIndex: 'created_at',
                width: 150,
                render: (v: string) => new Date(v).toLocaleString('zh-CN'),
              },
              {
                title: '价格',
                key: 'price',
                width: 110,
                render: (_, p) => `${p.old_price ?? '—'} → ${p.new_price}`,
              },
              {
                title: '原因',
                dataIndex: 'reason',
                width: 90,
                render: (r: string) => <Tag>{PRICE_REASON[r] ?? r}</Tag>,
              },
              {
                title: '公式',
                key: 'formula',
                render: (_, p) =>
                  p.detail?.formula ? (
                    <div
                      title={p.detail.formula}
                      style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}
                    >
                      {p.detail.formula}
                    </div>
                  ) : null,
              },
            ]}
          />
        ) : (
          <div style={{ color: '#999', marginBottom: 24 }}>暂无</div>
        )}
        <div style={{ fontWeight: 600, marginBottom: 8 }}>状态历史</div>
        <Timeline
          items={history?.items.map((h) => ({
            children: (
              <>
                <b>
                  {h.from_status} → {h.to_status}
                </b>
                {h.reason_code && <Tag style={{ marginLeft: 8 }}>{h.reason_code}</Tag>}
                <div style={{ color: '#999', fontSize: 12 }}>
                  {h.actor_type} · {h.occurred_at}
                </div>
              </>
            ),
          }))}
        />
      </Drawer>
      {reprice && (
        <RepriceModal
          listing={reprice}
          onClose={() => setReprice(null)}
          onDone={() => {
            setReprice(null)
            void load()
          }}
        />
      )}
    </>
  )
}

function RepriceModal({
  listing,
  onClose,
  onDone,
}: {
  listing: Listing
  onClose: () => void
  onDone: () => void
}) {
  async function submit(price: number, force: boolean) {
    try {
      // PATCH /listings/{id} 为契约新增端点，schema.d.ts 尚未重新生成，先走宽松调用
      const r = (await api.patch(
        `/listings/${listing.id}`,
        force ? { price, force: true } : { price },
      )) as { status?: string }
      // live/published 走定价管道三段式：非 succeeded 即在途（渠道确认后才回填价格）
      if (r?.status && r.status !== 'succeeded') {
        message.success('改价已提交渠道，确认后自动回填（列表价格暂不变属正常）')
      } else {
        message.success('改价成功')
      }
      onDone()
    } catch (e) {
      // BR-PR-008：变动超确认阈值（默认 30%）→ 422 PRICING_CONFIRM_REQUIRED（detail 带 old/new），
      // 弹确认后带 { price, force: true } 重发
      if (e instanceof ApiError && e.code === 'PRICING_CONFIRM_REQUIRED') {
        const d = (e.detail ?? {}) as { old?: number; new?: number }
        Modal.confirm({
          title: '价格变动超过确认阈值',
          content: `${e.message}：${d.old ?? '?'} → ${d.new ?? price}。确认强制改价？`,
          okText: '强制改价',
          okButtonProps: { danger: true },
          cancelText: '取消',
          onOk: () => submit(price, true),
        })
        return
      }
      // 其余业务拒绝（含低于底线的 PRICING_BELOW_MIN_PRICE）直接展示后端 message
      message.error(e instanceof ApiError ? e.message : '改价失败')
    }
  }

  async function onFinish(values: { price: number }) {
    await submit(values.price, false)
  }

  return (
    <Modal
      title={`改价 - ${listing.channel_sku}`}
      open
      onCancel={onClose}
      footer={null}
      destroyOnHidden
    >
      <Form
        layout="vertical"
        onFinish={onFinish}
        initialValues={{ price: listing.current_price || undefined }}
      >
        <Form.Item label="价格" name="price" rules={[{ required: true, message: '请输入价格' }]}>
          <InputNumber min={0.01} precision={2} style={{ width: '100%' }} />
        </Form.Item>
        <Button type="primary" htmlType="submit" block>
          确认改价
        </Button>
      </Form>
    </Modal>
  )
}
