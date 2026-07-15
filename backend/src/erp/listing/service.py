"""listing 生命周期：状态机唯一模块 + allocate/submit/poll/verify-back/delist。

总账铁律落位（specs/001 §06 头注）：
- feed 提交**永不盲重试**：提交无响应 → channel_feed_id=NULL + status=verify_pending，
  先对账渠道近程 feeds 再归位（adopt / lost），绝不直接再发。
- headline 计数不可信：对账一律以 feed_item 级结果为准。
- 状态迁移全部经 transition() 写 listing_state_history。
- 配额：submit 消耗 listing_create、delist 消耗 listing_delete；终态失败返还 create。
"""

import json
from typing import Any

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.channel import service as channel_service
from erp.channel.gateway import gateway
from erp.core.errors import BusinessError
from erp.listing import coerce
from erp.listing import gtin as gtin_pool
from erp.listing import spec as spec_builder
from erp.notify.service import notify

log = structlog.get_logger()

FEED_TYPE_BY_KIND = {
    "item_build": "MP_ITEM",
    "item_match": "MP_ITEM_MATCH",
    "delete": "RETIRE_ITEM",
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
                        "SELECT id, team_id, master_sku, title, brand, images, attrs,"
                        " price_snapshot, status FROM app.product"
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

        price = None
        snap = product.get("price_snapshot") or {}
        if isinstance(snap, dict) and snap.get("list") is not None:
            price = float(snap["list"])
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
        gtin_val: str | None = None
        if offer_mode == "build":
            try:
                gtin_val = await gtin_pool.hold_one(session, team_id=team_id, listing_id=listing_id)
            except BusinessError as e:
                await session.execute(
                    text("DELETE FROM app.listing WHERE id = :id"), {"id": listing_id}
                )
                rejected.append({"product_id": pid, "code": e.code, "message": e.message})
                continue
            await session.execute(
                text("UPDATE app.listing SET gtin = :g WHERE id = :id"),
                {"g": gtin_val, "id": listing_id},
            )
        await session.execute(
            text(
                "INSERT INTO app.listing_state_history"
                " (listing_id, team_id, from_status, to_status, reason_code, actor_type, actor_id)"
                " VALUES (:l, :t, 'draft', 'draft', 'allocated', 'user', :u)"
            ),
            {"l": listing_id, "t": team_id, "u": actor_id},
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


# ── submit：spec 构建 → 组 feed → 配额 → 网关提交（verify-back 分支）──


async def submit(  # noqa: PLR0911, PLR0912, PLR0915 提交链分支=协议分支（模式闸/配额/verify-back）,拆散失真
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
        return {"queued": 0, "skipped": skipped, "feed_id": None}
    assert store_id is not None and offer_mode is not None

    # 配额：listing_create 原子扣减（不足者跳过，其余照常）
    granted: list[dict[str, Any]] = []
    for listing in ready:
        if await channel_service.consume_quota(session, store_id, "listing_create"):
            granted.append(listing)
        else:
            skipped.append({"listing_id": listing["id"], "code": "ERP_QUOTA_EXHAUSTED"})
    if not granted:
        return {"queued": 0, "skipped": skipped, "feed_id": None}

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
    for listing in granted:
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
            )
        except BusinessError as e:
            await channel_service.release_quota(session, store_id, "listing_create")
            await transition(session, listing, "failed", reason_code=e.code,
                             detail={"message": e.message}, actor_id=actor_id)  # fmt: skip
            skipped.append({"listing_id": listing["id"], "code": e.code})
            continue
        # 提交前本地校验（增量 4）：官方 spec 层 errors=不许出门（省 10/hour 配额）
        validation = built.get("validation") or {"ok": True}
        if not validation["ok"]:
            await channel_service.release_quota(session, store_id, "listing_create")
            await transition(
                session, listing, "failed", reason_code="ERP_SPEC_INVALID",
                detail={"errors": validation["errors"][:8]}, actor_id=actor_id,
            )  # fmt: skip
            skipped.append({"listing_id": listing["id"], "code": "ERP_SPEC_INVALID"})
            continue
        items_payload.append(built["item"])
        feed_listings.append(listing)
    if not feed_listings:
        return {"queued": 0, "skipped": skipped, "feed_id": None}

    header = {
        "MPItemFeedHeader": await spec_builder.feed_header(session, offer_mode),
        "MPItem": items_payload,
    }
    # 提交边界最后一道小数位兜底（EXT_DATA_ERROR_68050064665065，源仓语义）
    header, _num_fixes = coerce.sanitize_feed_numbers(header)
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

    # 网关提交（dry_run 默认拿快照；live/live_test 真发）
    feed_type = FEED_TYPE_BY_KIND[feed_kind]
    try:
        resp = await gateway.request(
            session,
            store_id,
            "POST",
            "/v3/feeds",
            endpoint_key=f"POST /v3/feeds:{feed_type}",
            params={"feedType": feed_type},
            json_body=header,
        )
    except BusinessError:
        raise  # 模式闸拒绝（live 未开等）原样上抛——不是"无响应"，不进 verify-back
    except Exception as exc:
        # 提交无响应（超时/断连）：结果未知 → verify_pending，禁止盲重试（总账铁律）
        await session.execute(
            text(
                "UPDATE app.feed SET status = 'verify_pending', submitted_at = now() WHERE id = :f"
            ),
            {"f": feed_id},
        )
        log.warning("listing.feed_verify_pending", feed_id=feed_id, error=str(exc))
        return {
            "queued": len(feed_listings),
            "skipped": skipped,
            "feed_id": feed_id,
            "feed_status": "verify_pending",
        }

    if resp.dry_run:
        await session.execute(
            text(
                "UPDATE app.feed SET status = 'building',"
                " headline = cast(:h AS jsonb) WHERE id = :f"
            ),
            {"f": feed_id, "h": json.dumps({"dry_run": True}, ensure_ascii=False)},
        )
        return {
            "queued": len(feed_listings),
            "skipped": skipped,
            "feed_id": feed_id,
            "feed_status": "building",
            "dry_run": True,
            "request_snapshot": resp.request_snapshot,
        }

    if resp.status is None:
        # 网关吞掉传输错误返回 status=None = 提交结果未知 → verify_pending（永不盲重试）
        await session.execute(
            text(
                "UPDATE app.feed SET status = 'verify_pending', submitted_at = now() WHERE id = :f"
            ),
            {"f": feed_id},
        )
        log.warning("listing.feed_verify_pending", feed_id=feed_id, reason="status_none")
        return {
            "queued": len(feed_listings),
            "skipped": skipped,
            "feed_id": feed_id,
            "feed_status": "verify_pending",
        }

    channel_feed_id = (resp.data or {}).get("feedId")
    if resp.status == 200 and channel_feed_id:  # noqa: PLR2004
        await session.execute(
            text(
                "UPDATE app.feed SET status = 'submitted', channel_feed_id = :cf,"
                " submitted_at = now() WHERE id = :f"
            ),
            {"cf": channel_feed_id, "f": feed_id},
        )
        for listing in feed_listings:
            await transition(session, listing, "submitted", reason_code=f"feed:{feed_id}",
                             actor_id=actor_id)  # fmt: skip
        return {
            "queued": len(feed_listings),
            "skipped": skipped,
            "feed_id": feed_id,
            "feed_status": "submitted",
            "channel_feed_id": channel_feed_id,
        }

    # 渠道明确拒绝（有响应但非 200）：feed error + 配额返还 + listing 回 failed
    await session.execute(
        text("UPDATE app.feed SET status = 'error', headline = cast(:h AS jsonb) WHERE id = :f"),
        {"f": feed_id, "h": json.dumps({"status": resp.status}, ensure_ascii=False)},
    )
    for listing in feed_listings:
        await channel_service.release_quota(session, store_id, "listing_create")
        await _release_gtin(session, listing["id"])
        await transition(session, listing, "failed", reason_code="ERP_FEED_REJECTED",
                         detail={"http_status": resp.status}, actor_id=actor_id)  # fmt: skip
    return {"queued": 0, "skipped": skipped, "feed_id": feed_id, "feed_status": "error"}


async def _release_gtin(session: AsyncSession, listing_id: int) -> None:
    """终态失败：归还池 + 清 listing.gtin（重投时重新占号）。"""
    await gtin_pool.release(session, listing_id)
    await session.execute(
        text("UPDATE app.listing SET gtin = NULL WHERE id = :id"), {"id": listing_id}
    )


# ── poll：feed 状态轮询 → item 级权威回写 ──


async def poll_feed(session: AsyncSession, feed_id: int) -> dict[str, Any]:
    feed = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, store_id, channel_feed_id, status FROM app.feed"
                    " WHERE id = :f FOR UPDATE"
                ),
                {"f": feed_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if feed is None:
        raise BusinessError("FEED_NOT_FOUND", "feed 不存在")
    if feed["status"] not in ("submitted", "processing"):
        raise BusinessError("FEED_NOT_POLLABLE", f"feed 状态 {feed['status']} 无需轮询")

    resp = await gateway.request(
        session,
        feed["store_id"],
        "GET",
        f"/v3/feeds/{feed['channel_feed_id']}",
        endpoint_key="GET /v3/feeds",
        params={"includeDetails": "true"},
    )
    if resp.dry_run:
        return {"feed_id": feed_id, "dry_run": True}
    data = resp.data or {}
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


async def verify_back(session: AsyncSession, feed_id: int) -> dict[str, Any]:
    feed = (
        (
            await session.execute(
                text(
                    "SELECT id, team_id, store_id, feed_kind, item_count, submitted_at, status"
                    " FROM app.feed WHERE id = :f FOR UPDATE"
                ),
                {"f": feed_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if feed is None or feed["status"] != "verify_pending":
        raise BusinessError("FEED_NOT_VERIFY_PENDING", "feed 不在 verify_pending 状态")
    resp = await gateway.request(
        session,
        feed["store_id"],
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
        log.info("listing.verify_back_adopted", feed_id=feed_id, channel_feed_id=cf)
        return {"feed_id": feed_id, "feed_status": "submitted", "channel_feed_id": cf}
    # 对账确认渠道未收到（或无法唯一匹配→保守按 lost，人工核对后重投）
    await session.execute(text("UPDATE app.feed SET status = 'lost' WHERE id = :f"), {"f": feed_id})
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
    return {"feed_id": feed_id, "feed_status": "lost", "requeued": len(rows)}


# ── delist / retry ──


async def delist(
    session: AsyncSession, *, team_id: int, listing_id: int, actor_id: int | None = None
) -> dict[str, Any]:
    listing = await _load_listing(session, listing_id)
    if listing["team_id"] != team_id:
        raise BusinessError("LISTING_NOT_FOUND", "listing 不存在")
    if listing["status"] != "live":
        raise BusinessError("LISTING_STATE_INVALID", f"状态 {listing['status']} 不可下架")
    if not await channel_service.consume_quota(session, listing["store_id"], "listing_delete"):
        raise BusinessError("ERP_QUOTA_EXHAUSTED", "listing_delete 配额不足")
    await transition(session, listing, "delist_pending", actor_type="user", actor_id=actor_id)
    resp = await gateway.request(
        session,
        listing["store_id"],
        "POST",
        "/v3/feeds",
        endpoint_key="POST /v3/feeds:RETIRE_ITEM",
        params={"feedType": "RETIRE_ITEM"},
        json_body={"sku": listing["channel_sku"]},
    )
    if resp.dry_run:
        return {"listing_id": listing_id, "status": "delist_pending", "dry_run": True}
    if resp.status is None:
        # 结果未知：保持 delist_pending（不返还配额、不重发），人工/维护任务核对渠道侧
        return {"listing_id": listing_id, "status": "delist_pending", "verify": "unknown"}
    if resp.status == 200:  # noqa: PLR2004
        await transition(session, listing, "delisted", reason_code="retire_submitted",
                         actor_id=actor_id)  # fmt: skip
        return {"listing_id": listing_id, "status": "delisted"}
    await channel_service.release_quota(session, listing["store_id"], "listing_delete")
    raise BusinessError("ERP_FEED_REJECTED", f"下架提交被拒 HTTP {resp.status}")


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
