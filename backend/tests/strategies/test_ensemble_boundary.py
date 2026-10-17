"""Step 8.4' 组合层编排的边界与异常分支测试（纯加法）。

覆盖 combine_events / combine_series 的参数校验与 EnsembleResult 属性边界。
不修改任何既有测试。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.strategies.base import Signal
from deepquant.strategies.ensemble import (
    MAJORITY,
    EnsembleResult,
    combine_events,
    combine_series,
)
from deepquant.strategies.signals import SignalEvent


def _ev(day: str, direction: Signal = Signal.BUY, source: str = "ma") -> SignalEvent:
    return SignalEvent(
        date=pd.Timestamp(day),
        direction=direction,
        strength=1.0,
        source=source,
    )


def _days(*labels: str) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(d) for d in labels])


# ------------------------------------------------ combine_events 参数校验


def test_combine_events_rejects_bad_method():
    with pytest.raises(ValueError, match="method"):
        combine_events(
            {"ma": [_ev("2024-01-02")]}, _days("2024-01-02"), method="bogus"
        )


def test_combine_events_rejects_bool_min_sources():
    with pytest.raises(TypeError, match="min_sources"):
        combine_events(
            {"ma": [_ev("2024-01-02")]}, _days("2024-01-02"), min_sources=True
        )


def test_combine_events_rejects_min_sources_lt_one():
    with pytest.raises(ValueError, match="min_sources"):
        combine_events(
            {"ma": [_ev("2024-01-02")]}, _days("2024-01-02"), min_sources=0
        )


def test_combine_events_rejects_min_sources_gt_sources():
    with pytest.raises(ValueError, match="超过"):
        combine_events(
            {"ma": [_ev("2024-01-02")]}, _days("2024-01-02"), min_sources=3
        )


# ------------------------------------------------ combine_series 参数校验


def test_combine_series_rejects_empty():
    with pytest.raises(ValueError, match="非空"):
        combine_series({})


def test_combine_series_rejects_non_series_value():
    with pytest.raises(TypeError, match="pd.Series"):
        combine_series({"ma": "not-a-series"})


def test_combine_series_rejects_index_mismatch():
    s1 = pd.Series([1, 0], index=_days("2024-01-02", "2024-01-03"))
    s2 = pd.Series([0, 1], index=_days("2024-01-02", "2024-01-04"))
    with pytest.raises(ValueError, match="索引"):
        combine_series({"ma": s1, "mr": s2})


# ------------------------------------------------ EnsembleResult 属性


def test_ensemble_result_empty_calendar_coverage_zero():
    res = EnsembleResult(
        signals=pd.Series([], dtype="int8"),
        method=MAJORITY,
    )
    assert res.n_days == 0
    assert res.n_actionable == 0
    assert res.coverage == 0.0


def test_ensemble_result_coverage_counts_actionable():
    sig = pd.Series(
        [1, 0, -1, 0],
        index=_days("2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"),
        dtype="int8",
    )
    res = EnsembleResult(signals=sig, method=MAJORITY)
    assert res.n_days == 4
    assert res.n_actionable == 2
    assert res.coverage == pytest.approx(0.5)
