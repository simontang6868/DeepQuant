"""双均线交叉策略。

规则：
- 快线（短周期均线）上穿慢线（长周期均线）→ BUY；
- 快线下穿慢线 → SELL；
- 两条均线都有效（窗口已填满）时才判定交叉，预热期一律 HOLD。

均线使用滚动均值（只依赖当前与过去的数据），不含未来函数。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from deepquant.analysis.rolling import rolling_mean
from deepquant.strategies.base import Signal, Strategy


@dataclass
class MaCrossStrategy(Strategy):
    """快慢双均线交叉策略。"""

    fast_window: int = 5
    slow_window: int = 20

    name: str = "ma_cross"
    version: str = "0.1.0"

    def __post_init__(self) -> None:
        if self.fast_window < 1:
            raise ValueError(f"fast_window 必须 >= 1，收到 {self.fast_window}")
        if self.slow_window <= self.fast_window:
            raise ValueError(
                f"slow_window({self.slow_window}) 必须大于 fast_window({self.fast_window})"
            )

    @property
    def params(self) -> dict:
        return {"fast_window": self.fast_window, "slow_window": self.slow_window}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """产出事件型信号序列（与 data 同索引）。"""
        if "close" not in data.columns:
            raise ValueError("行情数据缺少 close 列")

        close = data["close"]
        fast = rolling_mean(close, self.fast_window)
        slow = rolling_mean(close, self.slow_window)

        above = fast > slow  # 当前快慢线相对位置
        valid = fast.notna() & slow.notna()  # 两条均线都有值
        prev_above = above.shift(1, fill_value=False)
        prev_valid = valid.shift(1, fill_value=False)
        crossable = valid & prev_valid  # 当前与前一状态都有效，才算真交叉

        buy = crossable & above & (~prev_above)  # 上穿
        sell = crossable & (~above) & prev_above  # 下穿

        signals = pd.Series(int(Signal.HOLD), index=close.index, dtype="int8")
        signals[buy] = int(Signal.BUY)
        signals[sell] = int(Signal.SELL)
        return signals
