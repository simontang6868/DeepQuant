"""均值回归策略（Mean Reversion Strategy）—— Step 7.5 新增异质策略。

信号逻辑：
- 对收盘价计算滚动 z-score：z_t = (close_t - rolling_mean_t) / rolling_std_t
- 当 z 从下向上突破 -threshold → BUY（价格显著低于均值，预期回归）
- 当 z 从上向下突破 +threshold → SELL（价格显著高于均值，预期回落）
- 预热期（rolling window 未填满 / std=0 / NaN）一律 HOLD

参数：
- lookback: 滚动窗口（交易日）
- threshold: z-score 阈值（绝对值，默认 2.0，即 2 倍标准差）

与 MaCrossStrategy 异质性：
- MaCross 是趋势跟随（追涨杀跌），本策略是均值回归（买低卖高）
- MaCross 不关心波动幅度，本策略直接基于价格偏离程度
- 两者在同一市场市况下通常给出相反信号，是良好的对照

防未来函数保证：
- rolling_mean / rolling_std 均使用 pandas rolling(window).mean()/std()，
  默认 `min_periods=window`，即窗口填满前全返回 NaN
- z-score 的分子分母都只依赖当时及以前的数据
- 信号按 t 日收盘后产生（同 MaCross），在 t+1 日成交
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from deepquant.analysis.rolling import rolling_mean
from deepquant.strategies.base import Signal, Strategy


def _rolling_std(series: pd.Series, window: int) -> pd.Series:
    """滚动标准差。min_periods=window 确保预热期返回 NaN。"""
    # pandas 3.0: std(ddof=0) 作为方法参数；2.x 也是
    return series.rolling(window=window, min_periods=window).std(ddof=0)


@dataclass
class MeanReversionStrategy(Strategy):
    """均值回归策略：基于滚动 z-score 的价格偏离信号。"""

    lookback: int = 20
    threshold: float = 2.0

    name: str = "mean_reversion"
    version: str = "0.1.0"

    def __post_init__(self) -> None:
        if self.lookback < 2:
            raise ValueError(f"lookback 必须 >= 2（需要至少 2 个点算 std），收到 {self.lookback}")
        if self.threshold <= 0:
            raise ValueError(f"threshold 必须 > 0，收到 {self.threshold}")

    @property
    def params(self) -> dict:
        return {"lookback": self.lookback, "threshold": self.threshold}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        if "close" not in data.columns:
            raise ValueError("行情数据缺少 close 列")

        close = data["close"].astype("float64")

        # 滚动均值 / 标准差
        mean = rolling_mean(close, self.lookback)
        std = _rolling_std(close, self.lookback)

        # z-score（std=0 时 z=NaN → 不会触发信号；std<0 理论上不会发生）
        z = (close - mean) / std.replace(0.0, float("nan"))

        # 只在 z 和前一日 z 都有效时才产生信号
        prev_z = z.shift(1)
        valid = z.notna() & prev_z.notna()

        # 上穿 -threshold → BUY（价格从低位向上回归）
        buy = valid & (prev_z < -self.threshold) & (z >= -self.threshold)
        # 下穿 +threshold → SELL（价格从高位向下回落）
        sell = valid & (prev_z > self.threshold) & (z <= self.threshold)

        signals = pd.Series(int(Signal.HOLD), index=close.index, dtype="int8")
        signals[buy] = int(Signal.BUY)
        signals[sell] = int(Signal.SELL)
        return signals
