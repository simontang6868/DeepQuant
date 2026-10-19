"""美股股债利差包：统计工具单元测试。

统计代码最容易「看起来对、其实错」，这里重点测三件事：
1. 前视收益的**时间对齐**（信号月 → 执行月 → 持有期）；
2. Newey-West 能否正确恢复已知系数；
3. 块自举在「强相关」和「无关」两种数据上给出方向相反的 p 值。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepquant.research.equity_bond_spread_us import stats


def _index(vals) -> pd.Series:
    idx = pd.date_range("2000-01-31", periods=len(vals), freq="ME")
    return pd.Series(vals, index=idx)


def test_forward_return_alignment():
    idx = _index([1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    fwd = stats.forward_return(idx, horizon=2, exec_lag=1)
    # t=0：从 i=1 买入、持有 2 个月 → idx[3]/idx[1] - 1 = 4/2 - 1
    assert fwd.iloc[0] == pytest.approx(1.0)
    assert fwd.iloc[-1] != fwd.iloc[-1]  # NaN


def test_forward_return_rejects_zero_lag():
    idx = _index([1.0, 2.0, 3.0])
    with pytest.raises(ValueError):
        stats.forward_return(idx, horizon=1, exec_lag=0)


def test_forward_return_is_nan_at_tail():
    idx = _index([1.0] * 10)
    fwd = stats.forward_return(idx, horizon=3, exec_lag=1)
    assert fwd.iloc[-4:].isna().all()
    assert fwd.iloc[:-4].notna().all()


def test_path_drawdown_negative_when_path_drops():
    idx = _index([1.0, 1.1, 0.9, 1.0, 1.2, 1.3])
    dd = stats.path_drawdown(idx, horizon=2, exec_lag=1)
    # t=1：区间 idx[2..4]=[0.9,1.0,1.2]，峰值 0.9 → 回撤 0
    assert dd.iloc[1] == pytest.approx(0.0)
    # t=0：区间 idx[1..3]=[1.1,0.9,1.0] → 0.9/1.1-1
    assert dd.iloc[0] == pytest.approx(0.9 / 1.1 - 1.0)


def test_nw_ols_recovers_slope():
    rng = np.random.default_rng(42)
    n = 400
    x = rng.normal(size=n)
    y = 1.5 + 2.0 * x + rng.normal(scale=0.5, size=n)
    res = stats.nw_ols(y, x, lag=1, names=["x"])
    assert res.params[1] == pytest.approx(2.0, abs=0.1)
    assert abs(res.tstat[1]) > 10
    assert 0.85 < res.r2 <= 1.0
    assert res.n == n


def test_nw_ols_widens_standard_error_under_persistent_regressor():
    """预测变量高度持续（真实的股债利差就是这样）时，NW 标准误必须大于朴素标准误。

    这正是本项目必须用 NW 的原因：估值类变量自相关接近 0.99，
    朴素 t 统计会把「少数几个独立观测」当成几百个。
    """
    rng = np.random.default_rng(7)
    n = 600
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.95 * x[i - 1] + rng.normal(scale=0.3)
    y = 1.0 + 0.5 * x + rng.normal(scale=1.0, size=n)
    res_nw = stats.nw_ols(y, x, lag=24, names=["x"])
    res_iid = stats.nw_ols(y, x, lag=0, names=["x"])
    assert res_nw.se[1] > res_iid.se[1]
    assert res_nw.n == n


def test_spearman_ic_monotone():
    x = pd.Series(np.arange(30, dtype=float))
    y = x * 2 + 1
    assert stats.spearman_ic(x, y) == pytest.approx(1.0)
    assert stats.spearman_ic(x, -y) == pytest.approx(-1.0)


def test_spearman_ic_too_short_returns_nan():
    assert np.isnan(stats.spearman_ic(pd.Series([1.0, 2.0]), pd.Series([1.0, 2.0])))


def test_block_bootstrap_detects_strong_relation():
    rng = np.random.default_rng(1)
    n = 240
    x = np.sin(np.arange(n) / 10.0)
    y = x + rng.normal(scale=0.1, size=n)
    out = stats.block_bootstrap_pvalue(x, y, stats.spearman_ic, block=12, n_boot=500, seed=3)
    assert out["p_value"] < 0.01
    assert out["n"] == n


def test_block_bootstrap_no_relation_gives_large_p():
    rng = np.random.default_rng(2)
    n = 240
    x = rng.normal(size=n)
    y = rng.normal(size=n)
    out = stats.block_bootstrap_pvalue(x, y, stats.spearman_ic, block=12, n_boot=500, seed=3)
    assert out["p_value"] > 0.05


def test_block_bootstrap_preserves_autocorrelation_scale():
    """强自相关的无关序列不应被误判为显著（这是普通置换检验会犯的错）。"""
    rng = np.random.default_rng(11)
    n = 600
    x = np.zeros(n)
    y = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.95 * x[i - 1] + rng.normal(scale=0.1)
        y[i] = 0.95 * y[i - 1] + rng.normal(scale=0.1)
    out = stats.block_bootstrap_pvalue(x, y, stats.spearman_ic, block=36, n_boot=400, seed=5)
    assert out["p_value"] > 0.01


def test_circular_block_indices_length_and_range():
    rng = np.random.default_rng(0)
    idx = stats.circular_block_indices(50, 7, rng)
    assert len(idx) == 50
    assert idx.min() >= 0 and idx.max() < 50


def test_effective_sample():
    assert stats.effective_sample(600, 60) == 10
    assert stats.effective_sample(59, 60) == 1


def test_min_detectable_ic_decreasing_in_sample():
    small = stats.min_detectable_ic(10)
    large = stats.min_detectable_ic(100)
    assert small > large > 0
    assert np.isnan(stats.min_detectable_ic(3))


def test_oos_r2_positive_when_model_beats_mean():
    y = np.array([1.0, 2.0, 3.0, 4.0] * 10)
    pred = y + 0.01
    bench = np.full_like(y, y.mean())
    assert stats.oos_r2(y, pred, bench) > 0.9


def test_oos_r2_negative_when_model_worse_than_mean():
    y = np.array([1.0, 2.0, 3.0, 4.0] * 10)
    pred = -y
    bench = np.full_like(y, y.mean())
    assert stats.oos_r2(y, pred, bench) < 0


def test_expanded_forecast_uses_only_past():
    rng = np.random.default_rng(0)
    x = pd.Series(rng.normal(size=80).cumsum())
    y = 2 * x + rng.normal(size=80)
    pred = stats.expanded_window_slope_forecast(x, y, min_train=30)
    # 改动未来值，早期预测不得变化
    x2 = x.copy()
    x2.iloc[60:] = x2.iloc[60:] * 5
    pred2 = stats.expanded_window_slope_forecast(x2, y, min_train=30)
    pd.testing.assert_series_equal(pred.iloc[:60], pred2.iloc[:60])


def test_annualized_helpers():
    # 2 年累计 21% → 年化 10%
    assert stats.annualized_return(0.21, 2.0) == pytest.approx(0.1, abs=1e-9)
    assert stats.annualized_return(-1.0, 2.0) == pytest.approx(-1.0)
    s = pd.Series([0.01, -0.02, 0.03, 0.01])
    assert stats.annualized_vol(s) > 0
    assert stats.max_drawdown(pd.Series([1.0, 1.2, 0.6, 0.9])) == pytest.approx(0.6 / 1.2 - 1.0)


def _frame(n=40, y=5.0, cpi_growth=0.01):
    idx = pd.date_range("2000-01-31", periods=n, freq="ME")
    cpi = 100.0 * (1 + cpi_growth) ** np.arange(n)
    return pd.DataFrame(
        {
            "y10": np.full(n, y),
            "cpi": cpi,
            "price": np.full(n, 100.0),
            "real_tr_index": np.full(n, 1.0),
        },
        index=idx,
    )


def test_bond_real_return_matches_formula():
    f = _frame()
    h = 12
    out = stats.bond_real_return(f, h, exec_lag=1)
    nominal = (1 + 0.05) ** (h / 12) - 1
    infl = 1.01 ** h - 1
    expected = (1 + nominal) / (1 + infl) - 1
    assert out.iloc[0] == pytest.approx(expected)


def test_bond_real_return_tail_is_nan():
    f = _frame(n=20)
    out = stats.bond_real_return(f, 12, exec_lag=1)
    assert out.isna().sum() >= 13


def test_excess_real_return_identity():
    f = _frame()
    h = 6
    eq = stats.forward_return(f["real_tr_index"], h, exec_lag=1)
    bd = stats.bond_real_return(f, h, exec_lag=1)
    ex = stats.excess_real_return(f, h, exec_lag=1)
    mask = eq.notna() & bd.notna()
    assert mask.any()
    assert np.allclose(ex[mask], (1 + eq[mask]) / (1 + bd[mask]) - 1)


def test_nw_ols_two_regressors_names():
    rng = np.random.default_rng(3)
    n = 300
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    y = 0.5 * x1 - 1.2 * x2 + rng.normal(scale=0.3, size=n)
    res = stats.nw_ols(y, np.column_stack([x1, x2]), lag=12, names=["a", "b"])
    assert res.names == ["const", "a", "b"]
    assert res.params[1] == pytest.approx(0.5, abs=0.1)
    assert res.params[2] == pytest.approx(-1.2, abs=0.1)
    assert res.n == n


def test_effective_sample_and_shortfall_consistency():
    """有效样本随窗口变长单调下降，且不会小于 1。"""
    vals = [stats.effective_sample(1000, h) for h in (12, 36, 60, 120)]
    assert vals == sorted(vals, reverse=True)
    assert min(vals) >= 1
