"""滚动统计。

所有滚动窗口都只使用「当前及过去」的观测，绝不引入未来数据。
min_periods 默认等于 window，即窗口未填满时结果为 NaN（不因窗口不足而给出
看似有效的数值）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _check_window(window: int) -> None:
    if window < 1:
        raise ValueError(f"window 必须 >= 1，收到 {window}")


def rolling_mean(series: pd.Series, window: int, *, min_periods: int | None = None) -> pd.Series:
    """滚动均值。"""
    _check_window(window)
    mp = window if min_periods is None else min_periods
    return series.rolling(window=window, min_periods=mp).mean()


def rolling_std(series: pd.Series, window: int, *, min_periods: int | None = None) -> pd.Series:
    """滚动标准差（样本标准差，ddof=1）。"""
    _check_window(window)
    mp = window if min_periods is None else min_periods
    return series.rolling(window=window, min_periods=mp).std()


def rolling_volatility(
    returns: pd.Series,
    window: int,
    *,
    periods_per_year: int = 252,
    min_periods: int | None = None,
) -> pd.Series:
    """滚动年化波动率 = 滚动标准差 × sqrt(periods_per_year)。

    periods_per_year 是市场交易日约定（A 股常用 252），作为参数由调用方传入，
    因此本模块不写死任何特定市场。
    """
    std = rolling_std(returns, window, min_periods=min_periods)
    return std * float(np.sqrt(periods_per_year))
