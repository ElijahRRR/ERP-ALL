import { Button, Form, Input, InputNumber, Modal, Select, Space, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type Schemas } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'
import { PrincipalDeleteModal } from '@/components/PrincipalDeleteModal'

type Purchaser = Schemas['Purchaser']

const KIND: Record<string, { color: string; label: string }> = {
  internal: { color: 'blue', label: '内部' },
  external: { color: 'purple', label: '外部' },
}

export default function PurchasersPage() {
  const { has } = useAuth()
  const [items, setItems] = useState<Purchaser[]>([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<Purchaser | null>(null)
  const [toDelete, setToDelete] = useState<Purchaser | null>(null)
  const canAdmin = has('procurement.admin')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setItems(await api.get<Purchaser[]>('/purchasers'))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  async function onCreate(values: Record<string, unknown>) {
    try {
      await api.post('/purchasers', values)
      message.success('采购方已创建')
      setCreateOpen(false)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '创建失败')
    }
  }

  async function onEdit(values: Record<string, unknown>) {
    if (!editing) return
    try {
      await api.patch(`/purchasers/${editing.id}`, values)
      message.success('采购方已更新')
      setEditing(null)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '更新失败')
    }
  }

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        {canAdmin && (
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建采购方
          </Button>
        )}
        <Button onClick={() => void load()}>刷新</Button>
      </Space>
      <Table<Purchaser>
        rowKey={(p) => p.id ?? 0}
        loading={loading}
        dataSource={items}
        pagination={false}
        columns={[
          { title: '名称', dataIndex: 'name' },
          {
            title: '类型',
            dataIndex: 'purchaser_kind',
            width: 100,
            render: (k?: string) => {
              const m = k ? KIND[k] : undefined
              return m ? <Tag color={m.color}>{m.label}</Tag> : (k ?? '—')
            },
          },
          {
            title: '绑定成员',
            dataIndex: 'user_id',
            width: 110,
            render: (v?: number | null) => (v != null ? `#${v}` : '—'),
          },
          { title: '汇率', dataIndex: 'exchange_rate', width: 100 },
          { title: '结算币', dataIndex: 'settle_currency', width: 90 },
          {
            title: '状态',
            dataIndex: 'status',
            width: 100,
            render: (s?: string) =>
              s === 'active' ? (
                <Tag color="green">启用</Tag>
              ) : s === 'disabled' ? (
                <Tag>停用</Tag>
              ) : (
                (s ?? '—')
              ),
          },
          {
            title: '操作',
            key: 'op',
            width: 140,
            render: (_, p) => (
              <Space>
                {canAdmin && (
                  <Button size="small" onClick={() => setEditing(p)}>
                    编辑
                  </Button>
                )}
                {has('procurement.purchaser_delete') && (
                  <Button size="small" danger onClick={() => setToDelete(p)}>
                    删除
                  </Button>
                )}
              </Space>
            ),
          },
        ]}
      />

      <PrincipalDeleteModal
        open={!!toDelete}
        title={`删除采购方：${toDelete?.name ?? ''}`}
        endpoint={`/purchasers/${toDelete?.id ?? 0}`}
        consequences={
          <>
            采购方会被<b>物理删除</b>，<b>没有回收站</b>。
            <br />
            其名下<b>未领单</b>（汇率尚未锁定）的执行单会<b>退回待分配池</b>并解除绑定
            ——不退回会变成永远锁不上汇率的孤单。
            <br />
            <b>仍有执行中的单</b>（已领单未完结）<b>删不掉</b>——请等其回填完结再删；
            若含 exception 单：其人工处置通道尚未建成（工单 FX-0730B），落地前暂无法删除。
            <br />
            已完结的执行单<b>一行不删</b>（汇率已锁），其采购方显示
            「{toDelete?.name ?? ''}<b>（已删除）</b>」；从未接过单则直删无墓碑。
          </>
        }
        successOf={(r) =>
          r.tombstoned
            ? `已删除 ${r.name ?? ''}（接过单，已留墓碑，历史单据仍可读）`
            : `已删除 ${r.name ?? ''}（从未接单，未留墓碑）`
        }
        onClose={() => setToDelete(null)}
        onDeleted={() => void load()}
      />

      <Modal
        title="新建采购方"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnHidden
      >
        {/* external 本期仅建档，不在此采集门户账号（后端拒绝 portal 字段） */}
        <Form
          layout="vertical"
          onFinish={onCreate}
          initialValues={{ purchaser_kind: 'internal', exchange_rate: 1, status: 'active' }}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true }]}>
            <Input placeholder="采购方名称" />
          </Form.Item>
          <Form.Item label="类型" name="purchaser_kind" rules={[{ required: true }]}>
            <Select
              options={[
                { value: 'internal', label: 'internal 内部成员' },
                { value: 'external', label: 'external 外部采购方（本期仅建档，门户账号后续开通）' },
              ]}
            />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(p, c) => p.purchaser_kind !== c.purchaser_kind}>
            {({ getFieldValue }) =>
              getFieldValue('purchaser_kind') === 'internal' ? (
                <Form.Item
                  label="内部成员 user_id"
                  name="user_id"
                  rules={[{ required: true, message: 'internal 类型必须绑定成员 user_id' }]}
                >
                  <InputNumber min={1} style={{ width: '100%' }} placeholder="成员数字 ID" />
                </Form.Item>
              ) : null
            }
          </Form.Item>
          <Form.Item label="汇率" name="exchange_rate" rules={[{ required: true }]}>
            <InputNumber min={0} step={0.01} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="状态" name="status">
            <Select
              options={[
                { value: 'active', label: '启用' },
                { value: 'disabled', label: '停用' },
              ]}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            创建
          </Button>
        </Form>
      </Modal>

      <Modal
        title={`编辑采购方：${editing?.name ?? ''}`}
        open={!!editing}
        onCancel={() => setEditing(null)}
        footer={null}
        destroyOnHidden
      >
        <Form
          layout="vertical"
          onFinish={onEdit}
          initialValues={{
            name: editing?.name,
            exchange_rate: editing?.exchange_rate,
            status: editing?.status,
          }}
        >
          <Form.Item label="名称" name="name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="汇率（改汇率只影响未锁单）" name="exchange_rate" rules={[{ required: true }]}>
            <InputNumber min={0} step={0.01} style={{ width: '100%' }} />
          </Form.Item>
          <Form.Item label="状态" name="status">
            <Select
              options={[
                { value: 'active', label: '启用' },
                { value: 'disabled', label: '停用' },
              ]}
            />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            保存
          </Button>
        </Form>
      </Modal>
    </>
  )
}
