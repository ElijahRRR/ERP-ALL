import { Alert, Button, Modal, Space, Spin, Table, Tag, Typography } from 'antd'
import { useRef, useState } from 'react'

import {
  ApiError,
  api,
  stableIdemKey,
  type AuditBatchResult,
  type IdemTicket,
} from '@/api/client'
import { AUDIT_CODE_LABEL, VERDICT_LABEL, codeText, statusText } from '@/pages/products/labels'

/**
 * 批量送审（R2-09 增量3a，纯手工路径）。
 *
 * 三件事这里必须做对，其余都是排版：
 *
 * 1. **幂等键稳定**。批量送审是同步端点，`frontend/nginx.conf` 的 `proxy_read_timeout`
 *    是 120s，跑久了网关先掐断回 504 而后端仍在跑、钱仍在花。用户必然再点一次——
 *    那一次若带新键就是整批重跑、付两遍钱。故键由 `stableIdemKey` 按**载荷**生成：
 *    同一批产品在同一会话内恒得同一个键（详见 `api/client.ts` 的头注）。
 * 2. **发出去的 product_ids 必须与算键用的规范串同源**（都是去重+升序）。
 *    后端 `run_idempotent` 的 payload_hash 算的是**原样列表**（`json.dumps` 只排键不排值），
 *    所以「同一批、勾选顺序不同」若不归一，会撞成同键异载荷 → 409 IDEMPOTENCY_CONFLICT。
 * 3. **回执全量列出**。逐品有 verdict / 成本 / 错误码，一行 toast 装不下且会自动消失；
 *    008§6 的底线是不许只展示成功面。四个桶（audited/skipped/failed/remaining）
 *    一条不漏地进表，并当场核对结构不变量。
 */

type Phase = 'confirm' | 'running' | 'result'

type RowKind = 'audited' | 'skipped' | 'failed' | 'remaining'

interface ResultRow {
  key: string
  kind: RowKind
  product_id: number
  run_id?: number
  verdict?: string
  reject_level?: string | null
  llm_cost_usd?: number
  product_status?: string | null
  code?: string
  message?: string
}

const KIND_TAG: Record<RowKind, { text: string; color: string }> = {
  audited: { text: '已审核', color: 'blue' },
  skipped: { text: '跳过（未开审）', color: 'default' },
  failed: { text: '失败（可能已扣费）', color: 'volcano' },
  remaining: { text: '未开审（本批没轮到）', color: 'geekblue' },
}

/** 去重 + 升序：既是发出去的载荷，也是算幂等键的规范串来源（见头注第 2 条）。 */
function normalize(ids: number[]): number[] {
  return Array.from(new Set(ids)).sort((a, b) => a - b)
}

function toRows(r: AuditBatchResult): ResultRow[] {
  return [
    ...r.audited.map((a) => ({ key: `a${a.product_id}`, kind: 'audited' as const, ...a })),
    ...r.skipped.map((s) => ({ key: `s${s.product_id}`, kind: 'skipped' as const, ...s })),
    ...r.failed.map((f) => ({ key: `f${f.product_id}`, kind: 'failed' as const, ...f })),
    ...r.remaining.map((id) => ({ key: `r${id}`, kind: 'remaining' as const, product_id: id })),
  ]
}

/**
 * 失败归类：只认**我们写过处置建议**的码，其余一律归到 NO_RESULT 兜底档。
 *
 * 这不是偷懒的 default，是针对具体故障形状的：nginx 掐断连接（504）返回的是
 * HTML 错误页，**不带 002 错误信封**，`client.ts` 只能回落成
 * `ApiError(504, 'UNKNOWN', '请求失败')`。若直接把 `e.code` 拿去当标题，用户在
 * 「后端可能仍在跑、别乱点」最要紧的时刻看到的是裸码「UNKNOWN」+「请求失败」，
 * 既违 008§6，也没给出唯一正确的动作（原样重试同一批）。
 * 原始码与原文不丢，落在下面的灰字里供运维。
 */
function classify(e: unknown): { code: string; raw: string; message: string } {
  if (e instanceof ApiError) {
    const known = e.code in AUDIT_CODE_LABEL
    return {
      code: known ? e.code : 'NO_RESULT',
      raw: `HTTP ${e.status} · ${e.code}`,
      message: e.message,
    }
  }
  return { code: 'NO_RESULT', raw: '网络层异常', message: '请求未送达或连接中断' }
}

function Counter({ label, n, color }: { label: string; n: number; color: string }) {
  return (
    <span style={{ marginRight: 16 }}>
      {label} <b style={{ color, fontSize: 16 }}>{n}</b>
    </span>
  )
}

export default function BatchAuditModal({
  productIds,
  excludedCount,
  onBatchDone,
  onClose,
}: {
  /** 已按状态筛过的合格 id */
  productIds: number[]
  /** 勾选里被状态过滤排除的件数（只在第一轮提示） */
  excludedCount: number
  /** 每批成功返回后回调（容器据此刷新列表） */
  onBatchDone: () => void
  onClose: () => void
}) {
  const [ids, setIds] = useState<number[]>(() => normalize(productIds))
  const [phase, setPhase] = useState<Phase>('confirm')
  const [result, setResult] = useState<AuditBatchResult | null>(null)
  const [submitted, setSubmitted] = useState(0)
  const [err, setErr] = useState<ReturnType<typeof classify> | null>(null)
  // 是否已进入「续跑剩余」轮次——第二轮起不该再提「已排除 N 件」（那是首轮勾选的事）
  const continuedRef = useRef(false)
  // 最近一次用过的幂等票据。留着只为「重放之后想真的重审一遍」这一个出口（见下方
  // forceRerun）——除此之外都不该主动 clear。
  const ticketRef = useRef<IdemTicket | null>(null)

  async function run() {
    const payloadIds = ids // 已在 setIds 处归一（去重升序），此处直接用同一份算键与发送
    setPhase('running')
    setErr(null)
    // 规范串 = 升序 id + '|' + levels。levels 本页不传（后端默认 l0/l2/l3），
    // 但分隔符与空段保留：将来加 levels 选择器时旧键不会与新键混淆。
    const ticket = await stableIdemKey('ab', `${payloadIds.join(',')}|`)
    ticketRef.current = ticket
    try {
      const r = await api.post<AuditBatchResult>(
        '/products/audit',
        { product_ids: payloadIds },
        { idemKey: ticket.key },
      )
      setSubmitted(payloadIds.length)
      setResult(r)
      setPhase('result')
      onBatchDone()
      // 拿到**非重放**的终局响应才作废 nonce：这一批确实跑完了，下次同样的勾选
      // 就该是一次全新的、本就要花钱的送审。重放/超时/409 一律保留 nonce——
      // 那几种情况下用户重试必须带同一个键，否则就是重复扣费。
      if (!r.idempotent_replay) ticket.clear()
    } catch (e) {
      // nonce 不清：这次没拿到终局结果，用户重试**必须**带同一个键
      setErr(classify(e))
      setPhase('confirm')
    }
  }

  /**
   * 重放之后的唯一出口：作废 nonce，回到确认页，下次点确认就是一次**真实、要花钱**
   * 的重审。
   *
   * 没有这个按钮时的死结（2026-07-28 审查 F3/F8）：504 → 用户按提示原样重试 →
   * 命中重放 → `idempotent_replay=true` → nonce 保留。此后本会话内再勾同一组 id，
   * `stableIdemKey` 恒产出同一个键，服务端 24h TTL 内一直回放那份旧结果——**这一批
   * 就再也审不动了**，而重审恰恰是审核域的核心动作（政策修好了要重跑）。
   * 默认仍保留 nonce（那是安全侧），出口做成需要明确点一下的显式动作。
   */
  function forceRerun() {
    ticketRef.current?.clear()
    ticketRef.current = null
    setResult(null)
    setErr(null)
    setPhase('confirm')
  }

  const counts = result?.verdict_counts
  const covered = result
    ? result.audited.length + result.skipped.length + result.failed.length + result.remaining.length
    : 0

  return (
    <Modal
      title="批量送审"
      open
      width={880}
      onCancel={onClose}
      maskClosable={false}
      closable={phase !== 'running'}
      footer={
        phase === 'result' ? (
          <Space>
            {!!result?.remaining.length && (
              <Button
                type="primary"
                onClick={() => {
                  continuedRef.current = true
                  setIds(normalize(result.remaining))
                  setResult(null)
                  setPhase('confirm')
                }}
              >
                继续送审剩余 {result.remaining.length} 件
              </Button>
            )}
            <Button onClick={onClose}>关闭</Button>
          </Space>
        ) : (
          <Space>
            <Button onClick={onClose} disabled={phase === 'running'}>
              取消
            </Button>
            <Button
              type="primary"
              loading={phase === 'running'}
              disabled={!ids.length}
              onClick={() => void run()}
            >
              确认送审（{ids.length} 件）
            </Button>
          </Space>
        )
      }
    >
      {phase !== 'result' && (
        <>
          <Alert
            type="warning"
            showIcon
            message={`本批 ${ids.length} 件，将发起最多 ${ids.length} 次 LLM 审核，可能产生费用。`}
            description={
              <>
                <div>
                  服务端逐条执行、部分成功照常返回：先跑完的已经落库并已计费，不会因为后面
                  某条出错而回滚。
                </div>
                <div>
                  跑得太久或 LLM 服务不可用时，服务端会主动收尾，没轮到的会列在「未开审」
                  里——那些没有扣费，可以直接续跑。
                </div>
                <div style={{ color: '#888', marginTop: 4 }}>
                  重复点击是安全的：同一批用同一个幂等键，网关超时后再点命中的是上一次的
                  结果，不会重复扣费。
                </div>
              </>
            }
          />
          {excludedCount > 0 && !continuedRef.current && (
            <Alert
              style={{ marginTop: 8 }}
              type="info"
              showIcon
              message={`已排除 ${excludedCount} 件状态不合格（不会送审）`}
              description="仅「已采集 / 审核通过 / 审核拒绝 / 待人工复核」可送审；其余勾选已自动剔除。"
            />
          )}
          {err && (
            <Alert
              style={{ marginTop: 8 }}
              type="error"
              showIcon
              message={codeText(err.code)}
              description={
                <>
                  <div>{err.message}</div>
                  {AUDIT_CODE_LABEL[err.code] && (
                    <div style={{ marginTop: 4 }}>{AUDIT_CODE_LABEL[err.code].hint}</div>
                  )}
                  <div style={{ color: '#888', marginTop: 4 }}>
                    {err.raw}。无论哪种失败，原样重试同一批都是安全的：幂等键已保留，
                    若后端其实已经跑完，重试拿到的就是那一次的结果，不会重复扣费。
                    请不要改动勾选后再试——换了载荷就是新的一批，会真的重跑。
                  </div>
                </>
              }
            />
          )}
          {phase === 'running' && (
            <div style={{ textAlign: 'center', padding: '24px 0' }}>
              <Spin tip="逐条审核中，请勿关闭本窗口……">
                <div style={{ height: 32 }} />
              </Spin>
            </div>
          )}
        </>
      )}

      {phase === 'result' && result && counts && (
        <>
          {result.idempotent_replay && (
            <Alert
              style={{ marginBottom: 8 }}
              type="info"
              showIcon
              message="这是上一次同批请求的结果重放"
              description={
                <>
                  <div>本次一条都没有重新审核、未产生任何费用；下面列的是上一次的落库结果。</div>
                  <div style={{ color: '#888', marginTop: 4 }}>
                    只要还用这一批同样的勾选，再点也仍是重放。确实要重新审一遍（例如政策已
                    更新），点右侧按钮换一个新的幂等键——那一次会真的执行、真的计费。
                  </div>
                </>
              }
              action={
                <Button size="small" danger onClick={forceRerun}>
                  重新审一遍（会计费）
                </Button>
              }
            />
          )}
          {covered !== submitted && (
            // 结构不变量（契约 AuditBatchResult 的 description 写明）：四桶之和恒等于
            // 去重入参。不平就是「有件数下落不明」——这正是 008§6 要求暴露而非藏起来的。
            <Alert
              style={{ marginBottom: 8 }}
              type="error"
              showIcon
              message={`对账不平：提交 ${submitted} 件，回执只覆盖 ${covered} 件`}
              description="服务端回执与提交件数不符，有产品既没结果也没被列入未开审。请把本窗口截图连同时间报运维。"
            />
          )}
          <Alert
            type={
              result.failed.length
                ? 'error'
                : counts.needs_review || result.skipped.length || result.remaining.length
                  ? 'warning'
                  : 'success'
            }
            message={
              <div>
                <Counter label="通过" n={counts.pass} color="#389e0d" />
                <Counter label="拒绝" n={counts.reject} color="#cf1322" />
                <Counter label="待人工复核" n={counts.needs_review} color="#d46b08" />
                <Counter label="跳过" n={result.skipped.length} color="#595959" />
                <Counter label="失败" n={result.failed.length} color="#d4380d" />
                <Counter label="未开审" n={result.remaining.length} color="#1d39c4" />
              </div>
            }
            description={
              <>
                <div>
                  本批 LLM 花费 ${Number(result.total_llm_cost_usd).toFixed(6)}（提交{' '}
                  {submitted} 件）
                </div>
                {counts.needs_review > 0 && (
                  <div style={{ color: '#d46b08' }}>
                    「待人工复核」不是通过：这 {counts.needs_review} 件判不下来，需要人去看。
                  </div>
                )}
                {result.remaining.length > 0 && (
                  <div>
                    「未开审」是服务端主动收尾（超时预算 / LLM 服务不可用）留下的，
                    没有扣费，点右下角可直接续跑。
                  </div>
                )}
              </>
            }
          />
          <Table<ResultRow>
            style={{ marginTop: 12 }}
            rowKey="key"
            size="small"
            dataSource={toRows(result)}
            pagination={false}
            scroll={{ y: 380 }}
            columns={[
              { title: '产品 ID', dataIndex: 'product_id', width: 90 },
              {
                title: '归档',
                dataIndex: 'kind',
                width: 150,
                render: (k: RowKind) => <Tag color={KIND_TAG[k].color}>{KIND_TAG[k].text}</Tag>,
              },
              {
                // 证据链入口：拿这个号去「审核运行」页可查逐层命中。没有它，回执里
                // 的判定就成了孤证，运维只能拿 product_id 反查。
                title: '运行号',
                dataIndex: 'run_id',
                width: 80,
                render: (v?: number) => v ?? '—',
              },
              {
                title: '判定',
                dataIndex: 'verdict',
                width: 110,
                render: (v?: string) =>
                  v ? <Tag color={VERDICT_LABEL[v]?.color}>{VERDICT_LABEL[v]?.text ?? v}</Tag> : '—',
              },
              {
                title: '否决层',
                dataIndex: 'reject_level',
                width: 80,
                render: (v?: string | null) => v ?? '—',
              },
              {
                title: 'LLM 成本',
                dataIndex: 'llm_cost_usd',
                width: 100,
                render: (v?: number) => (v == null ? '—' : `$${Number(v).toFixed(6)}`),
              },
              {
                title: '产品状态',
                dataIndex: 'product_status',
                width: 110,
                render: (v?: string | null) => (v ? statusText(v) : '—'),
              },
              {
                title: '说明',
                key: 'note',
                render: (_, row) =>
                  row.code ? (
                    // 008§6：不显示裸错误码——中文释义 + 处置建议在前，原始码留小灰字供运维。
                    <div>
                      <div>{codeText(row.code)}</div>
                      {AUDIT_CODE_LABEL[row.code] && (
                        <div style={{ color: '#8c8c8c' }}>{AUDIT_CODE_LABEL[row.code].hint}</div>
                      )}
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {row.code}
                        {row.message ? ` · ${row.message}` : ''}
                      </Typography.Text>
                    </div>
                  ) : row.kind === 'remaining' ? (
                    <Typography.Text type="secondary">
                      本批没轮到（未扣费），可原样续跑
                    </Typography.Text>
                  ) : (
                    '—'
                  ),
              },
            ]}
          />
        </>
      )}
    </Modal>
  )
}
