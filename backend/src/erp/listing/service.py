"""listing 生命周期：状态机唯一模块 + allocate/submit/poll/verify-back/delist。

总账铁律落位（specs/001 §06 头注）：
- feed 提交**永不盲重试**：提交无响应 → channel_feed_id=NULL + status=verify_pending，
  先对账渠道近程 feeds 再归位（adopt / lost），绝不直接再发。
- headline 计数不可信：对账一律以 feed_item 级结果为准。
- 状态迁移全部经 transition() 写 listing_state_history。
- 配额：submit 消耗 listing_create、delist 消耗 listing_delete；终态失败返还 create。

RS-03b（评审 A7）：渠道写路径全部三段式——tx1 短事务组 feed+落 outbox 命令后
COMMIT（行锁随之释放；渠道已收/DB 失忆的崩溃窗口消除），HTTP 段零事务，
tx2 经 fence 校验归位。读路径（poll/verify-back）行锁同样不跨 HTTP。
"""

import json
from collections.abc import Mapping
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from erp.catalog import variant as variant_svc
from erp.channel import outbox
from erp.channel import service as channel_service
from erp.channel.gateway import gateway
from erp.channel.gateway.client import GatewayResponse
from erp.core.db import ctx_tx
from erp.core.errors import BusinessError
from erp.listing import coerce
from erp.listing import gtin as gtin_pool
from erp.listing import spec as spec_builder
from erp.notify.service import notify
from erp.pricing import engine as pricing_engine
from erp.pricing import service as pricing_service

log = structlog.get_logger()

FEED_TYPE_BY_KIND = {
    "item_build": "MP_ITEM",
    "item_match": "MP_ITEM_MATCH",
    "delete": "RETIRE_ITEM",
    # price feed 提交用 feedType=PRICE_AND_PROMOTION（pricing.PRICE_FEED_TYPE），
    # 但渠道 feeds 列表/轮询里显示为 MP_ITEM_PRICE_UPDATE（R2-06 考古口径3）——
    # 本表供 verify-back 对账匹配，故记显示值
    "price": "MP_ITEM_PRICE_UPDATE",
}


async def transition(
    session: AsyncSession,
    listing: dict[str, Any],
    to_status: str,
    *,
    reason_code: str | None = None,
    detail: dict[str, Any] | None = None,
    actor_type: str = "system",
    actor_id: int | None = None,
) -> None:
    """状态迁移唯一出口：更新 listing + 写 state_history（同事务）。"""
    extra = ""
    if to_status == "published":
        extra = ", published_at = now()"
    elif to_status == "delisted":
        extra = ", delisted_at = now()"
    # error_code 仅在异常态携带（正常迁移清空——reason_code 只进 history）
    err = reason_code if to_status in ("failed", "degraded") else None
    await session.execute(
        text("UPDATE app.listing SET status = :s, error_code = :e" + extra + " WHERE id = :id"),
        {"s": to_status, "e": err, "id": listing["id"]},
    )
    await session.execute(
        text(
            "INSERT INTO app.listing_state_history"
            " (listing_id, team_id, from_status, to_status, reason_code, detail,"
            "  actor_type, actor_id)"
            " VALUES (:l, :t, :f, :to, :r, cast(:d AS jsonb), :at, :ai)"
        ),
        {
            "l": listing["id"],
            "t": listing["team_id"],
            "f": listing["status"],
            "to": to_status,
            "r": reason_code,
            "d": json.dumps(detail or {}, ensure_ascii=False),
            "at": actor_type,
            "ai": actor_id,
        },
    )
    listing["status"] = to_status


async def _load_listing(session: AsyncSession, listing_id: int) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, store_id, product_id, offer_mode, channel_sku,"
                    " gtin, status, error_code, is_locked, current_price, current_inventory,"
                    " end_date"
                    " FROM app.listing WHERE id = :id FOR UPDATE"
                ),
                {"id": listing_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise BusinessError("LISTING_NOT_FOUND", "listing 不存在")
    return dict(row)


# ── allocate：批量建 draft（去重/GTIN 预占/初价）──


def _initial_price(
    strategy: dict[str, Any] | None, product: Mapping[Any, Any]
) -> tuple[float | None, dict[str, Any] | None, dict[str, Any] | None]:
    """allocate 初价决策 → (price, 计算明细, 拒绝项)。

    - 有 cost_plus 策略 → 引擎算价；不出价（区间外/低于底线/无源价）→ 拒绝项
      （fail-closed，BR-PR-004 区间外不上架），明细留作 price_history.detail；
    - 无策略或 manual 策略 → 维持现状 price_snapshot.list 直取回退
      （兼容存量；manual 人工价随后经改价端点输入）。
    """
    if strategy is not None and strategy["algo_code"] == "cost_plus":
        result = pricing_service.price_product(strategy, product)
        if not result.ok:
            reason = result.reason or "unknown"
            reject = {
                "code": "PRICING_" + reason.upper(),
                "message": pricing_service.REJECT_MESSAGES.get(
                    reason, f"定价引擎不出价（{reason}）"
                ),
            }
            return None, None, reject
        return result.price, dict(result.detail), None
    snap = product.get("price_snapshot") or {}
    if isinstance(snap, dict) and snap.get("list") is not None:
        return float(snap["list"]), None, None
    return None, None, None


async def _broken_group_reject(
    session: AsyncSession, product: Mapping[Any, Any], offer_mode: str
) -> dict[str, Any] | None:
    """product 属 broken 变体组 → rejected 项（D-Q63③；否则 None）。

    最早拦截不浪费 GTIN 预占；修复后归组任务自动回 active。组同团队（RLS）恒可读。
    match 豁免（D-Q63 配套）：match 不携带变体段，组状态与其无关——评审 blocker 修复。
    """
    gid = product["variant_group_id"]
    if gid is None or offer_mode != "build":
        return None
    g_status = (
        await session.execute(
            text("SELECT status FROM app.variant_group WHERE id = :g"), {"g": gid}
        )
    ).scalar_one_or_none()
    if g_status != "broken":
        return None
    return {
        "product_id": product["id"],
        "code": "VARIANT_GROUP_BROKEN",
        "message": (
            f"变体组 #{gid} 处于 broken（成员不齐/主题冲突）不可分配；修复后归组任务自动回 active"
        ),
    }


async def _occupy_brand(
    session: AsyncSession,
    *,
    team_id: int,
    store_id: int,
    brand_norm: str | None,
    brand_display: str | None,
) -> None:
    """build 上架品牌店铺占用（001 §03 :89）。

    无品牌→跳过；本店已 occupied→跳过；异店 occupied→BRAND_OCCUPIED_OTHER_STORE
    拒绝（异常经 allocate 内 except 归 rejected，回滚本品 savepoint）；无占用→INSERT。
    brand_norm 复用 product 表生成列（lower(btrim(brand))），品牌身份与产品去重一致。
    并发：INSERT 撞部分唯一 uq_brand_occupied → ON CONFLICT DO NOTHING + 复查裁决
    （异店拒/本店过）；复查为空说明撞冲突与复查之间占用行被并发释放（窄窗）——
    重试一次 INSERT 把空槽占上，避免 listing 建成而品牌槽空置的欠占用泄漏。
    复查语义依赖 READ COMMITTED 每语句新快照；引擎若改隔离级别需重审此处。
    """
    if not brand_norm:
        return
    occ = None
    for _ in range(2):
        inserted = (
            await session.execute(
                text(
                    "INSERT INTO app.brand_assignment"
                    " (team_id, brand_norm, brand_display, store_id)"
                    " VALUES (:t, :bn, :bd, :s)"
                    " ON CONFLICT (team_id, brand_norm) WHERE status = 'occupied' DO NOTHING"
                    " RETURNING id"
                ),
                {"t": team_id, "bn": brand_norm, "bd": brand_display, "s": store_id},
            )
        ).first()
        if inserted is not None:
            return  # 新占成功
        occ = (
            await session.execute(
                text(
                    "SELECT ba.store_id, s.name FROM app.brand_assignment ba"
                    " JOIN app.store s ON s.id = ba.store_id"
                    " WHERE ba.team_id = :t AND ba.brand_norm = :bn AND ba.status = 'occupied'"
                ),
                {"t": team_id, "bn": brand_norm},
            )
        ).first()
        if occ is not None:
            break  # 有在场占用行，按其归属裁决
    if occ is None or occ.store_id == store_id:
        return  # 本店已占（或重试后槽仍瞬态空置——留待下次分配收敛）→ 过
    raise BusinessError(
        "BRAND_OCCUPIED_OTHER_STORE",
        f"品牌 {brand_display} 已被本团队店铺「{occ.name}」占用（同品牌只可绑定一店）",
    )


async def allocate(
    session: AsyncSession,
    *,
    team_id: int,
    store_id: int,
    product_ids: list[int],
    offer_mode: str,
    actor_id: int | None = None,
) -> dict[str, Any]:
    store = (
        (
            await session.execute(
                text("SELECT id, team_id, dedup_exempt, status FROM app.store WHERE id = :s"),
                {"s": store_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if store is None or store["team_id"] != team_id:
        raise BusinessError("STORE_NOT_FOUND", "店铺不存在")
    if store["status"] != "active":
        raise BusinessError("STORE_NOT_ACTIVE", f"店铺状态 {store['status']} 不可分配")

    # R2-06 增量2：active 策略解析（store 级 > team 级）——整批同店同模式，只解析一次
    strategy = await pricing_service.resolve_strategy(
        session, team_id=team_id, store_id=store_id, offer_mode=offer_mode
    )

    created, rejected = [], []
    for pid in dict.fromkeys(product_ids):
        # 去重协议（D-Q31）：advisory lock 串行化同 (team, product) 的并发分配
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:t, :p)"), {"t": team_id, "p": pid}
        )
        product = (
            (
                await session.execute(
                    text(
                        "SELECT id, team_id, master_sku, title, brand, brand_norm, images,"
                        " attrs, price_snapshot, status, variant_group_id FROM app.product"
                        " WHERE id = :p AND team_id = :t"
                    ),
                    {"p": pid, "t": team_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if product is None:
            rejected.append(
                {"product_id": pid, "code": "PRODUCT_NOT_FOUND", "message": "产品不存在"}
            )
            continue
        # 准入：audit_passed / ready（sourcing 域 R2 接入后收紧为 ready-only, D-Q25）
        if product["status"] not in ("audit_passed", "ready"):
            rejected.append(
                {
                    "product_id": pid,
                    "code": "PRODUCT_NOT_READY",
                    "message": f"产品状态 {product['status']} 不可上架（需审核通过）",
                }
            )
            continue
        # 变体组 broken 准入（D-Q63③；001 §03：broken 组 spec 构建拒绝）——成员不齐/
        # 主题冲突/超上限时归组服务置 broken，此处最早拦截不浪费 GTIN 预占；
        # 修复后归组任务自动回 active。_submit_tx1 组级守卫再兜一道（防绕过分配入口）。
        broken_reject = await _broken_group_reject(session, product, offer_mode)
        if broken_reject is not None:
            rejected.append(broken_reject)
            continue
        if not store["dedup_exempt"]:
            dup = (
                await session.execute(
                    text(
                        "SELECT l.id FROM app.listing l JOIN app.store s ON s.id = l.store_id"
                        " WHERE l.product_id = :p AND l.team_id = :t"
                        "   AND l.status NOT IN ('delisted','retired','failed')"
                        "   AND NOT s.dedup_exempt LIMIT 1"
                    ),
                    {"p": pid, "t": team_id},
                )
            ).scalar_one_or_none()
            if dup is not None:
                rejected.append(
                    {
                        "product_id": pid,
                        "code": "LISTING_DUP_IN_TEAM",
                        "message": f"团队内已有在架 listing #{dup}（店铺未豁免去重）",
                    }
                )
                continue

        price, price_detail, price_reject = _initial_price(strategy, product)
        if price_reject is not None:
            rejected.append({"product_id": pid, **price_reject})
            continue
        gtin_val: str | None = None
        try:
            # SAVEPOINT：占号失败回滚本品 INSERT，不影响批内其它产品。
            # 真机教训：此前用 DELETE 补偿，而 erp_app 无 DELETE 权限（最小权限，
            # listing 本就不允许物理删）——权限错误把池空业务提示覆盖成 500。
            async with session.begin_nested():
                listing_id = (
                    await session.execute(
                        text(
                            "INSERT INTO app.listing"
                            " (team_id, store_id, product_id, offer_mode, channel_sku,"
                            "  current_price, created_by)"
                            " VALUES (:t, :s, :p, :m, :sku, :pr, :u)"
                            " RETURNING id"
                        ),
                        {
                            "t": team_id,
                            "s": store_id,
                            "p": pid,
                            "m": offer_mode,
                            "sku": product["master_sku"],
                            "pr": price,
                            "u": actor_id,
                        },
                    )
                ).scalar_one()
                if offer_mode == "build":
                    # 品牌占用（001 §03 :89；match 豁免——不携带品牌绑定语义）。
                    # 异店占用抛 BRAND_OCCUPIED_OTHER_STORE → savepoint 回滚本品、归 rejected。
                    await _occupy_brand(
                        session,
                        team_id=team_id,
                        store_id=store_id,
                        brand_norm=product["brand_norm"],
                        brand_display=product["brand"],
                    )
                    gtin_val = await gtin_pool.hold_one(
                        session, team_id=team_id, listing_id=listing_id
                    )
                    await session.execute(
                        text("UPDATE app.listing SET gtin = :g WHERE id = :id"),
                        {"g": gtin_val, "id": listing_id},
                    )
        except BusinessError as e:
            rejected.append({"product_id": pid, "code": e.code, "message": e.message})
            continue
        await session.execute(
            text(
                "INSERT INTO app.listing_state_history"
                " (listing_id, team_id, from_status, to_status, reason_code, actor_type, actor_id)"
                " VALUES (:l, :t, 'draft', 'draft', 'allocated', 'user', :u)"
            ),
            {"l": listing_id, "t": team_id, "u": actor_id},
        )
        if price_detail is not None and price is not None:
            # 策略出价成功：initial 价史（同事务——listing 在则价史在）
            await pricing_service.record_price_history(
                session,
                listing_id=listing_id,
                team_id=team_id,
                old_price=None,
                new_price=price,
                reason="initial",
                strategy=strategy,
                detail=price_detail,
                actor_id=actor_id,
            )
        created.append(
            {
                "id": listing_id,
                "product_id": pid,
                "channel_sku": product["master_sku"],
                "gtin": gtin_val,
                "status": "draft",
                "current_price": price,
            }
        )
    return {"created": created, "rejected": rejected}


# ── submit：三段式（RS-03b）——tx1 组 feed+落命令 → HTTP（零锁）→ tx2 归位 ──


async def submit(
    sessions: async_sessionmaker[AsyncSession],
    *,
    team_id: int,
    listing_ids: list[int],
    actor_id: int | None = None,
    is_super: bool = False,
) -> dict[str, Any]:
    """批量提交上架。tx1 完成校验/配额/spec/组 feed/落 outbox 命令并 COMMIT；
    进程在此后任一点死，命令行即恢复线索（pending→drain 补发 / inflight 超
    lease→verify_pending 对账），渠道已收而 DB 失忆的窗口不复存在。"""
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as session:
        prep = await _submit_tx1(
            session, team_id=team_id, listing_ids=listing_ids, actor_id=actor_id
        )
    response: dict[str, Any] = prep["response"]
    if prep.get("command_id") is None:
        return response
    outcome = await outbox.execute_command(
        sessions,
        prep["command_id"],
        team_id=team_id,
        is_super=is_super,
        applier=_apply_feed_submit,
    )
    if outcome.get("command_status") == "pending":
        # 车道被挡（同店更早命令未终局，FIFO 背压）：命令已落库，drain/后续对账推进
        outcome = {**outcome, "feed_status": "submitting"}
    return {**response, **outcome}


async def _submit_tx1(  # noqa: PLR0912, PLR0915 提交链分支=协议分支（准入/配额/校验闸）,拆散失真
    session: AsyncSession,
    *,
    team_id: int,
    listing_ids: list[int],
    actor_id: int | None = None,
) -> dict[str, Any]:
    skipped: list[dict[str, Any]] = []
    ready: list[dict[str, Any]] = []
    store_id: int | None = None
    offer_mode: str | None = None
    for lid in dict.fromkeys(listing_ids):
        listing = await _load_listing(session, lid)
        if listing["team_id"] != team_id:
            skipped.append({"listing_id": lid, "code": "LISTING_NOT_FOUND"})
            continue
        if listing["is_locked"]:
            skipped.append({"listing_id": lid, "code": "WM_SKU_LOCKED"})
            continue
        if listing["status"] not in ("draft", "queued"):
            skipped.append({"listing_id": lid, "code": "LISTING_STATE_INVALID"})
            continue
        if store_id is None:
            store_id, offer_mode = listing["store_id"], listing["offer_mode"]
        elif listing["store_id"] != store_id or listing["offer_mode"] != offer_mode:
            skipped.append({"listing_id": lid, "code": "FEED_MIXED_BATCH"})  # 一 feed 一店一模式
            continue
        ready.append(listing)
    if not ready:
        return {"command_id": None, "response": {"queued": 0, "skipped": skipped, "feed_id": None}}
    assert store_id is not None and offer_mode is not None

    # 配额：listing_create 原子扣减（不足者跳过，其余照常）
    granted: list[dict[str, Any]] = []
    for listing in ready:
        if await channel_service.consume_quota(session, store_id, "listing_create"):
            granted.append(listing)
        else:
            skipped.append({"listing_id": listing["id"], "code": "ERP_QUOTA_EXHAUSTED"})
    if not granted:
        return {"command_id": None, "response": {"queued": 0, "skipped": skipped, "feed_id": None}}

    # spec 构建 + 组 feed
    feed_kind = "item_build" if offer_mode == "build" else "item_match"
    # PartnerID（inventory[].fulfillmentCenterID 实测必填，BR-LST-007）：store.profile
    # 维护（A152 runbook / GET /v3/settings/partnerprofile 回填）；缺失时构建器省略该键
    partner_id = (
        await session.execute(
            text("SELECT profile ->> 'partner_id' FROM app.store WHERE id = :s"),
            {"s": store_id},
        )
    ).scalar_one_or_none()
    items_payload: list[dict[str, Any]] = []
    feed_listings: list[dict[str, Any]] = []
    # 变体组切分（仅 build 模式；match 全程豁免变体——D-Q3 参数包差异）：
    # 逐 granted 载变体上下文，未分组/match 直接逐品 build（行为与改造前一致），
    # 分组成员按 group_id 归拢后走组级守卫（broken/缺员/anchor 整组判定，D-Q63）。
    grouped: dict[int, list[dict[str, Any]]] = {}
    group_ctx: dict[int, dict[str, Any]] = {}
    member_ctx: dict[int, dict[str, Any]] = {}  # listing_id → 本品变体上下文（build 复用免重查）
    for listing in granted:
        vctx = (
            None
            if offer_mode == "match"
            else await variant_svc.load_build_context(session, listing["product_id"])
        )
        if vctx is None:  # 未分组/match：逐品 build（改造前路径；skip_variant 免重查）
            item = await _build_listing_item(
                session, listing, offer_mode=offer_mode, store_id=store_id,
                partner_id=partner_id, variant_ctx=None, skip_variant=(offer_mode != "match"),
                actor_id=actor_id, skipped=skipped,
            )  # fmt: skip
            if item is not None:
                items_payload.append(item)
                feed_listings.append(listing)
            continue
        gid = int(vctx["group_id"])
        grouped.setdefault(gid, []).append(listing)
        group_ctx[gid] = vctx
        member_ctx[listing["id"]] = vctx
    # 变体组逐组守卫 + 整组构建（D-Q63③ 整组拒绝，不部分成功）
    for gid, members in grouped.items():
        built = await _submit_variant_group(
            session, gid=gid, members=members, ctx=group_ctx[gid], store_id=store_id,
            offer_mode=offer_mode, partner_id=partner_id, actor_id=actor_id,
            member_ctx=member_ctx, skipped=skipped,
        )  # fmt: skip
        if built:
            # 全员成功后先原子锁 anchor 再入列（D-Q63①）：谓词放宽到"空或已=本店"，
            # rowcount==1 才算持锁——并发双店提交时输家命中 0 行，整组撤除不出门
            # （闸③ 的快照读挡不住 TOCTOU，评审 major 修复）。
            locked = (
                await session.execute(
                    text(
                        "UPDATE app.variant_group SET anchor_store_id = :s"
                        " WHERE id = :g AND (anchor_store_id IS NULL OR anchor_store_id = :s)"
                        " RETURNING id"
                    ),
                    {"s": store_id, "g": gid},
                )
            ).first()
            if locked is None:
                for m_listing, _item in built:
                    await channel_service.release_quota(session, store_id, "listing_create")
                    skipped.append(
                        {
                            "listing_id": m_listing["id"],
                            "code": "VARIANT_ANCHOR_MISMATCH",
                            "message": f"变体组 #{gid} 已被并发提交锚定到其它店铺，"
                            "整组撤除（anchor 不自动转移，BR-LST-013）",
                        }
                    )
                built = []
        for m_listing, item in built:
            items_payload.append(item)
            feed_listings.append(m_listing)
    if not feed_listings:
        return {"command_id": None, "response": {"queued": 0, "skipped": skipped, "feed_id": None}}

    envelope = {
        "MPItemFeedHeader": await spec_builder.feed_header(session, offer_mode),
        "MPItem": items_payload,
    }
    # 提交边界最后一道小数位兜底（EXT_DATA_ERROR_68050064665065，源仓语义）
    envelope, _num_fixes = coerce.sanitize_feed_numbers(envelope)
    # 模式闸预检（live 未放量/非测试店等拒绝 → tx1 整体回滚，API 行为与改造前一致）；
    # 执行器在领取事务内会重新 prepare（运营切换模式的竞态由彼处兜住）
    await gateway.prepare(session, store_id)
    feed_id = (
        await session.execute(
            text(
                "INSERT INTO app.feed (team_id, store_id, feed_kind, item_count, created_by)"
                " VALUES (:t, :s, :k, :n, :u) RETURNING id"
            ),
            {"t": team_id, "s": store_id, "k": feed_kind, "n": len(feed_listings), "u": actor_id},
        )
    ).scalar_one()
    for listing in feed_listings:
        await session.execute(
            text(
                "INSERT INTO app.feed_item (feed_id, team_id, listing_id, channel_sku)"
                " VALUES (:f, :t, :l, :sku)"
            ),
            {"f": feed_id, "t": team_id, "l": listing["id"], "sku": listing["channel_sku"]},
        )
        await transition(session, listing, "queued", reason_code=f"feed:{feed_id}",
                         actor_type="user", actor_id=actor_id)  # fmt: skip

    # outbox 命令与业务同事务落库（幂等键=feed id，天然一 feed 一命令）
    feed_type = FEED_TYPE_BY_KIND[feed_kind]
    await session.execute(
        text("UPDATE app.feed SET status = 'submitting' WHERE id = :f"), {"f": feed_id}
    )
    enq = await outbox.enqueue(
        session,
        team_id=team_id,
        store_id=store_id,
        action="feed_submit",
        payload={
            "method": "POST",
            "path": "/v3/feeds",
            "endpoint_key": f"POST /v3/feeds:{feed_type}",
            "params": {"feedType": feed_type},
            "json_body": envelope,
        },
        idempotency_key=f"feed:{feed_id}",
        object_type="feed",
        object_id=feed_id,
        created_by=actor_id,
    )
    return {
        "command_id": enq["command_id"],
        "response": {"queued": len(feed_listings), "skipped": skipped, "feed_id": feed_id},
    }


async def _build_listing_item(
    session: AsyncSession,
    listing: dict[str, Any],
    *,
    offer_mode: str,
    store_id: int,
    partner_id: str | None,
    variant_ctx: dict[str, Any] | None,
    skip_variant: bool,
    actor_id: int | None,
    skipped: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """单 listing spec 构建 + 提交前本地校验。

    失败（BusinessError / 官方 spec 层 errors 非空）→ 返还 listing_create 配额 + 迁 failed
    + 记 skip，返回 None（省 10/hour 配额，不许出门吃渠道拒）；成功返回 MPItem 元素。
    变体组成员亦复用本助手：已加载的变体上下文经 variant_ctx 传入免重查（未分组 build 路径
    传 skip_variant=True 表意"确认非变体"，同样免 build_spec 内重查）。
    """
    product = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, title, brand, images, attrs, price_snapshot,"
                    " category_path, amazon_leaf_id"
                    " FROM app.product WHERE id = :p"
                ),
                {"p": listing["product_id"]},
            )
        )
        .mappings()
        .one()
    )
    try:
        built = await spec_builder.build_spec(
            session,
            product=dict(product),
            offer_mode=offer_mode,
            gtin=listing["gtin"],
            channel_sku=listing["channel_sku"],
            price=listing["current_price"],
            inventory=listing["current_inventory"],
            end_date=listing["end_date"],
            partner_id=partner_id,
            variant_ctx=variant_ctx,
            skip_variant=skip_variant,
        )
    except BusinessError as e:
        await channel_service.release_quota(session, store_id, "listing_create")
        await transition(session, listing, "failed", reason_code=e.code,
                         detail={"message": e.message}, actor_id=actor_id)  # fmt: skip
        skipped.append({"listing_id": listing["id"], "code": e.code})
        return None
    validation = built.get("validation") or {"ok": True}
    if not validation["ok"]:
        await channel_service.release_quota(session, store_id, "listing_create")
        await transition(
            session, listing, "failed", reason_code="ERP_SPEC_INVALID",
            detail={"errors": validation["errors"][:8]}, actor_id=actor_id,
        )  # fmt: skip
        skipped.append({"listing_id": listing["id"], "code": "ERP_SPEC_INVALID"})
        return None
    item: dict[str, Any] = built["item"]
    return item


async def _reject_group(
    session: AsyncSession,
    members: list[dict[str, Any]],
    store_id: int,
    *,
    code: str,
    message: str,
    skipped: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """整组拒绝：逐成员返还 listing_create 配额 + 记 skip（可见原因；状态不动）。→ 空入列表。"""
    for m in members:
        await channel_service.release_quota(session, store_id, "listing_create")
        skipped.append({"listing_id": m["id"], "code": code, "message": message})
    return []


async def _submit_variant_group(
    session: AsyncSession,
    *,
    gid: int,
    members: list[dict[str, Any]],
    ctx: dict[str, Any],
    store_id: int,
    offer_mode: str,
    partner_id: str | None,
    actor_id: int | None,
    member_ctx: dict[int, dict[str, Any]],
    skipped: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """变体组守卫 + 整组构建（D-Q63③ 整组拒绝，不部分成功）。

    准入三闸（任一不过→整组 skip + 配额返还，状态不动）：
      ① broken 组（001 §03）；② anchor 店已锁定且 ≠ 本批店（BR-LST-013 不自动转移）；
      ③ 组不齐——组全体 member_product_ids 任一成员既不在本批、也不在本店在途/在架
      （queued/submitted/published/live 视为在场：渠道部分失败后补投与已上架组追加成员
      不受 D-Q63③ 之外的死锁；可见缺席成员 product_id/master_sku）。
    通过准入：成员逐个 build（先全 build 再统一入列——失败即整组拒绝，无回撤已入列项）；
      任一 build/校验失败→整组拒绝：失败成员走 _build_listing_item 内既有 failed+配额返还
      路径，其余已成功 build 的成员撤除（未入列）+ 配额返还 + 记 VARIANT_GROUP_MEMBER_FAILED
      skip（状态不动）。全员成功→返回 [(listing, item)]（anchor 锁定由调用方入列后落）。
    """
    if str(ctx["status"]) == "broken":  # 闸① broken 组
        return await _reject_group(
            session, members, store_id, code="VARIANT_GROUP_BROKEN",
            message=f"变体组 #{gid} broken（成员不齐/主题冲突），整组拒绝上架；"
                    "修复后归组任务自动回 active",
            skipped=skipped,
        )  # fmt: skip
    anchor = ctx["anchor_store_id"]
    if anchor is not None and int(anchor) != store_id:  # 闸② anchor 不匹配（不自动转移）
        return await _reject_group(
            session, members, store_id, code="VARIANT_ANCHOR_MISMATCH",
            message=f"变体组 #{gid} 已锚定店铺 #{anchor}，不可在店铺 #{store_id} 上架"
                    "（anchor 不自动转移，BR-LST-013）",
            skipped=skipped,
        )  # fmt: skip
    present_pids = {m["product_id"] for m in members}
    absent = [pid for pid in ctx["member_product_ids"] if pid not in present_pids]
    if absent:
        # "在场"扩展（评审 major 修复）：同店已在途/在架成员视为在场——否则渠道侧部分失败
        # 后补投、或已上架组追加新成员，都会被 D-Q63③ 之外的死锁困住（永远不齐）。
        on_channel = {
            int(r.product_id)
            for r in await session.execute(
                text(
                    "SELECT DISTINCT product_id FROM app.listing"
                    " WHERE store_id = :s AND product_id = ANY(:ids)"
                    "   AND status IN ('queued','submitted','published','live')"
                ),
                {"s": store_id, "ids": absent},
            )
        }
        absent = [pid for pid in absent if pid not in on_channel]
    if absent:  # 闸③ 组不齐（缺席成员可见——验收"可见原因"）
        rows = (
            await session.execute(
                text("SELECT id, master_sku FROM app.product WHERE id = ANY(:ids)"),
                {"ids": absent},
            )
        ).all()
        sku_by_pid = {int(r.id): r.master_sku for r in rows}
        missing = ", ".join(f"{pid}({sku_by_pid.get(pid) or '?'})" for pid in absent)
        return await _reject_group(
            session, members, store_id, code="VARIANT_GROUP_INCOMPLETE",
            message=f"变体组 #{gid} 不齐：缺席成员 product_id(master_sku)=[{missing}]，整组拒绝",
            skipped=skipped,
        )  # fmt: skip

    built: list[tuple[dict[str, Any], dict[str, Any]]] = []
    failed_pids: list[int] = []
    for m in members:
        item = await _build_listing_item(
            session, m, offer_mode=offer_mode, store_id=store_id, partner_id=partner_id,
            variant_ctx=member_ctx[m["id"]], skip_variant=False, actor_id=actor_id,
            skipped=skipped,
        )  # fmt: skip
        if item is None:  # 失败成员已在 _build_listing_item 内走 failed+配额返还
            failed_pids.append(m["product_id"])
        else:
            built.append((m, item))
    if failed_pids:  # 整组拒绝：撤除已成功 build 成员（未入列）+ 配额返还 + skip（状态不动）
        for m, _item in built:
            await channel_service.release_quota(session, store_id, "listing_create")
            skipped.append(
                {
                    "listing_id": m["id"],
                    "code": "VARIANT_GROUP_MEMBER_FAILED",
                    "message": f"变体组 #{gid} 成员 product_id={failed_pids} 构建失败，整组拒绝",
                }
            )
        return []
    return built


async def _feed_listings_for_update(session: AsyncSession, feed_id: int) -> list[dict[str, Any]]:
    rows = (
        (
            await session.execute(
                text(
                    "SELECT l.id, l.team_id, l.status FROM app.listing l"
                    " JOIN app.feed_item fi ON fi.listing_id = l.id"
                    " WHERE fi.feed_id = :f ORDER BY l.id FOR UPDATE OF l"
                ),
                {"f": feed_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def _apply_feed_submit(  # noqa: PLR0911 归位分支=渠道响应形态（dry_run/未知/成功/明确拒）
    session: AsyncSession, cmd: dict[str, Any], resp: GatewayResponse | None
) -> dict[str, Any]:
    """feed_submit 的 tx2 归位（outbox.Applier 契约：complete(fence) 成功才落业务态）。

    price feed（feed_kind='price'，R2-06 增量3 D-Q62 聚合通道）分派到 pricing 模块——
    其明确拒分支不涉及 listing_create 配额/GTIN/状态机，归位口径不同。
    """
    feed_id = int(cmd["object_id"])
    feed_kind = (
        await session.execute(text("SELECT feed_kind FROM app.feed WHERE id = :f"), {"f": feed_id})
    ).scalar_one_or_none()
    if feed_kind == "price":
        return await pricing_service.apply_price_feed_submit(session, cmd, resp)
    cid, fence = int(cmd["id"]), int(cmd["fence"])
    actor_id = cmd.get("created_by")

    if resp is not None and resp.dry_run:
        if not await outbox.complete(
            session, command_id=cid, fence=fence, status="succeeded", result={"dry_run": True}
        ):
            return {"command_status": "superseded"}
        await session.execute(
            text(
                "UPDATE app.feed SET status = 'building',"
                " headline = cast(:h AS jsonb) WHERE id = :f"
            ),
            {"f": feed_id, "h": json.dumps({"dry_run": True}, ensure_ascii=False)},
        )
        return {
            "feed_status": "building",
            "dry_run": True,
            "request_snapshot": resp.request_snapshot,
        }

    if resp is None or resp.status is None:
        # 提交无响应（超时/断连）：结果未知 → verify_pending，禁止盲重试（总账铁律）
        if not await outbox.complete(session, command_id=cid, fence=fence, status="verify_pending"):
            return {"command_status": "superseded"}
        await session.execute(
            text(
                "UPDATE app.feed SET status = 'verify_pending', submitted_at = now() WHERE id = :f"
            ),
            {"f": feed_id},
        )
        log.warning("listing.feed_verify_pending", feed_id=feed_id)
        return {"feed_status": "verify_pending"}

    channel_feed_id = (resp.data or {}).get("feedId")
    if resp.status == 200 and channel_feed_id:  # noqa: PLR2004
        if not await outbox.complete(
            session,
            command_id=cid,
            fence=fence,
            status="succeeded",
            result={"channel_feed_id": channel_feed_id},
        ):
            return {"command_status": "superseded"}
        await session.execute(
            text(
                "UPDATE app.feed SET status = 'submitted', channel_feed_id = :cf,"
                " submitted_at = now() WHERE id = :f"
            ),
            {"cf": channel_feed_id, "f": feed_id},
        )
        for listing in await _feed_listings_for_update(session, feed_id):
            if listing["status"] == "queued":  # 并发动作已改状态者不强推（tx1 后世界可能已变）
                await transition(session, listing, "submitted", reason_code=f"feed:{feed_id}",
                                 actor_id=actor_id)  # fmt: skip
        return {"feed_status": "submitted", "channel_feed_id": channel_feed_id}

    # 渠道明确拒绝（有响应但非 200）：feed error + 配额返还 + listing 回 failed
    if not await outbox.complete(
        session,
        command_id=cid,
        fence=fence,
        status="failed",
        result={"http_status": resp.status},
        error_code="ERP_FEED_REJECTED",
    ):
        return {"command_status": "superseded"}
    await session.execute(
        text("UPDATE app.feed SET status = 'error', headline = cast(:h AS jsonb) WHERE id = :f"),
        {"f": feed_id, "h": json.dumps({"status": resp.status}, ensure_ascii=False)},
    )
    for listing in await _feed_listings_for_update(session, feed_id):
        await channel_service.release_quota(session, int(cmd["store_id"]), "listing_create")
        await _release_gtin(session, listing["id"])
        if listing["status"] == "queued":
            await transition(session, listing, "failed", reason_code="ERP_FEED_REJECTED",
                             detail={"http_status": resp.status}, actor_id=actor_id)  # fmt: skip
    return {"queued": 0, "feed_status": "error"}


async def _release_gtin(session: AsyncSession, listing_id: int) -> None:
    """终态失败：归还池 + 清 listing.gtin（重投时重新占号）。"""
    await gtin_pool.release(session, listing_id)
    await session.execute(
        text("UPDATE app.listing SET gtin = NULL WHERE id = :id"), {"id": listing_id}
    )


# ── poll：feed 状态轮询 → item 级权威回写 ──


async def poll_feed(
    sessions: async_sessionmaker[AsyncSession],
    feed_id: int,
    *,
    team_id: int | None = None,
    is_super: bool = False,
) -> dict[str, Any]:
    # tx1：读态校验 + prepare（RS-03b：行锁不跨 HTTP）
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT id, team_id, store_id, feed_kind, channel_feed_id, status"
                        " FROM app.feed WHERE id = :f"
                    ),
                    {"f": feed_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise BusinessError("FEED_NOT_FOUND", "feed 不存在")
        if row["status"] not in ("submitted", "processing"):
            raise BusinessError("FEED_NOT_POLLABLE", f"feed 状态 {row['status']} 无需轮询")
        feed = dict(row)
        ctx, mode = await gateway.prepare(session, feed["store_id"])

    # HTTP（零事务/零行锁）
    resp = await gateway.request_prepared(
        ctx,
        mode,
        "GET",
        f"/v3/feeds/{feed['channel_feed_id']}",
        endpoint_key="GET /v3/feeds",
        params={"includeDetails": "true"},
    )
    if resp.dry_run:
        return {"feed_id": feed_id, "dry_run": True}
    data = resp.data or {}

    # tx2：重锁复核后回写（并发轮询以先完成者为准）
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as session:
        current = (
            await session.execute(
                text("SELECT status FROM app.feed WHERE id = :f FOR UPDATE"), {"f": feed_id}
            )
        ).scalar_one()
        if current not in ("submitted", "processing"):
            return {"feed_id": feed_id, "feed_status": current, "concurrent": True}
        return await _apply_poll_result(session, feed, data)


async def _apply_poll_result(
    session: AsyncSession, feed: dict[str, Any], data: dict[str, Any]
) -> dict[str, Any]:
    feed_id = feed["id"]
    await session.execute(
        text(
            "UPDATE app.feed SET last_polled_at = now(), poll_attempts = poll_attempts + 1,"
            " headline = cast(:h AS jsonb), status = CASE WHEN :fs IN ('PROCESSED','ERROR')"
            " THEN status ELSE 'processing' END WHERE id = :f"
        ),
        {
            "f": feed_id,
            "h": json.dumps(data.get("summary") or {}, ensure_ascii=False),
            "fs": str(data.get("feedStatus") or ""),
        },
    )
    if str(data.get("feedStatus")) not in ("PROCESSED", "ERROR"):
        return {"feed_id": feed_id, "feed_status": "processing"}

    if feed.get("feed_kind") == "price":
        # price feed item 级回写走 pricing 模块（SUCCESS → 两段式回填；error → 复位）
        return await pricing_service.apply_price_feed_results(session, feed, data)

    # item 级权威回写（headline 不可信——总账）
    ok = err = 0
    for item in data.get("itemDetails", {}).get("itemIngestionStatus", []):
        sku = item.get("sku")
        listing_row = (
            (
                await session.execute(
                    text(
                        "SELECT l.id, l.team_id, l.status FROM app.listing l"
                        " JOIN app.feed_item fi ON fi.listing_id = l.id AND fi.feed_id = :f"
                        " WHERE fi.channel_sku = :sku FOR UPDATE OF l"
                    ),
                    {"f": feed_id, "sku": sku},
                )
            )
            .mappings()
            .one_or_none()
        )
        if listing_row is None:
            continue
        listing = dict(listing_row)
        if str(item.get("ingestionStatus")) == "SUCCESS":
            ok += 1
            await session.execute(
                text(
                    "UPDATE app.feed_item SET status = 'success', raw = cast(:r AS jsonb)"
                    " WHERE feed_id = :f AND channel_sku = :sku"
                ),
                {"f": feed_id, "sku": sku, "r": json.dumps(item, ensure_ascii=False)},
            )
            await session.execute(
                text("UPDATE app.listing SET wpid = :w WHERE id = :id"),
                {"w": item.get("wpid"), "id": listing["id"]},
            )
            await transition(session, listing, "published", reason_code=f"feed:{feed_id}")
            await gtin_pool.mark_used(session, listing["id"])
            await transition(session, listing, "live", reason_code="ingestion_success")
        else:
            err += 1
            errors = item.get("ingestionErrors", {}).get("ingestionError") or [{}]
            code = str(errors[0].get("code") or "WM_UNKNOWN")
            await _ensure_error_cataloged(session, code, errors[0].get("description"))
            await session.execute(
                text(
                    "UPDATE app.feed_item SET status = 'error', error_code = :c,"
                    " error_msg = :m, raw = cast(:r AS jsonb)"
                    " WHERE feed_id = :f AND channel_sku = :sku"
                ),
                {
                    "c": code,
                    "m": str(errors[0].get("description") or "")[:500],
                    "r": json.dumps(item, ensure_ascii=False),
                    "f": feed_id,
                    "sku": sku,
                },
            )
            await channel_service.release_quota(session, feed["store_id"], "listing_create")
            await _release_gtin(session, listing["id"])
            await transition(session, listing, "failed", reason_code=code)
    final = "processed" if err == 0 else ("partial" if ok else "error")
    await session.execute(
        text("UPDATE app.feed SET status = :s, completed_at = now() WHERE id = :f"),
        {"s": final, "f": feed_id},
    )
    if err:
        # feed 错误 → 通知告警（错误明细已入 error catalog 闭环, R1-12 失败路径③）
        await notify(
            session,
            team_id=feed["team_id"],
            severity="critical" if final == "error" else "warn",
            category="listing_feed",
            title=f"上架 Feed #{feed_id} {'全部失败' if final == 'error' else '部分失败'}",
            body=f"成功 {ok} / 失败 {err}；错误码已入字典待处置（feed 明细页可查）",
            object_type="feed",
            object_id=str(feed_id),
            dedupe_key=f"feed_result:{feed_id}",
        )
    elif ok:
        # 全部成功 → info 结果通知（SM-0716①：此前只报忧不报喜）
        # dedupe 与失败通知同键——一个 feed 只发一条结果通知
        await notify(
            session,
            team_id=feed["team_id"],
            severity="info",
            category="listing_feed",
            title=f"上架 Feed #{feed_id} 全部成功",
            body=f"{ok} 个 listing 已 live",
            object_type="feed",
            object_id=str(feed_id),
            dedupe_key=f"feed_result:{feed_id}",
        )
    return {"feed_id": feed_id, "feed_status": final, "success": ok, "error": err}


async def _ensure_error_cataloged(
    session: AsyncSession, code: str, description: str | None
) -> None:
    """未登记错误码 → 自动插草稿行（disposition=manual, category=未分类）。"""
    inserted = (
        await session.execute(
            text(
                "INSERT INTO app.listing_error_catalog (error_code, category, title, disposition)"
                " VALUES (:c, '未分类', :t, 'manual')"
                " ON CONFLICT (error_code) DO NOTHING RETURNING error_code"
            ),
            {"c": code, "t": (description or code)[:120]},
        )
    ).scalar_one_or_none()
    if inserted:
        log.warning("listing.error_code_uncataloged", code=code)


# ── verify-back：提交无响应的对账归位（adopt / lost）──


async def verify_back(
    sessions: async_sessionmaker[AsyncSession],
    feed_id: int,
    *,
    team_id: int | None = None,
    is_super: bool = False,
) -> dict[str, Any]:
    # tx1：读态校验 + prepare（RS-03b：行锁不跨 HTTP）
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as session:
        row = (
            (
                await session.execute(
                    text(
                        "SELECT id, team_id, store_id, feed_kind, item_count, submitted_at,"
                        " status FROM app.feed WHERE id = :f"
                    ),
                    {"f": feed_id},
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["status"] != "verify_pending":
            raise BusinessError("FEED_NOT_VERIFY_PENDING", "feed 不在 verify_pending 状态")
        feed = dict(row)
        ctx, mode = await gateway.prepare(session, feed["store_id"])

    # HTTP（零事务）：对账渠道近程 feeds——绝不重发（总账铁律）
    resp = await gateway.request_prepared(
        ctx,
        mode,
        "GET",
        "/v3/feeds",
        endpoint_key="GET /v3/feeds",
        params={"limit": "10", "offset": "0"},
    )
    if resp.dry_run:
        return {"feed_id": feed_id, "dry_run": True}
    candidates = [
        f
        for f in (resp.data or {}).get("results", {}).get("feed", [])
        if str(f.get("feedType")) == FEED_TYPE_BY_KIND[feed["feed_kind"]]
        and int(f.get("itemsReceived") or -1) == feed["item_count"]
    ]

    # tx2：重锁复核后归位（adopt / lost），并同步终局 outbox 命令解开店铺车道
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as session:
        current = (
            await session.execute(
                text("SELECT status FROM app.feed WHERE id = :f FOR UPDATE"), {"f": feed_id}
            )
        ).scalar_one()
        if current != "verify_pending":
            return {"feed_id": feed_id, "feed_status": current, "concurrent": True}
        return await _apply_verify_back(session, feed, candidates)


async def _resolve_feed_command(
    session: AsyncSession, feed_id: int, *, status: str, result: dict[str, Any] | None = None,
    error_code: str | None = None,
) -> None:  # fmt: skip
    cmd_id = (
        await session.execute(
            text(
                "SELECT id FROM app.channel_command WHERE action = 'feed_submit'"
                " AND object_type = 'feed' AND object_id = :f AND status = 'verify_pending'"
            ),
            {"f": feed_id},
        )
    ).scalar_one_or_none()
    if cmd_id is not None:
        await outbox.resolve_verify(
            session, command_id=cmd_id, status=status, result=result, error_code=error_code
        )


async def _apply_verify_back(
    session: AsyncSession, feed: dict[str, Any], candidates: list[dict[str, Any]]
) -> dict[str, Any]:
    feed_id = feed["id"]
    if len(candidates) == 1 and candidates[0].get("feedId"):
        cf = candidates[0]["feedId"]
        await session.execute(
            text("UPDATE app.feed SET status = 'submitted', channel_feed_id = :cf WHERE id = :f"),
            {"cf": cf, "f": feed_id},
        )
        adopted = (
            (
                await session.execute(
                    text(
                        "SELECT l.id, l.team_id, l.status FROM app.listing l"
                        " JOIN app.feed_item fi ON fi.listing_id = l.id WHERE fi.feed_id = :f"
                    ),
                    {"f": feed_id},
                )
            )
            .mappings()
            .all()
        )
        for r in adopted:
            listing = dict(r)
            if listing["status"] == "queued":
                await transition(session, listing, "submitted", reason_code="verify_back_adopt",
                                 detail={"feed_id": feed_id})  # fmt: skip
        await _resolve_feed_command(
            session,
            feed_id,
            status="succeeded",
            result={"channel_feed_id": cf, "via": "verify_back_adopt"},
        )
        log.info("listing.verify_back_adopted", feed_id=feed_id, channel_feed_id=cf)
        return {"feed_id": feed_id, "feed_status": "submitted", "channel_feed_id": cf}
    # 对账确认渠道未收到（或无法唯一匹配→保守按 lost，人工核对后重投）
    await session.execute(text("UPDATE app.feed SET status = 'lost' WHERE id = :f"), {"f": feed_id})
    if feed.get("feed_kind") == "price":
        # price feed lost：清 pending_price 复位 + 告警（无配额/状态机牵连）
        result = await pricing_service.apply_price_feed_lost(session, feed)
        await _resolve_feed_command(session, feed_id, status="failed", error_code="FEED_LOST")
        return result
    rows = (
        (
            await session.execute(
                text(
                    "SELECT l.id, l.team_id, l.status FROM app.listing l"
                    " JOIN app.feed_item fi ON fi.listing_id = l.id WHERE fi.feed_id = :f"
                ),
                {"f": feed_id},
            )
        )
        .mappings()
        .all()
    )
    for r in rows:
        listing = dict(r)
        await channel_service.release_quota(session, feed["store_id"], "listing_create")
        await transition(session, listing, "queued", reason_code="feed_lost",
                         detail={"feed_id": feed_id})  # fmt: skip
    await _resolve_feed_command(session, feed_id, status="failed", error_code="FEED_LOST")
    return {"feed_id": feed_id, "feed_status": "lost", "requeued": len(rows)}


# ── delist / retry ──


async def delist(
    sessions: async_sessionmaker[AsyncSession],
    *,
    team_id: int,
    listing_id: int,
    actor_id: int | None = None,
    is_super: bool = False,
) -> dict[str, Any]:
    """下架。三段式：tx1 锁品/配额/迁 delist_pending/落 outbox 命令 → HTTP → tx2 归位。"""
    async with ctx_tx(sessions, team_id=team_id, is_super=is_super) as session:
        listing = await _load_listing(session, listing_id)
        if listing["team_id"] != team_id:
            raise BusinessError("LISTING_NOT_FOUND", "listing 不存在")
        if listing["status"] != "live":
            raise BusinessError("LISTING_STATE_INVALID", f"状态 {listing['status']} 不可下架")
        if not await channel_service.consume_quota(session, listing["store_id"], "listing_delete"):
            raise BusinessError("ERP_QUOTA_EXHAUSTED", "listing_delete 配额不足")
        await transition(session, listing, "delist_pending", actor_type="user", actor_id=actor_id)
        # 同一 listing 多轮下架各占新幂等键（第 N 次 delist_pending 迁移=第 N 轮）
        episode = (
            await session.execute(
                text(
                    "SELECT count(*) FROM app.listing_state_history"
                    " WHERE listing_id = :l AND to_status = 'delist_pending'"
                ),
                {"l": listing_id},
            )
        ).scalar_one()
        # 模式闸预检（拒绝 → tx1 整体回滚：配额/迁移一并还原，API 行为与改造前一致）
        await gateway.prepare(session, listing["store_id"])
        enq = await outbox.enqueue(
            session,
            team_id=team_id,
            store_id=listing["store_id"],
            action="item_retire",
            payload={
                "method": "POST",
                "path": "/v3/feeds",
                "endpoint_key": "POST /v3/feeds:RETIRE_ITEM",
                "params": {"feedType": "RETIRE_ITEM"},
                "json_body": {"sku": listing["channel_sku"]},
            },
            idempotency_key=f"retire:{listing_id}:{episode}",
            object_type="listing",
            object_id=listing_id,
            created_by=actor_id,
        )
    outcome = await outbox.execute_command(
        sessions,
        enq["command_id"],
        team_id=team_id,
        is_super=is_super,
        applier=_apply_item_retire,
    )
    if outcome.get("error_code") == "ERP_FEED_REJECTED":
        raise BusinessError("ERP_FEED_REJECTED", f"下架提交被拒 HTTP {outcome.get('http_status')}")
    extras: dict[str, Any] = {
        k: outcome[k] for k in ("dry_run", "verify", "command_status") if k in outcome
    }
    return {"listing_id": listing_id, "status": outcome.get("status", "delist_pending"), **extras}


async def _apply_item_retire(  # noqa: PLR0911 归位分支=渠道响应形态（dry_run/未知/成功/明确拒）
    session: AsyncSession, cmd: dict[str, Any], resp: GatewayResponse | None
) -> dict[str, Any]:
    """item_retire 的 tx2 归位（outbox.Applier 契约：complete(fence) 成功才落业务态）。"""
    listing_id = int(cmd["object_id"])
    cid, fence = int(cmd["id"]), int(cmd["fence"])
    actor_id = cmd.get("created_by")
    listing = await _load_listing(session, listing_id)

    if resp is not None and resp.dry_run:
        if not await outbox.complete(
            session, command_id=cid, fence=fence, status="succeeded", result={"dry_run": True}
        ):
            return {"command_status": "superseded"}
        return {"status": "delist_pending", "dry_run": True}

    if resp is None or resp.status is None:
        # 结果未知：保持 delist_pending（不返还配额、不重发）；渠道侧核对随维护任务（R2-04）
        if not await outbox.complete(session, command_id=cid, fence=fence, status="verify_pending"):
            return {"command_status": "superseded"}
        return {"status": "delist_pending", "verify": "unknown"}

    if resp.status == 200:  # noqa: PLR2004
        if not await outbox.complete(session, command_id=cid, fence=fence, status="succeeded"):
            return {"command_status": "superseded"}
        if listing["status"] == "delist_pending":
            await transition(session, listing, "delisted", reason_code="retire_submitted",
                             actor_id=actor_id)  # fmt: skip
        return {"status": "delisted"}

    # 渠道明确拒绝：配额返还 + 占位迁移还原（delist_pending → live），错误上抛由调用方处理
    if not await outbox.complete(
        session,
        command_id=cid,
        fence=fence,
        status="failed",
        result={"http_status": resp.status},
        error_code="ERP_FEED_REJECTED",
    ):
        return {"command_status": "superseded"}
    await channel_service.release_quota(session, int(cmd["store_id"]), "listing_delete")
    if listing["status"] == "delist_pending":
        await transition(session, listing, "live", reason_code="ERP_FEED_REJECTED",
                         detail={"http_status": resp.status}, actor_id=actor_id)  # fmt: skip
    return {"status": "live", "error_code": "ERP_FEED_REJECTED", "http_status": resp.status}


async def update_price(
    session: AsyncSession,
    *,
    team_id: int,
    listing_id: int,
    price: float,
    force: bool = False,
    actor_id: int | None = None,
) -> dict[str, Any]:
    """提交前改价（draft/failed 专用；HF-0716：price_snapshot 无价 → 0 价出门被
    渠道 CAP 拒，本地校验器现已拦截，运营须有改价入口才能自救）。

    在架（live）价格调整不走这里——router 层分流到 pricing.push_price 定价管道
    （R2-06 增量3），防绕过策略；本函数保持纯 draft/failed 直改。
    R2-06 增量2 守护：有 active 策略 → 人工价过 min_price 硬底线（fail-closed）；
    变动幅度超阈（BR-PR-008，配置 pricing.confirm_threshold_pct 默认 30%）需
    force 二次确认。通过后写 price_history(reason='manual')。
    """
    if price <= 0:
        raise BusinessError("LISTING_PRICE_INVALID", "价格必须大于 0")
    listing = await _load_listing(session, listing_id)
    if listing["team_id"] != team_id:
        raise BusinessError("LISTING_NOT_FOUND", "listing 不存在")
    if listing["is_locked"]:
        raise BusinessError("LISTING_LOCKED", "listing 已锁定")
    if listing["status"] not in ("draft", "failed"):
        raise BusinessError(
            "LISTING_STATE_INVALID", "仅 draft/failed 可直接改价（在架调价走定价管道）"
        )
    strategy = await pricing_service.resolve_strategy(
        session, team_id=team_id, store_id=listing["store_id"], offer_mode=listing["offer_mode"]
    )
    guard_detail: dict[str, Any] = {}
    if strategy is not None:
        guard = pricing_engine.guard_manual_price(price, strategy["params"] or {})
        if not guard.ok:
            raise BusinessError(
                "PRICING_BELOW_MIN_PRICE",
                "人工价未过策略 min_price 硬底线（fail-closed 拒绝）",
                detail=guard.detail,
            )
        guard_detail = guard.detail
    old_price = float(listing["current_price"]) if listing["current_price"] is not None else None
    threshold = await pricing_service.confirm_threshold(session, team_id)
    if (
        old_price is not None
        and pricing_engine.exceeds_confirm_threshold(old_price, price, pct=threshold)
        and not force
    ):
        raise BusinessError(
            "PRICING_CONFIRM_REQUIRED",
            f"价格变动超 {threshold:.0%}，需 force 确认",
            detail={"old": old_price, "new": price, "threshold_pct": threshold},
            http_status=422,
        )
    await session.execute(
        text("UPDATE app.listing SET current_price = :p WHERE id = :id"),
        {"p": price, "id": listing_id},
    )
    await pricing_service.record_price_history(
        session,
        listing_id=listing_id,
        team_id=team_id,
        old_price=old_price,
        new_price=price,
        reason="manual",
        strategy=strategy,
        detail=guard_detail,
        actor_id=actor_id,
    )
    return {"listing_id": listing_id, "current_price": price}


async def retry_failed(
    session: AsyncSession, *, team_id: int, listing_id: int, actor_id: int | None = None
) -> dict[str, Any]:
    listing = await _load_listing(session, listing_id)
    if listing["team_id"] != team_id:
        raise BusinessError("LISTING_NOT_FOUND", "listing 不存在")
    if listing["status"] != "failed":
        raise BusinessError("LISTING_STATE_INVALID", "仅 failed 可重投")
    dispo = (
        await session.execute(
            text("SELECT disposition FROM app.listing_error_catalog WHERE error_code = :c"),
            {"c": listing["error_code"]},
        )
    ).scalar_one_or_none()
    if dispo in ("fatal", "skip"):
        raise BusinessError("ERROR_DISPOSITION_BLOCKS", f"错误处置策略（{dispo}）不允许重投")
    if listing["offer_mode"] == "build" and not listing["gtin"]:
        gtin_val = await gtin_pool.hold_one(session, team_id=team_id, listing_id=listing_id)
        await session.execute(
            text("UPDATE app.listing SET gtin = :g WHERE id = :id"),
            {"g": gtin_val, "id": listing_id},
        )
    await transition(session, listing, "queued", reason_code="manual_retry",
                     actor_type="user", actor_id=actor_id)  # fmt: skip
    return {"listing_id": listing_id, "status": "queued"}


# outbox 命令 → tx2 归位函数（drain 工具按 action 索取；请求内三段式直接传引用）
APPLIERS: dict[str, outbox.Applier] = {
    "feed_submit": _apply_feed_submit,
    "item_retire": _apply_item_retire,
}
