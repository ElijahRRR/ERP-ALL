"""批量送审执行体（R2-09 增量3a；**纯手工路径**）。

把「一个一个点送审」变成「勾一批点一次」。本模块**不读 automation_policy、不调
resolve_mode、不接三档、不起 beat**——三档接线是另一张工单的事，这里只做人工批量。

单品编排一行未动：逐条调用 `service.audit_one(..., trigger_kind="batch", ...)`。
**这是刻意的**——`audit_one` 的三段式（tx1 → HTTP → tx2）已两轮评审，批量若另写一套
就等于把「批量与单品对拍」这条核心判据变成对拍两份不同实现，判据当场失去意义。

## 一、为什么外层无事务

RS-03a 的教训：LLM 调用最长 120s，包在事务里就是**持 product 行锁调网络**——期间
任何人改这批产品全部阻塞，连接池也被一条慢请求占死。`audit_one` 为此拆成三段短事务，
HTTP 段不持任何锁。批量若在外层再包一个大事务（`async with sessions.begin()` 罩住整个
循环），三段式的全部收益当场归零，且锁的范围从 1 行放大到 N 行、持锁时长从单条放大到
整批——**比修复前更糟**。所以本模块只在两处开短事务：入口预检一次、熔断判定一次，
其余全部下沉给 `audit_one` 自管。`tests/db/test_audit_batch.py::T8` 用旁路连接
`FOR UPDATE NOWAIT` 把这条钉死。

## 二、为什么逐条结果不是全或无

`audit_one` 每条自己 COMMIT 两次：tx1 落 `audit_run` 并把 product 置 `auditing`，
tx2 写终局。**第 N 条失败时，前 N-1 条已经落库、LLM 的钱已经花掉，且审核域没有
DELETE 授权，补偿路径根本不存在**。此时整批返回 4xx 就是「响应说失败、库里说成功了
N-1 条」的谎——运维照响应重发，钱花第二遍。故逐条异常一律进桶（skipped/failed），
HTTP 恒 200。

整批 4xx 只留三类，且**都在任何一条开审之前判定、零金钱代价**：团队缺失（422）、
levels 含 l4（422）、幂等键冲突（409）。这三类由 router 判，不在本模块。

## 三、两个刹车覆盖两种不同的故障形状

`frontend/nginx.conf` 的 `location /api/` 置 `proxy_read_timeout 120s`，而单条最坏
耗时可达 **240s**（`audit/llm.py` 的 `HTTP_TIMEOUT_SECONDS` × `MAX_ATTEMPTS`）。

⚠️ **刹车不保证 120s 内返回，别照着这个理解调常量**：预算只在**开审前**检查，
t=89.9s 放行的那一条仍可跑满 240s，故本端点真实上界 = `_BATCH_DEADLINE_SECONDS +
_WORST_ITEM_SECONDS` = **330s**，是网关 120s 的 2.75 倍。越界那次由「504 → 前端
稳定幂等键同键重试 → 命中占位/存储响应」这条链兜底（`BatchAuditModal` 的 NO_RESULT
档），**因此 `_BATCH_AUDIT_STALE_MINUTES` 必须 > 330s**（T11 钉这条）。刹车真正
买到的是「一次请求最多花多少钱、最多留多少条 run」，不是「一定赶在网关之前返回」。

- **墙钟预算** `_BATCH_DEADLINE_SECONDS`：每条**开审前**检查已耗时，超了则剩余
  全部进 `remaining`。它拦的是「provider 挂死/极慢」——每条都要等满超时，件数不多
  也能拖垮整批。注意它对「第一条就挂死」这种形状不起作用（第一条永远放行），
  那种情况下网关必然先掐断，靠的正是上面那条 504 重试链。
- **provider 熔断** `_BREAKER_CONSECUTIVE`：连续 N 条 `needs_review` 且这 N 条 run
  **都带 `rule_code='llm_unavailable'` 的 audit_hit** 才跳闸。它拦的是「provider
  快速报错」——每条 50ms 就返回 500，墙钟预算永远够用，只有熔断拦得住；否则一个批
  能把几百件产品全刷成 `needs_review`，人工复核队列被垃圾灌满。
  判据故意写得窄：非连续不跳闸、`l3_policy_missing` 这类**真判定**不跳闸——那是本地
  配置问题，跟 provider 死活无关，误熔断会把一批本该跑完的产品截在半路。

两个刹车都不是错误：被刹下的 id 进 `remaining`（裸 id 数组），语义是「本次没开审，
原样重发即可」——没有 run、没有花钱、product 状态未变。

## 四、结构不变量

`len(audited) + len(skipped) + len(failed) + len(remaining) == len(去重后入参)`，
且四者 id 并集 == 去重入参、两两不交。调用方（前端/运维）据此判「有没有漏掉谁」，
不需要理解四个桶各自的语义。
"""

import time
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.audit import llm, service
from erp.core.db import ctx_tx
from erp.core.errors import BusinessError

log = structlog.get_logger()

# 墙钟预算：一次请求最多花多少钱、最多留多少条 run 的上限。**不是**「120s 内一定
# 返回」的保证——见模块头注第三节（真实上界 = 本值 + _WORST_ITEM_SECONDS = 330s）。
_BATCH_DEADLINE_SECONDS = 90

# 单条最坏耗时。**从 llm.py 派生而不是抄字面量**：调大 provider 超时或重试次数时，
# 「预算 + 最坏单条 ≤ 幂等占位失效阈值」这条不变量（T11）才会跟着动。抄成 240 的话，
# 有人把 `HTTP_TIMEOUT_SECONDS` 调到 600 时 T11 照样绿，而不变量已经破了。
_WORST_ITEM_SECONDS = llm.HTTP_TIMEOUT_SECONDS * llm.MAX_ATTEMPTS

# 进 `skipped` 的码白名单：只有**开审之前**就被判掉的两个码算「零金钱代价」
# （契约 AuditBatchResult.skipped 是这么向调用方承诺的）。今天 `audit_one` 恰好只在
# `audit_run` INSERT 之前抛这两个，但那是巧合不是保证——将来若有人在 tx2 里加一个
# BusinessError（例：回写时发现产品已被删），它已经建了 run、已经花了钱，落进
# skipped 就是「响应对成本说谎」。故未知 BusinessError 一律进 `failed`（语义是
# 「可能已扣费」，方向保守）。
_SKIPPED_CODES = frozenset({"AUDIT_PRODUCT_NOT_FOUND", "AUDIT_STATE_INVALID"})

# 熔断阈值：连续多少条 llm_unavailable 的 needs_review 判定 provider 已挂。
_BREAKER_CONSECUTIVE = 3

# 幂等占位失效阈值（分钟）。必须 > 本端点最坏执行时长，否则首个 handler 还在跑时
# 同键重试会重占占位并发双跑（RS-03b 评审发现 12 的原样复用）。
# 不变量：_BATCH_DEADLINE_SECONDS + _WORST_ITEM_SECONDS <= _BATCH_AUDIT_STALE_MINUTES * 60
_BATCH_AUDIT_STALE_MINUTES = 30


async def _provider_looks_down(
    sessions: async_sessionmaker[AsyncSession],
    *,
    team_id: int,
    is_super: bool,
    run_ids: list[int],
) -> bool:
    """这批 run 是否**全部**因 provider 不可用而落 needs_review（短事务，一次查完）。

    只认 `llm_unavailable` 这一个码：它由 `service.audit_one` 在 HTTP 段抛异常时写入
    （超时/非 200/缺字段/缺 key）。`l3_policy_missing`、`llm_needs_review`（模型输出
    坏但通了）都是**真判定**，不是 provider 死了，不能据此熔断。
    """
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as s:
        n = (
            await s.execute(
                text(
                    "SELECT count(*) FROM app.audit_hit"
                    " WHERE run_id = ANY(:ids) AND rule_code = 'llm_unavailable'"
                ),
                {"ids": run_ids},
            )
        ).scalar_one()
    return int(n) == len(run_ids)


async def audit_batch(
    sessions: async_sessionmaker[AsyncSession],
    *,
    product_ids: list[int],
    team_id: int,
    is_super: bool = False,
    levels: list[str] | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    """逐条送审一批产品，同步返回四桶结果。**不抛业务异常**（除非 DB 本身不可用）。

    入参先去重保序（`dict.fromkeys`）——同一 id 重复出现只审一次，否则「勾错了勾两下」
    就变成付两次 LLM 的钱、留两条 run。
    """
    t0 = time.monotonic()  # 墙钟从 handler 入口起算：预检查询也占请求预算
    ordered = list(dict.fromkeys(int(p) for p in product_ids))

    # 预检：一次查完本团队可见的 id。差集直接进 skipped，**不逐个进 audit_one 试错**
    # ——那会为每个坏 id 白开一次事务。RLS 之外再显式带 team_id 条件：不存在与越权
    # 走同一个码同一句话，防按响应差异探测他团队的 id 空间。
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as s:
        known = {
            int(r)
            for r in (
                await s.execute(
                    text("SELECT id FROM app.product WHERE team_id = :t AND id = ANY(:ids)"),
                    {"t": team_id, "ids": ordered},
                )
            )
            .scalars()
            .all()
        }

    audited: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    remaining: list[int] = []

    streak: list[int] = []  # 连续 needs_review 的 run_id（滑动窗口，供熔断判定）
    tripped = False

    for pid in ordered:
        if pid not in known:
            # 预检就判掉的，即便已过预算也照旧进 skipped：它压根不会被开审，
            # 放进 remaining 会误导调用方「重发就能审」。
            skipped.append(
                {
                    "product_id": pid,
                    "code": "AUDIT_PRODUCT_NOT_FOUND",
                    "message": "产品不存在",
                }
            )
            continue
        if tripped or time.monotonic() - t0 >= _BATCH_DEADLINE_SECONDS:
            remaining.append(pid)
            continue
        try:
            r = await service.audit_one(
                sessions,
                product_id=pid,
                team_id=team_id,
                is_super=is_super,
                trigger_kind="batch",  # 服务端恒写，不接受入参（批量就是批量）
                levels=levels,
                created_by=created_by,
            )
        except BusinessError as exc:
            if exc.code in _SKIPPED_CODES:
                # 被判过但没建 run（状态不合法 / 并发窗口内被删）——钱没花、库没变
                skipped.append({"product_id": pid, "code": exc.code, "message": exc.message})
            else:
                # 白名单外的业务异常：无从确认它抛在建 run 之前还是之后，按「可能已
                # 扣费」处理（见 _SKIPPED_CODES 的注释）
                log.warning("audit.batch.unexpected_business_error", product_id=pid, code=exc.code)
                failed.append(
                    {"product_id": pid, "code": "AUDIT_ITEM_FAILED", "message": exc.message}
                )
            continue
        except Exception as exc:  # 逐条隔离：一条炸不能连坐整批
            # 已开审但异常中断：run 可能悬挂 running，由下次同品送审的懒清扫收尾。
            # **不 raise**——raise 会让 run_idempotent 删掉占位行，把「已经花了钱的
            # 这一批」变成可被同键重放的空白，等于允许重复扣费。
            # 原始异常文本只进日志：DB 异常的 str() 常含 SQL 片段/表名/连接参数，
            # 而本条 message 会原样显示在运营的回执表格里。
            log.warning(
                "audit.batch.item_failed",
                product_id=pid,
                error=type(exc).__name__,
                detail=str(exc)[:300],
            )
            failed.append(
                {
                    "product_id": pid,
                    "code": "AUDIT_ITEM_FAILED",
                    "message": f"审核执行中断（{type(exc).__name__}），详情见服务端日志",
                }
            )
            continue

        audited.append(
            {
                "product_id": pid,
                "run_id": r["run_id"],
                "verdict": r["verdict"],
                "reject_level": r["reject_level"],
                "llm_cost_usd": r["llm_cost_usd"],
                "product_status": r["product_status"],
            }
        )

        if r["verdict"] != "needs_review":
            streak = []
            continue
        streak.append(int(r["run_id"]))
        if len(streak) >= _BREAKER_CONSECUTIVE:
            window = streak[-_BREAKER_CONSECUTIVE:]
            try:
                tripped = await _provider_looks_down(
                    sessions, team_id=team_id, is_super=is_super, run_ids=window
                )
            except Exception as exc:
                # 熔断判据取不到（连接池打满 / DB 抖动）**绝不能让异常逃出本函数**：
                # 逃出去就走 run_idempotent 的错误清理路径删掉占位行，把「已经花了钱
                # 的这一批」变成可被同键重放的空白——正是本模块第二节全篇要防的事，
                # 而循环里每条 audit_one 都被兜住了，唯独这一行曾经没有。
                # 取 False 而非 True：熔断只是省钱的启发式，探测失败不该把一批本可
                # 跑完的产品截停；墙钟预算那道刹车仍在。
                log.warning(
                    "audit.batch.breaker_probe_failed",
                    product_id=pid,
                    error=type(exc).__name__,
                    detail=str(exc)[:300],
                )
                tripped = False

    counts = {"pass": 0, "reject": 0, "needs_review": 0}
    for item in audited:
        v = str(item["verdict"])
        if v not in counts:
            # 契约声明 verdict_counts 恰三键；长出第四个键会让调用方的三键求和静默
            # 少算。今天 audit_one 只产出三种 verdict，真出现即是上游改了口径。
            log.warning("audit.batch.unknown_verdict", verdict=v, product_id=item["product_id"])
            continue
        counts[v] += 1
    return {
        "audited": audited,
        "skipped": skipped,
        "failed": failed,
        "remaining": remaining,
        "verdict_counts": counts,
        "total_llm_cost_usd": round(sum(float(i["llm_cost_usd"] or 0) for i in audited), 8),
    }
