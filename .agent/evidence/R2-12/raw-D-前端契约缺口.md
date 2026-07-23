# 考古D：合规中心页前端与契约缺口（2026-07-23）

## 前端现状
- 15 页清单在档（App.tsx:26-42 路由，AppLayout.tsx:28-41 导航）；黑名单/商标/合规页完全缺失（/audit 是 identity 操作流水非合规）。

## 契约现状（openapi-v0.yaml）
- import 已有：/import-jobs POST 上传 multipart（x-permission catalog.import_write :568-572）、GET 列表/详情（catalog.import_read :584-592）；ImportJob schema :1676-1690（status/行数/error_report_ref）。
- blacklist/trademark/tro：API 路径全部为 0；DB 侧齐备——四表同构（0008:35-53，source CHECK 四枚举=来源追溯）+ refdata.trademark（0008:239-254，ix_tm_mark_trgm gin + ix_tm_nice gin）。
- TRO：无契约无落表（tro 仅为 import_job domain 枚举）。
- import 进度△（GET job 返 status+行数）；错误报告下载✗（仅 error_report_ref 字符串，无下载端点）。
- **契约/DB 权限码不一致**：openapi 用 catalog.import_read/write（:572/:586），DB 实际种的是 compliance.import_read/admin（0010:82-83）——需对齐。

## 权限点
- 可复用：audit.run/read/policy_admin（0008:301-303）；compliance.import_read/admin（0010:82-83）。
- 需新增：compliance.blacklist_read/write、compliance.trademark_read、compliance.tro_read。

## 组件先例
- 首选 ListingsPage.tsx（Table :213 + 过滤 :207 + rowSelection :217-219 + Drawer :346-413）；次选 Products/Orders/Incidents。

## 合规页最小拼装清单
1. 契约新增：/blacklist/{domain} GET+POST+DELETE（含 source）、/trademark GET（trgm+nice）、/tro-cases GET、/import-jobs/{jobId}/error-report GET。
2. 契约修正：import-jobs x-permission 对齐 compliance.import_*。
3. 权限新增：compliance.blacklist_read/write、trademark_read、tro_read（迁移种子）。
4. 前端：CompliancePage.tsx（照抄 ListingsPage 骨架）+ App.tsx 路由 + AppLayout 导航（perm compliance.blacklist_read）。
5. DB 缺口：仅 tro_case 需建表；四黑名单+trademark 索引已就绪。
