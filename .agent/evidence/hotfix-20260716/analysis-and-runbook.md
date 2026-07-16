# 2026-07-16 生产三缺陷整改（HF-0716）

Owner 报告：①ERP 无订单页；②采集器卡住（未真抓、无审核输入）；③真实 UPC 上架失败，
Walmart 后台报「数据错误：Invalid Date」（feed #36 / listing #46 M0002418）。

## 原因分析

### ① 无订单页 —— 非缺陷，部署滞后 + 部署指令缺项
订单页代码只存在于 R2-05（main `543685d`，2026-07-16 合并）；报告时部署机仍在
前一版 `0d1b3aa`（PR#6 后），必然无订单页。次因：此前部署指令只重建
`db redis migrate api beat`，未提前端（frontend 服务是 dev-profile vite bind-mount，
检出新代码即热更，但需确认容器在跑 + 浏览器强刷）。权限面无问题
（超管 `has()` 旁路；order.read/procurement.read 0002 已种）。

### ② 采集器卡住 —— 双层结构性缺陷（代码审计确认，最终卡点需部署机取证）
**A. beat 层**：单进程串行 `await` 循环，任务全链无超时
（beat.py tick 顺序执行 / run_tracked 无 wait_for / httpx 虽有四相超时但无第二道防线）。
任何一个渠道 HTTP/DB 调用挂起 ⇒ 整个 beat 冻结 ⇒ 全部周期任务停摆
（含 scrape_reclaim/feed_poll/order_pull）。领取语义为先推 next_run_at 后执行，
挂起那轮丢失且 task_run 停 running 永不收尾。
**B. 采集管线层**：无任何采集侧告警与整单收口——
- worker 不在线（profile 门控/enroll token 空/代理缺失 fail-closed 崩溃重启环）时，
  job 永停 pending，UI 只见 0% 进度条，无告警；
- worker 反复中途死亡时任务在 pending↔dispatched 乒乓（attempt 派发即增，
  终态只在 submit_result 产生），永不 dead、job 永不收口；
- reclaim 只动 task 不结算 job；前端无 worker 节点健康可见性。

### ③ 上架失败 —— 【归因落定 2026-07-16 部署机取证】渠道拒的是 CAP 零价格，非日期

**取证结论**（feed_item.raw，feed #36 / M0002418）：
`EXT_DATA_ERROR_66685355746773`，type=DATA_ERROR，**field="CAP"，description="Invalid Data."**
——Seller Center 显示的「Invalid Date」实为「Invalid Data」。CAP = Competitive Price
Adjustment（渠道定价子系统）。提交体 `"price": 0.0`：该产品 price_snapshot 无 list 价
→ allocate 落 current_price=NULL → 构建器 `price if price is not None else 0.0` 兜底
0 价出门 → 定价子系统拒。两份 feed_submit 提交体均为 0 价（M0000009 同病）。

**修复**：validator 实践层拦 price ≤ 0/缺失（本地拒，引用真机错误码）；错误码入
listing_error_catalog（定价/manual）；新增 `PATCH /listings/{id}` 改价端点
（draft/failed 专用，在架调价仍归定价管道）+ 上架管理页「改价」入口与「无价」红标。
**listing #46 自救路径**：改价 → 重投（retry）。

### ③-附 日期格式加固（保留——按原始报错方向做的排查发现的真实缺口）
- schema 的 `format: date/date-time` 在 FieldSpec 摘要即被丢弃：LLM prompt 不知格式、
  coerce 链无日期步骤、必填兜底会把 `"Not Available"` 塞进日期字段、
  validator 仅查 endDate 含 "T"（连 `{END_DATE}` 占位符都能过）。
  LLM 填的 `"2024"`/`"March 2024"`/`"N/A"` 类值原样直达渠道。
- endDate 组合风险：D-Q9 远期 2049 + 无毫秒 `T00:00:00Z` 从未实测；
  旧仓 2049 唯一成功写法带毫秒 `.000Z`（BR-RET-007，2026-05-08 校正：
  `'2049-12-31' is not a valid format` 被拒、`2049-12-31T00:00:00.000Z` 被收）；
  官方 OpenAPI 示例亦用 `2049-12-31T08:00:00Z`/2055——2049 年份本身无被拒证据。
- 具体拒收字段名已在部署机 DB（feed_item.error_msg/raw 存了渠道完整 ingestion 错误），
  见下方取证 SQL。

## 修复清单（本次提交）

| # | 修复 | 位置 |
|---|---|---|
| 1 | beat 任务级硬超时：schedule.config.task_timeout_seconds > system_config beat.task_timeout_seconds > 默认 900s；超时=失败记账+告警，循环继续 | automation/beat.py, task_runner.py |
| 2 | beat 启动回收崩溃遗留 running task_run（STALE_RUNNING） | task_runner.reclaim_stale_runs |
| 3 | scrape reclaim：attempt≥max_attempts 判 dead+计 failed_tasks+job 收口（乒乓终结） | scrape/service.py |
| 4 | scrape_reclaim beat 任务扩展：running job 收口结算兜底 + 无在线 worker 告警（critical，宽限默认 10min）+ 零进展 job 告警（warn，默认 60min；均 schedule.config 可调） | automation/tasks.py |
| 5 | 采集作业页顶部「采集节点健康」横幅（在线数/0 在线红色警示；无权限静默隐藏） | frontend ScrapeJobsPage |
| 6 | FieldSpec 透出 format；LLM prompt 对日期字段加格式指令（禁 N/A/纯年份，不确定则省略） | wpt_schema.py, attr_fill.py |
| 7 | coerce 新增 fix_date_formats：可解析→规范化（date=YYYY-MM-DD；date-time 纯日期提升 .000Z），不可解析→可选删/必填留给校验器；日期字段禁 'Not Available' 兜底臆造 | coerce.py |
| 8 | validator：format=date/date-time 真解析校验、startDate 纳入、占位符泄漏由跳过改报错 | validator.py |
| 9 | endDate 输出 `.000Z` 毫秒写法（2049 唯一实测成功组合）；远期默认值入配置 listing.orderable_defaults.end_date_default（默认 2049-12-31，D-Q9 不变） | spec.py |

### ②-附 取证结论（2026-07-16 部署机回报）
- beat 健康（持续 tick、任务全 done）——本次卡点**不是** beat 停摆；超时护栏留作预防。
- 卡点 = **scraper 容器已停**（末次日志/心跳约 14.5h 前，job #9 建单前 4 分钟），
  8 任务 pending 无人领、无告警（本次修复的双告警 + UI 横幅正是对症）。
- 恢复：`--profile scraper up -d scraper`（enroll token/代理配置已在库，无需变更）。
- 前端「无订单页」补充确诊：容器磁盘已是新码但 vite 进程内存缓存旧模块
  （`up -d` 对运行中容器是 no-op）——需 `--force-recreate frontend`。

## 部署机指令·第二轮（升级+恢复，可整段粘贴；取证已完成不再重复）

```
【铁律】绝不操作生产库 erp_all 的结构；暂存用一次性容器；用毕清理；不输出密钥。

任务：升级到 HF-0716 版本并恢复前端/采集，回报核验结果。

1) 升级（<SHA> 用云端提供的合并后 main SHA）：
   git fetch origin main && git checkout <SHA>
   docker compose -f infra/docker-compose.yml up -d --build db redis migrate api beat
2) 前端强制重建（vite 内存缓存旧模块，up -d 对运行中容器是 no-op）：
   docker compose -f infra/docker-compose.yml --profile dev up -d --force-recreate frontend
3) 恢复采集 worker（配置已在库，无需变更）：
   docker compose -f infra/docker-compose.yml --profile scraper up -d --build scraper
4) 核验：
   - 浏览器 Ctrl+F5 后左侧菜单出现「订单管理/采购方」；采集作业页顶部出现节点健康横幅；
   - scraper 起后 job #9 的 8 个 pending 任务开始被领取（进度条动起来）；
   - beat 日志 beat.start + 每轮 beat.tick 正常（新版含任务超时护栏与启动回收）；
   - 上架管理页 listing #46（M0002418）：价格列有红色「无价」标 →「改价」填真实售价
     →「重试」重投 → 新 feed 应通过 ingestion（此前 field=CAP 拒收即 0 价所致）。
5) 回报：4 的各项结果 + 新 feed 的 ingestion 结果；通知中心如出现「采集停摆」类
   告警属新增预期行为，一并回报内容。
```

## Owner 侧后续

1. listing #46 归因已闭环（CAP=0 价）：部署后改价重投即可；M0000009 同病同治。
2. 采集 job #9 会随 scraper 恢复自动推进；若不需要该测试作业可直接取消。
3. R2-05 L1/L2 验收流程不变（evidence/R2-05/runbook.md），新版本部署后进行。

## 部署核验记录（2026-07-16，部署机回报）

HEAD `17f1a6d`：API healthz 200 / migrate 0 / frontend·beat·scraper 运行中；订单页可见；
采集页「节点在线 1 个」；作业 #9 done 8/8；beat tick 每分钟 failed=0；listing #46 改价
39.99 → retry → feed #37（提交 14:23:44，自动轮询中，未复现 CAP 拒收）；停摆双告警为
scraper 恢复前的预期存量。**部署惯例**：scraper 的 PROXY_URL 经进程环境注入（本机密码
文件），不入库明文；无代理时 PROXY_REQUIRED 崩溃属 fail-closed 预期（店铺关联风险防线）。
