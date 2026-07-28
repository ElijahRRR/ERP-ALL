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


async def resolve_mode(session: AsyncSession, *, team_id: int, flow: AutomationFlow) -> Mode:
    """→ 该团队该 flow 的当前档位。三种边角一律回 `manual` 并留痕（见模块头注）。

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
    if row is None:
        return Mode.MANUAL
    if not row.enabled:
        # 「配着但没生效」是最难发现的一类故障，必须留痕（Q3）
        log.warning(
            "automation.policy_disabled_treated_as_manual",
            team_id=team_id,
            flow=flow.value,
            stored_mode=row.mode,
        )
        return Mode.MANUAL
    try:
        mode = Mode(row.mode)
    except ValueError:  # DB CHECK 已挡住，除非有人绕过约束直接改库
        log.warning(
            "automation.unknown_mode_treated_as_manual",
            team_id=team_id,
            flow=flow.value,
            stored_mode=row.mode,
        )
        return Mode.MANUAL
    if mode not in FLOWS[flow].legal_modes:
        # **只告警，不改写**。差点写成「一律归 manual」，那对闸类 flow 是**放松**：
        # `order_block` 上若存着 `semi`，现行代码是拦截的（`mode in ("semi","auto")`），
        # 归 manual 会让该团队静默失去订单拦截——而本增量声明行为零变化。
        # 「非法档位该怎么处理」与 Q3 同族，归 Owner 裁定；在那之前照原样返回。
        log.warning(
            "automation.illegal_mode_for_flow",
            team_id=team_id,
            flow=flow.value,
            stored_mode=mode.value,
            legal=sorted(m.value for m in FLOWS[flow].legal_modes),
        )
    return mode
