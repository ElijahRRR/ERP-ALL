# Task Definition
- Mode: build（R2 后半程——007 计划生效；动工顺序 2026-07-17 更新：
  **R2-11 → R2-07 → R2-12（与 RS-04D 同窗）→ R2-09 → R2-08 → R2-10**，
  FE-DESIGN Owner 触发制，R2-10 前置 RS-01/02）
- Task: **R2-12 合规数据供给持续化【L1】（与 RS-04D 断言账本同窗）——2026-07-23 立项开工
  （Owner 指令），增量1-5 全部并入 main，仅剩验收① 三日连测未收账（07-26/27/28 窗口内）**；
  R2-07 07b 开发面完成（PR #25 已合并 2026-07-18）待验收②；R2-11 已整单关账（accepted）；
  R2-09 考古已完成（2026-07-25，只读未立项）。
  - R2-12 范围（007 §R2-12 + review_list R2-12/RS-04D）：①RS-04D 断言账本（blacklist
    写路径，硬验收=blacklist_brand 真实跑通非空框架）②USPTO 日度自增量（部署机
    daily_update → RS-04A 通道 → refdata.trademark + revision 失效 + beat/告警）③TRO
    采集按日进 tro_case + 派生品牌断言 ④全店后台 SKU 拉取对账（GET /v3/items 翻页 ↔
    本地 listing）+ 报错回收黑名单候选（source 扩 error_recycle）⑤合规中心页。
    依赖：RS-04A ✅（14M 实测另行）；07c 邮件钩子后置不阻塞。
  - R2-12 增量 1-5 ✅ **全部并入 main**（PR #30/31/32/33/34/35，增量5 = 2026-07-25 Owner
    授权 squash `73b8c19`）；验收②③④⑤已由前序增量真机复现（部署机两段分支验证全 PASS：
    黑名单账本三源并存+撤销恢复 / 商标库 trgm+nice 检索 / 导入报错 Drawer 逐块核对 /
    无权限账号 403 门控 / 「按主体追溯」撤销闭环）。**仅剩验收① USPTO 三日连测未收账**：
    07-25 第 1 日 FAIL（bat 为 LF-only 致密钥文件变量从未赋值，日志「密钥缺失」是假象），
    窗口顺延 **07-26/27/28**。修复链：bat 纳入版控 + `.gitattributes` 强制 CRLF → 改纯
    ASCII（compose 路径挪密钥文件）→ 静态过一遍修四处（findstr 对 Docker 裸 LF 输出
    误判 / 日志 `>>` 数字吞重定向 / PS 嵌套引号管道）→ `walmart-trademark-sync` PR #1
    （两跳下载修复）验证通过并合并 main `9bc0bbbf` → 彩排发现 ETL 慢如龟爬，根因是一次性
    迁移 `pg_restore -t` 遗漏四张子表二级索引（非 GIN 维护开销，此前误判已更正），补索引
    后整链首次端到端跑通（EXIT=0，300 倍提速）。**07-26 18:00 起为 A 段（自动触发）实质
    证据窗口**，待部署机回报填入三日表。docs 收尾 PR #36（草稿，另含 R2-09 考古，待三日
    连测结束后与 Owner 商定合并范围）。
  - R2-11 ✅ **accepted（2026-07-23，PR #24/26/27/28 全合并）**：D-Q64 四点全落地——
    实时归组（入库即组，真机 B0DGTYRBZQ 实证）/ 自动路由与散品上架双档 / 批次原子性
    守卫 v2 / live 补挂成组（组 8 真机修复 feed#42 8/8）+ 组 6 子集全 live 成组。
    观察项遗留见 progress 2026-07-23 节（coerce enum 改写、spec 换版核实、
    自动降散品开关待 Owner 立单）。
  - 07b（PR #25 已合并 2026-07-18）：0033 brand_assignment 建表+占用/释放闭环+outbox 封店门控+
    suspension_reminder beat+店铺事件前端页+run_task 单跑工具+契约/runbook。评审 2 major
    全修（outbox drain 无门控、提醒 24h 窗架空 remind_days）。CI 全绿（457）。
  - **待 Owner（07b 验收②，与 R2-12 无依赖、可另排时间）**：按 runbook「封店工作流演练」——
    A152 造品牌占用 → 前端登记 suspension（occurred_at 回填 ≥7 天前）→ 核对店铺
    suspended/占用批量 released → run_task suspension_reminder → 通知中心见提醒 →
    resolved 恢复。
  - R2-09 考古（2026-07-25，只读未立项，`.agent/evidence/R2-09/archaeology.md` 1512行）：
    六路并行+对抗性交叉核对，推翻两路初判；**四条硬阻塞待 Owner 裁决**（001§09 flow 清单
    冻结v2 / 007 验收判据四环 vs 001 供给两环 / 「吃 Redis pubsub」表述与实测不符需改述 /
    refund auto 端点+分级）；另 6 条待裁 + 8 条盲区；顺带实锤一处已上线 fail-open
    （`listing/maintenance.py:29` kinds 默认值，潜伏未触发）待 Owner 定修复方式。立项与
    增量0 均待 Owner（按 007 顺序 R2-12 收账后开工）。
  - 接续（按批准顺序）：R2-12 收账（验收① 三日齐绿）→ R2-09（考古已毕，待 Owner 裁决
    四阻塞后立项）→ R2-08 → R2-10；07b 验收②/07c 邮箱（需 Owner IMAP 凭证）为独立支线，
    随时可插不阻塞主线。
- R2-11 挂账：anchor 解锁通道、组上下文批量化 已随 2026-07-18 检修增量清偿（解锁端点
  POST /variant-groups/{id}/anchor/release + 批量 load_build_contexts + 同族历史分裂
  合并缺口修复 + 空维度 broken 判定增补）。余观察项：维度值过 coerce enum 改写
  （A152 实测关注）；spec 版本 5.0.20260304 换版窗口在线核实 per-PT variantAttributeNames。
- 全局挂账：R2-05 L2 发货补验（等 A152 真实来单）；R2-04 验收②模拟断连；钓鱼黑名单导入；
  erpAPI PR #2 待授权；售后前端页（returns/refund 部分随 07c；店铺事件页 07b 已交付）。
  已清偿：前端 schema.d.ts codegen（07b 随契约重生成，含 R2-05/06/07a/11 既往欠账）。
- **验证纪律挂账（2026-07-26 Owner 查出，铁律 4 实质违反，已认）**：增量 1/3/4b 均在
  「待分支验证」状态下直接合并，验证结果全仓零记录；4b 属渠道写路径而 R2-12 全单无一份
  dry-run 快照落仓（对照 R1-07/R1-11/R2-03/R2-06 皆有）；真机图全在 PR 评论、不在仓内
  （evidence/R2-12/ 6 个 .md、零图）。**①已清偿（2026-07-26）**：新增
  `evidence/R2-12/dryrun-mp-maintenance.json`——MP_MAINTENANCE（增量4b 引入的唯一新增渠道
  **写**路径）dry-run 请求快照落仓，机制与既有三处一致（test_gateway/test_listing_api/
  test_price_push：dry_run 断言请求形态 + 写 evidence），并硬断言 feedType 查询参/五必填头/
  代理地址不泄漏。item_pull 是 GET 读路径，不在铁律 4「渠道写路径」范围。
  余待 Owner 定序：②真机图从 PR 评论搬进 evidence/R2-12/ ③CI 加只读门禁（渠道写路径增量的
  PR 必须带 evidence 变更）④是否让服务层把 `request_snapshot` 落进 `channel_command.result`
  ——现 `_apply_item_maintenance` 在 dry_run 分支只落 `{"dry_run": True}` 把快照丢了，
  致生产 dry_run 态也观测不到请求全貌。
- **RBAC 挂账（2026-07-26）**：`0035_blacklist_assertion.py:150-167` 按角色名字面
  （'团队管理员'/'审核员'）发 `compliance.*`，而现网两账号均未绑任何角色——**该种子至今
  一份权限都没发出去，且静默不报错**。超管走 `is_super` 短路不受影响（authn.py:47），
  但一旦出现第二个真人非超管账号，合规中心即 403/空。待 Owner 定：是否改为按权限码
  声明式授权（按角色名字面匹配对改名/多语言/新建团队都会漏）。
- **次要项挂账（2026-07-26 Owner 提出）**：`rebuild_canonical` 无生产入口（CLI/端点/beat 皆无），
  RS-04D 第四条硬验收只在 pytest 内成立；`item_pull` 第四类差异 `gone_remote` 在 beat 聚合
  （:369-379）被丢弃；契约顶层 tags 块少声明 4 个 tag；`ImportJobsTab.tsx:15` 手写 interface
  （FE-DEBT-01 累计 29 处）；CI 无 codegen 漂移检查、无前端测试。
- **待 Owner 裁（P0-2 衍生）**：`listing/service.py:1682` 渠道拒时返还 maintenance 配额，与
  `release_quota` docstring「maintenance 不返还」相矛盾——该次 feed 已真实消耗 Walmart 调用，
  返还会让反复被拒的 listing 无限重试而本地计数不动（fail-open）。改限流语义属渠道写路径、
  按铁律 4 需 dry-run 证据，本轮只修名未动语义，代码注释已就地标注。
- **台账纪律挂账（2026-07-26 全面核对查出，见 progress 同日节）**：关账回写此前只做
  progress+task 两处、漏 review_list.json（PR #29「R2-11 整单关账回写」实证只改两文件，
  致 R2-11 accepted 后台账仍挂 in_progress 达 9 天）。已补修四条（R2-11/R2-07/RS-04A/RS-04D）。
  待 Owner 定机制：①关账强制三档齐写 ②加 CI 只读检查（status=accepted 条目的
  last_checked_at 不得早于其关账 PR 合并日）③review_list.json 字段形状收敛（现 8 种）。
- Constraints: workflow discipline per CLAUDE.md；每增量 CI 绿（pytest/ruff[check+format]/mypy +
  pnpm lint/build）；业务参数一律 system_config；不绕过 walmart_client 语义直连渠道；
  fail-closed 底线；migration 仅 ar 帽；specs 正文只由云端 AI 落笔（007/图纸归审计侧，批注回传）。
- 真机验证流程（2026-07-18 Owner 拍板）：增量先在 PR 分支上由部署机验证（前置核验点
  分支 head），通过后 Owner 授权合并 main，合并后重建分支接着开发；部署机验完切回 main
  常驻；含迁移的增量若分支被弃须 alembic downgrade 归位。
  **切回 main 前必做「运维资产在位检查」**（2026-07-26 Owner 查出 P0-3）：只在开发分支上的
  运维资产会随切换从检出树消失。现 `.gitattributes` + `infra/local-deploy/automation/`
  （uspto-daily.bat + README）**尚未进 main**（origin/main=5329146 无此二者），一旦执行
  「切回 main」修复版 bat 与 CRLF 声明同时丢失，07-25 LF-only 事故原样复发。检查命令见
  infra/local-deploy/README.md「切回 main 前必做」节，三行不齐**不许切**。
  根治=让这些资产进 main（PR #36 已含，待 Owner 授权合并；或另拆 ops-only PR，需 Owner 允许开新分支）。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR 按增量推），旧 erpAPI 仓挂载于 /home/user/erpAPI。
