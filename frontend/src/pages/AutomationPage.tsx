import { Alert, Button, Modal, Segmented, Space, Switch, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type AutomationPolicy } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'
import { EVALUATION_LABEL, FLOW_LABEL, MODE_LABEL, MODE_TAG_COLOR } from '@/pages/automation/labels'

// 闸类 flow 的危险确认文案。**这句是本页最要紧的一行字**：对 order_block /
// compliance_block 而言 manual 不是「更保守」而是「更放松」——拦截直接不生效。
// 误把它当成收紧的人会以为自己在加锁，实际是在开门。
const GATE_WARN =
  '对本流程而言 manual 是「只软标记不冻结」——拦截将不生效，命中的单/商品会照常放行。'

function modeText(mode: string): string {
  return MODE_LABEL[mode] ?? mode
}

/** 状态列主 Tag：三态各自可辨（unset / disabled 绝不能压成「配了 manual」）。 */
function stateTag(row: AutomationPolicy, gate: boolean) {
  if (row.state === 'unset') return <Tag>未配置（等同人工）</Tag>
  if (row.state === 'disabled') return <Tag color="orange">已停用（等同人工）</Tag>
  const mode = row.mode ?? ''
  // 闸类**且已接线**、实际生效档会拦截（auto，或非法遗留的 semi）时点明「拦截生效」。
  // wired 是硬前提：对没接消费点的 flow 说「拦截生效」是假话——compliance_block 今天
  // 就没有任何代码读它（审查 F1，与「ERP_ENV 从未注入而判据全绿」同形）。
  const intercepting = row.effective_mode === 'auto' || row.effective_mode === 'semi'
  const suffix = gate && row.wired && intercepting ? '（拦截生效）' : ''
  return (
    <Tag color={MODE_TAG_COLOR[mode]}>
      {modeText(mode)}
      {suffix}
    </Tag>
  )
}

function renderState(row: AutomationPolicy) {
  // gate/wired 都来自服务端（FlowSpec / WIRED_FLOWS 派生）。前端此前硬编码闸类清单，
  // §09 改判闸类时确认框和红字会静默失灵（审查 F4）——现在契约就是唯一权威。
  const gate = row.gate
  return (
    <Space direction="vertical" size={2}>
      <Space size={4} wrap>
        {stateTag(row, gate)}
        {!row.wired && (
          // 未接消费点：改档只落库、不改变任何系统行为。必须显式说出来，
          // 否则运维会以为「合规闸开着」而它根本没接线。
          <Tag color="default" style={{ color: '#888' }}>
            未接线（改档暂不生效）
          </Tag>
        )}
        {row.illegal_for_flow && (
          // 库层合法（ck_automation_mode 只管三档取值、不区分 flow）但 flow 层非法。
          // 内核只告警不改写，故这里如实写出「实际按哪档跑」而不是笼统说「按原值」——
          // mode 是坏字符串时实际生效的是 manual，写成「按原值生效」就成了假话。
          <Tag color="red">
            档位非法（{row.mode ?? '—'}），实际按「{modeText(row.effective_mode)}」生效
          </Tag>
        )}
      </Space>
      {gate && row.wired && row.effective_mode === 'manual' && (
        // 按事实挂而不是按 state 挂：unset / disabled / configured+manual 三种都是
        // 「拦截没生效」。wired 是前提——未接线的 flow「不生效」是常态而非警报。
        // 零行团队进来会看到一条红字（order_block）——那是真话，不是 bug。
        <span style={{ color: '#cf1322' }}>拦截当前不生效</span>
      )}
    </Space>
  )
}

export default function AutomationPage() {
  const { has } = useAuth()
  const canWrite = has('automation.write')
  const [items, setItems] = useState<AutomationPolicy[]>([])
  const [loading, setLoading] = useState(false)
  // 正在保存的 flow_code：只禁用该行控件，其余行照常可点
  const [saving, setSaving] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setItems(await api.get<AutomationPolicy[]>('/automation-policies'))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  // 写入唯一出口：两个 handler 都经此，档位与启用位永远整对提交（PUT 是全量置态）
  async function save(row: AutomationPolicy, mode: string, enabled: boolean) {
    setSaving(row.flow_code)
    try {
      await api.put(`/automation-policies/${row.flow_code}`, { mode, enabled })
      message.success('档位已保存')
      await load()
    } catch (e) {
      // 422 AUTOMATION_MODE_ILLEGAL（档位对该 flow 非法，报文自带合法档位清单）直接透出
      message.error(e instanceof ApiError ? e.message : '保存失败', 6)
    } finally {
      setSaving(null)
    }
  }

  function onModeChange(row: AutomationPolicy, mode: string) {
    // 沿用当前启用位：改档不该顺手把一条被人停用的 flow 重新打开
    const enabled = row.enabled ?? true
    // 只要当前**真在拦截**（effective 是 auto，或非法遗留的 semi——order_block 存 semi
    // 时订单闸照样拦，审查 F2 抓的正是这一档）且目标是放松到 manual，就要过危险确认。
    // wired 前提同上：未接线的 flow 改档不改变行为，吓唬人只会让确认框被点成肌肉记忆。
    const intercepting = row.effective_mode === 'auto' || row.effective_mode === 'semi'
    if (row.gate && row.wired && intercepting && mode === 'manual') {
      Modal.confirm({
        title: `确认把「${FLOW_LABEL[row.flow_code] ?? row.flow_code}」切回人工？`,
        content: GATE_WARN,
        okText: '确认切回人工',
        okButtonProps: { danger: true },
        cancelText: '取消',
        onOk: () => save(row, mode, enabled),
      })
      return
    }
    void save(row, mode, enabled)
  }

  function onEnabledChange(row: AutomationPolicy, enabled: boolean) {
    // unset 行没有 mode，取 manual 兜底：置态后语义仍等同人工，实际行为不变
    const mode = row.mode ?? 'manual'
    const intercepting = row.effective_mode === 'auto' || row.effective_mode === 'semi'
    if (row.gate && row.wired && intercepting && !enabled) {
      Modal.confirm({
        // 停用与「切回 manual」后果相同（resolve_mode 对 enabled=false 一律回 manual），
        // 所以这条闸同样要弹危险确认，不能只拦档位那一路。
        title: `确认停用「${FLOW_LABEL[row.flow_code] ?? row.flow_code}」？`,
        content: `停用后本流程退回 manual。${GATE_WARN}`,
        okText: '确认停用',
        okButtonProps: { danger: true },
        cancelText: '取消',
        onOk: () => save(row, mode, enabled),
      })
      return
    }
    void save(row, mode, enabled)
  }

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="档位改完即时生效（realtime 型下一次决策就按新档跑，不进缓存、无需重启）；snapshot 型在请求创建时固化，不影响在途请求。"
        description="闸类流程的 manual 只软标记不冻结——退回人工或停用都等于拦截不生效。标「未接线」的流程（含合规闸）当前没有任何代码读取档位，改档只落库、不改变行为，待后续增量接线。"
      />
      <Space style={{ marginBottom: 16 }} wrap>
        <Button onClick={() => void load()}>刷新</Button>
        {!canWrite && <Tag>只读（缺 automation.write）</Tag>}
      </Space>
      <Table<AutomationPolicy>
        rowKey="flow_code"
        loading={loading}
        dataSource={items}
        // 不分页：行数由后端 AutomationFlow 枚举定死为 10，顺序即 001 §09 注册清单顺序
        pagination={false}
        size="middle"
        columns={[
          {
            title: '流程',
            dataIndex: 'flow_code',
            width: 220,
            render: (code: string) => (
              <>
                <div>{FLOW_LABEL[code] ?? code}</div>
                <code style={{ color: '#888', fontSize: 12 }}>{code}</code>
              </>
            ),
          },
          {
            title: '求值语义',
            dataIndex: 'evaluation',
            width: 110,
            render: (v: string) => {
              const meta = EVALUATION_LABEL[v]
              return meta ? (
                <Tag color={meta.color} title={meta.tip}>
                  {meta.label}
                </Tag>
              ) : (
                <Tag>{v}</Tag>
              )
            },
          },
          {
            title: '状态',
            dataIndex: 'state',
            width: 260,
            render: (_: string, row: AutomationPolicy) => renderState(row),
          },
          {
            title: '档位',
            dataIndex: 'mode',
            width: 240,
            // 档位集由 API 的 legal_modes 驱动（闸类自然只渲染两档），前端不硬编码
            render: (_: string | null, row: AutomationPolicy) => (
              <Segmented
                // **不能写 `?? undefined`**——那正是部署机 UI 判据 C-unset 抓到的缺陷。
                // rc-segmented 对 `value === undefined` 回落到**第一个选项**
                // （`useMergedState(segmentedOptions[0]?.value, {value})`，index.js:118），
                // 而第一档恒为 manual（`_MODE_ORDER` 保守档最左）——于是未配置行渲染成
                // 「已选中人工」，把本增量最核心的 unset≠configured-as-manual 就地抹平：
                // 新团队十行全显示「有人配了人工」，order_block 上更像是有人主动选过。
                // 连带第二症：item 是 `<input type=radio checked>`，点击**已选中**项不触发
                // change，所以那条路上「显式设成人工」根本发不出请求；对闸类 flow（只有
                // manual/auto 两档）唯一绕法是先开 auto——先打开拦截再关回去。
                // 传一个不匹配任何选项的值 → 无选中项（三态在控件上也分得开），且点 manual
                // 是 false→true 的真实变更，请求正常发出。空串同时覆盖「库里存着脏串」那档。
                value={row.mode ?? ''}
                options={row.legal_modes.map((m) => ({ value: m, label: modeText(m) }))}
                disabled={!canWrite || saving === row.flow_code}
                onChange={(v) => onModeChange(row, String(v))}
              />
            ),
          },
          {
            title: '启用',
            dataIndex: 'enabled',
            width: 100,
            render: (_: boolean | null, row: AutomationPolicy) => (
              <Switch
                checked={row.enabled ?? false}
                disabled={!canWrite || saving === row.flow_code}
                checkedChildren="启用"
                unCheckedChildren="停用"
                onChange={(en) => onEnabledChange(row, en)}
              />
            ),
          },
          {
            title: '最后修改',
            dataIndex: 'updated_at',
            render: (v: string | null, row: AutomationPolicy) =>
              v ? `${new Date(v).toLocaleString('zh-CN')} · 用户 #${row.updated_by ?? '—'}` : '—',
          },
        ]}
      />
    </>
  )
}
