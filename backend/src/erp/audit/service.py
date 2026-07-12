"""审核编排（源仓 orchestrator.audit_one 语义）：L0 短路 → L2 收证据 → L3 判定。

RS-03a（评审 A7 / R2-06 采纳）：LLM 网络调用移出行锁与请求事务，三段式——
  tx1（短事务）：锁 product 校验 → 落 audit_run → L0/L2 → L3 组 prompt + 查缓存。
    L0 拒 / 政策缺失 / 缓存命中 → **同事务终局返回**（无 HTTP 的单事务快路径）。
  HTTP（无事务）：llm_client.call_provider——期间不持任何行锁/事务（可长达 120s）。
  tx2（短事务）：重锁 product → 记账/写缓存 → coerce → 终局回写。
    期间若出现更新的 run（并发重审）→ 本 run 照常终局但**不覆盖 product 状态**（新者胜）。
  崩溃恢复：tx1~tx2 间进程死亡 → run 悬挂 'running'——下次对同品发起审核时懒清扫
  （>10min 的 running → failed），无需常驻 beat。

verdict 联动 product.status（001 §05）：pass → audit_passed；reject → audit_rejected；
needs_review → needs_review（L3 输出/传输/配置异常一律 fail-closed，评审 A4）。
成本：audit_run.llm_cost_usd 累计本次全部 LLM 调用；cache_hit_rate 观测。
"""

import json
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.audit import pipeline
from erp.audit import policy_block as policy_module
from erp.audit.llm import cache_key, llm_client
from erp.core.errors import BusinessError

log = structlog.get_logger()

DEFAULT_LEVELS = ["l0", "l2", "l3"]
_STALE_RUNNING_MINUTES = 10  # tx1 后崩溃的遗孤 run 判定阈值（LLM 超时 120s + 富余）


@asynccontextmanager
async def _ctx_tx(
    sessions: async_sessionmaker[AsyncSession], *, team_id: int | None, is_super: bool
) -> AsyncIterator[AsyncSession]:
    """审核自管短事务：每段事务重放请求级 GUC（SET LOCAL 随 COMMIT 失效）。"""
    async with sessions() as s, s.begin():
        if is_super:
            await s.execute(text("SELECT set_config('app.is_super', 'on', true)"))
        if team_id is not None:
            await s.execute(
                text("SELECT set_config('app.current_team', :t, true)"), {"t": str(team_id)}
            )
        yield s


async def _write_hit(
    session: AsyncSession,
    *,
    run_id: int,
    team_id: int,
    product_id: int,
    level: str,
    rule_code: str,
    is_hard: bool,
    evidence: dict[str, Any],
) -> None:
    await session.execute(
        text(
            "INSERT INTO app.audit_hit"
            " (run_id, team_id, product_id, level, rule_code, is_hard, evidence)"
            " VALUES (:r, :t, :p, :lv, :c, :h, cast(:e AS jsonb))"
        ),
        {
            "r": run_id,
            "t": team_id,
            "p": product_id,
            "lv": level,
            "c": rule_code,
            "h": is_hard,
            "e": json.dumps(evidence, ensure_ascii=False),
        },
    )


async def _apply_l3(
    session: AsyncSession,
    *,
    run_id: int,
    team_id: int,
    product_id: int,
    result: dict[str, Any],
    policy_version: Any,
) -> tuple[str, str | None]:
    """coerce 结果 → (verdict, reject_level) + 写 l3 命中（缓存命中路径与真调用路径共用）。"""
    if result["verdict"] == "reject":
        await _write_hit(
            session,
            run_id=run_id,
            team_id=team_id,
            product_id=product_id,
            level="l3",
            rule_code=f"llm_{result['reason_category'].replace(' ', '_')}",
            is_hard=True,
            evidence={
                "reason_category": result["reason_category"],
                "reason_text": result["reason_text"],
                "llm_confidence": result["llm_confidence"],
                "signals": result["signals"],
                "blacklist_brand_verdict": result["blacklist_brand_verdict"],
                "policy_version": policy_version,
            },
        )
        return "reject", "l3"
    if result["verdict"] == "needs_review":
        # L3 输出异常（解析失败/非法 verdict）→ fail-closed 转人工复核。
        # reject_level 保持 NULL：该列语义=首个否决层，review 不是否决（R2-23）
        await _write_hit(
            session,
            run_id=run_id,
            team_id=team_id,
            product_id=product_id,
            level="l3",
            rule_code="llm_needs_review",
            is_hard=False,
            evidence={
                "reason_category": result["reason_category"],
                "reason_text": result["reason_text"],
                "llm_confidence": result["llm_confidence"],
                "parse_error": bool(result.get("parse_error")),
                "policy_version": policy_version,
            },
        )
        return "needs_review", None
    return "pass", None


async def _finalize(
    session: AsyncSession,
    *,
    run_id: int,
    run_created_at: Any,
    product_id: int,
    verdict: str,
    reject_level: str | None,
    total_cost: float,
    llm_calls: int,
    llm_hits: int,
    t0: float,
    skip_product_status: bool = False,
) -> dict[str, Any]:
    duration_ms = int((time.monotonic() - t0) * 1000)
    await session.execute(
        text(
            "UPDATE app.audit_run SET status = 'done', verdict = :v, reject_level = :rl,"
            " llm_cost_usd = :c, cache_hit_rate = :hr, finished_at = now(), duration_ms = :d"
            " WHERE id = :r AND created_at = :ca"
        ),
        {
            "v": verdict,
            "rl": reject_level,
            "c": total_cost,
            "hr": round(llm_hits / llm_calls, 3) if llm_calls else None,
            "d": duration_ms,
            "r": run_id,
            "ca": run_created_at,
        },
    )
    new_status = {"pass": "audit_passed", "reject": "audit_rejected"}.get(verdict, "needs_review")
    if skip_product_status:
        # 并发重审出现更新的 run：本 run 结果照常留档，product 状态由新 run 决定（新者胜）
        log.info("audit.stale_run_finalized", run_id=run_id, product_id=product_id)
    else:
        await session.execute(
            text("UPDATE app.product SET status = :s, latest_audit_run_id = :r WHERE id = :p"),
            {"s": new_status, "r": run_id, "p": product_id},
        )
    log.info(
        "audit.done",
        run_id=run_id,
        product_id=product_id,
        verdict=verdict,
        reject_level=reject_level,
        cost_usd=total_cost,
        duration_ms=duration_ms,
    )
    return {
        "run_id": run_id,
        "verdict": verdict,
        "reject_level": reject_level,
        "llm_cost_usd": total_cost,
        "product_status": None if skip_product_status else new_status,
    }


async def audit_one(  # noqa: PLR0915, PLR0912  三段式编排（tx1→HTTP→tx2），拆散反而割裂状态流
    sessions: async_sessionmaker[AsyncSession],
    *,
    product_id: int,
    team_id: int | None,
    is_super: bool = False,
    trigger_kind: str = "manual",
    levels: list[str] | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    levels = levels or DEFAULT_LEVELS
    t0 = time.monotonic()

    # ── tx1：锁品校验 → 落 run → L0/L2 → L3 组 prompt + 查缓存（短事务）──
    llm_job: dict[str, Any] | None = None  # 需出网时带出 tx1 的调用上下文
    async with _ctx_tx(sessions, team_id=team_id, is_super=is_super) as s:
        # 懒清扫：tx1~tx2 间崩溃的遗孤 run（running 超阈值）→ failed，可重审
        await s.execute(
            text(
                "UPDATE app.audit_run SET status = 'failed', finished_at = now()"
                " WHERE product_id = :p AND status = 'running'"
                "   AND started_at < now() - make_interval(mins => :m)"
            ),
            {"p": product_id, "m": _STALE_RUNNING_MINUTES},
        )
        row = (
            (
                await s.execute(
                    text(
                        "SELECT id, team_id, source_ref, title, brand, category_path,"
                        "       amazon_leaf_id, attrs, status"
                        " FROM app.product WHERE id = :p FOR UPDATE"
                    ),
                    {"p": product_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise BusinessError("AUDIT_PRODUCT_NOT_FOUND", "产品不存在")
        if row["status"] in ("listed", "retired"):
            # 在架/终局商品重审会覆盖生命周期状态——在架重审属维护流程（R2），此处硬闸
            raise BusinessError("AUDIT_STATE_INVALID", f"产品状态 {row['status']} 不可发起审核")
        product: dict[str, Any] = dict(row)
        prod_team: int = product["team_id"]

        run_row = (
            await s.execute(
                text(
                    "INSERT INTO app.audit_run"
                    " (team_id, product_id, trigger_kind, levels_requested, status,"
                    "  started_at, created_by)"
                    " VALUES (:t, :p, :k, :lv, 'running', now(), :u)"
                    " RETURNING id, created_at"
                ),
                {"t": prod_team, "p": product_id, "k": trigger_kind, "lv": levels, "u": created_by},
            )
        ).one()
        run_id, run_created_at = run_row.id, run_row.created_at
        await s.execute(
            text("UPDATE app.product SET status = 'auditing' WHERE id = :p"), {"p": product_id}
        )

        verdict = "pass"
        reject_level: str | None = None
        total_cost = 0.0
        llm_calls = 0
        llm_hits = 0

        # L0：确定性硬判断，命中即短路
        if "l0" in levels:
            l0 = await pipeline.run_l0(s, product)
            if l0 is not None:
                await _write_hit(
                    s,
                    run_id=run_id,
                    team_id=prod_team,
                    product_id=product_id,
                    level="l0",
                    rule_code=l0["rule_code"],
                    is_hard=True,
                    evidence=l0["evidence"],
                )
                verdict, reject_level = "reject", "l0"

        # L2：软证据（不否决）
        l2_hits: list[dict[str, Any]] = []
        if verdict == "pass" and "l2" in levels:
            l2_hits = await pipeline.run_l2(s, product)
            for h in l2_hits:
                await _write_hit(
                    s,
                    run_id=run_id,
                    team_id=prod_team,
                    product_id=product_id,
                    level="l2",
                    rule_code=h["rule_code"],
                    is_hard=h["is_hard"],
                    evidence=h["evidence"],
                )

        # L3：组 prompt + 查缓存；命中→同事务终局；未命中→带上下文出网
        if verdict == "pass" and "l3" in levels:
            policy = (
                (
                    await s.execute(
                        text(
                            "SELECT config, version FROM app.audit_policy"
                            " WHERE code = 'l3_intellectual_property' AND enabled"
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if policy is None:
                # 请求了 L3 但策略不存在/被禁——fail-closed 转人工，不能静默维持 pass
                # （评审 round-2 R2-20：区分"未请求 L3"与"请求了但配置缺失"）
                verdict = "needs_review"
                await _write_hit(
                    s,
                    run_id=run_id,
                    team_id=prod_team,
                    product_id=product_id,
                    level="l3",
                    rule_code="l3_policy_missing",
                    is_hard=False,
                    evidence={"detail": "audit_policy l3_intellectual_property 不存在或未启用"},
                )
            else:
                cfg = policy["config"] or {}
                # 37 条政策静态块拼 system prompt 末尾（吃 provider prefix cache；所有产品
                # 同一份，前缀稳定=cache 命中。空表→空块，退回单策略）。政策文本变→system
                # 内容变→llm_cache 键自动失效。见 l3-policy-design.md。
                policy_block = await policy_module.load_policy_block(s)
                valid_categories = await policy_module.valid_reason_categories(s)
                system_content = f"[policy_v{policy['version']}]\n" + pipeline.L3_SYSTEM_PROMPT
                if policy_block:
                    system_content += "\n\n" + policy_block
                messages = [
                    {"role": "system", "content": system_content},
                    {"role": "user", "content": pipeline.build_user_prompt(product, l2_hits)},
                ]
                model = str(cfg.get("model") or "deepseek-chat")
                temperature = float(cfg.get("temperature") or 0.0)
                max_tokens = int(cfg.get("max_tokens") or 1200)
                key = cache_key(model, messages, temperature, max_tokens)
                cached = await llm_client.check_cache(
                    s,
                    key=key,
                    model=model,
                    team_id=prod_team,
                    object_type="audit_run",
                    object_id=run_id,
                    cacheable=pipeline.l3_response_cacheable,
                )
                if cached is not None:
                    llm_calls, llm_hits = 1, 1
                    result = pipeline.coerce_l3_result(cached, valid_categories)
                    verdict, reject_level = await _apply_l3(
                        s,
                        run_id=run_id,
                        team_id=prod_team,
                        product_id=product_id,
                        result=result,
                        policy_version=policy["version"],
                    )
                else:
                    llm_job = {
                        "model": model,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "messages": messages,
                        "key": key,
                        "valid_categories": valid_categories,
                        "policy_version": policy["version"],
                    }

        if llm_job is None:
            # 单事务快路径：L0 拒 / L2 止 / 政策缺失 / 缓存命中——tx1 内终局
            return await _finalize(
                s,
                run_id=run_id,
                run_created_at=run_created_at,
                product_id=product_id,
                verdict=verdict,
                reject_level=reject_level,
                total_cost=total_cost,
                llm_calls=llm_calls,
                llm_hits=llm_hits,
                t0=t0,
            )

    # ── HTTP（无事务、无行锁；最长 120s）──
    llm_error: Exception | None = None
    content = ""
    pt = ct = 0
    try:
        content, pt, ct = await llm_client.call_provider(
            model=llm_job["model"],
            messages=llm_job["messages"],
            temperature=llm_job["temperature"],
            max_tokens=llm_job["max_tokens"],
        )
    except Exception as e:
        # 超时/HTTP 非200/响应缺字段/缺 key —— fail-closed 落痕转人工（评审 R2-20）
        llm_error = e

    # ── tx2：重锁 → 记账/缓存 → coerce → 终局（短事务）──
    async with _ctx_tx(sessions, team_id=team_id, is_super=is_super) as s:
        await s.execute(
            text("SELECT id FROM app.product WHERE id = :p FOR UPDATE"), {"p": product_id}
        )
        newer_run = (
            await s.execute(
                text("SELECT count(*) FROM app.audit_run WHERE product_id = :p AND id > :r"),
                {"p": product_id, "r": run_id},
            )
        ).scalar_one()
        if llm_error is not None:
            verdict = "needs_review"
            err_code = str(getattr(llm_error, "code", "") or type(llm_error).__name__)
            await _write_hit(
                s,
                run_id=run_id,
                team_id=prod_team,
                product_id=product_id,
                level="l3",
                rule_code="llm_unavailable",
                is_hard=False,
                evidence={"error": err_code, "detail": str(llm_error)[:300]},
            )
            log.warning("audit.l3_unavailable", run_id=run_id, error=err_code)
        else:
            total_cost = await llm_client.record_result(
                s,
                key=llm_job["key"],
                model=llm_job["model"],
                content=content,
                prompt_tokens=pt,
                completion_tokens=ct,
                team_id=prod_team,
                object_type="audit_run",
                object_id=run_id,
                cacheable=pipeline.l3_response_cacheable,
            )
            llm_calls = 1
            result = pipeline.coerce_l3_result(content, llm_job["valid_categories"])
            verdict, reject_level = await _apply_l3(
                s,
                run_id=run_id,
                team_id=prod_team,
                product_id=product_id,
                result=result,
                policy_version=llm_job["policy_version"],
            )
        return await _finalize(
            s,
            run_id=run_id,
            run_created_at=run_created_at,
            product_id=product_id,
            verdict=verdict,
            reject_level=reject_level,
            total_cost=total_cost,
            llm_calls=llm_calls,
            llm_hits=llm_hits,
            t0=t0,
            skip_product_status=bool(newer_run),
        )
