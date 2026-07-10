# R1-12 实施计划（qa 收口）

## 沙盒范围
1. 后端补线：
   - notify() 接入 listing poll：feed 终态 error/partial → notification（critical/warn, dedupe_key=feed:{id}）
   - gateway base URL 改走 Settings.channel_base_url（默认官方域名）——E2E 可指向本地 mock 渠道
   - catalog 最小路由：GET /products, GET /products/{id}（含 latest audit 摘要）——前端产品页需要
2. 前端三页 + 菜单（permission 门控）：
   - 采集作业页（建 job/进度计数）
   - 产品页（状态筛选/触发审核/审核结果抽屉/分配上架）
   - 上架页（listings+feeds/提交/轮询/下架/状态历史抽屉）
3. E2E 全链演示（Playwright + 本地 mock：LLM mock server + channel mock server）：
   - 主链：采集(worker 拨入模拟)→审核 pass→分配→提交(live_test→mock)→poll→live→前端截图全程
   - 失败三演示：①审核拒绝(mock LLM reject) ②配额耗尽(quota_config=0→submit 拒) ③feed 错误→error catalog 草稿→notification 铃铛告警
4. Owner 验收 runbook（部署机重放脚本+录屏点位）

## 状态
- [x] 计划落盘
- [ ] 后端补线
- [ ] 前端三页
- [ ] E2E 脚本+截图
- [ ] runbook + 汇报
