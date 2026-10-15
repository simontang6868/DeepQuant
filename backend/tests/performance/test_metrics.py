"""绩效指标与交易统计测试：收益、风险（最大回撤）、交易统计。"""

from __future__ import annotations

import pytest

from deepquant.market.rules import Side
from deepquant.performance.metrics import compute_performance, compute_trade_stats
from deepquant.backtest.models import OrderStatus
from helpers import (
    DATES,
    build_result,
    flat_states,
    make_fill,
    make_order,
    make_state,
    missing_state,
)

# ===================================================================== #
# A. 收益指标
# ===================================================================== #


def test_positive_return():
    result = build_result(states=flat_states([1000.0, 1100.0]), initial=1000.0)
    metrics = compute_performance(result)
    assert metrics.initial_equity == 1000.0
    assert metrics.final_equity == 1100.0
    assert metrics.total_pnl == 100.0
    assert metrics.cumulative_return == pytest.approx(0.1)
    values = [r.value for r in metrics.daily_returns]
    assert values[0] == pytest.approx(0.0)
    assert values[1] == pytest.approx(0.1)


def test_negative_return():
    metrics = compute_performance(build_result(states=flat_states([1000.0, 900.0]), initial=1000.0))
    assert metrics.total_pnl == -100.0
    assert metrics.cumulative_return == pytest.approx(-0.1)
    assert [r.value for r in metrics.daily_returns][1] == pytest.approx(-0.1)


def test_zero_return():
    metrics = compute_performance(
        build_result(states=flat_states([1000.0, 1000.0, 1000.0]), initial=1000.0)
    )
    assert metrics.total_pnl == 0.0
    assert metrics.cumulative_return == pytest.approx(0.0)
    assert all(r.value == pytest.approx(0.0) for r in metrics.daily_returns)


def test_first_day_baseline_uses_initial_equity():
    # 首日权益 1200 与初始权益 1000 不同：首日收益率必须以初始权益为基准（= 0.2），不得设为 0
    metrics = compute_performance(build_result(states=flat_states([1200.0]), initial=1000.0))
    first = metrics.daily_returns[0]
    assert first.prev_equity == 1000.0
    assert first.value == pytest.approx(0.2)


def test_multi_day_equity_series():
    metrics = compute_performance(
        build_result(states=flat_states([1000.0, 1050.0, 1029.0]), initial=1000.0)
    )
    values = [r.value for r in metrics.daily_returns]
    assert values[0] == pytest.approx(0.0)
    assert values[1] == pytest.approx(0.05)
    assert values[2] == pytest.approx(1029.0 / 1050.0 - 1.0)
    assert [r.date for r in metrics.daily_returns] == list(DATES[:3])
    assert [p.date for p in metrics.equity_curve] == list(DATES[:3])


def test_missing_equity_marks_incomplete_and_not_zero():
    states = [
        make_state(DATES[0], 1000.0),
        missing_state(DATES[1], cash=500.0, quantity=100),
        make_state(DATES[2], 520.0),
    ]
    metrics = compute_performance(build_result(states=states, initial=1000.0))
    values = [r.value for r in metrics.daily_returns]
    assert values[0] == pytest.approx(0.0)
    assert values[1] is None  # 不得记为 0
    assert values[2] is None  # 前一期权益未知 → 次日收益率也不可计算
    assert metrics.equity_curve[1].total_equity is None
    assert metrics.is_complete is False
    assert metrics.incomplete_reasons
    assert metrics.final_equity == 520.0


def test_no_states_marks_metrics_unavailable():
    metrics = compute_performance(build_result(states=[]))
    assert metrics.final_equity is None
    assert metrics.is_complete is False
    assert any("最终权益" in reason for reason in metrics.unavailable_metrics)


# ===================================================================== #
# B. 风险指标（最大回撤 / 高水位）
# ===================================================================== #


def test_drawdown_monotonic_increase():
    metrics = compute_performance(
        build_result(states=flat_states([1000.0, 1100.0, 1200.0]), initial=1000.0)
    )
    assert metrics.max_drawdown_amount == 0.0
    assert metrics.max_drawdown_ratio == pytest.approx(0.0)


def test_drawdown_up_then_down():
    metrics = compute_performance(
        build_result(states=flat_states([1000.0, 1200.0, 900.0]), initial=1000.0)
    )
    assert metrics.max_drawdown_amount == 300.0
    assert metrics.max_drawdown_ratio == pytest.approx(0.25)
    assert metrics.max_drawdown_peak_date == DATES[1]
    assert metrics.max_drawdown_trough_date == DATES[2]


def test_drawdown_multiple_highs():
    metrics = compute_performance(
        build_result(
            states=flat_states([1000.0, 1200.0, 1100.0, 1300.0, 1000.0]), initial=1000.0
        )
    )
    # 第一段回撤 1200→1100（100）；第二段高水位 1300→1000（300）为最大
    assert metrics.max_drawdown_amount == 300.0
    assert metrics.max_drawdown_ratio == pytest.approx(300.0 / 1300.0)
    assert metrics.max_drawdown_peak_date == DATES[3]
    assert metrics.max_drawdown_trough_date == DATES[4]


def test_drawdown_consecutive_decline():
    metrics = compute_performance(
        build_result(states=flat_states([1000.0, 900.0, 800.0]), initial=1000.0)
    )
    assert metrics.max_drawdown_amount == 200.0
    assert metrics.max_drawdown_ratio == pytest.approx(0.2)
    assert metrics.max_drawdown_peak_date == DATES[0]
    assert metrics.max_drawdown_trough_date == DATES[2]


def test_drawdown_with_missing_equity_is_partial():
    states = [
        make_state(DATES[0], 1000.0),
        missing_state(DATES[1], cash=800.0, quantity=100),
        make_state(DATES[2], 900.0),
    ]
    metrics = compute_performance(build_result(states=states, initial=1000.0))
    # 基于可用的 2 个权益点：1000 → 900
    assert metrics.drawdown_observation_count == 2
    assert metrics.max_drawdown_amount == 100.0
    assert metrics.max_drawdown_ratio == pytest.approx(0.1)
    assert metrics.is_complete is False
    assert any("部分统计" in reason for reason in metrics.incomplete_reasons)


def test_drawdown_when_initial_differs_from_first_day():
    # 初始权益仅作为首日收益率基准；最大回撤基于每日权益曲线（不含初始权益合成点）
    metrics = compute_performance(build_result(states=flat_states([1200.0, 900.0]), initial=1000.0))
    assert metrics.daily_returns[0].value == pytest.approx(0.2)
    assert metrics.max_drawdown_amount == 300.0
    assert metrics.max_drawdown_ratio == pytest.approx(0.25)


def test_drawdown_insufficient_points():
    # 仅一个权益点：无回撤可言，但已被观测
    metrics = compute_performance(build_result(states=flat_states([1000.0]), initial=1000.0))
    assert metrics.drawdown_observation_count == 1
    assert metrics.max_drawdown_amount == 0.0
    assert metrics.max_drawdown_ratio == pytest.approx(0.0)
    assert metrics.max_drawdown_peak_date == DATES[0]
    assert metrics.max_drawdown_trough_date == DATES[0]


def test_zero_equity_series_handled_explicitly():
    # 权益为 0（现金 0、空仓）是可达的：不得把零权益当成缺失，也不得虚构负值
    metrics = compute_performance(build_result(states=flat_states([0.0, 0.0]), initial=1000.0))
    assert metrics.final_equity == 0.0
    assert metrics.total_pnl == -1000.0
    assert metrics.cumulative_return == pytest.approx(-1.0)
    # 首日以初始权益为基准（1000 → 0 ⇒ -1.0），不是硬设为 0
    assert metrics.daily_returns[0].value == pytest.approx(-1.0)
    # 次日基准为 0，无法计算相对收益（0 作分母无定义）→ 记 None，不得记为 0
    assert metrics.daily_returns[1].prev_equity == 0.0
    assert metrics.daily_returns[1].value is None
    # 权益非负且恒为 0：高水位为 0，无回撤
    assert metrics.max_drawdown_amount == 0.0
    assert metrics.max_drawdown_ratio == pytest.approx(0.0)
    # 最终权益为 0 时年化收益率不可用（需要最终权益 > 0）
    assert metrics.annualized_return is None
    assert any("年化收益率" in reason for reason in metrics.unavailable_metrics)


def test_high_water_not_lowered_on_equal_equity():
    # 相同的权益点不构成新高水位，也不产生回撤
    metrics = compute_performance(
        build_result(states=flat_states([1000.0, 1000.0, 1000.0]), initial=1000.0)
    )
    assert metrics.max_drawdown_amount == 0.0
    assert metrics.max_drawdown_peak_date == DATES[0]
    assert metrics.max_drawdown_trough_date == DATES[0]


def test_annualized_return_and_volatility_explicit_basis():
    # periods_per_year=2 → years = 2/2 = 1，便于精确断言口径
    metrics = compute_performance(
        build_result(states=flat_states([1000.0, 1100.0]), initial=1000.0), periods_per_year=2
    )
    assert metrics.annualized_return == pytest.approx(0.1)
    assert metrics.annualized_volatility == pytest.approx(0.1)


def test_annualized_volatility_needs_two_returns():
    metrics = compute_performance(
        build_result(states=flat_states([1000.0]), initial=1000.0), periods_per_year=2
    )
    assert metrics.annualized_volatility is None
    assert any("波动率" in reason for reason in metrics.unavailable_metrics)


# ===================================================================== #
# C. 交易统计
# ===================================================================== #


def _round_trip_fills(sell_price: float):
    orders = [
        make_order("O1", Side.BUY, status=OrderStatus.FILLED, fill_id="F1"),
        make_order(
            "O2",
            Side.SELL,
            signal_date="2024-01-03",
            planned="2024-01-04",
            status=OrderStatus.FILLED,
            fill_id="F2",
        ),
    ]
    fills = [
        make_fill("F1", "O1", Side.BUY, fill_date="2024-01-03", price=10.0, quantity=100, commission=5.0),
        make_fill(
            "F2",
            "O2",
            Side.SELL,
            fill_date="2024-01-04",
            price=sell_price,
            quantity=100,
            commission=5.0,
            stamp_duty=0.6,
        ),
    ]
    return orders, fills


def test_completed_round_trip_winning_trade():
    orders, fills = _round_trip_fills(sell_price=12.0)
    stats = compute_trade_stats(orders, fills)
    # 成本 = 1000 + 5 = 1005；收入 = 1200 − 5.6 = 1194.4；盈亏 = 189.4
    assert stats.n_orders == 2
    assert stats.n_filled_orders == 2
    assert stats.n_unfilled_orders == 0
    assert stats.n_fills == 2
    assert stats.buy_gross_amount == 1000.0
    assert stats.sell_gross_amount == 1200.0
    assert stats.total_fees == pytest.approx(10.6)
    assert stats.n_closed_trades == 1
    assert stats.n_winning_trades == 1
    assert stats.n_losing_trades == 0
    assert stats.win_rate == pytest.approx(1.0)
    assert stats.open_position_quantity == 0


def test_completed_round_trip_losing_trade():
    orders, fills = _round_trip_fills(sell_price=9.0)
    stats = compute_trade_stats(orders, fills)
    # 收入 = 900 − 5 = 895；成本 1005；盈亏 = −110
    assert stats.n_closed_trades == 1
    assert stats.n_winning_trades == 0
    assert stats.n_losing_trades == 1
    assert stats.win_rate == pytest.approx(0.0)


def test_open_position_excluded_from_win_rate():
    orders = [make_order("O1", Side.BUY, status=OrderStatus.FILLED, fill_id="F1")]
    fills = [make_fill("F1", "O1", Side.BUY, price=10.0, quantity=100, commission=5.0)]
    stats = compute_trade_stats(orders, fills)
    assert stats.n_closed_trades == 0
    assert stats.win_rate is None
    assert stats.open_position_quantity == 100


def test_open_position_not_counted_in_win_rate_with_closed_trade():
    orders = [
        make_order("O1", Side.BUY, status=OrderStatus.FILLED, fill_id="F1"),
        make_order("O2", Side.SELL, signal_date="2024-01-03", planned="2024-01-04", status=OrderStatus.FILLED, fill_id="F2"),
        make_order("O3", Side.BUY, signal_date="2024-01-04", planned="2024-01-05", status=OrderStatus.FILLED, fill_id="F3"),
    ]
    fills = [
        make_fill("F1", "O1", Side.BUY, fill_date="2024-01-03", price=10.0, quantity=100),
        make_fill("F2", "O2", Side.SELL, fill_date="2024-01-04", price=12.0, quantity=100),
        make_fill("F3", "O3", Side.BUY, fill_date="2024-01-05", price=11.0, quantity=100),
    ]
    stats = compute_trade_stats(orders, fills)
    assert stats.n_closed_trades == 1
    assert stats.n_winning_trades == 1
    assert stats.win_rate == pytest.approx(1.0)  # 未平仓不计入
    assert stats.open_position_quantity == 100
    assert stats.n_fills == 3


def test_rejected_order_not_counted_as_fill():
    orders = [make_order("O1", Side.BUY, status=OrderStatus.REJECTED, reason="insufficient_cash")]
    stats = compute_trade_stats(orders, [])
    assert stats.n_orders == 1
    assert stats.n_filled_orders == 0
    assert stats.n_unfilled_orders == 1
    assert stats.n_fills == 0
    assert stats.win_rate is None
    assert stats.open_position_quantity == 0


def test_order_count_is_not_fill_count():
    orders = [
        make_order("O1", Side.BUY, status=OrderStatus.FILLED, fill_id="F1"),
        make_order("O2", Side.SELL, signal_date="2024-01-03", planned="2024-01-04", status=OrderStatus.FILLED, fill_id="F2"),
        make_order("O3", Side.BUY, signal_date="2024-01-04", planned="2024-01-05", status=OrderStatus.REJECTED),
    ]
    fills = [
        make_fill("F1", "O1", Side.BUY, fill_date="2024-01-03", price=10.0, quantity=100),
        make_fill("F2", "O2", Side.SELL, fill_date="2024-01-04", price=11.0, quantity=100),
    ]
    stats = compute_trade_stats(orders, fills)
    assert stats.n_orders == 3
    assert stats.n_filled_orders == 2
    assert stats.n_unfilled_orders == 1
    assert stats.n_fills == 2  # 未成交订单不计入成交笔数
