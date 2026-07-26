"""R2-12 增量4b 验收（end_date_renewal 执行通道）。

- dry_run 归位：renew_end_date 在默认 dry_run 模式下 tx1 全通（守卫/配额/延 end_date/
  enqueue item_maintenance），dry_run 归位命令 succeeded、listing 保持 degraded；
- 守卫：非 degraded → LISTING_STATE_INVALID；无 gtin → LISTING_MAINTENANCE_NO_GTIN；
- live_test 200：真渠道写（假渠道 POST /v3/feeds 回 200）→ degraded 转 live（续期生效）；
- live_test 明确拒（400）→ ERP_FEED_REJECTED 抛，listing 留 degraded；
- runner 认领 end_date_renewal → 调 renew_end_date（dry_run）→ 任务 done。
"""

import json
from pathlib import Path

import httpx
import psycopg
import pytest

from erp.channel.gateway import gateway
from erp.core.db import get_session_factory
from erp.core.errors import BusinessError
from erp.core.settings import get_settings
from erp.listing import maintenance
from erp.listing import service as listing_service

TEAM = "续期测试团队"
PREFIX = "zedr"


def _ean13(seed: int) -> str:
    body = f"69{seed:010d}"
    total = sum(int(c) * (3 if i % 2 else 1) for i, c in enumerate(body))
    return body + str((10 - total % 10) % 10)


class _FakeChannel:
    def __init__(self) -> None:
        self.routes: dict[str, httpx.Response | Exception] = {}
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v3/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 900})
        self.requests.append(request)
        key = f"{request.method} {request.url.path}"
        for prefix in sorted(self.routes, key=len, reverse=True):
            if key.startswith(prefix):
                item = self.routes[prefix]
                if isinstance(item, Exception):
                    raise item
                return item
        return httpx.Response(200, json={"ok": True})


@pytest.fixture(scope="module")
def seeded(migrated_db: str) -> dict[str, int]:
    key = get_settings().credential_key
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES (%s) ON CONFLICT (name) DO NOTHING", (TEAM,)
        )
        team_id = conn.execute("SELECT id FROM app.team WHERE name = %s", (TEAM,)).fetchone()[0]
        channel_id = conn.execute("SELECT id FROM app.channel WHERE code='walmart_us'").fetchone()[
            0
        ]
        store_id = conn.execute(
            "SELECT id FROM app.store WHERE team_id = %s AND code = 'ZEDR1'", (team_id,)
        ).fetchone()
        if store_id is None:
            store_id = conn.execute(
                "INSERT INTO app.store (team_id, channel_id, code, name, is_test)"
                " VALUES (%s, %s, 'ZEDR1', '续期测试店', true) RETURNING id",
                (team_id, channel_id),
            ).fetchone()
        sid = int(store_id[0])
        conn.execute("DELETE FROM app.store_credential WHERE store_id = %s", (sid,))
        conn.execute(
            "INSERT INTO app.store_credential (store_id, client_id, client_secret_encrypted)"
            " VALUES (%s, 'zedr-client', pgp_sym_encrypt('zedr-secret', %s))",
            (sid, key),
        )
        pr = conn.execute(
            "SELECT id FROM app.product WHERE team_id = %s AND source_ref = 'B0ZEDR001'",
            (team_id,),
        ).fetchone()
        if pr is None:
            pr = conn.execute(
                "INSERT INTO app.product (team_id, source_channel, source_ref, title, brand,"
                " attrs) VALUES (%s, 'amazon', 'B0ZEDR001', 'Renewal Test Product', 'ZEdrBrand',"
                ' \'{"wpt": "Drinkware"}\') RETURNING id',
                (team_id,),
            ).fetchone()
        conn.execute(
            "INSERT INTO app.system_config (key, value)"
            " VALUES ('channel.gateway_mode', '\"dry_run\"'::jsonb)"
            " ON CONFLICT (key) DO UPDATE SET value = excluded.value"
        )
    return {"team": team_id, "store": sid, "product": int(pr[0])}


@pytest.fixture(autouse=True)
def _clean(migrated_db: str, seeded: dict[str, int]):  # type: ignore[no-untyped-def]
    def _wipe() -> None:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "DELETE FROM app.channel_command"
                " WHERE store_id = %s AND action = 'item_maintenance'",
                (seeded["store"],),
            )
            conn.execute("DELETE FROM app.maintenance_task WHERE team_id = %s", (seeded["team"],))
            conn.execute(
                "DELETE FROM app.listing_state_history WHERE team_id = %s", (seeded["team"],)
            )
            conn.execute("DELETE FROM app.listing WHERE team_id = %s", (seeded["team"],))
            conn.execute(
                "UPDATE app.system_config SET value = '\"dry_run\"'::jsonb"
                " WHERE key = 'channel.gateway_mode'"
            )

    _wipe()
    yield
    _wipe()


_SKU_SEQ = [0]


def _mk_listing(
    migrated_db: str, seeded: dict[str, int], *, status: str = "degraded", gtin: str | None = None
) -> int:
    _SKU_SEQ[0] += 1
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        return int(
            conn.execute(
                "INSERT INTO app.listing (team_id, store_id, product_id, offer_mode,"
                " channel_sku, gtin, status, end_date)"
                " VALUES (%s, %s, %s, 'build', %s, %s, %s, '2026-06-01') RETURNING id",
                (
                    seeded["team"],
                    seeded["store"],
                    seeded["product"],
                    f"{PREFIX}-SKU{_SKU_SEQ[0]}",
                    gtin,
                    status,
                ),
            ).fetchone()[0]
        )


def _fake_gateway():  # type: ignore[no-untyped-def]
    f = _FakeChannel()
    gateway._clients.clear()
    gateway._transport_factory = lambda proxy: httpx.MockTransport(f.handler)
    return f


def _restore_gateway() -> None:
    gateway._clients.clear()
    gateway._transport_factory = gateway._default_transport


def _set_mode(migrated_db: str, mode: str) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "UPDATE app.system_config SET value = %s::jsonb WHERE key = 'channel.gateway_mode'",
            (f'"{mode}"',),
        )


def _listing_row(migrated_db: str, lid: int) -> tuple[str, str]:
    with psycopg.connect(migrated_db) as conn:
        return conn.execute(
            "SELECT status, end_date::text FROM app.listing WHERE id = %s", (lid,)
        ).fetchone()


class TestRenewEndDate:
    async def test_dry_run_extends_enddate_stays_degraded(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        lid = _mk_listing(migrated_db, seeded, gtin=_ean13(1))
        res = await listing_service.renew_end_date(
            get_session_factory(), team_id=seeded["team"], listing_id=lid, is_super=True
        )
        assert res["status"] == "degraded"
        assert res.get("dry_run") is True
        status, end_date = _listing_row(migrated_db, lid)
        assert status == "degraded"  # dry_run 不迁 live
        assert end_date == "2049-12-31"  # 本地 end_date 已延（tx1 生效）
        with psycopg.connect(migrated_db) as conn:
            cmd = conn.execute(
                "SELECT status, payload->'params'->>'feedType' FROM app.channel_command"
                " WHERE action = 'item_maintenance' AND object_id = %s",
                (lid,),
            ).fetchone()
        assert cmd == ("succeeded", "MP_MAINTENANCE")

    async def test_guard_non_degraded_and_no_gtin(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        live_lid = _mk_listing(migrated_db, seeded, status="live", gtin=_ean13(2))
        with pytest.raises(BusinessError) as ei:
            await listing_service.renew_end_date(
                get_session_factory(), team_id=seeded["team"], listing_id=live_lid, is_super=True
            )
        assert ei.value.code == "LISTING_STATE_INVALID"
        nogtin_lid = _mk_listing(migrated_db, seeded, gtin=None)
        with pytest.raises(BusinessError) as ei2:
            await listing_service.renew_end_date(
                get_session_factory(), team_id=seeded["team"], listing_id=nogtin_lid, is_super=True
            )
        assert ei2.value.code == "LISTING_MAINTENANCE_NO_GTIN"

    async def test_live_test_200_renews_to_live(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        lid = _mk_listing(migrated_db, seeded, gtin=_ean13(3))
        _set_mode(migrated_db, "live_test")
        _fake_gateway()  # POST /v3/feeds 默认回 200 {"ok": true}
        try:
            res = await listing_service.renew_end_date(
                get_session_factory(), team_id=seeded["team"], listing_id=lid, is_super=True
            )
        finally:
            _restore_gateway()
        assert res["status"] == "live"
        status, end_date = _listing_row(migrated_db, lid)
        assert status == "live"  # 续期生效 degraded → live
        assert end_date == "2049-12-31"

    async def test_live_test_reject_stays_degraded(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        lid = _mk_listing(migrated_db, seeded, gtin=_ean13(4))
        _set_mode(migrated_db, "live_test")
        f = _fake_gateway()
        f.routes["POST /v3/feeds"] = httpx.Response(400, json={"error": "bad"})
        try:
            with pytest.raises(BusinessError) as ei:
                await listing_service.renew_end_date(
                    get_session_factory(), team_id=seeded["team"], listing_id=lid, is_super=True
                )
        finally:
            _restore_gateway()
        assert ei.value.code == "ERP_FEED_REJECTED"
        status, _ = _listing_row(migrated_db, lid)
        assert status == "degraded"  # 明确拒：留 degraded

    async def test_runner_claims_end_date_renewal(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        lid = _mk_listing(migrated_db, seeded, gtin=_ean13(5))
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.maintenance_task (team_id, store_id, listing_id, task_kind,"
                " scheduled_at) VALUES (%s, %s, %s, 'end_date_renewal', now())",
                (seeded["team"], seeded["store"], lid),
            )
        stats = await maintenance.run(
            get_session_factory(), {"batch": 5, "kinds": ["end_date_renewal"]}
        )
        assert stats == {"claimed": 1, "done": 1, "failed": 0}  # dry_run 归位 → done
        with psycopg.connect(migrated_db) as conn:
            st = conn.execute(
                "SELECT status FROM app.maintenance_task WHERE listing_id = %s", (lid,)
            ).fetchone()[0]
        assert st == "done"


def _wipe_quota(migrated_db: str, seeded: dict[str, int]) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.quota_usage WHERE store_id = %s", (seeded["store"],))
        conn.execute("DELETE FROM app.quota_config WHERE store_id = %s", (seeded["store"],))


class TestMaintenanceRunnerFailClosed:
    """P0-1 回归：runner 的 kinds 默认值必须是空表（人工档），不得 fail-open 成 ['delist']。

    真实触发路径：0037 用 ON CONFLICT DO NOTHING，既有 schedule 行不会被覆盖；
    beat.py:129 与 run_task.py:32 都是 `config or {}` 不补键。故 schedule.config 一旦
    缺 kinds 键，runner 就会拿到 fallback——若 fallback 非空，就绕过 D-Q65② 人工闸
    直接发 RETIRE_ITEM outbox 真渠道下架。
    """

    async def _pending_delist(self, migrated_db: str, seeded: dict[str, int]) -> int:
        lid = _mk_listing(migrated_db, seeded, status="live", gtin=_ean13(6))
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.maintenance_task (team_id, store_id, listing_id, task_kind,"
                " scheduled_at) VALUES (%s, %s, %s, 'delist', now())",
                (seeded["team"], seeded["store"], lid),
            )
        return lid

    async def test_empty_config_claims_nothing(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        lid = await self._pending_delist(migrated_db, seeded)
        # config 全空（beat/run_task 在 schedule.config IS NULL 时就是传这个）
        assert await maintenance.run(get_session_factory(), {}) == {
            "claimed": 0,
            "done": 0,
            "failed": 0,
        }
        with psycopg.connect(migrated_db) as conn:
            st = conn.execute(
                "SELECT status FROM app.maintenance_task WHERE listing_id = %s", (lid,)
            ).fetchone()[0]
            sent = conn.execute(
                "SELECT count(*) FROM app.channel_command"
                " WHERE action = 'retire_item' AND object_id = %s",
                (lid,),
            ).fetchone()[0]
        assert st == "scheduled"  # 任务原地不动
        assert sent == 0  # 关键：没有真渠道下架命令外发

    async def test_config_missing_kinds_key_claims_nothing(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        await self._pending_delist(migrated_db, seeded)
        # 只有 batch、丢了 kinds 键——正是 0037 DO NOTHING 留下的旧行形态
        assert await maintenance.run(get_session_factory(), {"batch": 5}) == {
            "claimed": 0,
            "done": 0,
            "failed": 0,
        }

    async def test_explicit_kinds_still_claims(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """对照组：运营显式开半自动档时照常认领——修复不是把 runner 焊死。"""
        await self._pending_delist(migrated_db, seeded)
        stats = await maintenance.run(get_session_factory(), {"batch": 5, "kinds": ["delist"]})
        assert stats["claimed"] == 1


class TestMaintenanceQuotaGate:
    """P0-2 回归：MP_MAINTENANCE 续期的日限闸必须真的咬得住。

    quota_kind 词表由 ck_quota_kind（0003:162）与配额 API（channel/router.py:22,93）共同
    锁定为 listing_create/listing_delete/maintenance。代码此前传 'listing_maintenance'——
    该值插不进 quota_config、也过不了 API pattern，consume_quota 遂走「未配置=不设限」
    恒返 True，闸形同不存在。
    """

    async def test_daily_limit_blocks_second_renewal(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        _wipe_quota(migrated_db, seeded)
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "INSERT INTO app.quota_config (store_id, quota_kind, daily_limit, enabled)"
                " VALUES (%s, 'maintenance', 1, true)",
                (seeded["store"],),
            )
        try:
            first = _mk_listing(migrated_db, seeded, gtin=_ean13(7))
            res = await listing_service.renew_end_date(
                get_session_factory(), team_id=seeded["team"], listing_id=first, is_super=True
            )
            assert res["status"] == "degraded"  # dry_run 归位，但配额已扣

            with psycopg.connect(migrated_db) as conn:
                used = conn.execute(
                    "SELECT used FROM app.quota_usage"
                    " WHERE store_id = %s AND quota_kind = 'maintenance'",
                    (seeded["store"],),
                ).fetchone()
            assert used == (1,)  # 计入 'maintenance' 而非幻影 kind

            second = _mk_listing(migrated_db, seeded, gtin=_ean13(8))
            with pytest.raises(BusinessError) as ei:
                await listing_service.renew_end_date(
                    get_session_factory(), team_id=seeded["team"], listing_id=second, is_super=True
                )
            assert ei.value.code == "ERP_QUOTA_EXHAUSTED"  # 闸咬住了
        finally:
            _wipe_quota(migrated_db, seeded)

    async def test_quota_kind_rejected_by_db_constraint(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        """锁死回归方向：幻影 kind 连库都插不进——证明扩约束不是正解，对齐词表才是。"""
        _wipe_quota(migrated_db, seeded)
        with (
            psycopg.connect(migrated_db, autocommit=True) as conn,
            pytest.raises(psycopg.errors.CheckViolation),
        ):
            conn.execute(
                "INSERT INTO app.quota_config (store_id, quota_kind, daily_limit, enabled)"
                " VALUES (%s, 'listing_maintenance', 1, true)",
                (seeded["store"],),
            )


class TestMpMaintenanceDryRunEvidence:
    """铁律 4 补账：MP_MAINTENANCE 是渠道写路径，必须有 dry-run 请求快照落仓。

    此前 R2-12 全单没有一份 dry-run 快照进 `.agent/evidence/`（对照 R1-07/R1-11/R2-03/R2-06
    皆有），增量4b 就这么合并了。补齐机制与既有三处一致（test_gateway / test_listing_api /
    test_price_push）：dry_run 模式下断言请求形态，并把快照写进 evidence 供审计与回归对拍。

    注：`_apply_item_maintenance` 在 dry_run 分支只落 `{"dry_run": True}`，把
    `GatewayResponse.request_snapshot` 丢掉了——即生产 dry_run 态也观测不到请求全貌。
    本用例从网关 seam 抓取，不改生产码；是否让服务层把快照落进
    `channel_command.result` 待 Owner 定（已入 task.md 挂账）。
    """

    async def test_dry_run_snapshot_written_to_evidence(
        self, migrated_db: str, seeded: dict[str, int]
    ) -> None:
        lid = _mk_listing(migrated_db, seeded, gtin=_ean13(9))
        captured: list[dict[str, object]] = []
        original = gateway.request_prepared

        async def _spy(*args: object, **kwargs: object) -> object:
            resp = await original(*args, **kwargs)  # type: ignore[arg-type]
            snap = getattr(resp, "request_snapshot", None)
            if getattr(resp, "dry_run", False) and snap:
                captured.append(snap)
            return resp

        gateway.request_prepared = _spy  # type: ignore[method-assign,assignment]
        try:
            res = await listing_service.renew_end_date(
                get_session_factory(), team_id=seeded["team"], listing_id=lid, is_super=True
            )
        finally:
            gateway.request_prepared = original  # type: ignore[method-assign]

        assert res.get("dry_run") is True
        assert len(captured) == 1, "MP_MAINTENANCE 应恰好构造一次 dry_run 请求（零发包）"
        snap = captured[0]

        # 请求形态硬断言（考古口径：MP_MAINTENANCE 走 feeds 端点 + feedType 查询参数）
        assert snap["mode"] == "dry_run"
        assert snap["method"] == "POST"
        assert str(snap["url"]).endswith("/v3/feeds")
        assert snap["endpoint_key"] == "POST /v3/feeds:MP_MAINTENANCE"
        assert dict(snap["params"] or {}).get("feedType") == "MP_MAINTENANCE"  # type: ignore[arg-type]
        # 五个必填渠道头齐备（walmart_client 语义，不得裸调）
        for h in ("Authorization", "WM_SVC.NAME", "WM_CONSUMER.ID", "WM_SEC.ACCESS_TOKEN"):
            assert h in list(snap["headers"])  # type: ignore[arg-type]
        assert snap["proxy"] in (None, "<bound>")  # 代理地址不得泄漏进证据

        out = (
            Path(__file__).resolve().parents[3] / ".agent/evidence/R2-12/dryrun-mp-maintenance.json"
        )
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
