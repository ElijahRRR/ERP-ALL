# R1-07 验证证据（2026-07-10，全离线——沙盒严禁触碰真渠道）

| 检查 | 结果 |
|---|---|
| 考古对照表（两源仓机制逐条映射） | ✅ archaeology.md |
| dry_run 默认模式：构造快照不发包 | ✅ + dry-run-snapshot.json（MP_ITEM feed 提交快照） |
| live_test 闸：非测试店拒绝(GATEWAY_STORE_NOT_TEST)；测试店真发包(MockTransport) | ✅ |
| live 闸：channel.live_enabled 未开 → GATEWAY_LIVE_DISABLED（Owner 放量开关） | ✅ |
| token：900s 缓存 3 次调用只换 1 次；401 自愈刷新重试（断言第二次带新 token） | ✅ |
| 五个必填头逐一断言（make_headers 移植保真） | ✅ |
| 429 按 Retry-After 退避重试（计时断言） | ✅ |
| GCRA：超窗拒绝；x-current-token-count=0 时按响应头推后 next_avail | ✅ |
| parse_retry_after 优先级/上限 300s | ✅ |
| 全套 46 测试×2 / ruff / mypy strict | ✅ |

保留的源仓实战资产：MP_ITEM=10/hour 实测校正、SOCKS hang 防御注记、半死连接自愈、
max_wait 按端点 period 自适应（修 10/hour 端点 65s 即失败的坑）。
**A152 真调冒烟**：待 Owner 本地部署完成、界面录入 A152 真实凭证+代理后，
在部署机上执行（gateway_mode=live_test + GET /v3/items 只读）——runbook 后补一条命令。
