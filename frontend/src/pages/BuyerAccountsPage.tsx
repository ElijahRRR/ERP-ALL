import {
  Alert,
  Button,
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

import { ApiError, api, type BuyerAccount, type PageOf } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'
import PluginInstanceDrawer from '@/pages/buyerAccounts/PluginInstanceDrawer'
import { ACCOUNT_STATUS, SITE_LABEL, lastSeen } from '@/pages/buyerAccounts/labels'

const SITE_OPTIONS = Object.entries(SITE_LABEL).map(([value, label]) => ({ value, label }))
const STATUS_OPTIONS = Object.entries(ACCOUNT_STATUS).map(([value, m]) => ({
  value,
  label: m.label,
}))

// customerId 的取值方式不是自明的（不是系统生成、也不是亚马逊页面上能直接看到的东西），
// 表单上不写这段话运营就开不了号——Owner 2026-07-30 补-2 明确这个值就是靠插件面板那个
// 按钮取出来人工录入的。
const CUSTOMER_ID_HELP =
  '在指纹浏览器里打开该买家账号的亚马逊页面 → 点插件面板「提取 customerId」→ 复制粘贴到此处（形如 A1NS3HIW4IC6P7）'

interface FormValues {
  label?: string
  site?: string
  external_customer_id?: string
  status?: string
  daily_cap?: number | null
  note?: string
}

function AccountForm({
  initial,
  submitText,
  onFinish,
}: {
  initial?: BuyerAccount
  submitText: string
  onFinish: (v: FormValues) => void | Promise<void>
}) {
  return (
    <Form<FormValues>
      layout="vertical"
      onFinish={onFinish}
      initialValues={{
        label: initial?.label,
        site: initial?.site ?? 'amazon_com',
        external_customer_id: initial?.external_customer_id,
        status: initial?.status ?? 'active',
        daily_cap: initial?.daily_cap ?? undefined,
        note: initial?.note ?? undefined,
      }}
    >
      <Form.Item
        label="账号名"
        name="label"
        extra="语义 = 对应哪一个指纹浏览器——运营靠它把系统里的号和桌面上开着的窗口对上"
        rules={[{ required: true, message: '请填写账号名' }]}
      >
        <Input placeholder="如：指纹浏览器 07 号窗口" maxLength={100} />
      </Form.Item>
      <Form.Item label="站点" name="site" rules={[{ required: true }]}>
        <Select options={SITE_OPTIONS} />
      </Form.Item>
      <Form.Item
        label="customerId"
        name="external_customer_id"
        extra={CUSTOMER_ID_HELP}
        rules={[{ required: true, message: '请填写 customerId' }]}
      >
        <Input placeholder="A1NS3HIW4IC6P7" maxLength={64} />
      </Form.Item>
      <Form.Item
        label="状态"
        name="status"
        extra="非「启用」一律不再派新任务；但已派出去的单，其回填/异常/物流回报照常收（钱可能已经花出去了）"
      >
        <Select options={STATUS_OPTIONS} />
      </Form.Item>
      <Form.Item
        label="单日上限"
        name="daily_cap"
        extra="按「当日已派发且未取消的单数」计，不按拍成的单数计；留空 = 不限。这一列是日限的唯一权威"
      >
        <InputNumber min={1} max={10000} style={{ width: '100%' }} placeholder="留空 = 不限" />
      </Form.Item>
      <Form.Item label="备注" name="note">
        <Input.TextArea rows={2} maxLength={1000} />
      </Form.Item>
      <Button type="primary" htmlType="submit" block>
        {submitText}
      </Button>
    </Form>
  )
}

export default function BuyerAccountsPage() {
  const { has } = useAuth()
  const canAdmin = has('procurement.buyer_account_admin')
  const [data, setData] = useState<PageOf<BuyerAccount> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [site, setSite] = useState<string | undefined>()
  const [status, setStatus] = useState<string | undefined>()
  const [q, setQ] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<BuyerAccount | null>(null)
  const [instancesOf, setInstancesOf] = useState<BuyerAccount | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const qs = [
        `page=${page}`,
        'size=20',
        site ? `site=${site}` : '',
        status ? `status=${status}` : '',
        q ? `q=${encodeURIComponent(q)}` : '',
      ]
        .filter(Boolean)
        .join('&')
      setData(await api.get<PageOf<BuyerAccount>>(`/buyer-accounts?${qs}`))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page, site, status, q])

  useEffect(() => {
    void load()
  }, [load])

  async function onCreate(values: FormValues) {
    try {
      // daily_cap 留空时显式送 null（= 不限），与「不传」区分开：后端按
      // model_fields_set 判断，不送就是「不动这一列」，建号时两者恰好等价，
      // 但和下面 onEdit 保持同一形状，读代码时不会以为两处语义不同。
      await api.post('/buyer-accounts', { ...values, daily_cap: values.daily_cap ?? null })
      message.success('买家账号已创建')
      setCreateOpen(false)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '创建失败')
    }
  }

  async function onEdit(values: FormValues) {
    if (!editing) return
    try {
      // 这里必须显式送 null：表单把 daily_cap 清空就是「改成不限」，
      // 而 undefined 会被 JSON.stringify 丢掉，后端会读成「没传 = 不动」——
      // 结果就是运营点了保存却发现日限还在。
      await api.patch(`/buyer-accounts/${editing.id}`, {
        ...values,
        daily_cap: values.daily_cap ?? null,
      })
      message.success('买家账号已更新')
      setEditing(null)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '更新失败')
    }
  }

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="买家账号 = 一个亚马逊下单号；它登录在哪个指纹浏览器里由「账号名」标注，代理与指纹由外部浏览器管理，ERP 不管代理。"
        description="派单只发给「启用」且当日未超限的账号；同一渠道订单只会派给一个买家账号（库层唯一索引兜底）。账号本期只停用不删除。"
      />
      <Space style={{ marginBottom: 16 }} wrap>
        {canAdmin && (
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建买家账号
          </Button>
        )}
        <Input.Search
          allowClear
          placeholder="按账号名搜索"
          style={{ width: 200 }}
          onSearch={(v) => {
            setQ(v)
            setPage(1)
          }}
        />
        <Select
          allowClear
          placeholder="按站点筛选"
          style={{ width: 220 }}
          value={site}
          onChange={(v) => {
            setSite(v)
            setPage(1)
          }}
          options={SITE_OPTIONS}
        />
        <Select
          allowClear
          placeholder="按状态筛选"
          style={{ width: 150 }}
          value={status}
          onChange={(v) => {
            setStatus(v)
            setPage(1)
          }}
          options={STATUS_OPTIONS}
        />
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      {error && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message={error} closable />
      )}
      <Table<BuyerAccount>
        rowKey={(r) => r.id ?? 0}
        loading={loading}
        dataSource={data?.items ?? []}
        locale={{ emptyText: '还没有买家账号——先建一个，再给它签发插件实例令牌' }}
        pagination={{
          current: page,
          pageSize: data?.size ?? 20,
          total: data?.total ?? 0,
          showSizeChanger: false,
          onChange: setPage,
        }}
        columns={[
          { title: '账号名', dataIndex: 'label' },
          {
            title: '站点',
            dataIndex: 'site',
            width: 190,
            render: (s?: string) => (s ? (SITE_LABEL[s] ?? s) : '—'),
          },
          {
            title: 'customerId',
            dataIndex: 'external_customer_id',
            width: 170,
            render: (v?: string) => <code>{v ?? '—'}</code>,
          },
          {
            title: '状态',
            dataIndex: 'status',
            width: 100,
            render: (s?: string) => {
              const m = s ? ACCOUNT_STATUS[s] : undefined
              return m ? <Tag color={m.color}>{m.label}</Tag> : (s ?? '—')
            },
          },
          {
            title: '单日上限',
            dataIndex: 'daily_cap',
            width: 100,
            render: (v?: number | null) => (v == null ? '不限' : v),
          },
          {
            title: '最近上线',
            dataIndex: 'last_seen_at',
            width: 150,
            render: (v: string | null | undefined, row) => {
              // 已退役的账号不谈掉线——它本来就不该再上线，标灰会变成噪音
              if (row.status === 'retired') return '—'
              const s = lastSeen(v)
              return s.stale ? (
                <span style={{ color: '#999' }}>{s.text}（疑似掉线）</span>
              ) : (
                s.text
              )
            },
          },
          {
            title: '在用实例',
            dataIndex: 'active_instance_count',
            width: 100,
            render: (v?: number) =>
              v ? v : <span style={{ color: '#999' }}>0（无实例，拉不了单）</span>,
          },
          {
            title: '操作',
            key: 'op',
            width: 170,
            render: (_, row) => (
              <Space>
                {canAdmin && (
                  <Button size="small" onClick={() => setEditing(row)}>
                    编辑
                  </Button>
                )}
                <Button size="small" onClick={() => setInstancesOf(row)}>
                  插件实例
                </Button>
              </Space>
            ),
          },
        ]}
      />

      <Modal
        title="新建买家账号"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <AccountForm submitText="创建" onFinish={onCreate} />
      </Modal>

      <Modal
        title={`编辑买家账号：${editing?.label ?? ''}`}
        open={!!editing}
        onCancel={() => setEditing(null)}
        footer={null}
        destroyOnHidden
      >
        {editing && <AccountForm initial={editing} submitText="保存" onFinish={onEdit} />}
      </Modal>

      {instancesOf && (
        <PluginInstanceDrawer
          account={instancesOf}
          onClose={() => setInstancesOf(null)}
          onChanged={() => void load()}
        />
      )}
    </>
  )
}
