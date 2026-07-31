import {
  Button,
  Card,
  Checkbox,
  Descriptions,
  Drawer,
  Form,
  Input,
  InputNumber,
  Modal,
  Select,
  Space,
  Spin,
  Table,
  Tag,
  message,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type PageOf, type Schemas } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'

// 契约类型（schema.d.ts codegen）；OrderDetail 内嵌结构在契约里是 free-form object，本地补形态
type Order = Schemas['Order']
type ProcurementOrder = Schemas['ProcurementOrder']
type Purchaser = Schemas['Purchaser']

interface OrderLine {
  channel_line_no?: string
  channel_sku?: string
  qty?: number
  unit_price?: number
  line_status?: string
  tracking_no?: string | null
}

interface OrderCheck {
  check_kind?: string
  result?: string
  detail?: Record<string, unknown>
  resolved_at?: string | null
}

interface OrderDetail extends Order {
  ship_to?: Record<string, unknown>
  lines?: OrderLine[]
  checks?: OrderCheck[]
  procurement?: ProcurementOrder | null
}

interface StoreOpt {
  id: number
  code: string
  name: string
}

const INTERNAL_STATUS: Record<string, { color: string; label: string }> = {
  pulled: { color: 'default', label: '已拉取' },
  checked: { color: 'cyan', label: '已检查' },
  assigned: { color: 'gold', label: '已分配' },
  purchasing: { color: 'processing', label: '采购中' },
  shipped: { color: 'blue', label: '已发货' },
  completed: { color: 'green', label: '已完成' },
  cancelled: { color: 'default', label: '已取消' },
  refunded: { color: 'orange', label: '已退款' },
}

const PO_STATUS: Record<string, { color: string; label: string }> = {
  unassigned: { color: 'default', label: '待分配' },
  assigned: { color: 'gold', label: '已分配' },
  claimed: { color: 'processing', label: '已领单' },
  purchased: { color: 'cyan', label: '已采购' },
  shipped: { color: 'blue', label: '已发货' },
  backfilled: { color: 'green', label: '已回填' },
  exception: { color: 'red', label: '异常' },
  cancelled: { color: 'default', label: '已取消' },
}

// 采购异常分类中文名（R2-13 0045 `exception_kind`）。
// ⚠️ 它是**问题分类不是状态**：回填后核对不通过（ASIN/邮编/金额不平）属「已花钱但有问题」，
// 这时 status 仍是 purchased 而本列非空——面板必须把两者分开显示，
// 否则运营会把「已拍单但有问题」看成「没拍单」然后重拍一次。
const EXCEPTION_KIND: Record<string, string> = {
  address: '地址问题',
  stock: '无库存/售罄',
  product: '商品不可自动采购（非 FBA / 捆绑）',
  delivery: '预计送达超期',
  checkout: '结账/下单失败',
  price_guard: '价格护栏拦截',
  channel_cancelled: '渠道已取消或退款',
  order_not_found: '渠道查无此单',
  other: '其他',
}

// 四检 check_kind 中文名（契约枚举 phishing/purchaser/price_limit/consistency）
const CHECK_LABEL: Record<string, string> = {
  phishing: '钓鱼地址',
  purchaser: '采购方',
  price_limit: '限价',
  consistency: '一致性',
}

// 承运商白名单（BR：USPS/UPS/FedEx/DHL/OnTrac/Other）
const CARRIERS = ['USPS', 'UPS', 'FedEx', 'DHL', 'OnTrac', 'Other']

const SHIP_TO_LABELS: Record<string, string> = {
  name: '收件人',
  phone: '电话',
  postalAddress: '邮寄地址',
  address1: '地址 1',
  address2: '地址 2',
  addressLine1: '地址 1',
  addressLine2: '地址 2',
  city: '城市',
  state: '州',
  postal_code: '邮编',
  postalCode: '邮编',
  country: '国家',
  countryCode: '国家',
}

function statusTag(map: Record<string, { color: string; label: string }>, s?: string) {
  if (!s) return '—'
  const m = map[s]
  return <Tag color={m?.color}>{m ? `${s} ${m.label}` : s}</Tag>
}

function plainText(v: unknown): string {
  if (v == null) return '—'
  if (typeof v === 'object') {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${SHIP_TO_LABELS[k] ?? k}: ${plainText(x)}`)
      .join('，')
  }
  return String(v)
}

export default function OrdersPage() {
  const [data, setData] = useState<PageOf<Order> | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [storeId, setStoreId] = useState<number | undefined>()
  const [status, setStatus] = useState<string | undefined>()
  const [flagOnly, setFlagOnly] = useState(false)
  const [stores, setStores] = useState<StoreOpt[]>([])
  const [detailId, setDetailId] = useState<number | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const qs = [
        `page=${page}`,
        'size=20',
        storeId != null ? `store_id=${storeId}` : '',
        status ? `internal_status=${status}` : '',
        flagOnly ? 'has_flag=true' : '',
      ]
        .filter(Boolean)
        .join('&')
      setData(await api.get<PageOf<Order>>(`/orders?${qs}`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page, storeId, status, flagOnly])

  useEffect(() => {
    void load()
  }, [load])

  useEffect(() => {
    void api
      .get<PageOf<StoreOpt>>('/stores?page=1&size=100')
      .then((r) => setStores(r.items))
      .catch(() => {
        /* 无店铺读权限时下拉保持为空 */
      })
  }, [])

  return (
    <>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          allowClear
          placeholder="按店铺筛选"
          style={{ width: 180 }}
          value={storeId}
          onChange={(v) => {
            setStoreId(v)
            setPage(1)
          }}
          options={stores.map((s) => ({ value: s.id, label: `${s.code} ${s.name}` }))}
        />
        <Select
          allowClear
          placeholder="按内部状态筛选"
          style={{ width: 180 }}
          value={status}
          onChange={(v) => {
            setStatus(v)
            setPage(1)
          }}
          options={Object.entries(INTERNAL_STATUS).map(([k, v]) => ({
            value: k,
            label: `${k} ${v.label}`,
          }))}
        />
        <Checkbox
          checked={flagOnly}
          onChange={(e) => {
            setFlagOnly(e.target.checked)
            setPage(1)
          }}
        >
          仅看四检命中
        </Checkbox>
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      <Table<Order>
        rowKey={(r) => r.id ?? 0}
        loading={loading}
        dataSource={data?.items}
        pagination={{ current: page, pageSize: 20, total: data?.total ?? 0, onChange: setPage }}
        onRow={(r) => ({
          onClick: () => setDetailId(r.id ?? null),
          style: { cursor: 'pointer' },
        })}
        columns={[
          { title: '渠道单号', dataIndex: 'channel_order_no', width: 180 },
          {
            title: '下单时间',
            dataIndex: 'order_date',
            width: 170,
            render: (v?: string) => (v ? new Date(v).toLocaleString('zh-CN') : '—'),
          },
          { title: '渠道状态', dataIndex: 'channel_status', width: 110 },
          {
            title: '内部状态',
            dataIndex: 'internal_status',
            width: 150,
            render: (s?: string) => statusTag(INTERNAL_STATUS, s),
          },
          {
            title: '金额',
            dataIndex: 'order_total',
            width: 110,
            render: (v: number | undefined, r) => (v != null ? `${v} ${r.currency ?? ''}` : '—'),
          },
          { title: '行数', dataIndex: 'item_count', width: 70 },
          {
            title: '四检',
            dataIndex: 'has_flag',
            width: 90,
            render: (v?: boolean) => (v ? <Tag color="red">命中</Tag> : '—'),
          },
        ]}
      />
      {detailId != null && (
        <OrderDrawer
          orderId={detailId}
          stores={stores}
          onClose={() => setDetailId(null)}
          onChanged={() => void load()}
        />
      )}
    </>
  )
}

function OrderDrawer({
  orderId,
  stores,
  onClose,
  onChanged,
}: {
  orderId: number
  stores: StoreOpt[]
  onClose: () => void
  onChanged: () => void
}) {
  const { has } = useAuth()
  const [detail, setDetail] = useState<OrderDetail | null>(null)
  const [purchasers, setPurchasers] = useState<Purchaser[]>([])
  const [assignId, setAssignId] = useState<number | undefined>()
  const [shipOpen, setShipOpen] = useState(false)
  const [backfillOpen, setBackfillOpen] = useState(false)
  const [pluginBackfillOpen, setPluginBackfillOpen] = useState(false)
  const [exceptionOpen, setExceptionOpen] = useState(false)

  const load = useCallback(async () => {
    try {
      setDetail(await api.get<OrderDetail>(`/orders/${orderId}`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载详情失败')
    }
  }, [orderId])

  useEffect(() => {
    void load()
  }, [load])

  const canCheck = has('order.check')
  const canAssign = has('order.assign')
  const canExec = has('procurement.execute')

  useEffect(() => {
    if (!canAssign) return
    void api
      .get<Purchaser[]>('/purchasers')
      .then(setPurchasers)
      .catch(() => {
        /* 无采购方读权限时下拉保持为空 */
      })
  }, [canAssign])

  // 操作成功后：刷新抽屉详情 + 通知父级刷新列表
  async function op(fn: () => Promise<unknown>, ok: string) {
    try {
      await fn()
      message.success(ok)
      void load()
      onChanged()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '操作失败')
    }
  }

  const po = detail?.procurement ?? null
  const store = stores.find((s) => s.id === detail?.store_id)
  const canShip =
    !!detail &&
    has('order.ship') &&
    !['cancelled', 'refunded', 'shipped'].includes(detail.internal_status ?? '')

  return (
    <Drawer
      title={`订单 ${detail?.channel_order_no ?? `#${orderId}`}`}
      open
      width={760}
      onClose={onClose}
      extra={
        canShip ? (
          <Button type="primary" onClick={() => setShipOpen(true)}>
            发货
          </Button>
        ) : null
      }
    >
      {!detail && <Spin />}
      {detail && (
        <>
          <Descriptions column={2} size="small" bordered>
            <Descriptions.Item label="渠道单号">
              {detail.channel_order_no ?? '—'}
            </Descriptions.Item>
            <Descriptions.Item label="店铺">
              {store ? `${store.code} ${store.name}` : `#${detail.store_id ?? '—'}`}
            </Descriptions.Item>
            <Descriptions.Item label="下单时间">
              {detail.order_date ? new Date(detail.order_date).toLocaleString('zh-CN') : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="渠道状态">{detail.channel_status ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="内部状态">
              {statusTag(INTERNAL_STATUS, detail.internal_status)}
            </Descriptions.Item>
            <Descriptions.Item label="金额">
              {detail.order_total != null
                ? `${detail.order_total} ${detail.currency ?? ''}`
                : '—'}
            </Descriptions.Item>
            <Descriptions.Item label="四检命中">
              {detail.has_flag ? <Tag color="red">命中</Tag> : <Tag color="green">无</Tag>}
            </Descriptions.Item>
            <Descriptions.Item label="行数">{detail.item_count ?? '—'}</Descriptions.Item>
          </Descriptions>

          <Card size="small" title="收件地址" style={{ marginTop: 16 }}>
            {detail.ship_to && Object.keys(detail.ship_to).length > 0 ? (
              <Descriptions column={2} size="small">
                {Object.entries(detail.ship_to).map(([k, v]) => (
                  <Descriptions.Item key={k} label={SHIP_TO_LABELS[k] ?? k}>
                    {plainText(v)}
                  </Descriptions.Item>
                ))}
              </Descriptions>
            ) : (
              <span style={{ color: '#888' }}>—</span>
            )}
          </Card>

          <Card size="small" title="订单行" style={{ marginTop: 16 }}>
            <Table<OrderLine>
              rowKey={(l) => l.channel_line_no ?? ''}
              size="small"
              pagination={false}
              dataSource={detail.lines ?? []}
              columns={[
                { title: '行号', dataIndex: 'channel_line_no', width: 70 },
                { title: 'SKU', dataIndex: 'channel_sku' },
                { title: '数量', dataIndex: 'qty', width: 70 },
                { title: '单价', dataIndex: 'unit_price', width: 90 },
                { title: '行状态', dataIndex: 'line_status', width: 110 },
                {
                  title: '运单号',
                  dataIndex: 'tracking_no',
                  width: 150,
                  render: (v?: string | null) => v ?? '—',
                },
              ]}
            />
          </Card>

          <Card
            size="small"
            title="四检"
            style={{ marginTop: 16 }}
            extra={
              canCheck ? (
                <Button
                  size="small"
                  onClick={() =>
                    void op(() => api.post(`/orders/${orderId}/checks/rerun`), '四检已重跑')
                  }
                >
                  重跑四检
                </Button>
              ) : null
            }
          >
            <Table<OrderCheck>
              rowKey={(c) => c.check_kind ?? ''}
              size="small"
              pagination={false}
              dataSource={detail.checks ?? []}
              columns={[
                {
                  title: '检查项',
                  dataIndex: 'check_kind',
                  width: 100,
                  render: (k?: string) => (k ? (CHECK_LABEL[k] ?? k) : '—'),
                },
                {
                  title: '结果',
                  dataIndex: 'result',
                  width: 80,
                  render: (r?: string) =>
                    r === 'pass' ? (
                      <Tag color="green">通过</Tag>
                    ) : r === 'flagged' ? (
                      <Tag color="red">命中</Tag>
                    ) : (
                      <Tag>{r ?? '—'}</Tag>
                    ),
                },
                {
                  title: '证据',
                  dataIndex: 'detail',
                  ellipsis: true,
                  render: (d?: Record<string, unknown>) =>
                    d && Object.keys(d).length > 0 ? JSON.stringify(d) : '—',
                },
                {
                  title: '放行时间',
                  dataIndex: 'resolved_at',
                  width: 160,
                  render: (v?: string | null) => (v ? new Date(v).toLocaleString('zh-CN') : '—'),
                },
                {
                  title: '操作',
                  key: 'op',
                  width: 80,
                  render: (_, c) =>
                    canCheck && c.result === 'flagged' && !c.resolved_at ? (
                      <Button
                        size="small"
                        type="primary"
                        onClick={() =>
                          void op(
                            () => api.post(`/orders/${orderId}/checks/${c.check_kind}/resolve`),
                            '已放行',
                          )
                        }
                      >
                        放行
                      </Button>
                    ) : null,
                },
              ]}
            />
          </Card>

          <Card
            size="small"
            title="采购执行"
            style={{ marginTop: 16 }}
            extra={
              !po && canAssign ? (
                <Button
                  size="small"
                  type="primary"
                  onClick={() =>
                    void op(
                      () => api.post('/procurement-orders', { order_id: orderId }),
                      '执行单已创建',
                    )
                  }
                >
                  建执行单
                </Button>
              ) : null
            }
          >
            {po ? (
              <>
                <Descriptions column={2} size="small" bordered>
                  <Descriptions.Item label="状态">
                    {statusTag(PO_STATUS, po.status)}
                  </Descriptions.Item>
                  <Descriptions.Item label="采购方">
                    {/* 服务端两段式解析优先（§7.1.1(b)：已删采购方显示「XX（已删除）」），
                        客户端点名仅作旧数据回落 */}
                    {po.purchaser_label ??
                      (po.purchaser_id != null
                        ? (purchasers.find((p) => p.id === po.purchaser_id)?.name ??
                          `#${po.purchaser_id}`)
                        : '—')}
                  </Descriptions.Item>
                  {/* 买家账号非空 = 这单走的是插件自动采购路径（人工/门户路径该列恒 NULL）。
                      两条路径互斥由库层唯一索引兜底，面板把它显式摆出来，
                      运营才不会对一张机器人正在执行的单再点一次「分配采购方」。 */}
                  <Descriptions.Item label="买家账号">
                    {po.buyer_account_label ??
                      (po.buyer_account_id != null ? `#${po.buyer_account_id}` : '—')}
                  </Descriptions.Item>
                  <Descriptions.Item label="采购成本">
                    {po.purchase_cost != null
                      ? `${po.purchase_cost} ${po.purchase_currency ?? ''}`
                      : '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label="销售税">{po.tax_amount ?? '—'}</Descriptions.Item>
                  <Descriptions.Item label="运费">{po.freight_cost ?? '—'}</Descriptions.Item>
                  <Descriptions.Item label="锁定汇率">
                    {po.exchange_rate_locked ?? '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label="物流">
                    {po.carrier ? `${po.carrier} / ${po.tracking_no ?? '—'}` : '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label="采购平台">
                    {po.purchase_platform ?? '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label="平台单号">
                    {po.purchase_order_ref ?? '—'}
                  </Descriptions.Item>
                  <Descriptions.Item label="付款卡尾号">
                    {po.payment_card_last4 ?? '—'}
                  </Descriptions.Item>
                  {/* 悬浮显示渠道原文：解析成的日期与结账页原文都留着，
                      对不上时人能一眼看出是我们解析错了还是渠道就那么写的 */}
                  <Descriptions.Item label="预计送达">
                    {po.delivery_est_date ? (
                      <span title={po.delivery_est_raw ?? undefined}>
                        {po.delivery_est_date}
                        {po.delivery_est_raw ? '（悬浮看渠道原文）' : ''}
                      </span>
                    ) : (
                      (po.delivery_est_raw ?? '—')
                    )}
                  </Descriptions.Item>
                  {po.exception_kind && (
                    <Descriptions.Item label="问题分类" span={2}>
                      <Tag color={po.status === 'exception' ? 'red' : 'orange'}>
                        {EXCEPTION_KIND[po.exception_kind] ?? po.exception_kind}
                      </Tag>
                      {po.status !== 'exception' && (
                        <span style={{ color: '#999' }}>
                          单据状态不是「异常」——这单<b>已经拍出去了</b>
                          ，只是回填后核对有问题，请勿重拍
                        </span>
                      )}
                    </Descriptions.Item>
                  )}
                  {po.exception_reason && (
                    <Descriptions.Item label="异常原因" span={2}>
                      {po.exception_reason}
                    </Descriptions.Item>
                  )}
                </Descriptions>
                <Space style={{ marginTop: 12 }} wrap>
                  {canAssign && ['unassigned', 'assigned'].includes(po.status ?? '') && (
                    <Space.Compact>
                      <Select
                        size="small"
                        style={{ width: 180 }}
                        placeholder="选择采购方"
                        value={assignId}
                        onChange={setAssignId}
                        options={purchasers
                          .filter((p) => p.status === 'active')
                          .map((p) => ({
                            value: p.id,
                            label: `${p.name}（${p.purchaser_kind === 'internal' ? '内部' : '外部'}）`,
                          }))}
                      />
                      <Button
                        size="small"
                        type="primary"
                        disabled={assignId == null}
                        onClick={() =>
                          void op(
                            () =>
                              api.post(`/procurement-orders/${po.id}/assign`, {
                                purchaser_id: assignId,
                              }),
                            '已分配',
                          )
                        }
                      >
                        分配
                      </Button>
                    </Space.Compact>
                  )}
                  {canExec && ['unassigned', 'assigned'].includes(po.status ?? '') && (
                    <Button
                      size="small"
                      onClick={() =>
                        void op(() => api.post(`/procurement-orders/${po.id}/claim`), '已领单')
                      }
                    >
                      领单
                    </Button>
                  )}
                  {canExec && ['claimed', 'purchased', 'shipped'].includes(po.status ?? '') && (
                    <Button size="small" type="primary" onClick={() => setBackfillOpen(true)}>
                      回填
                    </Button>
                  )}
                  {canExec &&
                    !['backfilled', 'exception', 'cancelled'].includes(po.status ?? '') && (
                      <Button size="small" danger onClick={() => setExceptionOpen(true)}>
                        标异常
                      </Button>
                    )}
                  {/* 插件单两个互斥出口（Owner 2026-07-31 异常#16 裁定）：
                      先查亚马逊买家号——有购买记录→补录单号；确认没有→释放回池 */}
                  {canExec &&
                    po.buyer_account_id != null &&
                    !po.purchase_order_ref &&
                    ['assigned', 'exception'].includes(po.status ?? '') && (
                      <Button size="small" onClick={() => setPluginBackfillOpen(true)}>
                        补录插件购买
                      </Button>
                    )}
                  {has('order.assign') &&
                    po.buyer_account_id != null &&
                    !po.purchase_order_ref &&
                    po.status === 'exception' && (
                      <Button
                        size="small"
                        danger
                        onClick={() => {
                          Modal.confirm({
                            title: '释放回待派池？',
                            content:
                              '释放前请先在亚马逊买家号里核实：若已有这条购买记录，' +
                              '请走「补录插件购买」填单号，不要释放——释放后该单会被' +
                              '重新派发，等于再买一次。确认亚马逊侧没有购买记录才释放。',
                            okText: '确认没有购买记录，释放',
                            cancelText: '先去查',
                            onOk: () =>
                              op(
                                () => api.post(`/procurement-orders/${po.id}/release`),
                                '已释放回待派池',
                              ),
                          })
                        }}
                      >
                        释放回池
                      </Button>
                    )}
                </Space>
              </>
            ) : (
              <span style={{ color: '#888' }}>暂无执行单</span>
            )}
          </Card>
        </>
      )}

      {shipOpen && detail && (
        <ShipModal
          order={detail}
          onClose={() => setShipOpen(false)}
          onShipped={() => {
            setShipOpen(false)
            void load()
            onChanged()
          }}
        />
      )}
      {pluginBackfillOpen && po && (
        <PluginBackfillModal
          poId={po.id ?? 0}
          onClose={() => setPluginBackfillOpen(false)}
          onDone={() => {
            setPluginBackfillOpen(false)
            void load()
            onChanged()
          }}
        />
      )}
      {backfillOpen && po && (
        <BackfillModal
          poId={po.id ?? 0}
          onClose={() => setBackfillOpen(false)}
          onDone={() => {
            setBackfillOpen(false)
            void load()
            onChanged()
          }}
        />
      )}
      {exceptionOpen && po && (
        <ExceptionModal
          poId={po.id ?? 0}
          onClose={() => setExceptionOpen(false)}
          onDone={() => {
            setExceptionOpen(false)
            void load()
            onChanged()
          }}
        />
      )}
    </Drawer>
  )
}

function ShipModal({
  order,
  onClose,
  onShipped,
}: {
  order: OrderDetail
  onClose: () => void
  onShipped: () => void
}) {
  const lines = order.lines ?? []
  const shippable = (l: OrderLine) =>
    !['shipped', 'cancelled', 'refunded'].includes(l.line_status ?? '')
  const [selected, setSelected] = useState<string[]>(
    lines.filter(shippable).map((l) => l.channel_line_no ?? ''),
  )
  const [qtys, setQtys] = useState<Record<string, number>>(
    Object.fromEntries(lines.map((l) => [l.channel_line_no ?? '', l.qty ?? 1])),
  )
  const [submitting, setSubmitting] = useState(false)

  async function onFinish(values: { carrier: string; tracking_no: string }) {
    const body = {
      lines: selected.map((no) => ({ line_no: no, qty: qtys[no] ?? 1 })),
      carrier: values.carrier,
      tracking_no: values.tracking_no,
      ...(order.procurement?.id != null ? { procurement_order_id: order.procurement.id } : {}),
    }
    if (!body.lines.length) {
      message.warning('请至少选择一行')
      return
    }
    setSubmitting(true)
    try {
      await api.post(`/orders/${order.id}/ship`, body)
      message.success('发货已受理，异步回传渠道中')
      onShipped()
    } catch (e) {
      if (e instanceof ApiError && e.code === 'ERP_SHIP_REJECTED') {
        message.error('渠道拒绝，请核对物流信息后重试')
      } else {
        message.error(e instanceof ApiError ? e.message : '发货失败')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      title={`发货：${order.channel_order_no ?? ''}`}
      open
      onCancel={onClose}
      footer={null}
      width={640}
      destroyOnHidden
    >
      <Table<OrderLine>
        rowKey={(l) => l.channel_line_no ?? ''}
        size="small"
        pagination={false}
        dataSource={lines}
        style={{ marginBottom: 16 }}
        rowSelection={{
          selectedRowKeys: selected,
          onChange: (keys) => setSelected(keys as string[]),
          getCheckboxProps: (l) => ({ disabled: !shippable(l) }),
        }}
        columns={[
          { title: '行号', dataIndex: 'channel_line_no', width: 70 },
          { title: 'SKU', dataIndex: 'channel_sku' },
          { title: '行状态', dataIndex: 'line_status', width: 100 },
          {
            title: '发货数量',
            key: 'ship_qty',
            width: 110,
            render: (_, l) => (
              <InputNumber
                size="small"
                min={1}
                max={l.qty}
                value={qtys[l.channel_line_no ?? '']}
                disabled={!shippable(l)}
                onChange={(v) =>
                  setQtys((prev) => ({ ...prev, [l.channel_line_no ?? '']: v ?? 1 }))
                }
              />
            ),
          },
        ]}
      />
      <Form layout="vertical" onFinish={onFinish} initialValues={{ carrier: 'USPS' }}>
        <Form.Item label="承运商" name="carrier" rules={[{ required: true }]}>
          <Select options={CARRIERS.map((c) => ({ value: c, label: c }))} />
        </Form.Item>
        <Form.Item
          label="运单号"
          name="tracking_no"
          rules={[{ required: true, message: '请输入运单号' }]}
        >
          <Input placeholder="物流跟踪号" />
        </Form.Item>
        <Button type="primary" htmlType="submit" block loading={submitting}>
          提交发货（异步回传渠道）
        </Button>
      </Form>
    </Modal>
  )
}

function BackfillModal({
  poId,
  onClose,
  onDone,
}: {
  poId: number
  onClose: () => void
  onDone: () => void
}) {
  async function onFinish(values: Record<string, unknown>) {
    try {
      await api.post(`/procurement-orders/${poId}/backfill`, values)
      message.success('已回填')
      onDone()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '回填失败')
    }
  }

  return (
    <Modal title="回填采购与物流" open onCancel={onClose} footer={null} destroyOnHidden>
      <Form layout="vertical" onFinish={onFinish}>
        <Form.Item label="采购平台" name="purchase_platform">
          <Input placeholder="如 1688 / 拼多多" />
        </Form.Item>
        <Form.Item label="平台单号" name="purchase_order_ref">
          <Input />
        </Form.Item>
        <Form.Item label="采购成本" name="purchase_cost">
          <InputNumber min={0} style={{ width: '100%' }} addonAfter="CNY" />
        </Form.Item>
        <Form.Item label="运费" name="freight_cost">
          <InputNumber min={0} style={{ width: '100%' }} addonAfter="CNY" />
        </Form.Item>
        <Form.Item label="承运商" name="carrier">
          <Select allowClear options={CARRIERS.map((c) => ({ value: c, label: c }))} />
        </Form.Item>
        <Form.Item label="运单号" name="tracking_no">
          <Input />
        </Form.Item>
        {/* R2-13 0045 四个新列：插件路径由插件回报，人工路径也得能补，
            否则同一张表的一半字段只有机器人填得了，运营接管时无从下手 */}
        <Form.Item label="销售税" name="tax_amount">
          <InputNumber min={0} style={{ width: '100%' }} />
        </Form.Item>
        <Form.Item label="付款卡尾号" name="payment_card_last4">
          <Input maxLength={8} placeholder="如 4242" />
        </Form.Item>
        <Form.Item label="预计送达日期" name="delivery_est_date">
          <Input placeholder="YYYY-MM-DD" />
        </Form.Item>
        <Form.Item label="预计送达原文" name="delivery_est_raw" extra="渠道页面上的原话，照抄即可">
          <Input placeholder="如 Thursday, August 7" />
        </Form.Item>
        <Form.Item label="备注" name="note">
          <Input.TextArea rows={2} />
        </Form.Item>
        <Button type="primary" htmlType="submit" block>
          提交回填
        </Button>
      </Form>
    </Modal>
  )
}

function PluginBackfillModal({
  poId,
  onClose,
  onDone,
}: {
  poId: number
  onClose: () => void
  onDone: () => void
}) {
  async function onFinish(values: Record<string, unknown>) {
    try {
      await api.post(`/procurement-orders/${poId}/plugin-backfill`, values)
      message.success('已补录，单据转入 purchased（物流将随插件同步链继续）')
      onDone()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '补录失败')
    }
  }

  return (
    <Modal title="补录插件购买记录" open onCancel={onClose} footer={null} destroyOnHidden>
      {/* Owner 2026-07-31 异常#16 裁定：先查亚马逊买家号确有这条购买记录，再手填单号。
          与人工「回填」不同：这是替插件抄单（落 purchased），不是人工采购（落 backfilled） */}
      <p style={{ color: '#888' }}>
        仅用于插件已在亚马逊拍成但回传失败的单：请先在对应<b>亚马逊买家号</b>
        的订单列表核实确有这条购买记录，再把单号抄进来。若亚马逊侧
        <b>没有</b>购买记录，请关闭本窗改用「释放回池」。
      </p>
      <Form layout="vertical" onFinish={onFinish}>
        <Form.Item
          label="亚马逊订单号"
          name="purchase_order_ref"
          rules={[{ required: true, message: '必填：亚马逊订单号' }]}
        >
          <Input placeholder="如 111-5958998-9658617" />
        </Form.Item>
        <Form.Item label="实付金额（税前）" name="purchase_cost">
          <InputNumber min={0} style={{ width: '100%' }} addonAfter="USD" />
        </Form.Item>
        <Form.Item label="销售税" name="tax_amount">
          <InputNumber min={0} style={{ width: '100%' }} addonAfter="USD" />
        </Form.Item>
        <Form.Item label="运费" name="freight_cost">
          <InputNumber min={0} style={{ width: '100%' }} addonAfter="USD" />
        </Form.Item>
        <Form.Item label="付款卡尾号" name="payment_card_last4">
          <Input maxLength={4} placeholder="如 4242" />
        </Form.Item>
        <Button type="primary" htmlType="submit" block>
          补录
        </Button>
      </Form>
    </Modal>
  )
}

function ExceptionModal({
  poId,
  onClose,
  onDone,
}: {
  poId: number
  onClose: () => void
  onDone: () => void
}) {
  async function onFinish(values: { reason: string; kind?: string }) {
    try {
      await api.post(`/procurement-orders/${poId}/exception`, values)
      message.success('已标记异常')
      onDone()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '操作失败')
    }
  }

  return (
    <Modal title="标记执行异常" open onCancel={onClose} footer={null} destroyOnHidden>
      <Form layout="vertical" onFinish={onFinish}>
        <Form.Item
          label="异常原因"
          name="reason"
          rules={[{ required: true, message: '请填写异常原因' }]}
        >
          <Input.TextArea rows={3} placeholder="如：缺货 / 采购价超限 / 地址无法配送" />
        </Form.Item>
        {/* 分类可留空：不传 = 保持已有分类（后端 coalesce 语义），不会把插件已经归好的类抹掉 */}
        <Form.Item label="问题分类（可选）" name="kind" extra="留空则沿用已有分类，不会抹掉">
          <Select
            allowClear
            options={Object.entries(EXCEPTION_KIND).map(([value, label]) => ({ value, label }))}
          />
        </Form.Item>
        <Button type="primary" danger htmlType="submit" block>
          确认标异常
        </Button>
      </Form>
    </Modal>
  )
}
