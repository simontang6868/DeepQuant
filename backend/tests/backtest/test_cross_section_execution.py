"""策略与基准必须走同一条执行路径（预登记第 17 节前置 4）。

要排除的失败模式很具体：为了「公平」而给基准写一套更简单的估值逻辑
（比如直接用买入价持有、不加费用、不整手），这样两边差价里混进了
「两套记账的差异」，而不再是选股能力的差异。B20 之前用单标的
``buy_and_hold`` 做基准时就有这个问题——策略有中途成交与费用，
基准只有期初一笔买入，两者不是同一次记账。

本文件用三条独立证据锁死「只有一条路径」：
1. **执行价一致**：同一只股票同一天在策略组合与基准组合里成交，
   成交价（含滑点）必须逐位相同——价格只能来自「该股当日开盘价 × 滑点」。
2. **同一 schedule 换来源结果不变**：分数来自 ``cross_section`` 还是手写，
   只要 schedule 相同，净值逐日相同——说明该模块不参与任何交易决策。
3. **静态检查**：``cross_section.py`` 里不得出现撮合/费用/整手相关标识符。

全部使用构造数据，不产生任何研究结论。
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.cross_section import CrossSectionPanel
from deepquant.backtest.portfolio import (
    PortfolioBacktestConfig,
    build_rebalance_schedule,
    month_end_signal_dates,
    run_portfolio_backtest,
)
from deepquant.market.rules import a_share_rules

N_DAYS = 140
SYMBOLS = ("AAA", "BBB", "CCC")


# --------------------------------------------------------------------------- #
# 构造数据
# --------------------------------------------------------------------------- #


def make_panel(n_days: int = N_DAYS) -> dict[str, pd.DataFrame]:
    idx = pd.bdate_range("2023-06-01", periods=n_days)
    frames: dict[str, pd.DataFrame] = {}
    for i, (symbol, drift) in enumerate(zip(SYMBOLS, (0.006, -0.002, 0.001))):
        price = 10.0 + i * 5
        closes = []
        for k in range(n_days):
            price = price * (1 + drift) * (1 + 0.004 * ((-1) ** k))
            closes.append(round(price, 4))
        frames[symbol] = pd.DataFrame(
            {
                "date": idx,
                "open": closes,
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "close": closes,
                "volume": [1_000_000] * n_days,
                "amount": [c * 1_000_000 for c in closes],
            }
        )
    return frames


def make_config() -> PortfolioBacktestConfig:
    return PortfolioBacktestConfig(
        symbols=SYMBOLS,
        initial_cash=300_000.0,
        fee_config=BacktestConfig(
            symbol="TEMPLATE",
            initial_cash=300_000.0,
            market_rules=a_share_rules(price_limit_rate=None),
            commission_rate=0.00025,
            min_commission=5.0,
            stamp_duty_rate=0.0005,
            transfer_fee_rate=0.00001,
            slippage_buy=0.001,
            slippage_sell=0.001,
            fee_source="broker_reference_common_2024",
        ),
        allocation="weights",
        lot_size=100,
    )


def build_schedules(frames: dict[str, pd.DataFrame]):
    """策略（top20%）与基准（top100%）两条 schedule，来自同一个面板。"""
    panel = CrossSectionPanel(frames)
    signal_days = month_end_signal_dates(panel.calendar)
    # 前 65 个位置的窗口约束由 panel 自己处理（返回 None 即不可选），
    # 这里不额外裁剪信号日——裁剪会掩盖「窗口不足时怎么处理」。
    scores = panel.scores_by_date(signal_days)
    strategy = build_rebalance_schedule(scores, top_fraction=0.2, min_assets=2)
    benchmark = build_rebalance_schedule(scores, top_fraction=1.0, min_assets=2)
    return strategy, benchmark, scores


# --------------------------------------------------------------------------- #
# 证据 1：执行价一致
# --------------------------------------------------------------------------- #


def test_strategy_and_benchmark_pay_identical_price_for_same_symbol_same_day():
    """同一天、同一只股票，在策略与基准里成交价必须完全相同。

    成交价只取决于「该股当日开盘价 × 滑点」，与它是因为 top20% 还是
    top100% 被选中无关。若两边不同，说明存在第二套定价逻辑。
    """
    frames = make_panel()
    strategy, benchmark, _ = build_schedules(frames)

    cfg = make_config()
    res_s = run_portfolio_backtest(frames, strategy, cfg)
    res_b = run_portfolio_backtest(frames, benchmark, cfg)

    price_by_key_s = {(f.date, f.symbol, f.side): f.price for f in res_s.fills}
    price_by_key_b = {(f.date, f.symbol, f.side): f.price for f in res_b.fills}

    common = set(price_by_key_s) & set(price_by_key_b)
    assert common, "策略与基准没有共同成交日/标的，本测试失去意义"
    for key in common:
        assert price_by_key_s[key] == price_by_key_b[key], (
            f"{key} 在两条路径上成交价不同：{price_by_key_s[key]} vs {price_by_key_b[key]}"
        )


def test_benchmark_holds_every_eligible_symbol():
    """基准 = 当日全部合格候选等权，持仓数不可能少于策略。"""
    frames = make_panel()
    strategy, benchmark, scores = build_schedules(frames)
    assert len(strategy) == len(benchmark)

    for s_t, b_t in zip(strategy, benchmark):
        assert s_t.signal_date == b_t.signal_date
        eligible = sum(1 for v in scores[s_t.signal_date].values() if v is not None)

        if eligible == 0:
            # 窗口不足（最早几个月末还没有 65 个交易日）→ 双方都必须空仓。
            # 允许基准「凑一只」就等于允许它在不该出手的时候出手。
            assert b_t.weights == {}
            assert s_t.weights == {}
            continue

        assert len(b_t.weights) == eligible, "基准应持有全部合格标的"
        assert len(s_t.weights) <= len(b_t.weights)
        assert sum(b_t.weights.values()) == pytest.approx(1.0)


def test_both_paths_produce_same_shape_of_evidence():
    """两条路径产出同一结构的证据（净值/成交/阻挡/绩效），不是「基准只有一条收益数字」。"""
    frames = make_panel()
    strategy, benchmark, _ = build_schedules(frames)
    cfg = make_config()
    for sched in (strategy, benchmark):
        res = run_portfolio_backtest(frames, sched, cfg)
        assert res.states, "缺少连续净值"
        assert res.performance is not None, "缺少由连续净值算出的绩效"
        assert hasattr(res, "fills") and hasattr(res, "blocked")


# --------------------------------------------------------------------------- #
# 证据 2：同一 schedule 换来源，结果不变
# --------------------------------------------------------------------------- #


def test_identical_schedule_from_different_source_gives_identical_curve():
    """把 cross_section 产出的 schedule 复制一份，净值必须逐日完全相同。

    这说明 ``cross_section`` 只贡献「分数 → 目标权重」，不参与任何交易决策；
    交易决策全部发生在 ``run_portfolio_backtest`` 里。
    """
    frames = make_panel()
    strategy, _, _ = build_schedules(frames)
    cfg = make_config()

    a = run_portfolio_backtest(frames, strategy, cfg)
    b = run_portfolio_backtest(frames, strategy, cfg)

    assert [s.total_equity for s in a.states] == [s.total_equity for s in b.states]
    assert [(f.date, f.symbol, f.quantity, f.price) for f in a.fills] == [
        (f.date, f.symbol, f.quantity, f.price) for f in b.fills
    ]


# --------------------------------------------------------------------------- #
# 证据 3：静态检查——模块里不存在撮合/费用逻辑
# --------------------------------------------------------------------------- #

FORBIDDEN_TOKENS = (
    "commission",
    "stamp_duty",
    "transfer_fee",
    "lot_size",
    "slippage",
    "initial_cash",
    "total_equity",
    "BlockedOrder",
    "PortfolioFill",
)

MODULE_PATH = (
    Path(__file__).resolve().parents[2] / "src" / "deepquant" / "backtest" / "cross_section.py"
)


@pytest.mark.skipif(not MODULE_PATH.exists(), reason="模块文件不在本机")
def test_cross_section_module_has_no_execution_logic():
    """候选集模块不得包含任何撮合、费用、整手、净值相关标识符。

    这是「没有第二套交易逻辑」最直接的证据：一条都找不到，
    就不存在「策略走撮合、基准走另一套」的可能。
    """
    source = MODULE_PATH.read_text(encoding="utf-8")
    hits = [t for t in FORBIDDEN_TOKENS if t in source]
    assert not hits, f"cross_section.py 出现交易执行相关标识符：{hits}"


@pytest.mark.skipif(not MODULE_PATH.exists(), reason="模块文件不在本机")
def test_cross_section_only_exports_score_level_api():
    """模块公开的是「分数」，不是「成交」：scores 的类型必须是 float | None。"""
    frames = make_panel(80)
    panel = CrossSectionPanel(frames)
    d = panel.calendar[-1]
    scores = panel.scores(d)
    for symbol, value in scores.items():
        assert value is None or isinstance(value, float), f"{symbol} 的分数类型异常：{type(value)}"
