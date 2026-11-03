"""订单状态机：非法转移、终态冻结、成交数量与时间约束。"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.eventloop.orders import Order, OrderStateError, OrderStatus, OrderType
from deepquant.market.rules import Side

T0 = pd.Timestamp("2024-01-02 15:00")
T1 = pd.Timestamp("2024-01-03 09:30")


def _order(**overrides) -> Order:
    payload = {
        "order_id": "o-1",
        "symbol": "TEST",
        "side": Side.BUY,
        "order_type": OrderType.MARKET,
        "quantity": 1000,
        "created_at": T0,
    }
    payload.update(overrides)
    return Order(**payload)


def test_limit_order_requires_price() -> None:
    with pytest.raises(ValueError, match="必须给出 limit_price"):
        _order(order_type=OrderType.LIMIT)


def test_market_order_must_not_carry_price() -> None:
    with pytest.raises(ValueError, match="不应携带 limit_price"):
        _order(limit_price=10.0)


def test_cannot_fill_before_acceptance() -> None:
    order = _order()
    with pytest.raises(OrderStateError, match="不可成交"):
        order.record_fill(100, 10.0, T1)


def test_illegal_transition_rejected() -> None:
    order = _order()
    order.accept(T1)
    with pytest.raises(OrderStateError, match="非法状态转移"):
        order.transition(OrderStatus.PENDING_NEW, T1)


def test_terminal_status_is_frozen() -> None:
    order = _order()
    order.accept(T1)
    order.cancel(T1, "user")
    assert order.is_terminal
    with pytest.raises(OrderStateError, match="非法状态转移"):
        order.accept(T1)


def test_fill_beyond_remaining_rejected() -> None:
    order = _order(quantity=100)
    order.accept(T1)
    with pytest.raises(OrderStateError, match="超过剩余"):
        order.record_fill(101, 10.0, T1)


def test_partial_fills_accumulate_and_average_price() -> None:
    order = _order(quantity=1000)
    order.accept(T1)
    order.record_fill(300, 10.0, T1)
    assert order.status is OrderStatus.PARTIALLY_FILLED
    order.record_fill(200, 11.0, T1)
    assert order.filled_quantity == 500
    assert order.remaining == 500
    assert order.avg_fill_price == pytest.approx((300 * 10.0 + 200 * 11.0) / 500)
    order.record_fill(500, 12.0, T1)
    assert order.status is OrderStatus.FILLED
    assert order.remaining == 0


def test_transition_time_cannot_move_backwards() -> None:
    order = _order()
    order.accept(T1)
    with pytest.raises(OrderStateError, match="时间倒退"):
        order.cancel(pd.Timestamp("2024-01-02 10:00"), "backdated")


def test_reject_records_reason_in_history() -> None:
    order = _order()
    order.reject(T1, "limit_up")
    assert order.status is OrderStatus.REJECTED
    assert order.reason == "limit_up"
    assert [item.status for item in order.history] == [
        OrderStatus.PENDING_NEW,
        OrderStatus.REJECTED,
    ]


def test_zero_or_negative_fill_quantity_rejected() -> None:
    order = _order()
    order.accept(T1)
    with pytest.raises(OrderStateError, match="正整数"):
        order.record_fill(0, 10.0, T1)
    with pytest.raises(OrderStateError, match="正整数"):
        order.record_fill(-5, 10.0, T1)


def test_avg_fill_price_is_none_before_any_fill() -> None:
    assert _order().avg_fill_price is None
