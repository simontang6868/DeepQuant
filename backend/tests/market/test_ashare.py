"""A 股市场规则纯函数测试。

覆盖：手数取整、可负担手数、边界价计算、T+1 可卖数量、开盘价阻挡成交判定。
重点验证「规则未配置时不做涨跌停判定」「价格非法一律按无法成交处理」。
"""

import math

import pytest

from deepquant.market.ashare import (
    BLOCK_LIMIT_DOWN,
    BLOCK_LIMIT_UP,
    BLOCK_NO_PRICE,
    affordable_lot_quantity,
    limit_prices,
    open_fill_block_reason,
    round_down_to_lot,
    round_to_tick,
    sellable_quantity,
)
from deepquant.market.rules import PriceLimit, Side, a_share_rules

# ---------- round_to_tick ----------


def test_round_to_tick_half_up():
    assert round_to_tick(10.005, 0.01) == pytest.approx(10.01)
    assert round_to_tick(10.004, 0.01) == pytest.approx(10.00)
    assert round_to_tick(9.999, 0.01) == pytest.approx(10.00)
    assert round_to_tick(11.0, 0.01) == pytest.approx(11.0)


def test_round_to_tick_rejects_bad_tick_and_non_finite():
    with pytest.raises(ValueError):
        round_to_tick(10.0, 0.0)
    with pytest.raises(ValueError):
        round_to_tick(math.inf, 0.01)
    with pytest.raises(ValueError):
        round_to_tick(math.nan, 0.01)


# ---------- round_down_to_lot ----------


def test_round_down_to_lot_floors_to_lots():
    assert round_down_to_lot(250, 100) == 200
    assert round_down_to_lot(99, 100) == 0
    assert round_down_to_lot(100, 100) == 100
    assert round_down_to_lot(0, 100) == 0
    assert round_down_to_lot(-5, 100) == 0


def test_round_down_to_lot_rejects_non_positive_lot():
    with pytest.raises(ValueError):
        round_down_to_lot(100, 0)


# ---------- affordable_lot_quantity ----------


def test_affordable_lot_quantity_excludes_fees_only():
    # 1000 元 / 10 元 = 100 股 = 1 手（此处不含费用，费用由 backtest 处理）
    assert affordable_lot_quantity(1000, 10.0, 100) == 100
    # 999 元买不起 1 手
    assert affordable_lot_quantity(999, 10.0, 100) == 0
    # 2050 / (10 * 100) -> 2 手
    assert affordable_lot_quantity(2050, 10.0, 100) == 200


def test_affordable_lot_quantity_handles_non_positive_inputs():
    assert affordable_lot_quantity(0, 10.0, 100) == 0
    assert affordable_lot_quantity(1000, 0.0, 100) == 0
    assert affordable_lot_quantity(-100, 10.0, 100) == 0


# ---------- limit_prices ----------


def test_limit_prices_none_when_rule_not_configured():
    # 规则未配置：不得假装检测成功
    assert limit_prices(10.0, None) is None


def test_limit_prices_none_when_prev_close_unusable():
    limit = PriceLimit(0.10)
    assert limit_prices(None, limit) is None
    assert limit_prices(0.0, limit) is None
    assert limit_prices(-1.0, limit) is None
    assert limit_prices(math.nan, limit) is None
    assert limit_prices(math.inf, limit) is None


def test_limit_prices_computes_rounded_bounds():
    limit = PriceLimit(0.10, tick_size=0.01)
    lower, upper = limit_prices(10.0, limit)
    assert lower == pytest.approx(9.00)
    assert upper == pytest.approx(11.00)

    lower2, upper2 = limit_prices(10.03, limit)
    assert lower2 == pytest.approx(9.03)  # 10.03*0.9=9.027 -> 9.03
    assert upper2 == pytest.approx(11.03)  # 10.03*1.1=11.033 -> 11.03


# ---------- sellable_quantity ----------


def test_sellable_quantity_enforces_t_plus_one():
    rules = a_share_rules()
    assert rules.t_plus_one is True
    assert sellable_quantity(position=1000, bought_today=0, rules=rules) == 1000
    assert sellable_quantity(position=1000, bought_today=1000, rules=rules) == 0
    assert sellable_quantity(position=1000, bought_today=400, rules=rules) == 600


def test_sellable_quantity_without_t_plus_one_returns_all():
    from deepquant.market.rules import MarketRules

    rules = MarketRules(t_plus_one=False)
    assert sellable_quantity(position=1000, bought_today=1000, rules=rules) == 1000


def test_sellable_quantity_rejects_invalid_inputs():
    rules = a_share_rules()
    with pytest.raises(ValueError):
        sellable_quantity(position=-1, bought_today=0, rules=rules)
    with pytest.raises(ValueError):
        sellable_quantity(position=100, bought_today=-1, rules=rules)
    with pytest.raises(ValueError):
        sellable_quantity(position=100, bought_today=200, rules=rules)


# ---------- open_fill_block_reason ----------


def test_block_reason_no_price_for_missing_or_invalid_open():
    boundary = (9.00, 11.00)
    assert open_fill_block_reason(Side.BUY, None, boundary) == BLOCK_NO_PRICE
    assert open_fill_block_reason(Side.BUY, math.nan, boundary) == BLOCK_NO_PRICE
    assert open_fill_block_reason(Side.BUY, math.inf, boundary) == BLOCK_NO_PRICE
    assert open_fill_block_reason(Side.BUY, 0.0, boundary) == BLOCK_NO_PRICE
    assert open_fill_block_reason(Side.SELL, None, None) == BLOCK_NO_PRICE


def test_block_reason_buy_is_blocked_at_limit_up():
    boundary = (9.00, 11.00)
    assert open_fill_block_reason(Side.BUY, 11.00, boundary) == BLOCK_LIMIT_UP
    assert open_fill_block_reason(Side.BUY, 12.00, boundary) == BLOCK_LIMIT_UP
    assert open_fill_block_reason(Side.BUY, 10.50, boundary) is None


def test_block_reason_sell_is_blocked_at_limit_down():
    boundary = (9.00, 11.00)
    assert open_fill_block_reason(Side.SELL, 9.00, boundary) == BLOCK_LIMIT_DOWN
    assert open_fill_block_reason(Side.SELL, 8.50, boundary) == BLOCK_LIMIT_DOWN
    # 涨停卖出不受阻（涨停时卖出通常更易成交）
    assert open_fill_block_reason(Side.SELL, 11.00, boundary) is None
    # 跌停买入不受阻
    assert open_fill_block_reason(Side.BUY, 9.00, boundary) is None


def test_block_reason_skips_limit_check_when_boundary_unknown():
    # 规则未配置（boundary=None）：不做涨跌停判定，但价格非法仍按 no_price
    assert open_fill_block_reason(Side.BUY, 11.00, None) is None
    assert open_fill_block_reason(Side.BUY, None, None) == BLOCK_NO_PRICE
