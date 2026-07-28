"""`AutomationFlow` 枚举 ↔ 图纸 §09 注册清单的双向一致性门禁（R2-09 增量1）。

§09 原文写着「**本表即 `AutomationFlow` 枚举的唯一权威**——增删 flow 必须先改本表」。
这条判据就是那句话的机器化：**代码与图纸对不上即 CI 红**，不管是哪边先漂。

判据直接解析图纸的 markdown 表，不维护第二份清单——否则第三份清单又会跟前两份漂。

**故意不加的一条**：「每个 flow 必须有消费点」。`purchase_execute` 现无消费点，它归
R2-13，§09 明写是「趁 R2-09 尚未把枚举写死进代码时补入，避免上线后改枚举与消费点」，
属**有意的前置登记**。加那条判据会让它第一天就红，而红的原因不是缺陷。
"""

import re
from pathlib import Path

import pytest

from erp.core.automation import FLOWS, AutomationFlow, Evaluation, Mode

_SPEC = Path(__file__).resolve().parents[2] / "specs" / "001-domain-model" / "09-platform.md"
_MODE_TOKEN = re.compile(r"\b(manual|semi|auto)\b")
# 档位单元格必须是纯粹的斜杠清单（去掉 `**` 与括号补注之后），例如 `manual/semi/auto`。
# 这条**故意收得很紧**：同一文件里还有别的表（如建表 DDL 说明行
# `mode | NOT NULL DEFAULT 'manual' CHECK IN (manual, semi, auto)`），
# 松了就会把它当成 flow 行读进来。
_MODE_LIST = re.compile(r"[a-z/]+")


def _spec_rows() -> dict[str, tuple[frozenset[Mode], Evaluation]]:
    """解析 §09 的 flow_code 表 → {flow_code: (合法档位集, 求值语义)}。"""
    assert _SPEC.is_file(), f"找不到图纸 {_SPEC}"
    rows: dict[str, tuple[frozenset[Mode], Evaluation]] = {}
    for line in _SPEC.read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        code = cells[0].strip("`* ")
        if not re.fullmatch(r"[a-z][a-z_]+", code) or code == "flow_code":
            continue
        # **括号里的补注要先切掉**：`**manual/auto（无 semi）**` 那个 `semi` 是否定语，
        # 直接扫 token 会把它读成合法档位（本判据首版即如此，被自己抓出来）。
        head = cells[2].strip("* ").split("（")[0].split("(")[0].strip()
        if not _MODE_LIST.fullmatch(head):
            continue
        modes = frozenset(Mode(m) for m in _MODE_TOKEN.findall(head))
        if not modes:
            continue
        evaluation = Evaluation.SNAPSHOT if "快照" in cells[3] else Evaluation.REALTIME
        rows[code] = (modes, evaluation)
    return rows


def test_spec_table_is_parseable() -> None:
    """**前提自检**：解析不出行就说明表结构变了，别让下面几条静默通过。

    没有这一条，图纸表一改格式，`_spec_rows()` 返回空 dict，而「空 ⊆ 空」处处成立
    ——判据会在什么都没校验的情况下全绿。今天已在别处栽过同形的坑。
    """
    rows = _spec_rows()
    assert len(rows) >= 10, f"只从 §09 解析出 {len(rows)} 行 flow：{sorted(rows)}"


def test_enum_matches_spec_both_ways() -> None:
    """双向一致：枚举里的每条都在图纸上，图纸上的每条都在枚举里。"""
    spec = set(_spec_rows())
    code = {f.value for f in AutomationFlow}
    assert code - spec == set(), f"枚举里有图纸上没有的 flow（先改图纸）：{sorted(code - spec)}"
    assert spec - code == set(), f"图纸上有枚举里没有的 flow（补进枚举）：{sorted(spec - code)}"


@pytest.mark.parametrize("flow", list(AutomationFlow))
def test_legal_modes_match_spec(flow: AutomationFlow) -> None:
    """合法档位集逐条对齐——`order_block`/`compliance_block` 的两档不是笔误。"""
    expected, _ = _spec_rows()[flow.value]
    assert FLOWS[flow].legal_modes == expected, (
        f"{flow.value} 的合法档位与图纸不符：代码 "
        f"{sorted(m.value for m in FLOWS[flow].legal_modes)} vs 图纸 "
        f"{sorted(m.value for m in expected)}"
    )


@pytest.mark.parametrize("flow", list(AutomationFlow))
def test_evaluation_matches_spec(flow: AutomationFlow) -> None:
    """求值语义逐条对齐——快照型切档不影响在途请求，是正确行为不是缺陷。"""
    _, expected = _spec_rows()[flow.value]
    assert FLOWS[flow].evaluation == expected, (
        f"{flow.value} 的求值语义与图纸不符：代码 {FLOWS[flow].evaluation} vs 图纸 {expected}"
    )


def test_every_flow_has_a_spec_entry() -> None:
    """枚举与 `FLOWS` 表不许脱节（新增枚举忘了配 FlowSpec 会 KeyError 在运行期才炸）。"""
    missing = sorted(f.value for f in AutomationFlow if f not in FLOWS)
    assert not missing, f"这些 flow 没有 FlowSpec：{missing}"
