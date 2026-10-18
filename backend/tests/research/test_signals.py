"""信号与前瞻收益测试：合并规则、执行时点、未成熟样本、最大回撤。

重点验证两条红线：
- 执行时点必须晚于信号日（禁止同日收盘价成交）
- 前瞻收益只能使用执行时点之后的数据
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepquant.research.equity_bond_spread.config import IndicatorType, ResearchConfig
from deepquant.research.equity_bond_spread.experiment import (
    _path_stats,
    forward_outcome,
    summarize_returns,
    trading_days_to_obs,
)
from deepquant.research.equity_bond_spread.signals import (
    SpreadEvent,
    extract_events,
    merge_signal_runs,
)


# ---------------------------------------------------------------- 10. 连续信号合并


def test_merge_signal_runs_contiguous():
    flag = pd.Series([0, 1, 1, 1, 0, 0, 0, 1, 0])
    assert merge_signal_runs(flag, min_gap_obs=1) == [(1, 3), (7, 7)]


def test_merge_signal_runs_small_gap_merged():
    flag = pd.Series([1, 0, 1, 0, 0, 1])
    # 位置 0 与 2 之间只有 1 个非信号观测（< min_gap_obs=2）→ 合并
    # 位置 2 与 5 之间有 2 个非信号观测（不小于 2）→ 断开
    assert merge_signal_runs(flag, min_gap_obs=2) == [(0, 2), (5, 5)]
    # min_gap_obs=1 时，间隔 1 个位置也不合并（1 不小于 1）
    assert merge_signal_runs(flag, min_gap_obs=1) == [(0, 0), (2, 2), (5, 5)]
    # 放宽到 3 则全部合并
    assert merge_signal_runs(flag, min_gap_obs=3) == [(0, 5)]


def test_merge_signal_runs_no_signal():
    assert merge_signal_runs(pd.Series([0, 0, 0]), min_gap_obs=1) == []
    assert merge_signal_runs(pd.Series([np.nan, np.nan]), min_gap_obs=1) == []


def test_merge_signal_runs_rejects_bad_input():
    with pytest.raises(TypeError):
        merge_signal_runs([1, 0, 1])  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        merge_signal_runs(pd.Series([1, 0]), min_gap_obs=0)


def test_extract_events_executes_after_signal():
    """执行时点必须晚于信号日，且 exec_price 取自执行日。"""
    idx = pd.date_range("2010-01-31", periods=8, freq="ME")
    frame = pd.DataFrame(
        {
            "signal": [1, 1, 0, 0, 0, 0, 0, 0],
            "spread_pct": [5.0, 5.5, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
            "percentile": [95.0, 96.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
            "price": [100.0, 101.0, 102.0, 103.0, 104.0, 105.0, 106.0, 107.0],
            "pe": [10.0] * 8,
            "bond_10y": [3.0] * 8,
            "history_obs": [1.0] * 8,
        },
        index=idx,
    )
    cfg = ResearchConfig(indicator=IndicatorType.B, exec_lag_obs=1)
    events = extract_events(frame, cfg)
    assert len(events) == 1  # 连续两期合并为一个事件
    ev = events[0]
    assert ev.signal_date == idx[0]
    assert ev.n_signal_obs == 2
    assert ev.exec_date == idx[1]
    assert ev.exec_price == pytest.approx(101.0)


def test_extract_events_no_execution_at_end():
    """信号落在序列末尾时没有可执行日，必须明确标记而不是假装成交。"""
    idx = pd.date_range("2010-01-31", periods=3, freq="ME")
    frame = pd.DataFrame(
        {
            "signal": [0, 0, 1],
            "spread_pct": [1.0, 1.0, 9.0],
            "percentile": [10.0, 10.0, 99.0],
            "price": [100.0, 101.0, 102.0],
            "pe": [10.0] * 3,
            "bond_10y": [3.0] * 3,
            "history_obs": [1.0] * 3,
        },
        index=idx,
    )
    events = extract_events(frame, ResearchConfig(indicator=IndicatorType.B))
    assert len(events) == 1
    assert events[0].no_execution is True
    assert events[0].exec_date is None


def test_merge_prevents_double_counting():
    """连续 12 期信号必须合并为 1 个事件，而不是 12 个独立机会。"""
    idx = pd.date_range("2010-01-31", periods=20, freq="ME")
    frame = pd.DataFrame(
        {
            "signal": [0, 0, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 0, 0, 0, 0, 0, 0],
            "spread_pct": [1.0, 1.0] + [9.0] * 12 + [1.0] * 6,
            "percentile": [10.0, 10.0] + [99.0] * 12 + [10.0] * 6,
            "price": np.arange(100.0, 120.0),
            "pe": [10.0] * 20,
            "bond_10y": [3.0] * 20,
            "history_obs": [1.0] * 20,
        },
        index=idx,
    )
    events = extract_events(frame, ResearchConfig(indicator=IndicatorType.B))
    assert len(events) == 1
    assert events[0].n_signal_obs == 12


# ---------------------------------------------------------------- 11. 前瞻收益窗口


def test_trading_days_to_obs_conversion():
    assert trading_days_to_obs(20, 12) == 1    # ≈1 个月
    assert trading_days_to_obs(60, 12) == 3
    assert trading_days_to_obs(120, 12) == 6
    assert trading_days_to_obs(250, 12) == 12
    assert trading_days_to_obs(250, 252) == 250


def test_forward_outcome_simple_gain():
    prices = pd.Series([100.0, 110.0, 121.0])
    out = forward_outcome(prices, 0, 2)
    assert out["gross_return"] == pytest.approx(0.21)
    assert out["max_drawdown"] == pytest.approx(0.0)  # 单边上涨无回撤
    assert out["max_favorable"] == pytest.approx(0.21)
    assert out["max_adverse"] == pytest.approx(0.0)
    assert out["is_immature"] is False


def test_forward_outcome_cost_applied():
    prices = pd.Series([100.0, 110.0])
    out = forward_outcome(prices, 0, 1, cost_rate=0.001)
    assert out["gross_return"] == pytest.approx(0.10)
    assert out["net_return"] == pytest.approx(1.10 * 0.999 - 1.0)


# ---------------------------------------------------------------- 12. 未成熟样本


def test_forward_outcome_immature_at_end():
    prices = pd.Series([100.0, 101.0, 102.0])
    out = forward_outcome(prices, 2, 5)
    assert out["is_immature"] is True
    assert np.isnan(out["gross_return"])
    assert out["end_date"] is None


def test_forward_outcome_rejects_bad_position():
    prices = pd.Series([100.0, 101.0])
    with pytest.raises(IndexError):
        forward_outcome(prices, 5, 1)


# ---------------------------------------------------------------- 13. 最大回撤


def test_path_stats_drawdown_and_excursions():
    seg = np.array([100.0, 120.0, 90.0, 130.0])
    st = _path_stats(seg)
    # 回撤：90/120-1 = -25%
    assert st["max_drawdown"] == pytest.approx(-0.25)
    assert st["max_favorable"] == pytest.approx(0.30)
    assert st["max_adverse"] == pytest.approx(-0.10)


def test_path_stats_flat_path():
    st = _path_stats(np.array([100.0, 100.0, 100.0]))
    assert st["max_drawdown"] == pytest.approx(0.0)
    assert st["max_favorable"] == pytest.approx(0.0)
    assert st["max_adverse"] == pytest.approx(0.0)


def test_path_stats_bad_start():
    st = _path_stats(np.array([0.0, 100.0]))
    assert np.isnan(st["max_drawdown"])


# ---------------------------------------------------------------- 汇总统计


def test_summarize_returns_basic():
    v = pd.Series([-0.1, 0.0, 0.2, 0.4])
    s = summarize_returns(v)
    assert s["n"] == 4
    assert s["mean"] == pytest.approx(0.125)
    assert s["median"] == pytest.approx(0.10)
    assert s["pct_positive"] == pytest.approx(0.5)  # 0.0 不算正收益
    assert s["min"] == pytest.approx(-0.1)


def test_summarize_returns_empty():
    s = summarize_returns(pd.Series(dtype="float64"))
    assert s["n"] == 0
    assert s["mean"] is None
    assert s["pct_positive"] is None


def test_spread_event_to_dict_is_serializable():
    ev = SpreadEvent(
        event_id=1,
        signal_date=pd.Timestamp("2010-01-31"),
        last_signal_date=pd.Timestamp("2010-02-28"),
        exec_date=pd.Timestamp("2010-03-31"),
        exec_price=101.0,
        n_signal_obs=2,
        spread_at_signal=5.0,
        spread_max_in_event=5.5,
        percentile_at_signal=95.0,
        percentile_max_in_event=96.0,
        bond_10y_at_signal=3.0,
        pe_at_signal=10.0,
        history_obs=12.0,
    )
    d = ev.to_dict()
    assert d["signal_date"] == "2010-01-31T00:00:00"
    assert d["event_id"] == 1
