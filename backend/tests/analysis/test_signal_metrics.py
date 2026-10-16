"""Step 8.3' 信号层评价指标测试（纯研究层，不触碰引擎）。"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.analysis.signal_metrics import (
    evaluate_signals,
    forward_returns,
    signal_density,
    signal_gaps,
)
from deepquant.strategies.base import Signal

# 11 个交易日，收盘单调递增 → 任何前瞻收益都为正
CLOSE = pd.Series(
    [10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20],
    index=pd.date_range("2024-01-01", periods=11, freq="D"),
    dtype="float64",
)


def _sig(values: list[int]) -> pd.Series:
    """按 values 长度生成同长度索引；长度 11 时与 CLOSE 索引完全一致。"""
    return pd.Series(
        values,
        index=pd.date_range("2024-01-01", periods=len(values), freq="D"),
        dtype="int8",
    )


# --------------------------- forward_returns --------------------------- #

def test_forward_returns_basic() -> None:
    fwd = forward_returns(CLOSE, horizon=2)
    assert fwd.iloc[0] == pytest.approx(12 / 10 - 1)
    assert fwd.iloc[1] == pytest.approx(13 / 11 - 1)


def test_forward_returns_tail_is_nan() -> None:
    fwd = forward_returns(CLOSE, horizon=2)
    assert pd.isna(fwd.iloc[-1])
    assert pd.isna(fwd.iloc[-2])
    assert not pd.isna(fwd.iloc[-3])


def test_forward_returns_rejects_bad_horizon() -> None:
    with pytest.raises(ValueError, match="horizon"):
        forward_returns(CLOSE, horizon=0)


# ---------------------------- density / gaps -------------------------- #

def test_density_zero_when_all_hold() -> None:
    assert signal_density(_sig([0] * 11)) == 0.0


def test_density_counts_actionable_only() -> None:
    assert signal_density(_sig([1, 0, 0, -1, 0])) == pytest.approx(2 / 5)


def test_gaps_between_signals() -> None:
    assert signal_gaps(_sig([1, 0, 0, 1, 0, 0, 0, 1])) == [2, 3]


def test_gaps_empty_when_fewer_than_two_signals() -> None:
    assert signal_gaps(_sig([0, 1, 0])) == []
    assert signal_gaps(_sig([0, 0, 0])) == []


# --------------------------- evaluate_signals -------------------------- #

def test_buy_hits_when_price_rises() -> None:
    # 收盘单调递增：买入信号全命中，卖出信号全不命中
    report = evaluate_signals(
        _sig([1, -1, 0, 0, 0, 0, 0, 0, 0, 1, 0]), CLOSE, source="t", horizon=2
    )
    assert report.buy_hit_rate == pytest.approx(1.0)
    assert report.sell_hit_rate == pytest.approx(0.0)
    assert report.n_buy == 2
    assert report.n_sell == 1


def test_tail_signals_excluded_not_filled_with_zero() -> None:
    report = evaluate_signals(
        _sig([1, -1, 0, 0, 0, 0, 0, 0, 0, 1, 0]), CLOSE, source="t", horizon=2
    )
    # 索引 9 的买入信号前瞻期不足，应被剔除而非按 0 计入
    assert report.excluded_tail == 1
    assert report.n_evaluated == 2
    assert report.n_signals == 3


def test_hit_rates_are_none_when_no_signal_of_that_side() -> None:
    report = evaluate_signals(_sig([1] + [0] * 10), CLOSE, source="t", horizon=2)
    assert report.sell_hit_rate is None
    assert report.buy_hit_rate == pytest.approx(1.0)


def test_overall_hit_rate_blends_both_sides() -> None:
    report = evaluate_signals(
        _sig([1, -1, 0, 0, 0, 0, 0, 0, 0, 1, 0]), CLOSE, source="t", horizon=2
    )
    assert report.overall_hit_rate == pytest.approx(0.5)


def test_gap_stats() -> None:
    report = evaluate_signals(
        _sig([1, 0, 0, -1, 0, 0, 0, 1, 0, 0, 0]), CLOSE, source="t", horizon=2
    )
    # 信号位置 0 / 3 / 7 → 间隔 [2, 3]
    assert report.mean_gap_days == pytest.approx(2.5)
    assert report.max_gap_days == 3


def test_misaligned_index_raises() -> None:
    other = CLOSE.copy()
    other.index = pd.date_range("2030-01-01", periods=11, freq="D")
    with pytest.raises(ValueError, match="索引"):
        evaluate_signals(_sig([1] + [0] * 10), other, source="t", horizon=2)


def test_empty_signals_raises() -> None:
    with pytest.raises(ValueError, match="为空"):
        evaluate_signals(_sig([]), pd.Series(dtype="float64"), source="t", horizon=2)


def test_notes_declare_no_lookahead_misuse() -> None:
    report = evaluate_signals(_sig([1] + [0] * 10), CLOSE, source="t", horizon=2)
    joined = " ".join(report.notes)
    assert "不得用于参数选择" in joined
    assert "R2" in joined and "R11" in joined


def test_to_dict_is_serializable() -> None:
    report = evaluate_signals(_sig([1] + [0] * 10), CLOSE, source="t", horizon=2)
    payload = report.to_dict()
    assert payload["source"] == "t"
    assert payload["horizon"] == 2
    assert isinstance(payload["notes"], list)
