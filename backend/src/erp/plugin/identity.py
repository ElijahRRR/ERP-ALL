"""`customerId` → `buyer_account` 的团队内解析器（R2-13 建；R2-17 17d 改「首见即 active」）。

## 一句话口径

**令牌回答「这是不是本方授权的插件请求」（17c 共享 token）；请求带的 `customerId` 回答
「这台浏览器此刻登的是哪个买家号」。服务端只在配置指定团队的范围内把 `customerId`
解析成 `buyer_account`。**

## 17d（D-Q73）：首见即 active，认领仪式拆除

Owner 2026-07-30/31 裁定单人自用形态：「每个订单分配出去的时候已经指定了用哪个采购
账号了」「我给他认领的时候写一个账号名我也能自己分辨」。于是：

- **首见的 customerId 直接登记为 `active`**（label/site 留 NULL，人后补账号名即可分辨）；
  发一条 warn 通知 + 一条审计（来历长期可查），**不再有认领动作**。
- **`pending_claim` 闸与洪水闸整体拆除**：那套治理（数量上限 + advisory 串行锁 +
  rejected 粘性观察）保护的是「未认领行不派单」这个中间态——中间态没了，闸随之失效。
  **风险重估（为什么现在能拆）**：持共享 token 者伪造 customerId 只会得到一条**空的**
  active 账号行——拉取语义已改为「取本账号名下已指派单」（17d，`plugin/service.py`），
  没有人工指派就没有任务可拉；自动派发（拉取即认领 + daily_cap 计量）随单人模式休眠。
  垃圾行的代价从「可能被派走真单」降为「池子里多一行没名字的号」，运营把它
  `blocked`/`retired` 即可（`PATCH /buyer-accounts/{id}` 既有状态机）。
- **词表不动**：`pending_claim`/`rejected` 仍在 CHECK 词表与 `_ALLOWED_TRANSITIONS` 里
  （地基休眠）。遗留行照旧被拉取侧账号闸挡（`status != 'active'` 不出任务）；
  多人多机形态重启时把本文件的 `_REGISTER_SQL` 改回 `'pending_claim'` 并恢复洪水闸
  （原实现见 git 历史 15614e8 之前）即可。

## 跨团队为什么仍走「未见过」这一支

团队 A 的请求带团队 B 的 `customerId`——在 A 的范围内它就是一个没见过的字符串，
一视同仁登记为 A 团队的新号（空行，无单可拉）。不为跨团队单开错误路径的理由不变：
任何差异化响应都是存在性探针。B 的账号行、B 的任务**一列不动**。

## 为什么不同情形同一个出口（返回而不是抛）

1. 插件对空数组是天生就会处理的形状（「现在没单」）；
2. 任何差异化状态码都是存在性信道；
3. 账号 `paused`/`blocked` 不出任务是**业务闸**不是认证失败，用 401/403 表达会把
   运维引去查令牌。
"""

from dataclasses import dataclass

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.audit import AuditWriter
from erp.notify.service import notify
from erp.plugin.auth import PluginPrincipal

log = structlog.get_logger()

# 解析：`team_id = :t` 是**防线②的显式谓词**（不靠 RLS 单撑，同 `service.py` 头注既有
# 纪律）；RLS 是防线③兜底。两条都在，是因为任一条被绕开时另一条还在。
_RESOLVE_SQL = """
SELECT id, site, status, daily_cap
  FROM app.buyer_account
 WHERE team_id = :t AND external_customer_id = :c
"""

# 首见自动登记（17d：直接 `active`）。**`ON CONFLICT` 推断的就是 `uq_buyer_account
# (team_id, external_customer_id)`**，正是可能撞的那把键。
#
# 两路并发同一个新 customerId：慢的那路的 INSERT 会**阻塞**在快的那路的未提交元组上；
# 快的提交后，慢的 `DO NOTHING` 返回零行 → 代码回头重跑一次 `_RESOLVE_SQL`
# （READ COMMITTED 下新语句拿新快照，必然看得见）→ 拿到同一行。快的若回滚，慢的正常插入。
# **任一时刻恰好一行，且不抛异常。**
#
# **不需要 SAVEPOINT**：`ON CONFLICT DO NOTHING` 不产生错误，事务不会进 aborted。
# 这一点**依赖 label 在登记时为 NULL**——若编占位 label，`uq_buyer_account_label`
# 的冲突不在推断范围内，会抛 `IntegrityError` 让整个事务作废（一次拉取变 500）。
#
# **不写 `created_by`**：那一列是 `app_user.id` 软引用，插件请求不是用户，写进去是
# 事实错误。持久的取证落点是审计行与通知行。
_REGISTER_SQL = """
INSERT INTO app.buyer_account (team_id, external_customer_id, status, note)
VALUES (:t, :c, 'active', :note)
ON CONFLICT (team_id, external_customer_id) DO NOTHING
RETURNING id
"""


@dataclass(frozen=True)
class ResolvedAccount:
    """一次插件请求解析出的买家账号。

    `site` 与 `daily_cap` 可为 None：首见行没有站点（0043 放松 NOT NULL），
    `daily_cap` 的 NULL 语义照旧＝不限（自动派发休眠后它不再被消费，列保留）。
    **调用方必须自己判 `status`**——本类只负责「这个 customerId 在本团队里对应哪一行」，
    不负责「能不能出任务」。
    """

    id: int
    site: str | None
    status: str  # active | paused | blocked | retired |（休眠遗留）pending_claim | rejected
    daily_cap: int | None
    newly_registered: bool  # 本次请求是不是它的诞生时刻（只影响日志/告警，不影响响应）


async def _register(
    session: AsyncSession, principal: PluginPrincipal, customer_id: str
) -> ResolvedAccount | None:
    """首见自动登记：落一条 `active` 行 + 通知 + 审计（17d，无认领仪式、无洪水闸）。"""
    team_id = principal.team_id
    note = "插件首见自动登记（17d 首见即 active，账号名待补）"
    row = (
        await session.execute(text(_REGISTER_SQL), {"t": team_id, "c": customer_id, "note": note})
    ).first()
    if row is None:
        # 并发下另一路已经插进去并提交了（`DO NOTHING` 零行）。重解析一次即可拿到同一行。
        # 另一来源：手工预建 `POST /buyer-accounts` 恰好撞同一个 customerId。
        again = (
            (await session.execute(text(_RESOLVE_SQL), {"t": team_id, "c": customer_id}))
            .mappings()
            .one_or_none()
        )
        if again is None:
            # 理论上不可达：唯一键冲突之后那一行必然可见。真发生只可能是 RLS 上下文与
            # `principal.team_id` 不一致（INSERT 撞到了别的团队的行却看不见它）——
            # fail-closed 回 None（按「无任务」处理），并留一条可 grep 的证据。
            log.error("plugin.customer.register_lost", team=team_id)
            return None
        log.info("plugin.customer.registered_concurrently", team=team_id)
        return ResolvedAccount(
            id=int(again["id"]),
            site=None if again["site"] is None else str(again["site"]),
            status=str(again["status"]),
            daily_cap=None if again["daily_cap"] is None else int(again["daily_cap"]),
            newly_registered=False,
        )

    account_id = int(row[0])
    log.info("plugin.customer.registered", team=team_id, account=account_id)
    await notify(
        session,
        team_id=team_id,
        severity="warn",
        category="procurement",
        title="新买家号已自动登记（请补账号名）",
        body=(
            f"插件带来了一个本团队没见过的 customerId，已自动登记为买家账号"
            f" #{account_id}（active，账号名/站点为空）。\n"
            "请到买家账号池补齐账号名（写个名字就能分辨是谁的号）；若这个号不是"
            "我们的，请改为 blocked 或 retired——它名下没有指派的单，不会拉走任何任务。"
        ),
        object_type="buyer_account",
        object_id=str(account_id),
        dedupe_key=f"plugin.first_sight.{account_id}",
    )
    # 审计留痕（D-Q16 唯一出口纪律）。通知会被清理/静音，`audit_log` 是 append-only 的
    # 长期留存面——「这条账号行什么时候、由哪条插件请求带来」只有它保得住。
    # `actor_type='system'`（词表 user/portal/system，不扩）；instance_id 记共享通道
    # 哨兵 0（17c 放弃机器归因，写 0 而不是删键——分得清「老数据没记」与「记不了」）。
    await AuditWriter(session, team_id=team_id).log(
        "buyer_account.auto_register",
        "buyer_account",
        account_id,
        after={
            "external_customer_id": customer_id,
            "status": "active",
            "instance_id": principal.instance_id,
        },
    )
    return ResolvedAccount(
        id=account_id, site=None, status="active", daily_cap=None, newly_registered=True
    )


async def resolve_customer(
    session: AsyncSession, principal: PluginPrincipal, customer_id: str
) -> ResolvedAccount | None:
    """把请求带的 `customerId` 在**本团队范围内**解析成买家账号。

    返回 None 仅剩一个来源＝`_register` 的 register_lost 兜底（调用方按「无任务」处理）。
    **本函数永不抛业务异常**——不同情形同一个出口，理由见模块头注。

    本函数必须跑在 `ctx_tx(team_id=principal.team_id)` 下：`_RESOLVE_SQL` 的
    `team_id = :t` 是防线②，RLS 是防线③，两条都指望这个上下文已就位。
    """
    row = (
        (await session.execute(text(_RESOLVE_SQL), {"t": principal.team_id, "c": customer_id}))
        .mappings()
        .one_or_none()
    )
    if row is None:
        # 未见过——**含跨团队**。不为跨团队单开路径，那本身就是存在性探针（头注）。
        return await _register(session, principal, customer_id)

    status = str(row["status"])
    if status != "active":
        # 只影响日志（运维要能一眼看出「为什么这个号拉不到单」），对插件的响应完全一致。
        log.info(
            "plugin.customer.not_active",
            team=principal.team_id,
            account=int(row["id"]),
            status=status,
        )
    return ResolvedAccount(
        id=int(row["id"]),
        site=None if row["site"] is None else str(row["site"]),
        status=status,
        daily_cap=None if row["daily_cap"] is None else int(row["daily_cap"]),
        newly_registered=False,
    )
