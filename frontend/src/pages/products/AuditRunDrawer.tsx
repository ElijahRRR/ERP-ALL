import { Descriptions, Drawer, Table, Tag } from 'antd'

import { VERDICT_LABEL } from '@/pages/products/labels'
import type { AuditHit, AuditRunDetail } from '@/pages/products/types'

/** 审核运行详情（展示组件，不发请求——008§4：数据由页面容器取好后经 props 传入）。 */
export default function AuditRunDrawer({
  run,
  onClose,
}: {
  run: AuditRunDetail | null
  onClose: () => void
}) {
  const verdict = run?.verdict ? VERDICT_LABEL[run.verdict] : undefined
  return (
    <Drawer title={`审核运行 #${run?.id ?? ''}`} open={!!run} onClose={onClose} width={560}>
      {run && (
        <>
          <Descriptions column={2} size="small" bordered style={{ marginBottom: 16 }}>
            <Descriptions.Item label="判定">
              <Tag color={verdict?.color ?? 'default'}>{verdict?.text ?? run.verdict ?? '—'}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="否决层">{run.reject_level ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="LLM 成本">
              ${Number(run.llm_cost_usd).toFixed(6)}
            </Descriptions.Item>
            <Descriptions.Item label="缓存命中率">{run.cache_hit_rate ?? '—'}</Descriptions.Item>
          </Descriptions>
          <Table<AuditHit>
            rowKey={(h) => `${h.level}-${h.rule_code}`}
            size="small"
            dataSource={run.hits}
            pagination={false}
            columns={[
              { title: '层', dataIndex: 'level', width: 60 },
              { title: '规则', dataIndex: 'rule_code' },
              {
                title: '硬拒',
                dataIndex: 'is_hard',
                width: 70,
                render: (v: boolean) => (v ? <Tag color="red">是</Tag> : <Tag>否</Tag>),
              },
              {
                title: '证据',
                dataIndex: 'evidence',
                ellipsis: true,
                render: (e: Record<string, unknown>) => JSON.stringify(e),
              },
            ]}
          />
        </>
      )}
    </Drawer>
  )
}
