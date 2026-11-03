"""撮合：价格可及性、额度共享、排队位置、部分成交、封板与停牌。"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.eventloop.impact import FRICTIONLESS, SlippageMode, SlippageModel
from deepquant.eventloop.matching import (
    LIMIT_DOWN,
    LIMIT_UP,
    LIQUIDITY_CAP,
    NO_TOUCH,
    QUEUE_EXHAUSTED,
    SUSPENDED,
    Bar,
    MatchingError,
    MatchingParams,
    LiquidityLedger,
    match_on_close,
    match_on_open,
    new_ledger,
)
from deepquant.eventloop.orders import Order, OrderType
from deepquant.market.rules import Side

DAY = pd.Timestamp("2024-01-02")
PARAMS = MatchingParams(participation_limit=0.10, queue_position=0.5)


def _bar(**overrides) -> Bar:
    payload = {
        "day": DAY,
        "open": 10.0,
        "high": 10.5,
        "low": 9.5,
        "close": 10.2,
        "volume": 1_000_000.0,
    }
    payload.update(overrides)
    return Bar(**payload)


def _order(side: Side = Side.BUY, quantity: int = 10_000, **overrides) -> Order:
    payload = {
        "order_id": "o-1",
        "symbol": "T",
        "side": side,
        "order_type": OrderType.MARKET,
        "quantity": quantity,
        "created_at": pd.Timestamp("2024-01-01 15:00"),
    }
    payload.update(overrides)
    return Order(**payload)


def test_bar_rejects_contradictory_ohlc() -> None:
    with pytest.raises(MatchingError, match="high"):
        _bar(high=9.0, open=10.0)
    with pytest.raises(MatchingError, match="low"):
        _bar(low=11.0)


def test_market_buy_fills_at_open_within_capacity() -> None:
    bar = _bar()
    ledger = new_ledger(bar, PARAMS)
    assert ledger.capacity == pytest.approx(100_000.0)
    result = match_on_open(_order(quantity=50_000), bar, PARAMS, ledger, FRICTIONLESS)
    assert result.filled_quantity == 50_000
    assert result.fill_price == pytest.approx(10.0)
    assert ledger.consumed == pytest.approx(50_000.0)


def test_capacity_is_shared_across_orders() -> None:
    """同一 bar 内两笔订单竞争同一份额度：不能各自都拿满。"""
    bar = _bar()
    ledger = new_ledger(bar, PARAMS)
    first = match_on_open(_order(quantity=80_000), bar, PARAMS, ledger, FRICTIONLESS)
    second = match_on_open(_order(order_id="o-2", quantity=80_000), bar, PARAMS, ledger, FRICTIONLESS)
    assert first.filled_quantity == 80_000
    assert second.filled_quantity == 20_000  # 只剩 2 万股额度
    assert ledger.consumed == pytest.approx(100_000.0)


def test_capacity_exhausted_yields_no_fill() -> None:
    bar = _bar()
    ledger = new_ledger(bar, PARAMS)
    ledger.consume(100_000.0)
    result = match_on_open(_order(), bar, PARAMS, ledger, FRICTIONLESS)
    assert result.filled_quantity == 0
    assert result.reason == LIQUIDITY_CAP


def test_partial_fill_rounds_down_to_lot() -> None:
    bar = _bar(volume=1_000_000.0)
    params = MatchingParams(participation_limit=0.001, queue_position=0.0)  # 额度 1000 股
    ledger = new_ledger(bar, params)
    result = match_on_open(_order(quantity=10_000), bar, params, ledger, FRICTIONLESS)
    assert result.filled_quantity == 1_000
    assert result.filled_quantity % 100 == 0


def test_no_partial_mode_fills_all_or_nothing() -> None:
    bar = _bar(volume=1_000_000.0)
    params = MatchingParams(participation_limit=0.001, queue_position=0.0, allow_partial=False)
    ledger = new_ledger(bar, params)
    result = match_on_open(_order(quantity=10_000), bar, params, ledger, FRICTIONLESS)
    assert result.filled_quantity == 0
    assert result.reason == LIQUIDITY_CAP


def test_limit_buy_not_touched_does_not_fill() -> None:
    bar = _bar(open=10.0, low=9.8)
    ledger = new_ledger(bar, PARAMS)
    order = _order(order_type=OrderType.LIMIT, limit_price=9.0)
    assert match_on_open(order, bar, PARAMS, ledger, FRICTIONLESS).reason == NO_TOUCH
    assert match_on_close(order, bar, PARAMS, ledger, FRICTIONLESS).reason == NO_TOUCH


def test_limit_buy_crossing_at_open_uses_open_price() -> None:
    """开盘价已优于限价：按开盘价成交（对买方更有利），不按限价。"""
    bar = _bar(open=9.5, low=9.3)
    ledger = new_ledger(bar, PARAMS)
    order = _order(order_type=OrderType.LIMIT, limit_price=10.0)
    result = match_on_open(order, bar, PARAMS, ledger, FRICTIONLESS)
    assert result.filled_quantity == 10_000
    assert result.fill_price == pytest.approx(9.5)


def test_limit_buy_touched_intrabar_fills_at_limit_not_at_low() -> None:
    """盘中触价按**限价**成交，不把当天最低价当成自己能拿到的价格。"""
    bar = _bar(open=10.2, high=10.4, low=9.0)
    ledger = new_ledger(bar, PARAMS)
    order = _order(order_type=OrderType.LIMIT, limit_price=9.9)
    assert match_on_open(order, bar, PARAMS, ledger, FRICTIONLESS).reason == NO_TOUCH
    result = match_on_close(order, bar, PARAMS, ledger, FRICTIONLESS)
    assert result.filled_quantity > 0
    assert result.fill_price == pytest.approx(9.9)


def test_queue_position_one_blocks_touch_fills() -> None:
    bar = _bar(open=10.2, low=9.0)
    params = MatchingParams(participation_limit=0.10, queue_position=1.0)
    ledger = new_ledger(bar, params)
    order = _order(order_type=OrderType.LIMIT, limit_price=9.9)
    result = match_on_close(order, bar, params, ledger, FRICTIONLESS)
    assert result.filled_quantity == 0
    assert result.reason == QUEUE_EXHAUSTED


def test_limit_up_blocks_buy_by_price_and_by_flag() -> None:
    by_price = _bar(open=11.0, high=11.0, low=11.0, close=11.0, limit_up=11.0, limit_down=9.0)
    assert match_on_open(_order(), by_price, PARAMS, new_ledger(by_price, PARAMS), FRICTIONLESS).reason == LIMIT_UP
    by_flag = _bar(limit_up_hit=True)
    assert match_on_open(_order(), by_flag, PARAMS, new_ledger(by_flag, PARAMS), FRICTIONLESS).reason == LIMIT_UP


def test_limit_down_blocks_sell_only() -> None:
    bar = _bar(open=9.0, high=9.0, low=9.0, close=9.0, limit_up=11.0, limit_down=9.0)
    sell = match_on_open(_order(side=Side.SELL), bar, PARAMS, new_ledger(bar, PARAMS), FRICTIONLESS)
    assert sell.reason == LIMIT_DOWN
    buy = match_on_open(_order(side=Side.BUY), bar, PARAMS, new_ledger(bar, PARAMS), FRICTIONLESS)
    assert buy.filled_quantity == 10_000


def test_suspended_bar_blocks_everything() -> None:
    bar = _bar(tradable=False, volume=0.0)
    result = match_on_open(_order(), bar, PARAMS, new_ledger(bar, PARAMS), FRICTIONLESS)
    assert result.reason == SUSPENDED
    assert result.blocked


def test_market_order_is_not_retried_at_close() -> None:
    bar = _bar()
    ledger = new_ledger(bar, PARAMS)
    ledger.consume(100_000.0)
    order = _order()
    assert match_on_open(order, bar, PARAMS, ledger, FRICTIONLESS).reason == LIQUIDITY_CAP
    retry = match_on_close(order, bar, PARAMS, ledger, FRICTIONLESS)
    assert retry.filled_quantity == 0
    assert retry.detail["note"] == "market_order_not_retried"


def test_slippage_moves_price_against_trader() -> None:
    bar = _bar()
    params = MatchingParams(participation_limit=1.0, queue_position=0.0)
    slip = SlippageModel(mode=SlippageMode.CONSTANT, bps=100.0)  # 1%
    buy = match_on_open(_order(quantity=1_000), bar, params, new_ledger(bar, params), slip)
    sell = match_on_open(_order(side=Side.SELL, quantity=1_000), bar, params, new_ledger(bar, params), slip)
    assert buy.fill_price == pytest.approx(10.1)
    assert sell.fill_price == pytest.approx(9.9)


def test_params_reject_out_of_range_values() -> None:
    with pytest.raises(MatchingError, match="participation_limit"):
        MatchingParams(participation_limit=0.0)
    with pytest.raises(MatchingError, match="queue_position"):
        MatchingParams(queue_position=1.5)


def test_new_ledger_gives_zero_capacity_when_suspended() -> None:
    bar = _bar(tradable=False)
    assert new_ledger(bar, PARAMS).capacity == 0.0
