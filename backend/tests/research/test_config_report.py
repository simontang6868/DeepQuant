"""配置校验、报告生成与结论分级测试。"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from deepquant.research.equity_bond_spread.config import (
    IndicatorType,
    PercentileWindow,
    ResearchConfig,
    ReturnBasis,
    sensitivity_grid,
)
from deepquant.research.equity_bond_spread.report import (
    MIN_OOS_EVENTS,
    VERDICT_LABELS,
    build_report_input,
    render_report,
    verdict,
)


# ---------------------------------------------------------------- 14. 配置校验


def test_default_config_is_valid():
    cfg = ResearchConfig()
    assert cfg.indicator is IndicatorType.B
    assert cfg.threshold_pct == 90.0


def test_exec_lag_zero_is_rejected():
    """同日成交属未来函数，必须拒绝。"""
    with pytest.raises(ValueError, match="exec_lag_obs"):
        ResearchConfig(exec_lag_obs=0)


def test_threshold_out_of_range_rejected():
    with pytest.raises(ValueError):
        ResearchConfig(threshold_pct=0.0)
    with pytest.raises(ValueError):
        ResearchConfig(threshold_pct=101.0)


def test_bad_date_ranges_rejected():
    with pytest.raises(ValueError):
        ResearchConfig(start_date="2020-01-01", end_date="2019-01-01")
    with pytest.raises(ValueError):
        ResearchConfig(start_date="2010-01-01", end_date="2020-01-01",
                       is_end_date="2020-06-01")


def test_forward_windows_must_be_positive_sorted_unique():
    with pytest.raises(ValueError):
        ResearchConfig(forward_windows_td=(60, 20))
    with pytest.raises(ValueError):
        ResearchConfig(forward_windows_td=(20, 20))
    with pytest.raises(ValueError):
        ResearchConfig(forward_windows_td=())


def test_indicator_and_basis_type_checks():
    with pytest.raises(TypeError):
        ResearchConfig(indicator="A")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ResearchConfig(return_basis="price")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ResearchConfig(percentile_window="expanding")  # type: ignore[arg-type]


def test_data_config_frequency_checks():
    from deepquant.research.equity_bond_spread.config import DataConfig
    with pytest.raises(ValueError):
        DataConfig(frequency="yearly")
    assert DataConfig(frequency="monthly").obs_per_year() == 12
    assert DataConfig(frequency="weekly").obs_per_year() == 52
    assert DataConfig(frequency="daily").obs_per_year() == 252


def test_sensitivity_grid_is_predefined_and_valid():
    grid = sensitivity_grid()
    assert len(grid) >= 10
    thresholds = {c.threshold_pct for c in grid}
    assert {80.0, 85.0, 90.0, 95.0} <= thresholds
    lookbacks = {c.lookback_years for c in grid if c.indicator is IndicatorType.A}
    assert {5, 10} <= lookbacks
    names = [c.experiment_name for c in grid]
    assert len(names) == len(set(names))


# ---------------------------------------------------------------- 15. 报告生成


def _fake_result(indicator: IndicatorType, n_events: int = 4):
    class _Res:
        config = ResearchConfig(indicator=indicator)
        events = [object()] * n_events
        event_df = pd.DataFrame(
            [{"event_id": i, "signal_date": pd.Timestamp("2014-06-30"),
              "exec_date": pd.Timestamp("2014-07-31"),
              "spread_at_signal": 5.0, "percentile_at_signal": 92.0,
              "ret_250td": 0.3, "dd_250td": -0.2, "immature_any": False}
             for i in range(n_events)]
        )
        baseline_df = pd.DataFrame()
        comparison = [{"segment": "full", "window_td": 250, "n_signals": n_events,
                       "signal_mean": 0.1, "baseline_mean": 0.05,
                       "mean_difference": 0.05}]
        comparison_is = [{"segment": "is", "window_td": 250, "n_signals": n_events,
                          "signal_mean": 0.12, "baseline_mean": 0.05,
                          "mean_difference": 0.07}]
        comparison_oos = [{"segment": "oos", "window_td": 250, "n_signals": n_events,
                           "signal_mean": 0.08, "baseline_mean": 0.05,
                           "mean_difference": 0.03}]
        buy_hold = {"start_date": "2010-01-31", "end_date": "2020-12-31",
                    "years": 10.0, "total_return": 0.5,
                    "annualized_return": 0.04, "max_drawdown": -0.4}
        indicator_frame = pd.DataFrame()

    return _Res()


def test_render_report_is_deterministic():
    results = {"A": _fake_result(IndicatorType.A), "B": _fake_result(IndicatorType.B)}
    info = {
        "equity": {"date_min": "2010-01-31", "date_max": "2020-12-31",
                   "n_output_rows": 120, "sha256": "a" * 64},
        "bond": {"date_min": "2010-01-04", "date_max": "2020-12-31",
                 "n_output_rows": 2600, "sha256": "b" * 64},
        "coverage": {"n_obs": 120, "coverage_price": 1.0, "coverage_pe": 0.98},
        "bond_match": {"n_exact": 100, "n_filled": 20, "n_missing": 0,
                       "max_fill_days": 3},
    }
    inp = build_report_input(info, results, pd.DataFrame(), {"A": [], "B": []}, ["注记"])
    # 显式注入同一时刻：render_report 默认取挂钟时间（精确到秒），
    # 两次调用跨秒就会得到不同文本 → 本用例曾长期 flaky（CI 上随机红）。
    # 断言未改，只是把"可复现"这一契约真正钉死。
    md1 = render_report(inp, generated_at="2026-01-01 00:00:00")
    md2 = render_report(inp, generated_at="2026-01-01 00:00:00")
    assert md1 == md2
    assert "股债利差与长期市场机会识别研究报告" in md1
    assert "不构成任何投资建议" in md1


def test_report_mentions_caliber_warnings():
    results = {"B": _fake_result(IndicatorType.B)}
    info = {"equity": {}, "bond": {}, "coverage": {}, "bond_match": {}}
    inp = build_report_input(info, results, pd.DataFrame(), {"B": []}, [])
    md = render_report(inp)
    assert "价格指数" in md
    assert "全收益" in md
    assert "盈利收益率" in md


# ---------------------------------------------------------------- 结论分级判据


def test_verdict_insufficient_when_no_oos_events():
    assert verdict(0.05, 0.03, 0) == "insufficient"
    assert verdict(None, 0.03, 5) == "insufficient"
    assert verdict(0.05, None, 5) == "insufficient"


def test_verdict_supported_only_when_consistent_and_enough():
    assert verdict(0.05, 0.03, MIN_OOS_EVENTS) == "supported"
    assert verdict(0.05, -0.01, 5) == "not_supported"
    assert verdict(-0.02, 0.03, 5) == "not_supported"


def test_verdict_not_supported_with_few_events():
    assert verdict(0.05, 0.03, 1) == "not_supported"


def test_verdict_labels_exist():
    for k in ("supported", "not_supported", "insufficient"):
        assert k in VERDICT_LABELS
