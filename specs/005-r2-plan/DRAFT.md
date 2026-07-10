# R2 规划草案（待 Owner 排期确认——R1 验收通过后转正式工单）

> 输入：PRD §7 路线图 + R1 各单考古档案的「R2 欠账」段 + 部署实战遗留。
> 原则不变：考古移植优先、契约先行、每单带验收判据。

## A. R1 欠账（技术债，按依赖顺序）

### A1 平台底座
- **worker/beat 进程角色**：compose 已预留注释；beat 读 schedule 表驱动
  （feed 轮询自动化、采集任务回收兜底、llm_cache LRU 清理、GTIN 水位告警、预算闸）
- Redis pubsub 配置失效广播（ConfigService 已留口）；配置管理 UI（system/team_config）
- 部署 workflow（self-hosted runner：CI 绿→部署机自动拉新重启）——runner 待 Owner 注册
- 前端补齐：GTIN 池页、密码自改、铃铛已读即时刷新、审计日志过滤增强

### A2 采集域（R1-09 欠账）
- v3 worker 引擎移植（curl_cffi TLS 指纹/AIMD 自适应并发/session 池+prefer_zip 亲和）
  ——Owner 本地 worker 部署包（workers/ 目录）
- 批量回传端点、auto_retry 轮次策略、keyword/seller/bestseller 三入口展开

### A3 审核域（R1-10 欠账）
- L1 类目判定（pt_embeddings 6832 + 混合检索 + LLM 复排）→ 解锁 L2 R1/R2/R3
- 37 条政策全量种子（lark-cli 拉 OJSrkV）+ L2 R7/R8 + L4 视觉（占位已留）
- refdata.trademark 全量导入 14.18M（Owner 机执行，import_job 通道）+ Nice Class 过滤
- Aho-Corasick 替换 regex 扫描；黑名单内存字典加载器（版本失效）

### A4 上架域（R1-11 欠账）
- match 跟卖模式 spec 构建器 + pricing_strategy 引擎（cost_plus/manual + min_price 硬底线）
- category_map 域（6672+15771 映射导入）+ WPT 自动映射（替代 attrs.wpt 手填）
- maintenance_task 调度产出（盯价 1 次/日可配 D-Q26、end_date 续期、unlock_probe）
- price feed 聚合（6/day 铁律——按店按日窗口合并）+ price_history

## B. 新领域（PRD 大块，需 Owner 定优先级）

| 候选 | 内容 | 依赖 |
|---|---|---|
| B1 订单履约闭环 | 订单拉取/四检(限价/钓鱼/黑名单/重复)/确认发货 + 采购单 + portal(独立认证域) | 渠道网关✅ |
| B2 选品完整入口 | 关键词/卖家/榜单采集 + 变体组 + 选品工作台 UI | A2 |
| B3 邮件域 | 店铺邮箱收发聚合(D-Q22)、分类规则、正文 30 天保留 | 独立 |
| B4 automation_policy 三档面板 | audit_to_listing / pricing_watch 等 flow 的 manual/semi/auto | A1 beat |
| B5 共享域 + portal_account | shared_resource 只读共享(D-Q30) + 采购方账号(R2#6) | B1 |
| B6 云端迁移评估 | 试点数据量/团队规模复核 D-Q52（本地→云 or 续本地） | 试点运行 1-2 周 |

## C. 建议的 R2 第一批（我的推荐，Owner 可改）

**R2-01 worker/beat 底座(A1) → R2-02 订单履约闭环(B1, 营收核心) → R2-03 采集引擎+选品入口(A2+B2) → R2-04 审核 L1+全量政策(A3) → R2-05 定价引擎+维护调度(A4)**

理由：订单是唯一直接产生营收的域且完全未建；其余按"让已建域从最小闭环长成可日用"排。
