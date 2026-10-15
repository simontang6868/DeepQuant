"""滚动统计测试（重点验证「不使用未来数据」与「不做填充」）。"""

import numpy as np
import pandas as pd
import pytest

from deepquant.analysis.rolling import rolling_mean, rolling_std, rolling_volatility


def test_rolling_mean_needs_full_window():
    m = rolling_mean(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), 3)
    assert np.isnan(m.iloc[0]) and np.isnan(m.iloc[1])  # 窗口未满 -> NaN
    assert m.iloc[2] == pytest.approx(2.0)
    assert m.iloc[3] == pytest.approx(3.0)
    assert m.iloc[4] == pytest.approx(4.0)


def test_rolling_mean_no_future_leak():
    # 只改动最后一个观测，不应影响此前任何滚动值
    base = rolling_mean(pd.Series([1.0, 2.0, 3.0, 4.0, 5.0]), 3)
    changed = rolling_mean(pd.Series([1.0, 2.0, 3.0, 4.0, 999.0]), 3)
    pd.testing.assert_series_equal(base.iloc[:4], changed.iloc[:4])


def test_rolling_std_is_sample_std():
    sd = rolling_std(pd.Series([1.0, 2.0, 3.0, 4.0]), 2)
    assert np.isnan(sd.iloc[0])
    assert sd.iloc[1] == pytest.approx(np.std([1.0, 2.0], ddof=1))
    assert sd.iloc[2] == pytest.approx(np.std([2.0, 3.0], ddof=1))


def test_rolling_volatility_annualizes():
    returns = pd.Series([0.01, -0.01, 0.02, -0.02])
    vol = rolling_volatility(returns, 3, periods_per_year=252)
    expected = np.std([0.01, -0.01, 0.02], ddof=1) * np.sqrt(252)
    assert vol.iloc[2] == pytest.approx(expected)
    assert np.isnan(vol.iloc[0]) and np.isnan(vol.iloc[1])


def test_rolling_invalid_window():
    with pytest.raises(ValueError):
        rolling_mean(pd.Series([1.0]), 0)
    with pytest.raises(ValueError):
        rolling_std(pd.Series([1.0]), -2)
