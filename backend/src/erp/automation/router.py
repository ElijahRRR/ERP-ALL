"""自动化档位面板 API（R2-09 增量2；契约 002 §Automation）。

两个端点：档位总览（GET）与置档（PUT）。本增量**只做面板**，不接任何新 flow 的
消费点（那是增量3-5）——面板先可见可改，接线随后。

## 三态在 API 层显性区分（Owner 2026-07-27 Q3 裁定）

`unset`（本团队无行）/ `disabled`（有行但 enabled=false）/ `configured`。
**不许压成单个 `mode` 字段**：压了之后前端再想区分也区分不出来，而
「看起来配着、实际没生效」是最难发现的一类故障。

## 无行的 flow 也返回（PR#35 教训）

返回的是「`FLOWS` 注册表 ∪ 本团队行」的合并结果，10 条恒定。零行团队若只返回库中行
就会得到空列表——面板上没有行可点，也就永远配不上第一档。

## 关闭 = enabled=false 或 mode=manual，**不是删行**

`0025_order_domain.py:357-362` 只 `GRANT SELECT, INSERT, UPDATE`，无 DELETE。
故 PUT 一律 `ON CONFLICT (team_id, flow_code) DO UPDATE`。

## PUT 不接受 `config`

`automation_policy.config` 全仓零消费点（`amount_ceiling` / `daily_cap` /
`price_delta_pct` / `check_kinds` 四个护栏键在 `backend/src` 零命中）。开放写入只会
造成「护栏已配」的错觉——真正的护栏要等消费点落地。带 `config` 的请求**显式 422
`AUTOMATION_CONFIG_NOT_WRITABLE`**（走统一错误信封）而不是静默丢弃——静默丢弃正是
那种错觉的来源。此前用 `extra="forbid"` 实现同一意图，但那会落到 FastAPI 默认的
`{"detail": [...]}` 校验信封、绕过 002 的统一 Error 信封（审查 2026-07-28 指出），
故改为显式字段 + BusinessError。**代价**（如实记）：`config` 之外的未知字段现在被
pydantic 静默忽略（如拼错的 `mod`）——REST 常态行为，但不再报错。
"""

from datetime import datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.audit import AuditWriter
from erp.core.authn import CurrentUser, require_permission
from erp.core.automation import (
    FLOWS,
    WIRED_FLOWS,
    AutomationFlow,
    Evaluation,
    Mode,
    PolicyState,
    view_policy,
)
from erp.core.db import get_session
from erp.core.errors import BusinessError

automation_router = APIRouter(tags=["Automation"])

log = structlog.get_logger()

# 面板上档位从左到右的固定顺序。**不能 sorted(frozenset)**——那会得到 auto/manual/semi，
# 保守档跑到中间去，误点的代价不对称。
_MODE_ORDER = (Mode.MANUAL, Mode.SEMI, Mode.AUTO)

_ROW_COLS = "flow_code, mode, enabled, config, updated_at, updated_by"


def _legal_modes(flow: AutomationFlow) -> list[Mode]:
    legal = FLOWS[flow].legal_modes
    return [m for m in _MODE_ORDER if m in legal]


def _parse_flow(flow_code: str) -> AutomationFlow:
    """未知 flow_code → 404。**枚举是唯一权威**（§09 v2.1 的代码镜像）。"""
    try:
        return AutomationFlow(flow_code)
    except ValueError as exc:
        raise BusinessError(
            "AUTOMATION_FLOW_UNKNOWN",
            f"未知的自动化流程码：{flow_code}",
            {"known": [f.value for f in FLOWS]},
            http_status=404,
        ) from exc


def _require_team(user: CurrentUser) -> int:
    """超管的 team_id 可能为 None；写档位必须落到具体团队（表有 FK 到 app.team）。

    仓内既有同形（`pricing/router.py:84-87` 的 `PRICING_TEAM_REQUIRED`）。
    不用 `user.team_id or -1` 那套哨兵值：读路径上哨兵只是查空，写路径上会撞 FK 变成 500。
    """
    if user.team_id is None:
        raise BusinessError("AUTOMATION_TEAM_REQUIRED", "超管需切换到具体团队（X-Act-Team）")
    return user.team_id


class AutomationPolicyOut(BaseModel):
    """契约 002 `AutomationPolicy`。字段集须与 openapi 一字不差（全字段 required，
    可空字段是 nullable 不是 optional——「可为 null」和「可缺席」是两件事）。

    `state/effective_mode/evaluation/legal_modes` 用枚举类型而非裸 str：FastAPI 生成的
    schema 才带 enum，与 yaml 逐字一致（审查 R9）。`mode` 例外地保持 `str | None`——
    它要能原样透出绕过约束写进库的坏字符串，运维看得见坏值才能去修。
    """

    flow_code: str
    legal_modes: list[Mode]
    evaluation: Evaluation
    state: PolicyState
    mode: str | None
    enabled: bool | None
    effective_mode: Mode
    illegal_for_flow: bool
    # 闸类（legal_modes 无 semi）：manual 是放松不是收紧，前端据此挂危险确认与红字。
    # 由服务端从 FlowSpec 派生，**前端不许自己硬编码闸类清单**（审查 F4：硬编码会在
    # §09 改判闸类时静默失去确认框）。
    gate: bool
    # 已接消费点（core/automation.py WIRED_FLOWS）。false = 改档只落库、不改变任何行为，
    # 前端对这类 flow 不得渲染「拦截生效/不生效」（审查 F1）。
    wired: bool
    config: dict[str, Any] | None
    updated_at: datetime | None
    updated_by: int | None


class AutomationPolicyIn(BaseModel):
    # 不写成 Literal["manual","semi","auto"]、也不加 min/max_length：那些都会落到
    # FastAPI 默认校验错误（信封 {"detail": [...]}，无错误码、绕过 002 统一信封）。
    # mode 的一切取值校验统一收敛到服务层的 AUTOMATION_MODE_ILLEGAL 一个码——
    # 空串、超长串、"bogus" 全走同一条路（审查 F2：此前 min/max_length 把
    # 自己声称要规避的那个信封又请了回来）。
    mode: str
    # 必填，无默认：PUT 是「mode + enabled 整对提交」的全量置态，schema 必须与这句话
    # 一致。此前可省略且默认 true——对一条 enabled=false 的行只发 {"mode": ...} 会
    # 静默复启，落在 refund/cancel 上就是自动退款恢复（D-Q29 真钱，不可逆）。面板
    # 两条写路径本就整对提交，暴露面是直接调 API 的消费方；「openapi 里写了后果」
    # 不等于「schema 挡了」（审查 N2）。缺字段走 FastAPI 结构性 422（{"detail"} 信封，
    # 全仓既有缺口，已另行提单）——宁可信封不统一，不可静默复启。
    enabled: bool
    # 显式接住再拒绝（422 AUTOMATION_CONFIG_NOT_WRITABLE，统一信封），见模块头注。
    config: dict[str, Any] | None = None


def _to_out(flow: AutomationFlow, row: dict[str, Any] | None) -> AutomationPolicyOut:
    view = view_policy(
        flow,
        row_mode=None if row is None else str(row["mode"]),
        row_enabled=None if row is None else bool(row["enabled"]),
    )
    return AutomationPolicyOut(
        flow_code=flow.value,
        legal_modes=_legal_modes(flow),
        evaluation=FLOWS[flow].evaluation,
        state=view.state,
        mode=view.stored_mode,
        enabled=None if row is None else bool(row["enabled"]),
        effective_mode=view.effective_mode,
        illegal_for_flow=view.illegal_for_flow,
        gate=Mode.SEMI not in FLOWS[flow].legal_modes,
        wired=flow in WIRED_FLOWS,
        config=None if row is None else dict(row["config"]),
        updated_at=None if row is None else row["updated_at"],
        updated_by=None if row is None else row["updated_by"],
    )


@automation_router.get("/automation-policies")
async def list_automation_policies(
    user: Annotated[CurrentUser, Depends(require_permission("automation.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> list[AutomationPolicyOut]:
    """本团队全部 10 条 flow 的档位（注册表 ∪ 库中行；无行的返回 state=unset）。

    超管未带 `X-Act-Team` → 422 `AUTOMATION_TEAM_REQUIRED`，与 PUT 同口径。
    **不许**静默按 team_id=NULL 查——那会返回一屏「全 unset + 拦截不生效」的假面板
    （审查 F3：它描述的不是任何一个团队，而是「没有团队」）。

    **不分页**：行数由 `AutomationFlow` 枚举定死为 10，分页只会让「10 条全在」这条
    判据变难验，且面板本身就是一屏看完的东西。顺序即 §09 注册清单顺序（`FLOWS` 插入序）。
    """
    team_id = _require_team(user)
    rows = {
        str(r["flow_code"]): dict(r)
        for r in (
            await session.execute(
                text(f"SELECT {_ROW_COLS} FROM app.automation_policy WHERE team_id = :t"),
                {"t": team_id},
            )
        ).mappings()
    }
    # 库中 flow_code 不在枚举里的行（改名/历史遗留）不渲染，但必须留痕——
    # 静默吞掉就是又一处「发生了但看不见」（审查 F9）
    for orphan in sorted(set(rows) - {f.value for f in FLOWS}):
        log.warning("automation.unknown_flow_code_row", team_id=team_id, flow_code=orphan)
    return [_to_out(flow, rows.get(flow.value)) for flow in FLOWS]


@automation_router.put("/automation-policies/{flow_code}")
async def put_automation_policy(
    flow_code: str,
    body: AutomationPolicyIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("automation.write"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AutomationPolicyOut:
    """置本团队某 flow 的档位（upsert 全量置态）。

    不收 `Idempotency-Key`：全量置态天然幂等，且仓内 6 个 PUT 端点无一走
    `run_idempotent`（幂等头只挂 POST）。

    `mode` 的 flow 级合法性只能在这里挡——DB 的 `ck_automation_mode` 只约束
    `mode ∈ {manual,semi,auto}`、**不区分 flow**，而 §09 明写闸类只有两档。
    """
    flow = _parse_flow(flow_code)
    team_id = _require_team(user)
    if body.config is not None:
        raise BusinessError(
            "AUTOMATION_CONFIG_NOT_WRITABLE",
            "config 暂不可写：全仓尚无消费点，写入只会造成「护栏已配」的错觉；"
            "随消费点落地的增量开放",
            {"flow_code": flow.value},
        )
    # 第一段：取值必须是三档之一（空串/超长/"bogus" 都在这里挡，统一信封统一码）
    if body.mode not in {m.value for m in Mode}:
        raise BusinessError(
            "AUTOMATION_MODE_ILLEGAL",
            f"档位取值非法：{body.mode!r}，合法取值：manual/semi/auto",
            {"flow_code": flow.value, "legal_modes": [m.value for m in Mode], "got": body.mode},
        )
    # 第二段：flow 级合法性——**只在 enabled=true 时强制**。停用一条已存非法档位的行
    # （如 order_block 上遗留的 semi）必须走得通：停用是收紧（effective→manual），
    # 挡住它反而把运维锁在坏状态里（审查 R5）。停用态存非法值无危害：effective 恒 manual，
    # GET 的 illegal_for_flow 会如实标出；重新启用时本校验会拦住。
    legal = [m.value for m in _legal_modes(flow)]
    if body.enabled and body.mode not in legal:
        raise BusinessError(
            "AUTOMATION_MODE_ILLEGAL",
            f"档位 {body.mode} 对流程 {flow.value} 非法，合法档位：{'/'.join(legal)}",
            {"flow_code": flow.value, "legal_modes": legal, "got": body.mode},
        )

    # 审计 before 必须在写之前读（范式同 identity/router.py:330）
    before = (
        (
            await session.execute(
                text(
                    "SELECT mode, enabled FROM app.automation_policy"
                    " WHERE team_id = :t AND flow_code = :f FOR UPDATE"
                ),
                {"t": team_id, "f": flow.value},
            )
        )
        .mappings()
        .one_or_none()
    )

    row = (
        (
            await session.execute(
                text(
                    "INSERT INTO app.automation_policy"
                    " (team_id, flow_code, mode, enabled, updated_by)"
                    " VALUES (:t, :f, :m, :e, :by)"
                    " ON CONFLICT (team_id, flow_code) DO UPDATE"
                    " SET mode = excluded.mode, enabled = excluded.enabled,"
                    "     updated_by = excluded.updated_by"
                    f" RETURNING id, {_ROW_COLS}"
                ),
                {
                    "t": team_id,
                    "f": flow.value,
                    "m": body.mode,
                    "e": body.enabled,
                    "by": user.id,
                },
            )
        )
        .mappings()
        .one()
    )
    # updated_at 由 automation_policy_touch（BEFORE UPDATE 触发器，0025:313）维护，
    # INSERT 走列 DEFAULT now()；两条路径都不需要在这里手写。

    await AuditWriter.for_user(session, user, request).log(
        "automation.policy_set",
        "automation_policy",
        row["id"],
        before=None
        if before is None
        else {"flow_code": flow.value, "mode": before["mode"], "enabled": before["enabled"]},
        after={"flow_code": flow.value, "mode": body.mode, "enabled": body.enabled},
    )
    return _to_out(flow, dict(row))
