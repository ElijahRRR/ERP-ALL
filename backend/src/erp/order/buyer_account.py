"""买家账号池 + 采购插件实例的管理面（R2-13 13b；图纸 `07:249-282`）。

与 `purchaser` CRUD 并列同域同 tag（`Order`）——买家账号是「谁去亚马逊下单」的另一种
形态，与采购方是同一类运营对象，不值得为它另开一个契约 tag 面。服务单独成文件只是
为了不把已 500+ 行的 `order/router.py` 继续撑大。

## 三条纪律（违反任何一条即安全事故）

1. **明文令牌只在签发响应里出现一次**。列表端点不返回 token 的任何形态（含 hash）；
   审计 `after` 快照同样不得含 token 或 hash——审计表是长期留存面，把 hash 写进去等于
   给离线爆破留了素材，而 `after` 快照本就不该有凭证。遗失只能吊销后重新签发。
2. **签发是团队级的，不挂在任何买家账号下**（身份模型更正，Owner 2026-07-30，图纸
   `07:288-340`）：令牌绑「一台授权浏览器」，「此刻登的是哪个号」由插件每次请求带的
   `customerId` 在团队内解析。旧形状 `POST /buyer-accounts/{id}/plugin-instances`
   **已删除而不是保留**——它的形状本身就是「先有账号才能发令牌」，正是 Owner 指出的
   死循环（装插件前先要知道 customerId，而 customerId 只有装了插件才看得到）。
   留着它等于把被推翻的模型在契约里留一份可用副本。
3. **`exec_mode` 默认 `stop_before_payment`**：新签发的实例**不会花钱**。升到 `live`
   是一次显式的 PATCH，有 audit 留痕。默认值即安全边界，不要为了「省一步」改默认。
   **且签发端点根本不收 `live`**（`ISSUE_EXEC_MODE_PATTERN`，审查 B2）：只把 live 写成
   「非默认值」挡不住 `{"exec_mode": "live"}` 这一步直签——那条不变量得由校验器执行，
   不是由默认值执行。

## 账号状态机（本轮新增；首见自动登记的出口就在这里）

`pending_claim` 是**发现态**：插件带上来一个本团队没见过的 `customerId`，服务端自动
落一条待认领行（`plugin/identity.py`），**未认领一律不派单**。运营的两个出口：

- **认领** `pending_claim → active`：必须同时补齐 `label` 与 `site`，库层
  `ck_buyer_account_claimed` 兜底，缺则 422 `BUYER_ACCOUNT_CLAIM_INCOMPLETE`。
- **驳回** `pending_claim → rejected`：**终态**。不删行是刻意的——本表无 DELETE 授权，
  而驳回必须**粘住**：该 `(team_id, customerId)` 被 `uq_buyer_account` 永久占住，
  同一个伪造 id 再灌一万次都只解析到那一行、零新增行、零新通知。
  **粘性由两件东西共同保证，缺一即失效**：唯一索引占着那个值，**以及**结果态为
  `rejected` 的请求不许挪动 `external_customer_id`（`_guard_rejected_customer_id`，
  409 `BUYER_ACCOUNT_REJECTED_IMMUTABLE`；「结果态」包括本来就 rejected 的行**和**
  同一次 PATCH 里刚驳回的——只看 before 会被「驳回+改 id 合并成一笔」绕过）。
  只有前者时，把那一行的 id 改走就当场解除了粘性——「终态」在实效上退化成
  「到下一次有人改这行为止」。

**任何状态都不许转回 `pending_claim`**：那是发现态，不是运营可选态。
**建号也不许直接建这两个系统态**（`CREATE_STATUS_CHOICES`）——转入与凭空建是两条路，
只堵一条等于没堵。

## 为什么没有 DELETE 端点

买家账号的删除分支挂在 14b 之后另补（0043 头注、`review_list.json:1097`）：授 DELETE
就必须同时建 DELETE 策略，且要处置「该账号名下在途执行单」——那是删除路径要显式解决
的问题，不是本单顺手能带的。停用走 `status='retired'`。
**这条与「驳回是终态」互为因果**：正因为删不掉，驳回才必须粘住而不是删行。
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
# 词表与 0043 的 `ck_buyer_account_status` 同源（本轮加 `pending_claim` 与 `rejected`）。
# 合法转移另由 `_ALLOWED_TRANSITIONS` 约束——pattern 只管「是不是词表内的值」。
STATUS_PATTERN = "^(pending_claim|active|paused|blocked|retired|rejected)$"
# **建号只收人工可选的四值**（＝0043 现行 CHECK 全集 **减去两个系统态**，审查 F4）。
#
# `pending_claim` 与 `rejected` 是**系统发现/治理态**，不是运营在建号表单上该选的值：
# - `pending_claim` 的语义是「插件带上来一个我们没见过的号」，由 `plugin/identity.py`
#   独家生产。手工建一条 pending_claim 行是自相矛盾的——那个号根本没被任何插件见过，
#   却在池子里冒充「有人在等认领」，运营会去认领一个不存在的发现。
# - `rejected` 的语义是「这个号被驳回过」，前提是它先被发现过。直接建一条 rejected 行
#   等于凭空造一段并未发生的治理历史（且 `_ALLOWED_TRANSITIONS` 让它**当场变成死行**：
#   rejected 是终态，建完就再也改不动，本表又无 DELETE 授权 ⇒ 永久垃圾）。
#
# **这条收窄同时是洪水闸精确性的前提**：`_register` 的 advisory 锁只锁自己那条路径，
# `pending_claim` 行必须**只有它一个生产者**，cap 才是精确的（见
# `plugin/identity.py::_REGISTER_LOCK_SQL` 末段）。加回来就等于把闸重新变成近似值。
#
# 转入方向另由 `_ALLOWED_TRANSITIONS` 挡（任何态都不许改回 `pending_claim`）；
# 本常量挡的是**凭空建**那一面——两处缺一，那两个系统态就有一条能被人工写出来的路。
CREATE_STATUS_CHOICES = ("active", "paused", "blocked", "retired")
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
# `buyer_account_id` 本轮随身份模型更正删除；`last_seen_customer_id` 是它的替代观察列
# （**不参与鉴权**，0044 头注六）。
_INSTANCE_COLUMNS = (
    "id, status, exec_mode, version, last_seen_customer_id,"
    " last_seen_at, revoked_at, created_at, updated_at"
)

# 账号状态机（本轮新增，理由见模块头注）。键 = 现状，值 = 允许转入的目标。
# 自转移一律允许：PATCH 常常只改 note/daily_cap 而把 status 原样带上来。
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "pending_claim": {"pending_claim", "active", "rejected"},
    "active": {"active", "paused", "blocked", "retired"},
    "paused": {"paused", "active", "blocked", "retired"},
    "blocked": {"blocked", "active", "paused", "retired"},
    "retired": {"retired", "active"},
    # 终态：驳回是粘的，粘性即治理（无 DELETE 授权 ⇒ 该 customerId 被永久占住 ⇒
    # 同一个伪造 id 再灌不会产生新行、新通知）。
    "rejected": {"rejected"},
}


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


def _write_error(exc: IntegrityError) -> BusinessError:
    """把 `buyer_account` 的库层约束违例翻译成能直接照做的提示。

    指明撞的是哪一把键：处置完全不同（撞 customerId ＝ 这个亚马逊账号已经建过/已被自动
    登记，去改那一条；撞 label ＝ 换个名字；撞认领闸 ＝ 补齐站点与账号名）。
    只回「重复」会让运营把几条路都试一遍。

    **认领闸不能裸奔成 500**：`ck_buyer_account_claimed` 是「运营补 label/site 后转
    active」那句话的库内执行者，运营在界面上少填一格是**日常操作**，不是系统故障。
    """
    diag = getattr(getattr(exc, "orig", None), "diag", None)
    name = str(getattr(diag, "constraint_name", "") or "")
    blob = str(getattr(exc, "orig", exc))
    if name == "ck_buyer_account_claimed" or "ck_buyer_account_claimed" in blob:
        return BusinessError(
            "BUYER_ACCOUNT_CLAIM_INCOMPLETE",
            "认领必须同时补齐账号名与站点——没有站点就不知道该去哪个亚马逊站下单",
            detail={"fields": ["label", "site"]},
            http_status=422,
        )
    if name == "uq_buyer_account_label" or "uq_buyer_account_label" in blob:
        field, hint = "label", "同团队内已有同名买家账号——换一个名字"
    else:
        field, hint = (
            "external_customer_id",
            "同团队内已有该 customerId 的买家账号——同一个亚马逊账号不要建两条",
        )
    return BusinessError("BUYER_ACCOUNT_DUPLICATE", hint, detail={"field": field}, http_status=409)


def _transition_action(current: str, target: str | None) -> str:
    """校验状态转移合法性，并返回本次该记哪个审计动作名。

    **审计动作名按转移分化**（`buyer_account.claim` / `.reject` / `.update`）：审计面要能
    一眼看出「谁认领的、谁驳回的」——那两件事的责任分量与「改个备注」不是一回事。
    """
    if target is None or target == current:
        return "buyer_account.update"
    allowed = _ALLOWED_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise BusinessError(
            "BUYER_ACCOUNT_STATUS_TRANSITION",
            f"买家账号状态不能从 {current} 改成 {target}"
            + (
                "——已驳回是终态（该 customerId 已被永久占用，不会再自动登记）"
                if current == "rejected"
                else "——「待认领」是系统发现态，不是可选状态"
                if target == "pending_claim"
                else ""
            ),
            detail={"from": current, "to": target, "allowed": sorted(allowed)},
            http_status=409,
        )
    if current == "pending_claim" and target == "active":
        return "buyer_account.claim"
    if target == "rejected":
        return "buyer_account.reject"
    return "buyer_account.update"


def _guard_rejected_customer_id(before: dict[str, Any], body: BuyerAccountIn) -> None:
    """已驳回行的 `external_customer_id` **不可再改**（审查 F3：粘性此前可被 PATCH 解除）。

    **粘性是三处声明的同一件事**，而在本轮修复之前它们全都只依赖唯一索引「碰巧还占着
    那个值」，没有任何东西阻止有人把那个值挪走：
    - `0043` 迁移头注：「驳回**不删行**，于是该 `(team_id, customerId)` 被
      `uq_buyer_account` **永久占住** ⇒ 同一个伪造 id 再灌一万次都只解析到那一行」；
    - `plugin/identity.py` 头注治理面 2：「`rejected` 终态 + 天然粘性……粘性不是额外
      代码，是唯一索引的自然结果」；
    - 本模块头注与 openapi PATCH 说明：「驳回是终态……该 customerId 被永久占住」。

    **实测的解除序列（四步）**：① 插件灌一个伪造 customerId ⇒ 落 `pending_claim` 行；
    ② 运营驳回 ⇒ `rejected`（此时唯一索引占着那个值）；③ **PATCH 把这条 rejected 行的
    `external_customer_id` 改成别的字符串** ⇒ 唯一索引对原值的占用**当场释放**；
    ④ 同一个伪造 customerId 再灌一次 ⇒ 又是「没见过」⇒ 新增一条待认领行 + 新一条通知。
    于是「驳回是终态」在实际效果上只是「驳回到下一次有人改这条行的 id 为止」。

    第 ③ 步不需要越权也不需要绕过任何闸——它是本端点的正常入参，且**状态机守卫拦不住
    它**：`_transition_action` 只看 status，body 里不带 status 时它连分支都不进。
    故守卫必须单独写在这里。

    **判定看「结果态」，不是只看 `before` 态**（复验抓的三步变体）：只判 `before`
    的话，把 ②③ 合并成一次 `PATCH {"status":"rejected","external_customer_id":新串}`
    就整条绕过——转移 `pending_claim→rejected` 合法、守卫因 `before` 还是待认领而提前
    返回，随后同一条 UPDATE 把两列一起写掉；落库的 rejected 行占的还是一个**从未被灌过**
    的 customerId（凭空造出一段并未发生的治理历史，正是建号词表收窄要挡的同一件事）。
    故规则是：**凡本次请求的结果态为 `rejected`，`external_customer_id` 一律不许挪动**
    ——不管它是本来就 rejected，还是这一笔请求里刚变成 rejected。整个请求 409 原子拒绝，
    不做「驳回生效、改 id 忽略」的部分放行（静默丢弃入参比报错更糟）。

    **只拦「值真的会变」**：把同一个值原样带上来（前端整表单回填是常见形状）是 no-op，
    拦它只会制造假报错——于是「整表单回填 + 驳回」一次请求照常 200。改动的其余列
    （label/note/daily_cap）照常放行——驳回行仍然要能被批注「这是谁在什么时候灌的」。
    """
    result_is_rejected = str(before["status"]) == "rejected" or body.status == "rejected"
    if not result_is_rejected:
        return
    incoming = body.external_customer_id
    if incoming is None or incoming == before["external_customer_id"]:
        return
    raise BusinessError(
        "BUYER_ACCOUNT_REJECTED_IMMUTABLE",
        "已驳回（含本次请求驳回）账号的 customerId 不可修改——驳回是终态，靠这一行永久"
        "占住那个 customerId 才能挡住「同一个伪造 id 再灌」；改掉它等于把驳回撤销（且本表"
        "无 DELETE 授权，旧行也删不掉）。要给这个新 customerId 建号，请另建一条",
        detail={
            "field": "external_customer_id",
            "status_before": str(before["status"]),
            "status_requested": body.status,
        },
        http_status=409,
    )


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
    include_rejected: bool = False,
    q: str | None,
    page: int,
    size: int,
) -> Page[dict[str, Any]]:
    """服务端分页（008 §3）——账号池会长到几十上百条，不做一次拉全量的列表。

    **默认折叠 `rejected`**：驳回是终态且不删行（无 DELETE 授权，粘性即治理），
    被灌过伪造 customerId 的团队池子里会长期躺着一堆已驳回行。语义逐字照搬 14c
    折叠 `retired` 的既有做法（`catalog/router.py::list_products`）。
    """
    where = "WHERE team_id = :t"
    params: dict[str, Any] = {"t": team_id}
    if site:
        where += " AND site = :site"
        params["site"] = site
    if status:
        # **显式筛选优先，折叠不叠加**（同 14c）：运营在下拉里选「已驳回」却得到空列表，
        # 看起来就是功能坏了。故 status 与 include_rejected 是互斥两条路，不是 AND。
        where += " AND status = :st"
        params["st"] = status
    elif not include_rejected:
        where += " AND status <> 'rejected'"
    if q:
        where += " AND label ILIKE :q"
        params["q"] = f"%{q}%"
    total = int(
        (
            await session.execute(text(f"SELECT count(*) FROM app.buyer_account {where}"), params)
        ).scalar_one()
    )
    # **不再有 `active_instance_count` 子查询**：实例不属于账号了（令牌绑浏览器），
    # 那个数没有意义——留着只会让运营以为「这个号有 2 台机器在跑」。
    rows = (
        await session.execute(
            text(
                f"SELECT {_ACCOUNT_COLUMNS} FROM app.buyer_account {where}"
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
    """手工预建一个买家账号。**这是可选捷径，不是必经通道。**

    主路径是**首见自动登记**：让插件在那台浏览器上跑一次拉取，服务端会把它带上来的
    `customerId` 落成一条待认领行，运营再补 label/site 认领即可（`plugin/identity.py`）。
    本端点保留是因为运营手上若已经有 customerId（例如从别处抄来的），预建能省一轮往返。

    `external_customer_id` 在**本端点**仍必填：一条解析不到的账号行永远收不到任务，
    是一条无用行。缺它的正确做法是走主路径，而不是在这里留个空。

    **`status` 只收人工四值**（`CREATE_STATUS_CHOICES`，理由见该常量注释）：两个系统态
    `pending_claim` / `rejected` 直接建出来与「系统发现态」的口径自相矛盾，且 `rejected`
    建完即死行（终态 + 无 DELETE 授权）。
    """
    if not body.label or not body.site or not body.external_customer_id:
        raise BusinessError(
            "BUYER_ACCOUNT_FIELDS_REQUIRED",
            "label/site/external_customer_id 必填"
            "（若还不知道 customerId，别在这里建号——让插件在那台浏览器上跑一次拉取，"
            "系统会自动登记一条待认领账号，届时补齐名称与站点认领即可）",
        )
    if body.status is not None and body.status not in CREATE_STATUS_CHOICES:
        raise BusinessError(
            "BUYER_ACCOUNT_STATUS_NOT_CREATABLE",
            f"建号不能直接指定 {body.status}——它是系统态不是运营可选态"
            "（「待认领」由插件首见自动登记产生；「已驳回」只能从待认领行驳回而来，"
            "直接建出来是一条改不动也删不掉的死行）",
            detail={"field": "status", "allowed": list(CREATE_STATUS_CHOICES)},
            http_status=422,
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
        raise _write_error(exc) from exc
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
    """改买家账号。**认领与驳回也走这里**（不新增路由、不新增权限码）。

    状态转移由 `_ALLOWED_TRANSITIONS` 守卫；`label`/`site` 是否补齐由库层
    `ck_buyer_account_claimed` 守卫（`_write_error` 把它翻成 422，不许裸奔成 500）。
    结果态为 rejected 的请求（本来就驳回的行 + 本次请求刚驳回的）挪动
    `external_customer_id` 由 `_guard_rejected_customer_id` 守卫（409）——
    **那一条与状态机是两件事**：改 id 的请求可以完全不带 status，状态机连分支都不进；
    带 status 合并驳回的形状则靠「看结果态」拦住。
    审计动作名按转移分化——审计面要能一眼看出「谁认领的、谁驳回的」。
    """
    before = await _load_account(session, account_id, user.team_id or -1)
    action = _transition_action(str(before["status"]), body.status)
    _guard_rejected_customer_id(before, body)
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
            raise _write_error(exc) from exc
    await AuditWriter.for_user(session, user, request).log(
        action,
        "buyer_account",
        account_id,
        before={k: before[k] for k in ("label", "site", "status", "daily_cap")},
        after=body.model_dump(exclude_unset=True),
    )
    return {"id": account_id}


async def list_instances(
    session: AsyncSession,
    *,
    team_id: int,
    status: str | None,
    page: int,
    size: int,
) -> Page[dict[str, Any]]:
    """列**本团队**的插件实例（服务端分页，008 §3）。**响应逐字不含 token**（纪律 1）。

    `LEFT JOIN buyer_account` 取最近登录号的名称与状态：只显示一串 `customerId`
    对运营没有用，图纸 `07:283` 说的排障场景（「这台机器现在登的是哪个号」）需要看得懂
    的名字，以及「那个号认领了没有」。JOIN 条件带 `ba.team_id = :t` 是显式防线②
    ——`last_seen_customer_id` 是外部输入的字符串，不是外键。
    """
    where = "WHERE pi.team_id = :t"
    params: dict[str, Any] = {"t": team_id}
    if status:
        where += " AND pi.status = :st"
        params["st"] = status
    total = int(
        (
            await session.execute(
                text(f"SELECT count(*) FROM app.plugin_instance pi {where}"), params
            )
        ).scalar_one()
    )
    columns = ", ".join(f"pi.{c.strip()}" for c in _INSTANCE_COLUMNS.split(","))
    rows = (
        await session.execute(
            text(
                f"SELECT {columns},"
                " ba.label AS last_seen_account_label,"
                " ba.status AS last_seen_account_status"
                " FROM app.plugin_instance pi"
                " LEFT JOIN app.buyer_account ba"
                "   ON ba.team_id = :t AND ba.external_customer_id = pi.last_seen_customer_id"
                f" {where} ORDER BY pi.id DESC LIMIT :lim OFFSET :off"
            ),
            {**params, "lim": size, "off": (page - 1) * size},
        )
    ).mappings()
    return Page(items=[dict(r) for r in rows], total=total, page=page, size=size)


async def issue_instance(
    session: AsyncSession,
    *,
    user: CurrentUser,
    request: Request | None,
    body: PluginInstanceIssueIn,
) -> dict[str, Any]:
    """签发一个**团队级**实例令牌。**明文只在本函数的返回值里出现这一次**（纪律 1）。

    权限点单列 `procurement.plugin_instance_admin`：签发 = 发一个能代表本团队
    真下单的凭证，与「改个备注名」不是同一量级（0043 头注）。

    **不收 `account_id`**（纪律 2）：令牌绑一台授权浏览器，装插件时还不知道那台浏览器
    会登哪个买家号——那正是死循环被解开的地方。

    **`team_id` 必须真实存在**（审查 F8）：令牌的**全部**授权语义就是「这是团队 T 的一台
    授权浏览器」（`plugin/auth.py` → `plugin_instance.team_id`），一把没有团队的令牌是
    一个没有意义的对象。无团队的主体（超管未切团队）此前会把 `None` 直送 INSERT，撞
    `plugin_instance.team_id` 的 NOT NULL/FK 变成 **500**——运维看到的是「服务器错误」，
    而真因是「你没在任何团队上下文里」。这里显式 409 说清楚。
    **不套用本文件其余读路径的 `or -1` 哨兵**：那个值在读路径上只是查空（无害），
    在写路径上会撞外键，等于把一个可读的 409 换成一个 500（同
    `automation/router.py:99` 与 `audit/router.py:125` 已记的同一条教训）。
    """
    if user.team_id is None:
        raise BusinessError(
            "TEAM_REQUIRED",
            "签发插件令牌须在具体团队上下文中进行——令牌绑定的就是「团队 T 的一台授权"
            "浏览器」，没有团队的令牌无法解析任何任务。请超管先切换到目标团队",
            http_status=409,
        )
    token = plugin_auth.mint_token()
    instance_id = (
        await session.execute(
            text(
                "INSERT INTO app.plugin_instance"
                " (team_id, token_hash, exec_mode, version, created_by)"
                " VALUES (:t, :h, :m, :v, :by) RETURNING id"
            ),
            {
                "t": user.team_id,
                "h": plugin_auth.token_digest(token),
                "m": body.exec_mode,
                "v": body.version,
                "by": user.id,
            },
        )
    ).scalar_one()
    # after 快照只记「什么档」——**不含 token 明文，也不含 hash**（纪律 1）。
    await AuditWriter.for_user(session, user, request).log(
        "plugin_instance.issue",
        "plugin_instance",
        instance_id,
        after={"exec_mode": body.exec_mode, "version": body.version},
    )
    return {"id": int(instance_id), "token": token}


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
