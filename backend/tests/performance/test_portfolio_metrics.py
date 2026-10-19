"""组合级绩效指标测试：夏普、换手率、成本占比、证据等级。

这些口径是 B18 §9.1 明确要求、而原 ``PerformanceMetrics`` 缺失的三项（D4）。
测试的目的不只是「算得对」，更是**把口径钉死**：
夏普的无风险利率折算方式、换手率的「除 2 折单边」、成本比在毛亏时的 None 语义，
任何一项被后人「顺手优化」，策略与基准的比较就失去可比性。
"""

from __future__ import annotations

import math

import pytest

from deepquant.performance.portfolio_metrics import (
    assess_evidence_level,
    compute_cost_ratio,
    compute_portfolio_performance,
    compute_sharpe,
    compute_turnover,
)

# ===================================================================== #
# 夏普
# ===================================================================== #


def test_sharpe_matches_hand_computed_value():
    """[0.01, −0.005, 0.02]、rf=0、N=252 → 10.5135（手算核对过）。"""
    r = compute_sharpe([0.01, -0.005, 0.02], risk_free_rate_annual=0.0, periods_per_year=252)
    assert r.value == pytest.approx(10.5135, rel=1e-3)
    assert r.n_returns == 3
    assert r.risk_free_rate_annual == 0.0
    assert "ddof=1" in r.definition


def test_sharpe_with_nonzero_risk_free_rate_is_lower():
    """无风险利率按 rf/N 简单折算；抬高 rf 必然降低夏普（同波动、同收益）。"""
    base = compute_sharpe([0.01, -0.005, 0.02], risk_free_rate_annual=0.0)
    with_rf = compute_sharpe([0.01, -0.005, 0.02], risk_free_rate_annual=0.05)
    assert with_rf.value is not None and base.value is not None
    assert with_rf.value < base.value


def test_sharpe_zero_volatility_is_none_not_zero():
    """净值零波动时夏普**无定义**：返回 0 会被读成「风险调整后收益为零」。"""
    r = compute_sharpe([0.001, 0.001, 0.001])
    assert r.value is None
    assert r.unavailable_reason is not None and "标准差为 0" in r.unavailable_reason


def test_sharpe_requires_two_returns():
    r = compute_sharpe([0.01])
    assert r.value is None
    assert "少于 2 个" in (r.unavailable_reason or "")


def test_sharpe_drops_missing_returns_without_filling():
    """缺失收益直接剔除，不前向填充、不当 0 —— 填充会人为压低波动率、抬高夏普。"""
    r = compute_sharpe([0.01, None, -0.005, 0.02])
    assert r.n_returns == 3


def test_sharpe_rejects_bad_periods():
    with pytest.raises(ValueError, match="periods_per_year"):
        compute_sharpe([0.01, 0.02], periods_per_year=0)


# ===================================================================== #
# 换手率
# ===================================================================== #


def test_turnover_one_year_full_round_trip():
    """一年内买卖各 10 万、平均权益 10 万 → 单边年化换手 = 1.0。"""
    r = compute_turnover(200_000.0, [100_000.0] * 252, periods_per_year=252)
    assert r.one_way_annualized == pytest.approx(1.0)
    assert r.years == pytest.approx(1.0)
    assert r.average_equity == pytest.approx(100_000.0)
    assert "除 2" in r.definition or "/ 2" in r.definition


def test_turnover_uses_average_equity_not_initial():
    """分母是平均权益：净值翻倍后，同样成交额的换手应更低。"""
    low = compute_turnover(100_000.0, [100_000.0] * 252)
    high = compute_turnover(100_000.0, [200_000.0] * 252)
    assert high.one_way_annualized < low.one_way_annualized


def test_turnover_empty_equity_is_none():
    r = compute_turnover(1000.0, [])
    assert r.one_way_annualized is None
    assert r.unavailable_reason is not None


def test_turnover_rejects_negative_amount():
    with pytest.raises(ValueError, match="非负"):
        compute_turnover(-1.0, [100.0])


# ===================================================================== #
# 成本占毛收益比
# ===================================================================== #


def test_cost_ratio_basic():
    """净赚 10、费用 2 → 毛赚 12 → 费用占 1/6。"""
    r = compute_cost_ratio(2.0, initial_value=100.0, final_value=110.0)
    assert r.net_profit == pytest.approx(10.0)
    assert r.gross_profit == pytest.approx(12.0)
    assert r.cost_to_gross_ratio == pytest.approx(2.0 / 12.0)


def test_cost_ratio_none_when_gross_profit_non_positive():
    """毛收益 <= 0 时百分比无解释力 → None，不得给出「成本占比 150%」这种伪精确值。"""
    r = compute_cost_ratio(5.0, initial_value=100.0, final_value=90.0)
    assert r.gross_profit == pytest.approx(-5.0)
    assert r.cost_to_gross_ratio is None
    assert "毛收益 <= 0" in (r.unavailable_reason or "")


def test_cost_ratio_none_when_final_equity_unknown():
    r = compute_cost_ratio(5.0, initial_value=100.0, final_value=None)
    assert r.cost_to_gross_ratio is None


# ===================================================================== #
# 证据等级（B18 §9.2 预先登记门槛）
# ===================================================================== #


def test_evidence_level_low_when_below_thresholds():
    e = assess_evidence_level(n_rebalance_periods=20, n_tradable_assets=48)
    assert e.level == "low"
    assert len(e.reasons) == 2


def test_evidence_level_adequate_at_thresholds():
    e = assess_evidence_level(n_rebalance_periods=36, n_tradable_assets=100)
    assert e.level == "adequate"
    assert e.reasons == ()


# ===================================================================== #
# 组合聚合
# ===================================================================== #


def test_portfolio_performance_aggregates_consistently():
    equity = [100_000.0, 101_000.0, 99_000.0, 103_000.0]
    p = compute_portfolio_performance(
        equity,
        initial_value=100_000.0,
        total_traded_amount=60_000.0,
        total_fees=120.0,
        n_rebalance_periods=4,
        n_tradable_assets=50,
    )
    assert p.stats.final_value == pytest.approx(103_000.0)
    assert p.total_pnl == pytest.approx(3_000.0)
    assert p.stats.max_drawdown_ratio == pytest.approx((101_000.0 - 99_000.0) / 101_000.0)
    assert p.evidence.level == "low", "4 期 / 50 只必须判为低证据等级"
    assert p.cost.total_fees == pytest.approx(120.0)


def test_portfolio_performance_is_none_safe_with_missing_equity():
    p = compute_portfolio_performance(
        [100_000.0, None, 103_000.0],
        initial_value=100_000.0,
        total_traded_amount=0.0,
        total_fees=0.0,
    )
    assert p.stats.is_complete is False
    assert p.stats.n_returns == 1, "缺失期之后的收益不可算，不得填补"
    assert math.isclose(p.stats.cumulative_return, 0.03, rel_tol=1e-12)
