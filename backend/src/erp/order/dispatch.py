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

**「读余量 → 认领」必须按账号串行**（审查 B1）：它是典型的 read-then-claim，两路并发
各自读到的 `used` 都是对方写入之前的值，于是 N 路并发能派出 N×cap 单。上面那两层并发
防线一层都挡不住这件事——`SKIP LOCKED` 恰恰**帮倒忙**：它让各路候选集互斥，两边都不
撞车、都成功，把超发放大而不是挡住；唯一索引管的是「同一订单只派一个账号」，与日限无关。
故 `claim_tasks_for_account` 在计数之前先把**账号行**锁住（见该函数第③步）。

## 只派「派得出去」的单

候选谓词除站点匹配外还要求**该单每一行都能解出亚马逊 ASIN**（`_BAD_LINE_EXISTS`）——
插件拿到任务就照着 `amazon.com/dp/{asin}` 去买，解不出就没得买。缺 ASIN 的单不进候选
并告警；**不在下发时过滤**，那会让它先被认领、再占住名额，把账号静默饿死（见该常量注释）。

## 13c 的挂载位

护栏（`amount_ceiling` / `price_delta_pct` / `delivery_days_limit`）评估点在
「候选选出之后、UPDATE 之前」——不合格的候选转 `status='pending_review'` 并从本次
返回集剔除。**本轮不实现、不留桩、不留假配置键**（护栏缺失即禁止开 auto，007 R2-13）。
"""

from collections.abc import Sequence
from datetime import UTC, datetime, tzinfo
from typing import Any
from zoneinfo import ZoneInfo

import structlog
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from erp.notify.service import notify
from erp.order.procurement import advance_order

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

# 账号行锁：把「读余量 → 认领」整段按账号串行化（审查 B1，理由见模块头注）。
# **锁的是账号行而不是候选单**——余量是账号级的共享资源，锁候选单只会让两路各锁各的
# 一批、谁也不挡谁。取 `id` 一列即可，本语句只为拿锁，不为取值。
_LOCK_ACCOUNT_SQL = "SELECT id FROM app.buyer_account WHERE id = :a FOR UPDATE"

# 今日已派发且未取消的单数（daily_cap 的分子，口径见模块头注）
_USED_TODAY_SQL = """
SELECT count(*) FROM app.procurement_order
 WHERE buyer_account_id = :a AND status <> 'cancelled' AND assigned_at >= :day_start
"""

# 「每一行都能解出亚马逊 ASIN」——插件拿到任务就照着 `amazon.com/dp/{asin}` 去买，
# 解不出就没得买。谓词写成「**没有任何一行缺 ASIN**」而不是「至少有一行有 ASIN」：
# 一张四行的单若少给一行，插件会照着买三行，**少买的那件不会有任何人发现**。
#
# **为什么挡在候选层而不是下发时过滤**（13a §3.1 说的是「该单不派」）：过滤发生在认领
# 之后的话，这单已经挂在账号名下、还占着本次 `limit` 的名额，而续拉每次都会把它再捞
# 出来——攒够一批这样的单，该账号就再也拉不到任何可执行任务（**静默饿死**）。
# 挡在候选层则它根本不会被认领，池子里等人处置。
#
# 零订单行的单不被本条挡下（`NOT EXISTS` 对空集为真）；那种形状只出现在造数里，
# 由下发侧兜底（`plugin/service.py::_warn_undeliverable_task`）。
_BAD_LINE_EXISTS = """
   EXISTS (
     SELECT 1 FROM app.order_line ol
      WHERE ol.order_id = po.order_id AND ol.order_date = po.order_date
        AND NOT EXISTS (
          SELECT 1 FROM app.product p
           WHERE p.id = ol.product_id
             AND p.source_channel = 'amazon'
             AND coalesce(p.source_ref, '') <> ''
        )
   )
"""

# 候选：本团队、待派发、未被人工分配、收货国匹配本账号站点、每行都能解出 ASIN。
# `FOR UPDATE OF po SKIP LOCKED` 只锁 procurement_order 一侧——channel_order 是 JOIN
# 进来的读取面，锁它没有意义且会与拉单写路径互相干扰。
_CANDIDATE_SQL = f"""
SELECT po.id
  FROM app.procurement_order po
  JOIN app.channel_order co
    ON co.id = po.order_id AND co.order_date = po.order_date
 WHERE po.team_id = :t
   AND po.status = 'unassigned'
   AND po.buyer_account_id IS NULL
   AND po.purchaser_id IS NULL
   AND (co.ship_to ->> 'country') = :country
   AND NOT {_BAD_LINE_EXISTS}
 ORDER BY po.created_at, po.id
 LIMIT :n
 FOR UPDATE OF po SKIP LOCKED
"""

# 因缺 ASIN 而没能进候选的待派单——**不派但要有人知道**，否则运营只会看到「插件不拉单」
# 这个症状。dedupe_key 与下发侧兜底共用，同一张单 24 小时内只吵一次。
_MISSING_ASIN_SQL = f"""
SELECT po.id
  FROM app.procurement_order po
  JOIN app.channel_order co
    ON co.id = po.order_id AND co.order_date = po.order_date
 WHERE po.team_id = :t
   AND po.status = 'unassigned'
   AND po.buyer_account_id IS NULL
   AND po.purchaser_id IS NULL
   AND (co.ship_to ->> 'country') = :country
   AND {_BAD_LINE_EXISTS}
 ORDER BY po.created_at, po.id
 LIMIT :n
"""

# 认领。谓词重复一遍「仍未被派」是必要的：候选行虽已被 FOR UPDATE 锁住，但本语句
# 与候选查询之间隔着一次 Python 往返，写成幂等形状比依赖锁的持有时长更稳。
# `assignee_kind` 保持 'none'、`purchaser_id` 保持 NULL：插件不是 purchaser，
# **不扩第四种 assignee_kind**——扩了就有两处真相源（kind 与 buyer_account_id），
# 正是 daily_cap 刚被消灭的那种失效模式。
#
# `RETURNING` 带上 `order_id, order_date` 是为了紧接着推渠道订单的 `internal_status`
# （见 `_advance_claimed`）——`channel_order` 是复合主键 `(id, order_date)`，只拿 po 的
# 主键推不动它。
_CLAIM_SQL = """
UPDATE app.procurement_order
   SET buyer_account_id = :a, status = 'assigned', assigned_at = now(), assigned_by = NULL
 WHERE id = ANY(:ids) AND status = 'unassigned' AND buyer_account_id IS NULL
RETURNING id, order_id, order_date
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


async def _warn_missing_asin(
    session: AsyncSession, *, team_id: int, country: str, limit: int
) -> None:
    """待派但有订单行解不出亚马逊 ASIN 的单：告警，**不派**（不猜 ASIN）。

    没有这条告警，运营看到的症状只是「插件一直拉不到单」，而真因（某张单的商品没有
    货源、或货源不是 amazon）在数据里，不在插件里。dedupe_key 与下发侧兜底共用
    （`plugin/service.py::_warn_undeliverable_task`）——同一张单只是在两个阶段被发现，
    不该吵两次。
    """
    rows = (
        await session.execute(
            text(_MISSING_ASIN_SQL), {"t": team_id, "country": country, "n": limit}
        )
    ).all()
    for (po_id,) in rows:
        await notify(
            session,
            team_id=team_id,
            severity="warn",
            category="procurement",
            title="采购执行单缺 ASIN，未派给买家账号",
            body=(
                f"执行单 #{po_id} 存在无法解出亚马逊 ASIN 的订单行"
                "（商品未关联货源或货源渠道不是 amazon），已跳过自动派发"
                "——请补齐商品来源或改走人工采购"
            ),
            object_type="procurement_order",
            object_id=str(po_id),
            dedupe_key=f"plugin.no_asin.{po_id}",
        )


async def _advance_claimed(session: AsyncSession, rows: Sequence[Any]) -> list[int]:
    """认领落地后镜像 `assign_po` 的订单联动：`internal_status` `checked` → `assigned`。

    **审查 A2 的不对称加重项**。人工分配（`procurement.py::assign_po`）会把渠道订单推到
    `assigned`，插件认领此前**只改执行单不动订单**——同一件事经两条路走出两种订单状态：
    插件派走的单在订单列表里仍显示 `checked`（＝「已过四检、等着派」），运营据此再建一张
    人工执行单去下单，就是 A2 那条双买路径的**上游诱因**。建单入口那道闸（
    `_IN_FLIGHT_SIBLING_SQL`）挡住了后果，这里补的是别再把人误导过去。

    复用 `procurement.advance_order` 而不是在本文件另写一条 UPDATE：那个函数的头注写着
    「唯一的推进入口只应有这一处」，两处各写各的迟早会漂。它内部的 WHERE 带
    `id = :o AND order_date = :d`——`channel_order` 是复合主键 `(id, order_date)`，
    单给 `id` 会误伤同号不同日的行（先例：`identity/delete.py` 退回池那段）。

    条件推进（`from_statuses=('checked',)`）意味着已经在 `purchasing`/`shipped` 的订单
    不会被拉回，与 `assign_po` 逐字同款。
    """
    ids: list[int] = []
    for po_id, order_id, order_date in rows:
        await advance_order(
            session,
            order_id=order_id,
            order_date=order_date,
            to_status="assigned",
            from_statuses=("checked",),
        )
        ids.append(int(po_id))
    return ids


async def _claim(
    session: AsyncSession, *, buyer_account_id: int, candidate_ids: list[int]
) -> tuple[list[int], int]:
    """认领候选集，返回 (成功的 po_id, 撞唯一派发约束的条数)。

    先整批试一次（常态零冲突，一条语句解决）；撞 `uq_po_active_dispatch` 则回退逐单——
    **一单撞车不该让整批拉取失败**。回退必须走 SAVEPOINT：Postgres 里语句一报错，
    整个事务就进入 aborted 状态，不开子事务的话「继续处理其余单」在物理上做不到。

    订单联动（`_advance_claimed`）放在**同一个 SAVEPOINT 内**：认领若被回滚，那条
    `internal_status` 推进必须跟着回滚，否则会留下「订单说已分配、执行单说没派」的孤儿态。
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
            batch = await _advance_claimed(session, rows)
        return batch, 0
    except IntegrityError:
        log.info("plugin.dispatch.batch_conflict", account=buyer_account_id)

    claimed: list[int] = []
    conflicts = 0
    for po_id in candidate_ids:
        row: Any = None
        try:
            async with session.begin_nested():
                row = (
                    await session.execute(text(_CLAIM_SQL), {"a": buyer_account_id, "ids": [po_id]})
                ).first()
                if row is not None:
                    await _advance_claimed(session, [row])
        except IntegrityError:
            # 该渠道订单已有别的买家账号在拍（可能是异常单尚未走出边）——跳过，不放宽约束。
            conflicts += 1
            log.info("plugin.dispatch.conflict", po_id=po_id, account=buyer_account_id)
            continue
        if row is not None:
            claimed.append(int(row[0]))
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

    # ③ 账号行锁（**承重**）：从这一行起到本事务提交为止，同一账号的另一路拉取会排队。
    #
    # 为什么锁在这里，而不是靠 `touch_last_seen` 那句 UPDATE 顺带锁：那句跑在认领**之后**
    # ——余量早读完了，锁得太晚；而且它属于「最近露面」语义，将来任何一次挪动或删除都会
    # 静默拿掉串行化，没有人会意识到日限跟着废了。这里是显式的一行，理由写在旁边。
    #
    # `daily_cap` 为 NULL（不限）时也照锁：一个账号 = 一个装着插件的浏览器，同账号并发
    # 拉取本就是异常形态；且将来任何「按账号计的额度」都自动落在这把锁的保护里。
    # 代价可控——锁的粒度是单个账号行，不同账号之间零竞争。
    await session.execute(text(_LOCK_ACCOUNT_SQL), {"a": buyer_account_id})

    # ④ daily_cap 余量（NULL = 不限）
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

    # ⑤ 站点匹配 + ⑥ 认领（唯一并发点）
    await _warn_unroutable(session, team_id=team_id, limit=want)
    await _warn_missing_asin(session, team_id=team_id, country=country, limit=want)
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
