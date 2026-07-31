import {
  Alert,
  Button,
  Drawer,
  Form,
  Input,
  Modal,
  Select,
  Table,
  Tag,
  Typography,
  message,
} from 'antd'
import { useCallback, useEffect, useState } from 'react'

import {
  ApiError,
  api,
  type BuyerAccount,
  type PluginInstance,
  type PluginInstanceIssued,
} from '@/api/client'
import { useAuth } from '@/auth/AuthContext'

import { EXEC_MODE, fmtTime, lastSeen } from './labels'

const EXEC_OPTIONS = Object.entries(EXEC_MODE).map(([value, m]) => ({ value, label: m.label }))

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
function IssuedTokenModal({ issued, onClose }: { issued: PluginInstanceIssued; onClose: () => void }) {
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
    </Modal>
  )
}

export default function PluginInstanceDrawer({
  account,
  onClose,
  onChanged,
}: {
  account: BuyerAccount
  onClose: () => void
  onChanged: () => void
}) {
  const { has } = useAuth()
  const canAdmin = has('procurement.plugin_instance_admin')
  const [items, setItems] = useState<PluginInstance[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [issueOpen, setIssueOpen] = useState(false)
  const [issued, setIssued] = useState<PluginInstanceIssued | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      setItems(await api.get<PluginInstance[]>(`/buyer-accounts/${account.id}/plugin-instances`))
    } catch (e) {
      setError(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [account.id])

  useEffect(() => {
    void load()
  }, [load])

  async function issue(values: { exec_mode?: string; version?: string }) {
    try {
      const r = await api.post<PluginInstanceIssued>(
        `/buyer-accounts/${account.id}/plugin-instances`,
        // 版本留空就别送空串——存进去列表里会显示成一个空格而不是「—」
        { ...values, version: values.version || undefined },
      )
      setIssueOpen(false)
      setIssued(r) // 明文只在这个 state 里活到弹窗关闭
      void load()
      onChanged()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '签发失败')
    }
  }

  async function revoke(row: PluginInstance) {
    try {
      await api.post(`/plugin-instances/${row.id}/revoke`)
      message.success(`实例 #${row.id} 已吊销`)
      void load()
      onChanged()
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
            切换后该实例执行的采购任务会<b>真实下单、真实扣款</b>
            。仅在该买家账号与插件已完成「付款前停」演练、且本轮要正式跑单时才切。
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
    <Drawer
      title={`插件实例：${account.label ?? ''}`}
      open
      width={840}
      onClose={onClose}
      extra={
        canAdmin ? (
          <Button type="primary" onClick={() => setIssueOpen(true)}>
            签发新实例
          </Button>
        ) : null
      }
    >
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="一个实例 = 一个装了插件的浏览器；令牌是实例专属的，禁止多机共用一把。"
        description="新签发的实例默认「付款前停」，不会花钱。掉线判断看「最近上线」——它由插件拉任务时写。"
      />
      {error && (
        <Alert type="error" showIcon style={{ marginBottom: 16 }} message={error} closable />
      )}
      <Table<PluginInstance>
        rowKey={(r) => r.id ?? 0}
        loading={loading}
        dataSource={items}
        pagination={false}
        locale={{ emptyText: '该账号还没有插件实例——点右上角「签发新实例」拿令牌' }}
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
        <Form
          layout="vertical"
          onFinish={issue}
          initialValues={{ exec_mode: 'stop_before_payment' }}
        >
          <Form.Item
            label="执行档"
            name="exec_mode"
            extra={EXEC_MODE.stop_before_payment.hint + '；默认即此档，先演练再升 live。'}
          >
            <Select options={EXEC_OPTIONS} />
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
    </Drawer>
  )
}
