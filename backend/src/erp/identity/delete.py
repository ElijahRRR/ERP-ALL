"""R2-14 14b：主体硬删除（`00-conventions.md §7.1` 三级规则的 principal 侧落地）。

覆盖 user / role / purchaser 三类；buyer_account 的表由 R2-13 13b 建，
删除路径随 13b 另补（007「14b 的三个实现坑」第 3 条：不为凑齐四类阻塞本单）。
purchaser 属 order 域，落在本模块是因为三类共享 §7.1 的同一套级别判定与墓碑写入
——与 14a 把产品侧规则收进 `catalog/delete.py` 一个落点同理。

三级规则在本模块的落点：

| 级 | 判定（按类各异） | 动作 |
|---|---|---|
| ① 无历史 | user 无 audit 行 / role 无对象行 / purchaser 无执行单 | 物理删除，不写墓碑 |
| ② 有历史 | 上述判定为真 | 物理删除 + 写 `deleted_principal (kind,id)` 墓碑 |
| ③ 绝不删 | audit_log / procurement_order（订单族 D-Q18） | 一个 DELETE 都不发给它们 |

引用处置（007 坑 1 与外键拓扑，逐条显式）：

- `user_role` / `role_permission`：`ON DELETE CASCADE`（0002:154/173-174），数据库连带；
- `purchaser.user_id`：可空但**无 ON DELETE**（0025:96）——删用户前显式置 NULL，
  否则外键直接拒绝（不能指望 CASCADE，007 坑 1 原文）；
- `procurement_order.purchaser_id`：0047 起为软引用——③级行保留原 id，
  UI 先查主表、查不到再查墓碑显示「XX（已删除）」。
"""

from typing import Any

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.audit import AuditWriter
from erp.core.authn import CurrentUser
from erp.core.errors import BusinessError

# procurement_order 的非终态（ck_procurement_status 全集减去终态两个）。
# fail-closed 方向同 14a 的 listing 状态分类：将来 CHECK 加了新状态而这里没跟上，
# 新状态自动落进「非终态」→ 拒绝删除——宁可拒绝，不可把在途单删成无主。
_PROC_TERMINAL = frozenset({"backfilled", "cancelled"})


async def _delete_row_or_die(session: AsyncSession, table: str, entity_id: int) -> None:
    """DELETE 并校验确实删掉了一行。

    RLS 下**无 DELETE 策略或策略不匹配时，DELETE 静默匹配零行、不报任何错**——
    本单实撞（0025 的 TEAM_RLS 是三策略模板，purchaser 缺 DELETE 策略，端点回 200、
    墓碑照写、实体行还在）。把这一类静默失败变成响亮的 500：宁可炸，不可假删。
    `table` 只接受本模块内的白名单常量，不接外部输入。
    """
    gone = (
        await session.execute(
            text(f"DELETE FROM app.{table} WHERE id = :i RETURNING id"),
            {"i": entity_id},
        )
    ).one_or_none()
    if gone is None:
        raise RuntimeError(
            f"DELETE app.{table} id={entity_id} 匹配零行——多半是 RLS DELETE 策略或授权缺失，"
            "事务已中止，不会写墓碑/留痕"
        )


async def _write_tombstone(
    session: AsyncSession, *, kind: str, entity_id: int, team_id: int, label: str, by: int | None
) -> None:
    """②级墓碑。ON CONFLICT DO NOTHING：(kind,id) 是 PK，identity 序列不复用 id，
    冲突只可能来自重放（此时首条已是事实，吞掉重复即幂等）。"""
    await session.execute(
        text(
            "INSERT INTO app.deleted_principal (kind, id, team_id, label, deleted_by)"
            " VALUES (:k, :i, :t, :l, :b) ON CONFLICT (kind, id) DO NOTHING"
        ),
        {"k": kind, "i": entity_id, "t": team_id, "l": label, "b": by},
    )


async def delete_user(
    session: AsyncSession,
    *,
    user: CurrentUser,
    request: Request | None,
    target_user_id: int,
    reason: str,
) -> dict[str, Any]:
    """按 §7.1 硬删除一个用户。整体在请求事务内，任一守卫抛错即全部回滚。"""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, username, display_name, is_super, status, created_at"
                    " FROM app.app_user WHERE id = :u"
                ),
                {"u": target_user_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    # 跨团队一律 404（同 catalog/delete.py：403 等于确认该 id 存在，是存在性探测）。
    if row is None:
        raise BusinessError("USER_NOT_FOUND", "用户不存在", http_status=404)
    target = dict(row)

    if target_user_id == user.id:
        # 最常见的锁死路径：管理员删自己。留痕账号至少要活到写完这条 audit_log。
        raise BusinessError("USER_DELETE_SELF", "不能删除当前登录账号", http_status=409)
    if target["is_super"]:
        # 超管 team_id 可空（0002 ck_app_user_team），墓碑 team_id NOT NULL 放不下；
        # 且删超管属运维级动作，不该从团队管理入口走。
        raise BusinessError("USER_DELETE_SUPER", "超级管理员不可经本入口删除", http_status=409)

    # 「操作过」= 有 audit_log 行（ix_audit_actor 索引路径）。§7.1 的历史判定
    # 认的是审计留痕——正是墓碑要保可读的那份历史。
    has_history = bool(
        (
            await session.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM app.audit_log"
                    " WHERE actor_type = 'user' AND actor_id = :u)"
                ),
                {"u": target_user_id},
            )
        ).scalar_one()
    )

    # 007 坑 1：purchaser.user_id 无 ON DELETE，显式置 NULL 再删（RETURNING 计数进回执）。
    unlinked = [
        int(r[0])
        for r in await session.execute(
            text("UPDATE app.purchaser SET user_id = NULL WHERE user_id = :u RETURNING id"),
            {"u": target_user_id},
        )
    ]
    # user_role 行由 CASCADE 连带；audit_log 一行不动（③级）。
    await _delete_row_or_die(session, "app_user", target_user_id)

    if has_history:
        await _write_tombstone(
            session,
            kind="user",
            entity_id=target_user_id,
            team_id=target["team_id"],
            label=target["display_name"],
            by=user.id,
        )

    await AuditWriter.for_user(session, user, request).log(
        "identity.user_delete",
        "user",
        target_user_id,
        before={
            "username": target["username"],
            "display_name": target["display_name"],
            "status": target["status"],
            "created_at": target["created_at"].isoformat(),
        },
        after={"reason": reason, "tombstoned": has_history, "purchasers_unlinked": unlinked},
    )
    return {
        "user_id": target_user_id,
        "username": target["username"],
        "level": "with_history" if has_history else "no_history",
        "tombstoned": has_history,
        "purchasers_unlinked": len(unlinked),
    }


async def delete_role(
    session: AsyncSession,
    *,
    user: CurrentUser,
    request: Request | None,
    role_id: int,
    reason: str,
) -> dict[str, Any]:
    """按 §7.1 硬删除一个角色。"""
    row = (
        (
            await session.execute(
                text("SELECT id, team_id, name, created_at FROM app.role WHERE id = :r"),
                {"r": role_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    # 全局模板（team_id IS NULL）对全团队可见（role_sel RLS），但新团队建立时靠它复制
    # 权限集——删了它，此后每个新团队都长不出这个角色。与 patch_role 同一措辞口径。
    if row is None or row["team_id"] is None:
        raise BusinessError(
            "ROLE_NOT_FOUND", "角色不存在或为全局模板（模板不可删）", http_status=404
        )
    target = dict(row)

    # 在挂用户的角色不删：user_role 虽是 CASCADE，但那是「删角色顺手摘掉所有人的权限」
    # ——静默扩散，方向同 14a 拒绝 CASCADE 的理由。先解绑再删，代价只是多一步。
    assigned = int(
        (
            await session.execute(
                text("SELECT count(*) FROM app.user_role WHERE role_id = :r"), {"r": role_id}
            )
        ).scalar_one()
    )
    if assigned:
        raise BusinessError(
            "ROLE_IN_USE",
            "角色仍挂有成员，先在成员管理里解绑后再删除",
            {"assigned_users": assigned},
            http_status=409,
        )

    # 「有历史」= audit_log 里有以它为对象的行（建/改/挂权限都会留 object_type='role'）。
    has_history = bool(
        (
            await session.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM app.audit_log"
                    " WHERE object_type = 'role' AND object_id = :r)"
                ),
                {"r": str(role_id)},
            )
        ).scalar_one()
    )

    perms = [
        str(r[0])
        for r in await session.execute(
            text("SELECT permission_code FROM app.role_permission WHERE role_id = :r ORDER BY 1"),
            {"r": role_id},
        )
    ]
    # role_permission 由 CASCADE 连带（角色自身的构成，不是外部历史）。
    await _delete_row_or_die(session, "role", role_id)

    if has_history:
        await _write_tombstone(
            session,
            kind="role",
            entity_id=role_id,
            team_id=target["team_id"],
            label=target["name"],
            by=user.id,
        )

    await AuditWriter.for_user(session, user, request).log(
        "identity.role_delete",
        "role",
        role_id,
        before={
            "name": target["name"],
            "permissions": perms,
            "created_at": target["created_at"].isoformat(),
        },
        after={"reason": reason, "tombstoned": has_history},
    )
    return {
        "role_id": role_id,
        "name": target["name"],
        "level": "with_history" if has_history else "no_history",
        "tombstoned": has_history,
    }


async def delete_purchaser(
    session: AsyncSession,
    *,
    user: CurrentUser,
    request: Request | None,
    purchaser_id: int,
    reason: str,
) -> dict[str, Any]:
    """按 §7.1 硬删除一个采购方。"""
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, name, purchaser_kind, user_id, status, created_at"
                    " FROM app.purchaser WHERE id = :p"
                ),
                {"p": purchaser_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise BusinessError("PURCHASER_NOT_FOUND", "采购方不存在", http_status=404)
    target = dict(row)

    # 在途执行单指着它（认领中/已下单/在运……）：删了就是把跑着的单删成无主。
    # fail-closed：非终态一律拒绝，等单走完再删，代价只是等（同 14a 维护任务守卫）。
    in_flight = int(
        (
            await session.execute(
                text(
                    "SELECT count(*) FROM app.procurement_order"
                    " WHERE purchaser_id = :p AND NOT (status = ANY(:t))"
                ),
                {"p": purchaser_id, "t": list(_PROC_TERMINAL)},
            )
        ).scalar_one()
    )
    if in_flight:
        raise BusinessError(
            "PURCHASER_DELETE_IN_FLIGHT",
            "该采购方仍有未走完的采购执行单，请等其到终态（回填/取消）后再删除",
            {"in_flight": in_flight},
            http_status=409,
        )

    # 「接过单」= 有执行单引用（终态行也是历史）。③级行一行不删、id 原样保留，
    # 0047 已把外键改软引用——这正是墓碑能解析回「当时是谁」的前提。
    has_history = bool(
        (
            await session.execute(
                text("SELECT EXISTS (SELECT 1 FROM app.procurement_order WHERE purchaser_id = :p)"),
                {"p": purchaser_id},
            )
        ).scalar_one()
    )

    await _delete_row_or_die(session, "purchaser", purchaser_id)

    if has_history:
        await _write_tombstone(
            session,
            kind="purchaser",
            entity_id=purchaser_id,
            team_id=target["team_id"],
            label=target["name"],
            by=user.id,
        )

    await AuditWriter.for_user(session, user, request).log(
        "procurement.purchaser_delete",
        "purchaser",
        purchaser_id,
        before={
            "name": target["name"],
            "purchaser_kind": target["purchaser_kind"],
            "user_id": target["user_id"],
            "status": target["status"],
            "created_at": target["created_at"].isoformat(),
        },
        after={"reason": reason, "tombstoned": has_history},
    )
    return {
        "purchaser_id": purchaser_id,
        "name": target["name"],
        "level": "with_history" if has_history else "no_history",
        "tombstoned": has_history,
    }
