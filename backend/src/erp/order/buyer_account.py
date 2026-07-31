"""买家账号池 + 采购插件实例的管理面（R2-13 13b；图纸 `07:249-282`）。

与 `purchaser` CRUD 并列同域同 tag（`Order`）——买家账号是「谁去亚马逊下单」的另一种
形态，与采购方是同一类运营对象，不值得为它另开一个契约 tag 面。服务单独成文件只是
为了不把已 500+ 行的 `order/router.py` 继续撑大。

## 三条纪律（违反任何一条即安全事故）

1. **明文令牌只在签发响应里出现一次**。列表端点不返回 token 的任何形态（含 hash）；
   审计 `after` 快照同样不得含 token 或 hash——审计表是长期留存面，把 hash 写进去等于
   给离线爆破留了素材，而 `after` 快照本就不该有凭证。遗失只能吊销后重新签发。
2. **`external_customer_id` 是必填的人工录入项**，不是系统生成值。来源=在指纹浏览器里
   打开该买家账号的亚马逊页面 → 点插件面板「提取 customerId」→ 复制粘贴。Owner
   2026-07-30 补-2 明确：**预配置的账号 ID 本来就是这么来的**，那个按钮要保留
   （一手来源 §7 写「不需要」，那条错）。前端表单必须挂这段帮助文案，否则运营开不了号。
3. **`exec_mode` 默认 `stop_before_payment`**：新签发的实例**不会花钱**。升到 `live`
   是一次显式的 PATCH，有 audit 留痕。默认值即安全边界，不要为了「省一步」改默认。
   **且签发端点根本不收 `live`**（`ISSUE_EXEC_MODE_PATTERN`，审查 B2）：只把 live 写成
   「非默认值」挡不住 `{"exec_mode": "live"}` 这一步直签——那条不变量得由校验器执行，
   不是由默认值执行。

## 为什么没有 DELETE 端点

买家账号的删除分支挂在 14b 之后另补（0043 头注、`review_list.json:1097`）：授 DELETE
就必须同时建 DELETE 策略，且要处置「该账号名下在途执行单」与 `plugin_instance` 的硬
外键——那是删除路径要显式解决的问题，不是本单顺手能带的。停用走 `status='retired'`。
"""

from typing import Any

from fastapi import Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.audit import AuditWriter
from erp.core.authn import CurrentUser
from erp.core.errors import BusinessError
from erp.identity.schemas import Page
from erp.plugin import auth as plugin_auth

SITE_PATTERN = "^(amazon_com|amazon_ca|amazon_co_jp)$"
STATUS_PATTERN = "^(active|paused|blocked|retired)$"
EXEC_MODE_PATTERN = "^(dry_run|stop_before_payment|live)$"
# **签发端点不收 `live`**（审查 B2）：纪律 3 说的是「升到 live 是一次显式的 PATCH」，
# 而签发端点原先照抄了三档 pattern——于是一次 POST 就能直接拿到一把实盘令牌，
# 那条不变量（以及它保证的「新令牌一定先经过不花钱的演练」）等于没有。
# PATCH 仍收三档：升档本来就该从那里走，且它有 audit 留痕与前端二次确认。
ISSUE_EXEC_MODE_PATTERN = "^(dry_run|stop_before_payment)$"

_ACCOUNT_COLUMNS = (
    "id, team_id, label, site, external_customer_id, status, daily_cap,"
    " last_seen_at, note, created_at, updated_at"
)
# 实例列表**逐字不含 token_hash**——纪律 1 的落点在这一行。
_INSTANCE_COLUMNS = (
    "id, buyer_account_id, status, exec_mode, version,"
    " last_seen_at, revoked_at, created_at, updated_at"
)


class BuyerAccountIn(BaseModel):
    """建号与改号共用。建号时 label/site/external_customer_id 必填（服务层校验）。

    `daily_cap` 用 `model_fields_set` 区分「没传」与「显式传 null（＝改成不限）」——
    单靠 `None` 分不开这两件事，而「把日限改回不限」是运营会做的正常操作。
    """

    label: str | None = Field(default=None, min_length=1, max_length=100)
    site: str | None = Field(default=None, pattern=SITE_PATTERN)
    external_customer_id: str | None = Field(default=None, min_length=1, max_length=64)
    status: str | None = Field(default=None, pattern=STATUS_PATTERN)
    daily_cap: int | None = Field(default=None, ge=1, le=10000)
    note: str | None = Field(default=None, max_length=1000)


class PluginInstanceIssueIn(BaseModel):
    """签发入参。`exec_mode` **只收两个演练档**（`ISSUE_EXEC_MODE_PATTERN` 的理由见其注释）。"""

    exec_mode: str = Field(default="stop_before_payment", pattern=ISSUE_EXEC_MODE_PATTERN)
    version: str | None = Field(default=None, max_length=40)


class PluginInstancePatchIn(BaseModel):
    """只改 `exec_mode`。其余列要么不可变（team/account），要么另有专用端点（revoke）。"""

    exec_mode: str = Field(pattern=EXEC_MODE_PATTERN)


def _duplicate_error(exc: IntegrityError) -> BusinessError:
    """把 `uq_buyer_account*` 的唯一冲突翻译成能直接照做的提示。

    指明撞的是哪一把键：两把键的处置完全不同（撞 customerId ＝ 这个亚马逊账号已经建过，
    去改那一条；撞 label ＝ 换个名字）。只回「重复」会让运营两条路都试一遍。
    """
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    name = str(getattr(diag, "constraint_name", "") or "")
    blob = str(getattr(exc, "orig", exc))
    if name == "uq_buyer_account_label" or "uq_buyer_account_label" in blob:
        field, hint = "label", "同团队内已有同名买家账号——换一个名字"
    else:
        field, hint = (
            "external_customer_id",
            "同团队内已有该 customerId 的买家账号——同一个亚马逊账号不要建两条",
        )
    return BusinessError("BUYER_ACCOUNT_DUPLICATE", hint, detail={"field": field}, http_status=409)


async def _load_account(session: AsyncSession, account_id: int, team_id: int) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(f"SELECT {_ACCOUNT_COLUMNS} FROM app.buyer_account WHERE id = :i"),
                {"i": account_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["team_id"] != team_id:
        raise BusinessError("BUYER_ACCOUNT_NOT_FOUND", "买家账号不存在", http_status=404)
    return dict(row)


async def list_accounts(
    session: AsyncSession,
    *,
    team_id: int,
    site: str | None,
    status: str | None,
    q: str | None,
    page: int,
    size: int,
) -> Page[dict[str, Any]]:
    """服务端分页（008 §3）——账号池会长到几十上百条，不做一次拉全量的列表。"""
    where = "WHERE team_id = :t"
    params: dict[str, Any] = {"t": team_id}
    if site:
        where += " AND site = :site"
        params["site"] = site
    if status:
        where += " AND status = :st"
        params["st"] = status
    if q:
        where += " AND label ILIKE :q"
        params["q"] = f"%{q}%"
    total = int(
        (
            await session.execute(text(f"SELECT count(*) FROM app.buyer_account {where}"), params)
        ).scalar_one()
    )
    rows = (
        await session.execute(
            text(
                f"SELECT {_ACCOUNT_COLUMNS},"
                " (SELECT count(*) FROM app.plugin_instance pi"
                "   WHERE pi.buyer_account_id = app.buyer_account.id"
                "     AND pi.status = 'active') AS active_instance_count"
                f" FROM app.buyer_account {where}"
                " ORDER BY id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    items = [{k: v for k, v in dict(r).items() if k != "team_id"} for r in rows]
    return Page(items=items, total=total, page=page, size=size)


async def create_account(
    session: AsyncSession, *, user: CurrentUser, request: Request | None, body: BuyerAccountIn
) -> dict[str, Any]:
    if not body.label or not body.site or not body.external_customer_id:
        raise BusinessError(
            "BUYER_ACCOUNT_FIELDS_REQUIRED",
            "label/site/external_customer_id 必填"
            "（customerId 用插件面板「提取 customerId」按钮取出后粘贴）",
        )
    try:
        async with session.begin_nested():
            aid = (
                await session.execute(
                    text(
                        "INSERT INTO app.buyer_account"
                        " (team_id, label, site, external_customer_id, status, daily_cap,"
                        "  note, created_by)"
                        " VALUES (:t, :l, :s, :c, :st, :cap, :n, :by) RETURNING id"
                    ),
                    {
                        "t": user.team_id,
                        "l": body.label,
                        "s": body.site,
                        "c": body.external_customer_id,
                        "st": body.status or "active",
                        "cap": body.daily_cap,
                        "n": body.note,
                        "by": user.id,
                    },
                )
            ).scalar_one()
    except IntegrityError as exc:
        raise _duplicate_error(exc) from exc
    await AuditWriter.for_user(session, user, request).log(
        "buyer_account.create",
        "buyer_account",
        aid,
        after=body.model_dump(exclude_none=True),
    )
    return {"id": int(aid)}


async def update_account(
    session: AsyncSession,
    *,
    user: CurrentUser,
    request: Request | None,
    account_id: int,
    body: BuyerAccountIn,
) -> dict[str, Any]:
    before = await _load_account(session, account_id, user.team_id or -1)
    sets: list[str] = []
    params: dict[str, Any] = {"i": account_id}
    for col, val in (
        ("label", body.label), ("site", body.site), ("status", body.status),
        ("external_customer_id", body.external_customer_id), ("note", body.note),
    ):  # fmt: skip
        if val is not None:
            sets.append(f"{col} = :{col}")
            params[col] = val
    if "daily_cap" in body.model_fields_set:  # 显式传 null = 改成不限
        sets.append("daily_cap = :daily_cap")
        params["daily_cap"] = body.daily_cap
    if sets:
        try:
            async with session.begin_nested():
                await session.execute(
                    text(f"UPDATE app.buyer_account SET {', '.join(sets)} WHERE id = :i"), params
                )
        except IntegrityError as exc:
            raise _duplicate_error(exc) from exc
    await AuditWriter.for_user(session, user, request).log(
        "buyer_account.update",
        "buyer_account",
        account_id,
        before={k: before[k] for k in ("label", "site", "status", "daily_cap")},
        after=body.model_dump(exclude_unset=True),
    )
    return {"id": account_id}


async def list_instances(
    session: AsyncSession, *, team_id: int, account_id: int
) -> list[dict[str, Any]]:
    """列该账号的插件实例。**响应逐字不含 token**（纪律 1）。"""
    await _load_account(session, account_id, team_id)
    rows = (
        await session.execute(
            text(
                f"SELECT {_INSTANCE_COLUMNS} FROM app.plugin_instance"
                " WHERE buyer_account_id = :a ORDER BY id DESC"
            ),
            {"a": account_id},
        )
    ).mappings()
    return [dict(r) for r in rows]


async def issue_instance(
    session: AsyncSession,
    *,
    user: CurrentUser,
    request: Request | None,
    account_id: int,
    body: PluginInstanceIssueIn,
) -> dict[str, Any]:
    """签发一个实例令牌。**明文只在本函数的返回值里出现这一次**（纪律 1）。

    权限点单列 `procurement.plugin_instance_admin`：签发 = 发一个能代表该买家账号
    真下单的凭证，与「改个备注名」不是同一量级（0043 头注）。
    """
    account = await _load_account(session, account_id, user.team_id or -1)
    token = plugin_auth.mint_token()
    instance_id = (
        await session.execute(
            text(
                "INSERT INTO app.plugin_instance"
                " (team_id, buyer_account_id, token_hash, exec_mode, version, created_by)"
                " VALUES (:t, :a, :h, :m, :v, :by) RETURNING id"
            ),
            {
                "t": account["team_id"],
                "a": account_id,
                "h": plugin_auth.token_digest(token),
                "m": body.exec_mode,
                "v": body.version,
                "by": user.id,
            },
        )
    ).scalar_one()
    # after 快照只记「签给谁、什么档」——**不含 token 明文，也不含 hash**（纪律 1）。
    await AuditWriter.for_user(session, user, request).log(
        "plugin_instance.issue",
        "plugin_instance",
        instance_id,
        after={
            "buyer_account_id": account_id,
            "exec_mode": body.exec_mode,
            "version": body.version,
        },
    )
    return {"id": int(instance_id), "buyer_account_id": account_id, "token": token}


async def _load_instance(session: AsyncSession, instance_id: int, team_id: int) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                text(f"SELECT {_INSTANCE_COLUMNS}, team_id FROM app.plugin_instance WHERE id = :i"),
                {"i": instance_id},
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None or row["team_id"] != team_id:
        raise BusinessError("PLUGIN_INSTANCE_NOT_FOUND", "插件实例不存在", http_status=404)
    return dict(row)


async def revoke_instance(
    session: AsyncSession, *, user: CurrentUser, request: Request | None, instance_id: int
) -> dict[str, Any]:
    """吊销实例（`status='revoked'`）。已吊销的再调一次是 no-op，不报错。

    吊销是 UPDATE 不是 DELETE：`plugin_instance` 没授 DELETE（0044），且历史实例 id
    还挂在 `procurement_order.backfill_actor_id` 上（`kind='plugin'` 时解读为实例 id），
    删行会让「这单谁填的」失去落点。
    """
    instance = await _load_instance(session, instance_id, user.team_id or -1)
    if instance["status"] == "active":
        await session.execute(
            text(
                "UPDATE app.plugin_instance SET status = 'revoked', revoked_at = now()"
                " WHERE id = :i AND status = 'active'"
            ),
            {"i": instance_id},
        )
        await AuditWriter.for_user(session, user, request).log(
            "plugin_instance.revoke",
            "plugin_instance",
            instance_id,
            before={"status": "active"},
            after={"status": "revoked"},
        )
    return {"id": instance_id, "status": "revoked"}


async def update_instance(
    session: AsyncSession,
    *,
    user: CurrentUser,
    request: Request | None,
    instance_id: int,
    body: PluginInstancePatchIn,
) -> dict[str, Any]:
    """改执行档。切到 `live` 意味着该实例此后真实下单花钱，故必须显式提交且留审计。"""
    instance = await _load_instance(session, instance_id, user.team_id or -1)
    if instance["status"] != "active":
        raise BusinessError(
            "PLUGIN_INSTANCE_REVOKED", "已吊销的实例不可改档——请重新签发", http_status=409
        )
    await session.execute(
        text("UPDATE app.plugin_instance SET exec_mode = :m WHERE id = :i"),
        {"m": body.exec_mode, "i": instance_id},
    )
    await AuditWriter.for_user(session, user, request).log(
        "plugin_instance.update",
        "plugin_instance",
        instance_id,
        before={"exec_mode": instance["exec_mode"]},
        after={"exec_mode": body.exec_mode},
    )
    return {"id": instance_id, "exec_mode": body.exec_mode}
