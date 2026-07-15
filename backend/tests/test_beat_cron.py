"""beat cron 计算单元测试（纯函数，无 DB）。"""

from datetime import UTC, datetime

import pytest

from erp.automation.beat import next_fire


def test_next_fire_monthly_in_shanghai() -> None:
    """0004 种子 partition_maintain：每月 1 日 03:00 上海时间。"""
    after = datetime(2026, 7, 15, 4, 0, tzinfo=UTC)  # 上海 12:00
    nxt = next_fire("0 3 1 * *", "Asia/Shanghai", after)
    assert (nxt.year, nxt.month, nxt.day, nxt.hour, nxt.minute) == (2026, 8, 1, 3, 0)
    assert nxt.utcoffset() is not None  # aware，入库 timestamptz 语义正确


def test_next_fire_every_minute_strictly_after() -> None:
    after = datetime(2026, 7, 15, 12, 0, 30, tzinfo=UTC)
    nxt = next_fire("* * * * *", "UTC", after)
    assert nxt > after
    assert nxt.second == 0


def test_next_fire_bad_cron_raises() -> None:
    with pytest.raises(Exception):  # noqa: B017 —— beat 只关心"会抛"，异常类型属 cronsim 内部
        next_fire("not a cron", "UTC", datetime(2026, 7, 15, tzinfo=UTC))


def test_next_fire_bad_timezone_raises() -> None:
    with pytest.raises(Exception):  # noqa: B017
        next_fire("* * * * *", "Mars/Olympus", datetime(2026, 7, 15, tzinfo=UTC))
