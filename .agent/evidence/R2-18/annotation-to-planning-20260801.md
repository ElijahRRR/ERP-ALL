# 批注回传：D-Q74 落地后仍需规划侧/Owner 落笔的三处（2026-08-01，R2-18）

> 云端侧无权改 `specs/007-*` 与 `specs/000-founding/`，故以下三项以批注回传形式挂账，
> 请规划/审查 AI 落笔（第 1 项）、Owner 批准后落笔（第 2 项）、Owner 裁决（第 3 项）。
> 云端写权范围内的同类冲突（CLAUDE.md、`.agent/task.md`、`.agent/evidence/reviews/README.md`、
> `infra/local-deploy/README.md`、`.claude/agents/qa.md`）已在 R2-18 落地 PR 内消解。

## 1. specs/007「合并前闸序」未标分级适用（规划侧落笔即可）

007 §角色分工下的「**合并前闸序（2026-07-26 起）**：CI 绿 → 审查 AI 通读 diff →
部署机真机验证 → Owner 授权合并」仍是无条件版。009 卷首只声明「与 CLAUDE.md 冲突以
本文为准」，**没有声明压过 007**，而 CLAUDE.md 协作分工表头又写着「本表若与 007 冲突，
以 007 为准」——连读之下 L0/L1 的减免在 007 面前无据。建议在该句加一处
「2026-08-01 起按 D-Q74（specs/009-process-lite）分级适用——L2 走全套，Owner 授权
合并一律不减免」。

## 2. specs/000-founding 两处旧表述与 D-Q74 分级不一致（需 Owner 批准改宪法）

- `PRODUCT-TEAM.md` §3.1：「所有工作以工单存在……**没有工单的改动不合并**」——与 009 §三.1
  「半天以内的活免立单」冲突。
- `TEAM.md` T5：「为**每条合并**做对抗式 code-review」；§3.4 质量门禁第 5 条「review_list
  状态与 progress.md 会话记录已更新」（缺一不合并）——与 009 §二.1（L0 不审）、§三.1
  （免立单的活台账不回写）冲突。

按铁律 1，实现与宪法冲突须「改代码或经 Owner 批准改文档」。D-Q74 已是 Owner 批准的决策
（DECISION-FORM），故属后者：请 Owner 授权在上述两处各加一行「已由 D-Q74 分级取代，按级
适用」，由规划侧落笔。§3.4 的第 2 条（渠道写路径 dry-run）与第 1/3/4 条不受影响、原样保留。

## 3. 治理类文档的判级例外（提请 Owner 裁决，勿由 AI 自行拍板）

009 §一把「docs / specs」整体划入 L0，而保险丝的载体（CLAUDE.md、specs/000-founding/、
specs/009 本身）恰恰都是 docs——照字面，**修改保险丝条文或宪法的 PR 也可走
「CI 绿即合、不审查」**。当前有 Owner 合并闸兜底，不构成即刻风险，但审查闸在这类 PR 上
缺位。提请 Owner 裁一句：治理类文档（CLAUDE.md / specs/000-founding/ / specs/009）是否
例外为 **L1（需一轮审查）**。裁定后由规划侧落笔到 009 §一，云端侧再同步 CLAUDE.md。
