import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'

import {
  ApiError,
  api,
  type AuditResult,
  type PageOf,
  type ProductDeleteResult,
} from '@/api/client'
import { useAuth } from '@/auth/AuthContext'
import AuditRunDrawer from '@/pages/products/AuditRunDrawer'
import BatchAuditModal from '@/pages/products/BatchAuditModal'
import ProductDetailDrawer from '@/pages/products/ProductDetailDrawer'
import { STATUS_COLOR, STATUS_LABEL, isAuditEligible, statusText } from '@/pages/products/labels'
import type { AuditRunDetail, Product, ProductDetail, StoreOpt } from '@/pages/products/types'

/** 可批量分配上架的状态（与行内「分配上架」按钮同集合） */
const ALLOCATE_ELIGIBLE = ['audit_passed', 'ready']

export default function ProductsPage() {
  const { has } = useAuth()
  const canAudit = has('audit.run')
  const canAllocate = has('listing.allocate')
  const canDelete = has('catalog.product_delete')
  const [data, setData] = useState<PageOf<Product> | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState<string | undefined>()
  // 00-conventions §7.1 的「显示已停用」开关。产品域的「已停用/已归档」= `retired`
  // （本页文案「已下架」）。默认关 = 默认折叠。
  const [includeRetired, setIncludeRetired] = useState(false)
  const [auditDetail, setAuditDetail] = useState<AuditRunDetail | null>(null)
  const [allocate, setAllocate] = useState<{ ids: number[]; label: string } | null>(null)
  const [batch, setBatch] = useState<{ ids: number[]; excluded: number } | null>(null)
  const [selected, setSelected] = useState<number[]>([])
  const [stores, setStores] = useState<StoreOpt[]>([])
  const [detail, setDetail] = useState<ProductDetail | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [toDelete, setToDelete] = useState<Product | null>(null)
  const [deleting, setDeleting] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      // 后端语义：显式 status 时 include_retired 不生效（按状态精确筛选优先）。
      // 故这里也只在无 status 时才发该参数——发一个后端会忽略的参数，等于在请求里
      // 留一句假话，将来查日志的人会据此推断出错误的行为。
      const qs = status ? `&status=${status}` : includeRetired ? '&include_retired=true' : ''
      setData(await api.get<PageOf<Product>>(`/products?page=${page}&size=20${qs}`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page, status, includeRetired])

  useEffect(() => {
    void load()
  }, [load])

  // 切页 / 切筛选清空勾选。**不是洁癖**：勾选只存 id，跨页攒下来的 id 在当前页拿不到
  // 状态行，「有效数」和确认文案就会按不完整的信息算，用户会对着一个算错的数字掏钱。
  useEffect(() => {
    setSelected([])
  }, [page, status, includeRetired])

  // 勾选框现在对两种批量动作放行（见下方 rowSelection），所以**两个批量按钮都必须
  // 各自算自己的有效数**：否则拿了「送审合格但不可分配」的行去点分配，按钮上的数字
  // 与实际下发件数对不上，后端逐条拒绝后用户只会以为系统丢件。
  const rows = data?.items ?? []
  const selectedRows = rows.filter((p) => selected.includes(p.id))
  const auditableIds = selectedRows.filter((p) => isAuditEligible(p.status)).map((p) => p.id)
  const allocatableIds = selectedRows
    .filter((p) => ALLOCATE_ELIGIBLE.includes(p.status))
    .map((p) => p.id)

  async function onAudit(p: Product) {
    try {
      // 类型取自 codegen（008§5.4）：单品送审 201 的 schema 本单已补进 002 契约，
      // 手写的 `{verdict, run_id}` 就地清偿——契约将来加/改字段时这里会编译报错，
      // 而不是静默漂移。
      const r = await api.post<AuditResult>(`/products/${p.id}/audit`, {
        trigger_kind: p.status === 'ingested' ? 'manual' : 're_audit',
      })
      const verdictText =
        r.verdict === 'pass' ? '✅ 通过' : r.verdict === 'reject' ? '❌ 拒绝' : '🟠 待人工复核'
      message.success(`审核完成：${verdictText}`)
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

  async function onDelete(values: { reason: string }) {
    if (!toDelete) return
    setDeleting(true)
    try {
      // 类型取自 codegen（008§5.4）：删除回执的 schema 随本单进契约。
      const r = await api.del<ProductDeleteResult>(
        `/products/${toDelete.id}?reason=${encodeURIComponent(values.reason)}`,
      )
      // 回执如实播报：①级与②级的后果不同，②级还带着「重采不会再入库」这层含义，
      // 一句笼统的「删除成功」会让运营以为随时能采回来。
      message.success(
        r.tombstoned
          ? `已删除 ${r.master_sku}（清理 ${r.listings_deleted} 条上架记录；已留墓碑，重新采集不会再入库）`
          : `已删除 ${r.master_sku}（从未上架，未留墓碑——重新采集仍会入库）`,
      )
      setToDelete(null)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '删除失败')
    } finally {
      setDeleting(false)
    }
  }

  async function openAllocate(ids: number[], label: string) {
    setAllocate({ ids, label })
    try {
      const r = await api.get<PageOf<StoreOpt>>(`/stores?page=1&size=100`)
      setStores(r.items)
    } catch {
      /* 列表失败时下拉为空 */
    }
  }

  async function onAllocate(values: { store_id: number; offer_mode: string }) {
    if (!allocate) return
    try {
      const r = await api.post<{
        created: unknown[]
        rejected: { code: string; message: string }[]
      }>(`/listings/allocate`, { product_ids: allocate.ids, ...values })
      if (r.created.length) {
        message.success(`已分配 ${r.created.length} 个为上架草稿（前往上架管理提交）`)
      }
      if (r.rejected.length) {
        message.warning(
          `${r.rejected.length} 个被拒：${r.rejected[0]?.code} ${r.rejected[0]?.message ?? ''}`,
        )
      }
      setAllocate(null)
      setSelected([])
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '分配失败')
    }
  }

  return (
    <>
      <Space style={{ marginBottom: 16 }} wrap>
        <Select
          allowClear
          placeholder="按状态筛选"
          style={{ width: 160 }}
          value={status}
          onChange={(v) => {
            setPage(1)
            setStatus(v)
          }}
          options={Object.keys(STATUS_COLOR).map((s) => ({ value: s, label: STATUS_LABEL[s] ?? s }))}
        />
        {/* 开关在选了状态时**禁用而非静默失效**：后端此时忽略 include_retired，
            留一个点得动却不起作用的勾，正是本项目反复栽跟头的那类「看起来配着、
            实际没生效」。禁用 + 说明理由，用户至少知道该去清空状态筛选。 */}
        <Tooltip title={status ? '已按状态精确筛选，此时不再折叠——清空状态筛选后可用' : ''}>
          <Checkbox
            disabled={!!status}
            checked={includeRetired}
            onChange={(e) => {
              setPage(1)
              setIncludeRetired(e.target.checked)
            }}
          >
            显示已下架
          </Checkbox>
        </Tooltip>
        <Button onClick={() => void load()}>刷新</Button>
        {canAudit && (
          <Button
            type="primary"
            disabled={!auditableIds.length}
            onClick={() =>
              setBatch({ ids: auditableIds, excluded: selected.length - auditableIds.length })
            }
          >
            批量送审
            {selected.length ? `（${auditableIds.length}/已选 ${selected.length}）` : ''}
          </Button>
        )}
        {canAllocate && (
          <Button
            type="primary"
            disabled={!allocatableIds.length}
            onClick={() =>
              void openAllocate(allocatableIds, `${allocatableIds.length} 个产品（已选 ${selected.length}）`)
            }
          >
            批量分配上架
            {selected.length ? `（${allocatableIds.length}/已选 ${selected.length}）` : ''}
          </Button>
        )}
      </Space>
      <Table<Product>
        rowKey="id"
        loading={loading}
        dataSource={rows}
        rowSelection={
          // 复选框对**任一**批量动作有权限就要给：此前只认 listing.allocate，
          // 只有 audit.run 的审核员连勾选框都看不见，批量送审等于不存在。
          canAllocate || canAudit
            ? {
                selectedRowKeys: selected,
                onChange: (keys) => setSelected(keys as number[]),
                getCheckboxProps: (p) => ({
                  // 一件都不能对它做的行才禁用；能送审的与能分配的各按自己的合格态判。
                  disabled: !(
                    (canAllocate && ALLOCATE_ELIGIBLE.includes(p.status)) ||
                    (canAudit && isAuditEligible(p.status))
                  ),
                }),
              }
            : undefined
        }
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
            render: (s: string) => <Tag color={STATUS_COLOR[s]}>{statusText(s)}</Tag>,
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
                {canAudit && isAuditEligible(p.status) && (
                  <Button size="small" type="primary" onClick={() => void onAudit(p)}>
                    {p.status === 'ingested' ? '审核' : '重审'}
                  </Button>
                )}
                {has('audit.read') && p.latest_audit_run_id && (
                  <Button size="small" onClick={() => void showAudit(p.latest_audit_run_id!)}>
                    审核详情
                  </Button>
                )}
                {canAllocate && ALLOCATE_ELIGIBLE.includes(p.status) && (
                  <Button size="small" onClick={() => void openAllocate([p.id], p.master_sku)}>
                    分配上架
                  </Button>
                )}
                {canDelete && (
                  <Button size="small" danger onClick={() => setToDelete(p)}>
                    删除
                  </Button>
                )}
              </Space>
            ),
          },
        ]}
      />
      <AuditRunDrawer run={auditDetail} onClose={() => setAuditDetail(null)} />
      <ProductDetailDrawer
        detail={detail}
        loading={detailLoading}
        onClose={() => setDetail(null)}
      />
      {batch && (
        <BatchAuditModal
          productIds={batch.ids}
          excludedCount={batch.excluded}
          onBatchDone={() => {
            setSelected([])
            void load()
          }}
          onClose={() => setBatch(null)}
        />
      )}
      <Modal
        title={`分配上架：${allocate?.label ?? ''}`}
        open={!!allocate}
        onCancel={() => setAllocate(null)}
        footer={null}
        destroyOnHidden
      >
        <Form layout="vertical" onFinish={onAllocate} initialValues={{ offer_mode: 'build' }}>
          <Form.Item name="store_id" label="店铺" rules={[{ required: true }]}>
            <Select options={stores.map((s) => ({ value: s.id, label: `${s.code} ${s.name}` }))} />
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
      {/* 二次确认（§7.1「配套要求」）。**不可逆操作的确认必须说真话**：
          ①/②级要到服务端才判得出，所以提示同时覆盖两种后果，不含糊其辞。 */}
      <Modal
        title={`删除产品：${toDelete?.master_sku ?? ''}`}
        open={!!toDelete}
        onCancel={() => setToDelete(null)}
        footer={null}
        destroyOnHidden
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="删除不可撤销"
          description={
            <>
              产品行会被<b>物理删除</b>，标题/图片/属性一并消失，<b>没有回收站</b>。
              <br />
              若该产品<b>上过架</b>，还会留下墓碑——此后
              <b>重新采集同一个 ASIN 也不会再入库</b>；其已下架的上架记录会一并清理，
              占用的 GTIN 归还池中（已发到渠道的号码不回收）。
              <br />
              订单、审计、财务记录<b>不受影响</b>，一行都不会删。
              <br />
              在架或下架处理中的产品删不掉——请先完成下架。
            </>
          }
        />
        <Form layout="vertical" onFinish={onDelete}>
          <Form.Item
            name="reason"
            label="删除原因"
            rules={[{ required: true, message: '请填写删除原因（会写入墓碑与审计留痕）' }]}
            extra="会写入墓碑与 audit_log，用于日后追溯是谁、为什么删的。"
          >
            <Input.TextArea rows={2} maxLength={200} showCount />
          </Form.Item>
          <Button type="primary" danger htmlType="submit" block loading={deleting}>
            确认删除 {toDelete?.master_sku}
          </Button>
        </Form>
      </Modal>
    </>
  )
}
