"""Step 8.4' 投票内核的边界与异常分支测试（纯加法）。

覆盖 validate_trading_days / validate_inputs / validate_weights /
collect_votes / decide 的异常与边界分支。不修改任何既有测试。
"""

from __future__ import annotations

import math

import pandas as pd
import pytest

from deepquant.strategies.base import Signal
from deepquant.strategies.ensemble_voting import (
    MAJORITY,
    UNANIMOUS,
    WEIGHTED,
    collect_votes,
    decide,
    validate_inputs,
    validate_trading_days,
    validate_weights,
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


# ---------------------------------------------------- validate_trading_days


def test_trading_days_rejects_non_index():
    with pytest.raises(TypeError, match="DatetimeIndex"):
        validate_trading_days(pd.Series([pd.Timestamp("2024-01-02")]))


def test_trading_days_rejects_duplicates():
    with pytest.raises(ValueError, match="升序"):
        validate_trading_days(_days("2024-01-02", "2024-01-02"))


def test_trading_days_rejects_not_monotonic():
    with pytest.raises(ValueError, match="升序"):
        validate_trading_days(_days("2024-01-03", "2024-01-02"))


def test_trading_days_passthrough_ok():
    idx = _days("2024-01-02", "2024-01-03")
    assert validate_trading_days(idx) is idx


# ---------------------------------------------------- validate_inputs


def test_inputs_rejects_non_mapping():
    with pytest.raises(TypeError, match="Mapping"):
        validate_inputs(["not-a-mapping"])


def test_inputs_rejects_empty():
    with pytest.raises(ValueError, match="不能为空"):
        validate_inputs({})


def test_inputs_rejects_empty_name():
    with pytest.raises(ValueError, match="源名称"):
        validate_inputs({"": [_ev("2024-01-02")]})


def test_inputs_rejects_non_string_name():
    with pytest.raises(ValueError, match="源名称"):
        validate_inputs({123: [_ev("2024-01-02")]})


def test_inputs_rejects_non_event_element():
    with pytest.raises(TypeError, match="SignalEvent"):
        validate_inputs({"ma": ["not-an-event"]})


def test_inputs_rejects_source_mismatch():
    with pytest.raises(ValueError, match="不一致"):
        validate_inputs({"ma": [_ev("2024-01-02", source="mr")]})


def test_inputs_returns_normalized_lists():
    cleaned = validate_inputs(
        {"ma": [_ev("2024-01-02")], "mr": [_ev("2024-01-03", source="mr")]}
    )
    assert set(cleaned) == {"ma", "mr"}
    assert all(isinstance(v, list) for v in cleaned.values())


# ---------------------------------------------------- validate_weights


def test_weights_none_defaults_to_one():
    assert validate_weights(None, ["a", "b"], WEIGHTED) == {"a": 1.0, "b": 1.0}


def test_weights_rejected_for_non_weighted():
    with pytest.raises(ValueError, match="仅对"):
        validate_weights({"a": 1.0}, ["a"], MAJORITY)


def test_weights_rejects_unknown_source():
    with pytest.raises(ValueError, match="未知源"):
        validate_weights({"x": 1.0}, ["a", "b"], WEIGHTED)


def test_weights_rejects_non_positive():
    for bad in (0.0, -1.0):
        with pytest.raises(ValueError, match="正有限"):
            validate_weights({"a": bad}, ["a"], WEIGHTED)


def test_weights_rejects_non_finite():
    with pytest.raises(ValueError, match="正有限"):
        validate_weights({"a": math.nan}, ["a"], WEIGHTED)


def test_weights_ok_with_partial():
    assert validate_weights({"a": 2.0}, ["a", "b"], WEIGHTED) == {"a": 2.0, "b": 1.0}


# ---------------------------------------------------- collect_votes


def test_collect_votes_skips_hold():
    days = _days("2024-01-02", "2024-01-03")
    positions = pd.Series(range(len(days)), index=days)
    cleaned = {"ma": [_ev("2024-01-02", Signal.HOLD), _ev("2024-01-03")]}
    votes = collect_votes(cleaned, {"ma": 1.0}, positions)
    # HOLD 不计入，只有 01-03 一条
    assert list(votes) == [pd.Timestamp("2024-01-03")]


def test_collect_votes_date_out_of_calendar():
    days = _days("2024-01-02", "2024-01-03")
    positions = pd.Series(range(len(days)), index=days)
    cleaned = {"ma": [_ev("2024-01-09")]}
    with pytest.raises(ValueError, match="不在交易日历"):
        collect_votes(cleaned, {"ma": 1.0}, positions)


def test_collect_votes_rejects_duplicate_same_day():
    days = _days("2024-01-02", "2024-01-03")
    positions = pd.Series(range(len(days)), index=days)
    cleaned = {"ma": [_ev("2024-01-02"), _ev("2024-01-02")]}
    with pytest.raises(ValueError, match="多条事件"):
        collect_votes(cleaned, {"ma": 1.0}, positions)


def test_collect_votes_weighted_contribution():
    days = _days("2024-01-02")
    positions = pd.Series(range(len(days)), index=days)
    cleaned = {"ma": [_ev("2024-01-02", Signal.SELL)]}
    votes = collect_votes(cleaned, {"ma": 2.0}, positions)
    assert votes[pd.Timestamp("2024-01-02")]["ma"] == -2.0


# ---------------------------------------------------- decide


def test_decide_below_min_sources_holds():
    d, a = decide({}, MAJORITY, 1)
    assert d == int(Signal.HOLD) and a == 0.0


def test_decide_majority_buy_with_agreement():
    d, a = decide({"a": 1.0}, MAJORITY, 1)
    assert d == int(Signal.BUY) and a == 1.0


def test_decide_tie_resolves_to_hold():
    d, a = decide({"a": 1.0, "b": -1.0}, MAJORITY, 2)
    assert d == int(Signal.HOLD) and a == 0.0


def test_decide_weighted_score_ratio():
    d, a = decide({"a": 2.0, "b": -1.0}, WEIGHTED, 2)
    assert d == int(Signal.BUY)
    assert a == pytest.approx(1.0 / 3.0)


def test_decide_unanimous_conflict_holds():
    d, a = decide({"a": 1.0, "b": -1.0}, UNANIMOUS, 2)
    assert d == int(Signal.HOLD) and a == 0.0


def test_decide_unanimous_agree_returns_one():
    d, a = decide({"a": 2.0, "b": 1.0}, UNANIMOUS, 2)
    assert d == int(Signal.BUY) and a == 1.0


def test_decide_unanimous_below_min_holds():
    d, a = decide({"a": 1.0}, UNANIMOUS, 2)
    assert d == int(Signal.HOLD) and a == 0.0
