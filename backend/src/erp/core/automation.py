"""自动化三档内核（R2-09 增量1，D-Q13/29）。

**唯一权威是 `specs/001-domain-model/09-platform.md` 的 flow_code 注册清单（v2.1）**，
本模块的 `FLOWS` 是它的代码镜像；`tests/test_automation_flow_contract.py` 逐行比对两者，
对不上即 CI 红。增删 flow 必须**先改图纸**。

## 为什么要这个内核（不是为了整齐）

在此之前只有两处读点，各写各的 SQL（`order/procurement.py` 与 `aftersale/refund.py`），
两处对「没有行」「行被停用」「档位取值非法」的处理各自隐含、无人对账。三档要铺到 10 条
flow 上，再复制 8 遍就不可能保证一致。内核把这三种边角**收成一处**并写死方向。

## 三条边角，方向都取 manual（fail-closed），但**其中一条名不副实，已上报**

1. **无行** → manual。新团队 0 行即全人工，不会一上来就自动跑。
2. **`enabled = false`** → manual。**这一条会记 warn**——见下。
3. **档位取值不在该 flow 的合法集** → manual + warn。DB 的 `ck_automation_mode` 只约束
   `mode ∈ {manual,semi,auto}`，**不区分 flow**，而 §09 明写 `order_block` /
   `compliance_block` 只有两档。所以「`order_block` 被写成 semi」在库层是合法的，
   只能在这里挡。

**⚠️ 「manual 即最保守」对闸类 flow 不成立。** `order_block` 的 `auto` 才是「拦截 flagged
单」，`manual` 是「只软标记不冻结」——所以对它来说退回 manual 是**放松**而不是收紧。
上面 2/3 两条落到 `order_block` 上就成了「误停用/写错值 = 订单拦截静默失效」。

本增量声明**行为零变化**，故这里**只补 warn 不改语义**：真要改成「闸类 flow 停用需显式
确认、或退回 auto」属语义变更，按 CLAUDE.md 的升级清单归 Owner 裁定——已随 R2-09 立项
批注回传（`.agent/evidence/R2-09/owner-questions-20260727.md` 的 Q3）。

## 求值语义（§09 逐 flow 定死，不留「未定」）

- **`realtime`**：每次决策直读本表，切档对**下一次决策**即生效；
- **`snapshot`**：档位在请求创建时固化（`refund_request.mode_applied` 已如此实现），
  切档**不影响在途请求**——这是正确行为，验收时不得判为「未生效」。

**不进缓存**：档位不走 ConfigService / Redis 广播。实测该缓存无业务读者，且 config bus
是 **fail-open** 的，而档位闸必须 **fail-closed**——用 fail-open 的载体承载 fail-closed
的判据是错的。若将来引入带缓存的解析器，必须把本表写面接进 `CONFIG_INVALIDATE_CHANNEL`。
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

log = structlog.get_logger()


class Mode(StrEnum):
    MANUAL = "manual"
    SEMI = "semi"
    AUTO = "auto"


class Evaluation(StrEnum):
    REALTIME = "realtime"
    SNAPSHOT = "snapshot"


class AutomationFlow(StrEnum):
    """§09 v2.1 注册清单的代码镜像，**10 条**。"""

    SCRAPE_TO_AUDIT = "scrape_to_audit"
    AUDIT_TO_LISTING = "audit_to_listing"
    LISTING_DISPATCH = "listing_dispatch"
    PRICING_WATCH = "pricing_watch"
    ORDER_BLOCK = "order_block"
    COMPLIANCE_BLOCK = "compliance_block"
    REFUND = "refund"
    CANCEL = "cancel"
    # R2-13 才接线。**有意的前置登记**：§09 明写「趁 R2-09 尚未把枚举写死进代码时补入，
    # 避免上线后改枚举与消费点」。故本条现无消费点，契约判据不得要求「每个 flow 都有
    # 消费点」——那会让它第一天就红。
    PURCHASE_EXECUTE = "purchase_execute"
    MAINTENANCE_RUN = "maintenance_run"


_THREE = frozenset({Mode.MANUAL, Mode.SEMI, Mode.AUTO})
# 闸类：§09「不是隐藏第三个选项，是本来就没有 semi」。面板对这两条渲染二选一。
_TWO = frozenset({Mode.MANUAL, Mode.AUTO})


@dataclass(frozen=True)
class FlowSpec:
    legal_modes: frozenset[Mode]
    evaluation: Evaluation


FLOWS: dict[AutomationFlow, FlowSpec] = {
    AutomationFlow.SCRAPE_TO_AUDIT: FlowSpec(_THREE, Evaluation.REALTIME),
    AutomationFlow.AUDIT_TO_LISTING: FlowSpec(_THREE, Evaluation.REALTIME),
    AutomationFlow.LISTING_DISPATCH: FlowSpec(_THREE, Evaluation.REALTIME),
    AutomationFlow.PRICING_WATCH: FlowSpec(_THREE, Evaluation.REALTIME),
    AutomationFlow.ORDER_BLOCK: FlowSpec(_TWO, Evaluation.REALTIME),
    AutomationFlow.COMPLIANCE_BLOCK: FlowSpec(_TWO, Evaluation.REALTIME),
    AutomationFlow.REFUND: FlowSpec(_THREE, Evaluation.SNAPSHOT),
    AutomationFlow.CANCEL: FlowSpec(_THREE, Evaluation.SNAPSHOT),
    AutomationFlow.PURCHASE_EXECUTE: FlowSpec(_THREE, Evaluation.SNAPSHOT),
    AutomationFlow.MAINTENANCE_RUN: FlowSpec(_THREE, Evaluation.REALTIME),
}

# **已接消费点**的 flow——真的有代码在读 `resolve_mode` 并据此改变行为。
# 这是**代码事实**不是图纸事实（图纸把 10 条都登记了，接线随增量3-5 逐步来），
# 所以只能维护在这里，并靠纪律：**新增消费点的那个 PR 必须同步把 flow 加进本表**。
# 面板据此决定敢不敢说「拦截生效/不生效」——对没接线的 flow 说这两句话都是假话
# （审查 2026-07-28 F1：compliance_block 零消费点却被面板断言「拦截生效」，
# 与「compose 从未注入 ERP_ENV 而判据全绿」同形）。
# 当前三条：order_block（order/procurement.py:38）、refund/cancel（aftersale/refund.py:41）。
WIRED_FLOWS: frozenset[AutomationFlow] = frozenset(
    {AutomationFlow.ORDER_BLOCK, AutomationFlow.REFUND, AutomationFlow.CANCEL}
)


class PolicyState(StrEnum):
    """面板三态（Owner Q3 裁定）。

    **API 层不许把三态压成一个 `mode` 字段**——压了之后前端再想区分也区分不出来，
    而「配着但已停用」正是最难发现的一类故障（本模块头注同款理由）。
    """

    UNSET = "unset"  # 本团队无行：等同人工，且从未配置过
    DISABLED = "disabled"  # 有行但 enabled=false：等同人工，但**是被人关掉的**
    CONFIGURED = "configured"  # 有行且启用


@dataclass(frozen=True)
class PolicyView:
    """一条 (team, flow) 的完整判读。`resolve_mode` 与面板 API 共用同一份。"""

    state: PolicyState
    stored_mode: str | None  # 库中原值（无行=None）；取值非法时原样保留，便于运维看见坏值
    mode: Mode | None  # 解析成功的档位；无行 / 取值不在 Mode 内 = None
    effective_mode: Mode  # **等于 resolve_mode 的返回值**
    illegal_for_flow: bool  # 库中档位不在该 flow 的 legal_modes（含无法解析）
    warn: Literal["disabled", "unknown_mode", "illegal_for_flow"] | None


def view_policy(
    flow: AutomationFlow, *, row_mode: str | None, row_enabled: bool | None
) -> PolicyView:
    """纯函数：把 `automation_policy` 的一行（或「无行」）判读成三态 + 生效档位。

    抽出来的唯一理由：**面板显示的与订单闸实际执行的必须是同一个判断**。各写一份
    就会重演内核当初要消灭的东西（两处读点对三条边角各自隐含、无人对账）。

    `warn` 只把「该发哪条告警」返出去、不落日志——纯函数才能在 API 的列表路径上
    白跑 10 次而不刷屏。日志由 `resolve_mode` 发，事件名与字段与抽取前逐字一致。

    `row_mode` 与 `row_enabled` 必须成对传（同一行的两列），无行时两者都传 None。
    """
    legal = FLOWS[flow].legal_modes
    if row_mode is None:
        return PolicyView(PolicyState.UNSET, None, None, Mode.MANUAL, False, None)
    try:
        parsed: Mode | None = Mode(row_mode)
    except ValueError:  # DB CHECK 已挡住，除非有人绕过约束直接改库
        parsed = None
    illegal = parsed is None or parsed not in legal
    if not row_enabled:
        # 停用 → manual。**这对闸类 flow 是放松**（manual=只软标记不冻结），
        # 即「误停用 = 订单拦截静默失效」。只留痕不改语义，见本模块头注。
        return PolicyView(PolicyState.DISABLED, row_mode, parsed, Mode.MANUAL, illegal, "disabled")
    if parsed is None:
        return PolicyView(PolicyState.CONFIGURED, row_mode, None, Mode.MANUAL, True, "unknown_mode")
    # 非法档位**只告警不改写**：`order_block` 上存着 `semi` 时现行代码是拦截的，
    # 归 manual 会让该团队静默失去订单拦截，而本内核声明行为零变化。
    return PolicyView(
        PolicyState.CONFIGURED,
        row_mode,
        parsed,
        parsed,
        illegal,
        "illegal_for_flow" if illegal else None,
    )


async def resolve_mode(session: AsyncSession, *, team_id: int, flow: AutomationFlow) -> Mode:
    """→ 该团队该 flow 的当前档位。三种边角一律回 `manual` 并留痕（见模块头注）。

    判读逻辑在 `view_policy`（纯函数），**面板 API 共用同一份**——两边各算一份就会
    出现「面板显示 auto、闸实际按 manual 跑」而无人发现。本函数只负责取行与落日志。

    调用方一律用本函数，**不要再各写各的 SQL**——那正是内核要消灭的东西。
    `snapshot` 型 flow 由调用方在创建请求时把结果固化进自己的 `mode_applied` 列。
    """
    row = (
        await session.execute(
            text(
                "SELECT mode, enabled FROM app.automation_policy"
                " WHERE team_id = :t AND flow_code = :f"
            ),
            {"t": team_id, "f": flow.value},
        )
    ).first()
    view = view_policy(
        flow,
        row_mode=None if row is None else str(row.mode),
        row_enabled=None if row is None else bool(row.enabled),
    )
    if view.warn == "disabled":
        # 「配着但没生效」是最难发现的一类故障，必须留痕（Q3）
        log.warning(
            "automation.policy_disabled_treated_as_manual",
            team_id=team_id,
            flow=flow.value,
            stored_mode=view.stored_mode,
        )
    elif view.warn == "unknown_mode":
        log.warning(
            "automation.unknown_mode_treated_as_manual",
            team_id=team_id,
            flow=flow.value,
            stored_mode=view.stored_mode,
        )
    elif view.warn == "illegal_for_flow":
        log.warning(
            "automation.illegal_mode_for_flow",
            team_id=team_id,
            flow=flow.value,
            stored_mode=view.stored_mode,
            legal=sorted(m.value for m in FLOWS[flow].legal_modes),
        )
    return view.effective_mode
