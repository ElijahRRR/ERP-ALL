"""`customerId` → `buyer_account` 的团队内解析器（R2-13 身份模型更正，2026-07-30）。

## 一句话口径

**令牌回答「这是不是团队 T 的一台授权浏览器」；请求带的 `customerId` 回答「这台浏览器
此刻登的是哪个买家号」。服务端只在团队 T 的范围内把 `customerId` 解析成 `buyer_account`。
越权边界＝跨团队必败，不是「实例只能取自己账号的任务」。**

`customerId` 由此从「鉴权凭据」降为**路由参数**——它**不再是安全边界**。安全边界完全由
`plugin_instance.team_id` + RLS + 每条 SQL 的显式 `team_id = :t` 谓词承担。
出处：`specs/001-domain-model/07-order-sourcing-aftersale.md:288-340`。

## 为什么单独成一个文件

`plugin/service.py` 已 1100+ 行；「解析 + 首见登记 + 垃圾行治理」是一个自洽的关注点，
需要一整段头注解释两段式身份与洪水闸，塞进 service 会被淹没。

## 四种情形，**响应形状必须完全一致**

| 情形 | 落库 | 通知 | 返回给插件 |
|---|---|---|---|
| 解析到 `active` | 不变 | 无 | 正常派单 / 正常同步 |
| 解析到 `pending_claim` | 只 touch `last_seen_at` | 无（首见时发过，dedupe 24h） | `200` + 空数组 |
| 解析到 `paused`/`blocked`/`retired` | 只 touch | 无 | 同上 |
| 解析到 `rejected` | 只 touch | 无 | 同上 |
| **未见过（含跨团队）** | 落 `pending_claim` 行 | warn（dedupe） | 同上 |
| **未见过 + 撞洪水闸** | **不落** | critical（dedupe） | 同上 |

**跨团队为什么走「未见过」这一支、且必须如此**：团队 A 的实例带团队 B 的 `customerId`
——在 A 的范围内它就是一个没见过的字符串。若为它单开一条错误路径（403 / 特殊码 /
不登记），响应形状就与「真正的全新 customerId」不同，**那本身就是一个存在性探针**：
拿一把 A 的令牌扫 customerId，凡是「不给我落待认领行」的就是别的团队真实存在的号。
所以验收③判据 (a)「解析不到，非探测存在性」的正确落地就是**一视同仁地当新号登记**。
团队 B 的账号行、B 的任务、B 的额度**一列不动**——那才是「必败」的实质。

**为什么四种情形同一个出口（返回而不是抛）**：
1. 插件侧对非 200 的处理是 fork 未知区（厂商原版按 `response.data` 读，fork 改成按 HTTP
   状态判）；空数组是它**天生就会处理**的形状（「现在没单」）。
2. 任何差异化状态码都是存在性信道。
3. `pending_claim` 一律不派单是**业务闸**不是**认证失败**，用 401/403 表达会把运维引到
   错误的排障方向（去查令牌，而真因是「这个号还没认领」）。

## 垃圾行治理（四道并行的面）

威胁：持有效令牌者（或一台被人乱点的浏览器）可以用伪造 `customerId` 无限灌
`pending_claim` 行，而 `buyer_account` **无 DELETE 授权**（0043 刻意不授）⇒ 永久残留。

1. **数量上限（硬闸，落在灌注发生的那一刻）**——本文件的 `_PENDING_COUNT_SQL`：登记前
   先数本团队待认领行（走 `ix_buyer_account_pending`），到顶即拒绝登记 + critical 告警。
   只数 `pending_claim`：`rejected` **不占额度**，否则驳回越多越容易把闸撑死，治理动作
   反而变成自伤。并发下允许小幅越界（数与插之间无锁）——它是**治理边界不是安全边界**，
   不值得为此加表锁。代价如实登记：闸打满后一个**真实**的新号也会被拒到有人清理为止，
   这是有意的 fail-closed（「注册洪水无解」比「新号晚一天被发现」严重得多）。
2. **`rejected` 终态 + 天然粘性**——驳回不删行 ⇒ 该 `(team_id, customerId)` 被
   `uq_buyer_account` 永久占住 ⇒ 同一个伪造 id 再灌一万次都只解析到那一行、零新增行、
   零新通知。**粘性不是额外代码，是唯一索引的自然结果。**
3. **通知 dedupe + 行数可见**——每条待认领行一条 warn（`dedupe_key` 用行 id：稳定，
   且形状同既有 `plugin.no_asin.{po_id}`）；正文带当前待认领行数与上限，运营在数量
   还没打满时就能看出「有人在灌」。
4. **管理端出口**——认领（`pending_claim → active`，须补齐 label/site）与驳回
   （`→ rejected`，终态）走既有 `PATCH /buyer-accounts/{id}`，见
   `order/buyer_account.py::update_account` 的状态机守卫。

**明确不做「合并」端点**：`rejected` 行永久占住 customerId ⇒ 无法把该 customerId 挪到
另一条账号行上。运营真要把一个已有账号切到这个 customerId，正确做法是**把老账号
`retired`、把待认领行认领成 active**（历史单挂在老账号 id 上不受影响——
`procurement_order.buyer_account_id` 是软引用、记的是历史事实）。为「合并」造一条要
绕开唯一索引的路径，收益不抵复杂度。
"""

from dataclasses import dataclass

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.notify.service import notify
from erp.order import dispatch
from erp.plugin.auth import PluginPrincipal

log = structlog.get_logger()

# 解析：`team_id = :t` 是**防线②的显式谓词**（不靠 RLS 单撑，同 `service.py` 头注既有
# 纪律）；RLS 是防线③兜底。两条都在，是因为任一条被绕开时另一条还在。
_RESOLVE_SQL = """
SELECT id, site, status, daily_cap
  FROM app.buyer_account
 WHERE team_id = :t AND external_customer_id = :c
"""

# 洪水闸的计数（走 0043 的部分索引 `ix_buyer_account_pending`）。
_PENDING_COUNT_SQL = (
    "SELECT count(*) FROM app.buyer_account WHERE team_id = :t AND status = 'pending_claim'"
)

# 首见自动登记。**`ON CONFLICT` 推断的就是 `uq_buyer_account (team_id,
# external_customer_id)`**，正是可能撞的那把键（0043 头注三把它升为承重即为此）。
#
# 两路并发同一个新 customerId：慢的那路的 INSERT 会**阻塞**在快的那路的未提交元组上；
# 快的提交后，慢的 `DO NOTHING` 返回零行 → 代码回头重跑一次 `_RESOLVE_SQL`
# （READ COMMITTED 下新语句拿新快照，必然看得见）→ 拿到同一行。快的若回滚，慢的正常插入。
# **任一时刻恰好一行，且不抛异常。**
#
# **不需要 SAVEPOINT**：`ON CONFLICT DO NOTHING` 不产生错误，事务不会进 aborted。
# 这一点**依赖 label 在登记时为 NULL**——若沿用「编一个占位 label」的方案，
# `uq_buyer_account_label` 的冲突不在推断范围内，会抛 `IntegrityError` 让整个事务作废
# （一次拉取变 500）。这是 0043 放松 label/site NOT NULL 的第二个硬理由。
#
# **不写 `created_by`**：那一列是 `app_user.id` 软引用，实例不是用户，写进去是事实错误
# （同 0045 头注「扩 'plugin' 是三者里唯一不撒谎的」那条纪律）。持久的取证落点是
# **通知行**（含 created_at + 实例号，运营改不掉）与 `note`。
_REGISTER_SQL = """
INSERT INTO app.buyer_account (team_id, external_customer_id, status, note)
VALUES (:t, :c, 'pending_claim', :note)
ON CONFLICT (team_id, external_customer_id) DO NOTHING
RETURNING id
"""


@dataclass(frozen=True)
class ResolvedAccount:
    """一次插件请求解析出的买家账号。

    `site` 与 `daily_cap` 可为 None：待认领/已驳回行没有站点（0043 本轮放松 NOT NULL），
    `daily_cap` 的 NULL 语义照旧＝不限。**调用方必须自己判 `status`**——本类只负责
    「这个 customerId 在本团队里对应哪一行」，不负责「能不能派单」。

    **没有 `registration_refused` 字段**：撞洪水闸时连行都没落，没有账号可指，
    `resolve_customer` 直接回 `None`。两种表示法只留一种，免得将来出现
    「id 是假的但 refused=True」这种半真半假的对象。
    """

    id: int
    site: str | None
    status: str  # pending_claim | active | paused | blocked | retired | rejected
    daily_cap: int | None
    newly_registered: bool  # 本次请求是不是它的诞生时刻（只影响日志/告警，不影响响应）


async def _register(
    session: AsyncSession, principal: PluginPrincipal, customer_id: str
) -> ResolvedAccount | None:
    """首见自动登记：落一条 `pending_claim` 行 + 通知。撞洪水闸回 None。"""
    team_id = principal.team_id
    cfg = await dispatch.dispatch_config(session, team_id)
    pending = int((await session.execute(text(_PENDING_COUNT_SQL), {"t": team_id})).scalar_one())
    if pending >= cfg.pending_claim_cap:
        # fail-closed：宁可一个真实新号晚一天被发现，也不留「注册洪水无解」的形状。
        log.warning(
            "plugin.customer.flood_refused",
            instance=principal.instance_id,
            team=team_id,
            pending=pending,
            cap=cfg.pending_claim_cap,
        )
        await notify(
            session,
            team_id=team_id,
            severity="critical",
            category="procurement",
            title="待认领买家账号已达上限，新 customerId 拒绝登记",
            body=(
                f"本团队待认领（pending_claim）买家账号已有 {pending} 条，达到上限"
                f" {cfg.pending_claim_cap}，本次插件请求带来的新 customerId **未被登记**。\n"
                "⚠️ 这通常意味着有人（或一台被误用的浏览器）在灌伪造 customerId。"
                "请到买家账号池逐条认领或驳回——驳回是终态，被驳回的 customerId 此后"
                "不再自动登记，也不占用上限。清理之前，**真实的新买家号同样登记不进来**。"
            ),
            dedupe_key=f"plugin.pending_claim_flood.{team_id}",
        )
        return None

    note = f"插件实例 #{principal.instance_id} 首见自动登记"
    row = (
        await session.execute(text(_REGISTER_SQL), {"t": team_id, "c": customer_id, "note": note})
    ).first()
    if row is None:
        # 并发下另一路已经插进去并提交了（`DO NOTHING` 零行）。重解析一次即可拿到同一行。
        again = (
            (await session.execute(text(_RESOLVE_SQL), {"t": team_id, "c": customer_id}))
            .mappings()
            .one_or_none()
        )
        if again is None:
            # 理论上不可达：唯一键冲突之后那一行必然可见。真发生只可能是 RLS 上下文与
            # `principal.team_id` 不一致（INSERT 撞到了别的团队的行却看不见它）——
            # fail-closed 回 None（按「无任务」处理），并留一条可 grep 的证据。
            log.error(
                "plugin.customer.register_lost",
                instance=principal.instance_id,
                team=team_id,
            )
            return None
        log.info("plugin.customer.registered_concurrently", instance=principal.instance_id)
        return ResolvedAccount(
            id=int(again["id"]),
            site=None if again["site"] is None else str(again["site"]),
            status=str(again["status"]),
            daily_cap=None if again["daily_cap"] is None else int(again["daily_cap"]),
            newly_registered=False,
        )

    account_id = int(row[0])
    log.info(
        "plugin.customer.registered",
        instance=principal.instance_id,
        team=team_id,
        account=account_id,
    )
    await notify(
        session,
        team_id=team_id,
        severity="warn",
        category="procurement",
        title="发现未认领的买家账号（插件首次上报）",
        body=(
            f"插件实例 #{principal.instance_id} 带来了一个本团队没见过的 customerId，"
            f"已自动登记为待认领账号 #{account_id}。\n"
            f"**未认领一律不派单**——请补齐账号名与站点（以及日限）后转为启用；"
            f"若这个号不是我们的，请驳回（终态，此后不再自动登记）。\n"
            f"本团队待认领账号现有 {pending + 1} 条，上限 {cfg.pending_claim_cap}。"
        ),
        object_type="buyer_account",
        object_id=str(account_id),
        dedupe_key=f"plugin.pending_claim.{account_id}",
    )
    return ResolvedAccount(
        id=account_id, site=None, status="pending_claim", daily_cap=None, newly_registered=True
    )


async def resolve_customer(
    session: AsyncSession, principal: PluginPrincipal, customer_id: str
) -> ResolvedAccount | None:
    """把请求带的 `customerId` 在**本团队范围内**解析成买家账号。

    返回 None ＝ 撞了待认领洪水闸，本次连登记都没做（调用方按「无任务」处理）。
    **本函数永不抛业务异常**——四种情形同一个出口，理由见模块头注。

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
        # 三个分档只影响日志（运维要能一眼看出「为什么这台机器拉不到单」），
        # 对插件的响应完全一致。
        event = {
            "pending_claim": "plugin.customer.pending_claim",
            "rejected": "plugin.customer.rejected",
        }.get(status, "plugin.customer.not_active")
        log.info(
            event,
            instance=principal.instance_id,
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
