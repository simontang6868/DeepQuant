"""A3 组合记账循环的口径测试（对应 docs/A3_ACCOUNTING_SPEC.md）。

每个测试对应规格里的一条判定，命名直接写口径编号，便于「文档改了但测试没改」
时一眼看出来缺哪条：

- 口径① 卖出先于买入
- 口径② 订单名义金额 = 权益 × |Δw|，min_commission 逐笔生效
- 口径③ 停牌冻结估值
- 口径④ 买入涨停放弃 / 卖出跌停顺延

外加两条硬不变量：现金恒等式与权益恒等式（逐日断言，容差 1 分）。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.portfolio import (
    REASON_UNFILLED_AT_END,
    PortfolioBacktestConfig,
    PortfolioBacktestResult,
    TargetPortfolio,
    run_dual_allocation_backtest,
    run_portfolio_backtest,
)
from deepquant.market.rules import Side, a_share_rules


def _frame(rows: list[tuple[str, float, float]]) -> pd.DataFrame:
    """由 (日期, 开盘价, 收盘价) 构造行情；high/low 取两者极值，volume 常量。"""
    dates = pd.to_datetime([r[0] for r in rows])
    opens = [r[1] for r in rows]
    closes = [r[2] for r in rows]
    return pd.DataFrame(
        {
            "open": opens,
            "high": [max(o, c) for o, c in zip(opens, closes)],
            "low": [min(o, c) for o, c in zip(opens, closes)],
            "close": closes,
            "volume": [1_000_000] * len(rows),
        },
        index=pd.DatetimeIndex(dates),
    )


def _fee_template(*, price_limit_rate: float | None = None, cash: float = 100_000.0) -> BacktestConfig:
    return BacktestConfig(
        symbol="TEMPLATE",
        initial_cash=cash,
        market_rules=a_share_rules(price_limit_rate=price_limit_rate),
    )


def _config(
    symbols: tuple[str, ...],
    *,
    cash: float = 100_000.0,
    allocation: str = "weights",
    price_limit_rate: float | None = None,
) -> PortfolioBacktestConfig:
    return PortfolioBacktestConfig(
        symbols=symbols,
        initial_cash=cash,
        fee_config=_fee_template(price_limit_rate=price_limit_rate, cash=cash),
        allocation=allocation,  # type: ignore[arg-type]
    )


def _assert_identities(result: PortfolioBacktestResult) -> None:
    """逐日断言：权益 = 现金 + 市值，且现金非负。"""
    for st in result.states:
        assert st.total_equity is not None
        assert st.total_equity == pytest.approx(st.cash + st.market_value, abs=0.01)
        assert st.cash >= -0.01


# ===================================================================== #
# 口径①：卖出先于买入
# ===================================================================== #


def test_sells_execute_before_buys_on_rebalance_day() -> None:
    """调仓日必须先出现全部卖单，再出现买单。

    这不是风格问题：反过来会造成「现金不足 → 买入被压减 → 卖出后现金闲置」的
    顺序性损耗，而该损耗与策略无关。
    """
    prices = {
        "A": _frame([("2024-01-02", 10.0, 10.0), ("2024-01-03", 10.0, 10.0),
                     ("2024-01-04", 10.0, 10.0), ("2024-01-05", 10.0, 10.0)]),
        "B": _frame([("2024-01-02", 20.0, 20.0), ("2024-01-03", 20.0, 20.0),
                     ("2024-01-04", 20.0, 20.0), ("2024-01-05", 20.0, 20.0)]),
    }
    schedule = (
        TargetPortfolio(pd.Timestamp("2024-01-02"), {"A": 0.5, "B": 0.5}),
        # 第二个信号：全部换到 B → 必须先卖 A 再买 B
        TargetPortfolio(pd.Timestamp("2024-01-03"), {"B": 1.0}),
    )
    result = run_portfolio_backtest(prices, schedule, _config(("A", "B")))

    by_date: dict[pd.Timestamp, list[Side]] = {}
    for f in result.fills:
        by_date.setdefault(f.date, []).append(f.side)

    day2 = by_date[pd.Timestamp("2024-01-04")]
    assert day2 == [Side.SELL, Side.BUY], f"调仓日成交顺序应为「先卖后买」，实际 {day2}"
    _assert_identities(result)


def test_empty_target_liquidates_all_positions() -> None:
    """空权重 = 全现金（合法状态），必须把持仓卖干净，而不是留着。"""
    prices = {"A": _frame([("2024-01-02", 10.0, 10.0), ("2024-01-03", 10.0, 10.0),
                           ("2024-01-04", 10.0, 10.0)])}
    schedule = (
        TargetPortfolio(pd.Timestamp("2024-01-02"), {"A": 1.0}),
        TargetPortfolio(pd.Timestamp("2024-01-03"), {}),
    )
    result = run_portfolio_backtest(prices, schedule, _config(("A",)))
    assert result.states[-1].n_positions == 0
    assert result.states[-1].cash == pytest.approx(result.states[-1].total_equity, abs=0.01)


# ===================================================================== #
# 口径②：min_commission 逐笔生效
# ===================================================================== #


def test_min_commission_binds_on_small_orders() -> None:
    """20 只等权 → 每笔 5000 元，佣金万 3 = 1.5 元 < 最低 5 元 → 必须按 5 元收。

    若组合层「按权重记账」时跳过最低佣金，就等于凭空给策略降低 3 倍以上佣金成本。
    """
    symbols = tuple(f"S{i:02d}" for i in range(20))
    prices = {s: _frame([("2024-01-02", 10.0, 10.0), ("2024-01-03", 10.0, 10.0)])
              for s in symbols}
    weights = {s: 0.05 for s in symbols}
    schedule = (TargetPortfolio(pd.Timestamp("2024-01-02"), weights),)
    result = run_portfolio_backtest(prices, schedule, _config(symbols))

    buys = [f for f in result.fills if f.side is Side.BUY]
    assert len(buys) == 20
    for f in buys:
        assert f.gross < 5.0 / 0.0003, "该笔金额应低于「佣金达到最低值」的临界点"
        assert f.fees_total == pytest.approx(5.0, abs=0.01), (
            f"{f.symbol} 买入费用 {f.fees_total} 未按最低佣金 5 元收取"
        )


def test_weight_target_scales_with_equity() -> None:
    """订单名义金额 = 期初权益 × |Δw|：半仓目标的成交额应约占权益一半。"""
    prices = {"A": _frame([("2024-01-02", 10.0, 10.0), ("2024-01-03", 10.0, 10.0)])}
    schedule = (TargetPortfolio(pd.Timestamp("2024-01-02"), {"A": 0.5}),)
    result = run_portfolio_backtest(prices, schedule, _config(("A",), cash=100_000.0))
    buy = next(f for f in result.fills if f.side is Side.BUY)
    assert buy.gross == pytest.approx(50_000.0, rel=0.01)


# ===================================================================== #
# 口径③：停牌冻结估值
# ===================================================================== #


def test_suspended_symbol_is_valued_at_frozen_price() -> None:
    """停牌日：权益不变、stale 计数为 1、冻结权重 100% → 标记低可信。

    绝不能按 0 估值（会让净值凭空跳水再暴涨），也不能自动清仓（无价可卖）。
    """
    prices = {"A": _frame([("2024-01-02", 10.0, 10.0), ("2024-01-03", 10.0, 10.0),
                           ("2024-01-06", 10.0, 10.0)])}
    # 日历并集：01-02/03/06/07（07 只有 B，A 停牌）
    prices["B"] = _frame([("2024-01-02", 5.0, 5.0), ("2024-01-03", 5.0, 5.0),
                          ("2024-01-06", 5.0, 5.0), ("2024-01-07", 99.0, 99.0)])
    schedule = (TargetPortfolio(pd.Timestamp("2024-01-02"), {"A": 1.0}),)

    result = run_portfolio_backtest(prices, schedule, _config(("A", "B")))
    idx = {st.date: st for st in result.states}

    held = idx[pd.Timestamp("2024-01-03")]
    suspended = idx[pd.Timestamp("2024-01-07")]
    assert suspended.n_stale_positions == 1
    assert suspended.stale_weight_ratio == pytest.approx(1.0, abs=1e-4)
    assert suspended.low_confidence_valuation is True
    # 停牌日按冻结价估值 → 权益与前一交易日完全相同
    assert suspended.total_equity == pytest.approx(held.total_equity, abs=0.01)
    assert any("低可信" in m for m in result.data_quality_issues)


def test_no_fill_on_suspended_day() -> None:
    """停牌日不得产生任何成交；该笔卖单记为 no_price 并顺延到复牌日。

    场景：01-04 只有 B 有行情、A 停牌（A 的日历上缺这一行），
    信号日 01-03 要求换成 B —— 于是 01-04 卖不出 A（记 no_price），
    01-05 A 复牌后才成交。
    """
    prices = {"A": _frame([("2024-01-02", 10.0, 10.0), ("2024-01-03", 10.0, 10.0),
                           ("2024-01-05", 10.0, 10.0)])}
    # 01-04 缺失（停牌），01-05 复牌
    prices["B"] = _frame([("2024-01-04", 5.0, 5.0), ("2024-01-05", 5.0, 5.0)])
    schedule = (
        TargetPortfolio(pd.Timestamp("2024-01-02"), {"A": 1.0}),
        TargetPortfolio(pd.Timestamp("2024-01-03"), {"B": 1.0}),
    )
    result = run_portfolio_backtest(prices, schedule, _config(("A", "B")))

    assert not [f for f in result.fills
                if f.date == pd.Timestamp("2024-01-04") and f.symbol == "A"], (
        "A 在 2024-01-04 停牌，不可能成交"
    )
    assert any(
        b.date == pd.Timestamp("2024-01-04") and b.symbol == "A" and b.reason == "no_price"
        for b in result.blocked
    ), "停牌导致的卖不出必须被记录（no_price），不能静默跳过"
    # 复牌日顺延成交
    assert any(
        f.symbol == "A" and f.side is Side.SELL and f.date == pd.Timestamp("2024-01-05")
        for f in result.fills
    )


# ===================================================================== #
# 口径④：涨跌停（买侧放弃 / 卖侧顺延）
# ===================================================================== #


def test_buy_blocked_by_limit_up_is_abandoned_not_chased() -> None:
    """买入遇涨停：当日放弃，不顺延、不以更高价追。"""
    prices = {"A": _frame([("2024-01-02", 10.0, 10.0), ("2024-01-03", 11.0, 11.0)])}
    schedule = (TargetPortfolio(pd.Timestamp("2024-01-02"), {"A": 1.0}),)
    result = run_portfolio_backtest(
        prices, schedule, _config(("A",), price_limit_rate=0.10)
    )
    assert not result.fills
    assert any(b.reason == "limit_up" for b in result.blocked)
    assert result.states[-1].cash == pytest.approx(100_000.0, abs=0.01)


def test_sell_blocked_by_limit_down_is_deferred_to_next_day() -> None:
    """卖出遇跌停：顺延到下一交易日重试，并在那天成交（不是放弃）。"""
    prices = {"A": _frame([
        ("2024-01-02", 10.0, 10.0),
        ("2024-01-03", 10.0, 10.0),   # 建仓
        ("2024-01-04", 9.0, 9.0),     # 跌停：卖不出
        ("2024-01-05", 9.0, 9.0),     # 重试成交
    ])}
    schedule = (
        TargetPortfolio(pd.Timestamp("2024-01-02"), {"A": 1.0}),
        TargetPortfolio(pd.Timestamp("2024-01-03"), {}),   # 01-04 想清仓
    )
    result = run_portfolio_backtest(
        prices, schedule, _config(("A",), price_limit_rate=0.10)
    )
    sells = [f for f in result.fills if f.side is Side.SELL]
    assert len(sells) == 1, "顺延卖出最终应成交一次"
    assert sells[0].date == pd.Timestamp("2024-01-05")
    assert sells[0].reason == "deferred_sell"
    assert any(b.reason == "limit_down" for b in result.blocked)


def test_deferred_sell_still_unfilled_at_end_is_recorded_not_faked() -> None:
    """一直跌停到回测结束：不得虚构成交，必须记 unfilled_at_end。"""
    prices = {"A": _frame([
        ("2024-01-02", 10.0, 10.0),
        ("2024-01-03", 10.0, 10.0),
        ("2024-01-04", 9.0, 9.0),
        ("2024-01-05", 8.1, 8.1),
    ])}
    schedule = (
        TargetPortfolio(pd.Timestamp("2024-01-02"), {"A": 1.0}),
        TargetPortfolio(pd.Timestamp("2024-01-03"), {}),
    )
    result = run_portfolio_backtest(
        prices, schedule, _config(("A",), price_limit_rate=0.10)
    )
    assert not [f for f in result.fills if f.side is Side.SELL]
    assert any(b.reason == REASON_UNFILLED_AT_END for b in result.blocked)
    # 未卖出的持仓仍按最后有效收盘价估值，不被抹掉
    assert result.states[-1].n_positions == 1


# ===================================================================== #
# 双口径与不变量
# ===================================================================== #


def test_dual_allocation_reports_both_with_lot_multiples() -> None:
    """A4/A5 双口径都产出完整结果；`lots` 的成交量必须是整手倍数。"""
    prices = {"A": _frame([("2024-01-02", 10.0, 10.0), ("2024-01-03", 10.0, 10.0)]),
              "B": _frame([("2024-01-02", 30.0, 30.0), ("2024-01-03", 30.0, 30.0)])}
    schedule = (TargetPortfolio(pd.Timestamp("2024-01-02"), {"A": 0.5, "B": 0.5}),)
    out = run_dual_allocation_backtest(prices, schedule, _config(("A", "B"), cash=100_000.0))

    assert set(out) == {"weights", "lots"}
    for mode, res in out.items():
        assert res.performance is not None, f"{mode} 未产出组合绩效"
        _assert_identities(res)
    for f in out["lots"].fills:
        if f.side is Side.BUY:
            assert f.quantity % 100 == 0, f"lots 口径买入量 {f.quantity} 不是整手倍数"


def test_signal_is_not_executed_on_signal_day() -> None:
    """无未来函数：信号日 T 的目标只在 T+1 执行，T 日当天不得成交。"""
    prices = {"A": _frame([("2024-01-02", 10.0, 10.0), ("2024-01-03", 11.0, 11.0)])}
    schedule = (TargetPortfolio(pd.Timestamp("2024-01-02"), {"A": 1.0}),)
    result = run_portfolio_backtest(prices, schedule, _config(("A",)))
    assert all(f.date > pd.Timestamp("2024-01-02") for f in result.fills)
    assert result.fills[0].date == pd.Timestamp("2024-01-03")


def test_missing_price_frame_raises() -> None:
    """缺行情必须报错，不得静默跳过标的（否则组合少了一只而净值看不出来）。"""
    prices = {"A": _frame([("2024-01-02", 10.0, 10.0)])}
    with pytest.raises(ValueError, match="缺少行情数据"):
        run_portfolio_backtest(
            prices,
            (TargetPortfolio(pd.Timestamp("2024-01-02"), {"A": 0.5}),),
            _config(("A", "B")),
        )


def test_out_of_pool_symbol_in_schedule_rejected() -> None:
    prices = {"A": _frame([("2024-01-02", 10.0, 10.0)])}
    with pytest.raises(ValueError, match="池外标的"):
        run_portfolio_backtest(
            prices,
            (TargetPortfolio(pd.Timestamp("2024-01-02"), {"Z": 1.0}),),
            _config(("A",)),
        )
