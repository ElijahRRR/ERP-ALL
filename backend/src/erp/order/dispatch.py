"""买家账号任务路由（R2-13 13b；图纸 `07:270`「同一渠道订单只派一个买家账号」）。

## 为什么放在 order 域而不是 plugin 包

派发是**领域规则**（站点匹配 / 日限 / 唯一派发），不是插件协议。放这里让 13c 的护栏
评估点有一个与协议无关的挂载位，也让「人工触发一次派发」这类将来需求不必穿过插件包。
插件侧的拉取端点（13a）只是本函数的一个调用方。

## 认领即派发（claim-on-pull）

不做预派发、不建 beat：插件端点组是「插件来拉」的被动形态，预派发会把单派给已掉线的
账号后卡死。并发安全**两层**：
1. `FOR UPDATE ... SKIP LOCKED` 挡同一瞬间的行级竞争（两个实例同时拉同一张 PO）；
2. 部分唯一索引 `uq_po_active_dispatch` 挡跨行竞争（两张不同 PO 指向同一 `order_id`）
   ——**这一层在 DB 里**，应用层判断挡不住它（0045 头注二）。

## daily_cap 的口径（承重，写反了帽子形同虚设）

按**今日已派发且未取消的单数**计，不按已拍成的单数计。按后者算的话，一个账号可以被
派 100 单再一口气拍完，`daily_cap` 等于没有。日限的唯一权威是 `buyer_account.daily_cap`
列（Owner 2026-07-30 裁定：`automation_policy.config` 里**不得**有同名键）。

## 13c 的挂载位

护栏（`amount_ceiling` / `price_delta_pct` / `delivery_days_limit`）评估点在
「候选选出之后、UPDATE 之前」——不合格的候选转 `status='pending_review'` 并从本次
返回集剔除。**本轮不实现、不留桩、不留假配置键**（护栏缺失即禁止开 auto，007 R2-13）。
"""

from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from erp.notify.service import notify

log = structlog.get_logger()

# 业务参数走配置中心（CLAUDE.md 禁区：不写死）。整键是一个 JSON 对象，字段见下。
PLUGIN_DISPATCH_CONFIG_KEY = "procurement.plugin_dispatch"
# 日界时区：`daily_cap` 从哪一刻重新计数。属**业务口径**，已列入批注回传请 Owner 给值；
# 未获裁定前默认 UTC，改判只需改配置值、不动代码。
DEFAULT_DAY_BOUNDARY_TZ = "UTC"
DEFAULT_PULL_BATCH_MAX = 20
# 硬上限：配置写多大都不许超过这个数。一次拉太多会让掉线账号一口气占住大批单。
PULL_BATCH_HARD_MAX = 100

# 站点 ↔ 收货国。**渠道事实不是业务参数**，故是代码常量不进配置中心：
# 美国订单只能在 amazon.com 买，这不是可配置的口径。
# JP 分支保留（铁律 9：日本站 MVP 不做，但代码路径不删，将来开 JP 只补映射表）。
_SITE_TO_COUNTRY: dict[str, str] = {
    "amazon_com": "US",
    "amazon_ca": "CA",
    "amazon_co_jp": "JP",
}

# 空返回时的原因码（给调用方与运维看「为什么没拉到单」，比空数组更可诊断）
REASON_ACCOUNT_NOT_ACTIVE = "ACCOUNT_NOT_ACTIVE"
REASON_DAILY_CAP_REACHED = "DAILY_CAP_REACHED"
REASON_NO_TASK = "NO_TASK"
REASON_ALREADY_DISPATCHED = "ALREADY_DISPATCHED"

# 续拉：插件重启/掉线后重新拉，必须仍看得到自己已被派的单（图纸 07:125 的可见范围
# 就是 assigned + claimed）。**续拉不消耗新额度**——那是同一批单的重新可见，不是新派发。
_RECLAIM_SQL = """
SELECT id FROM app.procurement_order
 WHERE buyer_account_id = :a AND status IN ('assigned','claimed')
 ORDER BY assigned_at NULLS LAST, id
 LIMIT :n
"""

# 今日已派发且未取消的单数（daily_cap 的分子，口径见模块头注）
_USED_TODAY_SQL = """
SELECT count(*) FROM app.procurement_order
 WHERE buyer_account_id = :a AND status <> 'cancelled' AND assigned_at >= :day_start
"""

# 候选：本团队、待派发、未被人工分配、收货国匹配本账号站点。
# `FOR UPDATE OF po SKIP LOCKED` 只锁 procurement_order 一侧——channel_order 是 JOIN
# 进来的读取面，锁它没有意义且会与拉单写路径互相干扰。
_CANDIDATE_SQL = """
SELECT po.id
  FROM app.procurement_order po
  JOIN app.channel_order co
    ON co.id = po.order_id AND co.order_date = po.order_date
 WHERE po.team_id = :t
   AND po.status = 'unassigned'
   AND po.buyer_account_id IS NULL
   AND po.purchaser_id IS NULL
   AND (co.ship_to ->> 'country') = :country
 ORDER BY po.created_at, po.id
 LIMIT :n
 FOR UPDATE OF po SKIP LOCKED
"""

# 认领。谓词重复一遍「仍未被派」是必要的：候选行虽已被 FOR UPDATE 锁住，但本语句
# 与候选查询之间隔着一次 Python 往返，写成幂等形状比依赖锁的持有时长更稳。
# `assignee_kind` 保持 'none'、`purchaser_id` 保持 NULL：插件不是 purchaser，
# **不扩第四种 assignee_kind**——扩了就有两处真相源（kind 与 buyer_account_id），
# 正是 daily_cap 刚被消灭的那种失效模式。
_CLAIM_SQL = """
UPDATE app.procurement_order
   SET buyer_account_id = :a, status = 'assigned', assigned_at = now(), assigned_by = NULL
 WHERE id = ANY(:ids) AND status = 'unassigned' AND buyer_account_id IS NULL
RETURNING id
"""

# 无法路由的单（收货国缺失或不在映射表内）——**不猜**，告警让人处置。
_UNROUTABLE_SQL = """
SELECT po.id, co.ship_to ->> 'country' AS country
  FROM app.procurement_order po
  JOIN app.channel_order co
    ON co.id = po.order_id AND co.order_date = po.order_date
 WHERE po.team_id = :t
   AND po.status = 'unassigned'
   AND po.buyer_account_id IS NULL
   AND po.purchaser_id IS NULL
   AND (co.ship_to ->> 'country' IS NULL
        OR NOT ((co.ship_to ->> 'country') = ANY(:known)))
 ORDER BY po.created_at, po.id
 LIMIT :n
"""


def site_country(site: str) -> str | None:
    """站点 → 收货国；未知站点回 None（调用方据此 fail-closed，不猜）。"""
    return _SITE_TO_COUNTRY.get(site)


def day_start(tz_name: str) -> datetime:
    """该时区的今日零点（带时区），用于 `daily_cap` 计数窗口。

    **在 Python 侧算而不在 SQL 侧算**（设计原文是
    `date_trunc('day', now() AT TIME ZONE :tz) AT TIME ZONE :tz`）：坏时区名进 SQL 会
    抛 `InvalidParameterValue`，而语句报错在 Postgres 里**整事务作废**——一个配错的
    配置值就能让整条拉取链 500。Python 侧可以捕获并退回 UTC + 告警，坏配置只降级不断链。
    结果语义逐字等价。
    """
    tz: tzinfo
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        log.warning("plugin.dispatch.bad_timezone", tz=tz_name, fallback=DEFAULT_DAY_BOUNDARY_TZ)
        tz = UTC
    local = datetime.now(tz)
    return datetime(local.year, local.month, local.day, tzinfo=tz)


async def dispatch_config(session: AsyncSession, team_id: int) -> tuple[str, int]:
    """读 `procurement.plugin_dispatch`：team_config > system_config > 代码默认（D-Q11）。

    经请求会话直读（GUC 已就位、RLS 生效）——同 `pricing/service.py::confirm_threshold`
    的理由：ConfigService 自管会话没有团队上下文，读不到 team 级覆盖。
    """
    raw = (
        await session.execute(
            text(
                "SELECT value FROM ("
                "  SELECT value, 0 AS pri FROM app.team_config"
                "   WHERE team_id = :t AND key = :k"
                "  UNION ALL"
                "  SELECT value, 1 AS pri FROM app.system_config WHERE key = :k"
                ") c ORDER BY pri LIMIT 1"
            ),
            {"t": team_id, "k": PLUGIN_DISPATCH_CONFIG_KEY},
        )
    ).scalar_one_or_none()
    cfg: dict[str, Any] = raw if isinstance(raw, dict) else {}
    tz = str(cfg.get("day_boundary_tz") or DEFAULT_DAY_BOUNDARY_TZ)
    try:
        batch_max = int(cfg.get("pull_batch_max") or DEFAULT_PULL_BATCH_MAX)
    except (TypeError, ValueError):
        batch_max = DEFAULT_PULL_BATCH_MAX
    return tz, max(1, min(batch_max, PULL_BATCH_HARD_MAX))


async def _warn_unroutable(session: AsyncSession, *, team_id: int, limit: int) -> None:
    """收货国缺失/不在映射表内的待派单：告警，**不派**（不猜站点）。

    dedupe_key 带 po_id ⇒ 同一张单 24 小时内只吵一次（notify 自带的告警风暴闸）。
    """
    rows = (
        await session.execute(
            text(_UNROUTABLE_SQL),
            {"t": team_id, "known": list(_SITE_TO_COUNTRY.values()), "n": limit},
        )
    ).all()
    for po_id, country in rows:
        await notify(
            session,
            team_id=team_id,
            severity="warn",
            category="procurement",
            title="采购执行单无法路由到买家账号",
            body=(
                f"执行单 #{po_id} 的收货国为 {country!r}，不在站点映射表内"
                "（US/CA/JP），已跳过自动派发——请人工分配采购方或补正收货地址"
            ),
            object_type="procurement_order",
            object_id=str(po_id),
            dedupe_key=f"plugin.site.{po_id}",
        )


async def _claim(
    session: AsyncSession, *, buyer_account_id: int, candidate_ids: list[int]
) -> tuple[list[int], int]:
    """认领候选集，返回 (成功的 po_id, 撞唯一派发约束的条数)。

    先整批试一次（常态零冲突，一条语句解决）；撞 `uq_po_active_dispatch` 则回退逐单——
    **一单撞车不该让整批拉取失败**。回退必须走 SAVEPOINT：Postgres 里语句一报错，
    整个事务就进入 aborted 状态，不开子事务的话「继续处理其余单」在物理上做不到。
    """
    if not candidate_ids:
        return [], 0
    try:
        async with session.begin_nested():
            rows = (
                await session.execute(
                    text(_CLAIM_SQL), {"a": buyer_account_id, "ids": candidate_ids}
                )
            ).all()
        return [int(r[0]) for r in rows], 0
    except IntegrityError:
        log.info("plugin.dispatch.batch_conflict", account=buyer_account_id)

    claimed: list[int] = []
    conflicts = 0
    for po_id in candidate_ids:
        try:
            async with session.begin_nested():
                row = (
                    await session.execute(text(_CLAIM_SQL), {"a": buyer_account_id, "ids": [po_id]})
                ).first()
            if row is not None:
                claimed.append(int(row[0]))
        except IntegrityError:
            # 该渠道订单已有别的买家账号在拍（可能是异常单尚未走出边）——跳过，不放宽约束。
            conflicts += 1
            log.info("plugin.dispatch.conflict", po_id=po_id, account=buyer_account_id)
    return claimed, conflicts


async def claim_tasks_for_account(
    session: AsyncSession,
    *,
    team_id: int,
    buyer_account_id: int,
    site: str,
    account_status: str,
    daily_cap: int | None,
    limit: int,
) -> tuple[list[int], str | None]:
    """给一个买家账号派任务；返回 (可执行的 po_id 列表, 空列表时的原因码)。

    非空返回时原因码为 None。列表**含续拉的老单**——插件重启后必须还能看见自己的单。
    """
    # ① 账号闸：非 active 一律不派。**注意这只挡拉取，不挡写路径**——账号被停时
    #    回填/异常/物流三条路必须照常通，否则「钱花了但系统不知道」（13a 认证域纪律）。
    if account_status != "active":
        return [], REASON_ACCOUNT_NOT_ACTIVE

    country = site_country(site)
    if country is None:
        log.warning("plugin.dispatch.unknown_site", site=site, account=buyer_account_id)
        return [], REASON_ACCOUNT_NOT_ACTIVE

    tz_name, batch_max = await dispatch_config(session, team_id)
    want = max(1, min(limit, batch_max))

    # ② 续拉（不消耗新额度）
    reclaimed = [
        int(r[0])
        for r in (
            await session.execute(text(_RECLAIM_SQL), {"a": buyer_account_id, "n": want})
        ).all()
    ]
    room = want - len(reclaimed)
    if room <= 0:
        return reclaimed, None

    # ③ daily_cap 余量（NULL = 不限）
    if daily_cap is not None:
        used = int(
            (
                await session.execute(
                    text(_USED_TODAY_SQL),
                    {"a": buyer_account_id, "day_start": day_start(tz_name)},
                )
            ).scalar_one()
        )
        remaining = daily_cap - used
        if remaining <= 0:
            return reclaimed, (None if reclaimed else REASON_DAILY_CAP_REACHED)
        room = min(room, remaining)

    # ④ 站点匹配 + ⑤ 认领（唯一并发点）
    await _warn_unroutable(session, team_id=team_id, limit=want)
    candidates = [
        int(r[0])
        for r in (
            await session.execute(
                text(_CANDIDATE_SQL), {"t": team_id, "country": country, "n": room}
            )
        ).all()
    ]
    claimed, conflicts = await _claim(
        session, buyer_account_id=buyer_account_id, candidate_ids=candidates
    )

    out = reclaimed + claimed
    if out:
        return out, None
    return [], (REASON_ALREADY_DISPATCHED if conflicts else REASON_NO_TASK)
