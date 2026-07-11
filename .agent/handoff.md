# Session Handoff
- Current mode: build → R2 开工（R1 已 Owner 验收通过关账 2026-07-11）
- Done: EA-001~004 ✅；R1-01~12 全部 accepted（骨架验收，D-Q54 定级 L0）；部署机(Win11)整栈跑通+重启自愈实测
- R2-01 采集引擎移植【L1】沙盒完成（done）：workers/ 独立 uv 包，5 爬虫核心模块逐字移植 + erp_client(/worker/v1)+payload 胶水 + engine/run/config 编排壳 + Dockerfile + compose scraper profile + CI workers job；26 离线单测/ruff/format/mypy 全绿。L1 真抓验收待 Owner 机器+TPS 代理（evidence/R2-01/runbook.md）
- 队列: R2-02 审核弹药【L1】→ R2-03 上架真实化【L2, 含 A152 真调】→ R2-04 worker/beat → R2-05 订单；顺序=Owner 三缺口对应，见 specs/005-r2-plan/README.md
- Owner 侧待办: 路由器固定 IP（团队接入前）；rclone 异地备份（红线, 一周内）；TPS 代理凭证 + 部署机设 scrape.worker_enroll_token（R2-01 验收前）
- Read first: CLAUDE.md → .agent/progress.md（尾部3节）→ specs/005-r2-plan/README.md → .agent/evidence/R2-01/archaeology.md（三层移植策略）→ backend/src/erp/scrape/service.py（协议服务端）
- 铁律提醒：migration 仅 ar 帽可动；业务参数一律 system_config；worker/系统路径用 system_tx，用户路径禁用；沙盒永不真调渠道/不真抓 Amazon（引擎开发用离线 fixture）
