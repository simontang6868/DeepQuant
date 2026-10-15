"""收益率测试。"""

import numpy as np
import pandas as pd
import pytest

from deepquant.analysis.returns import log_returns, simple_returns


def test_simple_returns_basic():
    r = simple_returns(pd.Series([100.0, 110.0, 121.0]))
    assert np.isnan(r.iloc[0])  # 首日无前值
    assert r.iloc[1] == pytest.approx(0.10)
    assert r.iloc[2] == pytest.approx(0.10)


def test_simple_returns_over_two_periods():
    r = simple_returns(pd.Series([100.0, 110.0, 121.0]), periods=2)
    assert np.isnan(r.iloc[0])
    assert np.isnan(r.iloc[1])
    assert r.iloc[2] == pytest.approx(0.21)


def test_simple_returns_does_not_fill_missing():
    # 中间价格缺失时，其后一期的收益率也应缺失（不做前向填充）
    r = simple_returns(pd.Series([100.0, np.nan, 121.0]))
    assert bool(r.iloc[1:].isna().all())


def test_log_returns_basic():
    r = log_returns(pd.Series([100.0, 110.0]))
    assert np.isnan(r.iloc[0])
    assert r.iloc[1] == pytest.approx(np.log(1.1))


def test_returns_invalid_periods():
    with pytest.raises(ValueError):
        simple_returns(pd.Series([1.0, 2.0]), periods=0)
    with pytest.raises(ValueError):
        log_returns(pd.Series([1.0, 2.0]), periods=-1)
