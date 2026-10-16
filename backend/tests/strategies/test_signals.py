"""Step 8.1 结构化信号层测试（纯加法，不触碰既有策略与引擎）。"""

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
    """按 values 长度生成同长度的交易日索引（避免索引与取值长度不一致）。"""
    return pd.Series(values, index=_index(len(values)), dtype="int8")


# ------------------------- events_from_signals ------------------------- #

def test_hold_is_excluded_by_default() -> None:
    events = events_from_signals(_signals([1, 0, 0, -1, 0]), source="ma_cross")
    assert [int(e.direction) for e in events] == [int(Signal.BUY), int(Signal.SELL)]


def test_include_hold_emits_hold_events() -> None:
    events = events_from_signals(
        _signals([1, 0, 0]), source="ma_cross", include_hold=True
    )
    assert len(events) == 3
    assert not events[1].is_actionable


def test_strength_defaults_to_one_for_binary_signals() -> None:
    events = events_from_signals(_signals([1, 0, -1]), source="ma_cross")
    assert all(e.strength == 1.0 for e in events)


def test_strength_can_be_a_series() -> None:
    idx = _index(3)
    strength = pd.Series([0.2, 0.5, 0.9], index=idx)
    events = events_from_signals(
        pd.Series([1, 1, -1], index=idx, dtype="int8"),
        source="mean_reversion",
        strength=strength,
        include_hold=True,
    )
    assert [e.strength for e in events] == [0.2, 0.5, 0.9]


def test_valid_until_is_next_trading_day() -> None:
    idx = _index(4)
    events = events_from_signals(
        pd.Series([1, 0, 0, 0], index=idx, dtype="int8"), source="ma_cross"
    )
    assert events[0].valid_until == idx[1]


def test_valid_until_is_none_on_last_bar() -> None:
    idx = _index(3)
    events = events_from_signals(
        pd.Series([0, 0, 1], index=idx, dtype="int8"), source="ma_cross"
    )
    assert events[0].valid_until is None


def test_invalid_direction_raises() -> None:
    with pytest.raises(ValueError, match="取值非法"):
        events_from_signals(_signals([2, 0, 0]), source="ma_cross")


def test_nan_signal_raises() -> None:
    idx = _index(3)
    with pytest.raises(ValueError, match="NaN"):
        events_from_signals(
            pd.Series([1, None, 0], index=idx, dtype="object"), source="ma_cross"
        )


def test_misaligned_strength_index_raises() -> None:
    strength = pd.Series([0.1] * 3, index=pd.date_range("2030-01-01", periods=3))
    with pytest.raises(ValueError, match="索引"):
        events_from_signals(
            _signals([1, 0, 0]), source="ma_cross", strength=strength
        )


# ------------------------------ SignalEvent ---------------------------- #

def test_signal_event_rejects_bad_strength() -> None:
    with pytest.raises(ValueError, match="strength"):
        SignalEvent(date=_index(2)[0], direction=Signal.BUY, strength=1.5, source="s")


def test_signal_event_rejects_valid_until_before_date() -> None:
    idx = _index(3)
    with pytest.raises(ValueError, match="valid_until"):
        SignalEvent(
            date=idx[2],
            direction=Signal.BUY,
            strength=1.0,
            source="s",
            valid_until=idx[0],
        )


def test_side_mapping() -> None:
    ts = _index(2)[0]
    assert SignalEvent(ts, Signal.BUY, 1.0, "s").side is Side.BUY
    assert SignalEvent(ts, Signal.SELL, 1.0, "s").side is Side.SELL
    assert SignalEvent(ts, Signal.HOLD, 1.0, "s").side is None


def test_to_dict_is_json_friendly() -> None:
    idx = _index(3)
    payload = SignalEvent(idx[0], Signal.BUY, 0.7, "ma_cross", idx[1], {"z": -2.1}).to_dict()
    assert payload["date"] == idx[0].isoformat()
    assert payload["valid_until"] == idx[1].isoformat()
    assert payload["direction"] == 1
    assert payload["metadata"] == {"z": -2.1}


# ---------------------------- OrderPlan -------------------------------- #

def test_order_plan_rejects_same_day_execution() -> None:
    idx = _index(2)
    with pytest.raises(ValueError, match="必须晚于"):
        OrderPlan(
            signal_date=idx[0],
            planned_exec_date=idx[0],
            side=Side.BUY,
            source="ma_cross",
        )


# -------------------------- NextOpenExecutor --------------------------- #

def test_executor_uses_next_trading_day() -> None:
    idx = _index(4)
    events = events_from_signals(
        pd.Series([1, 0, 0, 0], index=idx, dtype="int8"), source="ma_cross"
    )
    plans = NextOpenExecutor().plan(events, idx)
    assert len(plans) == 1
    assert plans[0].planned_exec_date == idx[1]
    assert plans[0].side is Side.BUY
    assert plans[0].quantity is None


def test_executor_skips_signal_without_executable_day() -> None:
    idx = _index(3)
    events = events_from_signals(
        pd.Series([0, 0, 1], index=idx, dtype="int8"), source="ma_cross"
    )
    assert NextOpenExecutor().plan(events, idx) == []


def test_executor_respects_valid_until() -> None:
    idx = _index(5)
    events = [
        SignalEvent(idx[0], Signal.BUY, 1.0, "s", valid_until=idx[0]),
    ]
    assert NextOpenExecutor().plan(events, idx) == []


def test_executor_rejects_unsorted_calendar() -> None:
    idx = pd.DatetimeIndex(
        pd.to_datetime(["2024-01-03", "2024-01-01", "2024-01-02"])
    )
    with pytest.raises(ValueError, match="升序"):
        NextOpenExecutor().plan([], idx)
