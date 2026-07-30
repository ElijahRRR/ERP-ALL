import { Alert, Button, Form, Input, Modal, message } from 'antd'
import { useState, type ReactNode } from 'react'

import { ApiError, api, type PrincipalDeleteResult } from '@/api/client'

/** 主体删除二次确认（R2-14 14b，§7.1「配套要求」）——user/role/purchaser 三页共用。
 *
 * 不可逆操作的确认必须说真话（14a 的口径）：①/②级要到服务端才判得出，
 * `consequences` 由调用方按主体类写清两种后果；回执按 tombstoned 如实播报。 */
export function PrincipalDeleteModal({
  open,
  title,
  endpoint,
  consequences,
  successOf,
  onClose,
  onDeleted,
}: {
  open: boolean
  title: string
  /** 例：`/users/3`——reason 由本组件追加 */
  endpoint: string
  consequences: ReactNode
  successOf: (r: PrincipalDeleteResult) => string
  onClose: () => void
  onDeleted: () => void
}) {
  const [deleting, setDeleting] = useState(false)

  async function onFinish(values: { reason: string }) {
    setDeleting(true)
    try {
      const r = await api.del<PrincipalDeleteResult>(
        `${endpoint}?reason=${encodeURIComponent(values.reason)}`,
      )
      message.success(successOf(r))
      onClose()
      onDeleted()
    } catch (e) {
      message.error(e instanceof ApiError ? e.message : '删除失败')
    } finally {
      setDeleting(false)
    }
  }

  return (
    <Modal title={title} open={open} onCancel={onClose} footer={null} destroyOnHidden>
      <Alert
        type="warning"
        showIcon
        style={{ marginBottom: 16 }}
        message="删除不可撤销"
        description={consequences}
      />
      <Form layout="vertical" onFinish={onFinish}>
        <Form.Item
          name="reason"
          label="删除原因"
          rules={[{ required: true, message: '请填写删除原因（会写入墓碑与审计留痕）' }]}
          extra="会写入墓碑与 audit_log，用于日后追溯是谁、为什么删的。"
        >
          <Input.TextArea rows={2} maxLength={200} showCount />
        </Form.Item>
        <Button type="primary" danger htmlType="submit" block loading={deleting}>
          确认删除
        </Button>
      </Form>
    </Modal>
  )
}
