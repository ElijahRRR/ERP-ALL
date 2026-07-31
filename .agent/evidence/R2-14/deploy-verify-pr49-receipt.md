# PR #49 第三闸真机验证回执（R2-14 14b 主体删除 + deleted_principal 墓碑）

> **结论：通过。** 部署机在 Win11 部署机执行 `.agent/evidence/R2-14/deploy-verify-pr49.md`
> 全部步骤 ①–⑫，逐条判据均满足。本文是该次验证的取证留档（原文见 PR #49 两条部署机
> 评论：⑧-3 停点回执 + 续跑完成回执）。
>
> - **被验代码产物：`b97318c`**（后续 `1d89fff` 只动指令文件，产品代码零变更）
> - **指令最终版本：`1d89fff`**（v6；v5 跑到 ⑧-3 首停一次，见 §三）
> - CI：`b97318c` 四 job 全绿（run `30596714932`）
> - 收尾现场：留在验证分支、库 `alembic_version=0047`，等 Owner ④ 合并指令后收尾

---

## 一、逐条判据结果

| 步骤 | 内容 | 结果 |
|---|---|---|
| ① | 前置 + 基线：`tracked_dirty=0`；audit=199、po_rows=1、po 指纹 `d0bd571d…`、users=5 / roles=15 / purchasers=1 / perms=56 / channel_orders=4 / order_lines=4 | 过 |
| ② | 分支尖端一致；迁移清单只有 `0047_deleted_principal_tombstone.py` | 过 |
| ③ | 重建 backend/frontend；`alembic_version=0047`、`api_ready=True`、frontend Up | 过 |
| ④ | 结构核对：PK 顺序 `kind,id`、`fk_exists=0`（软引用落地）、purchaser DELETE 授权=t、DELETE policy=1、三权限码齐、`perms_after=59` | 过 |
| ⑤ | 承重基线复核与①同名项完全一致 | 过 |
| ⑥ | 残留清理六项 `DELETE 0` + 造数（U1=10、U2=11、P1=2、P2=3、R1=16；U2 audit 直插 1 行；P1 绑 U2；P2 名下测试单 R14B0730-PO1） | 过 |
| ⑦ | 验收账号四码齐（三删除 + `identity.audit_read`），登录成功未回显口令 | 过 |
| ⑧-1 | ①级：无历史用户直删无墓碑（`no_history` / `tombstoned=false` / 库内 0 行 0 墓碑） | 过 |
| ⑧-2 | ②级+坑1：`with_history` / `purchasers_unlinked=1` / 墓碑 label `R14B验证用户乙` / P1.user_id=NULL / U2 审计行保留 | 过 |
| ⑧-3 | 验收③承重：`label_hex=52313442e9aa8c…efbc89`、`label_hex_match=True`（= `R14B验证用户乙（已删除）`）——首跑停点后按 v6 复测 | 过 |
| ⑧-4a | claimed 在途拒删 `PURCHASER_DELETE_IN_FLIGHT`（守卫有效） | 过 |
| ⑧-4b | backfilled 终态删除：`with_history` / `orders_returned=0` / `orders_retained=1` / `po_purchaser_id=3` 原值保留（软引用）/ 墓碑 `R14B外协丙`；列表 `plabel_hex_match=True`（= `R14B外协丙（已删除）`） | 过 |
| ⑧-4 退回分支 | 未真机执行，按指令由 CI 三用例覆盖（审查五轮 D 口径，回执写明） | — |
| ⑧-5 | 角色删除 `no_history`（条件式判据，与⑥-1 直插不产审计一致） | 过 |
| ⑧-6 | 自删守卫 409 `USER_DELETE_SELF`，账号存活 | 过 |
| ⑨ | ③级承重：po_rows=1、指纹与①逐字相同；channel_orders=4、order_lines=4 不变；audit `199+1+4=204` 条件式吻合（⑧-1/2/4b/5 四次成功删除写审计，4a 被拒不写） | 过 |
| ⑩ | 清理六段 `DELETE 1/2/0/1/0/0`，`leftover=0` | 过 |
| ⑪ | 迁移可逆：0047→0042（`tbl=0 / fk=1 / del_grant=f / perms=56`）→0047（`tbl=1 / fk=0 / perms=59`）；beat 恢复 | 过 |
| ⑫ | 终态：audit=204、channel_orders=4、order_lines=4；六服务 Up（db/redis healthy）；工作树无已跟踪改动 | 过 |

## 二、承重结论

- **验收③**（14b 判据）：已删操作人在审计接口解析为「XX（已删除）」——hex 逐字节验证，真机成立。
- **§7.1.1(b) 前半**：历史执行单 `purchaser_id` 原值保留（软引用），列表经墓碑解析出
  「R14B外协丙（已删除）」——墓碑不是只写不读。
- **验收⑤**：全部删除动作后 ③级表行数与指纹不变（audit 只增不减）。
- **在途守卫**（审查五轮 A）：claimed 单在册时删除被 409 拒绝。

## 三、过程：一次停机、一条指令缺陷、产品代码零缺陷

| # | 停点 | 定性 | 修复 |
|---|---|---|---|
| 1 | ⑧-3 `actor_label` 读出乱码，严格相等判据失败，按指令停手 | **客户端解码问题，非产品缺陷**：Windows PowerShell 5.1 对无 charset 的 JSON 响应默认按 ISO-8859-1 解码；乱码逐字节吻合 UTF-8→ISO-8859-1（`验`=`e9aa8c`→`éª`+不可见 0x8C）；库内字节正确已由⑧-2 psql 直读证明 | 指令 v6（`1d89fff`）：⑧-3/⑧-4 两处 HTTP 中文判据改「原始字节 UTF-8 解码 + UTF-8 十六进制比对」（纯 ASCII 判据）；同类全扫仅此两处；四检查器 self-test+实跑 8/8 exit 0 |

指令缺陷类别新增：**HTTP 响应中文参与判据未考虑客户端解码**（写法约定已入 v6 头注，
后续指令沿用）。
