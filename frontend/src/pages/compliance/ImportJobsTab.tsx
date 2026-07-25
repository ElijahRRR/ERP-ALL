import { Alert, Button, Drawer, Space, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type ImportErrorReport, type ImportJob, type PageOf } from '@/api/client'

const STATUS_COLOR: Record<string, string> = {
  done: 'green',
  running: 'processing',
  validating: 'processing',
  pending: 'default',
  failed: 'red',
  cancelled: 'default',
}

interface ErrorRow {
  line?: number
  reason?: string
}

export default function ImportJobsTab() {
  const [page, setPage] = useState(1)
  const [data, setData] = useState<PageOf<ImportJob> | null>(null)
  const [loading, setLoading] = useState(false)
  const [report, setReport] = useState<ImportErrorReport | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await api.get<PageOf<ImportJob>>(`/import-jobs?page=${page}&size=20`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page])

  useEffect(() => {
    void load()
  }, [load])

  async function showReport(jobId: number) {
    try {
      setReport(await api.get<ImportErrorReport>(`/import-jobs/${jobId}/error-report`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    }
  }

  return (
    <>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message="导入执行走部署机 CLI（大文件不经 HTTP、全局数据超管事务）——本页只看进度与报错，供给链自动化由 USPTO/TRO 日度链 + 黑名单导入驱动。"
      />
      <Space style={{ marginBottom: 16 }}>
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      <Table<ImportJob>
        rowKey="id"
        loading={loading}
        dataSource={data?.items}
        pagination={{
          current: page,
          pageSize: 20,
          total: data?.total ?? 0,
          showSizeChanger: false,
          onChange: setPage,
        }}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 70 },
          { title: '域', dataIndex: 'domain', width: 140 },
          { title: '源文件', dataIndex: 'source_name', ellipsis: true },
          {
            title: '状态',
            dataIndex: 'status',
            width: 110,
            render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag>,
          },
          { title: '总行', dataIndex: 'total_rows', width: 80 },
          { title: '成功', dataIndex: 'ok_rows', width: 80 },
          {
            title: '报错',
            dataIndex: 'err_rows',
            width: 80,
            render: (n: number) => (n > 0 ? <Tag color="red">{n}</Tag> : n),
          },
          { title: '完成时间', dataIndex: 'finished_at', width: 180 },
          {
            title: '操作',
            key: 'op',
            width: 110,
            render: (_, r) =>
              (r.err_rows ?? 0) > 0 && r.id != null ? (
                <Button size="small" danger onClick={() => void showReport(r.id as number)}>
                  报错报告
                </Button>
              ) : null,
          },
        ]}
      />
      <Drawer
        title={`导入报错报告 · 作业 #${report?.job_id ?? ''}`}
        open={!!report}
        onClose={() => setReport(null)}
        width={620}
      >
        {report && (
          <>
            <Space style={{ marginBottom: 12 }} wrap>
              <Tag>域 {report.domain}</Tag>
              <Tag color={STATUS_COLOR[report.status ?? ''] ?? 'default'}>{report.status}</Tag>
              <Tag color="red">报错 {report.err_rows} 行</Tag>
            </Space>
            <div style={{ fontWeight: 600, margin: '8px 0' }}>逐块核对</div>
            <Table<Record<string, number>>
              rowKey={(_, i) => i ?? 0}
              size="small"
              pagination={false}
              dataSource={report.chunks as Record<string, number>[]}
              style={{ marginBottom: 20 }}
              columns={[
                { title: '块', dataIndex: 'chunk', width: 80 },
                { title: '预期', dataIndex: 'expected', width: 100 },
                { title: '载入', dataIndex: 'loaded', width: 100 },
              ]}
            />
            <div style={{ fontWeight: 600, margin: '8px 0' }}>报错样本（≤50）</div>
            <Table<ErrorRow>
              rowKey={(_, i) => i ?? 0}
              size="small"
              pagination={false}
              dataSource={report.errors as ErrorRow[]}
              columns={[
                { title: '行', dataIndex: 'line', width: 80 },
                { title: '原因', dataIndex: 'reason' },
              ]}
            />
          </>
        )}
      </Drawer>
    </>
  )
}
