"""strategies/signals.py 契约与不变量测试（A16#4）。

补充既有 ``test_signals.py`` 未覆盖的 **9 行守卫分支**（git 基线缺失：61, 63, 69, 121,
123, 189, 221, 232, 234）。既有测试覆盖了 happy path 与部分守卫，但以下契约点完全没被触达：

- ``SignalEvent``：date 必须是 ``pd.Timestamp``（TypeError）/ direction 必须 ∈ {-1,0,1}
  （ValueError）/ source 必须是非空字符串（ValueError）。
- ``events_from_signals``：signals 必须是 ``pd.Series``（TypeError）/ valid_days 必须 >= 1（ValueError）。
- ``OrderPlan``：strength 必须在 [0,1]（ValueError）。
- ``NextOpenExecutor.plan``：trading_days 必须是 ``pd.DatetimeIndex``（TypeError）/
  跳过非 actionable（HOLD）事件 / event.date 必须落在交易日历内（ValueError）。

所有断言均为**语义级**：精确校验异常类型（TypeError 与 ValueError 不容混）与消息中
点名的被违反契约（pd.Timestamp / 交易日历 / valid_days 等）。
纯加法，未改 ``src/`` 一行。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.market.rules import Side
from deepquant.strategies.base import Signal
from deepquant.strategies.signals import (
    NextOpenExecutor,
    OrderPlan,
    SignalEvent,
    events_from_signals,
)


def _index(days: int = 5) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.date_range("2024-01-01", periods=days, freq="D"))


def _signals(values: list[int]) -> pd.Series:
    return pd.Series(values, index=_index(len(values)), dtype="int8")


# ----------------------------- SignalEvent ------------------------------ #


def test_signal_event_rejects_non_timestamp_date() -> None:
    with pytest.raises(TypeError, match="pd.Timestamp"):
        SignalEvent(date="2024-01-01", direction=Signal.BUY, strength=1.0, source="s")


def test_signal_event_rejects_illegal_direction() -> None:
    ts = _index(2)[0]
    # 越界整数（非 Signal IntEnum 合法取值）；守卫必须拒绝
    with pytest.raises(ValueError, match="direction 非法"):
        SignalEvent(date=ts, direction=2, strength=1.0, source="s")


def test_signal_event_rejects_empty_source() -> None:
    ts = _index(2)[0]
    with pytest.raises(ValueError, match="source"):
        SignalEvent(date=ts, direction=Signal.BUY, strength=1.0, source="")


def test_signal_event_rejects_nonstring_source() -> None:
    ts = _index(2)[0]
    with pytest.raises(ValueError, match="source"):
        SignalEvent(date=ts, direction=Signal.BUY, strength=1.0, source=123)


# -------------------------- events_from_signals ------------------------- #


def test_events_from_signals_rejects_non_series() -> None:
    with pytest.raises(TypeError, match="pandas.Series"):
        events_from_signals([1, 0, -1], source="ma_cross")  # type: ignore[arg-type]


def test_events_from_signals_rejects_valid_days_lt_1() -> None:
    with pytest.raises(ValueError, match="valid_days"):
        events_from_signals(_signals([1, 0, 0]), source="ma_cross", valid_days=0)


# ------------------------------ OrderPlan ------------------------------- #


def test_order_plan_rejects_strength_above_one() -> None:
    idx = _index(3)
    with pytest.raises(ValueError, match="strength"):
        OrderPlan(
            signal_date=idx[0],
            planned_exec_date=idx[1],
            side=Side.BUY,
            source="ma_cross",
            strength=1.5,
        )


def test_order_plan_rejects_strength_below_zero() -> None:
    idx = _index(3)
    with pytest.raises(ValueError, match="strength"):
        OrderPlan(
            signal_date=idx[0],
            planned_exec_date=idx[1],
            side=Side.BUY,
            source="ma_cross",
            strength=-0.1,
        )


# --------------------------- NextOpenExecutor --------------------------- #


def test_executor_rejects_non_datetimeindex() -> None:
    with pytest.raises(TypeError, match="pd.DatetimeIndex"):
        NextOpenExecutor().plan([], [])  # type: ignore[arg-type]


def test_executor_skips_hold_event() -> None:
    # HOLD 事件无 side，不得产出订单计划（line 232 跳过分支）
    idx = _index(3)
    events = [SignalEvent(idx[0], Signal.HOLD, 1.0, "ma_cross")]
    assert NextOpenExecutor().plan(events, idx) == []


def test_executor_rejects_date_not_in_calendar() -> None:
    idx = _index(3)
    outside = pd.Timestamp("2099-12-31")
    events = [SignalEvent(outside, Signal.BUY, 1.0, "ma_cross")]
    with pytest.raises(ValueError, match="交易日历"):
        NextOpenExecutor().plan(events, idx)
