"""渠道原文解析（R2-13 13d）：金额 / 日期 / 轨迹事件时间 / 邮编比对。

插件送来的一切都是**结账页与订单页的 DOM 文本**——`"$1,234.56"`、`"Thursday, August 7"`、
`"July 28, 2026" + "8:42 AM"`。本文件是这些原文进入 `NUMERIC`/`DATE`/`timestamptz`
之前的唯一关口。

## 一条贯穿全文的纪律：**空值与解析失败是两件事，绝不能都写成 0/NULL 了事**

| 输入 | 含义 | 处置 |
|---|---|---|
| `None` / `""` | 渠道**没给**（结账页就没这一项） | 返回 `None`，列留 NULL |
| `"abc"` / `"$"` | 渠道给了、**我们读错了** | 抛 `MoneyParseError`，调用方落 `other` + 告警 |
| `"-12.34"` | 抓到了负数（多半是抓错行，如「优惠 -$5.00」） | 同上——见 `_non_negative`（审查 B5） |

**绝不返回 0**（图纸 `07:150-153` 点名的那处坑）：静默写 0 会在利润核算里埋一个永远查不
出来的错——一笔 `$1,234.56` 的采购成本记成 0，报表上只会显示「这单利润特别好」。
把它变成一条告警，人就能在当天看见。

## 日期与时间解析失败**不抛**，只回 None

金额与日期的失效代价不对称：金额错 = 账目错（必须打断并告警）；日期错 = 少一个可排序
的字段，而**原文照存**（`delivery_est_raw` / `raw_day` / `raw_time`），任何时候都能
事后重解析。为一个可回溯的字段打断整笔回填是本末倒置——那笔钱已经花出去了。

## 轨迹时间没有时区，按 UTC 收口

Amazon 轨迹页的 `day`/`time` 是**投递地当地时间**且不带时区标识。此处一律按 UTC 组装
`occurred_at`（误差 ≤ 一天，够用于排序与「最近一次事件是什么时候」），**原文两列同时
入库**，将来若要按承运商时区重解释，素材是全的。不要在这里猜时区。
"""

import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

import structlog

log = structlog.get_logger()


class MoneyParseError(ValueError):
    """金额有原文但解析不出来。**调用方必须落异常并告警，不得吞掉当 0 处理。**"""


# 允许的形状：可选负号 + 数字/千分位 + 可选小数。货币符号与空白先剥掉。
_MONEY_RE = re.compile(r"^-?\d{1,3}(?:,\d{3})*(?:\.\d+)?$|^-?\d+(?:\.\d+)?$")
# 结账页可能出现的货币前缀（美/加/日三站）。剥符号而不是白名单校验币种——
# 币种由 `purchase_currency` 列承载（美亚恒 USD），此处只负责把数字取出来。
_CURRENCY_CHARS = "$￥¥€£"

_MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)  # fmt: skip
# 12 小时制的折返点。写成常量是为了让 `hour < _NOON` / `hour == _NOON` 这两句自解释：
# 下午 1~11 点要 +12，而 12:xx AM 是零点、12:xx PM 就是 12 点，两头都不能按通式算。
_NOON = 12

# `July 28, 2026` / `July 28` / `Thursday, August 7`（星期前缀可有可无、年份可缺）
_DAY_RE = re.compile(
    r"(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})(?:\s*,\s*(?P<year>\d{4}))?",
)
# `8:42 AM` / `11:15 PM` / `08:42`
_TIME_RE = re.compile(r"(?P<h>\d{1,2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?\s*(?P<ampm>[AaPp][Mm])?")


def _non_negative(value: Decimal, raw: str | float) -> Decimal:
    """负金额一律当解析失败抛（审查 B5）。

    本文件解析的三项是**采购成本 / 税 / 运费**，业务上都非负。负值只可能来自两件事：
    结账页上抓错了行（抓到「优惠 -$5.00」那一行），或渠道文案换了版式。两者都是
    「渠道给了、我们读错了」——正是 `MoneyParseError` 的定义域，该走 `other` + 告警
    让人当天看见，而不是静静入库。

    **静默放行的代价与写 0 同级、方向相反**：一笔 `-1234.56` 的成本会让利润核算凭空
    多出两倍利润；而 `purchase_cost` 列上没有 CHECK 拦它（0025 只是 `numeric(12,2)`），
    库这一层不会替我们兜底。
    """
    if value < 0:
        raise MoneyParseError(f"金额为负（成本/税/运费不可为负）：{raw!r}")
    return value


def parse_money(raw: str | float | None) -> Decimal | None:
    """`"$1,234.56"` → `Decimal("1234.56")`；`""`/`None` → `None`；读不懂或为负 → 抛。

    数字类型（插件的 `unitPrice` 就是数字）直接转，不绕字符串——`float` 转 `Decimal`
    走 `str()` 而不是 `Decimal(float)`，后者会把 `9.99` 变成 `9.9900000000000002131628…`。
    """
    if raw is None:
        return None
    if isinstance(raw, bool):  # bool 是 int 的子类，别让 True 变成 1.00
        raise MoneyParseError(f"金额字段收到布尔值：{raw!r}")
    if isinstance(raw, int | float):
        try:
            return _non_negative(Decimal(str(raw)), raw)
        except InvalidOperation as exc:  # pragma: no cover - float 已是有限数才走到这
            raise MoneyParseError(f"金额数值无法转换：{raw!r}") from exc

    # **空判在剥符号之前**：`""` 是「渠道没给」，而 `"$"` 是「渠道给了但我们没读到数字」
    # ——先剥符号会把后者也变成空串，于是一次抓取失败被当成「本来就没有」静默放过。
    text = raw.replace("\xa0", " ").strip()
    if not text:
        return None
    for ch in _CURRENCY_CHARS:
        text = text.replace(ch, "")
    # 内部空白**不删**：`"9 99"` 删空格会变成 999——凭空多出 990 块的采购成本。
    text = text.strip()
    if not _MONEY_RE.match(text):
        raise MoneyParseError(f"金额原文无法解析：{raw!r}")
    try:
        value = Decimal(text.replace(",", ""))
    except InvalidOperation as exc:
        raise MoneyParseError(f"金额原文无法解析：{raw!r}") from exc
    # 负号仍留在 `_MONEY_RE` 里：先认出「这是个数、且是负的」，再按负值抛——
    # 那样错误信息说的是真因（抓到了负数），而不是含糊的「无法解析」。
    return _non_negative(value, raw)


def parse_iso_date(raw: str | None) -> date | None:
    """`"2026-08-07"` → `date`。插件的 `deliveryTime` 已是格式化好的 ISO 日期。

    **解析不出只回 None 不抛**：原文另存 `delivery_est_raw`，信息没丢（见模块头注）。
    """
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        log.warning("plugin.parse.bad_delivery_date", raw=raw)
        return None


def parse_event_time(day: str | None, clock: str | None) -> datetime | None:
    """`("July 28, 2026", "8:42 AM")` → `datetime(2026, 7, 28, 8, 42, tzinfo=UTC)`。

    - 缺年份（一手样例里出现过 `"July 28"`）→ 取**当前年**。轨迹是近期事实，跨年边界
      最多偏一年，而没有 `occurred_at` 的事件连排序都做不了；原文两列仍在，可回溯。
    - 缺时间 → 当日零点。缺日期 → `None`（只有时间的事件没法定位到哪一天）。
    - 一律 UTC（渠道原文无时区，见模块头注）。
    """
    if not day or not day.strip():
        return None
    m = _DAY_RE.search(day)
    if m is None:
        log.warning("plugin.parse.bad_event_day", day=day)
        return None
    month_name = m.group("month").lower()
    month = next((i + 1 for i, name in enumerate(_MONTHS) if name.startswith(month_name[:3])), None)
    if month is None:
        log.warning("plugin.parse.bad_event_month", day=day)
        return None
    year = int(m.group("year")) if m.group("year") else datetime.now(UTC).year

    hour = minute = second = 0
    if clock and clock.strip():
        t = _TIME_RE.search(clock)
        if t is not None:
            hour, minute = int(t.group("h")), int(t.group("m"))
            second = int(t.group("s") or 0)
            ampm = (t.group("ampm") or "").lower()
            if ampm == "pm" and hour < _NOON:
                hour += _NOON
            elif ampm == "am" and hour == _NOON:
                hour = 0
    try:
        return datetime(year, month, int(m.group("day")), hour, minute, second, tzinfo=UTC)
    except ValueError:
        log.warning("plugin.parse.bad_event_time", day=day, clock=clock)
        return None


def join_postcode(main: str | None, ext: str | None) -> str | None:
    """`("36759", "0000")` → `"36759-0000"`；扩展位为空则只回主段。"""
    head = (main or "").strip()
    tail = (ext or "").strip()
    if not head:
        return None
    return f"{head}-{tail}" if tail else head


def postcode_matches(left: str | None, right: str | None) -> bool:
    """邮编比对：**只比主段的数字**，`36759` 与 `36759-0000` 视为相同。

    不这么做会造出一整批假阳异常——渠道那侧的 zip+4 是否带扩展位取决于是谁录的地址，
    而「地址不符」这类异常必须让人相信它，一旦掺了假阳，真的那条就没人看了。
    加拿大/日本邮编含字母，故取「首个分隔符前的字母数字」而不是纯数字。
    """

    def head(value: str | None) -> str:
        raw = (value or "").strip().upper()
        first = re.split(r"[-\s]", raw, maxsplit=1)[0]
        return re.sub(r"[^0-9A-Z]", "", first)

    a, b = head(left), head(right)
    return bool(a) and a == b


def split_asins(raw: str | None) -> list[str]:
    """`"B0F8NB1T8Y,B0XXXX"` → `["B0F8NB1T8Y", "B0XXXX"]`（去空白、去空项，保序）。"""
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]
