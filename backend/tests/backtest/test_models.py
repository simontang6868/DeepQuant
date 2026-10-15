"""回测核心数据结构测试：创建、校验、关联、时序、估值状态、market 兼容。"""

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.models import (
    AccountState,
    BacktestResult,
    Fees,
    Fill,
    Order,
    OrderStatus,
    Position,
    ValuationStatus,
    assess_completeness,
    find_consistency_issues,
)
from deepquant.market.rules import MarketRules, Side, a_share_rules

D1 = pd.Timestamp("2024-01-02")
D2 = pd.Timestamp("2024-01-03")


def make_order(**overrides) -> Order:
    kwargs = dict(
        order_id="o1",
        symbol="600000",
        side=Side.BUY,
        signal_date=D1,
        order_date=D1,
        planned_exec_date=D2,
        quantity=100,
    )
    kwargs.update(overrides)
    return Order(**kwargs)


def make_fill(**overrides) -> Fill:
    kwargs = dict(
        fill_id="f1",
        order_id="o1",
        symbol="600000",
        side=Side.BUY,
        fill_date=D2,
        price=10.0,
        quantity=100,
    )
    kwargs.update(overrides)
    return Fill(**kwargs)


# ---------- Fees ----------


def test_fees_total_and_defaults():
    assert Fees().total == 0.0
    fees = Fees(commission=5.0, stamp_duty=0.5, transfer_fee=0.1)
    assert fees.total == pytest.approx(5.6)


@pytest.mark.parametrize("field_name", ["commission", "stamp_duty", "transfer_fee"])
def test_fees_reject_negative(field_name):
    with pytest.raises(ValueError):
        Fees(**{field_name: -1.0})


# ---------- Order ----------


def test_order_created_with_timing_fields():
    order = make_order()
    assert order.status is OrderStatus.PENDING
    assert order.signal_date == D1
    assert order.order_date == D1
    assert order.planned_exec_date == D2
    assert order.quantity == 100
    assert order.reason is None
    assert order.fill_id is None


@pytest.mark.parametrize("quantity", [0, -100, 1.5, True])
def test_order_rejects_invalid_quantity(quantity):
    with pytest.raises(ValueError):
        make_order(quantity=quantity)


@pytest.mark.parametrize("order_id", ["", "   ", None, 7])
def test_order_rejects_invalid_id(order_id):
    with pytest.raises(ValueError):
        make_order(order_id=order_id)


def test_order_rejects_non_side_value():
    with pytest.raises(ValueError):
        make_order(side="BUY")


def test_order_rejects_nat_date():
    with pytest.raises(ValueError):
        make_order(signal_date=pd.NaT)


def test_order_rejects_same_day_execution():
    # 不得用 T 日价格成交：计划执行日必须晚于信号日
    with pytest.raises(ValueError):
        make_order(signal_date=D1, order_date=D1, planned_exec_date=D1)


def test_order_rejects_exec_before_order_date():
    with pytest.raises(ValueError):
        make_order(signal_date=D1, order_date=D2, planned_exec_date=D1)


# ---------- Fill ----------


def test_fill_created_with_gross_amount():
    fill = make_fill(fees=Fees(commission=5.0))
    assert fill.price == pytest.approx(10.0)
    assert fill.quantity == 100
    assert fill.gross_amount == pytest.approx(1000.0)
    assert fill.fees.total == pytest.approx(5.0)


@pytest.mark.parametrize("price", [0, -1.0, float("nan"), float("inf"), "10"])
def test_fill_rejects_invalid_price(price):
    with pytest.raises(ValueError):
        make_fill(price=price)


@pytest.mark.parametrize("quantity", [0, -1, 1.5, True])
def test_fill_rejects_invalid_quantity(quantity):
    with pytest.raises(ValueError):
        make_fill(quantity=quantity)


def test_fill_rejects_nat_fill_date():
    with pytest.raises(ValueError):
        make_fill(fill_date=pd.NaT)


def test_fill_rejects_bad_fees_type():
    with pytest.raises(ValueError):
        make_fill(fees=5.0)  # type: ignore[arg-type]


# ---------- Position / market 兼容 ----------


def test_position_defaults_and_validation():
    pos = Position(symbol="600000")
    assert pos.quantity == 0 and pos.bought_today == 0 and pos.avg_cost is None
    with pytest.raises(ValueError):
        Position(symbol="600000", quantity=-1)
    with pytest.raises(ValueError):
        Position(symbol="600000", quantity=100, bought_today=200)
    with pytest.raises(ValueError):
        Position(symbol="600000", avg_cost=-0.1)


def test_position_sellable_reuses_market_rules():
    pos = Position(symbol="600000", quantity=1000, bought_today=400)
    assert pos.sellable(a_share_rules()) == 600  # T+1
    assert pos.sellable(MarketRules(t_plus_one=False)) == 1000  # 非 T+1


# ---------- AccountState / 估值不变量 ----------


def test_account_state_ok_with_position():
    state = AccountState(
        date=D1,
        cash=4000.0,
        position=Position(symbol="600000", quantity=100),
        close=10.0,
        market_value=1000.0,
        total_equity=5000.0,
        valuation_status=ValuationStatus.OK,
    )
    assert state.total_equity == pytest.approx(5000.0)


def test_account_state_empty_position_needs_no_close():
    state = AccountState(
        date=D1,
        cash=5000.0,
        position=Position(symbol="600000", quantity=0),
        close=None,
        market_value=0.0,
        total_equity=5000.0,
        valuation_status=ValuationStatus.OK,
    )
    assert state.close is None
    assert state.market_value == 0.0


def test_account_state_missing_close_keeps_equity_none():
    state = AccountState(
        date=D1,
        cash=4000.0,
        position=Position(symbol="600000", quantity=100),
        close=None,
        market_value=None,
        total_equity=None,
        valuation_status=ValuationStatus.MISSING_CLOSE,
    )
    assert state.market_value is None
    assert state.total_equity is None


def test_account_state_missing_close_must_not_record_zero_value():
    # 不得把缺失收盘价对应的持仓市值直接记为零
    with pytest.raises(ValueError):
        AccountState(
            date=D1,
            cash=4000.0,
            position=Position(symbol="600000", quantity=100),
            close=None,
            market_value=0.0,
            total_equity=None,
            valuation_status=ValuationStatus.MISSING_CLOSE,
        )
    # 也不得虚构权益
    with pytest.raises(ValueError):
        AccountState(
            date=D1,
            cash=4000.0,
            position=Position(symbol="600000", quantity=100),
            close=None,
            market_value=None,
            total_equity=4000.0,
            valuation_status=ValuationStatus.MISSING_CLOSE,
        )


def test_account_state_empty_position_cannot_be_missing_close():
    with pytest.raises(ValueError):
        AccountState(
            date=D1,
            cash=4000.0,
            position=Position(symbol="600000", quantity=0),
            close=None,
            market_value=None,
            total_equity=None,
            valuation_status=ValuationStatus.MISSING_CLOSE,
        )


def test_account_state_ok_requires_consistent_equity():
    with pytest.raises(ValueError):
        AccountState(
            date=D1,
            cash=4000.0,
            position=Position(symbol="600000", quantity=100),
            close=10.0,
            market_value=999.0,  # 应为 1000
            total_equity=4999.0,
            valuation_status=ValuationStatus.OK,
        )


# ---------- 订单 / 成交 一致性 ----------


def test_consistency_ok_for_matched_pair():
    order = make_order(status=OrderStatus.FILLED, fill_id="f1")
    fill = make_fill()
    assert find_consistency_issues([order], [fill]) == []


def test_consistency_detects_duplicate_ids():
    issues = find_consistency_issues([make_order(), make_order()], [make_fill(), make_fill()])
    assert any("重复的 order_id" in i for i in issues)
    assert any("重复的 fill_id" in i for i in issues)


def test_consistency_detects_fill_referencing_missing_order():
    issues = find_consistency_issues([], [make_fill(order_id="ghost")])
    assert any("不存在的订单" in i for i in issues)


def test_consistency_detects_fill_quantity_exceeding_order():
    order = make_order(quantity=100, status=OrderStatus.REJECTED)
    fill = make_fill(quantity=200)
    issues = find_consistency_issues([order], [fill])
    assert any("超过订单剩余数量" in i for i in issues)


def test_consistency_detects_symbol_and_side_mismatch():
    order = make_order(symbol="600000", side=Side.BUY, status=OrderStatus.FILLED, fill_id="f1")
    fill = make_fill(symbol="000001", side=Side.SELL)
    issues = find_consistency_issues([order], [fill])
    assert any("证券代码不一致" in i for i in issues)
    assert any("方向不一致" in i for i in issues)


def test_consistency_detects_filled_order_without_fill():
    order = make_order(status=OrderStatus.FILLED, fill_id="f1")
    issues = find_consistency_issues([order], [])
    assert any("没有对应成交" in i for i in issues)


def test_consistency_detects_non_filled_order_with_fill_id():
    order = make_order(status=OrderStatus.REJECTED, fill_id="f1")
    issues = find_consistency_issues([order], [make_fill()])
    assert any("却关联了 fill_id" in i for i in issues)


# ---------- 完整性评估 ----------


def test_assess_completeness_all_ok():
    states = [
        AccountState(
            date=D1,
            cash=5000.0,
            position=Position(symbol="600000", quantity=0),
            close=None,
            market_value=0.0,
            total_equity=5000.0,
            valuation_status=ValuationStatus.OK,
        )
    ]
    assert assess_completeness(states) == (True, [])


def test_assess_completeness_flags_missing_equity():
    states = [
        AccountState(
            date=D1,
            cash=5000.0,
            position=Position(symbol="600000", quantity=0),
            close=None,
            market_value=0.0,
            total_equity=5000.0,
            valuation_status=ValuationStatus.OK,
        ),
        AccountState(
            date=D2,
            cash=4000.0,
            position=Position(symbol="600000", quantity=100),
            close=None,
            market_value=None,
            total_equity=None,
            valuation_status=ValuationStatus.MISSING_CLOSE,
        ),
    ]
    is_complete, reasons = assess_completeness(states)
    assert is_complete is False
    assert len(reasons) == 1
    assert "2024-01-03" in reasons[0]


def test_assess_completeness_empty_is_incomplete():
    is_complete, reasons = assess_completeness([])
    assert is_complete is False
    assert reasons


# ---------- BacktestResult ----------


def test_backtest_result_defaults_incomplete():
    result = BacktestResult(config=BacktestConfig(symbol="600000", initial_cash=100000.0))
    assert result.is_complete is False
    assert result.orders == [] and result.fills == [] and result.account_states == []
    assert result.schema_version == "0.1.0"


def test_backtest_result_consistency_issues_helper():
    result = BacktestResult(
        config=BacktestConfig(symbol="600000", initial_cash=100000.0),
        orders=[make_order(status=OrderStatus.FILLED, fill_id="f1")],
        fills=[make_fill()],
    )
    assert result.consistency_issues() == []


def test_backtest_result_rejects_bad_period():
    with pytest.raises(ValueError):
        BacktestResult(
            config=BacktestConfig(symbol="600000", initial_cash=100000.0),
            period_start="2024-03-01",
            period_end="2024-01-02",
        )
