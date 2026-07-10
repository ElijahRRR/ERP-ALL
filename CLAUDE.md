# ERP-ALL 项目指南（Agent 必读）

## 铁律

1. **specs/000-founding/ 是宪法**：DECISION-FORM.md（50 项决策）> PRD-v1.md > business-rules-ledger.md。任何实现与之冲突：改代码或经 Owner 批准改文档，不许静默偏离。
2. **workflow 模式**：任务状态在 `.agent/`（task/review_list/progress/handoff）。每个工单一个原子目标；产出必须回写工单状态；无工单的改动不合并。
3. **角色制**：`.claude/agents/` 定义六角色的管辖目录与禁区。跨界需求提单给对应 owner，不许顺手改。
4. **验证纪律**（D-Q37 稳定优先）：每个增量必须 CI 绿 + dry-run 证据（渠道写路径）+ A152 实测/影子对拍 才算完成。测试验收店 = A152。
5. **禁区**：不写死业务参数（一律配置中心）；不引入 SQLite 到生产路径；不绕过 walmart_client 直连渠道 API；凭证只走加密存储。

## 关键决策速查

- 多租户：资源默认 team_id 隔离，共享开关仅超管（D-Q30）；去重键 (team_id, asin) + 店铺豁免（D-Q31）
- SKU：master_sku=M{seq} 渠道中立；渠道 SKU 默认=master_sku（D1/D-Q2）
- listing 单管道双模式 offer_mode ∈ {build, match}；定价策略按模式分套（D-Q3/23）
- 采购执行双入口：内部权限点 + 外部隔离门户（D-Q50）
- 自动化三档：人工/半自动/全自动，流程级开关（D-Q13/29）
