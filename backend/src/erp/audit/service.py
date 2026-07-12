"""审核编排（源仓 orchestrator.audit_one 语义）：L0 短路 → L2 收证据 → L3 判定。

verdict 联动 product.status（001 §05）：pass → audit_passed；reject → audit_rejected；
needs_review → needs_review（L3 输出异常 fail-closed，转人工复核，评审 round-1 A4）。
成本：audit_run.llm_cost_usd 累计本次全部 LLM 调用；cache_hit_rate 观测。
"""

import json
import time
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.audit import pipeline
from erp.audit import policy_block as policy_module
from erp.audit.llm import llm_client
from erp.core.errors import BusinessError

log = structlog.get_logger()

DEFAULT_LEVELS = ["l0", "l2", "l3"]


async def audit_one(  # noqa: PLR0915, PLR0912  编排函数（L0→L2→L3 全链），拆分反而割裂状态流
    session: AsyncSession,
    *,
    product_id: int,
    trigger_kind: str = "manual",
    levels: list[str] | None = None,
    created_by: int | None = None,
) -> dict[str, Any]:
    levels = levels or DEFAULT_LEVELS
    row = (
        (
            await session.execute(
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
    team_id = product["team_id"]

    run_row = (
        await session.execute(
            text(
                "INSERT INTO app.audit_run"
                " (team_id, product_id, trigger_kind, levels_requested, status,"
                "  started_at, created_by)"
                " VALUES (:t, :p, :k, :lv, 'running', now(), :u)"
                " RETURNING id, created_at"
            ),
            {"t": team_id, "p": product_id, "k": trigger_kind, "lv": levels, "u": created_by},
        )
    ).one()
    run_id = run_row.id
    await session.execute(
        text("UPDATE app.product SET status = 'auditing' WHERE id = :p"), {"p": product_id}
    )

    t0 = time.monotonic()
    verdict = "pass"
    reject_level: str | None = None
    total_cost = 0.0
    llm_calls = 0
    llm_hits = 0

    async def _write_hit(
        level: str, rule_code: str, is_hard: bool, evidence: dict[str, Any]
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

    # L0：确定性硬判断，命中即短路
    if "l0" in levels:
        l0 = await pipeline.run_l0(session, product)
        if l0 is not None:
            await _write_hit("l0", l0["rule_code"], True, l0["evidence"])
            verdict, reject_level = "reject", "l0"

    # L2：软证据（不否决）
    l2_hits: list[dict[str, Any]] = []
    if verdict == "pass" and "l2" in levels:
        l2_hits = await pipeline.run_l2(session, product)
        for h in l2_hits:
            await _write_hit("l2", h["rule_code"], h["is_hard"], h["evidence"])

    # L3：LLM 语义判定
    if verdict == "pass" and "l3" in levels:
        policy = (
            (
                await session.execute(
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
                "l3",
                "l3_policy_missing",
                False,
                {"detail": "audit_policy l3_intellectual_property 不存在或未启用"},
            )
        if policy is not None:
            cfg = policy["config"] or {}
            # 37 条政策静态块拼 system prompt 末尾（吃 provider prefix cache；所有产品同一份，
            # 前缀稳定=cache 命中。空表→空块，退回单策略）。政策文本变→system 内容变→
            # llm_cache 键自动失效（无需额外版本标记）。见 l3-policy-design.md。
            policy_block = await policy_module.load_policy_block(session)
            valid_categories = await policy_module.valid_reason_categories(session)
            system_content = f"[policy_v{policy['version']}]\n" + pipeline.L3_SYSTEM_PROMPT
            if policy_block:
                system_content += "\n\n" + policy_block
            messages = [
                {"role": "system", "content": system_content},
                {"role": "user", "content": pipeline.build_user_prompt(product, l2_hits)},
            ]
            try:
                raw_text, cost, cache_hit = await llm_client.chat(
                    session,
                    model=str(cfg.get("model") or "deepseek-chat"),
                    messages=messages,
                    temperature=float(cfg.get("temperature") or 0.0),
                    max_tokens=int(cfg.get("max_tokens") or 1200),
                    team_id=team_id,
                    object_type="audit_run",
                    object_id=run_id,
                    cacheable=pipeline.l3_response_cacheable,
                )
            except Exception as e:
                # 超时/HTTP 非200/响应缺字段/缺 API key —— fail-closed 落痕转人工，
                # 不让异常回滚整个事务、audit_run 无影无踪（评审 round-2 R2-20）
                verdict = "needs_review"
                err_code = str(getattr(e, "code", "") or type(e).__name__)
                await _write_hit(
                    "l3", "llm_unavailable", False, {"error": err_code, "detail": str(e)[:300]}
                )
                log.warning("audit.l3_unavailable", run_id=run_id, error=err_code)
            else:
                total_cost += cost
                llm_calls += 1
                llm_hits += int(cache_hit)
                result = pipeline.coerce_l3_result(raw_text, valid_categories)
                if result["verdict"] == "reject":
                    verdict, reject_level = "reject", "l3"
                    await _write_hit(
                        "l3",
                        f"llm_{result['reason_category'].replace(' ', '_')}",
                        True,
                        {
                            "reason_category": result["reason_category"],
                            "reason_text": result["reason_text"],
                            "llm_confidence": result["llm_confidence"],
                            "signals": result["signals"],
                            "blacklist_brand_verdict": result["blacklist_brand_verdict"],
                            "policy_version": policy["version"],
                        },
                    )
                elif result["verdict"] == "needs_review":
                    # L3 输出异常（解析失败/非法 verdict）→ fail-closed 转人工复核。
                    # reject_level 保持 NULL：该列语义=首个否决层，review 不是否决（R2-23）
                    verdict = "needs_review"
                    await _write_hit(
                        "l3",
                        "llm_needs_review",
                        False,
                        {
                            "reason_category": result["reason_category"],
                            "reason_text": result["reason_text"],
                            "llm_confidence": result["llm_confidence"],
                            "parse_error": bool(result.get("parse_error")),
                            "policy_version": policy["version"],
                        },
                    )

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
            "ca": run_row.created_at,
        },
    )
    new_status = {"pass": "audit_passed", "reject": "audit_rejected"}.get(verdict, "needs_review")
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
        "product_status": new_status,
    }
