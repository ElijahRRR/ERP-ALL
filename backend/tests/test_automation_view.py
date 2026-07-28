"""`view_policy` 纯函数与 `_legal_modes` 排序助手的单元判据（无 DB，增量2）。

两个补位：
- `unknown_mode` 分支（库里存了三档之外的脏串）在 DB 判据里**造不出来**——
  `ck_automation_mode` 挡着，而 openapi 与 runbook 都把该行为写成了承诺（审查 F8）。
  纯函数层不经过 DB，能直接验。
- `_legal_modes` 用 `_MODE_ORDER` 过滤 `FLOWS[...].legal_modes`，此前无判据钉住
  「过滤不丢档」——将来 `Mode` 加第四档而漏改 `_MODE_ORDER`，那一档会同时从面板
  和 PUT 白名单里**静默消失**（fail-closed 但静默，审查 F10）。
"""

import pytest

from erp.automation.router import _MODE_ORDER, _legal_modes
from erp.core.automation import (
    FLOWS,
    AutomationFlow,
    Mode,
    PolicyState,
    view_policy,
)


def test_unset_is_manual_and_not_illegal() -> None:
    v = view_policy(AutomationFlow.ORDER_BLOCK, row_mode=None, row_enabled=None)
    assert v.state is PolicyState.UNSET
    assert v.effective_mode is Mode.MANUAL
    assert v.illegal_for_flow is False and v.warn is None


def test_unknown_mode_string_falls_back_to_manual_but_keeps_bad_value() -> None:
    """脏串（绕过 ck_automation_mode 直改库才可能出现）→ manual + 原值可见。

    `stored_mode` 不置 None：置了就与 unset 混淆，而运维恰恰要看见坏值才能去修。
    """
    v = view_policy(AutomationFlow.PRICING_WATCH, row_mode="bogus", row_enabled=True)
    assert v.state is PolicyState.CONFIGURED
    assert v.stored_mode == "bogus"  # 坏值原样保留
    assert v.mode is None
    assert v.effective_mode is Mode.MANUAL
    assert v.illegal_for_flow is True and v.warn == "unknown_mode"


def test_disabled_takes_precedence_over_unknown_mode() -> None:
    """停用行即使存着脏串，warn 也是 disabled——与抽取前 resolve_mode 的早返回一致。"""
    v = view_policy(AutomationFlow.PRICING_WATCH, row_mode="bogus", row_enabled=False)
    assert v.state is PolicyState.DISABLED
    assert v.effective_mode is Mode.MANUAL
    assert v.warn == "disabled"


def test_illegal_for_gate_flow_is_effective_as_stored() -> None:
    """闸类存 semi → 原样生效（归 manual 是放松），warn=illegal_for_flow。"""
    v = view_policy(AutomationFlow.ORDER_BLOCK, row_mode="semi", row_enabled=True)
    assert v.effective_mode is Mode.SEMI
    assert v.illegal_for_flow is True and v.warn == "illegal_for_flow"


@pytest.mark.parametrize("flow", list(AutomationFlow))
def test_legal_modes_order_filter_drops_nothing(flow: AutomationFlow) -> None:
    """`_MODE_ORDER` 过滤后集合必须与 FlowSpec 完全一致——过滤只排序、不丢档。"""
    ordered = _legal_modes(flow)
    assert set(ordered) == FLOWS[flow].legal_modes
    assert len(ordered) == len(FLOWS[flow].legal_modes)
    assert ordered[0] is Mode.MANUAL  # 保守档恒在最左（误点代价不对称）


def test_mode_order_covers_every_mode() -> None:
    """`Mode` 新增档位而漏改 `_MODE_ORDER` → 本判据红（否则新档静默消失）。"""
    assert set(_MODE_ORDER) == set(Mode)
