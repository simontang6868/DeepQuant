"""组合层配置与调仓调度测试（A1 / A2）。

重点锁三件事：
1. **排序必须确定**：分数并列时按代码升序，否则同一份数据两次跑出不同组合；
2. **空仓是合法状态**：选不出股票时返回空权重，不得强行凑数；
3. **禁止做空**：负权重必须报错，而不是被静默截断成 0。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.portfolio import (
    PortfolioBacktestConfig,
    TargetPortfolio,
    build_rebalance_schedule,
    equal_weight_targets,
    month_end_signal_dates,
    validate_schedule,
)


def _fee_template() -> BacktestConfig:
    """费率模板：symbol / initial_cash 不参与组合层计算，仅承载费率与市场规则。"""
    return BacktestConfig(symbol="TEMPLATE", initial_cash=100_000.0)


# ===================================================================== #
# A1 配置
# ===================================================================== #


def test_config_accepts_basic_pool():
    cfg = PortfolioBacktestConfig(
        symbols=("600519.SH", "000001.SZ"),
        initial_cash=1_000_000.0,
        fee_config=_fee_template(),
    )
    assert cfg.allocation == "weights"
    assert cfg.lot_size == 100


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"symbols": ()}, "不能为空"),
        ({"symbols": ("A", "A")}, "重复"),
        ({"initial_cash": 0.0}, "有限正数"),
        ({"allocation": "cash"}, "allocation"),
        ({"lot_size": 0}, "lot_size"),
        ({"max_weight_per_asset": 1.5}, "max_weight_per_asset"),
    ],
)
def test_config_rejects_invalid(kwargs, match):
    base = dict(
        symbols=("600519.SH",),
        initial_cash=1_000_000.0,
        fee_config=_fee_template(),
    )
    base.update(kwargs)
    with pytest.raises(ValueError, match=match):
        PortfolioBacktestConfig(**base)


# ===================================================================== #
# A2 调仓日历
# ===================================================================== #


def test_month_end_signal_dates_picks_last_trading_day_per_month():
    days = pd.to_datetime(
        ["2024-01-30", "2024-01-31", "2024-02-01", "2024-02-28", "2024-02-29", "2024-03-01"]
    )
    result = month_end_signal_dates(days)
    assert list(result) == [
        pd.Timestamp("2024-01-31"),
        pd.Timestamp("2024-02-29"),
        pd.Timestamp("2024-03-01"),
    ]


def test_month_end_handles_empty_and_rejects_duplicates():
    assert month_end_signal_dates([]) == ()
    with pytest.raises(ValueError, match="重复"):
        month_end_signal_dates(pd.to_datetime(["2024-01-31", "2024-01-31"]))


# ===================================================================== #
# A2 选股与权重
# ===================================================================== #


def test_equal_weight_picks_top_fraction():
    scores = {"A": 5.0, "B": 4.0, "C": 3.0, "D": 2.0, "E": 1.0}
    w = equal_weight_targets(scores, top_fraction=0.2)
    assert w == {"A": 1.0}, "5 只取前 20% 应为 1 只、满仓"


def test_equal_weight_ten_assets_two_positions():
    scores = {f"S{i:02d}": float(i) for i in range(10)}
    w = equal_weight_targets(scores, top_fraction=0.2)
    assert len(w) == 2
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["S09"] == pytest.approx(0.5)


def test_missing_scores_are_dropped_not_filled_with_zero():
    """缺数据的标的必须整只剔除。

    若把 None 填成 0，它会排在所有负分之上、凭空进入组合——
    这正是「停牌股被当成低估值买入」的经典错误。
    """
    scores = {"A": 1.0, "B": None, "C": float("nan"), "D": 0.5}
    w = equal_weight_targets(scores, top_fraction=1.0)
    assert "B" not in w and "C" not in w
    assert set(w) == {"A", "D"}


def test_ties_are_broken_by_symbol_for_reproducibility():
    """分数并列 → 按代码升序。否则同一份数据两次运行可能选出不同组合。"""
    scores = {"600000.SH": 1.0, "000001.SZ": 1.0, "300750.SZ": 1.0}
    w = equal_weight_targets(scores, top_fraction=1 / 3, min_assets=1)
    assert list(w) == ["000001.SZ"], f"并列时应取代码最小者，实际 {list(w)}"


def test_empty_when_below_min_assets():
    """可参与排序的标的不足 → 空仓，不得强行凑数。"""
    w = equal_weight_targets({"A": 1.0}, top_fraction=0.2, min_assets=3)
    assert w == {}


def test_max_weight_caps_position_instead_of_redistributing():
    """超过上限时**降低总仓位**，不把权重挪给第二名。

    挪给第二名会改变「选股结果」，让上限参数偷偷变成选股参数。
    """
    scores = {"A": 3.0, "B": 2.0, "C": 1.0}
    w = equal_weight_targets(scores, top_fraction=1 / 3, max_weight_per_asset=0.5)
    assert w == {"A": 0.5}
    assert sum(w.values()) == pytest.approx(0.5), "剩余应为现金，不得转投他标的"


@pytest.mark.parametrize("fraction", [0.0, -0.1, 1.01])
def test_top_fraction_bounds(fraction):
    with pytest.raises(ValueError, match="top_fraction"):
        equal_weight_targets({"A": 1.0}, top_fraction=fraction)


# ===================================================================== #
# 目标组合校验
# ===================================================================== #


def test_target_rejects_negative_weight_no_shorting():
    """负权重 = 做空，当前引擎无法表达（SELL 仅平仓），必须报错。"""
    with pytest.raises(ValueError, match="不支持做空"):
        TargetPortfolio(signal_date=pd.Timestamp("2024-01-31"), weights={"A": -0.2})


def test_target_rejects_weight_sum_above_one():
    with pytest.raises(ValueError, match="超过 1.0"):
        TargetPortfolio(
            signal_date=pd.Timestamp("2024-01-31"),
            weights={"A": 0.7, "B": 0.7},
        )


def test_target_cash_only_is_valid():
    t = TargetPortfolio(signal_date=pd.Timestamp("2024-01-31"))
    assert t.is_cash_only is True


# ===================================================================== #
# 调度装配
# ===================================================================== #


def test_build_schedule_sorts_dates():
    scores = {
        pd.Timestamp("2024-02-29"): {"A": 1.0},
        pd.Timestamp("2024-01-31"): {"A": 2.0},
    }
    schedule = build_rebalance_schedule(scores, top_fraction=1.0)
    assert [t.signal_date for t in schedule] == [
        pd.Timestamp("2024-01-31"),
        pd.Timestamp("2024-02-29"),
    ]


def test_build_schedule_rejects_duplicate_dates():
    """Mapping 本身不会重复键，因此改从「同一天出现两次」的序列语义上验证。

    这里用 Timestamp 与其等值字符串作为两个键：它们在 dict 中是不同的键，
    但归一化为 Timestamp 后会撞车 —— 这正是需要拦下的情形。
    """
    scores = {pd.Timestamp("2024-01-31"): {"A": 1.0}, "2024-01-31": {"A": 2.0}}
    with pytest.raises(ValueError, match="重复"):
        build_rebalance_schedule(scores)


def test_validate_schedule_rejects_out_of_pool_symbol():
    schedule = (TargetPortfolio(pd.Timestamp("2024-01-31"), {"ZZZ": 1.0}),)
    with pytest.raises(ValueError, match="池外标的"):
        validate_schedule(schedule, symbols=("A", "B"))


def test_validate_schedule_rejects_non_increasing_dates():
    schedule = (
        TargetPortfolio(pd.Timestamp("2024-02-29"), {"A": 1.0}),
        TargetPortfolio(pd.Timestamp("2024-01-31"), {"A": 1.0}),
    )
    with pytest.raises(ValueError, match="严格递增"):
        validate_schedule(schedule, symbols=("A",))


def test_validate_schedule_accepts_valid():
    schedule = (
        TargetPortfolio(pd.Timestamp("2024-01-31"), {"A": 0.5}),
        TargetPortfolio(pd.Timestamp("2024-02-29"), {}),
    )
    validate_schedule(schedule, symbols=("A", "B"))
