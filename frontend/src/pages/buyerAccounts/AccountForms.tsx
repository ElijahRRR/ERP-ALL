// 买家账号池的两个表单（008 §1：页面超 ~400 行拆子组件进同名子目录）。
//
// 拆成**两个**表单而不是一个带条件分支的，是因为「建号/改号」与「认领」的形状本就不同：
// 认领**不能改 customerId**（那是系统发现的事实，改它等于认领了另一个号），而账号名与
// 站点是认领这一步必须补上的（库层 `ck_buyer_account_claimed` 兜底，缺则 422）。
// 一个表单里塞两套必填规则，迟早会漏掉某一侧。

import { Alert, Button, Form, Input, InputNumber, Select } from 'antd'

import type { BuyerAccount } from '@/api/client'

import { ACCOUNT_STATUS, SITE_LABEL } from './labels'

const SITE_OPTIONS = Object.entries(SITE_LABEL).map(([value, label]) => ({ value, label }))
const STATUS_OPTIONS = Object.entries(ACCOUNT_STATUS).map(([value, m]) => ({
  value,
  label: m.label,
}))

// 手工预建的定位（身份模型更正，Owner 2026-07-30，图纸 07:288-340）。
//
// ⚠️ 这段话此前写的是「点插件面板『提取 customerId』按钮 → 复制粘贴到此处」。**那是错的**：
// 插件源码实测**零 per-install 配置**，customerId 是它每次从亚马逊页面现抓并随请求带上的，
// 那个「提取」按钮**纯展示**（给人看一眼，不写进任何配置、也不是建号的必经通道）。
// 照旧文案办事就会撞上死循环：装插件前先要知道 customerId，而 customerId 只有装了插件
// 才看得到。**主路径是首见自动登记**——让插件在那台浏览器上跑一次拉取即可。
const CUSTOMER_ID_HELP =
  '可选捷径：若手上已经有这个号的 customerId（例如从插件面板上看到的），预建能省一轮往返；' +
  '不知道就别在这里编——让插件在那台浏览器上跑一次拉取，系统会自动登记一条待认领行'

export interface FormValues {
  label?: string
  site?: string
  external_customer_id?: string
  status?: string
  daily_cap?: number | null
  note?: string
}

/** 建号（可选预建捷径）与改号共用。三个必填与后端 `BUYER_ACCOUNT_FIELDS_REQUIRED` 同源。 */
export function AccountForm({
  initial,
  submitText,
  onFinish,
}: {
  initial?: BuyerAccount
  submitText: string
  onFinish: (v: FormValues) => void | Promise<void>
}) {
  return (
    <Form<FormValues>
      layout="vertical"
      onFinish={onFinish}
      initialValues={{
        label: initial?.label ?? undefined,
        site: initial?.site ?? 'amazon_com',
        external_customer_id: initial?.external_customer_id,
        status: initial?.status ?? 'active',
        daily_cap: initial?.daily_cap ?? undefined,
        note: initial?.note ?? undefined,
      }}
    >
      <Form.Item
        label="账号名"
        name="label"
        extra="语义 = 对应哪一个指纹浏览器——运营靠它把系统里的号和桌面上开着的窗口对上"
        rules={[{ required: true, message: '请填写账号名' }]}
      >
        <Input placeholder="如：指纹浏览器 07 号窗口" maxLength={100} />
      </Form.Item>
      <Form.Item label="站点" name="site" rules={[{ required: true }]}>
        <Select options={SITE_OPTIONS} />
      </Form.Item>
      <Form.Item
        label="customerId"
        name="external_customer_id"
        extra={CUSTOMER_ID_HELP}
        rules={[{ required: true, message: '请填写 customerId' }]}
      >
        <Input placeholder="A1NS3HIW4IC6P7" maxLength={64} />
      </Form.Item>
      <Form.Item
        label="状态"
        name="status"
        extra="非「启用」一律不再派新任务；但已派出去的单，其回填/异常/物流回报照常收（钱可能已经花出去了）"
      >
        {/* 「待认领」是系统发现态、不是运营可选态（后端任何态转回它一律 409），
            故这里也不给选——留一个点得动却必然报错的选项就是在骗人。
            「已驳回」同理不给选：驳回有专用入口（待认领行的操作列）且有二次确认，
            从下拉里顺手选到一个**不可撤销的终态**是本项目最不该有的形状。 */}
        <Select
          options={STATUS_OPTIONS.filter(
            (o) => o.value !== 'pending_claim' && o.value !== 'rejected',
          )}
        />
      </Form.Item>
      <Form.Item
        label="单日上限"
        name="daily_cap"
        extra="按「当日已派发且未取消的单数」计，不按拍成的单数计；留空 = 不限。这一列是日限的唯一权威"
      >
        <InputNumber min={1} max={10000} style={{ width: '100%' }} placeholder="留空 = 不限" />
      </Form.Item>
      <Form.Item label="备注" name="note">
        <Input.TextArea rows={2} maxLength={1000} />
      </Form.Item>
      <Button type="primary" htmlType="submit" block>
        {submitText}
      </Button>
    </Form>
  )
}

/**
 * 认领表单：`pending_claim → active`。
 *
 * 站点**不给默认值**：customerId 不含站点信息，服务端登记时也不敢编。默认填一个
 * amazon_com 会让运营顺手点过去，结果美国号收到加拿大单——这正是那条 CHECK 要挡的。
 */
export function ClaimForm({
  account,
  onFinish,
}: {
  account: BuyerAccount
  onFinish: (v: FormValues) => void | Promise<void>
}) {
  return (
    <Form<FormValues> layout="vertical" onFinish={onFinish}>
      <Alert
        type="info"
        showIcon
        style={{ marginBottom: 16 }}
        message={
          <>
            正在认领 customerId <code>{account.external_customer_id}</code>
          </>
        }
        description="认领后该号才开始收派单。站点必须与这个号实际登录的亚马逊站点一致——填错会让它收到买不了的单。"
      />
      <Form.Item
        label="账号名"
        name="label"
        extra="语义 = 对应哪一个指纹浏览器"
        rules={[{ required: true, message: '请填写账号名' }]}
      >
        <Input placeholder="如：指纹浏览器 07 号窗口" maxLength={100} />
      </Form.Item>
      <Form.Item label="站点" name="site" rules={[{ required: true, message: '请选择站点' }]}>
        <Select options={SITE_OPTIONS} placeholder="请选择该号实际登录的站点" />
      </Form.Item>
      <Form.Item label="单日上限（可选）" name="daily_cap" extra="留空 = 不限">
        <InputNumber min={1} max={10000} style={{ width: '100%' }} placeholder="留空 = 不限" />
      </Form.Item>
      <Form.Item label="备注" name="note">
        <Input.TextArea rows={2} maxLength={1000} />
      </Form.Item>
      <Button type="primary" htmlType="submit" block>
        认领并启用
      </Button>
    </Form>
  )
}
