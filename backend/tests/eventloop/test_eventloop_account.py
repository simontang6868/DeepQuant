"""账户记账：T+1、费用来源、估值缺失处理、不变量自检。"""

from __future__ import annotations

import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.costs import compute_fees
from deepquant.eventloop.account import (
    Account,
    AccountError,
    Holding,
    InsufficientCash,
    InsufficientPosition,
    MissingPriceError,
)
from deepquant.market.rules import Side


def _config(cash: float = 100_000.0) -> BacktestConfig:
    return BacktestConfig(symbol="TEST", initial_cash=cash)


def _account(cash: float = 100_000.0) -> Account:
    return Account(cash=cash, config=_config(cash))


def test_same_day_sell_blocked_until_settle() -> None:
    account = _account()
    account.buy("TEST", 100, 10.0)
    assert account.available("TEST") == 0
    with pytest.raises(InsufficientPosition, match="T\\+1"):
        account.sell("TEST", 100, 10.0)
    account.settle()
    assert account.available("TEST") == 100
    account.sell("TEST", 100, 10.0)
    assert account.quantity("TEST") == 0


def test_fees_are_exactly_compute_fees_output() -> None:
    """费用不得有第二份公式：与 backtest.costs.compute_fees 逐项相等。"""
    account = _account()
    config = _config()
    fees = account.buy("TEST", 100, 10.0)
    expected = compute_fees(Side.BUY, 10.0, 100, config)
    assert (fees.commission, fees.stamp_duty, fees.transfer_fee) == (
        expected.commission,
        expected.stamp_duty,
        expected.transfer_fee,
    )
    # 默认占位参数下：佣金取 5 元下限，买入无印花税
    assert fees.commission == 5.0
    assert fees.stamp_duty == 0.0
    assert account.cash == pytest.approx(100_000.0 - 1_000.0 - 5.0)


def test_sell_charges_stamp_duty_and_returns_net_cash() -> None:
    account = _account()
    account.buy("TEST", 100, 10.0)
    account.settle()
    fees = account.sell("TEST", 100, 10.0)
    assert fees.stamp_duty == pytest.approx(0.5)
    assert account.cash == pytest.approx(100_000.0 - 1_005.0 + 1_000.0 - 5.5)


def test_insufficient_cash_raises_instead_of_clamping() -> None:
    account = _account(cash=1_000.0)
    with pytest.raises(InsufficientCash):
        account.buy("TEST", 100, 10.0)
    assert account.cash == 1_000.0
    assert account.quantity("TEST") == 0


def test_missing_price_refuses_to_substitute_zero() -> None:
    account = _account()
    account.buy("TEST", 100, 10.0)
    with pytest.raises(MissingPriceError, match="拒绝以 0 或成本价顶替"):
        account.equity({})
    with pytest.raises(MissingPriceError, match="非正"):
        account.equity({"TEST": 0.0})


def test_market_value_and_equity() -> None:
    account = _account()
    account.buy("TEST", 100, 10.0)
    assert account.market_value({"TEST": 12.0}) == pytest.approx(1_200.0)
    assert account.equity({"TEST": 12.0}) == pytest.approx(100_195.0)
    snapshot = account.snapshot({"TEST": 12.0})
    assert snapshot["positions"] == {"TEST": 100}
    assert snapshot["equity"] == pytest.approx(100_195.0)


def test_cost_is_released_proportionally_on_partial_sell() -> None:
    account = _account()
    account.buy("TEST", 200, 10.0)
    account.settle()
    cost_before = account.holdings["TEST"].cost
    account.sell("TEST", 100, 10.0)
    assert account.holdings["TEST"].cost == pytest.approx(cost_before / 2)
    account.settle()
    account.sell("TEST", 100, 10.0)
    assert account.holdings["TEST"].cost == 0.0


def test_invariants_catch_corrupted_state() -> None:
    account = _account()
    assert account.check_invariants() == []
    account.holdings["BAD"] = Holding(quantity=-5, available=0, cost=0.0)
    problems = account.check_invariants()
    assert any("持仓为负" in item for item in problems)
    account.holdings["ODD"] = Holding(quantity=10, available=20, cost=100.0)
    assert any("超过持仓" in item for item in account.check_invariants())


def test_negative_cash_rejected_at_construction() -> None:
    with pytest.raises(ValueError, match="非负"):
        Account(cash=-1.0, config=_config())


def test_invalid_quantity_rejected() -> None:
    account = _account()
    with pytest.raises(AccountError, match="正整数"):
        account.buy("TEST", 0, 10.0)
    with pytest.raises(AccountError, match="有限正数"):
        account.buy("TEST", 100, 0.0)
