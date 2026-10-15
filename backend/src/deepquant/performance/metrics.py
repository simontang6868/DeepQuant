"""回测绩效指标与交易统计（纯计算，不修改回测结果）。

统一口径（本模块是这些口径的唯一定义处，其它模块不得各自另算）：

A. 资金与收益
- 初始权益 = ``config.initial_cash``；
- 每日收益率基于**账户总权益** ``total_equity``（不是股票收盘价变化）：
  ``daily_return_t = equity_t / equity_{t-1} - 1``；首个有效交易日的前一期基准取初始权益
  （因为初始权益没有对应的 AccountState），**不得**把首日收益率硬设为 0；
- 累计收益率 = ``最终权益 / 初始权益 - 1``；
- 总盈亏金额 = ``最终权益 - 初始权益``（金额规约到分）。

B. 风险
- 权益曲线 = 每日 AccountState 的 ``(date, total_equity)``（缺失记 ``None``）；
- 最大回撤基于**每日权益曲线的可用点**按历史高水位计算：
  ``高水位_t = max(equity_1..t)``；``回撤金额_t = 高水位_t - equity_t``；
  ``回撤比例_t = 回撤金额_t / 高水位_t``（高水位 <= 0 时比例不可用）；
- 年化收益率 = ``(最终/初始)^(1/年数) - 1``，``年数 = 交易日数 / periods_per_year``
  （口径为等比年化/CAGR，需要有效的初始与最终权益，且最终权益 > 0）；
- 年化波动率 = ``样本标准差(daily_returns, ddof=1) × sqrt(periods_per_year)``，
  需要至少 2 个可计算的日收益率。

C. 交易统计（以「完整买入—卖出周期」为一笔已完成交易）
- 买入成本 = 成交金额 + 买入费用；卖出收入 = 成交金额 − 卖出费用；盈亏 = 卖出收入 − 买入成本；
- 一笔买入配一笔卖出即为一笔已完成交易；费用已由引擎从现金扣除，因此已反映在权益中；
- 未平仓持仓不计入已完成交易，也不参与胜率；
- ``胜率 = 盈利交易数 / 已完成交易数``；盈亏恰为 0 记为「持平」，不计入盈利或亏损；
- 订单数量 ≠ 成交笔数；买入订单与卖出订单各自都不算作一笔已完成交易。

完整性
- 只要存在 ``total_equity is None`` 的交易日，权益序列即不完整；
- 依赖完整权益序列的指标（累计收益率、最大回撤、年化）在权益不完整时：
  可计算的部分统计予以保留，但通过 ``is_complete`` / ``incomplete_reasons`` 明确标记，
  完全无法计算时列入 ``unavailable_metrics``（含原因）。
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd

from deepquant.backtest.costs import round_money
from deepquant.backtest.models import (
    AccountState,
    BacktestResult,
    Fill,
    Order,
    OrderStatus,
)
from deepquant.market.rules import Side

DEFAULT_PERIODS_PER_YEAR = 252


@dataclass(frozen=True)
class DailyReturn:
    """单日收益率及其口径（缺失时 value 为 None，不得记为 0）。"""

    date: pd.Timestamp
    equity: float | None
    prev_equity: float | None
    value: float | None


@dataclass(frozen=True)
class EquityPoint:
    """每日权益曲线上的一个点（total_equity 缺失时为 None）。"""

    date: pd.Timestamp
    total_equity: float | None


@dataclass(frozen=True)
class TradeStats:
    """交易统计（订单 / 成交 / 已完成交易）。"""

    n_orders: int
    n_filled_orders: int
    n_unfilled_orders: int
    n_fills: int
    buy_gross_amount: float
    sell_gross_amount: float
    total_fees: float
    n_closed_trades: int
    n_winning_trades: int
    n_losing_trades: int
    n_breakeven_trades: int
    win_rate: float | None
    open_position_quantity: int


@dataclass(frozen=True)
class PerformanceMetrics:
    """回测绩效指标汇总。"""

    initial_equity: float
    final_equity: float | None
    total_pnl: float | None
    cumulative_return: float | None
    daily_returns: tuple[DailyReturn, ...]
    equity_curve: tuple[EquityPoint, ...]
    max_drawdown_amount: float | None
    max_drawdown_ratio: float | None
    max_drawdown_peak_date: pd.Timestamp | None
    max_drawdown_trough_date: pd.Timestamp | None
    drawdown_observation_count: int
    annualized_return: float | None
    annualized_volatility: float | None
    trade_stats: TradeStats
    periods_per_year: int
    is_complete: bool
    incomplete_reasons: tuple[str, ...]
    unavailable_metrics: tuple[str, ...]


def compute_trade_stats(orders: Sequence[Order], fills: Sequence[Fill]) -> TradeStats:
    """由订单与成交计算交易统计。

    配对规则：按成交日期（再按 fill_id）升序，逐笔维护一个「未平仓买入成本」；
    遇到卖出时按数量比例结转买入成本，形成一笔已完成交易。
    本模型为单标的、整仓买/卖，正常情形下卖出数量与未平仓数量相等。
    """
    n_orders = len(orders)
    n_filled_orders = sum(1 for o in orders if o.status is OrderStatus.FILLED)
    n_unfilled_orders = n_orders - n_filled_orders
    n_fills = len(fills)

    buy_gross = 0.0
    sell_gross = 0.0
    total_fees = 0.0
    for fill in fills:
        gross = round_money(fill.price * fill.quantity)
        if fill.side is Side.BUY:
            buy_gross = round_money(buy_gross + gross)
        else:
            sell_gross = round_money(sell_gross + gross)
        total_fees = round_money(total_fees + fill.fees.total)

    ordered = sorted(fills, key=lambda f: (f.fill_date, f.fill_id))
    open_quantity = 0
    open_cost = 0.0
    n_closed = 0
    n_winning = 0
    n_losing = 0
    n_breakeven = 0

    for fill in ordered:
        gross = round_money(fill.price * fill.quantity)
        if fill.side is Side.BUY:
            open_quantity += fill.quantity
            open_cost = round_money(open_cost + gross + fill.fees.total)
            continue

        # SELL：配平未平仓买入
        if open_quantity <= 0:
            # 没有可配对的买入（v1 不应发生）：不臆造盈亏，跳过该笔
            continue
        if fill.quantity >= open_quantity:
            cost_part = open_cost
            open_quantity_after = 0
        else:
            cost_part = round_money(open_cost * (fill.quantity / open_quantity))
            open_quantity_after = open_quantity - fill.quantity
        proceeds = round_money(gross - fill.fees.total)
        pnl = round_money(proceeds - cost_part)

        n_closed += 1
        if pnl > 0:
            n_winning += 1
        elif pnl < 0:
            n_losing += 1
        else:
            n_breakeven += 1

        open_quantity = max(0, open_quantity_after)
        open_cost = round_money(open_cost - cost_part) if open_quantity > 0 else 0.0

    win_rate = (n_winning / n_closed) if n_closed > 0 else None

    return TradeStats(
        n_orders=n_orders,
        n_filled_orders=n_filled_orders,
        n_unfilled_orders=n_unfilled_orders,
        n_fills=n_fills,
        buy_gross_amount=buy_gross,
        sell_gross_amount=sell_gross,
        total_fees=total_fees,
        n_closed_trades=n_closed,
        n_winning_trades=n_winning,
        n_losing_trades=n_losing,
        n_breakeven_trades=n_breakeven,
        win_rate=win_rate,
        open_position_quantity=open_quantity,
    )


def compute_performance(
    result: BacktestResult,
    *,
    periods_per_year: int = DEFAULT_PERIODS_PER_YEAR,
) -> PerformanceMetrics:
    """由回测结果计算绩效指标（不修改 ``result``）。"""
    if periods_per_year < 1:
        raise ValueError(f"periods_per_year 必须 >= 1，收到 {periods_per_year}")

    initial = float(result.config.initial_cash)
    states: Sequence[AccountState] = result.account_states

    # --- 权益曲线与完整性 ---
    equity_curve = tuple(EquityPoint(s.date, s.total_equity) for s in states)

    incomplete_reasons: list[str] = []
    if not states:
        incomplete_reasons.append("无每日账户状态，权益序列为空，无法评估绩效")
    for state in states:
        if state.total_equity is None:
            incomplete_reasons.append(
                f"{state.date.date()} 权益不可用（估值状态 {state.valuation_status.value}），"
                "该日及其次日的收益率不可计算"
            )
    is_complete = bool(states) and not incomplete_reasons

    # --- 最终权益 / 总盈亏 / 累计收益率 ---
    final_equity = states[-1].total_equity if states else None
    unavailable: list[str] = []
    total_pnl: float | None = None
    cumulative_return: float | None = None
    if final_equity is None:
        unavailable.append("最终权益不可用，无法计算总盈亏与累计收益率")
    else:
        total_pnl = round_money(final_equity - initial)
        cumulative_return = final_equity / initial - 1.0

    # --- 每日收益率（首日以前一期 = 初始权益）---
    daily_returns: list[DailyReturn] = []
    prev_equity: float | None = initial
    for state in states:
        equity = state.total_equity
        value: float | None
        if equity is None or prev_equity is None or prev_equity <= 0:
            value = None
        else:
            value = equity / prev_equity - 1.0
        daily_returns.append(
            DailyReturn(date=state.date, equity=equity, prev_equity=prev_equity, value=value)
        )
        prev_equity = equity  # 缺失日之后，次日基准未知 → 次日收益率也不可计算

    # --- 最大回撤（基于可用权益点，历史高水位）---
    available_points = [(s.date, s.total_equity) for s in states if s.total_equity is not None]
    drawdown_count = len(available_points)
    max_dd_amount: float | None = None
    max_dd_ratio: float | None = None
    peak_date: pd.Timestamp | None = None
    trough_date: pd.Timestamp | None = None
    if drawdown_count == 0:
        unavailable.append("无可用权益点，无法计算最大回撤")
    else:
        peak_value, peak_date = available_points[0][1], available_points[0][0]
        trough_date = peak_date
        max_dd_amount = 0.0
        max_dd_ratio = 0.0
        for date, equity in available_points:
            if equity > peak_value:
                peak_value, peak_date = equity, date
            drawdown = peak_value - equity
            if drawdown > max_dd_amount:
                max_dd_amount = drawdown
                max_dd_ratio = (drawdown / peak_value) if peak_value > 0 else 0.0
                trough_date = date
        max_dd_amount = round_money(max_dd_amount)

    # --- 年化收益率（CAGR 口径）---
    annualized_return: float | None = None
    if final_equity is None or final_equity <= 0 or not states:
        unavailable.append("年化收益率需要有效的最终权益（> 0）与至少一个交易日")
    else:
        years = len(states) / periods_per_year
        annualized_return = (final_equity / initial) ** (1.0 / years) - 1.0

    # --- 年化波动率 ---
    return_values = [r.value for r in daily_returns if r.value is not None]
    annualized_volatility: float | None = None
    if len(return_values) < 2:
        unavailable.append("年化波动率需要至少 2 个可计算的日收益率")
    else:
        annualized_volatility = statistics.stdev(return_values) * math.sqrt(periods_per_year)

    if drawdown_count < len(states) and drawdown_count > 0:
        incomplete_reasons.append(
            f"最大回撤基于 {drawdown_count}/{len(states)} 个可用权益点，属部分统计（序列不完整）"
        )

    return PerformanceMetrics(
        initial_equity=initial,
        final_equity=final_equity,
        total_pnl=total_pnl,
        cumulative_return=cumulative_return,
        daily_returns=tuple(daily_returns),
        equity_curve=equity_curve,
        max_drawdown_amount=max_dd_amount,
        max_drawdown_ratio=max_dd_ratio,
        max_drawdown_peak_date=peak_date,
        max_drawdown_trough_date=trough_date,
        drawdown_observation_count=drawdown_count,
        annualized_return=annualized_return,
        annualized_volatility=annualized_volatility,
        trade_stats=compute_trade_stats(result.orders, result.fills),
        periods_per_year=periods_per_year,
        is_complete=is_complete,
        incomplete_reasons=tuple(incomplete_reasons),
        unavailable_metrics=tuple(unavailable),
    )
