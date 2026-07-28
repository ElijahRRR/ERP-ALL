"""R2-09 增量3a 验收：纯手工批量送审端点 `POST /api/v1/products/audit`。

**本文件的第一职责是对拍**（T1/T2）：批量与单品必须走同一套编排，除 `trigger_kind`
外逐项相等。`audit/service.py` 一行未动正是为了这条判据成立——批量若另写一套判定，
对拍就退化成「两份不同实现互相印证」，印证不出任何东西。

夹具复用 `test_audit_api.py`（团队/管理员/替身 provider/造产品），不另造一套：
两套夹具会各自维护一份「什么算一个可审产品」，漂了也没人发现。
"""

import json
import time
import uuid
from typing import Any

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from erp.audit import batch, llm, service
from erp.audit.llm import llm_client
from erp.audit.router import AuditBatchIn
from erp.core.idempotency import _payload_hash
from erp.core.security import hash_password

from .test_audit_api import (  # noqa: F401  夹具跨文件复用（pyproject 已为 tests/* 豁免 F811）
    ADMIN,
    _FakeLlm,
    _mk_product,
    _pass_verdict,
    client,
    fake_llm,
    seeded,
)
from .test_identity_api import PASSWORD, _login

BATCH_URL = "/api/v1/products/audit"
SINGLE_URL = "/api/v1/products/{}/audit"


def _hdr(auth: dict[str, str], key: str | None = None) -> dict[str, str]:
    """写端点必填 Idempotency-Key（契约 002）——默认每次新键。"""
    return {**auth, "Idempotency-Key": key or str(uuid.uuid4())}


def _run_detail(client: TestClient, auth: dict[str, str], run_id: int) -> dict[str, Any]:
    r = client.get(f"/api/v1/audit-runs/{run_id}", headers=auth)
    assert r.status_code == 200, r.text
    return dict(r.json())


def _product_status(migrated_db: str, pid: int) -> str:
    with psycopg.connect(migrated_db) as conn:
        return str(
            conn.execute("SELECT status FROM app.product WHERE id = %s", (pid,)).fetchone()[0]
        )


def _fingerprint(
    client: TestClient, auth: dict[str, str], migrated_db: str, *, run_id: int, pid: int
) -> dict[str, Any]:
    """对拍指纹：判定 + 请求层级 + 命中集合 + 产品终态。**不含 trigger_kind**（唯一允许差异）。

    命中用 (rule_code, is_hard) 的**排序集合**而非列表：写入顺序不是契约，
    但「哪些规则命中了、硬不硬」是。
    """
    d = _run_detail(client, auth, run_id)
    return {
        "verdict": d["verdict"],
        "reject_level": d["reject_level"],
        "levels_requested": d["levels_requested"],
        "status": d["status"],
        "hits": sorted((h["rule_code"], bool(h["is_hard"])) for h in d["hits"]),
        "product_status": _product_status(migrated_db, pid),
    }


def _audit_run_count(migrated_db: str, *, team: int | None = None, pid: int | None = None) -> int:
    sql = "SELECT count(*) FROM app.audit_run WHERE true"
    params: list[Any] = []
    if team is not None:
        sql += " AND team_id = %s"
        params.append(team)
    if pid is not None:
        sql += " AND product_id = %s"
        params.append(pid)
    with psycopg.connect(migrated_db) as conn:
        return int(conn.execute(sql, tuple(params)).fetchone()[0])


# ══════════════════════════════════════════════════════════════════════════════
# T1/T2 对拍：批量 == 单品（唯一允许差异 = trigger_kind）
# ══════════════════════════════════════════════════════════════════════════════


def _pair(
    client: TestClient,
    migrated_db: str,
    seeded: dict[str, int],
    *,
    tag: str,
    **product_kw: Any,
) -> tuple[int, int]:
    """造内容完全相同的两件产品（仅 ASIN 不同——它是唯一键，且进 prompt 故不共享缓存）。"""
    a = _mk_product(migrated_db, seeded["team"], asin=f"B0BAT{tag}A", **product_kw)
    b = _mk_product(migrated_db, seeded["team"], asin=f"B0BAT{tag}B", **product_kw)
    return a, b


def _run_pair(
    client: TestClient, migrated_db: str, auth: dict[str, str], single_pid: int, batch_pid: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    """单品端点跑 A、批量端点跑 B，返回两侧的 (响应条目, 指纹)。"""
    r1 = client.post(SINGLE_URL.format(single_pid), headers=auth, json={})
    assert r1.status_code == 201, r1.text
    single = r1.json()

    r2 = client.post(BATCH_URL, headers=_hdr(auth), json={"product_ids": [batch_pid]})
    assert r2.status_code == 200, r2.text
    body = r2.json()
    assert len(body["audited"]) == 1, body
    return single, body["audited"][0]


def _assert_parity(
    client: TestClient,
    auth: dict[str, str],
    migrated_db: str,
    *,
    single: dict[str, Any],
    item: dict[str, Any],
    single_pid: int,
    batch_pid: int,
) -> None:
    fp_single = _fingerprint(client, auth, migrated_db, run_id=single["run_id"], pid=single_pid)
    fp_batch = _fingerprint(client, auth, migrated_db, run_id=item["run_id"], pid=batch_pid)
    assert fp_single == fp_batch, f"批量与单品判定分叉：\n单品={fp_single}\n批量={fp_batch}"

    # 响应体本身也必须同形（前端两条路径读同一批字段）
    for field in ("verdict", "reject_level", "product_status"):
        assert single[field] == item[field], f"{field} 分叉：{single[field]!r} vs {item[field]!r}"

    # 唯一允许的差异，显式钉住方向：批量必须是 batch，单品必须不是
    kinds = (
        _run_detail(client, auth, single["run_id"])["trigger_kind"],
        _run_detail(client, auth, item["run_id"])["trigger_kind"],
    )
    assert kinds == ("manual", "batch"), kinds


def test_t1_parity_pass_verdict(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T1：同内容两件产品，单品 vs 批量逐项相等（verdict/reject_level/levels_requested/
    命中集合/product.status），唯一差异是 trigger_kind。

    防的故障：有人图省事在批量里另写一条「简化版」判定（跳过 L2、或不写 audit_hit、
    或不回写 product.status）。那条路上批量端点照样返回 verdict，前端看不出差别，
    但库里两条路径产生的是**不同的审核事实**——事后追溯「这个品当时为什么被拒」时
    拿到的证据链取决于当初点的是哪个按钮。
    """
    a, b = _pair(client, migrated_db, seeded, tag="P1", title="Plain Ceramic Mug Parity P1")
    auth = _login(client, ADMIN, PASSWORD)
    single, item = _run_pair(client, migrated_db, auth, a, b)
    assert single["verdict"] == "pass"
    assert fake_llm.calls == 2  # 两件都真调用（ASIN 进 prompt，不共享缓存）
    _assert_parity(client, auth, migrated_db, single=single, item=item, single_pid=a, batch_pid=b)


def test_t2_parity_reject_l0(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T2-a：L0 硬命中（品牌黑名单）→ 两侧同为 reject/l0，且都不出 LLM 调用。

    防的故障：批量路径漏跑 L0 短路——那会把本该零成本拒掉的品送进 L3 花钱，
    且 reject_level 从 l0 漂成 l3，反馈闭环的归因统计整体失真。
    """
    a, b = _pair(client, migrated_db, seeded, tag="P2", title="Mug Parity P2", brand="Nike")
    auth = _login(client, ADMIN, PASSWORD)
    single, item = _run_pair(client, migrated_db, auth, a, b)
    assert (single["verdict"], single["reject_level"]) == ("reject", "l0")
    assert fake_llm.calls == 0
    _assert_parity(client, auth, migrated_db, single=single, item=item, single_pid=a, batch_pid=b)


def test_t2_parity_needs_review_provider_down(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T2-b：provider HTTP 500 → 两侧同为 needs_review + `llm_unavailable` 命中。

    防的故障：批量把 provider 异常吞成 skipped/failed（「这条没审成，重发吧」），
    而单品路径是 fail-closed 落痕转人工。两者不一致时，批量送审的品会**既没有
    审核记录、也不在人工队列里**——静默漏审，谁都不会去找它。
    """
    fake_llm.http_status = 500
    a, b = _pair(client, migrated_db, seeded, tag="P3", title="Mug Parity P3")
    auth = _login(client, ADMIN, PASSWORD)
    single, item = _run_pair(client, migrated_db, auth, a, b)
    assert single["verdict"] == "needs_review"
    assert single["reject_level"] is None
    _assert_parity(client, auth, migrated_db, single=single, item=item, single_pid=a, batch_pid=b)
    hits = {h["rule_code"] for h in _run_detail(client, auth, item["run_id"])["hits"]}
    assert "llm_unavailable" in hits


# ══════════════════════════════════════════════════════════════════════════════
# T3/T6/T7 混合批与结构不变量
# ══════════════════════════════════════════════════════════════════════════════


def test_t3_mixed_batch_partial_success(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T3：[正常, listed 品, 不存在 id, 正常] → 200，2 audited + 2 skipped，
    且两条正常品**都真的审了**（各自有 run、状态已改）。

    防的故障：一条坏 id 让整批 4xx。那时前面已开审的产品钱已花、run 已落库，而
    响应说「失败」——运维照响应重发，第二遍再花一次钱。这正是「逐条进桶」的由来。
    """
    ok1 = _mk_product(migrated_db, seeded["team"], asin="B0BATMIX1", title="Mug Mix 1")
    ok2 = _mk_product(migrated_db, seeded["team"], asin="B0BATMIX2", title="Mug Mix 2")
    listed = _mk_product(migrated_db, seeded["team"], asin="B0BATMIX3", title="Mug Mix 3")
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("UPDATE app.product SET status = 'listed' WHERE id = %s", (listed,))
    ghost = 999_000_111

    auth = _login(client, ADMIN, PASSWORD)
    r = client.post(BATCH_URL, headers=_hdr(auth), json={"product_ids": [ok1, listed, ghost, ok2]})
    assert r.status_code == 200, r.text
    body = r.json()

    assert [i["product_id"] for i in body["audited"]] == [ok1, ok2]
    assert {s["product_id"]: s["code"] for s in body["skipped"]} == {
        listed: "AUDIT_STATE_INVALID",
        ghost: "AUDIT_PRODUCT_NOT_FOUND",
    }
    assert body["failed"] == [] and body["remaining"] == []
    # 「真的审了」——不是响应里编出来的：库里各有一条 run，产品状态已离开 ingested
    for pid in (ok1, ok2):
        assert _audit_run_count(migrated_db, pid=pid) == 1
        assert _product_status(migrated_db, pid) == "audit_passed"
    assert _product_status(migrated_db, listed) == "listed"  # 在架品未被批量改状态
    assert body["verdict_counts"] == {"pass": 2, "reject": 0, "needs_review": 0}
    # skipped 的契约承诺是「开审之前即被判掉、**零金钱代价**」。只断言状态没变不够——
    # 状态没变也可能是建了 run 又回滚。直接钉「这两个 id 一条 run 都没有」。
    assert _audit_run_count(migrated_db, pid=listed) == 0
    assert _audit_run_count(migrated_db, pid=ghost) == 0
    # 总花费必须等于 audited 各条之和：这是回执上**唯一的钱数字**，运维拿它对预算。
    # 写错桶（拿 skipped 求和）、漏掉缓存命中项、或改成 Decimal 而序列化成字符串，
    # 全都能让它静默失真而其余断言照绿。
    assert body["total_llm_cost_usd"] == pytest.approx(
        sum(i["llm_cost_usd"] for i in body["audited"])
    )
    with psycopg.connect(migrated_db) as conn:
        db_cost = conn.execute(
            "SELECT coalesce(sum(llm_cost_usd), 0) FROM app.audit_run WHERE id = ANY(%s)",
            ([i["run_id"] for i in body["audited"]],),
        ).fetchone()[0]
    assert body["total_llm_cost_usd"] == pytest.approx(float(db_cost))


def test_t6_duplicate_ids_audited_once(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T6：入参 [1,1,2,2,2] → 恰好 2 条 audit_run、响应恰好 2 个条目。

    防的故障：前端多选组件重复推 id（或用户勾了两下）→ 同一品审两次 = 付两次 LLM
    的钱、留两条 run，且 `latest_audit_run_id` 指向后一条，两条 run 的结果若不同
    （provider 抖动）会让「这个品的判定」变成掷骰子。
    """
    p1 = _mk_product(migrated_db, seeded["team"], asin="B0BATDUP1", title="Mug Dup 1")
    p2 = _mk_product(migrated_db, seeded["team"], asin="B0BATDUP2", title="Mug Dup 2")
    auth = _login(client, ADMIN, PASSWORD)
    r = client.post(BATCH_URL, headers=_hdr(auth), json={"product_ids": [p1, p1, p2, p2, p2]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert [i["product_id"] for i in body["audited"]] == [p1, p2]  # 去重且保序
    assert _audit_run_count(migrated_db, pid=p1) == 1
    assert _audit_run_count(migrated_db, pid=p2) == 1
    assert fake_llm.calls == 2


def test_t7_structural_invariant_four_buckets(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T7：四桶计数和 == 去重入参数，并集相等、两两不交。

    防的故障：某个 id **谁都没接住**（被 continue 吞掉），响应看起来正常，调用方
    也无从发现——它只会在几周后表现为「这批里有几个品一直没被审过」。这条不变量
    让「漏掉谁」成为可当场断言的事实，且不要求调用方理解四个桶各自的语义。
    """
    ok = _mk_product(migrated_db, seeded["team"], asin="B0BATINV1", title="Mug Inv 1")
    listed = _mk_product(migrated_db, seeded["team"], asin="B0BATINV2", title="Mug Inv 2")
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("UPDATE app.product SET status = 'retired' WHERE id = %s", (listed,))
    ids = [ok, ok, listed, 999_000_222, 999_000_333]

    auth = _login(client, ADMIN, PASSWORD)
    body = client.post(BATCH_URL, headers=_hdr(auth), json={"product_ids": ids}).json()

    deduped = list(dict.fromkeys(ids))
    buckets = [
        {i["product_id"] for i in body["audited"]},
        {i["product_id"] for i in body["skipped"]},
        {i["product_id"] for i in body["failed"]},
        set(body["remaining"]),
    ]
    total = (
        len(body["audited"]) + len(body["skipped"]) + len(body["failed"]) + len(body["remaining"])
    )
    assert total == len(deduped)
    assert set().union(*buckets) == set(deduped)
    for i, left in enumerate(buckets):
        for right in buckets[i + 1 :]:
            assert not (left & right), f"同一 id 同时落在两个桶里：{left & right}"


# ══════════════════════════════════════════════════════════════════════════════
# T4/T5 隔离与守卫
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture(scope="module")
def other_team(migrated_db: str) -> dict[str, int]:
    """他团队 + 一件他团队的产品（用于越权判据）。"""
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.team (name) VALUES ('批量送审他团队') ON CONFLICT (name) DO NOTHING"
        )
        tid = conn.execute("SELECT id FROM app.team WHERE name = '批量送审他团队'").fetchone()[0]
        pid = conn.execute(
            "INSERT INTO app.product (team_id, source_channel, source_ref, title, attrs)"
            " VALUES (%s, 'amazon', 'B0BATOTHER', 'Other Team Mug', '{}'::jsonb)"
            " ON CONFLICT (team_id, source_channel, source_ref) DO UPDATE"
            "   SET status = 'ingested' RETURNING id",
            (tid,),
        ).fetchone()[0]
    return {"team": tid, "product": pid}


def test_t4_cross_team_id_indistinguishable_from_nonexistent(
    client: TestClient,
    seeded: dict[str, int],
    other_team: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T4：他团队 id 与纯粹不存在的 id **响应完全相同**；他团队零新增 run、状态未变。

    防的故障：① 越权审核——直接把别人的产品送审（花的是我的预算、改的是他的库）；
    ② 响应差异变探测器——若他团队 id 回 `AUDIT_STATE_INVALID` 而不存在的回
    `AUDIT_PRODUCT_NOT_FOUND`，攻击者可用一批连号 id 扫出「哪些 id 是真实存在的
    别人家产品」，进而推断对方的产品规模与增长速率。
    """
    before = _audit_run_count(migrated_db, team=other_team["team"])
    status_before = _product_status(migrated_db, other_team["product"])
    ghost = 999_000_444

    auth = _login(client, ADMIN, PASSWORD)
    body = client.post(
        BATCH_URL, headers=_hdr(auth), json={"product_ids": [other_team["product"], ghost]}
    ).json()

    by_id = {s["product_id"]: s for s in body["skipped"]}
    assert set(by_id) == {other_team["product"], ghost}
    cross = dict(by_id[other_team["product"]])
    fake = dict(by_id[ghost])
    cross.pop("product_id"), fake.pop("product_id")
    assert cross == fake, f"越权与不存在的响应可区分（可用于探测）：{cross} vs {fake}"
    assert cross["code"] == "AUDIT_PRODUCT_NOT_FOUND"
    assert body["audited"] == []
    assert _audit_run_count(migrated_db, team=other_team["team"]) == before
    assert _product_status(migrated_db, other_team["product"]) == status_before


def test_t4b_super_acting_as_team_a_cannot_reach_team_b(
    client: TestClient,
    seeded: dict[str, int],
    other_team: dict[str, int],
    super_user: str,
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T4 补口：**超管**带 `X-Act-Team=A` 送 B 团队的 id → 同样 AUDIT_PRODUCT_NOT_FOUND。

    T4 用的是普通团管，它**测不到真正的那道闸**：普通用户被 RLS 兜着，预检里那句
    `WHERE team_id = :t` 删掉也照样绿。而 RLS 策略是
    `USING (team_id = app.current_team() OR app.is_super())`——**超管这一路 RLS 直接放行**，
    `audit_one` 内部同样带着 `is_super` 开事务，也拦不住。于是对超管而言，预检里那句显式
    team 条件是**唯一**的隔离，删了就是「代表 A 团队操作的超管可以把 B 团队的产品批量送审」：
    B 的产品状态被改写、B 的 audit_run 里凭空多出记录，而操作者以为自己只在 A 里干活。
    这条判据把那句 `team_id = :t` 钉住。
    """
    before = _audit_run_count(migrated_db, team=other_team["team"])
    status_before = _product_status(migrated_db, other_team["product"])
    auth = {**_login(client, super_user, PASSWORD), "X-Act-Team": str(seeded["team"])}
    r = client.post(BATCH_URL, headers=_hdr(auth), json={"product_ids": [other_team["product"]]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["audited"] == [], "超管代表 A 团队却审到了 B 团队的产品——跨团队隔离被击穿"
    assert body["skipped"] == [
        {
            "product_id": other_team["product"],
            "code": "AUDIT_PRODUCT_NOT_FOUND",
            "message": "产品不存在",
        }
    ]
    assert _audit_run_count(migrated_db, team=other_team["team"]) == before
    assert _product_status(migrated_db, other_team["product"]) == status_before


@pytest.fixture(scope="module")
def super_user(migrated_db: str) -> str:
    username = "audit_batch_super"
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("DELETE FROM app.app_user WHERE username = %s", (username,))
        conn.execute(
            "INSERT INTO app.app_user (team_id, username, password_hash, display_name, is_super)"
            " VALUES (NULL, %s, %s, %s, TRUE)",
            (username, hash_password(PASSWORD), username),
        )
    return username


def test_t5_teamless_super_gets_422_and_audits_nothing(
    client: TestClient,
    seeded: dict[str, int],
    super_user: str,
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T5：超管未带 X-Act-Team → 422 `AUDIT_TEAM_REQUIRED`，**全库** audit_run 零新增。

    防的故障：`team_id=None` 一路传下去。预检 `WHERE team_id = :t` 用 NULL 比较恒
    不成立 → 全批「产品不存在」，超管看到一屏假话；而若哪天有人「修」成不带 team
    条件，超管就成了跨全平台批量送审的入口（RLS 对 is_super 是放行的）。
    422 是唯一诚实的答复：先说清你代表哪个团队。
    """
    pid = _mk_product(migrated_db, seeded["team"], asin="B0BATSUP1", title="Mug Super")
    before = _audit_run_count(migrated_db)
    auth = _login(client, super_user, PASSWORD)  # 不带 X-Act-Team
    r = client.post(BATCH_URL, headers=_hdr(auth), json={"product_ids": [pid]})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "AUDIT_TEAM_REQUIRED"
    assert _audit_run_count(migrated_db) == before
    assert fake_llm.calls == 0


def test_t16_l4_rejected_before_any_run(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T16：levels 含 l4 → 422 `AUDIT_L4_DISABLED`，零 audit_run 新增。

    防的故障：L4 闸只在单品端点上有，批量绕过它。L4 视觉审核未实现，`levels_requested`
    里写着 l4 而实际没跑，落库的是一条**声称做了视觉审核**的记录——比没做更糟。
    闸必须在任何一条开审之前（零金钱代价），不能审到一半才发现。
    """
    pid = _mk_product(migrated_db, seeded["team"], asin="B0BATL4", title="Mug L4")
    before = _audit_run_count(migrated_db)
    auth = _login(client, ADMIN, PASSWORD)
    r = client.post(
        BATCH_URL, headers=_hdr(auth), json={"product_ids": [pid], "levels": ["l0", "l4"]}
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == "AUDIT_L4_DISABLED"
    assert _audit_run_count(migrated_db) == before


# ══════════════════════════════════════════════════════════════════════════════
# T8 锁纪律
# ══════════════════════════════════════════════════════════════════════════════


def test_t8_no_row_locks_held_across_provider_call(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T8：批量跑到第 2 条时，旁路连接对**全部**参与产品行 `FOR UPDATE NOWAIT`
    必须立刻全拿到。

    防的故障：有人给预检那句 SELECT 加上 `FOR UPDATE`（「顺手把这批锁住免得中途被
    改」），或在循环里对 product 取行锁。那样 RS-03a 修掉的「持锁调网络」原样复活，
    且更糟——锁的范围从 1 行放大到 N 行、持锁时长从单条放大到整批；此时任何人改这批
    产品全部阻塞，而症状只是「系统偶尔卡」，没有任何报错指向这里。

    ⚠️ **它抓不到「外层包一个大事务」**（曾经的 docstring 这么写，是错的，2026-07-28
    审查 N4 指出）：`service.audit_one` 内部走 `_ctx_tx` 从 sessionmaker **新开会话**，
    与外层事务无任何关系，外层 `sessions.begin()` 只会白占一条连接 idle-in-transaction
    而不持任何行锁——旁路 NOWAIT 照样全拿到。那个形状由「本模块不 import
    `sessions.begin`」这条代码事实与人工审查兜底，本判据不承诺覆盖它。
    形制照抄 `test_audit_api.py` 里既有的 `test_row_lock_released_during_provider_call`。
    """
    pids = [
        _mk_product(migrated_db, seeded["team"], asin=f"B0BATLOCK{i}", title=f"Mug Lock {i}")
        for i in range(1, 4)
    ]
    probe: dict[str, Any] = {}
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 2:  # 第 2 条正在出网的当口
            with psycopg.connect(migrated_db) as conn:
                try:
                    conn.execute(
                        "SELECT id FROM app.product WHERE id = ANY(%s) FOR UPDATE NOWAIT", (pids,)
                    )
                    probe["all_lockable"] = True
                except psycopg.errors.LockNotAvailable:
                    probe["all_lockable"] = False
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(_pass_verdict())}}],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 200},
            },
        )

    llm_client._transport_factory = lambda: httpx.MockTransport(handler)
    auth = _login(client, ADMIN, PASSWORD)
    body = client.post(BATCH_URL, headers=_hdr(auth), json={"product_ids": pids}).json()
    assert probe == {"all_lockable": True}  # 三行全可锁（实证：外层没有大事务）
    assert len(body["audited"]) == 3


# ══════════════════════════════════════════════════════════════════════════════
# T9/T10/T11 幂等与常量不变量
# ══════════════════════════════════════════════════════════════════════════════


def test_t9_same_key_same_payload_replays_without_reaudit(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T9：同键同载荷连发两次 → 第二次 `idempotent_replay=true`、audit_run 零新增、
    **替身 provider 调用计数不变**。

    防的故障：网络抖动导致客户端重发（或用户连点两次提交）→ 整批重跑。批量的每一件
    都要花 LLM 的钱，重跑一批 500 件就是把这笔钱付第二遍，且每件多一条 run 污染
    「这个品被审过几次」的统计。计数不变这条断言是关键——只查 run 数不够：缓存命中
    时也不会新增调用，但那说明它**又走了一遍编排**。
    """
    pid = _mk_product(migrated_db, seeded["team"], asin="B0BATIDEM1", title="Mug Idem 1")
    auth = _login(client, ADMIN, PASSWORD)
    key = str(uuid.uuid4())
    payload = {"product_ids": [pid]}

    first = client.post(BATCH_URL, headers=_hdr(auth, key), json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["audited"][0]["verdict"] == "pass"
    calls_after_first = fake_llm.calls
    runs_after_first = _audit_run_count(migrated_db, pid=pid)

    second = client.post(BATCH_URL, headers=_hdr(auth, key), json=payload)
    assert second.status_code == 200, second.text
    assert second.json()["idempotent_replay"] is True
    assert second.json()["audited"] == first.json()["audited"]
    assert fake_llm.calls == calls_after_first
    assert _audit_run_count(migrated_db, pid=pid) == runs_after_first == 1
    # 顶层字段也必须逐字重放**且类型不变**：`run_idempotent` 存的是
    # `json.dumps(..., default=str)`，任何一天 llm_cost_usd 变成 Decimal，重放响应里
    # 的钱就会悄悄变成字符串，而只比 audited[] 的断言看不见（那一层是列表整体比较，
    # 首次与重放两侧都取自同一份存储响应）。
    assert second.json()["total_llm_cost_usd"] == first.json()["total_llm_cost_usd"]
    assert isinstance(second.json()["total_llm_cost_usd"], float | int)
    assert second.json()["verdict_counts"] == first.json()["verdict_counts"]
    # 重放**也要记一行审计**并标 idempotent_replay=true——「谁重发过」是要留痕的事实，
    # 且这是 router 里唯一区分两次调用的痕迹。把那个 bool 写死成 False 时 T18 不会红
    # （T18 只测首次），本条是它的对照。
    with psycopg.connect(migrated_db) as conn:
        rows = conn.execute(
            "SELECT after FROM app.audit_log WHERE action = 'audit.run_batch' AND team_id = %s"
            " ORDER BY id DESC LIMIT 2",
            (seeded["team"],),
        ).fetchall()
    assert rows[0][0]["idempotent_replay"] is True, "重放那一行必须自陈是重放"
    assert rows[1][0]["idempotent_replay"] is False, "首次那一行不是重放"


def test_t10_in_flight_placeholder_returns_409(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T10：手工插 `response IS NULL` 的占位行后同键请求 → 409 `IDEMPOTENCY_IN_PROGRESS`。

    防的故障：首个 handler 还在跑（同步批量可达 90 秒）时同键重试**并发双跑**——
    两批同时对同一组产品开审，同一件品被两条 run 竞争回写 `product.status`，钱也付两遍。
    占位行是唯一能表达「正在处理中」的信号，409 让客户端稍后拿同键取结果而不是再跑一遍。
    """
    pid = _mk_product(migrated_db, seeded["team"], asin="B0BATIDEM2", title="Mug Idem 2")
    key = str(uuid.uuid4())
    # 载荷哈希必须与端点真正提交的一致，否则会先撞 IDEMPOTENCY_CONFLICT 而非 IN_PROGRESS
    payload_hash = _payload_hash(AuditBatchIn(product_ids=[pid]).model_dump())
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "INSERT INTO app.api_idempotency (team_id, endpoint, idem_key, payload_hash)"
            " VALUES (%s, 'POST /products/audit', %s, %s)",
            (seeded["team"], key, payload_hash),
        )
    try:
        r = client.post(
            BATCH_URL,
            headers=_hdr(_login(client, ADMIN, PASSWORD), key),
            json={"product_ids": [pid]},
        )
        assert r.status_code == 409, r.text
        assert r.json()["error"]["code"] == "IDEMPOTENCY_IN_PROGRESS"
        assert _audit_run_count(migrated_db, pid=pid) == 0
    finally:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute("DELETE FROM app.api_idempotency WHERE idem_key = %s", (key,))


def test_t11_stale_window_covers_worst_case_runtime() -> None:
    """T11（纯常量，不起 DB）：`_BATCH_DEADLINE_SECONDS + 最坏单条 <= 失效阈值`。

    防的故障：幂等占位的失效阈值小于本端点最坏执行时长。那时首个 handler 还在跑，
    占位已被判为「失效可重占」——同键重试重占占位并发双跑，T10 那道闸当场失效
    （RS-03b 评审发现 12 的原样复现）。预算只在**开审前**检查，最后放行的那条仍可能
    再跑满一整条的最坏耗时。

    ⚠️ **最坏单条必须从 `llm.py` 派生**，不许在任一侧写字面量 240（审查 2026-07-28
    N2：原版断言 `_WORST_ITEM_SECONDS == 240` 是在断言自己刚写进去的值——有人把
    `HTTP_TIMEOUT_SECONDS` 调到 600 或加一次重试时，真实最坏跑到 1200s 而本条照绿，
    也就是「它命名要防的那个漂移发生了，它一点反应都没有」）。下面这句改成对
    **来源**求值，llm.py 一动这里就跟着动。
    """
    assert batch._WORST_ITEM_SECONDS == llm.HTTP_TIMEOUT_SECONDS * llm.MAX_ATTEMPTS
    assert (
        batch._BATCH_DEADLINE_SECONDS + batch._WORST_ITEM_SECONDS
        <= batch._BATCH_AUDIT_STALE_MINUTES * 60
    ), "调大 provider 超时/重试次数后必须同步调大 _BATCH_AUDIT_STALE_MINUTES"


# ══════════════════════════════════════════════════════════════════════════════
# T13/T14/T15 两个刹车
# ══════════════════════════════════════════════════════════════════════════════


def test_t13_breaker_trips_after_three_llm_unavailable(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T13：provider 恒快速报错，提交 10 件 → 恰好 3 条 audit_run（全 needs_review）、
    7 个 id 在 remaining、**provider 恰好被调 3 次**。

    防的故障：provider 快速报错（每条 50ms 返 500）时墙钟预算永远够用，只有熔断
    拦得住。没有熔断，一个批能把几百件产品全刷成 `needs_review`——人工复核队列被
    垃圾灌满，而真正需要人看的品淹在里面。调用计数这条断言不可省：只查 run 数会被
    「跳闸后还在继续打 provider」蒙混过去。
    """
    pids = [
        _mk_product(migrated_db, seeded["team"], asin=f"B0BATBRK{i:02d}", title=f"Mug Brk {i}")
        for i in range(10)
    ]
    fake_llm.http_status = 500
    auth = _login(client, ADMIN, PASSWORD)
    body = client.post(BATCH_URL, headers=_hdr(auth), json={"product_ids": pids}).json()

    assert len(body["audited"]) == 3
    assert {i["verdict"] for i in body["audited"]} == {"needs_review"}
    assert body["remaining"] == pids[3:]
    assert len(body["remaining"]) == 7
    assert fake_llm.calls == 3  # 跳闸后一次都没再打
    assert sum(_audit_run_count(migrated_db, pid=p) for p in pids) == 3
    # 被刹下的 7 件原样未动：可直接重发
    for p in pids[3:]:
        assert _product_status(migrated_db, p) == "ingested"


def test_t14_wallclock_budget_stops_before_gateway_timeout(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T14：provider 每次慢到超预算，提交 5 件 → 1 条 audited、4 个 id 在 remaining，
    整体耗时 < 预算 + 单条最坏。**用 monkeypatch 后的小预算跑，不真等 90 秒。**

    防的故障：`frontend/nginx.conf` 的 `proxy_read_timeout 120s` 先掐断连接——客户端
    拿到 504，服务端还在继续跑并继续花钱，已完成的部分结果永远送不出去（谁审过了、
    谁没审过，全批不可知）。熔断在这个形状上是瞎的：provider 没报错，只是慢。
    """
    deadline, item_seconds = 1.0, 2.0
    monkeypatch.setattr(batch, "_BATCH_DEADLINE_SECONDS", deadline)
    pids = [
        _mk_product(migrated_db, seeded["team"], asin=f"B0BATSLOW{i}", title=f"Mug Slow {i}")
        for i in range(5)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        time.sleep(item_seconds)
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps(_pass_verdict())}}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 10},
            },
        )

    llm_client._transport_factory = lambda: httpx.MockTransport(handler)
    auth = _login(client, ADMIN, PASSWORD)
    t0 = time.monotonic()
    body = client.post(BATCH_URL, headers=_hdr(auth), json={"product_ids": pids}).json()
    elapsed = time.monotonic() - t0

    assert len(body["audited"]) == 1
    assert body["audited"][0]["product_id"] == pids[0]
    assert body["remaining"] == pids[1:]
    # 刹车在「开审前」检查，故上界 = 预算 + 一条最坏（再留 5s 给 DB/序列化）
    assert elapsed < deadline + item_seconds + 5, f"耗时 {elapsed:.1f}s 超出刹车上界"
    for p in pids[1:]:
        assert _product_status(migrated_db, p) == "ingested"


def test_t15_breaker_does_not_trip_on_genuine_needs_review(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T15：3 条**真判定**的 needs_review（`l3_policy_missing`，无 `llm_unavailable`
    命中）→ 批量继续跑完，一件不落进 remaining。

    防的故障：熔断只数「连续 needs_review」而不核对命中码。那样 L3 策略被禁用
    （或模型连续输出坏 JSON）会被误判成「provider 挂了」，把一批本该跑完的产品截在
    第 3 条——症状是「批量送审老是只审前几个」，且没有任何报错解释为什么。
    """
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute(
            "UPDATE app.audit_policy SET enabled = false WHERE code = 'l3_intellectual_property'"
        )
    try:
        pids = [
            _mk_product(migrated_db, seeded["team"], asin=f"B0BATNR{i}", title=f"Mug NR {i}")
            for i in range(5)
        ]
        auth = _login(client, ADMIN, PASSWORD)
        body = client.post(BATCH_URL, headers=_hdr(auth), json={"product_ids": pids}).json()
        assert len(body["audited"]) == 5, "真判定的 needs_review 被误当作 provider 故障熔断了"
        assert body["remaining"] == []
        assert body["verdict_counts"] == {"pass": 0, "reject": 0, "needs_review": 5}
        assert fake_llm.calls == 0  # 策略缺失走同事务快路径，压根不出网
        codes = {
            h["rule_code"]
            for i in body["audited"]
            for h in _run_detail(client, auth, i["run_id"])["hits"]
        }
        assert codes == {"l3_policy_missing"}
    finally:
        with psycopg.connect(migrated_db, autocommit=True) as conn:
            conn.execute(
                "UPDATE app.audit_policy SET enabled = true WHERE code = 'l3_intellectual_property'"
            )


# ══════════════════════════════════════════════════════════════════════════════
# T17/T18 遗孤自愈与审计
# ══════════════════════════════════════════════════════════════════════════════


def test_t17_stale_running_run_swept_by_batch(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T17：某品置 `auditing` + 一条 `started_at = now()-20min` 的 running run，
    再送审 → 懒清扫判 failed、新 run 正常终局。

    防的故障：上一次审核在 tx1 与 tx2 之间崩溃（进程被杀/机器重启）留下的遗孤。
    没有清扫，这个品永远卡在 `auditing`；若批量另立一套准入判断（比如「auditing
    不许重审」），这类品就**再也审不了了**，且批量端点还得为此把它记进 skipped，
    看上去像一条正常业务规则。
    """
    pid = _mk_product(migrated_db, seeded["team"], asin="B0BATORPH", title="Mug Orphan")
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        old = conn.execute(
            "INSERT INTO app.audit_run"
            " (team_id, product_id, trigger_kind, levels_requested, status, started_at)"
            " VALUES (%s, %s, 'manual', '{l0,l2,l3}', 'running', now() - interval '20 min')"
            " RETURNING id",
            (seeded["team"], pid),
        ).fetchone()[0]
        conn.execute("UPDATE app.product SET status = 'auditing' WHERE id = %s", (pid,))

    auth = _login(client, ADMIN, PASSWORD)
    body = client.post(BATCH_URL, headers=_hdr(auth), json={"product_ids": [pid]}).json()
    assert len(body["audited"]) == 1
    item = body["audited"][0]
    assert item["verdict"] == "pass"
    assert item["product_status"] == "audit_passed"
    with psycopg.connect(migrated_db) as conn:
        assert (
            conn.execute("SELECT status FROM app.audit_run WHERE id = %s", (old,)).fetchone()[0]
            == "failed"
        )
        assert (
            conn.execute(
                "SELECT status FROM app.audit_run WHERE id = %s", (item["run_id"],)
            ).fetchone()[0]
            == "done"
        )


def test_t18_one_audit_log_row_per_batch(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T18：一批一条 audit_log，`action='audit.run_batch'`，after 含四计数。

    防的故障：① 逐条写审计——一次 500 件的送审在审计流里刷出 500 行，「谁在什么时候
    送了哪一批」淹得看不见；② 干脆不写——批量送审是**花钱且改产品状态**的写操作
    （D-Q16 要求全部经 AuditWriter 记账），不记账就等于这批操作没有责任人。
    """
    ok = _mk_product(migrated_db, seeded["team"], asin="B0BATLOG1", title="Mug Log 1")
    ghost = 999_000_555
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        before = int(
            conn.execute(
                "SELECT count(*) FROM app.audit_log WHERE action = 'audit.run_batch'"
                " AND team_id = %s",
                (seeded["team"],),
            ).fetchone()[0]
        )
    auth = _login(client, ADMIN, PASSWORD)
    r = client.post(BATCH_URL, headers=_hdr(auth), json={"product_ids": [ok, ghost]})
    assert r.status_code == 200, r.text

    with psycopg.connect(migrated_db) as conn:
        rows = conn.execute(
            "SELECT after FROM app.audit_log WHERE action = 'audit.run_batch' AND team_id = %s"
            " ORDER BY id DESC",
            (seeded["team"],),
        ).fetchall()
    assert len(rows) == before + 1, "一批必须恰好一条审计（不是逐条、也不是不写）"
    after = rows[0][0]
    assert after == {
        "audited": 1,
        "skipped": 1,
        "failed": 0,
        "remaining": 0,
        # 逐品可追溯的锚点：批量的 audit_log 没有 object_id（一行对一批），只有四个
        # 计数就回答不了「谁在什么时候送审了产品 X」。run_id → audit_run.product_id
        # 把链条接上。**不存 product_ids**：入参无上限，几万个 id 会撑爆这一行。
        "run_ids": [r.json()["audited"][0]["run_id"]],
        "idempotent_replay": False,
    }


# ══════════════════════════════════════════════════════════════════════════════
# T12/T19/T20 failed 桶、levels 闸、空入参（2026-07-28 审查 + 变异证伪补齐）
# ══════════════════════════════════════════════════════════════════════════════


def test_t12_non_business_exception_lands_in_failed_and_keeps_placeholder(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T12：第 2 条抛非业务异常 → HTTP 200、该条落 `failed`、**幂等占位带 response 存活**。

    防的故障：有人把 `except Exception` 那段改成 `raise`（或在里面漏出一个异常）。
    那时 `run_idempotent` 走错误清理路径 **DELETE 掉占位行**，把「前 N-1 条已经落库、
    LLM 的钱已经花掉」的这一批变成一个可被同键重放的空白——客户端按前端提示原样重试
    （NO_RESULT 档就是这么指导的），整批从头重跑，钱付第二遍。审核域没有 DELETE 授权，
    补偿路径不存在，所以这一步错了就没有回头路。

    变异证伪实测（2026-07-28）：本判据补进来之前，把 `failed` 整段换成 `raise`
    **19 条测试全绿**——模块头注花最多篇幅论证的一条纪律，此前一条判据都没有。
    """
    ok = _mk_product(migrated_db, seeded["team"], asin="B0BATFAIL1", title="Mug Fail 1")
    boom = _mk_product(migrated_db, seeded["team"], asin="B0BATFAIL2", title="Mug Fail 2")
    real = service.audit_one

    async def flaky(*args: Any, **kwargs: Any) -> dict[str, Any]:
        if kwargs.get("product_id") == boom:
            raise RuntimeError("provider exploded mid-flight: dsn=postgres://secret@db/erp")
        return await real(*args, **kwargs)

    monkeypatch.setattr(service, "audit_one", flaky)
    key = str(uuid.uuid4())
    auth = _login(client, ADMIN, PASSWORD)
    r = client.post(BATCH_URL, headers=_hdr(auth, key), json={"product_ids": [ok, boom]})

    assert r.status_code == 200, r.text  # 一条炸 ≠ 整批 4xx
    body = r.json()
    assert [i["product_id"] for i in body["audited"]] == [ok]
    assert body["failed"] == [
        {
            "product_id": boom,
            "code": "AUDIT_ITEM_FAILED",
            "message": "审核执行中断（RuntimeError），详情见服务端日志",
        }
    ], "message 只回码与异常类名——原始异常串常含 SQL/DSN，而这段会原样显示给运营"
    assert "secret" not in json.dumps(body, ensure_ascii=False)
    # 结构不变量在有 failed 的批上照样成立（此前 failed 恒空，四桶不变量实为三桶）
    assert len(body["audited"]) + len(body["skipped"]) + len(body["failed"]) + len(
        body["remaining"]
    ) == len({ok, boom})
    # 占位行必须**带 response 存活**：这才是「同键重试拿回上次结果」而不是重跑的前提
    with psycopg.connect(migrated_db) as conn:
        row = conn.execute(
            "SELECT response IS NOT NULL FROM app.api_idempotency WHERE idem_key = %s", (key,)
        ).fetchone()
    assert row is not None and row[0] is True, "占位被删=同键重试会整批重跑=重复扣费"


def test_t19_invalid_levels_rejected_before_any_run(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T19：`levels=["L0"]`（大小写笔误）/ `["x"]` → 422 `AUDIT_LEVELS_INVALID`，零 run。

    防的故障（2026-07-28 审查阻塞项）：`service.audit_one` 里 L0/L1/L2/L3 四段都是
    `if "lN" in levels` 的**精确匹配**。一个都不命中时 `verdict` 保持初值 `pass`，
    走单事务快路径直接 `_finalize` —— 整批产品落 `audit_passed`、`llm_cost_usd=0`、
    一条 hit 都没有，**一次检查都没跑**。这些品随即可被 `listing.allocate` 分配上架，
    L0 黑名单 / L2 断言 / L3 侵权判定整条合规闸被绕过。契约 002 早就声明了
    `enum: [l0..l4]`，实现侧此前没做——这正是「契约写了代码没做」。

    同时钉住**错误信封**：必须是 002 的 `error.code`，不是 FastAPI 的 `{"detail":[...]}`
    （所以校验写在 `_resolve_levels` 而不是 pydantic 的 `Literal`）。
    """
    pid = _mk_product(migrated_db, seeded["team"], asin="B0BATLVL1", title="Mug Level 1")
    auth = _login(client, ADMIN, PASSWORD)
    for bad in (["L0"], ["x"], ["l0", "l9"]):
        r = client.post(BATCH_URL, headers=_hdr(auth), json={"product_ids": [pid], "levels": bad})
        assert r.status_code == 422, f"{bad} → {r.status_code} {r.text}"
        assert r.json()["error"]["code"] == "AUDIT_LEVELS_INVALID", r.text
        assert _audit_run_count(migrated_db, pid=pid) == 0
        assert _product_status(migrated_db, pid) == "ingested"
    # 单品端点同一段判定，口径不许分叉——否则「批量堵上了、一个一个点还能绕」
    single = client.post(
        SINGLE_URL.format(pid),
        headers=_hdr(auth),
        json={"levels": ["L0"], "trigger_kind": "manual"},
    )
    assert single.status_code == 422, single.text
    assert single.json()["error"]["code"] == "AUDIT_LEVELS_INVALID", single.text
    assert _audit_run_count(migrated_db, pid=pid) == 0


def test_t20_empty_product_ids_returns_four_empty_buckets(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
) -> None:
    """T20：`product_ids: []` → 200 + 四个空桶（结构不变量 0 == 0）。

    防的故障：`AuditBatchIn` 的头注**明文承诺**了这个行为（并据此拒绝加
    `min_length=1`，理由是那会落进 FastAPI 无错误码的默认信封）。承诺没有判据 =
    一句会烂掉的话：预检那句 `id = ANY(:ids)` 传空 list 今天由 psycopg3 正常处理，
    换驱动/换写法就可能变成 500，而没有任何测试会红。
    """
    auth = _login(client, ADMIN, PASSWORD)
    r = client.post(BATCH_URL, headers=_hdr(auth), json={"product_ids": []})
    assert r.status_code == 200, r.text
    assert r.json() == {
        "audited": [],
        "skipped": [],
        "failed": [],
        "remaining": [],
        "verdict_counts": {"pass": 0, "reject": 0, "needs_review": 0},
        "total_llm_cost_usd": 0,
    }


def test_t21_breaker_probe_failure_does_not_abort_the_batch(
    client: TestClient,
    seeded: dict[str, int],
    migrated_db: str,
    fake_llm: _FakeLlm,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T21：熔断判定查询本身抛异常 → 整批照常跑完、HTTP 200、占位带 response 存活。

    防的故障（2026-07-28 审查阻塞项）：`_provider_looks_down` 曾是循环体内**唯一**
    没被 try 包住的 await。它开自己的短事务——连接池打满（批量本身长时间占着请求
    会话那条）或 DB 抖一下就会抛，异常逃出 `audit_batch` → `run_idempotent` 的
    `except` 分支 **DELETE 掉占位行** → 500。前端 NO_RESULT 档明确指导用户「原样重试
    同一批」，而占位已没了，同键重试被当成全新请求：**已经落库、已经花过钱的那几条
    再跑一遍，钱付第二遍**。这正是模块头注第二节全篇要防的那件事，却被这一行绕开。

    取 `tripped=False`（继续跑）而不是 True（当场刹停）：熔断只是省钱的启发式，
    探测失败不该把一批本可跑完的产品截停；墙钟预算那道刹车仍在。
    """
    pids = [
        _mk_product(migrated_db, seeded["team"], asin=f"B0BATPRB{i:02d}", title=f"Mug Prb {i}")
        for i in range(4)
    ]
    fake_llm.http_status = 500  # 每条都落 needs_review + llm_unavailable → 必然触发探测

    async def boom(*args: Any, **kwargs: Any) -> bool:
        raise RuntimeError("connection pool exhausted")

    monkeypatch.setattr(batch, "_provider_looks_down", boom)
    key = str(uuid.uuid4())
    auth = _login(client, ADMIN, PASSWORD)
    r = client.post(BATCH_URL, headers=_hdr(auth, key), json={"product_ids": pids})

    assert r.status_code == 200, r.text
    body = r.json()
    # 探测失败 = 不跳闸：四件全跑完，remaining 为空（跳闸的话第 4 件会落 remaining）
    assert [i["product_id"] for i in body["audited"]] == pids
    assert body["remaining"] == [] and body["failed"] == []
    assert body["verdict_counts"] == {"pass": 0, "reject": 0, "needs_review": 4}
    with psycopg.connect(migrated_db) as conn:
        row = conn.execute(
            "SELECT response IS NOT NULL FROM app.api_idempotency WHERE idem_key = %s", (key,)
        ).fetchone()
    assert row is not None and row[0] is True, "占位被删=同键重试整批重跑=重复扣费"
