"""实验层测试：基准比较、样本内外划分、可重复性。

这里的合成数据只用于证明**程序逻辑**正确；它们不能证明投资方法有效。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepquant.research.equity_bond_spread.config import (
    IndicatorType,
    ResearchConfig,
)
from deepquant.research.equity_bond_spread.experiment import (
    build_baseline,
    buy_and_hold,
    compare_to_baseline,
    compute_event_outcomes,
    run_experiment,
    run_sensitivity,
)
from deepquant.research.equity_bond_spread.indicators import build_indicator_frame
from deepquant.research.equity_bond_spread.signals import extract_events


def _panel(n: int = 120, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2010-01-31", periods=n, freq="ME")
    price = 100 * np.exp(np.cumsum(rng.normal(0.004, 0.05, n)))
    pe = np.clip(20 / (1 + 0.3 * np.sin(np.arange(n) / 9.0)), 6, 40)
    return pd.DataFrame(
        {
            "price": price,
            "pe": pe,
            "pb": np.full(n, 1.5),
            "div_yield": np.full(n, 2.0),
            "bond_10y": np.full(n, 3.0) + 0.5 * np.sin(np.arange(n) / 20.0),
        },
        index=idx,
    )


def test_build_baseline_excludes_immature():
    panel = _panel(40)
    cfg = ResearchConfig(indicator=IndicatorType.B, start_date="2010-01-01",
                         end_date="2030-12-31")
    base = build_baseline(panel, cfg)
    assert len(base) > 0
    # 最长窗口 250td → 12 个月；n=40 时应有 40-12=28 个成熟样本
    longest = base[base["window_td"] == 250]
    assert len(longest) == 28


def test_baseline_returns_match_manual_calc():
    """20 交易日 ≈ 1 个月：每个观测到下一个观测的收益必须手工可核算。"""
    idx = pd.date_range("2010-01-31", periods=4, freq="ME")
    panel = pd.DataFrame(
        {"price": [100.0, 110.0, 121.0, 100.0], "pe": [10.0] * 4,
         "pb": [1.0] * 4, "div_yield": [2.0] * 4, "bond_10y": [3.0] * 4},
        index=idx,
    )
    cfg = ResearchConfig(indicator=IndicatorType.B, start_date="2010-01-01",
                         end_date="2020-12-31", forward_windows_td=(20,),
                         cost_rate=0.0)
    base = build_baseline(panel, cfg)
    got = dict(zip(pd.to_datetime(base["obs_date"]), base["gross_return"]))
    assert got[idx[0]] == pytest.approx(0.10)          # 100 → 110
    assert got[idx[1]] == pytest.approx(0.10)          # 110 → 121
    assert got[idx[2]] == pytest.approx(100.0 / 121.0 - 1.0)  # 121 → 100
    # 最后一个观测之后没有完整窗口，不应出现
    assert idx[3] not in got


def test_event_outcomes_match_manual_calc():
    idx = pd.date_range("2010-01-31", periods=15, freq="ME")
    price = np.full(15, 100.0)
    price[2] = 100.0     # 执行日
    price[14] = 121.0    # 12 个月后（250td）
    frame = pd.DataFrame(
        {
            "signal": [1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
            "spread_pct": [9.0, 9.0] + [1.0] * 13,
            "percentile": [99.0, 99.0] + [10.0] * 13,
            "price": price,
            "pe": [10.0] * 15,
            "bond_10y": [3.0] * 15,
            "history_obs": [1.0] * 15,
        },
        index=idx,
    )
    cfg = ResearchConfig(indicator=IndicatorType.B, exec_lag_obs=1,
                         start_date="2010-01-01", end_date="2020-12-31",
                         forward_windows_td=(250,), cost_rate=0.0)
    events = extract_events(frame, cfg)
    out = compute_event_outcomes(frame, events, cfg)
    # 信号在 idx[0]，执行在 idx[1]，12 个月后是 idx[13]
    assert out["ret_250td"].iloc[0] == pytest.approx(0.0)
    assert out["exec_date"].iloc[0] == idx[1]


def test_compare_to_baseline_segments():
    panel = _panel(120)
    cfg = ResearchConfig(indicator=IndicatorType.B, start_date="2010-01-01",
                         end_date="2030-12-31", is_end_date="2015-12-31",
                         threshold_pct=80.0)
    frame = build_indicator_frame(panel, cfg)
    events = extract_events(frame, cfg)
    ev = compute_event_outcomes(frame, events, cfg)
    base = build_baseline(frame, cfg)
    is_end = pd.Timestamp("2015-12-31")
    c_is = compare_to_baseline(ev, base, cfg, segment="is", is_end=is_end)
    c_oos = compare_to_baseline(ev, base, cfg, segment="oos", is_end=is_end)
    assert c_is[0]["segment"] == "is"
    assert c_oos[0]["segment"] == "oos"
    # 两段样本数之和应等于全样本数（未成熟样本已排除）
    c_all = compare_to_baseline(ev, base, cfg, segment="full")
    assert c_all[0]["n_signals"] == c_is[0]["n_signals"] + c_oos[0]["n_signals"]


def test_buy_and_hold_matches_manual():
    idx = pd.date_range("2010-01-31", periods=13, freq="ME")
    panel = pd.DataFrame(
        {"price": [100.0] * 12 + [121.0], "pe": [10.0] * 13, "pb": [1.0] * 13,
         "div_yield": [2.0] * 13, "bond_10y": [3.0] * 13},
        index=idx,
    )
    cfg = ResearchConfig(start_date="2010-01-01", end_date="2011-06-30",
                         is_end_date="2011-01-31")
    bh = buy_and_hold(panel, cfg)
    assert bh["total_return"] == pytest.approx(0.21)
    assert bh["annualized_return"] == pytest.approx(0.21, abs=0.02)
    assert bh["max_drawdown"] == pytest.approx(0.0)


# ---------------------------------------------------------------- 17. 可重复


def test_run_experiment_is_deterministic():
    panel = _panel(120)
    cfg = ResearchConfig(indicator=IndicatorType.B, start_date="2010-01-01",
                         end_date="2030-12-31", threshold_pct=85.0)
    r1 = run_experiment(panel, cfg)
    r2 = run_experiment(panel, cfg)
    assert len(r1.events) == len(r2.events)
    pd.testing.assert_frame_equal(r1.event_df, r2.event_df)
    assert r1.comparison == r2.comparison
    assert r1.comparison_oos == r2.comparison_oos


def test_run_experiment_does_not_mutate_input():
    panel = _panel(60)
    before = panel.copy()
    cfg = ResearchConfig(indicator=IndicatorType.B, start_date="2010-01-01",
                         end_date="2030-12-31")
    run_experiment(panel, cfg)
    pd.testing.assert_frame_equal(panel, before)


def test_run_sensitivity_shape():
    panel = _panel(120)
    from deepquant.research.equity_bond_spread.config import sensitivity_grid
    grid = sensitivity_grid()[:3]
    out = run_sensitivity(panel, grid)
    assert len(out) == 3 * 3 * len(grid[0].forward_windows_td)  # full/is/oos × 窗口
    assert set(out["segment"]) == {"full", "is", "oos"}


def test_config_to_dict_roundtrip():
    cfg = ResearchConfig(indicator=IndicatorType.A, lookback_years=10,
                         threshold_pct=95.0)
    d = cfg.to_dict()
    assert d["indicator"] == "A"
    assert d["lookback_years"] == 10
    assert d["threshold_pct"] == 95.0
    assert "data" in d
