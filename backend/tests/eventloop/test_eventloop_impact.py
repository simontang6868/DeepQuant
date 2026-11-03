"""滑点与冲击模型：参数缺失必须报错，不能静默返回 0。"""

from __future__ import annotations

import pytest

from deepquant.eventloop.impact import (
    FRICTIONLESS,
    SlippageMode,
    SlippageModel,
    SlippageModelError,
    sqrt_impact,
)
from deepquant.market.rules import Side


def test_frictionless_mode_leaves_price_untouched() -> None:
    price, detail = FRICTIONLESS.apply(10.0, Side.BUY, 1000)
    assert price == 10.0
    assert detail["unit_impact"] == 0.0
    assert FRICTIONLESS.is_frictionless


def test_constant_mode_uses_bps_in_both_directions() -> None:
    model = SlippageModel(mode=SlippageMode.CONSTANT, bps=50.0)  # 0.5%
    buy_price, _ = model.apply(10.0, Side.BUY, 1000)
    sell_price, _ = model.apply(10.0, Side.SELL, 1000)
    assert buy_price == pytest.approx(10.05)
    assert sell_price == pytest.approx(9.95)


def test_sqrt_impact_requires_adv() -> None:
    model = SlippageModel(mode=SlippageMode.SQRT_IMPACT, eta=1.0, sigma=0.02, adv=0.0)
    with pytest.raises(SlippageModelError, match="ADV"):
        model.unit_impact(1000)


def test_sqrt_impact_scales_with_sqrt_of_participation() -> None:
    small = sqrt_impact(1_000, adv=1_000_000, sigma=0.02, eta=1.0)
    large = sqrt_impact(100_000, adv=1_000_000, sigma=0.02, eta=1.0)
    # 参与率 100 倍 → 冲击 10 倍（平方根），不是 100 倍
    assert large / small == pytest.approx(10.0)


def test_sqrt_impact_price_is_tick_rounded_and_adverse() -> None:
    model = SlippageModel(mode=SlippageMode.SQRT_IMPACT, eta=1.0, sigma=0.02, adv=100_000.0)
    price, detail = model.apply(10.0, Side.BUY, 100_000)
    assert price > 10.0
    assert round(price, 2) == price
    assert detail["unit_impact"] == pytest.approx(0.02)


def test_negative_sigma_rejected() -> None:
    model = SlippageModel(mode=SlippageMode.SQRT_IMPACT, eta=1.0, sigma=-0.01, adv=1e6)
    with pytest.raises(SlippageModelError, match="sigma"):
        model.unit_impact(1000)


def test_zero_bps_constant_is_treated_as_frictionless() -> None:
    model = SlippageModel(mode=SlippageMode.CONSTANT, bps=0.0)
    assert model.is_frictionless


def test_describe_exposes_all_assumptions() -> None:
    model = SlippageModel(mode=SlippageMode.SQRT_IMPACT, eta=0.5, sigma=0.03, adv=5e6)
    payload = model.describe()
    assert payload["mode"] == "sqrt_impact"
    assert payload["eta"] == 0.5
    assert payload["adv"] == 5e6
    assert payload["frictionless"] is False
