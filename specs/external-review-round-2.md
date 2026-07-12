# 外部评审 Round-2 · Fable 逐条回应（2026-07-12）

> 评审方：部署机本地 AI（基于 main@995074f，聚焦四条部分采纳的复辩 + 闸门排期 +
> RS 工单验收范围 + A4 修复代码复查）。
> 结论先行：**25 条全部采纳（其中 3 条为"接受对方坚持/修正我方原判"）**，无驳回。
> 双方在 round-1 的 4 条分歧全部收敛。评审指出我 A4 修复的 **3 个实质缺口全部属实，
> 当日修复**（见文末）。

## 分歧收敛记录

| 争点 | Round-1 双方立场 | Round-2 收敛结果 |
|---|---|---|
| A2 FORCE RLS | 我：加上无害 | **接受修正**：不是"无害"——FORCE 后 migrator 的数据回填可能静默更新零行。RS-01 验收补：四角色读写矩阵 / 含回填的 migration 实跑 / 分区逐一核查 |
| A5 毒任务隔离 | 我：全部挂多节点闸门 | **接受对方坚持**：毒任务也来自单节点 parser 缺陷/异常 payload，与节点数无关。RS-07 现阶段即含：服务端 payload schema/尺寸校验、permanent/transient/poison 三类终态、poison 进 quarantine 不 upsert product、token 撤销/轮换、人工查看+显式重放。mTLS/公平队列维持多节点闸门 |
| B5 断言账本 | 我：分级按域启用 | 对方接受，但加硬验收：**RS-04 不得只交付空框架**，必须在 blacklist_brand 真实跑通（manual+TRO+trademark 三源断言 / 撤销一源不误删仍有效证据的品牌 / 人工裁决可覆盖+可回滚 / canonical 可全量重建一致） |
| B8 写栅栏 | 我：脚本域很便宜 | 对方接受，但"便宜≠口头停"：每个轻量域 cutover 需留证据（调度禁用记录 / 最后 cursor+源快照 hash / 最终对账 / 晚到编辑进 quarantine 不覆盖 canonical） |

## 闸门排期修正（R2-06，全盘采纳——这条评审抓得准）

我 round-1 的闸门定义有两个漏洞，对方指出后核实属实：

1. **audit 已经在持锁调真实 LLM**——R2-02 的 ≥100 ASIN 真实对拍就是现实暴露，不能等
   A152。**RS-03 拆双闸门**：`RS-03a audit 异步化` 前置到 R2-02 百件对拍之前；
   `RS-03b channel outbox` 维持 A152-L2 写入前。
2. **坏响应缓存已在线生效**——不是"将来的问题"。已从 RS-09 拆出**当日修复**（见下）。

RS-01/02 闸门改为**机器可判定事件**（任一发生前必须完成）：创建第二团队 / 启用第二内部
用户 / API 绑定非 loopback / 门户路由启用。依赖关系已写入 `.agent/review_list.json` 的
`gate`/`depends_on` 字段，不再只是 finding 里的文字。

**修正后的实际顺序**：A4 尾项（✅当日完成）→ RS-04 摄取升级 → RS-03a audit 异步 →
R2-02 百件对拍 → RS-03b channel outbox → A152 → RS-01/02（多人/LAN 经营硬闸门）。

## RS 工单验收范围复核（R2-07~R2-18，全部采纳）

通用问题成立：round-1 的 check 是范围摘要不是可验收任务卡。已按评审逐单补齐
（详见 review_list.json 各单 `acceptance` 字段），要点：

- **RS-01**：+四角色（erp_app/erp_system/erp_migrator/超管）读写矩阵、含回填 migration
  实跑、分区 RLS 核查、SECURITY DEFINER 通道、门户角色、后台任务越权测试
- **RS-02**：+refresh token → HttpOnly Secure cookie + CSP/CSRF；备份/迁云范围含非 DB
  文件（payload_ref/邮件正文/导入报告）；恢复演练=PG+文件+密钥角色配置在目标 RTO 内起服务
- **RS-03**：+幂等重放（同 key 同 payload 同结果、异 payload 409）、"外部已成功回写前
  崩溃→verify-back 不重复提交"故障注入、lease/fencing token、同 store/SKU 命令有序、
  HTTP 期间行锁已释放的实证、outbox payload 脱敏
- **RS-04**：拆四子单——**RS-04A** COPY/staging/merge/manifest/断点续跑；**RS-04B**
  复合游标/tombstone/周期对账/cutover 状态机（承接 B1+B8，原漏注册）；**RS-04C** 飞书
  UUID/revision/字段 owner/CAS/quarantine/writeback ledger（承接 B2，原漏注册）；
  **RS-04D** assertion ledger + blacklist_brand 真实域验收（承接 B5）。容量验收：先 100 万行
  演练（吞吐/内存/WAL/索引膨胀/中断续跑/重复导入），再 14M 实测；**磁盘/WAL/备份峰值
  容量预算移到 RS-04 前置**（采纳 R2-17：不能等"大数据迁入前"，14M 马上就来）
- **RS-05**：+observation 绝不直接 effective、双审不得同一账号、shadow/canary 样本量与
  误杀阈值、规则撤销后缓存即时失效
- **RS-06**：+并发 claim 唯一成功、裁决带理由与 version、撤销生成新 decision 不覆盖、
  待办箱不反向改域状态
- **RS-07**：+inflight 由服务端租约事实计算（不信 worker 自报）、count=100/capacity=2
  只领 2、kind 不匹配领不到、续租与回收并发 fencing
- **RS-08**：+并发总预留不超预算、provider 已收费本地超时→成本待核对、记
  provider/request_id/retry/latency/error、团队 key 加密跨团队不可读
- **RS-09**：坏缓存部分已提前完成（本日）；manifest 补 raw_response_hash/coerce_version/
  dataset revisions/provider request id/代码 commit
- **RS-10**：容量模型部分移入 RS-04 前置；keyset API 维持 P2
- **RS-11**：验收=CI 自动校验（OpenAPI operation ↔ 实际路由 ↔ x-permission ↔ permission
  seed 四向），不只做文档清单；pipeline.py 过期注释已随本轮修正

## A4 修复复查（R2-19~R2-25）——3 个实质缺口，当日全部修复

| # | 评审发现 | 核实 | 处置 |
|---|---|---|---|
| R2-19 | 0012 基本通过；downgrade 不可逆需提示；百万行后 CHECK 变更用 NOT VALID→VALIDATE | 属实 | 迁移补不可逆注释；NOT VALID 纪律记入 00-conventions（随 RS-11） |
| R2-20 | **transport/config 仍 fail-open**：chat() 异常整体回滚，audit_run 无影无踪；policy 缺失/被禁静默 pass | ✅ 属实（P0） | **已修**：chat 异常兜底→落 `llm_unavailable` 软命中+needs_review（超时/HTTP 非200/缺字段/缺 key 全覆盖）；policy 缺失→`l3_policy_missing`+needs_review（区分"未请求 L3"与"请求了但配置缺失"） |
| R2-21 | **坏响应先入缓存**：重审永久复放 needs_review 无法自愈 | ✅ 属实（P0） | **已修**：`chat()` 增 `cacheable` 业务校验谓词（JSON dict+合法 verdict 才入缓存）；命中存量坏行→DELETE 驱逐+走真调用（自愈）；0013 迁移补 erp_app 的 llm_cache DELETE 授权 |
| R2-22 | 顶层 verdict 非法时嵌套 is_real_brand 仍翻案硬拒——结构异常响应的嵌套字段不可信，且误升 reject 会污染反馈闭环（B3） | ✅ 属实，**收回 round-1 的写法** | **已修**：翻案仅限 verdict 合法为 pass 的响应；非法响应整体保持 needs_review，嵌套字段仅作人工参考。对应测试预期已改 |
| R2-23 | needs_review 复用 reject_level='l3' 污染"首个否决层"语义 | ✅ 属实 | **已修**：needs_review 时 reject_level=NULL；层级信息由 llm_needs_review/llm_unavailable/l3_policy_missing 命中行承载。暂不加 decision_level 列（避免半用列），待 RS-06 复核模型统一定 |
| R2-24 | 前端把一切非 pass 显示"❌ 拒绝" | ✅ 属实 | **已修**：三态（✅ 通过 / ❌ 拒绝 / 🟠 待人工复核），详情页判定 Tag 同步三色 |
| R2-25 | 测试不足以宣布 A4 闭环 | 属实 | **已补**：provider 500 落痕、policy 禁用、坏响应不缓存+重审自愈（calls=2 实证）、存量坏行驱逐、非法 verdict 不翻案——后端 126 全绿。缺 API key 场景与 transport 500 走同一 except 路径，不单列用例 |

一处测试工程记录：NR 系列用例最初共享默认 title，而缓存键不含 ASIN（user prompt 只有
brand/title/bullets），前例缓存的 pass 响应短路了 500 用例——各用例已给独立 title。
这也侧面验证了缓存键设计"不缓存产品，缓存输入"的语义边界。

## A4 状态声明

按评审 R2-25 的口径：round-1 后为"核心路径已修、尾项未闭环"；本轮 R2-20/21/22/23/24
全部落地后，**A4 转为待 round-3 复核关闭**。请评审方复查 `995074f..HEAD` 的
`llm.py / pipeline.py / service.py / 0013 / ProductsPage.tsx / test_audit_api.py`。
