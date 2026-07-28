import { Descriptions, Divider, Drawer, Empty, Image, List, Space, Spin, Tag } from 'antd'

import {
  ATTR_LABELS,
  PRICE_LABELS,
  STATUS_COLOR,
  attrText,
  statusText,
} from '@/pages/products/labels'
import type { ProductDetail } from '@/pages/products/types'

/** 产品详情抽屉（展示组件，不发请求——008§4）。 */
export default function ProductDetailDrawer({
  detail,
  loading,
  onClose,
}: {
  detail: ProductDetail | null
  loading: boolean
  onClose: () => void
}) {
  const rawBullets = detail?.attrs?.bullets
  const bullets = Array.isArray(rawBullets) ? (rawBullets as string[]) : []
  const otherAttrs = Object.entries(detail?.attrs ?? {}).filter(([k]) => k !== 'bullets')

  return (
    <Drawer
      title={`产品详情 ${detail?.master_sku ?? ''}`}
      open={loading || !!detail}
      onClose={onClose}
      width={640}
    >
      {loading && <Spin />}
      {detail && (
        <>
          {detail.images && detail.images.length > 0 ? (
            <Image.PreviewGroup>
              <Space wrap size={8} style={{ marginBottom: 16 }}>
                {detail.images.map((src) => (
                  <Image
                    key={src}
                    src={src}
                    width={96}
                    height={96}
                    style={{ objectFit: 'contain' }}
                  />
                ))}
              </Space>
            </Image.PreviewGroup>
          ) : (
            <Empty description="无图片" style={{ marginBottom: 16 }} />
          )}

          <Descriptions column={1} size="small" bordered>
            <Descriptions.Item label="标题">{detail.title}</Descriptions.Item>
            <Descriptions.Item label="品牌">{detail.brand ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="类目">{detail.category_path ?? '—'}</Descriptions.Item>
            <Descriptions.Item label="ASIN">
              {detail.source_channel === 'amazon' ? (
                <a
                  href={`https://www.amazon.com/dp/${detail.source_ref}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  {detail.source_ref}
                </a>
              ) : (
                detail.source_ref
              )}
            </Descriptions.Item>
            <Descriptions.Item label="状态">
              <Tag color={STATUS_COLOR[detail.status]}>{statusText(detail.status)}</Tag>
            </Descriptions.Item>
          </Descriptions>

          {detail.price_snapshot && Object.keys(detail.price_snapshot).length > 0 && (
            <>
              <Divider orientation="left">价格</Divider>
              <Descriptions column={2} size="small" bordered>
                {Object.entries(detail.price_snapshot).map(([k, v]) => (
                  <Descriptions.Item key={k} label={PRICE_LABELS[k] ?? k}>
                    {attrText(v)}
                  </Descriptions.Item>
                ))}
              </Descriptions>
            </>
          )}

          {bullets.length > 0 && (
            <>
              <Divider orientation="left">五点描述</Divider>
              <List size="small" dataSource={bullets} renderItem={(b) => <List.Item>{b}</List.Item>} />
            </>
          )}

          {otherAttrs.length > 0 && (
            <>
              <Divider orientation="left">其他采集字段</Divider>
              <Descriptions column={1} size="small" bordered>
                {otherAttrs.map(([k, v]) => (
                  <Descriptions.Item key={k} label={ATTR_LABELS[k] ?? k}>
                    {attrText(v)}
                  </Descriptions.Item>
                ))}
              </Descriptions>
            </>
          )}
        </>
      )}
    </Drawer>
  )
}
