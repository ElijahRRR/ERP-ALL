import { Input, Select, Space, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type PageOf, type TroCase } from '@/api/client'

const STATUS_COLOR: Record<string, string> = {
  active: 'red',
  dismissed: 'default',
  settled: 'blue',
}

export default function TroTab() {
  const [q, setQ] = useState('')
  const [status, setStatus] = useState<string | undefined>(undefined)
  const [page, setPage] = useState(1)
  const [data, setData] = useState<PageOf<TroCase> | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ page: String(page), size: '20' })
      if (q) qs.set('q', q)
      if (status) qs.set('status', status)
      setData(await api.get<PageOf<TroCase>>(`/tro-cases?${qs.toString()}`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [q, status, page])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <>
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          allowClear
          placeholder="案号 / 原告 / 品牌词"
          style={{ width: 260 }}
          onSearch={(v) => {
            setQ(v)
            setPage(1)
          }}
        />
        <Select
          allowClear
          placeholder="状态"
          style={{ width: 140 }}
          value={status}
          onChange={(v) => {
            setStatus(v)
            setPage(1)
          }}
          options={[
            { value: 'active', label: 'active 生效' },
            { value: 'dismissed', label: 'dismissed 驳回' },
            { value: 'settled', label: 'settled 和解' },
          ]}
        />
      </Space>
      <Table<TroCase>
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
          { title: '案号', dataIndex: 'case_no', width: 160 },
          { title: '法院', dataIndex: 'court', width: 130 },
          { title: '原告', dataIndex: 'plaintiff', ellipsis: true },
          { title: '律所', dataIndex: 'law_firm', ellipsis: true },
          {
            title: '品牌词',
            dataIndex: 'brand_terms',
            render: (ts: string[]) => (ts ?? []).map((t) => <Tag key={t}>{t}</Tag>),
          },
          {
            title: '状态',
            dataIndex: 'status',
            width: 110,
            render: (s: string) => <Tag color={STATUS_COLOR[s]}>{s}</Tag>,
          },
          { title: '立案日', dataIndex: 'filed_date', width: 120 },
        ]}
      />
    </>
  )
}
