"""端到端联调：3 只构造数据走完「月末信号 → 目标权重 → 调仓 → 费用 → 净值 → 绩效」。

这是 G0-7 的验收用例。它与单元测试的区别在于**串联**：
单测分别验证「选股对不对」「费用算得对不对」「停牌估值对不对」，
而这里验证「四件事串起来之后，净值和绩效仍是自洽的」。

刻意用**构造数据**而不是真实行情：真实行情的期望值无法手算，
一旦断言失败，你分不清是代码错了还是记忆错了。构造数据的每一步都可以手推。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.portfolio import (
    PortfolioBacktestConfig,
    build_rebalance_schedule,
    month_end_signal_dates,
    run_dual_allocation_backtest,
)
from deepquant.market.rules import a_share_rules


def _panel(n_days: int = 120) -> dict[str, pd.DataFrame]:
    """3 只标的、120 个交易日；走势各不相同（一只上涨、一只下跌、一只震荡）。"""
    idx = pd.bdate_range("2024-01-02", periods=n_days)
    frames: dict[str, pd.DataFrame] = {}
    for symbol, drift, base in (("AAA", 0.004, 10.0), ("BBB", -0.003, 20.0), ("CCC", 0.0, 5.0)):
        closes = []
        price = base
        for i in range(n_days):
            price = price * (1 + drift) * (1 + 0.01 * ((-1) ** i))
            closes.append(round(price, 4))
        frames[symbol] = pd.DataFrame(
            {
                "open": closes,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "close": closes,
                "volume": [1_000_000] * n_days,
            },
            index=idx,
        )
    return frames


def _scores(frames: dict[str, pd.DataFrame]) -> dict[pd.Timestamp, dict[str, float]]:
    """用「过去 20 日收益」当分数：这就是横截面动量的最小形态（仅用于联调）。"""
    calendar = sorted(set().union(*(set(f.index) for f in frames.values())))
    signal_days = month_end_signal_dates(calendar)
    out: dict[pd.Timestamp, dict[str, float]] = {}
    for d in signal_days:
        row: dict[str, float] = {}
        for symbol, df in frames.items():
            hist = df.loc[:d, "close"]
            if len(hist) < 21:
                row[symbol] = None
            else:
                row[symbol] = float(hist.iloc[-1] / hist.iloc[-21] - 1.0)
        out[pd.Timestamp(d)] = row
    return out


def _config(mode: str) -> PortfolioBacktestConfig:
    return PortfolioBacktestConfig(
        symbols=("AAA", "BBB", "CCC"),
        initial_cash=300_000.0,
        fee_config=BacktestConfig(
            symbol="TEMPLATE",
            initial_cash=300_000.0,
            market_rules=a_share_rules(price_limit_rate=None),
        ),
        allocation=mode,  # type: ignore[arg-type]
    )


def _schedule(frames: dict[str, pd.DataFrame]):
    return build_rebalance_schedule(_scores(frames), top_fraction=1.0, min_assets=2)


def test_end_to_end_produces_self_consistent_curve() -> None:
    """闭环：调仓有成交、成交有费用、费用进现金、现金与市值合成净值。"""
    frames = _panel()
    schedule = _schedule(frames)
    assert len(schedule) >= 3, "120 个交易日应至少产生 3 个月末信号"

    results = run_dual_allocation_backtest(frames, schedule, _config("weights"))
    for mode, res in results.items():
        assert res.states, f"{mode} 未产出净值"
        assert res.fills, f"{mode} 未产生任何成交（调仓失效）"
        # 每个状态都满足权益恒等式
        for st in res.states:
            assert st.total_equity == pytest.approx(st.cash + st.market_value, abs=0.01)
            assert st.cash >= -0.01
        # 绩效必须由连续净值算出（R12 的正解）
        assert res.performance is not None
        assert res.performance.stats.final_value == pytest.approx(res.final_equity, abs=0.01)
        assert res.performance.turnover.total_traded_amount > 0
        assert res.performance.cost.total_fees > 0
        assert res.is_complete


def test_weights_and_lots_diverge_only_by_rounding() -> None:
    """双口径的差异应来自「整手截断 + 最低佣金」，而不是来自不同的交易逻辑。

    断言的是**方向性事实**，不是数值大小：整手口径的可投资金额不可能超过
    1 股口径（截断只会更少），因此其成交额应不高于 1 股口径。
    """
    frames = _panel()
    schedule = _schedule(frames)
    results = run_dual_allocation_backtest(frames, schedule, _config("weights"))

    w = results["weights"].performance
    l = results["lots"].performance
    assert w is not None and l is not None
    assert l.turnover.total_traded_amount <= w.turnover.total_traded_amount + 1.0
    # 整手约束**只作用于买入**：A 股买入必须整手，卖出允许零股。
    # 若这里把卖出也断言成整手倍数，就等于假设了一条不存在的规则。
    for f in results["lots"].fills:
        if f.side.value == "BUY":
            assert f.quantity % 100 == 0, f"买入 {f.quantity} 股不是整手倍数"


def test_same_inputs_produce_identical_curve() -> None:
    """可复现：同一份输入跑两次，净值逐日完全相同（无隐藏随机性/字典序依赖）。"""
    frames = _panel()
    schedule = _schedule(frames)
    a = run_dual_allocation_backtest(frames, schedule, _config("weights"))["weights"]
    b = run_dual_allocation_backtest(frames, schedule, _config("weights"))["weights"]
    assert [s.total_equity for s in a.states] == [s.total_equity for s in b.states]
    assert [f.fill_id for f in a.fills] == [f.fill_id for f in b.fills]


def test_costs_reduce_final_equity_vs_zero_cost_run() -> None:
    """费用必须真的扣掉：同一批信号下，零费用口径的期末权益应高于有费用口径。

    这条不是同义反复——它跑了一次**真的**零费用回测（费率、最低佣金、滑点全为 0），
    如果费用被漏记或只在部分成交上扣，两者会相等。
    """
    frames = _panel()
    schedule = _schedule(frames)

    with_costs = run_dual_allocation_backtest(frames, schedule, _config("weights"))["weights"]

    zero_cost_config = PortfolioBacktestConfig(
        symbols=("AAA", "BBB", "CCC"),
        initial_cash=300_000.0,
        fee_config=BacktestConfig(
            symbol="TEMPLATE",
            initial_cash=300_000.0,
            market_rules=a_share_rules(price_limit_rate=None),
            commission_rate=0.0,
            min_commission=0.0,
            stamp_duty_rate=0.0,
            transfer_fee_rate=0.0,
            slippage_buy=0.0,
            slippage_sell=0.0,
        ),
        allocation="weights",
    )
    from deepquant.backtest.portfolio import run_portfolio_backtest

    no_costs = run_portfolio_backtest(frames, schedule, zero_cost_config)

    assert with_costs.performance is not None and no_costs.performance is not None
    assert with_costs.performance.cost.total_fees > 0
    assert no_costs.performance.cost.total_fees == 0.0
    assert no_costs.final_equity > with_costs.final_equity, (
        "零费用口径的期末权益必须更高；若相等说明费用没有被真正扣除"
    )
