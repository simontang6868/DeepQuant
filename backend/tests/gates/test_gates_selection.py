"""选择效应（L3r）测试。

四类最容易做假的地方：

1. **Newey-West 必须与独立实现一致**。同一份数据、同一滞后，两套实现算出的 ``se``
   差一点就说明核权重或分母写错了——而这类错误只改 MDE、不改 t 的单调性，看结论
   是看不出来的。
2. **匹配剔除必须真的剔除**。期初或期末缺价就整只不计；若静默把它当 0 收益，
   死票会变成「横盘股」，系统性压低效应。
3. **对照臂不许二次归一**。压到上限后再把权重摊给别人，等于偷偷改了持仓数。
4. **判定顺序不许改**。先给「检出」最严门槛，再给「有功效地排除」，最后才允许
   「功效不足」。反过来写会让每轮都落在 INCONCLUSIVE。
"""

from __future__ import annotations

import math
import statistics

import numpy as np
import pandas as pd
import pytest

from deepquant.backtest.portfolio import TargetPortfolio
from deepquant.gates.selection import (
    BLOCKED,
    INCONCLUSIVE_POWER,
    NO_SELECTION_EFFECT,
    SELECTION_EFFECT_DETECTED,
    SELECTION_EFFECT_NEGATIVE,
    MDE_FACTOR,
    decide_selection,
    minimum_detectable_effect,
    newey_west_mean,
    observed_power,
    period_effects,
    t_stat,
    to_selection_verdict,
    tradable_control_schedule,
)

# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
def _series_frame(opens, closes=None) -> pd.DataFrame:
    idx = pd.bdate_range("2020-01-01", periods=len(opens))
    closes = opens if closes is None else closes
    return pd.DataFrame(
        {
            "open": opens,
            "high": np.maximum(opens, closes) * 1.01,
            "low": np.minimum(opens, closes) * 0.99,
            "close": closes,
            "volume": 1e6,
        },
        index=idx,
    )


def _reference_newey_west(values, lag: int = 3) -> float:
    """测试内独立实现（逐项手写，不复用包内代码）。"""
    n = len(values)
    mean = statistics.fmean(values)
    e = [v - mean for v in values]
    total = sum(v * v for v in e) / n
    for j in range(1, lag + 1):
        cov = sum(e[t] * e[t - j] for t in range(j, n)) / n
        total += 2 * (1 - j / (lag + 1)) * cov
    return math.sqrt(total / n)


# --------------------------------------------------------------------------- #
# 一、统计工具
# --------------------------------------------------------------------------- #
def test_t_stat_matches_hand_calculation():
    values = [0.01, -0.02, 0.03, 0.00, 0.015]
    out = t_stat(values)
    mean = statistics.fmean(values)
    se = statistics.stdev(values) / math.sqrt(5)
    assert out["mean"] == pytest.approx(mean)
    assert out["se"] == pytest.approx(se)
    assert out["t"] == pytest.approx(mean / se)


def test_newey_west_matches_independent_implementation():
    rng = np.random.default_rng(20261028)
    values = list(rng.normal(0.002, 0.02, size=120))
    ours = newey_west_mean(values, lag=3)
    assert ours["se_nw"] == pytest.approx(_reference_newey_west(values, 3), rel=1e-12)
    assert ours["t_nw"] == pytest.approx(ours["mean"] / ours["se_nw"])


def test_newey_west_reduces_to_iid_when_uncorrelated():
    rng = np.random.default_rng(7)
    values = list(rng.normal(0.0, 0.01, size=5000))
    nw = newey_west_mean(values, lag=3)
    iid = t_stat(values)
    # 独立同分布时 NW 与 iid 应接近（大样本）
    assert nw["se_nw"] == pytest.approx(iid["se"], rel=0.15)


def test_newey_west_survives_negative_autocorrelation():
    """强负自相关下也要给出有限 se。

    注：Bartlett 核的估计量（渐近）半正定，负方差在实践中几乎造不出来；这里的
    ``variance_fell_back_to_g0`` 是防御性分支，本测试只保证不会返回 NaN 让判定悄悄失衡。
    """
    values = [0.01 if i % 2 == 0 else -0.0099 for i in range(60)]
    out = newey_west_mean(values, lag=3)
    assert isinstance(out["variance_fell_back_to_g0"], bool)
    assert math.isfinite(out["se_nw"])
    assert out["se_nw"] > 0


def test_mde_and_power_constants():
    assert minimum_detectable_effect(0.001) == pytest.approx(MDE_FACTOR * 0.001)
    # 记忆里的老坑：power(1.96) 不是 0.5，而是 0.500044
    assert observed_power(1.96) == pytest.approx(0.500044, abs=1e-6)


# --------------------------------------------------------------------------- #
# 二、可成交等权对照臂
# --------------------------------------------------------------------------- #
def test_tradable_control_drops_unpriced_symbols():
    idx = pd.bdate_range("2020-01-01", periods=30)
    frames = {
        "A": _series_frame([10.0] * 30),
        "B": _series_frame([20.0] * 10),  # 第 10 天后没有行情
        "C": _series_frame([30.0] * 30),
    }
    schedule = tradable_control_schedule(
        ["A", "B", "C"], [idx[15]], calendar=idx, frames=frames, max_weight_per_asset=None
    )
    assert len(schedule) == 1
    assert set(schedule[0].weights) == {"A", "C"}
    assert schedule[0].weights["A"] == pytest.approx(0.5)


def test_tradable_control_respects_cap_without_renormalising():
    idx = pd.bdate_range("2020-01-01", periods=20)
    frames = {s: _series_frame([10.0] * 20) for s in ("A", "B", "C", "D")}
    schedule = tradable_control_schedule(
        ["A", "B", "C", "D"],
        [idx[5]],
        calendar=idx,
        frames=frames,
        max_weight_per_asset=0.20,
    )
    # 1/4 = 0.25 > 0.20 ⇒ 压到 0.20，合计 0.80，**不再**摊到 1.0
    assert sum(schedule[0].weights.values()) == pytest.approx(0.80)


def test_tradable_control_skips_period_when_nothing_available():
    idx = pd.bdate_range("2020-01-01", periods=30)
    frames = {"A": _series_frame([10.0] * 5), "B": _series_frame([10.0] * 5)}
    schedule = tradable_control_schedule(
        ["A", "B"], [idx[20]], calendar=idx, frames=frames, max_weight_per_asset=None
    )
    assert schedule == ()


# --------------------------------------------------------------------------- #
# 三、逐期截面效应
# --------------------------------------------------------------------------- #
def _two_symbol_frames(n: int, a_opens: list[float], b_opens: list[float]):
    return {"A": _series_frame(a_opens), "B": _series_frame(b_opens)}


def test_period_effect_computes_equal_weight_difference():
    n = 42
    a = [10.0] * 21 + [12.0] * 21  # A 涨 20%
    b = [10.0] * 42  # B 持平
    frames = _two_symbol_frames(n, a, b)
    idx = pd.bdate_range("2020-01-01", periods=n)
    schedule = (
        TargetPortfolio(signal_date=idx[0], weights={"A": 1.0}),  # 执行在 idx[1]
        TargetPortfolio(signal_date=idx[20], weights={}),  # 执行在 idx[21]
    )
    effects = period_effects(
        schedule,
        symbols=["A", "B"],
        calendar=idx,
        frames=frames,
        window_end=idx[41],
    )
    assert len(effects) == 2
    first = effects[0]
    assert first.period_start == idx[1] and first.period_end == idx[21]
    assert first.r_strategy == pytest.approx(0.2)  # 只持 A
    assert first.r_universe == pytest.approx(0.1)  # A、B 等权
    assert first.delta == pytest.approx(0.1)


def test_period_effect_excludes_symbols_missing_at_either_end():
    """期初或期末缺价必须整只剔除，不能当 0 收益。

    这里 B 期末缺价：两臂都只剩 A（策略）+ C（池），池子仍有 2 只所以基准可算。
    """
    n = 42
    a = [10.0] * 21 + [12.0] * 21  # A 涨 20%
    b = [10.0] * 25  # B 期末缺价
    c = [10.0] * 42  # C 持平
    frames = {"A": _series_frame(a), "B": _series_frame(b), "C": _series_frame(c)}
    idx = pd.bdate_range("2020-01-01", periods=n)
    schedule = (TargetPortfolio(signal_date=idx[0], weights={"A": 0.5, "B": 0.5}),)
    effects = period_effects(
        schedule, symbols=["A", "B", "C"], calendar=idx, frames=frames, window_end=idx[41]
    )
    first = effects[0]
    assert first.n_universe == 2  # B 被剔除，A/C 留下
    assert first.n_strategy_priced == 1  # 策略持仓里只有 A 可定价
    assert first.r_strategy == pytest.approx(0.2)
    assert first.r_universe == pytest.approx(0.1)  # (A 20% + C 0%)/2
    assert first.delta == pytest.approx(0.1)


def test_period_effect_last_period_uses_window_end():
    n = 30
    frames = _two_symbol_frames(n, [10.0] * n, [10.0] * n)
    idx = pd.bdate_range("2020-01-01", periods=n)
    schedule = (TargetPortfolio(signal_date=idx[0], weights={"A": 1.0}),)
    effects = period_effects(
        schedule, symbols=["A", "B"], calendar=idx, frames=frames, window_end=idx[20]
    )
    assert effects[-1].period_end == idx[20]


def test_period_effect_returns_none_delta_when_universe_too_small():
    n = 20
    frames = {"A": _series_frame([10.0] * n)}
    idx = pd.bdate_range("2020-01-01", periods=n)
    schedule = (TargetPortfolio(signal_date=idx[0], weights={"A": 1.0}),)
    effects = period_effects(
        schedule, symbols=["A"], calendar=idx, frames=frames, window_end=idx[19]
    )
    assert effects[0].delta is None


# --------------------------------------------------------------------------- #
# 四、判定顺序
# --------------------------------------------------------------------------- #
def _summary(mean, t_iid, t_nw3, mde=0.002):
    return {"mean": mean, "t_iid": t_iid, "t_nw3": t_nw3, "mde": mde}


def test_decide_detected_requires_both_metrics_and_fold_consistency():
    verdict, _ = decide_selection(_summary(0.002, 2.5, 2.0), fold_win_rate=0.65)
    assert verdict == SELECTION_EFFECT_DETECTED
    # 折胜率不够 → 不许判检出
    verdict2, _ = decide_selection(_summary(0.002, 2.5, 2.0), fold_win_rate=0.50)
    assert verdict2 != SELECTION_EFFECT_DETECTED
    # NW 不过 → 不许判检出
    verdict3, _ = decide_selection(_summary(0.002, 2.5, 1.2), fold_win_rate=0.65)
    assert verdict3 != SELECTION_EFFECT_DETECTED


def test_decide_negative_requires_both_metrics():
    verdict, _ = decide_selection(_summary(-0.002, -2.5, -2.0), fold_win_rate=0.3)
    assert verdict == SELECTION_EFFECT_NEGATIVE
    verdict2, _ = decide_selection(_summary(-0.002, -2.5, -1.2), fold_win_rate=0.3)
    assert verdict2 != SELECTION_EFFECT_NEGATIVE


def test_decide_no_effect_needs_power_not_just_absence():
    verdict, _ = decide_selection(_summary(0.0005, 0.2, 0.1, mde=0.003), fold_win_rate=0.5)
    assert verdict == NO_SELECTION_EFFECT
    # 同样的 t，但 MDE 太大 → 只能说功效不足
    verdict2, _ = decide_selection(_summary(0.0005, 0.2, 0.1, mde=0.02), fold_win_rate=0.5)
    assert verdict2 == INCONCLUSIVE_POWER


def test_decide_handles_empty_sample():
    verdict, _ = decide_selection(_summary(None, None, None, mde=None), fold_win_rate=None)
    assert verdict == INCONCLUSIVE_POWER


def test_downgrade_when_prior_not_passed():
    assert to_selection_verdict(NO_SELECTION_EFFECT, prior_passed=False) == BLOCKED
    assert to_selection_verdict(NO_SELECTION_EFFECT, prior_passed=True) == NO_SELECTION_EFFECT
