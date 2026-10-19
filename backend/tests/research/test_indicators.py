"""指标计算测试：滚动年化收益、盈利收益率、股债利差、历史百分位。

这些测试用**手工可核算**的合成数据验证数学本身是否正确。
合成数据只能证明程序逻辑正确，不能证明投资方法有效。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepquant.research.equity_bond_spread.config import (
    IndicatorType,
    PercentileWindow,
    ResearchConfig,
    ReturnBasis,
)
from deepquant.research.equity_bond_spread.indicators import (
    annualized_return_years,
    build_indicator_frame,
    compute_spread,
    earnings_yield,
    historical_percentile,
    rolling_annualized_return,
)


def _month_index(n: int) -> pd.DatetimeIndex:
    return pd.date_range("2010-01-31", periods=n, freq="ME")


# ---------------------------------------------------------------- 1. 滚动年化收益


def test_rolling_annualized_return_exact_two_year_compounding():
    """月频、回溯 24 个观测（=2 年）：100 → 121 的两年年化应为 10%。"""
    idx = _month_index(25)
    prices = pd.Series([100.0] + [np.nan] * 23 + [121.0], index=idx)
    prices.iloc[0] = 100.0
    prices.iloc[-1] = 121.0
    r = rolling_annualized_return(prices, 24, obs_per_year=12)
    # 只有最后一个观测有完整 24 期历史
    assert np.isnan(r.iloc[-2])
    assert r.iloc[-1] == pytest.approx(10.0, abs=1e-9)  # (121/100)^(1/2)-1 = 10%


def test_rolling_annualized_return_one_year():
    """回溯 12 个观测（=1 年）：100 → 110 应为 10%。"""
    idx = _month_index(13)
    prices = pd.Series(np.nan, index=idx, dtype="float64")
    prices.iloc[0] = 100.0
    prices.iloc[-1] = 110.0
    r = rolling_annualized_return(prices, 12, obs_per_year=12)
    assert r.iloc[-1] == pytest.approx(10.0, abs=1e-9)


def test_rolling_annualized_return_rejects_nonpositive_price():
    idx = _month_index(15)
    prices = pd.Series(np.linspace(100, 110, 15), index=idx)
    prices.iloc[5] = -1.0
    r = rolling_annualized_return(prices, 12, obs_per_year=12)
    # 以非正价格为"当前值"或"起点值"的窗口必须为 NaN
    assert np.isnan(r.iloc[5])    # 当前值为 -1
    assert np.isnan(r.iloc[17]) if len(r) > 17 else True
    # 其他窗口不受影响
    assert not np.isnan(r.iloc[-1])


def test_total_approx_adds_mean_dividend():
    """TOTAL_APPROX = 价格年化收益 + 窗口内平均股息率（近似口径）。"""
    idx = _month_index(13)
    prices = pd.Series(np.nan, index=idx, dtype="float64")
    prices.iloc[0] = 100.0
    prices.iloc[-1] = 110.0
    div = pd.Series(2.0, index=idx)  # 恒定 2%
    base = rolling_annualized_return(prices, 12, obs_per_year=12)
    tot = rolling_annualized_return(prices, 12, obs_per_year=12,
                                    basis=ReturnBasis.TOTAL_APPROX, div_yield=div)
    assert tot.iloc[-1] == pytest.approx(base.iloc[-1] + 2.0, abs=1e-6)


def test_annualized_return_years_conversion():
    assert annualized_return_years(24, 12) == pytest.approx(2.0)
    assert annualized_return_years(252, 252) == pytest.approx(1.0)


# ---------------------------------------------------------------- 2. 盈利收益率


def test_earnings_yield_basic():
    pe = pd.Series([10.0, 20.0, 25.0])
    ey = earnings_yield(pe)
    assert ey.iloc[0] == pytest.approx(10.0)
    assert ey.iloc[1] == pytest.approx(5.0)
    assert ey.iloc[2] == pytest.approx(4.0)


def test_earnings_yield_invalid_inputs_are_nan():
    """PE 为 0 / 负 / NaN / inf 时必须返回 NaN，不得取倒数当作有效信号。"""
    pe = pd.Series([0.0, -5.0, np.nan, np.inf, 12.0])
    ey = earnings_yield(pe)
    assert ey.iloc[0:4].isna().all()
    assert ey.iloc[4] == pytest.approx(100.0 / 12.0)


# ---------------------------------------------------------------- 3. 股债利差


def test_spread_b_is_earnings_yield_minus_bond():
    idx = _month_index(3)
    panel = pd.DataFrame(
        {"price": [100.0, 101.0, 102.0],
         "pe": [10.0, 20.0, 25.0],
         "pb": [1.0, 1.0, 1.0],
         "div_yield": [2.0, 2.0, 2.0],
         "bond_10y": [3.0, 3.0, 3.0]},
        index=idx,
    )
    cfg = ResearchConfig(indicator=IndicatorType.B)
    s = compute_spread(panel, cfg)
    assert s.iloc[0] == pytest.approx(10.0 - 3.0)
    assert s.iloc[1] == pytest.approx(5.0 - 3.0)
    assert s.iloc[2] == pytest.approx(4.0 - 3.0)


def test_spread_a_uses_annualized_price_return():
    """月频 13 个观测、回溯 1 年（12 个观测）：100 → 121，年化 21% − 3% = 18%。"""
    idx = _month_index(13)
    price = np.full(13, np.nan)
    price[0] = 100.0
    price[-1] = 121.0
    panel = pd.DataFrame(
        {"price": price, "pe": np.nan, "pb": np.nan,
         "div_yield": np.nan, "bond_10y": 3.0},
        index=idx,
    )
    cfg = ResearchConfig(indicator=IndicatorType.A, lookback_years=1)
    s = compute_spread(panel, cfg)
    assert s.iloc[-1] == pytest.approx(18.0, abs=1e-6)
    # 历史不足 1 年的观测必须为 NaN
    assert np.isnan(s.iloc[5])


# ---------------------------------------------------------------- 8. 历史百分位


def test_percentile_does_not_use_future_data():
    """截断序列算出的百分位，必须与全序列在该时点的百分位完全一致。"""
    rng = np.random.default_rng(20241018)
    full = pd.Series(rng.normal(size=120))
    pct_full = historical_percentile(full, min_history_obs=1)
    for cut in (30, 60, 90):
        pct_cut = historical_percentile(full.iloc[:cut], min_history_obs=1)
        pd.testing.assert_series_equal(
            pct_cut, pct_full.iloc[:cut], check_names=False
        )


def test_percentile_min_history_gate():
    s = pd.Series([1.0, 2.0, 3.0, 4.0, 5.0])
    pct = historical_percentile(s, min_history_obs=3)
    assert pct.iloc[0] is not None and np.isnan(pct.iloc[0])
    assert np.isnan(pct.iloc[1])
    assert pct.iloc[2] == pytest.approx(100.0)  # 前 3 个中最大
    assert pct.iloc[4] == pytest.approx(100.0)


def test_percentile_fixed_window_only_recent_history():
    s = pd.Series([1.0, 5.0, 5.0, 5.0, 5.0, 5.0, 2.0])
    # 固定窗口 3：最后一个值 2.0 在 [5,5,2] 中排第 1/3
    pct = historical_percentile(s, window=PercentileWindow.FIXED,
                                window_obs=3, min_history_obs=1)
    assert pct.iloc[-1] == pytest.approx(100.0 / 3.0)
    # 扩展窗口下 2.0 只高于 1.0，百分位 = 2/7
    pct_e = historical_percentile(s, window=PercentileWindow.EXPANDING,
                                  min_history_obs=1)
    assert pct_e.iloc[-1] == pytest.approx(2.0 / 7.0 * 100.0)


def test_percentile_requires_positive_min_history():
    with pytest.raises(ValueError):
        historical_percentile(pd.Series([1.0]), min_history_obs=0)


def test_percentile_fixed_window_requires_window_obs():
    with pytest.raises(ValueError):
        historical_percentile(pd.Series([1.0, 2.0]),
                              window=PercentileWindow.FIXED)


# ---------------------------------------------------------------- 9. 阈值边界


def test_threshold_boundary_is_inclusive():
    """百分位恰好等于阈值时必须触发信号（>= 语义）。"""
    idx = _month_index(6)
    panel = pd.DataFrame(
        {"price": [100.0] * 6,
         "pe": [20.0, 10.0, 10.0, 20.0, 20.0, 20.0],
         "pb": [1.0] * 6,
         "div_yield": [2.0] * 6,
         "bond_10y": [3.0] * 6},
        index=idx,
    )
    cfg = ResearchConfig(indicator=IndicatorType.B, threshold_pct=50.0,
                         min_history_obs=1)
    frame = build_indicator_frame(panel, cfg)
    # 手工核算：EY 序列 5,10,10,5,5,5；百分位 = 历史中 <= 当前值的比例
    # t0: [5] -> 100; t1: [5,10] -> 100; t2: [5,10,10] -> 100
    # t3: [5,10,10,5] -> 2/4=50; t4: 3/5=60; t5: 4/6=66.7
    assert list(frame["signal"]) == [1, 1, 1, 1, 1, 1]


def test_signal_nan_when_percentile_unavailable():
    idx = _month_index(3)
    panel = pd.DataFrame(
        {"price": [100.0] * 3, "pe": [10.0] * 3, "pb": [1.0] * 3,
         "div_yield": [2.0] * 3, "bond_10y": [3.0] * 3},
        index=idx,
    )
    cfg = ResearchConfig(indicator=IndicatorType.B, min_history_obs=10)
    frame = build_indicator_frame(panel, cfg)
    assert frame["signal"].isna().all()


def test_build_indicator_frame_has_expected_columns():
    idx = _month_index(40)
    rng = np.random.default_rng(7)
    panel = pd.DataFrame(
        {"price": 100 + np.arange(40), "pe": 10 + rng.normal(size=40),
         "pb": 1.0, "div_yield": 2.0, "bond_10y": 3.0},
        index=idx,
    )
    frame = build_indicator_frame(panel, ResearchConfig(indicator=IndicatorType.B))
    for c in ("spread_pct", "percentile", "signal", "history_obs", "earnings_yield_pct"):
        assert c in frame.columns
