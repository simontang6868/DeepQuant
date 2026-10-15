"""市场规则配置测试：参数校验、默认值、涨跌停配置语义。"""

import pytest

from deepquant.market.rules import (
    MarketRules,
    PriceLimit,
    Side,
    a_share_rules,
    price_limit_notes,
)


def test_side_values_are_buy_and_sell():
    assert Side.BUY.value == "BUY"
    assert Side.SELL.value == "SELL"


def test_default_a_share_rules_have_no_price_limit():
    # 关键：默认不得把 10% 冒充为所有 A 股标的的统一规则
    rules = a_share_rules()
    assert rules.name == "A_SHARE"
    assert rules.lot_size == 100
    assert rules.t_plus_one is True
    assert rules.price_limit is None


def test_a_share_rules_build_price_limit_only_when_rate_given():
    rules = a_share_rules(price_limit_rate=0.10)
    assert isinstance(rules.price_limit, PriceLimit)
    assert rules.price_limit.rate == pytest.approx(0.10)
    assert rules.price_limit.tick_size == pytest.approx(0.01)


def test_price_limit_rejects_out_of_range_rate():
    with pytest.raises(ValueError):
        PriceLimit(0.0)
    with pytest.raises(ValueError):
        PriceLimit(1.0)
    with pytest.raises(ValueError):
        PriceLimit(-0.1)


def test_price_limit_rejects_non_positive_tick():
    with pytest.raises(ValueError):
        PriceLimit(0.10, tick_size=0.0)


def test_market_rules_reject_non_positive_lot_size():
    with pytest.raises(ValueError):
        MarketRules(lot_size=0)


def test_price_limit_notes_flag_unconfigured_rule():
    notes = price_limit_notes(a_share_rules())
    assert len(notes) == 1
    assert "未配置" in notes[0]
    assert "未进行" in notes[0]


def test_price_limit_notes_describe_enabled_simplified_model():
    notes = price_limit_notes(a_share_rules(price_limit_rate=0.10))
    assert len(notes) == 1
    assert "简化模型" in notes[0]
    assert "0.1" in notes[0] or "0.10" in notes[0]
