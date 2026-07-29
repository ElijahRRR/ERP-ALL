"""R2-14 生命周期出口验收（14c 列表折叠 / 14a 产品删除）。

口径：`specs/001-domain-model/00-conventions.md §7.1`（三级硬删除规则 + 墓碑表 +
列表默认折叠），分片见 `specs/007-mvp-completion-plan/README.md` R2-14。

本文件对应验收⑥（列表默认不显示已停用项且开关可切）。14a 的判据随后续增量补入同文件
——**折叠与删除是同一个「出口」的两半**，判据放一起才看得出「删掉的确实从列表消失了」。

夹具复用 `test_audit_api.py`（团队/管理员/造产品），范式同 `test_audit_batch.py`：
另造一套夹具等于各自维护一份「什么算一个产品」，漂了没人发现。
"""

from typing import Any

import psycopg
from fastapi.testclient import TestClient

from .test_audit_api import (  # noqa: F401  夹具跨文件复用（pyproject 已为 tests/* 豁免 F811）
    ADMIN,
    _mk_product,
    client,
    seeded,
)
from .test_identity_api import PASSWORD, _login

LIST_URL = "/api/v1/products"


def _set_status(migrated_db: str, pid: int, status: str) -> None:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        conn.execute("UPDATE app.product SET status = %s WHERE id = %s", (status, pid))


def _list(client: TestClient, auth: dict[str, str], **params: Any) -> dict[str, Any]:
    r = client.get(LIST_URL, headers=auth, params={"size": 100, **params})
    assert r.status_code == 200, r.text
    return dict(r.json())


def _ids(body: dict[str, Any]) -> set[int]:
    return {int(it["id"]) for it in body["items"]}


class TestRetiredFold:
    """验收⑥：默认折叠 `retired`，开关可切，显式状态筛选优先。"""

    def test_default_hides_retired(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        live = _mk_product(migrated_db, seeded["team"], asin="B0R214F001")
        gone = _mk_product(migrated_db, seeded["team"], asin="B0R214F002")
        _set_status(migrated_db, gone, "retired")
        auth = _login(client, ADMIN, PASSWORD)

        body = _list(client, auth)
        ids = _ids(body)
        assert live in ids
        assert gone not in ids
        # **total 必须跟着折叠走**：若只过滤了行而 total 仍按全量算，分页会出现
        # 「共 N 条」却翻不到的空页——用户看到的是「系统丢了商品」。
        assert body["total"] == len(ids)

    def test_switch_reveals_retired(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        live = _mk_product(migrated_db, seeded["team"], asin="B0R214F003")
        gone = _mk_product(migrated_db, seeded["team"], asin="B0R214F004")
        _set_status(migrated_db, gone, "retired")
        auth = _login(client, ADMIN, PASSWORD)

        ids = _ids(_list(client, auth, include_retired="true"))
        assert {live, gone} <= ids

    def test_explicit_status_retired_wins_over_fold(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """**这条是本增量最容易写错的方向**：默认折叠若与 status 筛选叠成 AND，
        运营在下拉里选「已下架」会得到空列表——一个明确要求看某状态、系统却按默认
        把它过滤掉的行为，在用户眼里就是功能坏了。故两者互斥，不是两个 AND 条件。
        """
        gone = _mk_product(migrated_db, seeded["team"], asin="B0R214F005")
        _set_status(migrated_db, gone, "retired")
        auth = _login(client, ADMIN, PASSWORD)

        body = _list(client, auth, status="retired")  # 不传 include_retired
        assert gone in _ids(body)
        assert {it["status"] for it in body["items"]} == {"retired"}

    def test_explicit_status_not_widened_by_switch(
        self, client: TestClient, seeded: dict, migrated_db: str
    ) -> None:
        """反方向同样要钉住：开关打开也不能把 `retired` 掺进按状态筛选的结果里。"""
        live = _mk_product(migrated_db, seeded["team"], asin="B0R214F006")
        gone = _mk_product(migrated_db, seeded["team"], asin="B0R214F007")
        _set_status(migrated_db, gone, "retired")
        auth = _login(client, ADMIN, PASSWORD)

        body = _list(client, auth, status="ingested", include_retired="true")
        ids = _ids(body)
        assert live in ids
        assert gone not in ids
