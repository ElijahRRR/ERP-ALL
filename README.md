# ERP-ALL — 多团队跨境电商 ERP

从零构建的新一代 ERP：选品采集 → 合规审核 → 货源匹配 → 上架 → 在架维护 → 订单履约 → 售后 → 财务复盘。
首发渠道 Walmart US；多租户（团队隔离 + RBAC + 审计）；自动化三档可切换。

## 创始文档（specs/000-founding/，开工前必读）

| 文档 | 内容 |
|---|---|
| `PRD-v1.md` | 产品定义：范围/角色/领域模型/流程/NFR/迭代切分（已验收） |
| `business-rules-ledger.md` | 业务规则总账 v1.2：23 域 187 条规则（唯一需求基准） |
| `DECISION-FORM.md` | 决策日志：Owner 已拍板的 50 项决策（最高效力） |
| `data-survey/` | 数据资产调研（结构/规模/样例）+ SYNTHESIS 综合结论 |
| `PRODUCT-TEAM.md` / `TEAM.md` | 职能分工与 agent 执行编制 |

## 技术栈（D-Q46~49）

Python 3.11+ / FastAPI · PostgreSQL（分区+团队隔离）· Redis · Vite+React+TS+AntD（中文）
部署：阿里云（DB 与应用分离）；采集 worker 本地拨入。

## 移植白名单（源仓均已考古，erp-core 内嵌副本弃用）

采集 = [amazon-scraper-v3](https://github.com/ElijahRRR/amazon-scraper-v3) ·
审核 L0-L4 = [walmart-audit-system](https://github.com/ElijahRRR/walmart-audit-system) ·
渠道网关 = erpAPI walmart_client+GCRA · GTIN 生成器（UPC-A/EAN-13）

## 历史

R0 需求阶段的完整过程（调研/考古/决策）见 [erpAPI PR #1](https://github.com/ElijahRRR/erpAPI/pull/1)。
erpAPI 仓从此定位为「考古档案 + 旧系统运行仓」。
