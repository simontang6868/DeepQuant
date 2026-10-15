"""A 股相关市场规则的纯函数实现（无 IO、无状态）。

本模块只回答「数量如何取整 / 边界价如何计算 / 可卖多少 / 开盘价是否阻挡成交」
这类可独立测试的问题；现金、持仓、成本等账务一律属于 backtest 模块。

必须随结果记录的限制（本模块不替调用方断言已实现）：
- 涨跌停为简化模型：仅以「开盘价是否触及配置的边界价」保守判定，不模拟排队与部分成交；
- 涨跌停规则未配置（price_limit=None）时不做任何涨跌停判定，不得假装已实现；
- 不区分「数据缺失」与「已确认停牌」：目标日无行情或开盘价非法一律记为无法成交
  （no_price），但不会断言当天一定是停牌。
"""

from __future__ import annotations

import math

from deepquant.market.rules import MarketRules, PriceLimit, Side

# 无法成交原因码（由 backtest 写入 Order.reason）
BLOCK_NO_PRICE = "no_price"  # 目标日无行情，或开盘价缺失/非法
BLOCK_LIMIT_UP = "limit_up"  # 买入时开盘价触及配置的涨停价
BLOCK_LIMIT_DOWN = "limit_down"  # 卖出时开盘价触及配置的跌停价


def _check_lot_size(lot_size: int) -> None:
    if lot_size < 1:
        raise ValueError(f"lot_size 必须 >= 1，收到 {lot_size}")


def round_to_tick(value: float, tick_size: float) -> float:
    """按最小价格变动单位四舍五入（半值向上），并清理浮点误差。"""
    if tick_size <= 0:
        raise ValueError(f"tick_size 必须 > 0，收到 {tick_size}")
    if not math.isfinite(value):
        raise ValueError(f"value 必须是有限值，收到 {value}")
    steps = value / tick_size
    return round(math.floor(steps + 0.5) * tick_size, 10)


def round_down_to_lot(quantity: float, lot_size: int) -> int:
    """把数量向下取整到整数手；不足一手或非正数返回 0。"""
    _check_lot_size(lot_size)
    if quantity <= 0:
        return 0
    return int(quantity // lot_size) * lot_size


def affordable_lot_quantity(cash: float, price: float, lot_size: int) -> int:
    """给定现金与价格，最多可买的整手数量（不含费用）。

    这里只给「未计费用」的理论上限；含费用后的最终数量由 backtest 逐手回退决定。
    """
    _check_lot_size(lot_size)
    if cash <= 0 or price <= 0:
        return 0
    lots = math.floor(cash / (price * lot_size))
    return int(lots) * lot_size


def limit_prices(
    prev_close: float | None, price_limit: PriceLimit | None
) -> tuple[float, float] | None:
    """按配置计算 (跌停价, 涨停价)。

    返回 None 的情形（调用方不得假装检测成功）：
    - 涨跌停规则未配置（price_limit=None）；
    - 前收盘价缺失、非有限或非正，无法可靠计算边界价。
    """
    if price_limit is None:
        return None
    if prev_close is None or not math.isfinite(prev_close) or prev_close <= 0:
        return None
    lower = round_to_tick(prev_close * (1.0 - price_limit.rate), price_limit.tick_size)
    upper = round_to_tick(prev_close * (1.0 + price_limit.rate), price_limit.tick_size)
    return (lower, upper)


def sellable_quantity(position: int, bought_today: int, rules: MarketRules) -> int:
    """按 T+1 规则计算当日可卖数量。

    T+1 市场：可卖 = 总持仓 - 当日买入；
    非 T+1 市场：可卖 = 总持仓。
    """
    if position < 0:
        raise ValueError(f"position 不能为负，收到 {position}")
    if bought_today < 0:
        raise ValueError(f"bought_today 不能为负，收到 {bought_today}")
    if bought_today > position:
        raise ValueError("bought_today 不能大于总持仓")
    if not rules.t_plus_one:
        return position
    return position - bought_today


def open_fill_block_reason(
    side: Side,
    open_price: float | None,
    boundary: tuple[float, float] | None,
) -> str | None:
    """以开盘价与涨跌停边界判断当日是否无法成交。

    返回原因码（BLOCK_*）表示无法成交；返回 None 表示「未发现阻挡」——
    注意 None 不等于「一定成交」（本模块不模拟排队、部分成交与盘中打开）。

    保守判定（对应配置化涨跌停）：
    - 买入：开盘价 >= 涨停价 → 拒绝（limit_up）；
    - 卖出：开盘价 <= 跌停价 → 拒绝（limit_down）；
    - 开盘价缺失/非有限/非正 → 无法成交（no_price）。
    boundary 为 None 时跳过涨跌停判定（规则未配置或前收盘价不可用）。
    """
    if open_price is None or not math.isfinite(open_price) or open_price <= 0:
        return BLOCK_NO_PRICE
    if boundary is not None:
        lower, upper = boundary
        if side == Side.BUY and open_price >= upper:
            return BLOCK_LIMIT_UP
        if side == Side.SELL and open_price <= lower:
            return BLOCK_LIMIT_DOWN
    return None
