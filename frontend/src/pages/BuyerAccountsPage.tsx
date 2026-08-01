import {
  Alert,
  Button,
  Checkbox,
  Input,
  Modal,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  message,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type BuyerAccount, type PageOf } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'
import { AccountForm, ClaimForm, type FormValues } from '@/pages/buyerAccounts/AccountForms'
import { ACCOUNT_STATUS, SITE_LABEL, lastSeen } from '@/pages/buyerAccounts/labels'

const SITE_OPTIONS = Object.entries(SITE_LABEL).map(([value, label]) => ({ value, label }))
const STATUS_OPTIONS = Object.entries(ACCOUNT_STATUS).map(([value, m]) => ({
  value,
  label: m.label,
}))

export default function BuyerAccountsPage() {
  const { has } = useAuth()
  const canAdmin = has('procurement.buyer_account_admin')
  const [data, setData] = useState<PageOf<BuyerAccount> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [site, setSite] = useState<string | undefined>()
  const [status, setStatus] = useState<string | undefined>()
  // 默认折叠已驳回（后端 `include_rejected`，同 14c 产品页折叠 retired）。驳回是终态且
  // 不删行，被灌过一轮伪造 customerId 的团队池子里会长期躺着一堆——默认视图堆满垃圾，
  // 真正要认领的那条就看不见了。
  const [includeRejected, setIncludeRejected] = useState(false)
  const [q, setQ] = useState('')
  const [qInput, setQInput] = useState('')
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<BuyerAccount | null>(null)
  const [claiming, setClaiming] = useState<BuyerAccount | null>(null)
  // 待认领行数：单独一次 count 查询（size=1 只取 total），走 `ix_buyer_account_pending`。
  // 不从当前页数据里数——默认视图可能被站点/搜索筛过，也可能翻在第 3 页，
  // 而「有几个号在等人认领」必须是全池的数，否则这条提示会漏报。
  const [pendingCount, setPendingCount] = useState(0)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const qs = [
        `page=${page}`,
        'size=20',
        site ? `site=${site}` : '',
        // 后端语义：显式 status 时 include_rejected 不生效（按状态精确筛选优先）。
        // 故只在无 status 时才发它——发一个后端会忽略的参数等于在请求里留一句假话。
        status ? `status=${status}` : includeRejected ? 'include_rejected=true' : '',
        q ? `q=${encodeURIComponent(q)}` : '',
      ]
        .filter(Boolean)
        .join('&')
      const [pageData, pending] = await Promise.all([
        api.get<PageOf<BuyerAccount>>(`/buyer-accounts?${qs}`),
        api.get<PageOf<BuyerAccount>>('/buyer-accounts?status=pending_claim&page=1&size=1'),
      ])
      setData(pageData)
      setPendingCount(pending.total ?? 0)
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page, site, status, includeRejected, q])

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

  async function onClaim(values: FormValues) {
    if (!claiming) return
    try {
      await api.patch(`/buyer-accounts/${claiming.id}`, {
        ...values,
        status: 'active',
        daily_cap: values.daily_cap ?? null,
      })
      message.success(`已认领并启用：${values.label}`)
      setClaiming(null)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '认领失败')
    }
  }

  function confirmReject(row: BuyerAccount) {
    Modal.confirm({
      title: '确认驳回这个 customerId？',
      content: (
        <>
          驳回后 <code>{row.external_customer_id}</code> 变成<b>终态「已驳回」，不可撤销</b>；
          该 customerId 会被<b>永久占用</b>，此后插件再带它上来也不会重新登记、不会再有通知。
          <br />
          用于「这不是我们的号」——若只是暂时不想派单给它，请改用认领后置为「暂停派单」。
        </>
      ),
      okText: '确认驳回',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        try {
          await api.patch(`/buyer-accounts/${row.id}`, { status: 'rejected' })
          message.success('已驳回')
          void load()
        } catch (e) {
          message.error(e instanceof ApiError ? e.message : '驳回失败')
        }
      },
    })
  }

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="买家账号 = 一个亚马逊下单号；它登录在哪个指纹浏览器里由「账号名」标注，代理与指纹由外部浏览器管理，ERP 不管代理。"
        description={
          <>
            账号<b>不靠人工预录入</b>：插件每次请求都会带上它当前登录的 customerId，本团队没见过
            的会自动落成一条「待认领」行并通知——补齐账号名与站点<b>认领</b>后才开始派单。
            令牌绑的是<b>一台授权浏览器</b>而不是买家账号（在「插件实例」页签发），同一台机器换登
            另一个已认领的号，派单会自动跟着走。派单只发给「启用」且当日未超限的账号；同一渠道订单
            只会派给一个买家账号（库层唯一索引兜底）。账号本期只停用不删除。
          </>
        }
      />
      {pendingCount > 0 && (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message={`有 ${pendingCount} 个待认领的买家号——它们收不到任何派单`}
          description="这些是插件上报过、但还没人确认的 customerId。是我们的号就认领（补账号名与站点），不是就驳回。"
          action={
            <Button
              size="small"
              onClick={() => {
                setPage(1)
                setSite(undefined)
                // 必须连搜索框的显示值一起清：待认领行的 label 是 NULL，任何 `q`
                // 都会把它们滤掉（`label ILIKE :q` 对 NULL 恒为 NULL）——只清 q 而
                // 留着框里的旧字，用户会看到「筛了待认领却一条没有」且找不到原因。
                setQInput('')
                setQ('')
                setStatus('pending_claim')
              }}
            >
              只看待认领
            </Button>
          }
        />
      )}
      <Space style={{ marginBottom: 16 }} wrap>
        {canAdmin && (
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建买家账号
          </Button>
        )}
        {/* 受控：`qInput` 是框里显示的字，`q` 是**已生效**的筛选值。两者分开而不是
            合成一个，是为了保住「回车才搜」的语义（合成一个就变成逐键请求）；
            而搜索框必须受控，否则上面「只看待认领」清不掉它显示的旧字。 */}
        <Input.Search
          allowClear
          placeholder="按账号名搜索"
          style={{ width: 200 }}
          value={qInput}
          onChange={(e) => setQInput(e.target.value)}
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
        {/* 开关在选了状态时**禁用而非静默失效**（同 14c 产品页）：后端此时忽略
            include_rejected，留一个点得动却不起作用的勾正是本项目反复栽跟头的
            那类「看起来配着、实际没生效」。 */}
        <Tooltip title={status ? '已按状态精确筛选，此时不再折叠——清空状态筛选后可用' : ''}>
          <Checkbox
            disabled={!!status}
            checked={includeRejected}
            onChange={(e) => {
              setPage(1)
              setIncludeRejected(e.target.checked)
            }}
          >
            显示已驳回
          </Checkbox>
        </Tooltip>
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      {error && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message={error} closable />
      )}
      <Table<BuyerAccount>
        rowKey={(r) => r.id ?? 0}
        loading={loading}
        dataSource={data?.items ?? []}
        locale={{
          emptyText:
            '还没有买家账号——去「插件实例」页签发一把令牌，让那台浏览器跑一次拉取，账号会自动出现在这里等认领',
        }}
        pagination={{
          current: page,
          pageSize: data?.size ?? 20,
          total: data?.total ?? 0,
          showSizeChanger: false,
          onChange: setPage,
        }}
        columns={[
          {
            title: '账号名',
            dataIndex: 'label',
            // 待认领/已驳回行没有账号名（首见登记时服务端只知道一个 customerId，名字是人给的）
            render: (v?: string | null) => v ?? <span style={{ color: '#999' }}>—（未认领）</span>,
          },
          {
            title: '站点',
            dataIndex: 'site',
            width: 190,
            render: (s?: string | null) => (s ? (SITE_LABEL[s] ?? s) : '—'),
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
              // 已驳回同理不标「疑似掉线」；但**时间照显**——一个被驳回的 customerId 仍在
              // 活动，正是「有人在灌」的信号，藏起来就看不见了
              if (row.status === 'rejected') return s.text
              return s.stale ? (
                <span style={{ color: '#999' }}>{s.text}（疑似掉线）</span>
              ) : (
                s.text
              )
            },
          },
          {
            title: '操作',
            key: 'op',
            width: 180,
            render: (_, row) => {
              if (!canAdmin) return null
              if (row.status === 'pending_claim') {
                return (
                  <Space>
                    <Button type="primary" size="small" onClick={() => setClaiming(row)}>
                      认领
                    </Button>
                    <Button size="small" danger onClick={() => confirmReject(row)}>
                      驳回
                    </Button>
                  </Space>
                )
              }
              // 已驳回是终态：不给编辑入口——那一行唯一还能改的只有备注，
              // 而放一个「编辑」按钮会让人以为还能改回启用（后端一律 409）
              if (row.status === 'rejected') {
                return <span style={{ color: '#999' }}>终态</span>
              }
              return (
                <Button size="small" onClick={() => setEditing(row)}>
                  编辑
                </Button>
              )
            },
          },
        ]}
      />

      <Modal
        title="新建买家账号（可选捷径）"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="通常不需要走这里"
          description="主路径是让插件在那台浏览器上跑一次拉取，系统会自动登记待认领行。只有手上已经有 customerId 时，预建才能省一轮往返。"
        />
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

      <Modal
        title="认领买家账号"
        open={!!claiming}
        onCancel={() => setClaiming(null)}
        footer={null}
        destroyOnHidden
      >
        {claiming && <ClaimForm account={claiming} onFinish={onClaim} />}
      </Modal>
    </>
  )
}
