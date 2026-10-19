"""组合层自检容差的回归测试。

背景（真实事故）
----------------
``PortfolioState.total_equity`` 存的是 ``round_money(cash + market_value)``，
与未舍入的和最多差 0.005（合法舍入）。但当差额**恰好**是一分钱时，
二进制表示是 ``0.0100000000093…``，用 ``> MONEY_EPS`` 比较会判成不一致。

实测：3 只等权回测（``selection=equal_weight_all``）在 59 个交易日上全部命中，
整次回测以 ``RuntimeError: 组合回测内部一致性检查失败`` 抛掉——
账是对的，检查器在浮点边界上误报。

本文件锁两件事：
1. 恰好一分钱的差额**不报**（修掉误报）；
2. 两分钱及以上的差额**仍然报**（容差没有被放宽到掩盖真实错账）。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.portfolio import (
    ACCOUNTING_TOLERANCE,
    MONEY_EPS,
    PortfolioBacktestConfig,
    PortfolioBacktestResult,
    PortfolioFill,
    PortfolioState,
)
from deepquant.market.rules import Side


def _result_with_state(*, total_equity: float, cash: float, market_value: float):
    config = PortfolioBacktestConfig(
        symbols=("AAA01",),
        initial_cash=1_000_000.0,
        fee_config=BacktestConfig(symbol="PORTFOLIO", initial_cash=1_000_000.0),
    )
    state = PortfolioState(
        date=pd.Timestamp("2022-03-09"),
        cash=cash,
        market_value=market_value,
        total_equity=total_equity,
        n_positions=1,
        n_stale_positions=0,
        stale_weight_ratio=0.0,
        low_confidence_valuation=False,
    )
    return PortfolioBacktestResult(
        config=config,
        schedule=(),
        states=(state,),
        fills=(),
        blocked=(),
        performance=None,
        limitations=(),
        data_quality_issues=(),
        is_complete=True,
        incomplete_reasons=(),
    )


def test_tolerance_is_above_one_cent() -> None:
    """容差必须严格大于一分钱，否则「恰差一分」会因浮点表示而误报。"""
    assert ACCOUNTING_TOLERANCE > MONEY_EPS
    # 余量必须远小于一分钱：不能靠放大容差来换「不报错」
    assert ACCOUNTING_TOLERANCE - MONEY_EPS < 1e-6


def test_exact_one_cent_gap_is_not_reported() -> None:
    """真实触发过的数值：舍入后 1095910.44，未舍入的和是 1095910.45。"""
    result = _result_with_state(
        total_equity=1095910.44, cash=3.8, market_value=1095906.65
    )
    gap = abs(1095910.44 - (3.8 + 1095906.65))
    # 关键：这个差额在浮点里**严格大于** 0.01，正是旧实现误报的原因
    assert gap > MONEY_EPS
    assert result.consistency_issues() == []


def test_two_cent_gap_is_still_reported() -> None:
    """容差只覆盖「一分钱 + 浮点噪声」，两分钱必须报出来。"""
    result = _result_with_state(total_equity=1000.02, cash=1000.0, market_value=0.0)
    issues = result.consistency_issues()
    assert len(issues) == 1
    assert "权益" in issues[0]


def test_negative_cash_beyond_tolerance_is_reported() -> None:
    result = _result_with_state(total_equity=1000.0, cash=-1.0, market_value=1001.0)
    issues = result.consistency_issues()
    assert any("现金为负" in i for i in issues)


def test_fill_amount_mismatch_is_reported() -> None:
    """成交额与 价×量 不符必须报（这条与容差无关，防止被顺手放宽）。"""
    config = PortfolioBacktestConfig(
        symbols=("AAA01",),
        initial_cash=1_000_000.0,
        fee_config=BacktestConfig(symbol="PORTFOLIO", initial_cash=1_000_000.0),
    )
    fill = PortfolioFill(
        fill_id="F1",
        date=pd.Timestamp("2022-03-09"),
        symbol="AAA01",
        side=Side.BUY,
        price=10.0,
        quantity=100,
        gross=1000.0 + MONEY_EPS * 2,  # 比 价×量 多两分钱
        fees_total=5.0,
        reason="rebalance",
    )
    result = PortfolioBacktestResult(
        config=config,
        schedule=(),
        states=(),
        fills=(fill,),
        blocked=(),
        performance=None,
        limitations=(),
        data_quality_issues=(),
        is_complete=True,
        incomplete_reasons=(),
    )
    assert any("成交额" in i for i in result.consistency_issues())
