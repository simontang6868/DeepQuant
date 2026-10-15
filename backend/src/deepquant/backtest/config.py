"""回测配置与执行相关枚举（纯数据，无 IO）。

本模块只描述「回测怎么跑」，不做撮合与账务（那些属于 backtest.engine）。

费用参数：默认值仅为**开发 / 测试 / 模拟**用的占位参数（对应 D6），
不代表任何真实券商费率，也不代表所有交易日的真实市场标准。
凡使用默认值时，fee_source 明确标注为 "simulation_placeholder"。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd

from deepquant.market.rules import MarketRules, a_share_rules


class ExecutionPrice(str, Enum):
    """成交基准价规则。

    v1 仅实现 NEXT_OPEN（下一交易日开盘价）。
    其它规则（如 NEXT_CLOSE）在后续阶段实现前不应引入，以免被误认为已支持。
    """

    NEXT_OPEN = "next_open"


# --- D6 模拟占位费率预设：仅用于开发、测试与模拟，不是真实费率 ---
SIMULATION_COMMISSION_RATE = 0.0003
SIMULATION_MIN_COMMISSION = 5.0
SIMULATION_STAMP_DUTY_RATE = 0.0005
SIMULATION_TRANSFER_FEE_RATE = 0.0
SIMULATION_SLIPPAGE_BUY = 0.001
SIMULATION_SLIPPAGE_SELL = 0.001

FEE_SOURCE_SIMULATION = "simulation_placeholder"

DEFAULT_ASSUMPTIONS: tuple[str, ...] = (
    "费用与滑点为模拟占位参数（fee_source=simulation_placeholder），"
    "仅用于程序开发、测试与模拟，不代表真实券商费率或真实市场标准；"
    "滑点通过成交价格体现，不重复计入手续费。",
)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def require_positive(value: object, name: str) -> float:
    """校验为「有限正数」，返回 float；否则抛出 ValueError。"""
    if not _is_number(value) or not math.isfinite(value) or value <= 0:  # type: ignore[arg-type]
        raise ValueError(f"{name} 必须是有限的非零正数，收到 {value!r}")
    return float(value)  # type: ignore[arg-type]


def require_non_negative(value: object, name: str) -> float:
    """校验为「有限的非负数」，返回 float；否则抛出 ValueError。"""
    if not _is_number(value) or not math.isfinite(value) or value < 0:  # type: ignore[arg-type]
        raise ValueError(f"{name} 必须是有限的非负数，收到 {value!r}")
    return float(value)  # type: ignore[arg-type]


def require_naive_timestamp(value: object, name: str) -> pd.Timestamp:
    """把日期类值规范化为无时区（tz-naive）的 pd.Timestamp。

    校验规则：
    - 无法解析的日期抛出 ValueError；
    - NaT 抛出 ValueError；
    - 带时区的日期戳抛出 ValueError（全项目统一使用无时区交易日）。
    """
    try:
        ts = pd.Timestamp(value)  # type: ignore[arg-type]
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} 无法解析为时间戳：{value!r}") from exc
    if pd.isna(ts):
        raise ValueError(f"{name} 不能为 NaT")
    if ts.tzinfo is not None:
        raise ValueError(f"{name} 必须是无时区（tz-naive）时间戳，收到 {ts}")
    return ts


@dataclass(frozen=True)
class BacktestConfig:
    """单标的、单策略、日线回测的配置。

    说明：
    - 交易单位（lot_size）、T+1、涨跌停等市场规则统一由 market_rules 提供，
      此处不重复定义，避免与 market 模块概念冲突；
    - 费用与滑点参数全部可配置，默认值为模拟占位参数（见上方常量）；
    - start_date / end_date 为闭区间的回测日期范围；None 表示不设边界。
    """

    symbol: str
    initial_cash: float
    start_date: pd.Timestamp | None = None
    end_date: pd.Timestamp | None = None
    exec_price_rule: ExecutionPrice = ExecutionPrice.NEXT_OPEN
    signal_lag: int = 1
    market_rules: MarketRules = field(default_factory=a_share_rules)
    commission_rate: float = SIMULATION_COMMISSION_RATE
    min_commission: float = SIMULATION_MIN_COMMISSION
    stamp_duty_rate: float = SIMULATION_STAMP_DUTY_RATE
    transfer_fee_rate: float = SIMULATION_TRANSFER_FEE_RATE
    slippage_buy: float = SIMULATION_SLIPPAGE_BUY
    slippage_sell: float = SIMULATION_SLIPPAGE_SELL
    fee_source: str = FEE_SOURCE_SIMULATION
    assumptions: tuple[str, ...] = DEFAULT_ASSUMPTIONS

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError(f"symbol 必须是非空字符串，收到 {self.symbol!r}")
        if not _is_number(self.initial_cash) or not math.isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError(f"initial_cash 必须是有限的非零正数，收到 {self.initial_cash!r}")
        if not isinstance(self.signal_lag, int) or isinstance(self.signal_lag, bool) or self.signal_lag < 1:
            raise ValueError(f"signal_lag 必须是 >= 1 的整数，收到 {self.signal_lag!r}")
        if not isinstance(self.exec_price_rule, ExecutionPrice):
            raise ValueError(f"exec_price_rule 必须是 ExecutionPrice，收到 {self.exec_price_rule!r}")
        if not isinstance(self.market_rules, MarketRules):
            raise ValueError("market_rules 必须是 MarketRules 实例")

        for name in (
            "commission_rate",
            "min_commission",
            "stamp_duty_rate",
            "transfer_fee_rate",
            "slippage_buy",
            "slippage_sell",
        ):
            require_non_negative(getattr(self, name), name)

        if not isinstance(self.fee_source, str) or not self.fee_source.strip():
            raise ValueError(f"fee_source 必须是非空字符串，收到 {self.fee_source!r}")
        if not isinstance(self.assumptions, tuple) or not all(
            isinstance(a, str) for a in self.assumptions
        ):
            raise ValueError("assumptions 必须是字符串组成的元组")

        start = None if self.start_date is None else require_naive_timestamp(self.start_date, "start_date")
        end = None if self.end_date is None else require_naive_timestamp(self.end_date, "end_date")
        if start is not None and end is not None and start > end:
            raise ValueError(f"start_date 不能晚于 end_date（{start} > {end}）")

        # frozen dataclass：规范化的时间戳需用 object.__setattr__ 写回
        object.__setattr__(self, "start_date", start)
        object.__setattr__(self, "end_date", end)
