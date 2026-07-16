import { MinusCircleOutlined, PlusOutlined } from '@ant-design/icons'
import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  InputNumber,
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

// R2-06 增量4：定价端点已冻结于 specs/002-api-contract/openapi-v0.yaml，
// 但 schema.d.ts 尚未重新 codegen（待 pnpm gen:api）——按本页面既有惯例
// 先用页面内局部类型 + api 客户端宽松调用，生成后可切换 Schemas['PricingStrategy']。

type BandTuple = (number | null)[]

interface StrategyParams {
  min_price?: number | null
  bands?: { FBA?: BandTuple[]; FBM?: BandTuple[] }
}

interface PricingStrategy {
  id: number
  store_id: number | null
  offer_mode: string
  name: string
  algo_code: string
  params: StrategyParams
  status: string
  version: number
}

interface PreviewItem {
  product_id: number
  listing_id: number | null
  old_price: number | null
  new_price: number | null
  ok: boolean
  reason: string | null
  detail?: Record<string, unknown>
}

interface PreviewResult {
  strategy?: { id?: number; name?: string; algo_code?: string; version?: number }
  items?: PreviewItem[]
}

interface RepriceResult {
  pushed: number
  skipped?: { listing_id: number; reason: string }[]
  failed?: { listing_id: number; code: string | null }[]
}

interface StoreOpt {
  id: number
  code: string
  name: string
}

// 表单内区间行（提交时转回契约的 [low, high, multiplier] 元组）
interface BandRow {
  low?: number
  high?: number
  mul?: number
}

interface StrategyFormValues {
  offer_mode: string
  store_id?: number | null
  name: string
  algo_code: string
  min_price?: number | null
  bands_fba?: BandRow[]
  bands_fbm?: BandRow[]
}

const MODE_COLOR: Record<string, string> = { build: 'blue', match: 'purple' }

const MODE_OPTIONS = [
  { value: 'build', label: 'build 自建' },
  { value: 'match', label: 'match 跟卖' },
]

// D-Q62 建档默认区间模板（Owner 2026-07-16 定值）：倍数留空、由行内必填校验兜底
const DEFAULT_FBA: BandRow[] = [
  { low: 0, high: 20 },
  { low: 20, high: 80 },
]
const DEFAULT_FBM: BandRow[] = [
  { low: 20, high: 80 },
  { low: 80, high: 1000 },
]

function parseIds(raw: string): number[] {
  return Array.from(
    new Set(
      raw
        .split(/[,，\s]+/)
        .map((s) => Number(s.trim()))
        .filter((n) => Number.isInteger(n) && n > 0),
    ),
  )
}

function storeLabel(stores: StoreOpt[], id: number): string {
  const s = stores.find((x) => x.id === id)
  return s ? `${s.code} ${s.name}` : `#${id}`
}

function bandRows(group?: BandTuple[]): BandRow[] {
  return (group ?? []).map((b) => ({
    low: b[0] ?? undefined,
    high: b[1] ?? undefined,
    mul: b[2] ?? undefined,
  }))
}

export default function PricingPage() {
  const { has } = useAuth()
  const canWrite = has('pricing.write')
  const [items, setItems] = useState<PricingStrategy[]>([])
  const [stores, setStores] = useState<StoreOpt[]>([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<PricingStrategy | null>(null)
  const [previewOpen, setPreviewOpen] = useState(false)
  const [repriceOpen, setRepriceOpen] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setItems(await api.get<PricingStrategy[]>('/pricing-strategies'))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

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

  async function toggleStatus(s: PricingStrategy) {
    try {
      await api.patch(`/pricing-strategies/${s.id}`, {
        status: s.status === 'active' ? 'disabled' : 'active',
      })
      message.success(s.status === 'active' ? '策略已停用' : '策略已启用')
      void load()
    } catch (e) {
      // 409 = (team×store×offer_mode) 活跃唯一冲突（D-Q23），直接展示后端 message
      message.error(e instanceof ApiError ? e.message : '操作失败', 6)
    }
  }

  return (
    <>
      <Space style={{ marginBottom: 16 }} wrap>
        {canWrite && (
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            建策略
          </Button>
        )}
        <Button onClick={() => setPreviewOpen(true)}>试算</Button>
        {canWrite && <Button onClick={() => setRepriceOpen(true)}>批量重定价</Button>}
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      <Table<PricingStrategy>
        rowKey="id"
        loading={loading}
        dataSource={items}
        pagination={false}
        columns={[
          {
            title: '模式',
            dataIndex: 'offer_mode',
            width: 90,
            render: (m: string) => <Tag color={MODE_COLOR[m]}>{m}</Tag>,
          },
          {
            title: '店铺',
            dataIndex: 'store_id',
            width: 170,
            render: (v: number | null) => (v == null ? <Tag>团队默认</Tag> : storeLabel(stores, v)),
          },
          { title: '名称', dataIndex: 'name' },
          {
            title: '算法',
            dataIndex: 'algo_code',
            width: 110,
            render: (a: string) => <Tag color={a === 'cost_plus' ? 'geekblue' : 'default'}>{a}</Tag>,
          },
          {
            title: 'min_price',
            dataIndex: ['params', 'min_price'],
            width: 110,
            render: (v?: number | null) =>
              v != null ? `$${v}` : <span style={{ color: '#999' }}>不设防</span>,
          },
          { title: '版本', dataIndex: 'version', width: 70, render: (v: number) => `v${v}` },
          {
            title: '状态',
            dataIndex: 'status',
            width: 90,
            render: (s: string) =>
              s === 'active' ? <Tag color="green">启用</Tag> : <Tag>停用</Tag>,
          },
          {
            title: '操作',
            key: 'op',
            width: 150,
            render: (_, s) =>
              canWrite ? (
                <Space>
                  <Button size="small" onClick={() => setEditing(s)}>
                    编辑
                  </Button>
                  <Button
                    size="small"
                    danger={s.status === 'active'}
                    onClick={() => void toggleStatus(s)}
                  >
                    {s.status === 'active' ? '停用' : '启用'}
                  </Button>
                </Space>
              ) : null,
          },
        ]}
      />

      {(createOpen || editing) && (
        <StrategyModal
          editing={editing}
          stores={stores}
          onClose={() => {
            setCreateOpen(false)
            setEditing(null)
          }}
          onDone={() => {
            setCreateOpen(false)
            setEditing(null)
            void load()
          }}
        />
      )}
      {previewOpen && <PreviewModal stores={stores} onClose={() => setPreviewOpen(false)} />}
      {repriceOpen && <BulkRepriceModal onClose={() => setRepriceOpen(false)} />}
    </>
  )
}

// FBA/FBM 区间组编辑器：若干行 [下限, 上限] × 倍数 的动态行（简单可用版）
function BandsGroup({ name, label }: { name: string; label: string }) {
  return (
    <Form.Item label={label} required style={{ marginBottom: 8 }}>
      <Form.List
        name={name}
        rules={[
          {
            validator: async (_rule: unknown, rows?: BandRow[]) => {
              if (!rows || rows.length === 0) throw new Error('至少需要一条区间')
            },
          },
        ]}
      >
        {(fields, { add, remove }, { errors }) => (
          <>
            {fields.map((field) => (
              <Space key={field.key} align="baseline" style={{ display: 'flex', marginBottom: 4 }}>
                <Form.Item
                  name={[field.name, 'low']}
                  rules={[{ required: true, message: '下限必填' }]}
                  style={{ marginBottom: 0 }}
                >
                  <InputNumber min={0} placeholder="下限 $" style={{ width: 100 }} />
                </Form.Item>
                <span>—</span>
                <Form.Item
                  name={[field.name, 'high']}
                  rules={[{ required: true, message: '上限必填' }]}
                  style={{ marginBottom: 0 }}
                >
                  <InputNumber min={0} placeholder="上限 $" style={{ width: 100 }} />
                </Form.Item>
                <span>×</span>
                <Form.Item
                  name={[field.name, 'mul']}
                  rules={[{ required: true, message: '倍数必填' }]}
                  style={{ marginBottom: 0 }}
                >
                  <InputNumber min={0.01} step={0.01} placeholder="倍数（必填）" style={{ width: 120 }} />
                </Form.Item>
                <MinusCircleOutlined onClick={() => remove(field.name)} />
              </Space>
            ))}
            <Form.ErrorList errors={errors} />
            <Button type="dashed" size="small" icon={<PlusOutlined />} onClick={() => add({})}>
              加区间
            </Button>
          </>
        )}
      </Form.List>
    </Form.Item>
  )
}

// 建策略 / 行编辑（编辑仅改 name/algo/params——模式与店铺建后不改，避免撞活跃唯一语义）
function StrategyModal({
  editing,
  stores,
  onClose,
  onDone,
}: {
  editing: PricingStrategy | null
  stores: StoreOpt[]
  onClose: () => void
  onDone: () => void
}) {
  const initial: StrategyFormValues = editing
    ? {
        offer_mode: editing.offer_mode,
        store_id: editing.store_id,
        name: editing.name,
        algo_code: editing.algo_code,
        min_price: editing.params?.min_price ?? undefined,
        bands_fba: bandRows(editing.params?.bands?.FBA),
        bands_fbm: bandRows(editing.params?.bands?.FBM),
      }
    : {
        offer_mode: 'build',
        name: '',
        algo_code: 'cost_plus',
        bands_fba: DEFAULT_FBA,
        bands_fbm: DEFAULT_FBM,
      }

  async function onFinish(values: StrategyFormValues) {
    const params: StrategyParams = {}
    // min_price 可选（D-Q62 补充裁定）：留空不下发 = 不设防；填了后端校验须 > 0
    if (values.min_price != null) params.min_price = values.min_price
    if (values.algo_code === 'cost_plus') {
      params.bands = {
        FBA: (values.bands_fba ?? []).map((r) => [r.low ?? null, r.high ?? null, r.mul ?? null]),
        FBM: (values.bands_fbm ?? []).map((r) => [r.low ?? null, r.high ?? null, r.mul ?? null]),
      }
    }
    try {
      if (editing) {
        // PATCH 同体局部更新；params 每改后端 version+1
        await api.patch(`/pricing-strategies/${editing.id}`, {
          name: values.name,
          algo_code: values.algo_code,
          params,
        })
        message.success('策略已更新')
      } else {
        await api.post('/pricing-strategies', {
          store_id: values.store_id ?? null,
          offer_mode: values.offer_mode,
          name: values.name,
          algo_code: values.algo_code,
          params,
        })
        message.success('策略已创建')
      }
      onDone()
    } catch (e) {
      // 409 = (team×store×offer_mode) 活跃唯一冲突（D-Q23），直接展示后端 message
      message.error(e instanceof ApiError ? e.message : '保存失败', 6)
    }
  }

  return (
    <Modal
      title={editing ? `编辑策略：${editing.name}（v${editing.version}）` : '建策略'}
      open
      onCancel={onClose}
      footer={null}
      width={640}
    >
      {editing && (
        <div style={{ color: '#999', marginBottom: 12 }}>
          模式 {editing.offer_mode} · 店铺{' '}
          {editing.store_id == null ? '团队默认' : storeLabel(stores, editing.store_id)}
        </div>
      )}
      <Form layout="vertical" onFinish={onFinish} initialValues={initial}>
        {!editing && (
          <>
            <Form.Item label="模式 offer_mode" name="offer_mode" rules={[{ required: true }]}>
              <Select options={MODE_OPTIONS} />
            </Form.Item>
            <Form.Item label="店铺（留空 = 团队默认策略）" name="store_id">
              <Select
                allowClear
                placeholder="团队默认"
                options={stores.map((s) => ({ value: s.id, label: `${s.code} ${s.name}` }))}
              />
            </Form.Item>
          </>
        )}
        <Form.Item label="名称" name="name" rules={[{ required: true, message: '请输入策略名称' }]}>
          <Input placeholder="策略名称" />
        </Form.Item>
        <Form.Item label="算法" name="algo_code" rules={[{ required: true }]}>
          <Select
            options={[
              { value: 'cost_plus', label: 'cost_plus 成本加成' },
              { value: 'manual', label: 'manual 人工价' },
            ]}
          />
        </Form.Item>
        <Form.Item
          label="min_price 底线（可选）"
          name="min_price"
          extra="留空 = 不设防（D-Q62：填了须 > 0，算出价低于底线不出价）"
        >
          <InputNumber min={0.01} precision={2} style={{ width: '100%' }} placeholder="留空不设防" />
        </Form.Item>
        <Form.Item noStyle shouldUpdate={(p, c) => p.algo_code !== c.algo_code}>
          {({ getFieldValue }) =>
            getFieldValue('algo_code') === 'cost_plus' ? (
              <>
                <BandsGroup name="bands_fba" label="FBA 区间（[下限, 上限] × 倍数）" />
                <BandsGroup name="bands_fbm" label="FBM 区间（[下限, 上限] × 倍数）" />
              </>
            ) : null
          }
        </Form.Item>
        <Button type="primary" htmlType="submit" block>
          {editing ? '保存' : '创建'}
        </Button>
      </Form>
    </Modal>
  )
}

// 试算：POST /pricing-strategies/preview 只读不落库；listing_ids 简化为逗号分隔输入
function PreviewModal({ stores, onClose }: { stores: StoreOpt[]; onClose: () => void }) {
  const [result, setResult] = useState<PreviewResult | null>(null)
  const [running, setRunning] = useState(false)

  async function onFinish(values: { store_id: number; offer_mode: string; listing_ids: string }) {
    const ids = parseIds(values.listing_ids)
    if (!ids.length) {
      message.warning('请输入至少一个合法的 listing_id（逗号分隔）')
      return
    }
    setRunning(true)
    try {
      setResult(
        await api.post<PreviewResult>('/pricing-strategies/preview', {
          store_id: values.store_id,
          offer_mode: values.offer_mode,
          listing_ids: ids,
        }),
      )
    } catch (e) {
      // 422 = 无活跃策略（PRICING_STRATEGY_NOT_FOUND）等，直接展示后端 message
      message.error(e instanceof ApiError ? e.message : '试算失败', 6)
    } finally {
      setRunning(false)
    }
  }

  return (
    <Modal title="重定价试算（只读，不落库）" open onCancel={onClose} footer={null} width={780}>
      <Form layout="inline" onFinish={onFinish} initialValues={{ offer_mode: 'build' }} style={{ rowGap: 8 }}>
        <Form.Item label="店铺" name="store_id" rules={[{ required: true, message: '请选店铺' }]}>
          <Select
            style={{ width: 180 }}
            placeholder="选择店铺"
            options={stores.map((s) => ({ value: s.id, label: `${s.code} ${s.name}` }))}
          />
        </Form.Item>
        <Form.Item label="模式" name="offer_mode" rules={[{ required: true }]}>
          <Select style={{ width: 130 }} options={MODE_OPTIONS} />
        </Form.Item>
        <Form.Item
          label="listing_ids"
          name="listing_ids"
          rules={[{ required: true, message: '请输入 listing_ids' }]}
        >
          <Input style={{ width: 220 }} placeholder="逗号分隔，如 1,2,3（≤200）" />
        </Form.Item>
        <Form.Item>
          <Button type="primary" htmlType="submit" loading={running}>
            试算
          </Button>
        </Form.Item>
      </Form>
      {result && (
        <>
          <Alert
            style={{ marginTop: 16, marginBottom: 8 }}
            type="info"
            message={`策略 #${result.strategy?.id ?? '—'} ${result.strategy?.name ?? ''} · ${
              result.strategy?.algo_code ?? ''
            } · v${result.strategy?.version ?? '—'}`}
          />
          <Table<PreviewItem>
            rowKey={(r) => `${r.product_id}-${r.listing_id ?? 'x'}`}
            size="small"
            dataSource={result.items}
            pagination={false}
            columns={[
              {
                title: 'Listing',
                dataIndex: 'listing_id',
                width: 90,
                render: (v: number | null) => v ?? '—',
              },
              { title: '产品', dataIndex: 'product_id', width: 90 },
              {
                title: '旧价',
                dataIndex: 'old_price',
                width: 90,
                render: (v: number | null) => v ?? '—',
              },
              {
                title: '新价',
                dataIndex: 'new_price',
                width: 160,
                render: (v: number | null, r) =>
                  v == null ? (
                    '—'
                  ) : r.detail?.['out_of_band'] === true ? (
                    // clamp 版参考价：区间外红字标记
                    <span style={{ color: '#cf1322' }}>{v}（区间外 clamp）</span>
                  ) : (
                    v
                  ),
              },
              {
                title: '严格判定',
                dataIndex: 'ok',
                width: 100,
                render: (ok: boolean) =>
                  ok ? <Tag color="green">ok</Tag> : <Tag color="red">拒绝</Tag>,
              },
              { title: '原因', dataIndex: 'reason', render: (v: string | null) => v ?? '—' },
            ]}
          />
        </>
      )}
    </Modal>
  )
}

// 批量重定价：确认 → POST /pricing/reprice（Idempotency-Key 由 client.ts api.post 统一注入）
function BulkRepriceModal({ onClose }: { onClose: () => void }) {
  const [result, setResult] = useState<RepriceResult | null>(null)

  function onFinish(values: { listing_ids: string; force?: boolean }) {
    const ids = parseIds(values.listing_ids)
    if (!ids.length) {
      message.warning('请输入至少一个合法的 listing_id（逗号分隔）')
      return
    }
    Modal.confirm({
      title: `确认对 ${ids.length} 条 listing 批量重定价？`,
      content: values.force
        ? '已勾选 force：超确认阈值（默认 30%）的变动将强制放行推送（BR-PR-008）。'
        : '价格变动超 30%（默认阈值）的条目会被拦下（failed=PRICING_CONFIRM_REQUIRED），核对后可勾选 force 重发。',
      okText: '执行',
      cancelText: '取消',
      onOk: async () => {
        try {
          setResult(
            await api.post<RepriceResult>('/pricing/reprice', {
              listing_ids: ids,
              force: !!values.force,
            }),
          )
        } catch (e) {
          // 409 = 幂等键冲突（同键异载荷/处理中）等，直接展示后端 message
          message.error(e instanceof ApiError ? e.message : '重定价失败', 6)
        }
      },
    })
  }

  return (
    <Modal
      title="批量重定价（live 按策略算价推渠道）"
      open
      onCancel={onClose}
      footer={null}
      width={640}
    >
      <Form layout="vertical" onFinish={onFinish}>
        <Form.Item
          label="listing_ids"
          name="listing_ids"
          rules={[{ required: true, message: '请输入 listing_ids' }]}
        >
          <Input.TextArea rows={3} placeholder="逗号分隔，如 1,2,3（单次 ≤200）" />
        </Form.Item>
        <Form.Item name="force" valuePropName="checked">
          <Checkbox>force 强制放行（超 30% 变动确认阈值时仍推送，BR-PR-008）</Checkbox>
        </Form.Item>
        <Button type="primary" htmlType="submit" block>
          批量重定价
        </Button>
      </Form>
      {result && (
        <Alert
          style={{ marginTop: 16 }}
          type={(result.failed?.length ?? 0) > 0 ? 'warning' : 'success'}
          message={`已推送 ${result.pushed} · 跳过 ${result.skipped?.length ?? 0} · 失败 ${
            result.failed?.length ?? 0
          }`}
          description={
            <>
              {!!result.skipped?.length && (
                <div>
                  跳过：{result.skipped.map((s) => `#${s.listing_id}(${s.reason})`).join('、')}
                </div>
              )}
              {!!result.failed?.length && (
                <div>
                  失败：{result.failed.map((f) => `#${f.listing_id}(${f.code ?? '未知'})`).join('、')}
                  <div style={{ color: '#999' }}>
                    PRICING_CONFIRM_REQUIRED = 变动超 30% 被拦，核对后勾选 force 重发
                  </div>
                </div>
              )}
            </>
          }
        />
      )}
    </Modal>
  )
}
