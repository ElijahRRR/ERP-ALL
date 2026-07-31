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
   反而变成自伤。**并发下精确**——`_register` 在计数之前先取一把按团队派生的
   `pg_advisory_xact_lock`（见 `_REGISTER_LOCK_SQL`）。此前这里写的是「允许小幅越界」，
   **那句话是失实的**：数与插之间无锁时，N 路并发各自读到的都是对方写入之前的值，
   实测 `cap=3` 落地 15 条（＝连接池上限，即「越界幅度 = 并发度」而不是「小幅」）。
   代价如实登记：闸打满后一个**真实**的新号也会被拒到有人清理为止，
   这是有意的 fail-closed（「注册洪水无解」比「新号晚一天被发现」严重得多）。
2. **`rejected` 终态 + 天然粘性**——驳回不删行 ⇒ 该 `(team_id, customerId)` 被
   `uq_buyer_account` 永久占住 ⇒ 同一个伪造 id 再灌一万次都只解析到那一行、零新增行、
   零新通知。**粘性不是额外代码，是唯一索引的自然结果。**
   > **粘性挡的是「同一个 id 再灌」，不挡「换一个 id 再灌」**——后者每次都是一个全新的
   > `(team_id, customerId)`，照样新增一条待认领行。那一面**只由面 1 的数量闸兜底**，
   > 驳回本身不产生任何额外保护，只是把该行挪出额度（面 1 只数 `pending_claim`）。
   > 于是驳回得越勤，池子里的 `rejected` 残留越多而额度看起来越宽松——**残留量本身
   > 没有任何告警**。观察手段：撞闸的 critical 正文里带**本团队 rejected 行数现值**
   > （见 `_register`），运营看到「待认领 50/50，已驳回 900」就知道是长期在被灌，
   > 而不是刚好来了 50 个新号；管理端另有 `status=rejected` 筛选可逐条看。
3. **通知 dedupe + 行数可见**——每条待认领行一条 warn（`dedupe_key` 用行 id：稳定，
   且形状同既有 `plugin.no_asin.{po_id}`）；正文带当前待认领行数与上限，运营在数量
   还没打满时就能看出「有人在灌」。**两档通知正文都带实例号**——「哪台浏览器在灌」
   是排障与处置（吊销哪一把令牌）的第一个问题，正文不写就得去翻结构化日志。
   通知之外另落一条 `audit_log`（见 `_register`）：通知会被清理/静音，审计是 append-only
   的长期留存面，「这条账号行是什么时候、由哪台实例自动登记的」只有它保得住。
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

from erp.core.audit import AuditWriter
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

# 已驳回行数：**只用于告警正文的可见性**（治理面 2 的残留观察手段），不参与任何判断。
_REJECTED_COUNT_SQL = (
    "SELECT count(*) FROM app.buyer_account WHERE team_id = :t AND status = 'rejected'"
)

# 洪水闸的串行化锁（**承重**：没有它，`cap` 的实际落地值 = 并发度而不是 cap）。
#
# 「数一次 pending → 决定收不收 → INSERT」是典型的 read-then-decide：无锁时 N 路并发
# 各自读到的都是对方写入之前的值，于是每一路都认为自己没到顶，N 路全放行。实测
# `cap=3` 落地 15 条（＝连接池上限）——原头注「并发下允许小幅越界」是失实的。
#
# **只锁注册路径，不碰热路径**：本语句只在 `_register` 里执行，而 `_register` 只在
# `_RESOLVE_SQL` 解析不到时才被调用。已认领账号的每一次拉取/写回（真实流量的 99.9%）
# 一次都不会取这把锁。锁的对象是**整个团队的注册动作**（粒度必须是团队，因为被保护的
# 共享资源就是「本团队待认领行数」这一个标量），代价是同团队的首见登记串行——而首见
# 登记本就是罕见事件，串行零感知。
#
# 键 = `(常量命名空间, team_id)`。用**两参数形式**并把命名空间放在第一位，是为了与
# `listing/service.py::allocate` 的 `pg_advisory_xact_lock(team_id, product_id)` 分开：
# 那一处第一参数是 team_id，本处第一参数是一个不可能成为 team_id 的大常量，故两族键
# 不会相撞（即便撞上也只是多一次无谓串行，不影响正确性——两条路径各自只取一把锁，
# 不存在死锁的环）。`xact` 版本＝随事务结束自动释放，不需要也不许手工解锁。
#
# **cap 之所以能精确，还依赖「`pending_claim` 行只有这一个生产者」**：
# `POST /buyer-accounts` 的 status 词表已收窄为人工四值（不含 `pending_claim`），
# 且 `_ALLOWED_TRANSITIONS` 禁止任何状态转回 `pending_claim`。哪天有人给那两处开口子，
# 这把锁就只锁住了半边，cap 会重新变成一个近似值。
_REGISTER_LOCK_NS = 1_302_043  # R2-13 + 0043，一个不会与真实 team_id 相撞的常量
_REGISTER_LOCK_SQL = "SELECT pg_advisory_xact_lock(:ns, :t)"

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
    """首见自动登记：落一条 `pending_claim` 行 + 通知 + 审计。撞洪水闸回 None。"""
    team_id = principal.team_id
    cfg = await dispatch.dispatch_config(session, team_id)
    # 洪水闸串行化：**必须在计数之前**取锁，否则数与插之间的窗口就是超发窗口
    # （理由与实测数字见 `_REGISTER_LOCK_SQL` 注释）。
    await session.execute(text(_REGISTER_LOCK_SQL), {"ns": _REGISTER_LOCK_NS, "t": team_id})
    pending = int((await session.execute(text(_PENDING_COUNT_SQL), {"t": team_id})).scalar_one())
    if pending >= cfg.pending_claim_cap:
        # fail-closed：宁可一个真实新号晚一天被发现，也不留「注册洪水无解」的形状。
        rejected = int(
            (await session.execute(text(_REJECTED_COUNT_SQL), {"t": team_id})).scalar_one()
        )
        log.warning(
            "plugin.customer.flood_refused",
            instance=principal.instance_id,
            team=team_id,
            pending=pending,
            rejected=rejected,
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
                # 实例号：告警正文的第一个可执行信息——处置手段（吊销哪一把令牌）按它走。
                # dedupe 按团队，故这里记的是**撞闸那一刻**的实例；同一轮里若有多台在灌，
                # 后面几台不会再发通知，要看全量请查结构化日志 `plugin.customer.flood_refused`。
                f"本次请求来自插件实例 #{principal.instance_id}"
                "（dedupe 按团队去重，同一轮里若有多台实例在灌，正文只会记下最先撞闸的那台"
                "——全量请查日志 `plugin.customer.flood_refused`）。\n"
                # rejected 现值：治理面 2 的残留观察手段。驳回只挡「同一个 id 再灌」，
                # 换个 id 照灌不误，而 rejected 行不占额度、平时也没有任何告警——
                # 这个数是判断「长期在被灌」还是「刚好来了一批真新号」的唯一现成信号。
                f"本团队已驳回（rejected）行现有 {rejected} 条：该数长期走高说明有人在"
                "**换 id 反复灌**（驳回只对同一个 customerId 粘住，不挡新伪造的 id），"
                "此时应查的是令牌而不是继续逐条驳回。\n"
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
        #
        # **advisory 锁之后这条路只剩两个来源**（不是死代码，别删）：
        # ① 手工预建 `POST /buyer-accounts`——它不取这把锁（也不该取：它不受洪水闸管），
        #    两路撞同一个 customerId 时本 INSERT 会命中 `uq_buyer_account`；
        # ② 本请求进锁之前，另一路已经登记完并提交（等锁期间对方已经走完）。
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
    # 审计留痕（D-Q16 唯一出口纪律：写操作一律经 AuditWriter，不裸 INSERT audit_log）。
    #
    # **为什么通知之外还要落审计**：通知面会被清理、被静音、被 dedupe 折叠，而
    # `audit_log` 是 append-only（无 UPDATE/DELETE 授权 + 无对应策略，0002）。买家账号池
    # 里凭空多出来的行「什么时候来的、哪台实例带来的」，长期只有审计答得上；且它与人工
    # 建号（`buyer_account.create`）落在同一张表、同一个 `object_type`，账号池的完整
    # 来历在一次查询里就能拉齐。
    #
    # `actor_type='system'`（`AuditWriter` 的默认值）：`ck_audit_actor` 的词表是
    # `('user','portal','system')`，**不含 `plugin`**。本轮不扩那条 CHECK——它是 0002 的
    # 全局约束，为一个登记动作改它属于跨域改动；`system` 是既有的机器侧惯例（`actor_id`
    # 留空，因为实例不是 `app_user`，塞实例 id 进去就是把两个 id 空间混成一个）。
    # **实例号不丢**：它写在 `after.instance_id` 里（以及 `note` 与通知正文里）。
    await AuditWriter(session, team_id=team_id).log(
        "buyer_account.auto_register",
        "buyer_account",
        account_id,
        after={
            "external_customer_id": customer_id,
            "status": "pending_claim",
            "instance_id": principal.instance_id,
        },
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
