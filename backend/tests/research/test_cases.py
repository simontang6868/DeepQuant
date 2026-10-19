"""案例检查：数据可用性判定。

重点回归一审计发现的缺陷：当研究区间**前段**历史百分位尚不可用时，
不能把这段记为「没有信号」——那是「无法验证」，两者含义完全不同。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from deepquant.research.equity_bond_spread.cases import CASE_WINDOWS, check_cases
from deepquant.research.equity_bond_spread.config import ResearchConfig


def _frame(pct_start: str | None) -> pd.DataFrame:
    idx = pd.date_range("2005-01-31", "2010-12-31", freq="ME")
    n = len(idx)
    frame = pd.DataFrame(
        {
            "price": np.linspace(1000.0, 3000.0, n),
            "pe": np.full(n, 15.0),
            "div_yield": np.full(n, 2.0),
            "bond_10y": np.full(n, 3.0),
            "spread_pct": np.full(n, 3.67),
        },
        index=idx,
    )
    pct = pd.Series(np.nan, index=idx, dtype="float64")
    if pct_start is not None:
        mask = idx >= pd.Timestamp(pct_start)
        pct[mask] = 50.0
    frame["percentile"] = pct
    return frame


def _empty_events() -> pd.DataFrame:
    return pd.DataFrame(
        columns=[
            "signal_date", "last_signal_date", "exec_date", "spread_at_signal",
            "percentile_at_signal", "pe_at_signal", "bond_10y_at_signal",
            "ret_20td", "ret_60td", "ret_120td", "ret_250td",
            "dd_250td", "mae_250td", "immature_any",
        ]
    )


def _case(results, name):
    for r in results:
        if r.case_name == name:
            return r
    raise AssertionError(f"未找到案例 {name}")


def test_partial_coverage_reported_as_unverifiable_not_no_signal():
    """区间前段无百分位时，必须标注 pct_available_from 且措辞为「无法验证」。"""
    name = next(k for k, (s, e) in CASE_WINDOWS.items() if s.startswith("2008"))
    results = check_cases(_frame("2009-02-28"), _empty_events(), ResearchConfig())
    case = _case(results, name)

    assert case.data_available is True          # 区间不是完全不可用
    assert case.pct_available_from == pd.Timestamp("2009-02-28")
    assert "无法验证" in case.note
    # 关键：不能把前段无数据说成「没有满足规则的信号事件」而省略说明
    assert case.n_events == 0


def test_full_coverage_keeps_plain_no_signal_wording():
    """区间起点已有百分位时，不应误报为「无法验证」。"""
    name = next(k for k, (s, e) in CASE_WINDOWS.items() if s.startswith("2008"))
    results = check_cases(_frame("2005-01-31"), _empty_events(), ResearchConfig())
    case = _case(results, name)

    assert case.pct_available_from is None
    assert case.data_available is True
    assert "无法验证" not in case.note
    assert "没有满足规则的信号事件" in case.note


def test_window_entirely_before_data_is_unavailable():
    """整个区间都早于首个百分位日 → data_available=False。"""
    name = next(k for k, (s, e) in CASE_WINDOWS.items() if s.startswith("2008"))
    results = check_cases(_frame("2010-06-30"), _empty_events(), ResearchConfig())
    case = _case(results, name)

    assert case.data_available is False
    assert "无法验证" in case.note
