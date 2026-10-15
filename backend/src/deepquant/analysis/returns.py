"""收益率计算。

约定（与全项目一致）：
- 不使用未来数据：t 时刻的收益率只依赖 t 及更早的价格；
- 不做任何填充：价格中的缺失值原样透传，结果对应位置为 NaN；
- 价格为非正时结果可能无意义，调用方应保证输入是已校验的行情。
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _check_periods(periods: int) -> None:
    if periods < 1:
        raise ValueError(f"periods 必须 >= 1，收到 {periods}")


def simple_returns(prices: pd.Series, periods: int = 1) -> pd.Series:
    """简单收益率：prices[t] / prices[t - periods] - 1。

    注意：此处显式用除法实现，而非 pandas 的 pct_change（后者历史上会前向填充，
    属于伪造数据）。
    """
    _check_periods(periods)
    return prices / prices.shift(periods) - 1.0


def log_returns(prices: pd.Series, periods: int = 1) -> pd.Series:
    """对数收益率：ln(prices[t] / prices[t - periods])。"""
    _check_periods(periods)
    return np.log(prices / prices.shift(periods))
