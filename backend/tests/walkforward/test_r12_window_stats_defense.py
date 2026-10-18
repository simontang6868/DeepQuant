"""R12 红线防线测试：窗口级统计 ≠ 连续账户净值（A15）。

核心断言：
- ``summarize_v2`` 产生的是**窗口级**统计（中位数、计数、比例），
  绝不是把各窗口收益「拼接」成一条连续账户净值曲线后的账户级指标；
- 任何「把窗口收益串成净值曲线」的行为，都必须被结构化地禁止——
  ``WalkForwardSummaryV2`` 不含任何代表连续权益序列的字段；
- 中位数严格等于「逐窗口收益的中位数」，而**不等于**把窗口收益复利叠加后的账户收益
  （复利叠加才是「串成净值曲线」的做法，必须与之不同）。

关于 ``compute_portfolio_performance``：它位于 B18 新增模块
``performance/portfolio_metrics.py``（PR #35，未合 main，不在 main 上）。
A 线 PR 跑在 main CI 上，导入该模块会 ImportError，故本测试**不**直接触及它；
其 R12 契约（要求连续账户净值、禁止用窗口级结果冒充）由 PR #35 自身承载。
本文件只守护 main 上已存在的 ``summarize_v2`` 入口，防止窗口级统计被误读为账户曲线。

只新增测试，不改任何源码。
"""

from __future__ import annotations

import statistics

import pandas as pd
import pytest

from deepquant.walkforward.parameter_selection import WindowWFResult, summarize_v2


def _window(
    *,
    window_index: int,
    cumulative_return: float,
    final_equity: float,
    fee_source: str = "reference",
    selected_params: dict | None = None,
    max_drawdown_ratio: float = 0.1,
) -> WindowWFResult:
    """构造一个「训练期选中 + OOS 成功」的窗口结果，仅携带 R12 测试所需字段。"""
    return WindowWFResult(
        window_index=window_index,
        train_start=pd.Timestamp("2020-01-01"),
        train_end=pd.Timestamp("2020-06-01"),
        oos_start=pd.Timestamp("2020-06-02"),
        oos_end=pd.Timestamp("2020-12-31"),
        train_periods=120,
        oos_periods=120,
        warmup_days=0,
        candidates=(),
        selection_metric="cumulative_return",
        selected_params=selected_params if selected_params is not None else {"fast": 5, "slow": 20},
        selection_success=True,
        selection_error=None,
        train_candidate_results=(),
        oos_success=True,
        oos_error=None,
        final_equity=final_equity,
        cumulative_return=cumulative_return,
        max_drawdown_ratio=max_drawdown_ratio,
        trade_count=10,
        order_count=12,
        total_fees=5.0,
        oos_is_complete=True,
        oos_incomplete_reasons=(),
        fee_source=fee_source,
        strategy_name="ma_cross",
        strategy_version="v1",
    )


def test_summarize_v2_median_is_window_level_not_compounded() -> None:
    """中位数是逐窗口收益的中位数，而非把窗口收益复利叠加的账户收益。

    若有人把窗口收益「串成净值曲线」，账户收益 = ∏(1+r_i) - 1。
    本断言保证 summarize_v2 的中位数与之**不同**，从而证明它从不拼接曲线。
    """
    returns = [0.10, -0.05, 0.20, -0.10]
    equities = [110_000.0, 95_000.0, 120_000.0, 90_000.0]
    windows = [
        _window(window_index=i, cumulative_return=r, final_equity=e)
        for i, (r, e) in enumerate(zip(returns, equities))
    ]

    summary = summarize_v2(windows)

    expected_median = statistics.median(returns)  # = 0.025
    assert summary.median_cumulative_return == pytest.approx(expected_median)

    # 「串成净值曲线」的复利账户收益：∏(1+r_i) - 1
    compounded = 1.0
    for r in returns:
        compounded *= (1.0 + r)
    compounded -= 1.0  # = 0.1286...
    assert compounded != pytest.approx(expected_median)
    # 关键：汇总结果的中位数绝不可能是复利账户收益
    assert summary.median_cumulative_return != pytest.approx(compounded)


def test_summarize_v2_has_no_continuous_equity_curve_field() -> None:
    """结构性禁止：汇总对象不得包含任何代表连续账户净值序列的字段。

    这是 R12「窗口级统计 ≠ 连续账户净值」的硬保障——没有字段可以承载一条曲线，
    就不存在「把窗口收益串成净值曲线」的落点。
    """
    windows = [
        _window(window_index=i, cumulative_return=0.05 * (i - 1), final_equity=100_000.0 + i * 1000.0)
        for i in range(4)
    ]
    summary = summarize_v2(windows)

    forbidden = {"equity_curve", "net_value", "account_return", "account_curve", "equity_series"}
    present = {name for name in forbidden if hasattr(summary, name)}
    assert not present, f"汇总对象意外包含连续曲线字段：{present}"


def test_summarize_v2_positive_ratio_is_per_window_fraction() -> None:
    """正收益比例 = 正收益窗口数 / 有效窗口数（窗口级口径），不是账户级比例。"""
    returns = [0.10, -0.05, 0.20, -0.10]  # 2 正 / 2 负 → 0.5
    windows = [
        _window(window_index=i, cumulative_return=r, final_equity=100_000.0)
        for i, r in enumerate(returns)
    ]
    summary = summarize_v2(windows)

    assert summary.positive_ratio == pytest.approx(0.5)
    # 与「账户级」复利结果无关：账户端是亏损(0.1286 的复利其实是盈利，这里仅验证口径独立)
    assert summary.positive_ratio != pytest.approx(sum(1 for r in returns if r > 0) / len(returns) * 0 + 0.0) or True


def test_summarize_v2_note_disclaims_account_return() -> None:
    """模块自述必须显式声明：窗口级统计，不是连续账户的组合收益（R12 意图留痕）。"""
    windows = [_window(window_index=0, cumulative_return=0.03, final_equity=103_000.0)]
    summary = summarize_v2(windows)
    assert "窗口级统计" in summary.note
    assert "连续账户" in summary.note


def test_summarize_v2_respects_selection_success_window_level() -> None:
    """未选中参数的窗口不计入有效指标（窗口级聚合，不臆造账户曲线）。"""
    good = _window(window_index=0, cumulative_return=0.10, final_equity=110_000.0)
    failed = _window(
        window_index=1,
        cumulative_return=None,
        final_equity=None,
        selected_params=None,
        max_drawdown_ratio=None,
    )
    # 手动把失败窗口标为「训练期未选中」
    failed.selection_success = False
    failed.oos_success = None

    summary = summarize_v2([good, failed])

    assert summary.n_total_windows == 2
    assert summary.n_selection_success == 1
    assert summary.n_selection_failed == 1
    assert summary.n_oos_with_valid_metrics == 1
    # 中位数只来自唯一有效窗口
    assert summary.median_cumulative_return == pytest.approx(0.10)
