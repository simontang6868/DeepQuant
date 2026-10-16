"""Step 8.4' 多信号组合：输入校验与 combine_series 便捷入口。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import deepquant.strategies.ensemble as ensemble_mod
from deepquant.strategies.base import Signal
from deepquant.strategies.ensemble import WEIGHTED, combine_events, combine_series
from deepquant.strategies.signals import SignalEvent


def _index(days: int = 5) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.date_range("2024-01-01", periods=days, freq="D"))


def _series(values: list[int], days: int | None = None) -> pd.Series:
    return pd.Series(values, index=_index(days or len(values)), dtype="int8")


def test_unknown_weight_key_raises() -> None:
    days = _index(3)
    event = SignalEvent(date=days[0], direction=Signal.BUY, strength=1.0, source="a")
    with pytest.raises(ValueError, match="未知源"):
        combine_events({"a": [event]}, days, weights={"zzz": 2.0}, method=WEIGHTED)


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_non_positive_or_non_finite_weight_raises(bad: float) -> None:
    days = _index(3)
    event = SignalEvent(date=days[0], direction=Signal.BUY, strength=1.0, source="a")
    with pytest.raises(ValueError, match="正有限数"):
        combine_events({"a": [event]}, days, weights={"a": bad}, method=WEIGHTED)


def test_empty_inputs_raises() -> None:
    with pytest.raises(ValueError, match="不能为空"):
        combine_events({}, _index())


def test_invalid_method_raises() -> None:
    days = _index(3)
    event = SignalEvent(date=days[0], direction=Signal.BUY, strength=1.0, source="a")
    with pytest.raises(ValueError, match="method"):
        combine_events({"a": [event]}, days, method="magic")


def test_event_source_must_match_key() -> None:
    days = _index(3)
    orphan = SignalEvent(
        date=days[0], direction=Signal.BUY, strength=1.0, source="someone_else"
    )
    with pytest.raises(ValueError, match="不一致"):
        combine_events({"a": [orphan]}, days)


def test_event_outside_calendar_raises() -> None:
    days = _index(3)
    event = SignalEvent(
        date=pd.Timestamp("2030-01-01"), direction=Signal.BUY, strength=1.0, source="a"
    )
    with pytest.raises(ValueError, match="交易日历"):
        combine_events({"a": [event]}, days)


def test_duplicate_event_same_day_raises() -> None:
    days = _index(3)
    dupes = [
        SignalEvent(date=days[0], direction=Signal.BUY, strength=1.0, source="a"),
        SignalEvent(date=days[0], direction=Signal.SELL, strength=1.0, source="a"),
    ]
    with pytest.raises(ValueError, match="多条事件"):
        combine_events({"a": dupes}, days)


@pytest.mark.parametrize(
    "bad_days",
    [["2024-01-02", "2024-01-01"], ["2024-01-01", "2024-01-01"]],
)
def test_bad_trading_days_raises(bad_days: list[str]) -> None:
    days = pd.DatetimeIndex(pd.to_datetime(bad_days))
    event = SignalEvent(date=days[0], direction=Signal.BUY, strength=1.0, source="a")
    with pytest.raises(ValueError, match="升序且无重复"):
        combine_events({"a": [event]}, days)


def test_trading_days_type_raises() -> None:
    with pytest.raises(TypeError):
        combine_events({}, ["2024-01-01", "2024-01-02"])  # type: ignore[arg-type]


def test_non_signal_event_element_raises() -> None:
    with pytest.raises(TypeError, match="SignalEvent"):
        combine_events({"a": ["not-an-event"]}, _index())  # type: ignore[dict-item]


def test_combine_series_end_to_end() -> None:
    result = combine_series(
        {"ma": _series([1, 1, 0, -1, 0]), "mr": _series([1, -1, 1, -1, 0])}
    )
    assert result.signals.tolist() == [1, 0, 1, -1, 0]
    assert [str(e.date.date()) for e in result.events] == [
        "2024-01-01",
        "2024-01-03",
        "2024-01-04",
    ]


def test_combine_series_accepts_weights_and_method() -> None:
    result = combine_series(
        {"a": _series([1, 0, 0]), "b": _series([-1, 0, 0])},
        weights={"a": 4.0, "b": 1.0},
        method=WEIGHTED,
    )
    assert int(result.signals.iloc[0]) == int(Signal.BUY)


def test_combine_series_mismatched_index_raises() -> None:
    with pytest.raises(ValueError, match="隐式对齐"):
        combine_series({"a": _series([1, 0, 0, 0]), "b": _series([1, 0, 0])})


def test_combine_series_empty_raises() -> None:
    with pytest.raises(ValueError, match="非空"):
        combine_series({})


def test_combine_series_non_series_value_raises() -> None:
    with pytest.raises(TypeError, match="pd.Series"):
        combine_series({"a": [1, 0, 0]})  # type: ignore[dict-item]


def test_combine_series_ignores_hold_events() -> None:
    """include_hold=True 拉进来的 HOLD 事件仍不参与投票。"""
    result = combine_series(
        {"a": _series([0, 0, 0]), "b": _series([1, 0, 0])}, include_hold=True
    )
    assert int(result.signals.iloc[0]) == int(Signal.BUY)
    assert result.votes.loc[_index(3)[0], "a"] == pytest.approx(0.0)


def test_module_does_not_import_signal_metrics() -> None:
    """组合层不得依赖评价层：评价与选参必须隔离（R2 / R11）。"""
    source = Path(ensemble_mod.__file__).read_text(encoding="utf-8")
    import_lines = [
        line.strip()
        for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    assert all("signal_metrics" not in line for line in import_lines)
