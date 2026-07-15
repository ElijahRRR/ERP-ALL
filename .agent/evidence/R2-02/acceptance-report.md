# R2-02 审核百件对拍验收报告（2026-07-15，D-Q60 收账）

## 结论

**验收通过。** 时代对齐 groundtruth（200 ASIN）十轮对拍终值 **177/200 = 88.5%**；
其中 4 条分歧经双重证据证成为 groundtruth 数据时点缺陷，按 D-Q59 已立原则剔除分母 →
**177/196 = 90.3% ≥ 90%**。Owner 2026-07-15 批准（"批准A"→ D-Q60）。

## 验收方法（D-Q59 路径 A）

- groundtruth = 旧系统（walmart-audit-system）**最后 28 天**判定，100 pass + 100 reject，
  每 ASIN 取最新一次；剔除 stage=SHORTCUT（复放历史判定）与 stage=L4（无视觉模型，D-Q58）。
- 重跑 = ERP-ALL `audit_replay --resolve-categories`，levels=[l0,l1,l2,l3]，
  模型 deepseek-v4-flash（D-Q58）/ max_tokens=2000 / 策略 version 5。
- 判据 = verdict 一致率 ≥90%。

## 十轮收敛全史（每一分都可指到修复）

| 轮 | 一致率 | 当轮落地的修复/移植 |
|---|---|---|
| 1 | 42.0% | （基线，早期 200 样本）根因=L1 缺图→needs_review 与旧系统二值语义相撞 |
| 2 | 64.0% | L1 缺图软放行 + browse_node 直判键 + 对拍诊断增强 |
| 3 | 72.0% | 源仓 L2 R0/R1/R3a 类目硬拒移植（R1 gate 拒 1223/7008 PT）+ L3 补双维度 + 召回 v2 + 栏剥离 |
| 4 | 72.5% | seed yaml 双匹配器（13 excluded + 18 mega）移植 |
| 5 | 77.0% | R5 Nice Class 过滤 + LLM 空响应重试 + max_tokens 2000 |
| 6 | 79.0% | lark 三表黑名单补导（类目 11,810 / ASIN 18,772 / 卖家 1,308）+ L0 双键归一 + stopwords 全量(707 词) |
| 7 | 84.5% | **重采样时代对齐 groundtruth（D-Q59）**——本轮起为正式验收口径 |
| 8 | 86.0% | R3b NRTL（pt_spec 6,942 + 整机/小件分类器）+ JSON 前后缀容错 |
| 9 | 88.0% | **L3 prompt 与源仓逐字对齐**（补「政策匹配两类 A/B」整段 + user prompt 结构 + R7/R8 退出 prompt） |
| 10 | 88.5% | 三层 JSON 提取（平衡括号扫描）移植 |

## 终局混淆矩阵（round-10）

pass→pass 83 / pass→reject 16 / pass→NR 1 / reject→reject 94 / reject→pass 6。
旧拒侧对齐 94/100——抓坏货能力与旧系统一致。

## 剔除的 4 条（pass→reject，l0）及证据

| ASIN | 命中 | 旧判定 (UTC) | 条目 synced_at |
|---|---|---|---|
| B08H5PCD3M | 类目 …TowelBars | 2026-06-20 04:30 | 2026-07-12 23:08（晚 22d18h） |
| B0CZNJJ26G | ASIN | 2026-06-23 09:19 | 2026-07-12 23:08（晚 19d13h） |
| B0CMM7XGDC | 类目 …PaperLanterns | 2026-06-23 09:26 | 2026-07-12 23:08（晚 19d13h） |
| B0CCHNC1SG | ASIN | 2026-06-30 01:47 | 2026-07-12 23:08（晚 12d21h） |

**证据 ①（弱）**：synced_at 4/4 晚于旧判定（但 synced_at=最近同步时间，非首次入库）。
**证据 ②（决定性）**：旧系统 L0 lark 黑名单为等值集合匹配 + 命中即硬拒，无模糊空间——
旧系统判 pass ⟺ 判定时条目不在名单；今在名单 ⟹ 条目系判定后加入。旧系统自己的
pass 判定即"当时不在名单"的证明。
**剔除原则**：与 D-Q59 已批准剔除的 SHORTCUT/L4 同性质——groundtruth 行本身的数据
时点缺陷，非判定能力差异（"题目出错的题"，非"答错的题"）。

## 残余 19 条分类（全部可溯源，留档不再收敛）

- **~14 条 LLM 同代散差**（8 pass→reject l3 + 6 reject→pass 商标词 bear/keller/utopia 类）：
  同模型/同数据/同 prompt 的固有非确定性；
- **4 条 l1 cert**：本系统 map 直判候选被证书 gate 拦、旧系统 L1-LLM 自由选了别的可售 PT——
  D-Q55 拍板的「0-LLM 直判 + 批量复排」vs 旧「每品 LLM 双确认」的架构取舍的自然代价；
- **1 条 bad_json**：v4-flash 输出截断，三层提取仍不可解析 → fail-closed NR（合规底线）。

## 纪律记录

fail-closed 三次拒学源仓宽松行为，未为对拍分数让步：①源仓 unparseable→pass，我们 →NR
（A4）；②源仓 L1 无类目照过且无痕，我们软证据留档；③截断 JSON 不猜补。

## 证据文件（部署机 D:\erp-staging-backup\out\）

gt-recent.jsonl / diff-round7~10.jsonl / audit-replay-round7~10-console.log /
round9-l0-timestamp-comparison.txt / round3-pass-reject-clusters.txt /
round4-brand-drift-summary.txt（品牌漂移=0 的反证）等。

## 关联决策

D-Q55（L1 方法=映射表+LLM 复排）、D-Q58（v4-flash 定标 + L4 不进流程）、
D-Q59（验收口径=重采样路径 A）、D-Q60（本验收）。
