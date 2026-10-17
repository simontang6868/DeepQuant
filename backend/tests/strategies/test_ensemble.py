"""Step 8.4' 多信号组合：投票语义与结果结构（纯加法，不触碰既有策略与引擎）。"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.strategies.base import Signal
from deepquant.strategies.ensemble import (
    MAJORITY,
    UNANIMOUS,
    WEIGHTED,
    EnsembleResult,
    combine_events,
)
from deepquant.strategies.signals import SignalEvent, events_from_signals


def _index(days: int = 5) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.date_range("2024-01-01", periods=days, freq="D"))


def _series(values: list[int], days: int | None = None) -> pd.Series:
    return pd.Series(values, index=_index(days or len(values)), dtype="int8")


def _inputs(**series: list[int]) -> dict[str, list[SignalEvent]]:
    """`_inputs(a=[1,0], b=[1,1])` → 各源的事件列表（按最长序列补零对齐）。"""
    out: dict[str, list[SignalEvent]] = {}
    days = max((len(v) for v in series.values()), default=0)
    for name, values in series.items():
        padded = list(values) + [0] * (days - len(values))
        out[name] = events_from_signals(_series(padded, days), source=name)
    return out


def test_two_sources_agree_yields_that_direction() -> None:
    result = combine_events(_inputs(a=[1, 0, 0, 0, 0], b=[1, 0, 0, 0, 0]), _index())
    assert int(result.signals.iloc[0]) == int(Signal.BUY)


def test_conflict_yields_hold() -> None:
    result = combine_events(_inputs(a=[1, 0, 0, 0, 0], b=[-1, 0, 0, 0, 0]), _index())
    assert int(result.signals.iloc[0]) == int(Signal.HOLD)


def test_two_against_one_takes_majority() -> None:
    result = combine_events(
        _inputs(a=[1, 0, 0, 0, 0], b=[1, 0, 0, 0, 0], c=[-1, 0, 0, 0, 0]), _index()
    )
    assert int(result.signals.iloc[0]) == int(Signal.BUY)


def test_tie_yields_hold_conservatively() -> None:
    result = combine_events(
        _inputs(
            a=[1, 0, 0, 0, 0], b=[1, 0, 0, 0, 0], c=[-1, 0, 0, 0, 0], d=[-1, 0, 0, 0, 0]
        ),
        _index(),
    )
    assert int(result.signals.iloc[0]) == int(Signal.HOLD)
    assert result.agreement.iloc[0] == 0.0


def test_absent_source_is_not_counted_as_hold() -> None:
    """缺席 ≠ 弃权票：缺席的源不计入分母。"""
    result = combine_events(
        _inputs(a=[0, 0, 0, 0, 0], b=[1, 0, 0, 0, 0]), _index(), min_sources=1
    )
    assert int(result.signals.iloc[0]) == int(Signal.BUY)
    assert result.agreement.iloc[0] == pytest.approx(1.0)


def test_unanimous_requires_all_sources_agree() -> None:
    agree = combine_events(
        _inputs(a=[1, 0, 0, 0, 0], b=[1, 0, 0, 0, 0]), _index(), method=UNANIMOUS
    )
    assert int(agree.signals.iloc[0]) == int(Signal.BUY)
    assert agree.agreement.iloc[0] == pytest.approx(1.0)


def test_unanimous_conflict_yields_hold() -> None:
    conflict = combine_events(
        _inputs(a=[1, 0, 0, 0, 0], b=[-1, 0, 0, 0, 0]), _index(), method=UNANIMOUS
    )
    assert int(conflict.signals.iloc[0]) == int(Signal.HOLD)


def test_weighted_lets_heavy_source_win() -> None:
    result = combine_events(
        _inputs(a=[1, 0, 0, 0, 0], b=[-1, 0, 0, 0, 0]),
        _index(),
        weights={"a": 3.0, "b": 1.0},
        method=WEIGHTED,
    )
    assert int(result.signals.iloc[0]) == int(Signal.BUY)


def test_weights_are_rejected_for_majority() -> None:
    with pytest.raises(ValueError, match="weights"):
        combine_events(
            _inputs(a=[1, 0, 0, 0, 0], b=[1, 0, 0, 0, 0]),
            _index(),
            weights={"a": 2.0},
            method=MAJORITY,
        )


def test_weights_are_rejected_for_unanimous() -> None:
    with pytest.raises(ValueError, match="weights"):
        combine_events(
            _inputs(a=[1, 0, 0, 0, 0], b=[1, 0, 0, 0, 0]),
            _index(),
            weights={"a": 2.0},
            method=UNANIMOUS,
        )


def test_min_sources_gates_thin_days() -> None:
    result = combine_events(
        _inputs(a=[0, 0, 0, 0, 0], b=[1, 0, 0, 0, 0]), _index(), min_sources=2
    )
    assert int(result.signals.iloc[0]) == int(Signal.HOLD)


def test_min_sources_above_source_count_raises() -> None:
    with pytest.raises(ValueError, match="源数量"):
        combine_events(
            _inputs(a=[1, 0, 0, 0, 0], b=[1, 0, 0, 0, 0]), _index(), min_sources=3
        )


def test_min_sources_zero_raises() -> None:
    with pytest.raises(ValueError, match=">= 1"):
        combine_events(_inputs(a=[1, 0, 0, 0, 0]), _index(), min_sources=0)


def test_agreement_is_vote_margin_ratio() -> None:
    """2 买 1 卖等权 → 净票 1 / 总票 3 = 1/3。"""
    result = combine_events(
        _inputs(a=[1, 0, 0, 0, 0], b=[1, 0, 0, 0, 0], c=[-1, 0, 0, 0, 0]), _index()
    )
    assert result.agreement.iloc[0] == pytest.approx(1.0 / 3.0)


def test_event_strength_equals_agreement() -> None:
    result = combine_events(
        _inputs(a=[1, 0, 0, 0, 0], b=[1, 0, 0, 0, 0], c=[-1, 0, 0, 0, 0]), _index()
    )
    assert len(result.events) == 1
    assert result.events[0].strength == pytest.approx(result.agreement.iloc[0])
    assert result.events[0].metadata["strength_meaning"] == "agreement_ratio"


def test_hold_days_are_not_in_agreement_nor_events() -> None:
    result = combine_events(_inputs(a=[0, 0, 0, 0, 0], b=[0, 0, 0, 0, 0]), _index())
    assert result.agreement.tolist() == [0.0] * 5
    assert result.events == []


def test_signals_index_and_value_domain() -> None:
    days = _index()
    result = combine_events(_inputs(a=[1, 0, -1, 0, 0]), days)
    assert result.signals.index.equals(days)
    assert set(result.signals.unique().tolist()) <= {-1, 0, 1}


def test_votes_frame_has_one_column_per_source() -> None:
    result = combine_events(_inputs(ma=[1, 0, 0, 0, 0], mr=[-1, 0, 0, 0, 0]), _index())
    assert list(result.votes.columns) == ["ma", "mr"]
    assert len(result.votes) == 5
    assert result.votes.loc[_index()[0], "ma"] == pytest.approx(1.0)
    assert result.votes.loc[_index()[0], "mr"] == pytest.approx(-1.0)


def test_source_label_is_deterministic() -> None:
    first = combine_events(_inputs(b=[1, 0, 0, 0, 0], a=[1, 0, 0, 0, 0]), _index())
    second = combine_events(_inputs(a=[1, 0, 0, 0, 0], b=[1, 0, 0, 0, 0]), _index())
    assert first.source == second.source == "ensemble[majority](a,b)"
    assert first.events[0].source == first.source


def test_coverage_and_counters() -> None:
    result = combine_events(_inputs(a=[1, 0, 0, 0, 0], b=[1, 0, 0, 0, 0]), _index())
    assert result.n_days == 5
    assert result.n_actionable == 1
    assert result.coverage == pytest.approx(0.2)


def test_event_valid_until_is_next_day() -> None:
    days = _index(2)
    result = combine_events(_inputs(a=[1, 0]), days)
    assert result.events[0].valid_until == days[1]


def test_event_valid_until_is_none_on_last_day() -> None:
    """末日信号无可执行日，与引擎「最后一个交易日的信号不生成订单」一致。"""
    result = combine_events(_inputs(a=[0, 1]), _index(2))
    assert result.events[0].valid_until is None


def test_result_type_and_method() -> None:
    result = combine_events(_inputs(a=[1, 0, 0, 0, 0]), _index())
    assert isinstance(result, EnsembleResult)
    assert result.method == MAJORITY
