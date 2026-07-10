# R1-12 本地验证记录（2026-07-10）

## 验收对照（specs/003 §R1-12）

| 验收项 | 结果 | 证据 |
|---|---|---|
| 脚本化演示：采集→审核→上架→轮询回写→前端全程可见 | ✅ | e2e/full-chain.mjs 全自动跑通；截图 01-08（采集作业进度→产品审核状态→分配→提交→feed PROCESSED→listing live+状态历史时间线） |
| 失败路径①审核拒绝 | ✅ | 截图 03/04：Compatible for Dyson 品 audit_rejected + LLM 证据抽屉（is_real_brand 翻案） |
| 失败路径②配额耗尽拒绝 | ✅ | 截图 09/11：listing_create 配额 2 用尽后第 3 次提交 skipped ERP_QUOTA_EXHAUSTED（listing 停在 draft，不出包） |
| 失败路径③feed 错误→error catalog→notification | ✅ | 截图 10/11：WM_E2E_DEMO 自动入字典草稿 + listing failed 红标 + 通知中心 critical「上架 Feed 全部失败」 |
| Owner 现场/录屏验收 = R1 完成判据 | ⏸ Owner | owner-acceptance-runbook.md（部署机重放脚本）；A152 真调（R1-11 尾巴）同场执行 |

## 演示架构

- 后端指向双本地 mock：`ERP_LLM_API_BASE`（9801，按 'compatible for dyson' 判 reject）
  + `ERP_CHANNEL_BASE_URL`（9802，奇数 feed 全成/偶数全败）——网关基址已参数化（Settings）。
- worker 拨入用真实机器协议（register/pull/result 走 HTTP），非测试桩。
- headline 防伪演示：mock 汇总故意报 999 成功，系统按 item 级回写 1 成 1 败。

## 本单顺手修复

- transition() 把 reason_code 误写入 error_code 列（live 行挂红色 ingestion_success 标签）
  → error_code 仅 failed/degraded 落值，其余清空。
- 前端提交 0 项时不再弹 "feed: undefined" 成功提示；跳过警告展示 6s。
- 网关 BASE_URL 硬编码 → Settings.channel_base_url（生产默认官方域名不变）。

## 回归

75 pytest ×2 + ruff/format/mypy + eslint/tsc build 全绿。

## R1 收口状态

R1-01~10 ✅（CI 绿）；R1-11 沙盒✅ + A152 真调待 Owner；R1-12 沙盒演示✅ + Owner 验收待执行。
**Owner 侧两件事合并为一场：部署机跑 owner-acceptance-runbook.md（含 A152 真调 1 SKU）→ R1 完成。**
