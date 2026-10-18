"""美股股债利差包：百分位与信号的单元测试。

重点验证两件事：
1. 百分位公式（闭区间、0-100、min_obs 之前为 NaN）；
2. **无前视偏差**：截断未来数据后，历史时点的百分位必须完全不变。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepquant.research.equity_bond_spread_us.indicators import (
    add_percentiles,
    historical_percentile,
    zscore_expanding,
)
from deepquant.research.equity_bond_spread_us.signals import (
    decile_bucket,
    merge_events,
    threshold_flag,
)


def _series(values) -> pd.Series:
    idx = pd.date_range("2000-01-31", periods=len(values), freq="ME")
    return pd.Series(values, index=idx, name="x")


def test_percentile_closed_interval():
    s = _series([1.0, 2.0, 3.0])
    pct = historical_percentile(s, min_obs=1, expanding=True)
    assert pct.iloc[0] == pytest.approx(100.0)
    assert pct.iloc[1] == pytest.approx(100.0)
    assert pct.iloc[2] == pytest.approx(100.0)


def test_percentile_middle_value():
    s = _series([1.0, 3.0, 2.0, 5.0])
    pct = historical_percentile(s, min_obs=1, expanding=True)
    # 第 3 个点 2.0：历史 [1,3,2] 中 <=2 的有 2 个 → 66.67
    assert pct.iloc[2] == pytest.approx(200.0 / 3.0)


def test_percentile_min_obs_nan():
    s = _series([1.0, 2.0, 3.0, 4.0])
    pct = historical_percentile(s, min_obs=3, expanding=True)
    assert pct.iloc[:2].isna().all()
    assert not np.isnan(pct.iloc[2])


def test_percentile_no_lookahead():
    rng = np.random.default_rng(0)
    vals = rng.normal(size=60).cumsum()
    s = _series(vals)
    full = historical_percentile(s, min_obs=12, expanding=True)
    cut = historical_percentile(s.iloc[:40], min_obs=12, expanding=True)
    pd.testing.assert_series_equal(full.iloc[:40], cut)


def test_rolling_percentile_only_uses_window():
    vals = [0.0] * 24 + [10.0]
    s = _series(vals)
    pct = historical_percentile(s, window=12, min_obs=12, expanding=False)
    assert pct.iloc[-1] == pytest.approx(100.0)
    assert pct.iloc[:11].isna().all()


def test_zscore_expanding_uses_past_only():
    s = _series(list(range(1, 40)))
    z = zscore_expanding(s, min_obs=12)
    assert z.iloc[:11].isna().all()
    assert z.iloc[-1] > 0


def test_add_percentiles_requires_column():
    df = pd.DataFrame({"a": [1.0, 2.0]}, index=pd.date_range("2020-01-31", periods=2, freq="ME"))
    with pytest.raises(KeyError):
        add_percentiles(df, columns=("missing",))


def test_threshold_flag_nan_is_false():
    pct = _series([95.0, np.nan, 89.9, 90.0])
    flag = threshold_flag(pct, 90.0)
    assert flag.tolist() == [True, False, False, True]


def test_threshold_flag_rejects_bad_threshold():
    pct = _series([50.0, 60.0])
    with pytest.raises(ValueError):
        threshold_flag(pct, 0.0)


def test_merge_events_groups_close_trigger_months():
    idx = pd.date_range("2000-01-31", periods=10, freq="ME")
    flag = pd.Series([False, True, True, False, False, True, False, False, False, False], index=idx)
    values = pd.Series(np.arange(10.0), index=idx)
    pct = pd.Series(np.arange(10.0) * 10, index=idx)
    events = merge_events(flag, values, pct, gap=3, exec_lag=1)
    # 第 2、3 个月连续触发；第 6 个月距第 3 个月间隔 3 → 合并为同一事件
    assert len(events) == 1
    assert events[0].signal_month == "2000-02-29"
    assert events[0].last_signal_month == "2000-06-30"
    assert events[0].n_months == 3
    assert events[0].exec_month == "2000-03-31"


def test_merge_events_splits_when_gap_exceeded():
    idx = pd.date_range("2000-01-31", periods=12, freq="ME")
    flag = pd.Series(False, index=idx)
    flag.iloc[1] = True
    flag.iloc[9] = True
    values = pd.Series(np.arange(12.0), index=idx)
    pct = pd.Series(np.arange(12.0) * 10, index=idx)
    events = merge_events(flag, values, pct, gap=4, exec_lag=1)
    assert len(events) == 2
    assert events[1].signal_month == "2000-10-31"


def test_merge_events_empty():
    idx = pd.date_range("2000-01-31", periods=5, freq="ME")
    flag = pd.Series(False, index=idx)
    assert merge_events(flag, flag.astype(float), flag.astype(float)) == []


def test_merge_events_rejects_same_month_execution():
    idx = pd.date_range("2000-01-31", periods=5, freq="ME")
    flag = pd.Series(False, index=idx)
    with pytest.raises(ValueError):
        merge_events(flag, flag.astype(float), flag.astype(float), exec_lag=0)


def test_decile_bucket_boundaries():
    pct = pd.Series([0.0, 9.99, 10.0, 99.0, 100.0, np.nan])
    b = decile_bucket(pct)
    assert b.iloc[0] == 0
    assert b.iloc[1] == 0
    assert b.iloc[2] == 1
    assert b.iloc[3] == 9
    assert b.iloc[4] == 9
    assert np.isnan(b.iloc[5])
