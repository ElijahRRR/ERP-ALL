import { Alert, Button, Form, Input, Modal, Select, Space, Table, Tag, Typography, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import {
  ApiError,
  api,
  type PageOf,
  type PluginInstance,
  type PluginInstanceIssued,
} from '@/api/client'
import { useAuth } from '@/auth/AuthContext'
import { ACCOUNT_STATUS, EXEC_MODE, fmtTime, lastSeen } from '@/pages/buyerAccounts/labels'

const EXEC_OPTIONS = Object.entries(EXEC_MODE).map(([value, m]) => ({ value, label: m.label }))
/**
 * 签发弹窗的档位选项：**没有 live**。
 *
 * 契约里签发端点的枚举就只有两个演练档（传 live 一律 422，见 openapi 该端点说明）——
 * 升到 live 是签发之后一次单独的 PATCH，那一步有二次确认与服务端 audit。这里跟着契约走，
 * 不是前端自行加的限制：留着一个必然 422 的选项只会让人以为「签发时就能选实盘」。
 */
const ISSUE_EXEC_OPTIONS = EXEC_OPTIONS.filter((o) => o.value !== 'live')

/**
 * 签发回执。**明文令牌只在这一个组件里存在，且只在本次弹窗生命周期内**。
 *
 * 关掉即不可再取：后端库里只有 sha256（`plugin/auth.py::token_digest`），列表端点
 * 逐字不返回 token 的任何形态。这里既不写 localStorage 也不写 sessionStorage，
 * 也不回填进外层的实例列表——遗失只能吊销后重新签发，这正是设计意图。
 *
 * 复制按钮用 antd `Typography.Paragraph copyable`，它走 `copy-to-clipboard`
 * （`document.execCommand`）而不是 `navigator.clipboard`：本系统按 http://内网IP 部署，
 * 非安全上下文下 `navigator.clipboard` 是 undefined，直接用会静默失效。
 */
function IssuedTokenModal({
  issued,
  onClose,
}: {
  issued: PluginInstanceIssued
  onClose: () => void
}) {
  return (
    <Modal
      title={`实例 #${issued.id} 已签发`}
      open
      onCancel={onClose}
      maskClosable={false}
      footer={
        <Button type="primary" onClick={onClose}>
          我已保存，关闭
        </Button>
      }
    >
      <Alert
        type="error"
        showIcon
        style={{ marginBottom: 12 }}
        message="此令牌只显示这一次"
        description="关闭本窗口后无法再取（服务端只存散列）。请立刻粘贴进插件设置；遗失请吊销后重新签发。"
      />
      <Typography.Paragraph copyable={{ text: issued.token }} style={{ marginBottom: 0 }}>
        <Typography.Text code style={{ wordBreak: 'break-all' }}>
          {issued.token}
        </Typography.Text>
      </Typography.Paragraph>
      <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
        插件侧需要两个请求头：<Typography.Text code>X-Plugin-Instance: {issued.id}</Typography.Text>
        {' 与 '}
        <Typography.Text code>X-Plugin-Token: 上面这串</Typography.Text>。
      </Typography.Paragraph>
      <Typography.Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>
        插件侧只需要配这两个头和 baseUrl，<b>不需要配任何买家账号</b>——它登的是哪个号，
        每次请求都会自己带上来。
      </Typography.Paragraph>
    </Modal>
  )
}

/**
 * 采购插件实例管理（**团队级**）。
 *
 * 本页此前是买家账号页下的一个抽屉（`buyerAccounts/PluginInstanceDrawer`），随身份模型
 * 更正改成独立页：令牌绑的是**一台授权浏览器**，不绑买家账号（图纸 07:288-340）。
 * 旧形状「先选一个账号，再给它签发令牌」正是 Owner 指出的死循环——装插件前先要知道
 * customerId，而 customerId 只有装了插件才看得到。
 *
 * 「最近登录买家号」是**观察值不是绑定关系**：它由插件每次请求带上来的 customerId 在
 * 本团队内 JOIN 出来，换号即变。任何据此推断「这台机器只能拉那个号的单」的读法都是错的。
 */
export default function PluginInstancesPage() {
  const { has } = useAuth()
  const canAdmin = has('procurement.plugin_instance_admin')
  const [data, setData] = useState<PageOf<PluginInstance> | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [page, setPage] = useState(1)
  const [status, setStatus] = useState<string | undefined>()
  const [issueOpen, setIssueOpen] = useState(false)
  const [issued, setIssued] = useState<PluginInstanceIssued | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const qs = status ? `&status=${status}` : ''
      setData(await api.get<PageOf<PluginInstance>>(`/plugin-instances?page=${page}&size=20${qs}`))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page, status])

  useEffect(() => {
    void load()
  }, [load])

  async function issue(values: { exec_mode?: string; version?: string }) {
    try {
      const r = await api.post<PluginInstanceIssued>(
        '/plugin-instances',
        // 版本留空就别送空串——存进去列表里会显示成一个空格而不是「—」
        { ...values, version: values.version || undefined },
      )
      setIssueOpen(false)
      setIssued(r) // 明文只在这个 state 里活到弹窗关闭
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '签发失败')
    }
  }

  async function revoke(row: PluginInstance) {
    try {
      await api.post(`/plugin-instances/${row.id}/revoke`)
      message.success(`实例 #${row.id} 已吊销`)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '吊销失败')
    }
  }

  async function setMode(row: PluginInstance, mode: string) {
    try {
      await api.patch(`/plugin-instances/${row.id}`, { exec_mode: mode })
      message.success('执行档已更新')
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '改档失败')
    }
  }

  function onModeChange(row: PluginInstance, mode: string) {
    // 升到 live 是「此后真实下单花钱」的开关，必须过一道显式确认（服务端另有 audit 留痕）。
    // 反方向（降回演练档）不拦：那是往安全一侧走。
    if (mode === 'live') {
      Modal.confirm({
        title: `确认把实例 #${row.id} 切到实盘 live？`,
        content: (
          <>
            切换后这台浏览器执行的采购任务会<b>真实下单、真实扣款</b>
            ，无论它当时登的是哪个买家号。仅在这台机器已完成「付款前停」演练、且本轮要正式跑单时才切。
          </>
        ),
        okText: '确认切到 live',
        okButtonProps: { danger: true },
        cancelText: '取消',
        onOk: () => setMode(row, mode),
      })
      return
    }
    void setMode(row, mode)
  }

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="一个实例 = 一台装了插件的授权浏览器；令牌是实例专属的，禁止多机共用一把。"
        description={
          <>
            令牌<b>不绑买家账号</b>：这台机器此刻登的是哪个号，由插件每次请求自己带上来，服务端在
            本团队范围内解析——换登另一个已认领的号，派单会自动跟着走。插件侧只需要配令牌与
            baseUrl 两项。新签发的实例默认「付款前停」，不会花钱。掉线判断看「最近上线」——它由
            插件拉任务时写。
          </>
        }
      />
      <Space style={{ marginBottom: 16 }} wrap>
        {canAdmin && (
          <Button type="primary" onClick={() => setIssueOpen(true)}>
            签发新实例
          </Button>
        )}
        <Select
          allowClear
          placeholder="按状态筛选"
          style={{ width: 150 }}
          value={status}
          onChange={(v) => {
            setStatus(v)
            setPage(1)
          }}
          options={[
            { value: 'active', label: '在用' },
            { value: 'revoked', label: '已吊销' },
          ]}
        />
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      {error && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message={error} closable />
      )}
      <Table<PluginInstance>
        rowKey={(r) => r.id ?? 0}
        loading={loading}
        dataSource={data?.items ?? []}
        pagination={{
          current: page,
          pageSize: data?.size ?? 20,
          total: data?.total ?? 0,
          showSizeChanger: false,
          onChange: setPage,
        }}
        locale={{ emptyText: '本团队还没有插件实例——点左上角「签发新实例」拿令牌' }}
        columns={[
          { title: '实例 ID', dataIndex: 'id', width: 90 },
          {
            title: '状态',
            dataIndex: 'status',
            width: 90,
            render: (s?: string) =>
              s === 'active' ? <Tag color="green">在用</Tag> : <Tag>已吊销</Tag>,
          },
          {
            title: '执行档',
            dataIndex: 'exec_mode',
            width: 260,
            render: (m: string | undefined, row) =>
              canAdmin && row.status === 'active' ? (
                <Select
                  size="small"
                  style={{ width: 240 }}
                  value={m}
                  options={EXEC_OPTIONS}
                  onChange={(v) => onModeChange(row, v)}
                />
              ) : (
                <Tag color={m ? EXEC_MODE[m]?.color : undefined}>
                  {(m && EXEC_MODE[m]?.label) ?? m ?? '—'}
                </Tag>
              ),
          },
          {
            // 图纸 07:283 的排障场景（「这台机器现在登的是哪个号」）。**观察值，不是绑定**。
            // 只显示一串 customerId 对运营没用，故后端把账号名与账号状态 JOIN 出来一并给：
            // 标着「待认领」的那些，正是这一页唯一能看出「该去认领谁」的入口。
            title: '最近登录买家号',
            dataIndex: 'last_seen_account_label',
            width: 230,
            render: (label: string | null | undefined, row) => {
              if (!row.last_seen_customer_id) {
                return <span style={{ color: '#999' }}>从未上报</span>
              }
              const st = row.last_seen_account_status
              const meta = st ? ACCOUNT_STATUS[st] : undefined
              return (
                <Space direction="vertical" size={0}>
                  <span>
                    {label ?? <span style={{ color: '#999' }}>未认领</span>}
                    {meta && st !== 'active' && (
                      <Tag color={meta.color} style={{ marginLeft: 8 }}>
                        {meta.label}
                      </Tag>
                    )}
                  </span>
                  <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                    <code>{row.last_seen_customer_id}</code>
                  </Typography.Text>
                </Space>
              )
            },
          },
          { title: '插件版本', dataIndex: 'version', width: 100, render: (v?: string) => v ?? '—' },
          {
            title: '最近上线',
            dataIndex: 'last_seen_at',
            width: 150,
            render: (v: string | null | undefined, row) => {
              // 已吊销的实例不谈掉线——它本来就不该再上线
              if (row.status !== 'active') return fmtTime(v)
              const s = lastSeen(v)
              return <span style={s.stale ? { color: '#999' } : undefined}>{s.text}</span>
            },
          },
          {
            title: '签发于',
            dataIndex: 'created_at',
            width: 170,
            render: (v?: string) => fmtTime(v),
          },
          {
            title: '操作',
            key: 'op',
            width: 90,
            render: (_, row) =>
              canAdmin && row.status === 'active' ? (
                <Button
                  size="small"
                  danger
                  onClick={() =>
                    Modal.confirm({
                      title: `确认吊销实例 #${row.id}？`,
                      content:
                        '吊销后该令牌对全部插件端点立即 401，那台浏览器会停止拉单与回报。吊销不可撤销——要恢复只能重新签发一把新令牌。',
                      okText: '确认吊销',
                      okButtonProps: { danger: true },
                      cancelText: '取消',
                      onOk: () => revoke(row),
                    })
                  }
                >
                  吊销
                </Button>
              ) : null,
          },
        ]}
      />

      <Modal
        title="签发新实例"
        open={issueOpen}
        onCancel={() => setIssueOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 16 }}
          message="签发不需要先有买家账号"
          description="令牌代表这台浏览器。把它配进插件、让插件跑一次拉取，它当时登的买家号会自动出现在「买家账号池」里等认领。"
        />
        <Form layout="vertical" onFinish={issue} initialValues={{ exec_mode: 'stop_before_payment' }}>
          <Form.Item
            label="执行档"
            name="exec_mode"
            extra={
              EXEC_MODE.stop_before_payment.hint +
              '；默认即此档。签发只能选演练档——实盘 live 要在上面的实例列表里单独切。'
            }
          >
            <Select options={ISSUE_EXEC_OPTIONS} />
          </Form.Item>
          <Form.Item label="插件版本（可选）" name="version" extra="填了便于日后对照哪台机器跑的哪版">
            <Input placeholder="如 v2.4.1" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            签发并显示令牌
          </Button>
        </Form>
      </Modal>

      {issued && <IssuedTokenModal issued={issued} onClose={() => setIssued(null)} />}
    </>
  )
}
