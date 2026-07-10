# R1-06 验证证据（2026-07-10）

| 检查 | 结果 |
|---|---|
| migration 0006（notification 月分区 + target + receipt + RLS） | ✅ 升降级往返随全套 |
| notify() 唯一发送入口 + dedupe_key 24h 抑制 | ✅ test_dedupe_24h |
| **task_run 失败 → critical 通知联动**（run_tracked，验收判据） | ✅ test_task_fail_creates_notification |
| task_run 成功记 stats | ✅ |
| API：可见性（团队投递）/未读数/标记已读/全部已读 | ✅ test_visibility_and_read_flow |
| 前端：铃铛角标+弹层预览 / 通知中心页 | ✅ E2E notify-smoke.mjs + 截图 07/08 |
| 后端 33 测试×2 / 前端 lint+build | ✅ |

设计落点：**system_tx 系统事务上下文**（worker/beat 无用户身份，以 is_super GUC 执行，
仅限非用户代码路径）——解决后台任务被 RLS 拒写的通用问题，后续所有 worker 复用。
已知小节：铃铛角标 30s 轮询，页面内已读后角标下轮刷新（R1 接受）。
