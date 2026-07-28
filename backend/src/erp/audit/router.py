"""audit 域路由（契约 Audit 段）。R1-10：同步执行单品审核；R2-09 增量3a：纯手工批量送审。"""

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.audit import batch, service
from erp.core.audit import AuditWriter
from erp.core.authn import CurrentUser, require_permission
from erp.core.db import get_session, get_session_factory
from erp.core.errors import BusinessError
from erp.core.idempotency import run_idempotent
from erp.identity.schemas import Page

audit_router = APIRouter(tags=["Audit"])

# Idempotency-Key（契约 002 必填头）：同键同载荷重放存储响应，异载荷 409。
# 形制抄 pricing/router.py:63、listing/router.py:160。
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=64)]


class AuditTriggerIn(BaseModel):
    levels: list[str] | None = Field(
        default=None, description="默认 l0/l2/l3；取值限 l0..l4，非法值 422 AUDIT_LEVELS_INVALID"
    )
    trigger_kind: str = Field(default="manual", pattern="^(auto|manual|batch|re_audit)$")


class AuditBatchIn(BaseModel):
    """批量送审入参。

    `product_ids` **不设上限**（Owner 2026-07-28 裁定）：成本由墙钟预算约束，不由件数
    约束——`max_length` 只会让「勾了 201 件」整批 422，而真正要防的（跑太久把网关拖到
    超时）它一件也防不住。超预算的部分由 `batch.audit_batch` 返回进 `remaining`。

    也**不设** `min_length=1`：空列表返回四个空桶的 200，结构不变量 0==0 照样成立；
    加了反而会落进 FastAPI 的默认校验信封（`{"detail": [...]}`，无错误码），绕过 002
    统一错误信封——automation/router.py 的 `AutomationPolicyIn` 已吃过这个亏。

    **不接受 `trigger_kind`**：批量就是批量，服务端恒写 'batch'。留给客户端填 = 允许
    它把批量伪装成 manual/auto，`audit_run.trigger_kind` 这列的统计立刻失真。
    """

    product_ids: list[int] = Field(description="产品 ID 列表；服务端去重保序，无数量上限")
    levels: list[str] | None = Field(
        default=None,
        description="默认 l0/l2/l3；取值限 l0..l4（非法值 422 AUDIT_LEVELS_INVALID），l4 未开放",
    )


_VALID_LEVELS = frozenset({"l0", "l1", "l2", "l3", "l4"})


def _resolve_levels(raw: list[str] | None) -> list[str]:
    """入参 levels → 实际执行层级；非法值与 l4 在此统一判掉（单品与批量共用一段）。

    **为什么必须校验**：契约 002 早就给 `levels` 声明了 `enum: [l0..l4]`，实现侧此前
    没做。`service.audit_one` 里 L0/L1/L2/L3 四段都是 `if "lN" in levels` 的**精确
    匹配**——`levels=["L0"]`（大小写笔误）或 `["x"]` 一个分支都不命中，`verdict`
    保持初值 `pass` 直接走单事务快路径落库，产品当场变 `audit_passed` 而一次检查
    都没跑。这不需要恶意，一个大小写写错的客户端即可触发；批量把影响面从一件放大
    到一批（审查 2026-07-28 阻塞项）。

    **为什么不写成 `list[Literal[...]]` 交给 pydantic**：那样落进 FastAPI 的
    `{"detail": [...]}` 默认校验信封（无 `error.code`），绕过 002 统一错误信封，
    前端只显示「请求失败」——`AuditBatchIn` 的头注已就 `min_length` 交代过同一个亏。
    """
    levels = raw or service.DEFAULT_LEVELS
    unknown = sorted({x for x in levels if x not in _VALID_LEVELS})
    if unknown:
        raise BusinessError("AUDIT_LEVELS_INVALID", f"未知审核层级：{'、'.join(unknown)}")
    if "l4" in levels:
        raise BusinessError("AUDIT_L4_DISABLED", "L4 视觉审核未开放（R2）")
    return levels


class AuditRunOut(BaseModel):
    id: int
    product_id: int
    trigger_kind: str
    levels_requested: list[str]
    status: str
    verdict: str | None
    reject_level: str | None
    llm_cost_usd: float
    cache_hit_rate: float | None
    duration_ms: int | None
    created_at: datetime


@audit_router.post("/products/{product_id}/audit", status_code=201)
async def trigger_audit(
    product_id: int,
    body: AuditTriggerIn,
    request: Request,
    user: Annotated[CurrentUser, Depends(require_permission("audit.run"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    levels = _resolve_levels(body.levels)
    # RS-03a：审核自管短事务（sessionmaker + 用户 GUC 上下文），LLM 调用期间不持
    # 行锁/事务；本请求事务（session）只承载末尾的 audit_log 写
    result = await service.audit_one(
        get_session_factory(),
        product_id=product_id,
        team_id=user.team_id,
        is_super=user.is_super,
        trigger_kind=body.trigger_kind,
        levels=levels,
        created_by=user.id,
    )
    await AuditWriter.for_user(session, user, request).log(
        "audit.run", "product", product_id, after={"verdict": result["verdict"]}
    )
    return result


def _require_team(user: CurrentUser) -> int:
    """超管的 team_id 可能为 None；批量必须落到具体团队。

    仓内既有同形（`pricing/router.py:84-87`、`listing/router.py:172`、
    `automation/router.py:86-94`）。不用 `user.team_id or -1` 那套哨兵：预检 SELECT
    会静默查空 → 全批「产品不存在」，超管看到的是一屏假话而不是报错。
    """
    if user.team_id is None:
        raise BusinessError("AUDIT_TEAM_REQUIRED", "超管需切换到具体团队（X-Act-Team）")
    return user.team_id


@audit_router.post("/products/audit")
async def trigger_audit_batch(
    body: AuditBatchIn,
    request: Request,
    idempotency_key: IdemKey,
    user: Annotated[CurrentUser, Depends(require_permission("audit.run"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    """批量送审（纯手工；不读 automation_policy、不接三档）。同步 200 返回四桶结果。

    闸序：团队守卫 → levels 闸（非法层级 / l4）→ `run_idempotent(handler=audit_batch)`
    → 一批一条审计。**前两闸必须在幂等占位之前**——它们是「零金钱代价的整批 4xx」，
    占位后再抛会走 `run_idempotent` 的错误清理路径，白开两次事务。

    业务层整批 4xx 只有这四类（团队缺失 / 非法 levels / l4 / 幂等冲突）；其余一律
    逐条进桶、HTTP 恒 200，理由见 `audit/batch.py` 模块头注第二节（前 N-1 条已落库
    且无补偿路径）。**另有一类不到业务层的 422**：缺 `product_ids`、元素非整数、
    缺 `Idempotency-Key` 头由 pydantic/FastAPI 直接拒，走默认 `{"detail": [...]}`
    信封而非 002 信封（全仓无 RequestValidationError handler）。
    """
    team_id = _require_team(user)
    levels = _resolve_levels(body.levels)  # 与单品端点同一段判定，口径不许分叉

    async def _handler() -> dict[str, Any]:
        return await batch.audit_batch(
            get_session_factory(),
            product_ids=body.product_ids,
            team_id=team_id,
            is_super=user.is_super,
            levels=levels,
            created_by=user.id,
        )

    result = await run_idempotent(
        get_session_factory(),
        team_id=team_id,
        is_super=user.is_super,
        endpoint="POST /products/audit",
        idem_key=idempotency_key,
        payload=body.model_dump(),
        created_by=user.id,
        handler=_handler,
        stale_minutes=batch._BATCH_AUDIT_STALE_MINUTES,
    )
    # 一批一条：逐条写审计会让「谁在什么时候送了哪一批」淹在几百行里。
    # 重放那次照样记一行并标 idempotent_replay——「谁重发过」本身是要留痕的事实。
    # 带上 run_ids：单品路径的 audit_log 有 object_id=product_id，批量路径没有
    # （一行对一批），只留四个计数就回答不了「谁在什么时候送审了产品 X」。run_ids
    # 把跨表链条锚死（run_id → audit_run.product_id）。**只存 run_ids 不存
    # product_ids**：入参无数量上限，几万个 id 会把这一行 jsonb 撑爆；而 run_ids
    # 天然受墙钟预算约束（跑得完才有 run）。
    await AuditWriter.for_user(session, user, request).log(
        "audit.run_batch", "product", None,
        after={"audited": len(result["audited"]), "skipped": len(result["skipped"]),
               "failed": len(result["failed"]), "remaining": len(result["remaining"]),
               "run_ids": [i["run_id"] for i in result["audited"]],
               "idempotent_replay": bool(result.get("idempotent_replay"))},
    )  # fmt: skip
    return result


@audit_router.get("/audit-runs")
async def list_runs(
    user: Annotated[CurrentUser, Depends(require_permission("audit.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
    product_id: int | None = Query(default=None),
    verdict: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
) -> Page[AuditRunOut]:
    where = "WHERE team_id = :team"
    params: dict[str, Any] = {"team": user.team_id}
    if product_id:
        where += " AND product_id = :p"
        params["p"] = product_id
    if verdict:
        where += " AND verdict = :v"
        params["v"] = verdict
    total = (
        await session.execute(text(f"SELECT count(*) FROM app.audit_run {where}"), params)
    ).scalar_one()
    rows = (
        await session.execute(
            text(
                "SELECT id, product_id, trigger_kind, levels_requested, status, verdict,"
                " reject_level, llm_cost_usd, cache_hit_rate, duration_ms, created_at"
                f" FROM app.audit_run {where}"
                " ORDER BY created_at DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return Page(items=[AuditRunOut(**r) for r in rows], total=total, page=page, size=size)


@audit_router.get("/audit-runs/{run_id}")
async def get_run(
    run_id: int,
    user: Annotated[CurrentUser, Depends(require_permission("audit.read"))],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, Any]:
    run = (
        (
            await session.execute(
                text(
                    "SELECT id, product_id, trigger_kind, levels_requested, status, verdict,"
                    " reject_level, llm_cost_usd, cache_hit_rate, duration_ms,"
                    " started_at, finished_at, created_at"
                    " FROM app.audit_run WHERE id = :r"
                ),
                {"r": run_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if run is None:
        raise BusinessError("AUDIT_RUN_NOT_FOUND", "审核运行不存在")
    hits = (
        await session.execute(
            text(
                "SELECT level, rule_code, is_hard, score, evidence, created_at"
                " FROM app.audit_hit WHERE run_id = :r ORDER BY id"
            ),
            {"r": run_id},
        )
    ).mappings()
    return {**dict(run), "hits": [dict(h) for h in hits]}
