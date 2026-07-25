import { Checkbox, Input, InputNumber, Space, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type PageOf, type Trademark } from '@/api/client'

export default function TrademarkTab() {
  const [q, setQ] = useState('')
  const [nice, setNice] = useState<number | null>(null)
  const [liveOnly, setLiveOnly] = useState(false)
  const [page, setPage] = useState(1)
  const [data, setData] = useState<PageOf<Trademark> | null>(null)
  const [loading, setLoading] = useState(false)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      const qs = new URLSearchParams({ page: String(page), size: '20' })
      if (q) qs.set('q', q)
      if (nice != null) qs.set('nice', String(nice))
      if (liveOnly) qs.set('live_only', 'true')
      setData(await api.get<PageOf<Trademark>>(`/trademarks?${qs.toString()}`))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [q, nice, liveOnly, page])

  useEffect(() => {
    void load()
  }, [load])

  return (
    <>
      <Space style={{ marginBottom: 16 }} wrap>
        <Input.Search
          allowClear
          placeholder="商标名（trigram 模糊）"
          style={{ width: 240 }}
          onSearch={(v) => {
            setQ(v)
            setPage(1)
          }}
        />
        <InputNumber
          placeholder="Nice 类号"
          min={1}
          max={45}
          value={nice ?? undefined}
          onChange={(v) => {
            setNice(v ?? null)
            setPage(1)
          }}
        />
        <Checkbox
          checked={liveOnly}
          onChange={(e) => {
            setLiveOnly(e.target.checked)
            setPage(1)
          }}
        >
          仅 LIVE
        </Checkbox>
      </Space>
      <Table<Trademark>
        rowKey="serial_no"
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
          { title: '商标', dataIndex: 'mark_text', width: 200 },
          { title: '归一', dataIndex: 'mark_norm', width: 160 },
          {
            title: 'LIVE',
            dataIndex: 'is_live',
            width: 90,
            render: (v: boolean | null) =>
              v ? <Tag color="green">LIVE</Tag> : <Tag>{v === false ? 'DEAD' : '—'}</Tag>,
          },
          { title: '状态码', dataIndex: 'status_code', width: 120 },
          {
            title: 'Nice 类',
            dataIndex: 'nice_classes',
            width: 160,
            render: (ns: number[]) => (ns ?? []).map((n) => <Tag key={n}>{n}</Tag>),
          },
          { title: '权利人', dataIndex: 'owner_name', ellipsis: true },
          { title: '申请日', dataIndex: 'filed_date', width: 120 },
          { title: 'Serial', dataIndex: 'serial_no', width: 120 },
        ]}
      />
    </>
  )
}
