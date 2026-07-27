"""CI 只读门禁②：`.agent/` 工单台账结构不变量（2026-07-26 Owner 拍板）。

**为什么要这条门禁**——2026-07-26 的台账全面核对查出两类坏账，都是人工巡检才发现的：

1. **关账回写漏档**：PR #29 标题就叫「R2-11 整单关账回写（docs-only）」，`git show --stat`
   却只有 `progress.md` + `task.md` 两个文件，**从未触碰 `review_list.json`**——致 R2-11
   已 accepted 却在评审台账里挂着 `in_progress` 达 9 天，违反铁律 2。
2. **字段被写坏没人知道**：R2-11 那条的 `acceptance` 值是 `"；改一个产品字段→..."`，
   开头一个孤立分号、随单补欠项串进了验收判据位，真正的验收判据当时只写在 `note` 里。

3. **（2026-07-27 追加）`finding` 膨胀成流水账**：R2-08 与 R2-09 连续两轮成为 rebase 冲突面，
   每次都冲在同一个 `finding` 字符串上——两边各往同一段文字尾部追加，git 只看得见「同一行都改了」。
   病根是**字段形状不对**：那里装的其实是按日期追加的时间线，该用数组。详见下方 `FINDING_CAP` 段。

这三类都能机器化判据。**关于「`status=accepted` 的 `last_checked_at` 不得早于关账 PR
合并日」那条**：PR 合并日不在检出树里（CI 拿不到），做不成硬判据；此处用可机械判定的
等价替代——日期格式合法、不得晚于今天、`accepted` 条目必须有非空 finding，
并把字段形状收敛（核对时发现 47 条竟有 8 种形状，机器无从校验）。

本文件不连库，纯读 `.agent/review_list.json`。
"""

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER = REPO_ROOT / ".agent" / "review_list.json"

REQUIRED_KEYS = {"id", "priority", "dimension", "check", "status", "evidence", "finding"}
OPTIONAL_KEYS = {"last_checked_at", "note", "acceptance", "gate", "depends_on", "updates"}

# ── `finding` 膨胀治理（2026-07-27）──
#
# 起因：R2-08 与 R2-09 连续两轮成为 rebase 冲突面，**每次都冲在同一个 `finding` 字符串上**
# ——审计侧和我各自往同一段文字尾部追加，git 只能看见「同一行都改了」。查了下数据才发现
# 病根不是「写太多」而是**字段形状不对**：`finding` 实际承载的是一条按日期追加的时间线
# （R2-09 两个日期段、RS-11 三个），却被塞在一个标量里。时间线该是数组——两边各追加一个
# 元素，天然不冲突。
#
# 治法**不是**把现存 7 条长文重写一遍：那是 7 段密集正文，手工重排的搞坏风险高于收益。
# 改用祖父条款（与 `SUPER_ONLY`、`CONTRACT_AHEAD_OF_CODE` 同一套路）：
#   · 未登记的条目：`finding` 不得超过 `FINDING_CAP`；
#   · 已登记的条目：**只准缩不准涨**——新内容一律进 `updates`，老文本原样留着；
#   · 缩到 CAP 以下就必须从表里删（防「豁免」变永久）。
# 于是即刻止血、零重写风险，迁移在下次自然touch到该条目时顺手做。
FINDING_CAP = 1200
FINDING_OVERSIZE_GRANDFATHERED: dict[str, int] = {
    "R2-08": 3998,
    "R2-12": 3550,
    "R2-11": 3143,
    "R2-07": 2434,
    "R2-09": 2404,
    "RS-04D": 1733,
}
KNOWN_UPDATE_KIND = {
    "ruling",  # 裁定：Owner 或审计侧对开放问题的结论
    "landed",  # 落笔回执：谁在哪个 commit 把它落成了什么
    "progress",  # 进展：增量完成、验收推进
    "correction",  # 更正：推翻自己此前的判断
}
KNOWN_STATUS = {
    "todo",
    "in_progress",
    "done",
    "accepted",
    "deferred",
    # 带限定语的收账态：保留但必须显式登记，防随手造新词
    "accepted-l2-ship-deferred",
    "diagnosed-hardened",
}
CLOSED_STATUS = {"done", "accepted", "accepted-l2-ship-deferred"}


@pytest.fixture(scope="module")
def entries() -> list[dict[str, Any]]:
    assert LEDGER.exists(), f"台账不存在：{LEDGER}"
    data = json.loads(LEDGER.read_text(encoding="utf-8"))
    assert isinstance(data, list) and data, "台账应是非空数组"
    return data


def test_ids_unique(entries: list[dict[str, Any]]) -> None:
    ids = [e.get("id") for e in entries]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"工单 id 重复：{dupes}"


def test_field_shape_converged(entries: list[dict[str, Any]]) -> None:
    """字段形状收敛：必填键齐、不出现未登记的键。

    核对时 47 条有 8 种形状（acceptance/gate/note 随意出现），机器无从校验；此处把
    「必填」与「可选」都登记下来，新增字段须先进这两个集合。
    """
    problems: list[str] = []
    for e in entries:
        tid = e.get("id", "<无 id>")
        if missing := REQUIRED_KEYS - e.keys():
            problems.append(f"{tid}: 缺必填键 {sorted(missing)}")
        if unknown := e.keys() - REQUIRED_KEYS - OPTIONAL_KEYS:
            problems.append(f"{tid}: 出现未登记的键 {sorted(unknown)}")
    assert not problems, "台账字段形状不合规：\n  " + "\n  ".join(problems)


def test_status_values_known(entries: list[dict[str, Any]]) -> None:
    bad = sorted({str(e.get("status")) for e in entries if e.get("status") not in KNOWN_STATUS})
    assert not bad, f"出现未登记的 status 取值（新增状态须先进 KNOWN_STATUS）：{bad}"


def _business_today() -> dt.date:
    """业务日按 Asia/Shanghai 口径，与 `channel/service.py::_business_date()` 同源。

    首版误用 `dt.date.today()`（容器本地＝UTC），2026-07-27 北京时间写台账时立刻红了：
    UTC 还是 07-26，于是把当天的条目判成「日期在未来」。台账是人在北京时区写的，
    判据必须同口径——`00-conventions §quota_usage` 已把业务日定死为 Asia/Shanghai。
    """
    return (dt.datetime.now(dt.UTC) + dt.timedelta(hours=8)).date()


def test_dates_valid_and_not_in_future(entries: list[dict[str, Any]]) -> None:
    today = _business_today()
    problems: list[str] = []
    for e in entries:
        raw = e.get("last_checked_at")
        if raw is None:
            problems.append(f"{e.get('id')}: 缺 last_checked_at")
            continue
        try:
            d = dt.date.fromisoformat(str(raw))
        except ValueError:
            problems.append(f"{e.get('id')}: last_checked_at 非 ISO 日期：{raw!r}")
            continue
        if d > today:
            problems.append(f"{e.get('id')}: last_checked_at 在未来：{raw}")
    assert not problems, "台账日期不合规：\n  " + "\n  ".join(problems)


def test_closed_entries_have_substantive_finding(entries: list[dict[str, Any]]) -> None:
    """已收账的条目必须有实质 finding——「关账了但台账没写清凭什么关」正是 PR #29 那类坏账。

    阈值刻意压得很低（10 字）：目的是抓空值与占位符，不是强求篇幅。参照 `EA-003` 的
    「12 个 R1 工单已注册（见下）」——15 字但确有实质，这类不该被判违规。
    """
    thin = [
        f"{e.get('id')}（{e.get('status')}，finding {len(str(e.get('finding') or ''))} 字）"
        for e in entries
        if e.get("status") in CLOSED_STATUS and len(str(e.get("finding") or "").strip()) < 10
    ]
    assert not thin, "已收账条目的 finding 过于单薄：\n  " + "\n  ".join(thin)


def test_text_fields_not_malformed(entries: list[dict[str, Any]]) -> None:
    """防「字段被写坏」：正文不得以标点开头、不得为空白。

    实例：R2-11 的 acceptance 曾是 `"；改一个产品字段→..."`——开头孤立分号，是随单补欠项
    串进了验收判据位造成的，人工巡检才看出来。
    """
    problems: list[str] = []
    for e in entries:
        for key in ("check", "finding", "note", "acceptance", "gate"):
            val = e.get(key)
            if val is None:
                continue
            text = str(val)
            if not text.strip():
                problems.append(f"{e.get('id')}.{key}: 空白")
            elif re.match(r"^[；;，,。.、：:）)】\]]", text.strip()):
                problems.append(f"{e.get('id')}.{key}: 以标点开头（疑似拼接串行）：{text[:30]!r}")
    assert not problems, "台账文本字段疑似被写坏：\n  " + "\n  ".join(problems)


def test_evidence_is_list_of_paths(entries: list[dict[str, Any]]) -> None:
    problems: list[str] = []
    for e in entries:
        ev = e.get("evidence")
        if not isinstance(ev, list):
            problems.append(f"{e.get('id')}: evidence 不是数组")
            continue
        for item in ev:
            if not isinstance(item, str) or not item.strip():
                problems.append(f"{e.get('id')}: evidence 含非字符串/空项：{item!r}")
    assert not problems, "台账 evidence 字段不合规：\n  " + "\n  ".join(problems)


def test_finding_not_bloated(entries: list[dict[str, Any]]) -> None:
    """`finding` 是「发现了什么」，不是流水账；时间线进 `updates`。

    祖父条款见文件头。**只准缩不准涨**——已登记条目的字数超过登记值即红，逼新内容进 `updates`。
    """
    problems: list[str] = []
    for e in entries:
        tid = str(e.get("id"))
        n = len(str(e.get("finding") or ""))
        cap = FINDING_OVERSIZE_GRANDFATHERED.get(tid)
        if cap is None:
            if n > FINDING_CAP:
                problems.append(
                    f"{tid}: finding {n} 字 > 上限 {FINDING_CAP}——"
                    "把「裁定 / 落笔回执 / 进展」这类按日期追加的内容移进 `updates` 数组"
                    "（两边各追加一个元素，天然不冲突），`finding` 只留「发现了什么」"
                )
        elif n > cap:
            problems.append(
                f"{tid}: finding {n} 字 > 祖父登记值 {cap}——已豁免的条目**只准缩不准涨**，"
                "新增内容请写进 `updates`"
            )
    assert not problems, "台账 finding 字段膨胀：\n  " + "\n  ".join(problems)


def test_grandfathered_finding_entries_still_oversize(entries: list[dict[str, Any]]) -> None:
    """反向不变量：缩到上限以下的条目必须从祖父表里删掉，否则豁免变永久。

    与 `SUPER_ONLY` 防僵尸、契约门禁的「归属工单必须未收账」是同一条道理。
    """
    by_id = {str(e.get("id")): e for e in entries}
    problems: list[str] = []
    for tid in sorted(FINDING_OVERSIZE_GRANDFATHERED):
        e = by_id.get(tid)
        if e is None:
            problems.append(f"{tid}: 在祖父表里但台账已无此工单——请从表里删除")
            continue
        n = len(str(e.get("finding") or ""))
        if n <= FINDING_CAP:
            problems.append(
                f"{tid}: finding 已缩到 {n} 字（≤ 上限 {FINDING_CAP}），"
                "迁移完成——请从 FINDING_OVERSIZE_GRANDFATHERED 删除该条"
            )
    assert not problems, "祖父表有陈旧条目：\n  " + "\n  ".join(problems)


def test_updates_shape(entries: list[dict[str, Any]]) -> None:
    """`updates` 是有结构的时间线，不是又一个自由文本兜底。"""
    today = _business_today()
    problems: list[str] = []
    for e in entries:
        tid = str(e.get("id"))
        ups = e.get("updates")
        if ups is None:
            continue
        if not isinstance(ups, list) or not ups:
            problems.append(f"{tid}: updates 应是非空数组（不写就别留空壳）")
            continue
        for idx, u in enumerate(ups):
            where = f"{tid}.updates[{idx}]"
            if not isinstance(u, dict):
                problems.append(f"{where}: 不是对象")
                continue
            if missing := {"date", "kind", "text"} - u.keys():
                problems.append(f"{where}: 缺键 {sorted(missing)}")
            if extra := u.keys() - {"date", "kind", "text"}:
                problems.append(f"{where}: 出现未登记的键 {sorted(extra)}")
            if u.get("kind") not in KNOWN_UPDATE_KIND:
                problems.append(
                    f"{where}: kind={u.get('kind')!r} 未登记（可选 {sorted(KNOWN_UPDATE_KIND)}）"
                )
            try:
                if dt.date.fromisoformat(str(u.get("date"))) > today:
                    problems.append(f"{where}: date 在未来：{u.get('date')}")
            except ValueError:
                problems.append(f"{where}: date 非 ISO 日期：{u.get('date')!r}")
            text = str(u.get("text") or "")
            if len(text.strip()) < 10:
                problems.append(f"{where}: text 过于单薄（{len(text.strip())} 字）")
            elif re.match(r"^[；;，,。.、：:）)】\]]", text.strip()):
                problems.append(f"{where}: text 以标点开头（疑似拼接串行）：{text[:30]!r}")
    assert not problems, "台账 updates 字段不合规：\n  " + "\n  ".join(problems)
