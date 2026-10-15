"""BacktestConfig 与执行枚举的测试。"""

from dataclasses import FrozenInstanceError

import pandas as pd
import pytest

from deepquant.backtest.config import (
    DEFAULT_ASSUMPTIONS,
    FEE_SOURCE_SIMULATION,
    SIMULATION_COMMISSION_RATE,
    SIMULATION_MIN_COMMISSION,
    SIMULATION_SLIPPAGE_BUY,
    SIMULATION_SLIPPAGE_SELL,
    SIMULATION_STAMP_DUTY_RATE,
    SIMULATION_TRANSFER_FEE_RATE,
    BacktestConfig,
    ExecutionPrice,
)
from deepquant.market.rules import MarketRules, a_share_rules


def test_default_config_uses_simulation_placeholder_fees():
    cfg = BacktestConfig(symbol="600000", initial_cash=100000.0)
    assert cfg.commission_rate == pytest.approx(SIMULATION_COMMISSION_RATE)
    assert cfg.min_commission == pytest.approx(SIMULATION_MIN_COMMISSION)
    assert cfg.stamp_duty_rate == pytest.approx(SIMULATION_STAMP_DUTY_RATE)
    assert cfg.transfer_fee_rate == pytest.approx(SIMULATION_TRANSFER_FEE_RATE)
    assert cfg.slippage_buy == pytest.approx(SIMULATION_SLIPPAGE_BUY)
    assert cfg.slippage_sell == pytest.approx(SIMULATION_SLIPPAGE_SELL)
    assert cfg.fee_source == FEE_SOURCE_SIMULATION
    assert cfg.assumptions == DEFAULT_ASSUMPTIONS
    assert cfg.exec_price_rule is ExecutionPrice.NEXT_OPEN
    assert cfg.signal_lag == 1


def test_default_market_rules_do_not_assume_price_limit():
    cfg = BacktestConfig(symbol="600000", initial_cash=100000.0)
    assert isinstance(cfg.market_rules, MarketRules)
    assert cfg.market_rules.lot_size == 100
    assert cfg.market_rules.t_plus_one is True
    # 默认不得把 10% 冒充为所有 A 股标的的统一规则
    assert cfg.market_rules.price_limit is None


def test_config_normalizes_date_range():
    cfg = BacktestConfig(
        symbol="600000",
        initial_cash=1000.0,
        start_date="2024-01-02",
        end_date=pd.Timestamp("2024-03-01"),
    )
    assert cfg.start_date == pd.Timestamp("2024-01-02")
    assert cfg.end_date == pd.Timestamp("2024-03-01")


def test_config_is_frozen():
    cfg = BacktestConfig(symbol="600000", initial_cash=1000.0)
    with pytest.raises(FrozenInstanceError):
        cfg.initial_cash = 1.0  # type: ignore[misc]


@pytest.mark.parametrize("symbol", ["", "   ", None, 123])
def test_config_rejects_invalid_symbol(symbol):
    with pytest.raises(ValueError):
        BacktestConfig(symbol=symbol, initial_cash=1000.0)


@pytest.mark.parametrize("cash", [0, -1.0, float("nan"), float("inf"), "100"])
def test_config_rejects_invalid_initial_cash(cash):
    with pytest.raises(ValueError):
        BacktestConfig(symbol="600000", initial_cash=cash)


@pytest.mark.parametrize("lag", [0, -1, 1.5, True])
def test_config_rejects_invalid_signal_lag(lag):
    with pytest.raises(ValueError):
        BacktestConfig(symbol="600000", initial_cash=1000.0, signal_lag=lag)


@pytest.mark.parametrize("field_name", ["commission_rate", "stamp_duty_rate", "slippage_buy"])
def test_config_rejects_negative_fee_params(field_name):
    with pytest.raises(ValueError):
        BacktestConfig(symbol="600000", initial_cash=1000.0, **{field_name: -0.01})


def test_config_rejects_start_after_end():
    with pytest.raises(ValueError):
        BacktestConfig(
            symbol="600000",
            initial_cash=1000.0,
            start_date="2024-03-01",
            end_date="2024-01-02",
        )


def test_config_rejects_unparsable_date():
    with pytest.raises(ValueError):
        BacktestConfig(symbol="600000", initial_cash=1000.0, start_date="not-a-date")


def test_config_rejects_tz_aware_date():
    with pytest.raises(ValueError):
        BacktestConfig(
            symbol="600000", initial_cash=1000.0, start_date="2024-01-02T00:00:00+08:00"
        )


def test_config_rejects_bad_market_rules_type():
    with pytest.raises(ValueError):
        BacktestConfig(symbol="600000", initial_cash=1000.0, market_rules="a_share")  # type: ignore[arg-type]


def test_config_accepts_custom_market_rules():
    rules = a_share_rules(price_limit_rate=0.10)
    cfg = BacktestConfig(symbol="600000", initial_cash=1000.0, market_rules=rules)
    assert cfg.market_rules.price_limit is not None
    assert cfg.market_rules.price_limit.rate == pytest.approx(0.10)
