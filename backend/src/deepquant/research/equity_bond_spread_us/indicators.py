"""指标与历史百分位。

百分位的实现要点（A 股审计中被重点核查过，这里保持同一口径）：
- 百分位 = 「历史上小于等于当前值的观测占比 × 100」，用闭区间 ``<=``；
- **只使用截至当前时点（含当前）的数据**，不使用任何未来观测；
- 历史观测数不足 ``min_history`` 时返回 NaN，而不是用不足样本给出「精确」百分位；
- 不插值、不回填。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _expanding_percentile(values: np.ndarray, min_obs: int) -> np.ndarray:
    """扩展窗口百分位（含当前值），不足 min_obs 处为 NaN。"""
    n = len(values)
    out = np.full(n, np.nan, dtype=float)
    for i in range(n):
        if not np.isfinite(values[i]):
            continue
        hist = values[: i + 1]
        hist = hist[np.isfinite(hist)]
        if len(hist) < min_obs:
            continue
        out[i] = float((hist <= values[i]).mean() * 100.0)
    return out


def _rolling_percentile(values: np.ndarray, window: int, min_obs: int) -> np.ndarray:
    """固定窗口百分位（含当前值）。"""
    n = len(values)
    out = np.full(n, np.nan, dtype=float)
    for i in range(n):
        if not np.isfinite(values[i]):
            continue
        lo = max(0, i - window + 1)
        hist = values[lo : i + 1]
        hist = hist[np.isfinite(hist)]
        if len(hist) < min_obs:
            continue
        out[i] = float((hist <= values[i]).mean() * 100.0)
    return out


def historical_percentile(
    series: pd.Series,
    window: int = 120,
    min_obs: int = 120,
    expanding: bool = True,
) -> pd.Series:
    """历史百分位（0-100）。

    Parameters
    ----------
    series : pd.Series
        待求百分位的序列，索引须为时间且升序。
    window : int
        固定窗口长度；``expanding=True`` 时不生效。
    min_obs : int
        输出百分位所需的最小历史观测数。
    expanding : bool
        True 用扩展窗口（历史越长越稳），False 用固定窗口（更贴近「当下制度」）。
    """
    if not isinstance(series, pd.Series):
        raise TypeError(f"series 必须是 pd.Series，收到 {type(series)!r}")
    if window <= 0 or min_obs <= 0:
        raise ValueError("window 与 min_obs 必须为正")
    if not series.index.is_monotonic_increasing:
        raise ValueError("series 索引必须升序")
    values = series.to_numpy(dtype=float)
    if expanding:
        pct = _expanding_percentile(values, min_obs)
    else:
        pct = _rolling_percentile(values, window, min_obs)
    return pd.Series(pct, index=series.index, name=f"{series.name}_pct")


def zscore_expanding(series: pd.Series, min_obs: int = 120) -> pd.Series:
    """扩展窗口 z 分数（同样是只用历史数据）。"""
    mean = series.expanding(min_periods=min_obs).mean()
    std = series.expanding(min_periods=min_obs).std(ddof=1)
    return (series - mean) / std.replace(0.0, np.nan)


def add_percentiles(
    frame: pd.DataFrame,
    columns: tuple[str, ...] = ("spread_cape", "ey_cape", "y10"),
    window: int = 120,
    min_obs: int = 120,
    expanding: bool = True,
) -> pd.DataFrame:
    """为一组列追加 ``<col>_pct`` 百分位列。"""
    out = frame.copy()
    for col in columns:
        if col not in out.columns:
            raise KeyError(f"缺少列 {col}")
        out[f"{col}_pct"] = historical_percentile(
            out[col], window=window, min_obs=min_obs, expanding=expanding
        )
    return out
