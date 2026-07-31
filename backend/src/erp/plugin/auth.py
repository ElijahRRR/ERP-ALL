"""插件实例令牌的散列链 + 双头部认证域（R2-13 13a；13b 先落了签发侧要用的两个原语）。

## 为什么走散列链而不是加密链

仓内有两条凭证链，用途相反：

| 链 | 代表 | 存的是什么 | 用途 |
|---|---|---|---|
| **散列** | 本文件、`scrape/service.py:37` | 只存 sha256 | 我方要拿来**验**的凭证 |
| 加密 | `channel/service.py` 的 `pgp_sym_encrypt` | 可解回明文 | 我方要拿去**用**的凭证 |

插件令牌属前者——ERP 只需判断「来者是不是它」，永远不需要把它取回来用。
图纸 `07:280` 也写的是 `token_hash`。**任何时候都不要把这一列改成可解密存储**，
那会让一次库泄漏直接等于「以买家身份下单」的能力泄漏。

## 明文的生命周期

`mint_token()` 生成 → 写入库的只有 `token_digest()` 的结果 → 明文**只在签发端点的
响应体里出现一次**，此后 ERP 侧任何地方（列表端点、审计快照、日志）都取不到。
遗失只能吊销后重新签发。这条纪律的落点在 `order/buyer_account.py::issue_plugin_instance`。

## 载体 = 双头部，对齐仓内唯一非 JWT 机器面先例

`X-Plugin-Instance`（`plugin_instance.id` 的十进制串）+ `X-Plugin-Token`（明文令牌），
形状同 `scrape/router.py::_node_auth` 的 `X-Node-Key` + `X-Node-Token`。三条不选：

- **不引入 `instance_key` 列**：签发方就是 ERP，`id` 即可索引；id 不是秘密，秘密只有 token。
- **不用 query 参数带 token**：会进 access log / 浏览器历史 / Referer，属安全边界放宽。
- **不复用 `Authorization: Bearer`**：与 `bearerAuth`/`portalAuth` 同头不同域，
  中间件按 audience 分流容易误配——同一把头四种含义是给将来埋雷。

## 失败一律同一个错误码（不区分原因）

不存在的 id / 错 token / 已吊销 → 全部 `PLUGIN_AUTH` + 401。不泄露「这个实例存不存在、
是不是已经被吊销了」——这两条信息对合法插件毫无用处，对探测者却是地图。
**同码还不够，三条路的工作量也要一样**：短路掉散列比较会让「id 不存在」比「令牌不对」
快一截，那条承诺就从响应体漏到了响应时间上（审查 B6，落点见 `_DUMMY_TOKEN_HASH`）。
**剩余项（#50 首轮点出，如实记）**：抹平的只是散列比较那一段；「行存在 / 不存在」的
数据库查询本身没有抹平，其耗时差通常比一次 sha256 大一个量级。真关死要恒定响应时间，
代价不值——所以这条承诺的准确读法是「大幅收窄」而非「已经关死」。令牌 256 位熵，
枚举出活跃 id 也无从爆破，残余风险可接受。

## 认证只看 `plugin_instance.status`，**不看 `buyer_account.status`**

这条是刻意的，不是漏了：账号被 `paused`/`blocked` 时**拉任务必须停**（那道闸在
`order/dispatch.py::claim_tasks_for_account` 第①步），但**回填 / 异常 / 物流三条写路径
必须照常通**——钱可能已经花出去了，账号被停就收不到回填 = 花了钱系统不知道。
把账号状态提到认证层就等于把这三条写路径一并掐掉，那是比「账号被风控」严重得多的故障。
"""

import hashlib
import hmac
import secrets
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from erp.core.errors import BusinessError

# bigint 上界：`X-Plugin-Instance` 是外部输入，超界的十进制串若直接进 SQL 会让
# Postgres 抛 NumericValueOutOfRange（500，且整事务作废）。认证失败本就该是 401。
_MAX_INSTANCE_ID = 2**63 - 1

_INSTANCE_SQL = """
SELECT pi.id, pi.team_id, pi.buyer_account_id, pi.token_hash, pi.status, pi.exec_mode,
       ba.site, ba.status AS account_status, ba.external_customer_id
  FROM app.plugin_instance pi
  JOIN app.buyer_account ba ON ba.id = pi.buyer_account_id
 WHERE pi.id = :i
"""


def token_digest(token: str) -> str:
    """令牌 → sha256 十六进制串（入库形态）。与 `scrape/service.py::_token_digest` 同算法。"""
    return hashlib.sha256(token.encode()).hexdigest()


def mint_token() -> str:
    """生成一个新的实例令牌明文（32 字节熵，URL 安全）。同 `worker_node` 注册。"""
    return secrets.token_urlsafe(32)


# 陪跑用的固定假散列（审查 B6）。行不存在 / 已吊销这两条路原先**短路返回**，
# 于是它们比「实例存在但令牌不对」少做一次 sha256 + 一次比较——那点时间差恰好把
# 模块头注承诺不泄露的「这个实例存不存在、是不是已被吊销」变成一个可测的时序信道，
# 探测者只要拿一把随便的 token 扫 id 就能画出实例地图。让三条失败路径做同样的活。
# **它本身不是秘密**：值域是 sha256 的输出，与任何真令牌的散列都不相等（`mint_token`
# 出的是 32 字节随机串，撞上这一把的概率是 2^-256 量级）。
_DUMMY_TOKEN_HASH = hashlib.sha256(b"plugin-auth-dummy-never-a-real-token").hexdigest()


@dataclass(frozen=True)
class PluginPrincipal:
    """一次插件请求的授权主体。**授权只来自这里**，请求体自称的身份一律不作数。

    `external_customer_id` 放进来是为了做一致性校验（插件每次调用都带 `customerId`），
    **它永远不是授权依据**——授权链是 `token → plugin_instance → buyer_account`，
    把 `customerId` 当依据等于让调用方自选身份，验收③当场打穿。
    """

    instance_id: int
    team_id: int
    buyer_account_id: int
    exec_mode: str  # dry_run | stop_before_payment | live
    account_site: str  # amazon_com | amazon_ca | amazon_co_jp
    account_status: str  # active | paused | blocked | retired
    external_customer_id: str


def auth_failed() -> BusinessError:
    """认证失败的唯一出口——三种原因同码同状态（见模块头注）。"""
    return BusinessError("PLUGIN_AUTH", "插件实例认证失败", http_status=401)


async def authenticate_instance(
    session: AsyncSession, instance_id_raw: str, token: str
) -> PluginPrincipal:
    """双头部校验：按 id 取行 → `hmac.compare_digest` 比散列。

    本函数跑在 `system_tx` 下（认证阶段还不知道团队，取不到 RLS 上下文），这是它
    **唯一**被允许绕 RLS 的理由；拿到 principal 之后所有业务查询都必须换成
    `ctx_tx(team_id=principal.team_id)`（`plugin/router.py` 的形状）。
    """
    try:
        instance_id = int(instance_id_raw)
    except (TypeError, ValueError):
        raise auth_failed() from None
    if not 0 < instance_id <= _MAX_INSTANCE_ID:
        raise auth_failed()
    row = (await session.execute(text(_INSTANCE_SQL), {"i": instance_id})).mappings().one_or_none()
    # 比较前先散列：明文 token 可能含非 ASCII，直接进 compare_digest 会抛 TypeError；
    # 而两侧都是十六进制串时它才是恒定时比较。
    #
    # **散列 + 比较一定要跑**，哪怕行不存在或已吊销（审查 B6）——`or` 的短路会让那两条
    # 路提前返回，快出一次 sha256 的时间，等于把「实例存不存在」按毫秒播了出去。
    # 故先无条件算完 `token_ok`（拿不到真散列就跟固定假散列比），再统一判定。
    stored = str(row["token_hash"]) if row is not None else _DUMMY_TOKEN_HASH
    token_ok = hmac.compare_digest(stored, token_digest(token))
    if row is None or row["status"] != "active" or not token_ok:
        raise auth_failed()
    return PluginPrincipal(
        instance_id=int(row["id"]),
        team_id=int(row["team_id"]),
        buyer_account_id=int(row["buyer_account_id"]),
        exec_mode=str(row["exec_mode"]),
        account_site=str(row["site"]),
        account_status=str(row["account_status"]),
        external_customer_id=str(row["external_customer_id"]),
    )
