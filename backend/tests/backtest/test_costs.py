"""费用与滑点计算测试：公式、最低佣金、方向差异、取整、精度、异常与副作用。"""

import math

import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.costs import (
    DEFAULT_PRICE_TICK,
    MONEY_DECIMALS,
    PriceAdjustment,
    adjust_price,
    apply_slippage,
    compute_fees,
    execution_price,
    fee_model_notes,
    round_money,
)
from deepquant.backtest.models import Fees
from deepquant.market.rules import Side


def make_config(**overrides) -> BacktestConfig:
    kwargs = dict(symbol="600000", initial_cash=1_000_000.0)
    kwargs.update(overrides)
    return BacktestConfig(**kwargs)


# ---------- round_money（金额精度：半值向上） ----------


@pytest.mark.parametrize(
    "value,expected",
    [
        (0.004, 0.00),
        (0.005, 0.01),  # 半值向上，非银行家舍入
        (1.005, 1.01),
        (2.675, 2.68),
        (30.0, 30.00),
        (0.0, 0.0),
    ],
)
def test_round_money_half_up(value, expected):
    assert round_money(value) == pytest.approx(expected)


def test_round_money_rejects_non_finite():
    for bad in (math.nan, math.inf, -math.inf, "1.0", None):
        with pytest.raises(ValueError):
            round_money(bad)


def test_money_decimal_constant():
    assert MONEY_DECIMALS == 2


# ---------- compute_fees ----------


def test_buy_fees_with_default_placeholder_rates():
    # gross = 100 × 1000 = 100000；佣金 = max(30, 5) = 30；买入无印花税
    fees = compute_fees(Side.BUY, price=100.0, quantity=1000, config=make_config())
    assert isinstance(fees, Fees)
    assert fees.commission == pytest.approx(30.0)
    assert fees.stamp_duty == pytest.approx(0.0)
    assert fees.transfer_fee == pytest.approx(0.0)
    assert fees.total == pytest.approx(30.0)


def test_sell_fees_include_stamp_duty():
    # 卖出：佣金 30 + 印花税 100000×0.0005 = 50
    fees = compute_fees(Side.SELL, price=100.0, quantity=1000, config=make_config())
    assert fees.commission == pytest.approx(30.0)
    assert fees.stamp_duty == pytest.approx(50.0)
    assert fees.transfer_fee == pytest.approx(0.0)
    assert fees.total == pytest.approx(80.0)


def test_buy_and_sell_difference_is_stamp_duty_only():
    config = make_config()
    buy = compute_fees(Side.BUY, 100.0, 1000, config)
    sell = compute_fees(Side.SELL, 100.0, 1000, config)
    assert sell.total - buy.total == pytest.approx(50.0)  # 仅印花税差异


def test_min_commission_applies_on_small_trade():
    # gross = 10 × 100 = 1000；佣金 = max(0.3, 5) = 5
    fees = compute_fees(Side.BUY, price=10.0, quantity=100, config=make_config())
    assert fees.commission == pytest.approx(5.0)


def test_commission_uses_rate_when_above_minimum():
    # gross = 100000；30 > 5，最低佣金不生效
    fees = compute_fees(Side.BUY, price=100.0, quantity=1000, config=make_config())
    assert fees.commission == pytest.approx(100000 * 0.0003)


def test_transfer_fee_applies_both_directions():
    config = make_config(transfer_fee_rate=0.001)
    buy = compute_fees(Side.BUY, 100.0, 1000, config)
    sell = compute_fees(Side.SELL, 100.0, 1000, config)
    assert buy.transfer_fee == pytest.approx(100.0)
    assert sell.transfer_fee == pytest.approx(100.0)


def test_zero_rate_config_yields_zero_fees():
    config = make_config(
        commission_rate=0.0,
        min_commission=0.0,
        stamp_duty_rate=0.0,
        transfer_fee_rate=0.0,
    )
    for side in (Side.BUY, Side.SELL):
        fees = compute_fees(side, price=100.0, quantity=1000, config=config)
        assert fees.total == pytest.approx(0.0)


def test_small_trade_is_rounded_to_cent():
    # gross = 50，佣金率 0.0001 → 0.005 → 0.01（半值向上）
    config = make_config(commission_rate=0.0001, min_commission=0.0)
    fees = compute_fees(Side.BUY, price=1.0, quantity=50, config=config)
    assert fees.commission == pytest.approx(0.01)


@pytest.mark.parametrize("price", [0, -1.0, math.nan, math.inf, "10"])
def test_compute_fees_rejects_invalid_price(price):
    with pytest.raises(ValueError):
        compute_fees(Side.BUY, price=price, quantity=100, config=make_config())


@pytest.mark.parametrize("quantity", [0, -100, 1.5, True])
def test_compute_fees_rejects_invalid_quantity(quantity):
    with pytest.raises(ValueError):
        compute_fees(Side.BUY, price=10.0, quantity=quantity, config=make_config())


def test_compute_fees_rejects_invalid_side_and_config():
    with pytest.raises(ValueError):
        compute_fees("BUY", 10.0, 100, make_config())  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        compute_fees(Side.BUY, 10.0, 100, "config")  # type: ignore[arg-type]


def test_compute_fees_has_no_side_effects_and_is_deterministic():
    config = make_config()
    snapshot = make_config()
    first = compute_fees(Side.SELL, 100.0, 1000, config)
    second = compute_fees(Side.SELL, 100.0, 1000, config)
    assert first == second
    assert config == snapshot  # 配置未被修改


# ---------- apply_slippage ----------


def test_apply_slippage_buy_price_increases():
    assert apply_slippage(Side.BUY, base_price=10.0, slippage_rate=0.001) == pytest.approx(10.01)


def test_apply_slippage_sell_price_decreases():
    assert apply_slippage(Side.SELL, base_price=10.0, slippage_rate=0.001) == pytest.approx(9.99)


def test_apply_slippage_zero_rate_keeps_price():
    assert apply_slippage(Side.BUY, base_price=10.0, slippage_rate=0.0) == pytest.approx(10.0)
    assert apply_slippage(Side.SELL, base_price=10.0, slippage_rate=0.0) == pytest.approx(10.0)


def test_apply_slippage_rounds_to_tick():
    # 10.0 × 1.0006 = 10.006 → 就近取整到 10.01
    assert apply_slippage(Side.BUY, base_price=10.0, slippage_rate=0.0006) == pytest.approx(10.01)
    # 10.0 × 1.0004 = 10.004 → 就近取整到 10.00
    assert apply_slippage(Side.BUY, base_price=10.0, slippage_rate=0.0004) == pytest.approx(10.00)
    # 基准价本身不在 tick 上：9.996 → 10.00
    assert apply_slippage(Side.BUY, base_price=9.996, slippage_rate=0.0) == pytest.approx(10.00)
    assert DEFAULT_PRICE_TICK == pytest.approx(0.01)


def test_apply_slippage_exact_half_tick_is_deterministic():
    # 恰好落在半 tick 上时，结果由二进制浮点表示决定（此处 10.0×1.0005 略小于 10.005 → 10.00）；
    # 该边界情况已在 costs 模块文档中说明，且同一输入始终得到同一结果。
    value = apply_slippage(Side.BUY, base_price=10.0, slippage_rate=0.0005)
    assert value == apply_slippage(Side.BUY, base_price=10.0, slippage_rate=0.0005)
    assert 10.00 <= value <= 10.01


@pytest.mark.parametrize("base_price", [0, -1.0, math.nan, math.inf])
def test_apply_slippage_rejects_invalid_base_price(base_price):
    with pytest.raises(ValueError):
        apply_slippage(Side.BUY, base_price, 0.001)


@pytest.mark.parametrize("rate", [-0.001, math.nan, math.inf])
def test_apply_slippage_rejects_invalid_rate(rate):
    with pytest.raises(ValueError):
        apply_slippage(Side.BUY, 10.0, rate)


@pytest.mark.parametrize("rate", [1.0, 1.5])
def test_apply_slippage_rejects_sell_rate_making_price_invalid(rate):
    with pytest.raises(ValueError):
        apply_slippage(Side.SELL, 10.0, rate)


def test_apply_slippage_rejects_invalid_side():
    with pytest.raises(ValueError):
        apply_slippage("BUY", 10.0, 0.001)  # type: ignore[arg-type]


# ---------- adjust_price / execution_price / PriceAdjustment ----------


def test_adjust_price_returns_structured_result():
    config = make_config(slippage_buy=0.001, slippage_sell=0.002)
    buy = adjust_price(Side.BUY, 10.0, config)
    assert isinstance(buy, PriceAdjustment)
    assert buy.base_price == pytest.approx(10.0)
    assert buy.adjusted_price == pytest.approx(10.01)
    assert buy.slippage_rate == pytest.approx(0.001)
    assert buy.slippage_per_share == pytest.approx(0.01)

    sell = adjust_price(Side.SELL, 10.0, config)
    assert sell.adjusted_price == pytest.approx(9.98)
    assert sell.slippage_per_share == pytest.approx(-0.02)


def test_execution_price_matches_adjust_price():
    config = make_config(slippage_buy=0.003)
    assert execution_price(Side.BUY, 10.0, config) == pytest.approx(
        adjust_price(Side.BUY, 10.0, config).adjusted_price
    )


def test_adjust_price_rejects_bad_config():
    with pytest.raises(ValueError):
        adjust_price(Side.BUY, 10.0, "config")  # type: ignore[arg-type]


def test_price_adjustment_rejects_invalid_fields():
    with pytest.raises(ValueError):
        PriceAdjustment(side=Side.BUY, base_price=0.0, adjusted_price=10.0, slippage_rate=0.0)
    with pytest.raises(ValueError):
        PriceAdjustment(side=Side.BUY, base_price=10.0, adjusted_price=10.0, slippage_rate=-0.1)


# ---------- 规则说明保留 fee_source 语义 ----------


def test_fee_model_notes_keep_placeholder_semantics():
    notes = fee_model_notes(make_config())
    joined = " ".join(notes)
    assert "simulation_placeholder" in joined
    assert "印花税仅卖出" in joined
    assert "最低佣金" in joined
    assert "不代表" in joined
