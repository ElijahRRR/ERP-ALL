"""`WIRED_FLOWS` 的反向判据：钉死 `resolve_mode` 的全部调用点（PR #42 审查 F1）。

`test_automation_api.py::T1` 只守一个方向——改了 `WIRED_FLOWS` 忘改测试会红；但它
声称要防的是**反方向**：接了消费点忘改 `WIRED_FLOWS`。那条路上 API 照样返回
`wired=false`，T1 照样绿，而面板对一条**已经在拦**的闸类 flow 挂「未接线（改档暂不
生效）」并跳过危险确认——运维读着假话去停用合规闸，全程无红字、CI 全绿（审查给出的
失败场景；与「ERP_ENV 从未注入而判据全绿」同形，判据在「没发生」和「发生了但我看
不见」两种情况下给同一个绿）。

本判据把锚打在**代码事实**上：`backend/src` 里 `resolve_mode(` 调用点的 census。
新增一处调用（= 新接一条 flow 的消费点）即红，逼接线 PR 当场面对 `WIRED_FLOWS`。
`aftersale/refund.py` 传的是动态 flow（`AutomationFlow(kind)`），静态推不出具体
flow——所以钉**调用点位置与数量**这个信号就够：它要拦的就是「有人新接了一处」。

同一手法的仓内先例：flow 契约门禁直接解析 §09 图纸，而不是维护第二份清单。
"""

import re
from pathlib import Path

from erp.core.automation import WIRED_FLOWS, AutomationFlow

_SRC = Path(__file__).resolve().parents[1] / "src" / "erp"

# `\b` 前是下划线时无词界 → `_resolve_mode(`（refund.py 的私有包装、gateway/client.py
# 同名不同物的 GatewayMode 解析）都不会误中；`automation.resolve_mode(` 这类属性调用
# 会中——那正是要抓的。
_CALL = re.compile(r"\bresolve_mode\(")

# 调用点 census（相对 src/erp 的 posix 路径 → 出现次数）。
# **接线 PR 动这里时必须三处一起动**：本表、`core/automation.py` 的 `WIRED_FLOWS`、
# `test_automation_api.py` T1 的 wired 集合断言——缺任何一处，面板就在说假话。
_EXPECTED = {
    "aftersale/refund.py": 1,  # refund / cancel（动态 flow）
    "order/procurement.py": 1,  # ORDER_BLOCK
}


def test_resolve_mode_call_sites_are_pinned() -> None:
    census: dict[str, int] = {}
    for py in sorted(_SRC.rglob("*.py")):
        rel = py.relative_to(_SRC).as_posix()
        if rel == "core/automation.py":  # 定义处（含 def 行），不是消费点
            continue
        n = len(_CALL.findall(py.read_text(encoding="utf-8")))
        if n:
            census[rel] = n
    assert census == _EXPECTED, (
        f"resolve_mode 调用点变了：{census} != {_EXPECTED}。新接（或挪动）消费点必须"
        "同步更新三处：本表、core/automation.py 的 WIRED_FLOWS、"
        "test_automation_api.py T1 的 wired 集合断言——否则面板对该 flow 显示假话。"
    )


def test_wired_flows_content_is_colocated_with_census() -> None:
    """`WIRED_FLOWS` 的内容钉在 census 旁边：census 红了的人在同一个文件里改两处。"""
    expected = {AutomationFlow.ORDER_BLOCK, AutomationFlow.REFUND, AutomationFlow.CANCEL}
    assert expected == WIRED_FLOWS
