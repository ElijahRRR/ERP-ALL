import { Card, Col, Row, Typography } from 'antd'

import { useAuth } from '@/auth/AuthContext'

// R2 起替换为真实工作台（配额/告警/任务概览）；R1 为欢迎页占位
export default function HomePage() {
  const { me } = useAuth()
  return (
    <Card>
      <Typography.Title level={4}>
        你好，{me?.user.display_name}
      </Typography.Title>
      <Typography.Paragraph type="secondary">
        ERP-ALL 行走骨架（R1）。当前可用模块见左侧菜单——菜单按你的权限点动态渲染。
      </Typography.Paragraph>
      <Row gutter={16}>
        <Col span={8}>
          <Card size="small" title="下一步">
            采集 → 审核 → 上架最小闭环（R1-09 ~ R1-11）交付后，此处将成为运营工作台。
          </Card>
        </Col>
      </Row>
    </Card>
  )
}
