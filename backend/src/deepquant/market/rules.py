"""市场规则配置（纯数据，无 IO，不写死任何特定市场）。

设计原则：
- 交易单位、T+1、涨跌停等规则全部参数化，不把某市场的固定值写进代码；
- 涨跌停规则允许缺省（None），表示「未明确配置」——此时不得假装已实现检测，
  也不得默认声称已完整模拟涨跌停；
- 本模块只描述规则，不做撮合判定；判定逻辑见 market.ashare 的纯函数。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Side(str, Enum):
    """订单方向。

    独立于 strategies 的信号枚举：market 是被回测依赖的底层，不反向依赖 strategies。
    """

    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class PriceLimit:
    """涨跌停规则配置。

    rate: 涨跌幅比例（如 0.10 表示 ±10%）。必须由调用方显式提供，
          不提供则视为「规则未知」，检测不得假装成功。
    tick_size: 最小价格变动单位（A 股通常 0.01），用于边界价取整。
    """

    rate: float
    tick_size: float = 0.01

    def __post_init__(self) -> None:
        if not (0.0 < self.rate < 1.0):
            raise ValueError(f"price_limit.rate 必须在 (0, 1) 之间，收到 {self.rate}")
        if self.tick_size <= 0:
            raise ValueError(f"tick_size 必须 > 0，收到 {self.tick_size}")


@dataclass(frozen=True)
class MarketRules:
    """市场规则集合（纯数据）。

    price_limit 为 None 表示「未明确配置涨跌停规则」：
    此时不进行任何涨跌停判定，调用方必须把该限制写入回测结果的 model_limitations。
    """

    name: str = "custom"
    lot_size: int = 100
    t_plus_one: bool = True
    price_limit: PriceLimit | None = None

    def __post_init__(self) -> None:
        if self.lot_size < 1:
            raise ValueError(f"lot_size 必须 >= 1，收到 {self.lot_size}")


def a_share_rules(
    *,
    price_limit_rate: float | None = None,
    lot_size: int = 100,
    tick_size: float = 0.01,
    name: str = "A_SHARE",
) -> MarketRules:
    """构造 A 股规则配置。

    price_limit_rate=None（默认）表示涨跌停规则未配置：
    这是刻意的默认，避免把 10% 冒充为所有 A 股标的的统一规则。
    需要检测涨跌停时，必须由调用方按标的具体情况显式传入比例。
    """
    limit = None if price_limit_rate is None else PriceLimit(price_limit_rate, tick_size)
    return MarketRules(name=name, lot_size=lot_size, t_plus_one=True, price_limit=limit)


def price_limit_notes(rules: MarketRules) -> list[str]:
    """返回与涨跌停配置相关的模型限制说明，供 BacktestResult.model_limitations 记录。"""
    limit = rules.price_limit
    if limit is None:
        return [
            "涨跌停规则未配置：未进行涨跌停成交判定（这并不表示未发生涨跌停）。"
        ]
    return [
        (
            f"涨跌停为简化模型：按配置比例 ±{limit.rate:.6g}、最小变动 "
            f"{limit.tick_size:g} 计算边界价，仅以开盘价是否触及边界做保守判定；"
            "不模拟排队、部分成交、盘中打开与不同板块差异。"
        )
    ]
