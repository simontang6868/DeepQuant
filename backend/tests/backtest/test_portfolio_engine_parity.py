"""组合层与单标的引擎的**成交对账**（证明「不重写核心撮合」不是口号）。

背景
----
A3 的规格（`docs/A3_ACCOUNTING_SPEC.md` §0）承认了一个技术事实：
`run_backtest` 是「单标的、单持仓、单现金」的循环，无法直接表达组合，
因此组合层只复用了它的**撮合原语**（`compute_fees` / `execution_price` /
`round_money` / `limit_prices` / `open_fill_block_reason`），循环是自己写的。

「复用原语」这句话如果只写在注释里，就会在某个改动之后悄悄失效：
有人把滑点算法复制一份、或把费用改成 `price * qty * rate` 少了个 `max(min_commission)`，
表面上看不出来，但组合结果与单标的回测就不再可比。

本文件用**等价场景**把它们钉在一起：同一只标的、同一天、同一笔交易，
两条路径必须给出**相同的成交价、股数、费用、期末权益**。
一旦有人重写撮合，这里会先失败。

等价场景的构造条件（缺一不可）
------------------------------
1. 单标的，避免「组合层现金池」与「引擎单账户」的语义差；
2. 行情 **open == close**（无跳空）：引擎的买入数量以「信号日收盘价」估算后再按
   开盘价回退，跳空时这种「只减不增」会与组合层的「按开盘价重新折算」产生
   真实的口径差 —— 那不是 bug，而是两套模型的已知差异，不应混进对账测试；
3. 涨跌停不启用：两边都显式传 `price_limit_rate=None`。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.engine import run_backtest
from deepquant.backtest.portfolio import (
    PortfolioBacktestConfig,
    TargetPortfolio,
    run_portfolio_backtest,
)
from deepquant.market.rules import a_share_rules
from deepquant.strategies.base import Signal, Strategy


class _BuyThenHold(Strategy):
    """第 0 天买入，之后不再动。"""

    name = "buy_then_hold"
    version = "0.0.1"

    @property
    def params(self) -> dict:
        return {}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        s = pd.Series(0, index=data.index, dtype=int)
        s.iloc[0] = int(Signal.BUY)
        return s


class _BuyThenSell(Strategy):
    """第 0 天买入，第 2 天卖出。"""

    name = "buy_then_sell"
    version = "0.0.1"

    @property
    def params(self) -> dict:
        return {}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        s = pd.Series(0, index=data.index, dtype=int)
        s.iloc[0] = int(Signal.BUY)
        s.iloc[2] = int(Signal.SELL)
        return s


def _flat_frame(prices: list[float]) -> pd.DataFrame:
    """open == close 的行情（无跳空，见模块 docstring 条件 2）。"""
    idx = pd.bdate_range("2024-01-02", periods=len(prices))
    return pd.DataFrame(
        {
            "open": prices,
            "high": prices,
            "low": prices,
            "close": prices,
            "volume": [1_000_000] * len(prices),
        },
        index=idx,
    )


def _backtest_config(symbol: str = "600000.SH", cash: float = 100_000.0) -> BacktestConfig:
    return BacktestConfig(
        symbol=symbol,
        initial_cash=cash,
        market_rules=a_share_rules(price_limit_rate=None),
    )


def _portfolio_config(symbol: str = "600000.SH", cash: float = 100_000.0) -> PortfolioBacktestConfig:
    return PortfolioBacktestConfig(
        symbols=(symbol,),
        initial_cash=cash,
        fee_config=_backtest_config(symbol, cash),
        allocation="lots",  # 整手口径，才和引擎的 lot_size 约束等价
        lot_size=100,
    )


def test_buy_fill_matches_engine() -> None:
    """买入：成交价 / 股数 / 费用逐项相同。"""
    df = _flat_frame([10.0, 10.0, 10.0, 10.0, 10.0])
    engine_res = run_backtest(df, _BuyThenHold(), _backtest_config())
    port_res = run_portfolio_backtest(
        {"600000.SH": df},
        (TargetPortfolio(df.index[0], {"600000.SH": 1.0}),),
        _portfolio_config(),
    )

    e = engine_res.fills[0]
    p = port_res.fills[0]
    assert p.side is e.side
    assert p.date == e.fill_date
    assert p.price == pytest.approx(e.price, abs=1e-9)
    assert p.quantity == e.quantity
    assert p.gross == pytest.approx(round(e.price * e.quantity, 2), abs=0.01)
    assert p.fees_total == pytest.approx(e.fees.total, abs=0.01)
    # 期末权益也必须一致（估值口径相同）
    assert port_res.final_equity == pytest.approx(engine_res.account_states[-1].total_equity, abs=0.01)


def test_sell_fill_matches_engine() -> None:
    """卖出：成交价 / 股数 / 费用（含印花税）逐项相同。"""
    df = _flat_frame([10.0, 10.0, 12.0, 12.0, 12.0])
    engine_res = run_backtest(df, _BuyThenSell(), _backtest_config())
    port_res = run_portfolio_backtest(
        {"600000.SH": df},
        (
            TargetPortfolio(df.index[0], {"600000.SH": 1.0}),
            TargetPortfolio(df.index[2], {}),
        ),
        _portfolio_config(),
    )

    e_sell = next(f for f in engine_res.fills if f.side.value == "SELL")
    p_sell = next(f for f in port_res.fills if f.side.value == "SELL")
    assert p_sell.date == e_sell.fill_date
    assert p_sell.price == pytest.approx(e_sell.price, abs=1e-9)
    assert p_sell.quantity == e_sell.quantity
    assert p_sell.fees_total == pytest.approx(e_sell.fees.total, abs=0.01)
    assert port_res.final_equity == pytest.approx(engine_res.account_states[-1].total_equity, abs=0.01)


def test_equity_path_matches_engine_after_buy() -> None:
    """买入后的逐日权益曲线完全重合（说明估值与记账顺序一致）。"""
    df = _flat_frame([10.0, 10.5, 9.5, 11.0, 10.0])
    engine_res = run_backtest(df, _BuyThenHold(), _backtest_config())
    port_res = run_portfolio_backtest(
        {"600000.SH": df},
        (TargetPortfolio(df.index[0], {"600000.SH": 1.0}),),
        _portfolio_config(),
    )
    engine_equity = [s.total_equity for s in engine_res.account_states]
    port_equity = [s.total_equity for s in port_res.states]
    assert len(engine_equity) == len(port_equity)
    for i, (a, b) in enumerate(zip(engine_equity, port_equity)):
        assert a is not None and b is not None
        assert b == pytest.approx(a, abs=0.01), f"第 {i} 天权益不一致：组合 {b} vs 引擎 {a}"
