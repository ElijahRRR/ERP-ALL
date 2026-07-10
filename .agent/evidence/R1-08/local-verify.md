# R1-08 验证证据（2026-07-10）

| 检查 | 结果 |
|---|---|
| 凭证：加密落库（密文断言）/掩码回显/永不回显 secret/网关通道解密往返 | ✅ test_store_credential_proxy_quota |
| 代理独占：第二家店绑同一代理 → 422 PROXY_OCCUPIED | ✅ |
| 配额：3 限额下连取 5 次 = [T,T,T,F,F]；返还后可再取；用量 API 对账 | ✅ |
| 封店事件：suspension→店铺 suspended；resolved→人工确认恢复 active | ✅ test_incident_suspension_links_store |
| 后端 35 测试×2 / 前端 lint+build | ✅ |
| E2E channel-smoke（店铺列表/凭证抽屉/配额面板/代理台账）+ 4 截图交付 Owner | ✅ |

踩坑：pgcrypto 表达式里可空绑定参数需显式 cast（CASE WHEN cast(:pw AS text)…）。
A152 真实凭证由 Owner 部署后在店铺管理界面录入（R1-07 前置就绪）。
