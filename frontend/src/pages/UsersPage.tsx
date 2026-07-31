import { Button, Form, Input, InputNumber, Modal, Select, Space, Switch, Table, Tag, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, api, type PageOf, type Role, type User } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'
import { PrincipalDeleteModal } from '@/components/PrincipalDeleteModal'

export default function UsersPage() {
  const { me, has } = useAuth()
  const [data, setData] = useState<PageOf<User> | null>(null)
  const [roles, setRoles] = useState<Role[]>([])
  const [loading, setLoading] = useState(false)
  const [page, setPage] = useState(1)
  const [createOpen, setCreateOpen] = useState(false)
  const [roleTarget, setRoleTarget] = useState<User | null>(null)
  const [toDelete, setToDelete] = useState<User | null>(null)
  const canDelete = has('identity.user_delete')

  const load = useCallback(async () => {
    setLoading(true)
    try {
      setData(await api.get<PageOf<User>>(`/users?page=${page}&size=50`))
      setRoles(await api.get<Role[]>('/roles'))
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '加载失败')
    } finally {
      setLoading(false)
    }
  }, [page])

  useEffect(() => {
    void load()
  }, [load])

  const teamRoles = roles.filter((r) => r.team_id != null)

  async function onCreate(values: {
    username: string
    password: string
    display_name: string
    team_id?: number
  }) {
    try {
      await api.post('/users', values)
      message.success('成员已创建')
      setCreateOpen(false)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '创建失败')
    }
  }

  async function onToggleStatus(user: User, active: boolean) {
    try {
      await api.patch(`/users/${user.id}`, { status: active ? 'active' : 'disabled' })
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '操作失败')
    }
  }

  async function onSetRoles(roleIds: number[]) {
    if (!roleTarget) return
    try {
      await api.put(`/users/${roleTarget.id}/roles`, { role_ids: roleIds })
      message.success('角色已更新')
      setRoleTarget(null)
      void load()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '操作失败')
    }
  }

  return (
    <>
      <Space style={{ marginBottom: 16 }}>
        {has('identity.user_write') && (
          <Button type="primary" onClick={() => setCreateOpen(true)}>
            新建成员
          </Button>
        )}
      </Space>
      <Table<User>
        rowKey="id"
        loading={loading}
        dataSource={data?.items ?? []}
        pagination={{
          current: page,
          pageSize: 50,
          total: data?.total ?? 0,
          onChange: setPage,
          showTotal: (t) => `共 ${t} 人`,
        }}
        columns={[
          { title: '用户名', dataIndex: 'username' },
          { title: '姓名', dataIndex: 'display_name' },
          {
            title: '角色',
            render: (_, u) => (
              <>
                {u.is_super && <Tag color="gold">超管</Tag>}
                {(u.roles ?? []).map((r) => (
                  <Tag key={r.id}>{r.name}</Tag>
                ))}
              </>
            ),
          },
          {
            title: '状态',
            dataIndex: 'status',
            render: (_, u) =>
              has('identity.user_write') && !u.is_super ? (
                <Switch
                  checked={u.status === 'active'}
                  checkedChildren="启用"
                  unCheckedChildren="停用"
                  onChange={(v) => void onToggleStatus(u, v)}
                />
              ) : (
                <Tag color={u.status === 'active' ? 'green' : 'red'}>{u.status}</Tag>
              ),
          },
          {
            title: '最近登录',
            dataIndex: 'last_login_at',
            render: (v: string | null) => (v ? new Date(v).toLocaleString('zh-CN') : '—'),
          },
          {
            title: '操作',
            render: (_, u) =>
              !u.is_super && (
                <Space>
                  {has('identity.user_write') && (
                    <Button size="small" onClick={() => setRoleTarget(u)}>
                      设置角色
                    </Button>
                  )}
                  {/* 不能删自己（服务端 USER_DELETE_SELF 同款守卫，入口即不给） */}
                  {canDelete && u.id !== me?.user.id && (
                    <Button size="small" danger onClick={() => setToDelete(u)}>
                      删除
                    </Button>
                  )}
                </Space>
              ),
          },
        ]}
      />

      <Modal
        title="新建成员"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnClose
      >
        <Form layout="vertical" onFinish={onCreate}>
          <Form.Item
            label="用户名"
            name="username"
            rules={[{ required: true, pattern: /^[a-zA-Z0-9_.-]{2,64}$/, message: '2-64位字母数字._-' }]}
          >
            <Input />
          </Form.Item>
          <Form.Item
            label="初始密码"
            name="password"
            rules={[{ required: true, min: 8, message: '至少 8 位' }]}
          >
            <Input.Password />
          </Form.Item>
          <Form.Item label="姓名" name="display_name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          {me?.user.is_super && (
            <Form.Item label="团队 ID（超管必填）" name="team_id" rules={[{ required: true }]}>
              <InputNumber style={{ width: '100%' }} />
            </Form.Item>
          )}
          <Button type="primary" htmlType="submit" block>
            创建
          </Button>
        </Form>
      </Modal>

      <PrincipalDeleteModal
        open={!!toDelete}
        title={`删除成员：${toDelete?.display_name ?? ''}（${toDelete?.username ?? ''}）`}
        endpoint={`/users/${toDelete?.id ?? 0}`}
        consequences={
          <>
            账号会被<b>物理删除</b>，登录立即失效，<b>没有回收站</b>。
            <br />
            若该成员<b>操作过系统</b>，会留下墓碑——其审计留痕仍在，操作人显示
            「{toDelete?.display_name ?? ''}<b>（已删除）</b>」；从未操作过则直删无墓碑。
            <br />
            其名下的采购方绑定会被<b>解除</b>（采购方本身保留）；审计与订单记录
            <b>一行都不会删</b>。
            <br />
            只是暂时停用的话，请用「状态」开关而不是删除。
          </>
        }
        successOf={(r) =>
          r.tombstoned
            ? `已删除 ${r.username ?? ''}（有操作留痕，已留墓碑，审计里显示「已删除」）`
            : `已删除 ${r.username ?? ''}（从未操作过，未留墓碑）`
        }
        onClose={() => setToDelete(null)}
        onDeleted={() => void load()}
      />

      <Modal
        title={`设置角色 — ${roleTarget?.display_name ?? ''}`}
        open={roleTarget != null}
        onCancel={() => setRoleTarget(null)}
        footer={null}
        destroyOnClose
      >
        <RoleSelect
          teamRoles={teamRoles}
          initial={(roleTarget?.roles ?? []).map((r) => r.id!)}
          onSubmit={onSetRoles}
        />
      </Modal>
    </>
  )
}

function RoleSelect({
  teamRoles,
  initial,
  onSubmit,
}: {
  teamRoles: Role[]
  initial: number[]
  onSubmit: (ids: number[]) => void
}) {
  const [value, setValue] = useState<number[]>(initial)
  return (
    <Space direction="vertical" style={{ width: '100%' }}>
      <Select
        mode="multiple"
        style={{ width: '100%' }}
        value={value}
        onChange={setValue}
        options={teamRoles.map((r) => ({ value: r.id!, label: r.name }))}
        placeholder="选择角色（仅本团队角色）"
      />
      <Button type="primary" block onClick={() => onSubmit(value)}>
        保存
      </Button>
    </Space>
  )
}
