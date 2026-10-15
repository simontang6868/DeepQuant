"""策略基类与信号约定。

信号约定（关键）：
- 信号序列与行情数据同索引（通常为日期）；
- t 时刻的信号只使用 t 及更早的数据，绝不使用未来数据；
- 信号取值见 Signal：BUY=1 / SELL=-1 / HOLD=0；
- 信号是「事件型」：仅在状态发生变化的那一天给出 BUY 或 SELL，其余为 HOLD；
- 信号在 t 日（收盘后）产生，因此回测最早应在 t+1 日成交（避免未来函数）；
- 本模块只产出信号，不负责撮合、持仓与成本（那些属于 backtest 模块）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import IntEnum

import pandas as pd


class Signal(IntEnum):
    """策略信号取值。"""

    SELL = -1
    HOLD = 0
    BUY = 1


class Strategy(ABC):
    """策略基类：所有策略只负责「由行情产出信号」。

    子类需声明 name / version，并实现 params 与 generate_signals。
    """

    name: str = ""
    version: str = "0.1.0"

    @property
    @abstractmethod
    def params(self) -> dict:
        """策略参数（用于记录回测设置，以便复现）。"""

    @abstractmethod
    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        """由行情数据产出信号序列（与 data 同索引，取值来自 Signal）。"""

    def describe(self) -> dict:
        """返回策略标识（名称 / 版本 / 参数），用于回测结果的可复现记录。"""
        return {"name": self.name, "version": self.version, "params": self.params}
