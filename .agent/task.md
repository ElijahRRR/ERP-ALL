# Task Definition
- Mode: build（R2 后半程——007 计划生效；**动工顺序 2026-07-27 按 D-Q66/67/69 更新**：
  **R2-09 → R2-13（自动采购接入）→ R2-07c（邮箱）→ R2-08（财务域）→ RS-06 → RS-08**；
  并行收尾 R2-12 验收① / R2-04 beat 实测 / RS-02b。
  **已移出 MVP，不要做**：R2-10 采购方门户（D-Q66）、R2-05 发货环节、RS-01、
  RS-05/07/09/10、RS-04C；FE-DESIGN 仍为 Owner 触发制）
- Task: **R2-09 三档自动化贯通【L1】——2026-07-27 立项，开工中**。
  前置全部解除：考古完成（`.agent/evidence/R2-09/archaeology.md`，1512 行，六路并行 +
  对抗性交叉核对）；四条硬阻塞由 Owner 2026-07-26 四条裁定解除并由审计侧落 001§09/007；
  flow 清单 **v2.1** 冻结（10 条，07-27 补 `purchase_execute`）。
  - **增量拆分**（沿用考古 §4，按 v2.1 对齐）：
    ①policy 内核 + 10 条 flow Enum + CI 一致性校验 + 两处旧代码回接 + `kinds` 跨域清偿
    （纯重构，行为零变化，整单地基）**✅ 已关账——PR #41 squash 合入 main `4b668e4`，四道闸走满**
    ②策略读写 API（GET/PUT `/automation-policies`）+ 权限点 `automation.read/write`
    + 前端策略面板 + **Q3 三条**（面板显性区分「未配置/已停用/manual」三态、`enabled=false`
    记 warn【增量1 已落】、闸类 flow 停用给显式二次确认）——**无前置，当前在推**（分支已从 `4b668e4` 重建）。
    **面板不做「应用默认模板」按钮**（Q2 终裁不加模板）；新团队仍是零策略行 = 全 manual
    ③`audit_to_listing` 三档（含 `scrape_to_audit`）④`pricing_watch` 三档 + 第二套档位语义
    收口 ⑤`refund`/`cancel` auto 档渠道执行链（**本单唯一 L2 片**，不可逆真钱）
    ⑥三档全链验收取证 + runbook + 图纸/契约/工单回写。
  - **落码三条不能写反的 fail-closed**（v2.1 原文）：`maintenance_run.kinds` 默认空；
    档位**不进** ConfigService/Redis 缓存（config bus 是 fail-open，档位闸必须 fail-closed）；
    `order_block`/`compliance_block` 只有 `{manual, auto}` 两档。
  - **`purchase_execute` 现无消费点**（归 R2-13），属**有意的前置登记**——CI 一致性判据
    只校验「Enum ↔ 表」双向一致，**不得**加「每个 flow 必须有消费点」，否则第一天就红。
  - **三条待 Owner 裁定 → 2026-07-27 全部裁定并结清**（回执
    `.agent/evidence/R2-09/owner-rulings-20260727.md`；原提问件 `owner-questions-20260727.md`）：
    **Q1** 验收判据改「三件同族商品各跑一档」（选 (a)；**不引入状态回退通道**）——台账已改，
    007:88 正文**已由审计侧 `6e4b7d4` 同步**（连同 R2-13 13c 护栏留痕、§09 契约门禁解析
    约束提醒——三条批注回传全部落地，此项挂账已销）；**Q2 终裁不做模板**（同日两次裁定，第二次推翻第一次：「既然规划里
    没有模板，那就不加了，按现状实现，面板不做模板按钮，以后想要了以后再说」）——增量2 不再
    拆分，无需改图纸，未采纳方案降格备查；**Q3** 接受三条建议 → 入增量2。
  - **R2-09 当前零条待 Owner**（原随 Q2 提出的 B1/B2 两条阻塞与 R1/R2/R3 三条报备
    **随 Q2 终裁一并撤回**，不再需要裁定）。
  - **跨单留痕（未采纳方案里唯一仍成立且有用的一条）**：`automation_policy.config`
    **全仓零消费点**——`core/automation.py:114` 只 `SELECT mode, enabled`，四个护栏键
    `amount_ceiling`/`daily_cap`/`price_delta_pct`/`check_kinds` 在 `backend/src` 零命中。
    **R2-13 13c 开 `purchase_execute` auto 档时，护栏消费点得从零建**，不能假设
    「config 里配上就生效」。
  前序状态：R2-12 增量 1-5 全部并入 main，**仅剩验收① 三日连测未收账**（07-28 为第 3 日）；
  RS-02a 已整单关账（PR #39/#40，四道闸首次按原序走满）；R2-07 07b 待验收②；
  R2-11 已整单关账（accepted）。
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
    后整链首次端到端跑通（EXIT=0，300 倍提速）。**验收① 第 1 日（07-26）PASS**（首条 A 段
    实质证据：Last Run 18:00:01 / Result=0 / Next Run 07-27 18:00；无数据日——apc260723
    429 限流窗内；USPTO 14,216,076 / ERP 4,475,105 / 双侧 newest 2026-07-25 / revision 204 /
    lag_days=1）。**只差第 2、3 日（07-27 / 07-28 各 18:00 的 A 段）**，三日齐绿即与 RS-04D
    一并关账。收尾 PR #36 已由 Owner 授权 **squash 合入 main `5b37ded`（2026-07-26）**，
    含运维路径/三日协议/两处 fail-open 修复/bat 纳入版控/R2-09 考古/台账修正；合并前已
    更正正文三处失真（合并后正文即永久记录）。分支已按纪律从 main 重建（重建前比对树，
    唯一差异是 main 多一处 specs/007 验收④措辞修订＝审计侧落笔，本分支内容零丢失）。
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
  erpAPI PR #2 待授权；售后前端页（returns/refund 部分随 07c；店铺事件页 07b 已交付）；
  **部署机删 `erp_all-before-0039.dump`（Owner 2026-07-28 裁定「删」，指令已下发，等回执
  ——判据 `Test-Path` 必须 False；误提交路径已先行用 .gitignore 堵死）**。
  已清偿：前端 schema.d.ts codegen（07b 随契约重生成，含 R2-05/06/07a/11 既往欠账）。
- **2026-07-26 Owner 批准落地四件（「按你建议的做」），见 progress 同日末节**：
  ①`service.py` 渠道明确拒绝**不再返还** maintenance 配额（活 fail-open 已清；闭环无界已由
  `test_repeated_rejects_exhaust_the_daily_gate` 对旧码实测证明，非推理）②dry_run 请求快照
  落进 `channel_command.result`（抽 `_dry_run_result` 统一三处，原先三处都在丢快照，带 32KB
  体积守卫）③三条 CI 只读门禁上车（权限可达性 / 台账结构 / 渠道写路径必带 evidence——第三条
  首轮 `ADVISORY=1` 只告警，观察无误伤后删掉即变硬闸）④Windows 自动登录**定案不配**。
  ✅ **8 个漏授权限码已裁定并补齐（迁移 0039，2026-07-26）**：实测坐实 0039 生效后无角色可达
  的码只剩 `identity.team_admin` + `compliance.import_admin` 两条设计上超管专属；白名单同步
  收窄。三处授予对象按权限官方名称收敛（我给 Owner 的描述与实际名称不符，故未照批的执行）：
  `listing.error_admin`=错误字典维护→仅团管；`catalog.source_write`=货源录入→仅团管；
  `catalog.category_write`=类目映射修正→审核员+团管。新增授予矩阵精确锁定 + read/write
  对称性两条测试。
- **R2-09 开工前四条硬阻塞：Owner 2026-07-26 全部裁定，批注已回传待审计侧落笔**
  （`.agent/evidence/R2-09/owner-rulings-20260726.md`，逐条给出可套用的改动请求）。
  口径更正：此前说「四条硬阻塞」，核原文应为**10 条待裁、前 4 条不裁开不了工**，后 6 条随
  对应增量逐个提请、不阻塞开工。裁定：①flow 清单 v2 冻结（删两行双落点 / listing_pricing 归一
  为 pricing_watch / 新登记 scrape_to_audit / 新登记 D-Q65② 宪法要求的 maintenance runner 档位 /
  match 跳 sourcing 归 audit_to_listing）②验收判据四环不下调，补登记 scrape_to_audit +
  listing_dispatch 凑齐③order_block/compliance_block 认二元（唯一已上线消费点，不动）
  ④删「吃 R2-04 Redis pubsub」实现指定，改「档位每决策直读、不进缓存」（实测那套缓存生产零
  读者、且 fail-open 与档位必须 fail-closed 方向相反）。
  ✅ **前置已解除（2026-07-26）**：规划/审查 AI 已按批注落笔并合入 main `de3c546`——001§09
  flow 清单 v2 冻结（九条：新登记 scrape_to_audit / listing_dispatch / maintenance_run，删
  gtin_alert / suspension_reminder，listing_pricing→pricing_watch 归一）+ 逐 flow 求值语义表
  （实时求值 vs 创建快照）+ order_block/compliance_block 二元档位 + 直读不进缓存 + beat 逐条目
  读档纪律；007 验收四环 flow 映射与切档口径同步修订。审计侧另注明四条断言已源码复核属实。
  **R2-09 可立项开工**（按考古 §4 的 7 增量拆分；余 §2[5]~[10] 六条随对应增量提请）。
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
- **RBAC 结论（2026-07-26，前述判断已更正，0035 不改）**：曾判「0035 按角色名字面授权至今
  一份都没发出去」——**干净库实跑全量迁移已证伪**：0002 本就把七个角色种成全局模板角色，
  0035 按名匹配确实命中（团队管理员 5 条 compliance、审核员 3 条），且 `identity/router.py`
  建团队时复制模板角色连带权限映射，范式自洽，无需改造。现网 `compliance_perms=0` 的真因是
  **没有任何用户绑角色**（user_role 空）——部署数据状态，非代码缺陷。真问题是那 10 个无角色
  可达的权限码，已由 CI 门禁① 钉住 + B 组 8 条待 Owner 裁（见本文件上方条目）。
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
  运维资产会随切换从检出树消失。**该 P0 已根治**——PR #36 合并后 `origin/main`=`5b37ded`
  已含 `.gitattributes` + `infra/local-deploy/automation/`（uspto-daily.bat + README），
  实测三行齐全，**部署机收账后可安全切回 main 常驻**。检查命令（见
  infra/local-deploy/README.md「切回 main 前必做」节）作为长期 fail-closed 门保留，防将来
  又有只活在分支上的运维资产：三行不齐**不许切**。Owner 明示不必另拆 ops-only PR。
- 本工作区环境：开发分支 claude/r2-03-launch-leg5n8（PR 按增量推），旧 erpAPI 仓挂载于 /home/user/erpAPI。
- **CT-0727 新登记（2026-07-27，由 RS-11 门禁的反向不变量逼出）**：002 契约已声明但端点未建的
  7 个 operation——catalog 5（`GET /category-map`、`PATCH /category-map/{mapId}`、
  `PATCH /products/{productId}`、`GET`+`POST /products/{productId}/sources`）+ listing 2
  （`GET /listing-errors`、`PATCH /listing-errors/{errorCode}`）。**7 条都是想要而未建、不是废
  声明**，且其中三码（category_write/source_write/error_admin）正是 0039 补授 8 码之三（权限已授、端点未建＝提前授权）。**更正**：原写「4 个」把 `catalog.product_write` 算了进来，该码不在 0039 内、0002 授的是审核员非团管——团管是否需要它已挂进 CT-0727 待裁。**优先级与拆单
  口径待 Owner 立项时拍**，本单只做登记不预设范围。门禁白名单已改指 CT-0727——本单一旦收账
  而端点仍未建，反向不变量会再红（防「前置声明豁免」退化成永久豁免）。
- **RS-11 子项① 已落地、本单不能关账**：契约四向一致性门禁已进 CI；子项② `superseded_by` 标注
  与 D-Q→文档→工单追踪列**需 Owner 批准**（动 DECISION-FORM 宪法）后由规划/审查 AI 落笔；
  子项③ NOT VALID→VALIDATE 纪律入 `00-conventions` 归规划/审查 AI；子项④ 已由审计侧 421f83d 核销。
