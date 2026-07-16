import { Alert, Button, Form, Input, Modal, Progress, Select, Space, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type PageOf, type Schemas } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'

type WorkerNode = Schemas['WorkerNode']

interface ScrapeJob {
  id: number
  source: string
  job_kind: string
  status: string
  total_tasks: number
  done_tasks: number
  failed_tasks: number
  created_at: string
  finished_at: string | null
}

const STATUS_COLOR: Record<string, string> = {
  pending: 'default',
  running: 'processing',
  done: 'green',
  partial: 'orange',
  failed: 'red',
  cancelled: 'default',
}

export default function ScrapeJobsPage() {
  const { has } = useAuth()
  const [data, setData] = useState<PageOf<ScrapeJob> | null>(null)
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [createOpen, setCreateOpen] = useState(false)
  // null = 未获取到节点数据（未加载 / 无 scrape.node_admin 权限 / 请求失败）→ 不渲染健康横幅
  const [nodes, setNodes] = useState<WorkerNode[] | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    // 采集节点健康：与列表并行拉取；403 等任何错误静默隐藏横幅，不打扰无权限用户
    void api.get<WorkerNode[]>('/worker-nodes').then(setNodes, () => setNodes(null))
    try {
      setData(await api.get<PageOf<ScrapeJob>>(`/scrape-jobs?page=${page}&size=20`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page])

  useEffect(() => {
    void load()
  }, [load])

  async function onCreate(values: { source: string; targets: string }) {
    const targets = values.targets
      .split(/[\s,，]+/)
      .map((s) => s.trim())
      .filter(Boolean)
    try {
      await api.post('/scrape-jobs', {
        source: values.source,
        job_kind: 'product_detail',
        input: { targets },
      })
      message.success(`采集作业已创建（${targets.length} 个目标）`)
      setCreateOpen(false)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '创建失败')
    }
  }

  async function onCancel(id: number) {
    try {
      await api.post(`/scrape-jobs/${id}/cancel`, {})
      message.success('作业已取消')
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '取消失败')
    }
  }

  const onlineCount = (nodes ?? []).filter((n) => n.status === 'online').length
  const hasActiveJobs =
    data?.items.some((j) => ['pending', 'running'].includes(j.status)) ?? false

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        {has('scrape.job_write') && (
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建采集
          </Button>
        )}
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      {nodes != null && (
        <Alert
          style={{ marginBottom: 16, padding: '4px 12px' }}
          showIcon
          type={onlineCount > 0 ? 'success' : hasActiveJobs ? 'error' : 'warning'}
          message={
            onlineCount > 0
              ? `采集节点在线 ${onlineCount} 个`
              : hasActiveJobs
                ? '没有在线采集节点——采集作业不会推进。检查 scraper 容器（enroll token / 代理配置）'
                : '没有在线采集节点'
          }
        />
      )}
      <Table<ScrapeJob>
        rowKey="id"
        loading={loading}
        dataSource={data?.items}
        pagination={{
          current: page,
          total: data?.total,
          pageSize: 20,
          onChange: setPage,
        }}
        columns={[
          { title: 'ID', dataIndex: 'id', width: 70 },
          { title: '来源', dataIndex: 'source', width: 90 },
          { title: '类型', dataIndex: 'job_kind', width: 120 },
          {
            title: '状态',
            dataIndex: 'status',
            width: 100,
            render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag>,
          },
          {
            title: '进度',
            key: 'progress',
            render: (_, r) => (
              <Space>
                <Progress
                  style={{ width: 160 }}
                  size="small"
                  percent={Math.round(
                    ((r.done_tasks + r.failed_tasks) / Math.max(r.total_tasks, 1)) * 100,
                  )}
                  status={r.failed_tasks ? 'exception' : undefined}
                />
                <span>
                  {r.done_tasks}成 / {r.failed_tasks}败 / 共{r.total_tasks}
                </span>
              </Space>
            ),
          },
          { title: '创建时间', dataIndex: 'created_at', width: 180 },
          {
            title: '操作',
            key: 'op',
            width: 100,
            render: (_, r) =>
              has('scrape.job_write') &&
              ['pending', 'running'].includes(r.status) && (
                <Button size="small" danger onClick={() => void onCancel(r.id)}>
                  取消
                </Button>
              ),
          },
        ]}
      />
      <Modal
        title="新建采集作业"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <Form layout="vertical" onFinish={onCreate} initialValues={{ source: 'amazon' }}>
          <Form.Item name="source" label="来源" rules={[{ required: true }]}>
            <Select options={[{ value: 'amazon', label: 'Amazon' }]} />
          </Form.Item>
          <Form.Item
            name="targets"
            label="ASIN 列表（空格/逗号/换行分隔）"
            rules={[{ required: true, message: '请输入至少一个 ASIN' }]}
          >
            <Input.TextArea rows={4} placeholder="B0XXXXXXXX B0YYYYYYYY" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            创建
          </Button>
        </Form>
      </Modal>
    </>
  )
}
