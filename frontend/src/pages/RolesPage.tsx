import { Button, Checkbox, Drawer, Form, Input, Space, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useMemo, useState } from 'react'

import { ApiError, api, type Permission, type Role } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'

export default function RolesPage() {
  const { has } = useAuth()
  const [roles, setRoles] = useState<Role[]>([])
  const [permissions, setPermissions] = useState<Permission[]>([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [editing, setEditing] = useState<Role | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setRoles(await api.get<Role[]>('/roles'))
      setPermissions(await api.get<Permission[]>('/permissions'))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  const byModule = useMemo(() => {
    const groups = new Map<string, Permission[]>()
    for (const p of permissions) {
      const list = groups.get(p.module!) ?? []
      list.push(p)
      groups.set(p.module!, list)
    }
    return [...groups.entries()]
  }, [permissions])

  async function onCreate(values: { name: string; description?: string }) {
    try {
      await api.post('/roles', values)
      message.success('角色已创建')
      setCreateOpen(false)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '创建失败')
    }
  }

  async function onSavePermissions(codes: string[]) {
    if (!editing) return
    try {
      await api.put(`/roles/${editing.id}/permissions`, { permission_codes: codes })
      message.success('权限已更新')
      setEditing(null)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '保存失败')
    }
  }

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        {has('identity.role_write') && (
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建角色
          </Button>
        )}
      </Space>
      <Table<Role>
        rowKey="id"
        loading={loading}
        dataSource={roles}
        pagination={false}
        columns={[
          {
            title: '角色',
            dataIndex: 'name',
            render: (v: string, r) => (
              <>
                {v} {r.team_id == null && <Tag color="blue">模板</Tag>}
              </>
            ),
          },
          {
            title: '权限点',
            render: (_, r) => (
              <span style={{ color: '#888' }}>{(r.permission_codes ?? []).length} 项</span>
            ),
          },
          {
            title: '操作',
            render: (_, r) =>
              has('identity.role_write') &&
              r.team_id != null && (
                <Button size="small" onClick={() => setEditing(r)}>
                  设置权限
                </Button>
              ),
          },
        ]}
      />

      <Drawer
        title="新建角色"
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        destroyOnClose
      >
        <Form layout="vertical" onFinish={onCreate}>
          <Form.Item label="名称" name="name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="描述" name="description">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            创建
          </Button>
        </Form>
      </Drawer>

      <Drawer
        title={`设置权限 — ${editing?.name ?? ''}`}
        open={editing != null}
        onClose={() => setEditing(null)}
        width={520}
        destroyOnClose
      >
        {editing && (
          <PermissionEditor
            byModule={byModule}
            initial={editing.permission_codes ?? []}
            onSave={onSavePermissions}
          />
        )}
      </Drawer>
    </>
  )
}

function PermissionEditor({
  byModule,
  initial,
  onSave,
}: {
  byModule: [string, Permission[]][]
  initial: string[]
  onSave: (codes: string[]) => void
}) {
  const [checked, setChecked] = useState<string[]>(initial)
  return (
    <Space direction="vertical" style={{ width: '100%' }} size="large">
      {byModule.map(([module, perms]) => (
        <div key={module}>
          <div style={{ fontWeight: 600, marginBottom: 8 }}>{module}</div>
          <Checkbox.Group
            value={checked.filter((c) => perms.some((p) => p.code === c))}
            options={perms.map((p) => ({ value: p.code!, label: p.name }))}
            onChange={(vals) => {
              const others = checked.filter((c) => !perms.some((p) => p.code === c))
              setChecked([...others, ...(vals as string[])])
            }}
          />
        </div>
      ))}
      <Button type="primary" block onClick={() => onSave(checked)}>
        保存
      </Button>
    </Space>
  )
}
