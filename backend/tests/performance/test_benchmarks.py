"""基准策略测试：B0 现金 / B1 买入持有 / B2 指数。

重点不是「算得出来」，而是**口径不会被悄悄改掉**：
- 买不起 1 手必须记为不可执行，不得记作 0% 收益；
- 涨停日不得买入，且顺延语义要可核；
- 指数必须显式声明价格/全收益口径，不得默认猜测；
- 指数不扣个股费用。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.market.rules import a_share_rules
from deepquant.performance.benchmarks import (
    benchmark_table,
    buy_and_hold,
    cash_benchmark,
    index_benchmark,
)
from helpers import make_config

DATES = pd.to_datetime(
    ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
)


# ===================================================================== #
# B0 现金
# ===================================================================== #


def test_cash_benchmark_is_exactly_zero():
    """现金基准必须严格为 0：不得假设任何无风险利率。"""
    r = cash_benchmark(100_000.0, start_date=DATES[0], end_date=DATES[-1], n_periods=5)
    assert r.cumulative_return == 0.0
    assert r.annualized_return == 0.0
    assert r.max_drawdown_ratio == 0.0
    assert r.annualized_volatility == 0.0
    assert r.includes_trading_costs is False
    assert r.is_complete is True


def test_cash_benchmark_rejects_non_positive_initial():
    with pytest.raises(ValueError, match="有限正数"):
        cash_benchmark(0.0)


# ===================================================================== #
# B1 买入持有
# ===================================================================== #


def test_buy_and_hold_equity_identity():
    """末期权益必须等于「持股数 × 期末收盘 + 剩余现金」，不得有隐藏项。"""
    opens = [10.0, 10.0, 10.5, 11.0, 11.0]
    closes = [10.0, 10.2, 10.8, 11.5, 11.2]
    bh = buy_and_hold(DATES, opens, closes, make_config())

    assert bh.is_executable
    assert bh.quantity % 100 == 0, "必须整手"
    assert bh.buy_date == DATES[0]
    expected_final = bh.quantity * closes[-1] + bh.cash_left
    assert bh.summary.final_value == pytest.approx(expected_final, rel=1e-12)
    assert bh.summary.cumulative_return == pytest.approx(
        bh.summary.final_value / 100_000.0 - 1.0, rel=1e-12
    )


def test_buy_and_hold_charges_buy_fee():
    """买入必须扣佣金：剩余现金 = 初始 − 成交额 − 费用。"""
    bh = buy_and_hold(DATES, [10.0] * 5, [10.0] * 5, make_config())
    assert bh.buy_fees > 0, "买入费用未计入（否则等于免佣金基准）"
    gross = bh.buy_price * bh.quantity
    assert bh.cash_left == pytest.approx(100_000.0 - gross - bh.buy_fees, abs=0.01)


def test_buy_and_hold_unaffordable_is_not_zero_return():
    """买不起 1 手 → 不可执行，且累计收益为 None，**不得**记作 0%。

    「买不起」和「买了不涨」是两件事。把它记成 0% 会让一个不可执行的标的
    看起来像「中性结果」，进而污染基准均值。
    """
    config = make_config(initial_cash=50.0)
    bh = buy_and_hold(DATES, [100.0] * 5, [100.0] * 5, config)

    assert not bh.is_executable
    assert bh.quantity == 0
    assert bh.blocked_reason == "insufficient_cash"
    assert bh.summary.cumulative_return is None
    assert bh.summary.is_complete is False
    assert any("不得把该情形记作 0% 收益" in x for x in bh.summary.limitations)


def test_buy_and_hold_skips_limit_up_day():
    """期初涨停买不进 → 顺延到下一可买日，且原因可核。

    这是 D2（涨跌停未启用）修复后的最关键行为：动量策略的赢家常涨停，
    不建模就会把「买不到」当成「买到了」。
    """
    config = make_config(initial_cash=100_000.0, market_rules=a_share_rules(price_limit_rate=0.10))
    opens = [None, 11.0, 10.5, 10.6, 10.6]   # 第 1 天开盘 = 前收 10 的 +10% 涨停
    closes = [10.0, 10.0, 10.6, 10.7, 10.8]

    bh = buy_and_hold(DATES, opens, closes, config)

    assert bh.is_executable
    assert bh.blocked_reason == "limit_up", "未记录涨停拦截原因"
    assert bh.buy_date == DATES[2], f"应顺延到第 3 天，实际 {bh.buy_date}"


def test_buy_and_hold_without_price_limit_buys_on_limit_day():
    """未配置涨跌停时（旧口径）该日可买 —— 用于证明 D2 确实改变行为。

    这条测试是 D2 的「对照」：两条路径结果不同，才说明配置真的生效，
    而不是写了个永远不触发的参数。
    """
    config = make_config(initial_cash=100_000.0)  # 默认 a_share_rules() → price_limit=None
    opens = [None, 11.0, 10.5, 10.6, 10.6]
    closes = [10.0, 10.0, 10.6, 10.7, 10.8]

    bh = buy_and_hold(DATES, opens, closes, config)

    assert bh.buy_date == DATES[1], "未启用涨跌停时应在第 2 天就买入"
    assert bh.blocked_reason is None
    assert any("未配置涨跌停" in x for x in bh.summary.limitations)


def test_buy_and_hold_documents_dividend_convention():
    """qfq 的股息口径必须声明为「近似」，不得声称已含真实分红。"""
    bh = buy_and_hold(DATES, [10.0] * 5, [10.0] * 5, make_config())
    assert bh.summary.dividend_convention == "approximate_qfq"
    assert any("未验证" in x for x in bh.summary.limitations)


def test_buy_and_hold_requires_aligned_inputs():
    with pytest.raises(ValueError, match="长度不一致"):
        buy_and_hold(DATES, [10.0] * 4, [10.0] * 5, make_config())


def test_buy_and_hold_liquidate_at_end_charges_sell_fee():
    """含平仓成本口径：期末必须扣卖出费用，且低于仅估值口径。"""
    params = ([10.0] * 5, [10.0, 10.5, 11.0, 11.5, 12.0])
    hold = buy_and_hold(DATES, *params, make_config())
    liquidated = buy_and_hold(DATES, *params, make_config(), liquidate_at_end=True)

    assert liquidated.liquidated is True
    assert liquidated.sell_fees > 0
    assert liquidated.summary.final_value < hold.summary.final_value
    assert any("平仓" in x for x in liquidated.summary.assumptions)


# ===================================================================== #
# B2 指数
# ===================================================================== #


def test_index_benchmark_normalizes_to_base():
    """指数基准以首期收盘归一，累计收益 = 末/首 − 1。"""
    closes = [100.0, 110.0, 121.0]
    dates = DATES[:3]
    r = index_benchmark(dates, closes, index_code="000300", index_kind="price")

    assert r.cumulative_return == pytest.approx(0.21)
    assert r.includes_trading_costs is False, "指数不可交易，不得扣任何个股费用"
    assert r.dividend_convention == "none"
    assert r.is_complete is True


def test_index_kind_must_be_explicit_and_distinguishes_dividends():
    r_price = index_benchmark(DATES[:3], [100.0, 110.0, 121.0], index_code="000300", index_kind="price")
    r_total = index_benchmark(
        DATES[:3], [100.0, 110.0, 121.0], index_code="000300", index_kind="total_return"
    )
    assert r_price.dividend_convention == "none"
    assert r_total.dividend_convention == "included"


def test_index_benchmark_rejects_unknown_kind():
    """口径不得默认猜测：价格指数与全收益指数年化可差 1~2pp。"""
    with pytest.raises(ValueError, match="index_kind"):
        index_benchmark(DATES[:3], [100.0, 110.0, 121.0], index_code="000300", index_kind="whatever")


def test_index_benchmark_rejects_non_positive_base():
    with pytest.raises(ValueError, match="有限正数"):
        index_benchmark(DATES[:3], [0.0, 110.0, 121.0], index_code="000300", index_kind="price")


def test_index_benchmark_handles_missing_price():
    r = index_benchmark(
        DATES[:3], [100.0, None, 121.0], index_code="000300", index_kind="price"
    )
    assert r.is_complete is False
    assert r.n_periods == 3


# ===================================================================== #
# 落盘表
# ===================================================================== #


def test_benchmark_table_keeps_none_instead_of_zero():
    """不可计算的指标在落盘时必须保持 None，填 0 会被下游当成「确实为 0」。"""
    bh = buy_and_hold(
        DATES, [100.0] * 5, [100.0] * 5, make_config(initial_cash=50.0)
    )
    rows = benchmark_table([cash_benchmark(1000.0), bh.summary])

    assert rows[0]["kind"] == "cash"
    assert rows[1]["cumulative_return"] is None
    assert rows[1]["final_value"] is None
    assert isinstance(rows[0]["start_date"], str) or rows[0]["start_date"] is None
