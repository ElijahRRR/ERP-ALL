import { Button, Form, Input, Modal, Select, Space, message } from 'antd'
import { useCallback, useEffect, useState } from 'react'

import { ApiError, actTeamStore, api } from '@/api/client'

interface Team {
  id: number
  name: string
  status: string
}

/** 超管专用：选择"当前作用团队"（业务操作以该团队身份执行）+ 快捷建团队。 */
export default function TeamSwitcher() {
  const [teams, setTeams] = useState<Team[]>([])
  const [createOpen, setCreateOpen] = useState(false)
  const current = actTeamStore.get()

  const load = useCallback(async () => {
    try {
      const r = await api.get<{ items: Team[] }>(`/teams?page=1&size=100`)
      setTeams(r.items.filter((t) => t.status === 'active'))
    } catch {
      /* 无权限或加载失败时下拉为空 */
    }
  }, [])

  useEffect(() => {
    void load()
  }, [load])

  function switchTo(teamId: number | null) {
    actTeamStore.set(teamId)
    window.location.reload() // 全局身份语境切换，整页刷新最稳
  }

  async function onCreate(values: { name: string }) {
    try {
      const t = await api.post<Team>(`/teams`, values)
      message.success(`团队「${t.name}」已创建（含 7 个模板角色）`)
      setCreateOpen(false)
      switchTo(t.id)
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '创建失败')
    }
  }

  return (
    <>
      <Space size={8}>
        <span style={{ color: '#999' }}>作用团队</span>
        <Select
          size="small"
          style={{ minWidth: 160 }}
          placeholder="未选择（仅管理功能）"
          value={current ? Number(current) : undefined}
          allowClear
          onChange={(v) => switchTo(v ?? null)}
          options={teams.map((t) => ({ value: t.id, label: t.name }))}
          data-testid="team-switcher"
        />
        <Button size="small" onClick={() => setCreateOpen(true)}>
          新建团队
        </Button>
      </Space>
      <Modal
        title="新建团队"
        open={createOpen}
        onCancel={() => setCreateOpen(false)}
        footer={null}
        destroyOnHidden
      >
        <Form layout="vertical" onFinish={onCreate}>
          <Form.Item
            name="name"
            label="团队名称"
            rules={[{ required: true, min: 2, max: 32 }]}
          >
            <Input placeholder="如：一部" />
          </Form.Item>
          <Button type="primary" htmlType="submit" block>
            创建并切换
          </Button>
        </Form>
      </Modal>
    </>
  )
}
