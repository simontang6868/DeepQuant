"""Step 8.3' 信号评价指标的边界与异常分支测试（纯加法）。

覆盖 forward_returns / signal_density / signal_gaps / evaluate_signals 的
异常与边界分支（空序列、纯 HOLD、tail 剔除、索引校验等）。
不修改任何既有测试。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.analysis.signal_metrics import (
    evaluate_signals,
    forward_returns,
    signal_density,
    signal_gaps,
)


def _days(*labels: str) -> pd.DatetimeIndex:
    return pd.DatetimeIndex([pd.Timestamp(d) for d in labels])


def _sig(values, days):
    return pd.Series(values, index=_days(*days), dtype="int8")


# ------------------------------------------------ forward_returns


def test_forward_returns_rejects_zero_horizon():
    with pytest.raises(ValueError, match="horizon"):
        forward_returns(pd.Series([1.0, 2.0, 3.0]), 0)


def test_forward_returns_rejects_negative_horizon():
    with pytest.raises(ValueError, match="horizon"):
        forward_returns(pd.Series([1.0, 2.0]), -1)


# ------------------------------------------------ signal_density


def test_signal_density_empty_is_zero():
    assert signal_density(pd.Series([], dtype="int8")) == 0.0


def test_signal_density_only_hold_is_zero():
    assert signal_density(_sig([0, 0, 0], ("2024-01-02", "2024-01-03", "2024-01-04"))) == 0.0


# ------------------------------------------------ signal_gaps


def test_signal_gaps_less_than_two_signals_empty():
    assert signal_gaps(_sig([0, 0], ("2024-01-02", "2024-01-03"))) == []
    assert signal_gaps(_sig([1, 0, 0], ("2024-01-02", "2024-01-03", "2024-01-04"))) == []


def test_signal_gaps_counts_interval():
    gaps = signal_gaps(
        _sig([1, 0, 0, -1], ("2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"))
    )
    assert gaps == [2]


# ------------------------------------------------ evaluate_signals 异常


def test_evaluate_rejects_bad_horizon():
    sig = _sig([1, 0], ("2024-01-02", "2024-01-03"))
    close = pd.Series([10.0, 11.0], index=sig.index)
    with pytest.raises(ValueError, match="horizon"):
        evaluate_signals(sig, close, source="ma", horizon=0)


def test_evaluate_rejects_empty_signals():
    close = pd.Series([10.0], index=_days("2024-01-02"))
    with pytest.raises(ValueError, match="空"):
        evaluate_signals(pd.Series([], dtype="int8"), close, source="ma")


def test_evaluate_rejects_index_mismatch():
    sig = _sig([1, 0], ("2024-01-02", "2024-01-03"))
    close = pd.Series([10.0, 11.0], index=_days("2024-01-02", "2024-01-09"))
    with pytest.raises(ValueError, match="一致"):
        evaluate_signals(sig, close, source="ma")


# ------------------------------------------------ evaluate_signals 边界


def test_evaluate_all_hold_has_no_signals():
    sig = _sig([0, 0, 0], ("2024-01-02", "2024-01-03", "2024-01-04"))
    close = pd.Series([10.0, 11.0, 12.0], index=sig.index)
    rep = evaluate_signals(sig, close, source="ma", horizon=1)
    assert rep.n_signals == 0
    assert rep.n_buy == 0 and rep.n_sell == 0
    assert rep.buy_hit_rate is None
    assert rep.sell_hit_rate is None
    assert rep.overall_hit_rate is None
    assert rep.mean_gap_days is None
    assert rep.max_gap_days is None


def test_evaluate_excluded_tail_reported():
    # 末日买入信号无未来数据 → 进 excluded_tail，不进评估
    sig = _sig([1, 0], ("2024-01-02", "2024-01-03"))
    close = pd.Series([10.0, 11.0], index=sig.index)
    rep = evaluate_signals(sig, close, source="ma", horizon=5)
    assert rep.excluded_tail == 1
    assert rep.n_evaluated == 0
    assert rep.buy_hit_rate is None


def test_evaluate_buy_hit_rate_direction():
    # 01-02 买入、次日涨 → 命中；01-04 买入、次日跌 → 不命中
    sig = _sig([1, 0, 1, 0], ("2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"))
    close = pd.Series([10.0, 11.0, 10.0, 9.0], index=sig.index)
    rep = evaluate_signals(sig, close, source="ma", horizon=1)
    assert rep.n_buy == 2
    assert rep.buy_hit_rate == pytest.approx(0.5)
    # 无卖出信号 → sell_hit_rate None；两个买入间隔 1 个交易日
    assert rep.sell_hit_rate is None
    assert rep.mean_gap_days == pytest.approx(1.0)
    assert rep.max_gap_days == 1
