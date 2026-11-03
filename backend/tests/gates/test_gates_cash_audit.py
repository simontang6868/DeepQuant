"""现金归因（L3q）测试。

重点不在"能跑通"，而在四条**容易被做假**的口径：

1. **可加性必须精确**：各分量之和 == ``avg_cash_ratio``。差一点点就说明分摊方式把
   可加性弄丢了，报告里「占 94%」这类说法会立刻失真。
2. **恒等式残差必须报错而不是被吞**：``cash_after`` 与分量之和、与成交回放现金
   两路都要对上。对上不了还继续出数，等于把账做平了再报告。
3. **三类无价必须分开**：行情终止 / 尚未上市 / 停牌。混成一个 ``no_price`` 会把
   数据缺陷说成现实约束——这是本项目最容易自欺的一步。
4. **「不足一手」与「资金不足」不能混**：前者是资金门槛（可以靠加钱解决），后者
   是换手与先卖后买顺序（加钱解决不了）。
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.portfolio import (
    PortfolioBacktestConfig,
    TargetPortfolio,
    run_portfolio_backtest,
)
from deepquant.gates.cash_audit import (
    BASE_COMPONENTS,
    CASH_CAP,
    CASH_DEAD,
    CASH_NOT_LISTED,
    CASH_OTHER,
    CASH_PRESTART,
    CASH_ROUNDING,
    CASH_SUSPENDED,
    CashAuditError,
    classify_unpriced,
    replay_ledger,
    spearman,
    tradable_only_schedule,
)
from deepquant.gates.cash_audit import audit_cash

# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _flat_frame(periods: int, price: float = 10.0) -> pd.DataFrame:
    idx = pd.bdate_range("2020-01-01", periods=periods)
    return pd.DataFrame(
        {
            "open": price,
            "high": price * 1.01,
            "low": price * 0.99,
            "close": price,
            "volume": 1e6,
        },
        index=idx,
    )


def _free_config(cash: float, allocation: str = "weights") -> PortfolioBacktestConfig:
    return PortfolioBacktestConfig(
        symbols=("A", "B"),
        initial_cash=cash,
        fee_config=BacktestConfig(
            symbol="TEST",
            initial_cash=cash,
            commission_rate=0.0,
            min_commission=0.0,
            stamp_duty_rate=0.0,
            transfer_fee_rate=0.0,
            slippage_buy=0.0,
            slippage_sell=0.0,
        ),
        allocation=allocation,  # type: ignore[arg-type]
    )


def _run(frames, schedule, config):
    return run_portfolio_backtest(frames, schedule, config)


def _schedule(pairs, index) -> tuple[TargetPortfolio, ...]:
    """``pairs`` = [(第几天, {symbol: weight}), ...]"""
    return tuple(
        TargetPortfolio(signal_date=index[day], weights=weights) for day, weights in pairs
    )


# --------------------------------------------------------------------------- #
# 一、无价成因分类
# --------------------------------------------------------------------------- #
def test_classify_unpriced_separates_three_causes():
    idx = pd.bdate_range("2020-01-01", periods=10)
    assert classify_unpriced(idx, idx[0] - pd.Timedelta(days=1)) == CASH_NOT_LISTED
    assert classify_unpriced(idx, idx[-1] + pd.Timedelta(days=1)) == CASH_DEAD
    # 区间内但缺行 = 停牌
    assert classify_unpriced(idx, idx[5]) == CASH_SUSPENDED


def test_classify_unpriced_rejects_empty_index():
    with pytest.raises(CashAuditError):
        classify_unpriced(pd.DatetimeIndex([]), pd.Timestamp("2020-01-01"))


# --------------------------------------------------------------------------- #
# 二、回放与引擎逐分一致
# --------------------------------------------------------------------------- #
def test_replay_matches_engine_cash():
    idx = pd.bdate_range("2020-01-01", periods=60)
    frames = {"A": _flat_frame(60, 10.0), "B": _flat_frame(60, 20.0)}
    schedule = _schedule([(0, {"A": 0.5, "B": 0.5})], idx)
    result = _run(frames, schedule, _free_config(1_000_000.0))
    ledger = replay_ledger(result, symbols=["A", "B"], initial_cash=1_000_000.0)
    assert ledger.max_cash_gap == pytest.approx(0.0, abs=1e-9)
    assert len(ledger.cash_daily) == len(result.states)


def test_audit_refuses_when_replay_disagrees():
    """回放对不上时必须抛错，不得降级继续出数。"""
    idx = pd.bdate_range("2020-01-01", periods=60)
    frames = {"A": _flat_frame(60, 10.0), "B": _flat_frame(60, 20.0)}
    schedule = _schedule([(0, {"A": 0.5, "B": 0.5})], idx)
    result = _run(frames, schedule, _free_config(1_000_000.0))
    broken = replace(result, fills=())  # 现金不可能还等于初始资金
    with pytest.raises(CashAuditError):
        audit_cash(broken, frames, schedule, initial_cash=1_000_000.0, unit=1)


# --------------------------------------------------------------------------- #
# 三、可加性与残差
# --------------------------------------------------------------------------- #
def test_components_sum_to_avg_cash_ratio():
    idx = pd.bdate_range("2020-01-01", periods=80)
    frames = {"A": _flat_frame(80, 10.0), "B": _flat_frame(80, 20.0)}
    schedule = _schedule([(0, {"A": 0.5, "B": 0.5}), (40, {"A": 1.0})], idx)
    result = _run(frames, schedule, _free_config(1_000_000.0))
    audit = audit_cash(result, frames, schedule, initial_cash=1_000_000.0, unit=1)
    assert set(audit.components) == set(BASE_COMPONENTS)
    assert sum(audit.components.values()) == pytest.approx(audit.avg_cash_ratio, abs=1e-12)
    assert audit.component_sum_gap == pytest.approx(0.0, abs=1e-12)
    assert audit.residual_identity_max < 1e-6
    assert audit.residual_ledger_max < 1.0


def test_prestart_day_is_not_buried_in_other():
    """首个建仓日之前的空仓必须单列：它是折设计成本，不是未归因残余。"""
    idx = pd.bdate_range("2020-01-01", periods=50)
    frames = {"A": _flat_frame(50, 10.0), "B": _flat_frame(50, 10.0)}
    schedule = _schedule([(0, {"A": 0.5, "B": 0.5})], idx)
    result = _run(frames, schedule, _free_config(1_000_000.0))
    audit = audit_cash(result, frames, schedule, initial_cash=1_000_000.0, unit=1)
    # 1 个空仓日 / 50 个交易日
    assert audit.components[CASH_PRESTART] == pytest.approx(1 / 50, rel=0.02)
    assert audit.components[CASH_OTHER] == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# 四、行情终止：这正是 L3p 11.71% 的主因
# --------------------------------------------------------------------------- #
def test_dead_symbol_weight_becomes_cash():
    """B 的行情在第 60 天后终止，之后仍被分配 50% 权重 ⇒ 该权重的现金再也买不进。"""
    idx = pd.bdate_range("2020-01-01", periods=100)
    frames = {"A": _flat_frame(100, 10.0), "B": _flat_frame(60, 10.0)}
    # 第 0 天只买 A；第 61 天把 B 加进目标（此时 B 已无行情）
    schedule = _schedule([(0, {"A": 1.0}), (61, {"A": 0.5, "B": 0.5})], idx)
    result = _run(frames, schedule, _free_config(1_000_000.0))
    audit = audit_cash(result, frames, schedule, initial_cash=1_000_000.0, unit=1)

    assert audit.components[CASH_DEAD] > 0.10
    assert audit.shares[CASH_DEAD] > 0.5
    assert audit.periods_with_unpriced == 1
    second = audit.rebalances[1]
    assert second.unpriced_weight == pytest.approx(0.5)
    assert second.components[CASH_DEAD] == pytest.approx(0.5 * second.equity_open, rel=0.01)


def test_tradable_only_schedule_drops_dead_symbol():
    idx = pd.bdate_range("2020-01-01", periods=100)
    frames = {"A": _flat_frame(100, 10.0), "B": _flat_frame(60, 10.0)}
    states = {
        "A": pd.Series(1, index=idx),
        "B": pd.Series(1, index=idx),
    }
    schedule = tradable_only_schedule(
        states, [idx[61]], calendar=idx, frames=frames, max_weight_per_asset=None
    )
    assert len(schedule) == 1
    assert set(schedule[0].weights) == {"A"}
    assert schedule[0].weights["A"] == pytest.approx(1.0)


def test_tradable_only_schedule_respects_cap_without_renormalising():
    """压到上限后**不再**二次归一：否则等于偷偷把权重挪给别的标的。"""
    idx = pd.bdate_range("2020-01-01", periods=50)
    frames = {"A": _flat_frame(50, 10.0), "B": _flat_frame(50, 20.0), "C": _flat_frame(50, 30.0)}
    states = {s: pd.Series(1, index=idx) for s in ("A", "B", "C")}
    schedule = tradable_only_schedule(
        states, [idx[0]], calendar=idx, frames=frames, max_weight_per_asset=0.20
    )
    assert sum(schedule[0].weights.values()) == pytest.approx(0.60)


def test_tradable_only_schedule_skips_instead_of_liquidating():
    """持仓全部不可成交时必须**跳过本期**，不能发空目标（那等于清仓）。

    首版就是在这里出错的：折 3 的持仓当日全部停牌，诊断臂发出空目标 → 把 23 只
    可成交的持仓全部卖掉 → 凭空造出 −23pp 的假效应。
    """
    idx = pd.bdate_range("2020-01-01", periods=100)
    frames = {"A": _flat_frame(100, 10.0), "B": _flat_frame(60, 10.0)}
    only_dead = {"A": pd.Series(0, index=idx), "B": pd.Series(1, index=idx)}
    assert tradable_only_schedule(
        only_dead, [idx[61]], calendar=idx, frames=frames, max_weight_per_asset=None
    ) == ()


def test_tradable_only_schedule_keeps_genuine_cash_target():
    """策略本身空仓时仍要发空目标：那是真的「全部卖出」，不是「全部不可成交」。"""
    idx = pd.bdate_range("2020-01-01", periods=50)
    frames = {"A": _flat_frame(50, 10.0), "B": _flat_frame(50, 10.0)}
    flat = {s: pd.Series(0, index=idx) for s in ("A", "B")}
    schedule = tradable_only_schedule(
        flat, [idx[0]], calendar=idx, frames=frames, max_weight_per_asset=None
    )
    assert len(schedule) == 1
    assert schedule[0].weights == {}


# --------------------------------------------------------------------------- #
# 五、整手 vs 1 股
# --------------------------------------------------------------------------- #
def test_lot_rounding_creates_cash_but_is_bounded():
    """每只 50 万、股价 37.77：100 股的零头会留下可计算的现金，且不超过一手的金额。"""
    idx = pd.bdate_range("2020-01-01", periods=60)
    frames = {"A": _flat_frame(60, 37.77), "B": _flat_frame(60, 37.77)}
    schedule = _schedule([(0, {"A": 0.5, "B": 0.5})], idx)

    lots_result = _run(frames, schedule, _free_config(1_000_000.0, "lots"))
    lots_audit = audit_cash(
        lots_result, frames, schedule, initial_cash=1_000_000.0, unit=100
    )
    weights_result = _run(frames, schedule, _free_config(1_000_000.0, "weights"))
    weights_audit = audit_cash(
        weights_result, frames, schedule, initial_cash=1_000_000.0, unit=1
    )

    first = lots_audit.rebalances[0]
    # 500,000 / 37.77 = 13,238 股 → 取整到 13,200 股 → 残差 38×37.77×2 只
    assert first.components[CASH_ROUNDING] == pytest.approx(2 * 38 * 37.77, rel=0.01)
    assert lots_audit.components[CASH_ROUNDING] > 0
    assert weights_audit.components[CASH_ROUNDING] < 1e-4
    assert lots_audit.avg_cash_ratio > weights_audit.avg_cash_ratio


def test_one_lot_larger_than_budget_is_classified_as_rounding():
    """股价 600 元、每只预算 5 万 ⇒ 一手 6 万，整只买不进。必须记 rounding，不是 other。"""
    idx = pd.bdate_range("2020-01-01", periods=40)
    frames = {"A": _flat_frame(40, 600.0), "B": _flat_frame(40, 600.0)}
    schedule = _schedule([(0, {"A": 0.5, "B": 0.5})], idx)
    result = _run(frames, schedule, _free_config(100_000.0, "lots"))
    audit = audit_cash(result, frames, schedule, initial_cash=100_000.0, unit=100)
    assert audit.components[CASH_ROUNDING] > 0.9  # 整仓都进不去
    assert audit.components[CASH_OTHER] == pytest.approx(0.0, abs=1e-9)


# --------------------------------------------------------------------------- #
# 六、权重上限
# --------------------------------------------------------------------------- #
def test_weight_cap_gap_is_structural():
    """3 只 + 上限 0.20 ⇒ 目标合计 0.60，40% 的现金是模型参数决定的，不是缺陷。"""
    idx = pd.bdate_range("2020-01-01", periods=40)
    frames = {s: _flat_frame(40, 10.0) for s in ("A", "B", "C")}
    schedule = _schedule([(0, {"A": 0.20, "B": 0.20, "C": 0.20})], idx)
    config = replace(_free_config(1_000_000.0), symbols=("A", "B", "C"))
    result = _run(frames, schedule, config)
    audit = audit_cash(result, frames, schedule, initial_cash=1_000_000.0, unit=1)
    assert audit.periods_with_cap == 1
    assert audit.components[CASH_CAP] == pytest.approx(0.40 * 39 / 40, rel=0.02)


# --------------------------------------------------------------------------- #
# 七、工具函数
# --------------------------------------------------------------------------- #
def test_spearman_monotone_and_degenerate():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert spearman([1, 1, 1, 1], [1, 2, 3, 4]) is None
    assert spearman([1, 2], [1, 2]) is None  # 样本太少
