# R2 计划 v2 — 真实能力灌入（验收与进度对齐重排版，待 Owner 确认排期）

> 依据 D-Q54：Owner 验收实测揭示 R1 验收判据与开发进度错位（骨架单配了真实验收）。
> 本版为重排基线：每单标注**数据真实性等级**，验收方式与等级绑定。

## 数据真实性等级（每单必标，验收必须匹配）

| 等级 | 含义 | 验收允许的方式 |
|---|---|---|
| **L0 模拟** | 协议/状态机/管道骨架，数据用替身 | 模拟数据全链 + 状态断言 + 失败路径 |
| **L1 真实只读** | 接真实数据源但只读（导入/抓取/查询） | 真实数据入库量、字段完整率、与旧系统对照一致率 |
| **L2 真实写-测试店** | 真实渠道写操作，仅 is_test 店（A152） | 渠道后台可见 + 回写闭环 + 收尾清理 |
| **L3 真实写-正式店** | 正式店真实经营写入 | 灰度放量 + 人工复核期 + 回滚预案 |

**R1 复盘定级**：全部工单 = L0（这是立项定义，无偏差）；偏差在于 R1-11/12 的验收判据
写入了 L2 动作（A152 真调）——已修正，R1 验收=L0 骨架验收（见 evidence/R1-12/owner-acceptance-runbook.md v2）。

## R2 工单序列（前三单 = Owner 实测三缺口，一一对应）

### R2-01 采集引擎移植【L1】← 解决"任务卡 pending"
- 移植 amazon-scraper-v3 worker 引擎（curl_cffi TLS 指纹 / AIMD 自适应并发 / session 池
  + prefer_zip 亲和 / 批量回传）到 `workers/`，打包为 Owner 机器可跑的本地 worker（拨入现有协议）。
- 需要：TPS 代理（Owner 提供，同旧系统）。
- **验收（L1）**：Owner 机器 worker 真抓 ≥10 个真实 ASIN → 产品库字段完整
  （标题/品牌/图片/五点/价格）；对照旧系统同 ASIN 抓取结果字段一致；失败任务正确回收重试。

### R2-02 审核弹药灌入【L1】← 解决"审核缺数据"
- 数据导入（import_job 通道，Owner 机执行）：黑名单四表全量（飞书 3.6 万）、
  refdata.trademark 14.18M（uspto 库→部署机 PG）、37 条政策种子（lark OJSrkV）、
  pt_embeddings 6832（L1 检索用）。
- 代码补齐：L1 类目判定（混合检索+LLM 复排）、L2 R1/R2/R3/R7/R8、Aho-Corasick、
  黑名单内存字典加载器（版本失效）。
- **验收（L1）**：取旧系统（walmart-audit-system 已全量跑过 4326 ASIN）子集 ≥100 个
  重跑新系统 → verdict 一致率 ≥90%（差异逐条归因）；成本记账与缓存命中复核。

### R2-03 上架真实化【L2】← 解决"spec 骨架撑不起真上架"
- spec 构建器真实化：接 MPSetup v5 官方规格（按 WPT 取必填属性 schema）+ AI 属性填写
  （走 llm_cache/usage_log 既有记账）+ category_map 导入（6672+15771 映射）+ 提交前
  本地 schema 校验器；listing_error_catalog 灌入渠道实战错误码。
- **验收（L2，分两档）**：①dry-run 产物通过官方 spec 校验（≥5 个不同 WPT 的产品）；
  ②A152 真调 1 SKU → PROCESSED → live 回写 → Walmart 后台截图 → delist 收尾
  （即原 R1-11 尾巴，正式挂在此处）。

### R2-04 worker/beat 底座【L0→L1】
- beat 读 schedule 表驱动：feed 自动轮询、采集回收兜底、llm_cache LRU、GTIN 水位、预算闸；
  Redis pubsub 配置广播；compose worker/beat 角色启用。
- **验收**：无人工点查——A152 提交后自动轮询回写；模拟断连自动回收。

### R2-05 订单履约最小闭环【L1→L2】
- 订单拉取（真实只读起步）→ 四检（限价/钓鱼/黑名单/重复）→ 采购单 → 确认发货（测试单）。
- **验收（L1）**：真实订单只读拉取入库对账一致；（L2）测试单全流程流转。

### 后续（顺序待 Owner 定）
邮件域 / 定价引擎（cost_plus+min_price）/ automation 三档面板 / 选品关键词・卖家入口 /
portal+共享域 / 前端设计工单（D-Q53）/ 云迁移评估（D-Q52, R2#6 检查点）。

## R1 遗留欠账归属

- A152 真调 → R2-03 验收②；sim_worker 保留为管道自检工具（明确标注模拟）。
- 采集三入口(keyword/seller/bestseller)展开 → R2-01 或选品单。
- refdata/黑名单导入器（import_job 通道实现）→ R2-02 的一部分。
