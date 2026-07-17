# R2-07 07b 封店工作流 考古汇总（2026-07-17）

> 五路侦察原始报告：raw-07b-{A图纸决策,B旧表schema,C现有代码,D契约工单,E旧仓语义}.md。
> 本文为规划侧汇总，实现以此为准；与图纸冲突处以 specs/001 为准。

## 一句话地形

**store_incident 表（0003）、三端点（GET/POST/transition）、封店↔店铺状态联动均已存在**；
`channel/router.py:537` 留坑「SKU/品牌释放作业随 R2#7 catalog 联动接入」。
**brand_assignment 表整仓不存在**（图纸预留 ≠ DB 预留）——07b 真缺口：
①建表 ②占用生产端（build 分配 upsert）③封店批量释放 ④beat 提醒 ⑤前端页。

## 硬事实速查

| 项 | 事实 | 出处 |
|---|---|---|
| store_incident 表 | 0003 已建全列（含 sku_released_at/brand_released_at 预留）| alembic/versions/0003_channel.py:190-216 |
| incident 端点 | 列表/建单/transition 已接线；suspension→store suspended、resolved→active | channel/router.py:481-589 |
| 释放坑位 | create_incident suspension 分支 :536-541，:537 占位注释 | 同上 |
| brand_assignment 图纸 | 列级+`uq_brand_occupied (team_id,brand_norm) WHERE status='occupied'`+incident_id 回链 | 001/03-catalog.md:72-89 |
| 分配/释放时机 | 「build 上架分配店铺时自动 upsert（同店已占通过、异店拒绝+警示）；封店工作流批量 released」 | 03-catalog.md:89 |
| 联动四步 | ①suspended 回写（已有）②brand 释放+listing 停止维护+GTIN 保持 used（缺）③schedule 提醒→notification（缺）④resolved 人工（已有） | 02-channel.md:183-187 |
| 提醒配置键 | automation_flow `suspension_reminder` → config `remind_days`（D-Q33） | 09-platform.md:166 |
| 权限 | channel.incident_read/write 已种并挂团队管理员，复用 | 0002_identity.py:275-276,338-339 |
| 迁移号 | 0033 空闲（目录 backend/alembic/versions/） | 实测 |
| beat/通知范式 | 抄 gtin_watermark（tasks.py:593-657）+ notify() dedupe 24h；注册进 TASKS dict | automation/tasks.py, notify/service.py:20 |
| 种子范式 | INSERT app.schedule ON CONFLICT (code) DO NOTHING | 0022/0024/0030 |
| 测试范式 | beat=tests/db/test_beat_alerts.py；API=test_channel_api.py（已有 test_incident_suspension_links_store :212-240 可扩） | 实测 |
| 前端 | 无 incident 页；schema.d.ts 已含 StoreIncident* 类型；路由 App.tsx + MENU AppLayout.tsx（permission 门控） | raw-D |
| 契约 | /store-incidents 三路径已在（Channel tag）；brand-assignment 路径缺；incidentId 未抽共享 param | openapi-v0.yaml:345-375 |

## 旧仓语义（对拍参照，不可照搬）

- 旧封店链三孤岛：sellerStatus→飞书表→上架闸门（fail-open，仅"本轮不上架"）；store_incidents 表只有 mock seed + 只读端点；brand_store_assignments 按 listing 计数 delete-on-zero（listings.py:268-284 `_release_brand_assignment`）。**无"封店→批量释放"闭环，07b 为净新建**。
- 新旧释放机制根本不同：旧=删行；新=翻状态 occupied→released + incident_id 回链 + 历史保留（图纸 :80-84）。partial unique 使 released 行不占唯一槽，品牌可再分配。
- 旧表差异：无 team_id、varchar 主键、store_name 主维、resolved 布尔非状态机——不迁移旧 3 行数据。
- ops 文档（walmart_ops_knowledge_v4 1.1.4/1.1.5）要求"暂停/封店定时提醒申诉"= beat 提醒的需求权威；污染池/自动下架/停采集等属 07b 范围外（挂账）。

## 范围裁定（规划侧）

1. **入**：0033 建 brand_assignment（图纸全列+RLS+touch）+ schedule 种子 suspension_reminder；
   build 分配 upsert 占用（match 豁免；无 brand 不占用；异店冲突拒绝 BRAND_OCCUPIED_OTHER_STORE）；
   suspension 建单批量释放（幂等：brand_released_at 已填则跳过）+ 回填 + notify；
   beat suspension_reminder（remind_days 周期，dedupe_key 带周期号）；
   GET /brand-assignments + 单条 manual release；前端店铺事件页；契约落笔；runbook 演练步骤。
2. **sku_released_at**：只在"listing 停止维护"真实成立时回填——实现侧先盘点渠道维护类 beat 任务对 store.status 的门控现状，缺则补 `store.status='active'` 过滤（纯内部，不碰渠道写路径），全覆盖后在释放作业里回填；盘点结果记录进 findings。
3. **出**（挂账）：申诉信管理（D-Q33 backlog 原文）、污染池、自动下架级联、07c 邮件生产端（mail_message_id/mail_body_snapshot 列已备）。

## 验收（007 原文）

② 手工造 incident 演练：封店→品牌占用批量 released→beat 提醒送达（A152，真机）。
