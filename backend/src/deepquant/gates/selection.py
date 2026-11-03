"""选择效应（L3r）：选股相对「池内可成交等权」有没有正贡献。

为什么要另起一套尺子
--------------------
L3p 用 ``per_invested = (1+r)/(1−cash) − 1`` 把两臂的现金差异折算掉，得出
「选择没有正贡献」。L3q 证明这个折算的前提不成立：两臂现金分别有 69% / 95% 来自
**不可成交标的**（行情终止 / 未上市 / 停牌），``1−cash`` 不是「主动投出的比例」，
而是「能投出的比例」。于是那个数字既不能证明也不能否证选择有没有用。

本模块给两把不依赖「现金可投」假设的尺子：

1. **R1 组合级**：把对照臂换成「每期只在**当期能成交**的标的上等权」。两臂的现金
   来源就只剩上限与费用，稀释程度可比。
2. **R2 截面级**：逐调仓期直接比「策略持仓的等权收益」与「可成交池的等权收益」。
   不进组合账户，因此没有现金、没有整手、没有费用——它测的就是选择本身。

R1 保留真实摩擦但可能被现金结构污染；R2 干净但不是可实现收益。两者必须并列报，
且当两者结论不同向时**以 R2 为准**（R1 的污染是已证实的）。

三条容易做错的口径
------------------
1. **匹配剔除**：标的必须在期初与期末**两端都有有效价**才计入两臂。这条同时把死票
   从两臂剔除，所以 R2 测的是「在能成交的标的中选股是否优于等权」，**不是**「含死票
   的完整组合收益」。不这么做的替代方案（缺价 ffill 成 0 收益）会把死票当成"横盘"，
   系统性抬高两臂收益并压低差异。
2. **R2 用目标持仓，不用实际成交**：选择是"想买什么"，执行摩擦属于 R1 的口径。
   若两者偏离太大（P5 相关性低），说明执行把选择完全改写了，那是另一个发现。
3. **`newey_west` 入参必须是小数**。传 pp 会让 ``se`` 与 ``mean`` 同时放大 100 倍，
   ``t`` 不变——只看 t 的判定不会错，但报出来的 MDE、量级会全错 100 倍。

本模块不做参数搜索、不改被测策略、不改回测引擎。
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from deepquant.backtest.portfolio import TargetPortfolio, _valid_price

# --------------------------------------------------------------------------- #
# 一、判定枚举
# --------------------------------------------------------------------------- #
SELECTION_EFFECT_DETECTED = "SELECTION_EFFECT_DETECTED"
SELECTION_EFFECT_NEGATIVE = "SELECTION_EFFECT_NEGATIVE"
NO_SELECTION_EFFECT = "NO_SELECTION_EFFECT"
INCONCLUSIVE_POWER = "INCONCLUSIVE_POWER"
BLOCKED = "L3R_BLOCKED_PREREQUISITE"

SELECTION_VERDICTS: tuple[str, ...] = (
    SELECTION_EFFECT_DETECTED,
    SELECTION_EFFECT_NEGATIVE,
    NO_SELECTION_EFFECT,
    INCONCLUSIVE_POWER,
    BLOCKED,
)

#: MDE 系数：双侧 5% + 功效 80% 的正态近似（1.96 + 0.8416）
MDE_FACTOR = 2.8016

#: 「有功效地排除」的门槛：月均 0.5pp。低于它的效应即便存在，也小于本项目已确认的
#: 成本量级（单边 0.051%、月度双边约 0.10%），不足以为它改口径。
MDE_THRESHOLD = 0.005


class SelectionError(ValueError):
    """选择效应的输入不合法（期间不足、标的缺失等）。"""


# --------------------------------------------------------------------------- #
# 二、统计工具
# --------------------------------------------------------------------------- #
def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def t_stat(values: Sequence[float]) -> dict[str, float]:
    """iid 口径的均值 t（与 L3 系列同式：``stdev/√n``，样本标准差 n−1）。"""
    clean = [float(v) for v in values if v == v]
    n = len(clean)
    if n < 2:
        return {"n": n, "mean": float("nan"), "se": float("nan"), "t": float("nan")}
    mean = statistics.fmean(clean)
    se = statistics.stdev(clean) / math.sqrt(n)
    return {
        "n": n,
        "mean": mean,
        "se": se,
        "t": (mean / se) if se > 0 else float("nan"),
    }


def newey_west_mean(values: Sequence[float], *, lag: int = 3) -> dict[str, Any]:
    """Newey-West（Bartlett 核）均值的标准误与 t。

    与 ``scripts/run_phase_c.py::newey_west`` 同一套约定（``g0`` 用有偏方差、
    核权重 ``1 − j/(lag+1)``、方差为负时退回 ``g0`` 并标记），两处实现互为校验。

    Args:
        values: **小数**收益序列。传百分点会把 ``se``/``mean`` 同时放大 100 倍，
            ``t`` 不变——判定不错但量级全错，所以参数说明写在这里。
        lag: 滞后阶数。

    Returns:
        ``{n, mean, se_nw, t_nw, p, lag, variance_fell_back_to_g0}``。
    """
    clean = [float(v) for v in values if v == v]
    n = len(clean)
    if n < lag + 3:
        return {
            "n": n,
            "mean": float("nan"),
            "se_nw": float("nan"),
            "t_nw": float("nan"),
            "p": float("nan"),
            "lag": lag,
            "variance_fell_back_to_g0": False,
        }
    mean = statistics.fmean(clean)
    errors = [v - mean for v in clean]
    g0 = sum(e * e for e in errors) / n
    total = g0
    for j in range(1, lag + 1):
        gj = sum(errors[t] * errors[t - j] for t in range(j, n)) / n
        total += 2.0 * (1.0 - j / (lag + 1)) * gj
    fell_back = False
    if total <= 0:
        total, fell_back = g0, True
    se = math.sqrt(total / n)
    t = mean / se if se > 0 else float("nan")
    p = math.erfc(abs(t) / math.sqrt(2.0)) if t == t else float("nan")
    return {
        "n": n,
        "mean": mean,
        "se_nw": se,
        "t_nw": t,
        "p": p,
        "lag": lag,
        "variance_fell_back_to_g0": fell_back,
    }


def minimum_detectable_effect(se: float) -> float:
    """MDE = 2.8016 × se（双侧 5%、功效 80%）。``se`` 与返回值同为小数口径。"""
    if se != se or se <= 0:
        return float("nan")
    return MDE_FACTOR * se


def observed_power(t: float) -> float:
    """事后功效：``Φ(|t|−1.96) + Φ(−|t|−1.96)``。|t|=1.96 时恰为 50.0%。"""
    if t != t:
        return float("nan")
    return _normal_cdf(abs(t) - 1.96) + _normal_cdf(-abs(t) - 1.96)


def bootstrap_mean_ci(
    values: Sequence[float],
    *,
    block: int = 6,
    reps: int = 5000,
    seed: int = 20261028,
) -> dict[str, float]:
    """均值的 stationary bootstrap 95% 区间（平均块长 ``block``）。"""
    from deepquant.gates.l2 import stationary_bootstrap

    clean = np.asarray([float(v) for v in values if v == v], dtype=float)
    if clean.size < 5:
        return {"low": float("nan"), "high": float("nan"), "reps": 0}
    rng = np.random.default_rng(seed)
    means = np.empty(reps)
    for i in range(reps):
        means[i] = stationary_bootstrap(clean, block, rng).mean()
    return {
        "low": float(np.quantile(means, 0.025)),
        "high": float(np.quantile(means, 0.975)),
        "reps": reps,
        "block": block,
    }


# --------------------------------------------------------------------------- #
# 三、可成交等权对照臂
# --------------------------------------------------------------------------- #
def tradable_control_schedule(
    symbols: Sequence[str],
    signal_dates: Sequence[pd.Timestamp],
    *,
    calendar: pd.DatetimeIndex,
    frames: Mapping[str, pd.DataFrame],
    max_weight_per_asset: float | None = None,
) -> tuple[TargetPortfolio, ...]:
    """对照臂 C_t：每期在**当期能成交**的标的上等权（不做选择）。

    与原对照臂（等权持有全部 30 只）的差别只有一条：把「执行日没有有效开盘价」的
    标的从那一期的目标里去掉。未上市与已终止的标的因此不再吃权重，两臂的现金来源
    就只剩上限与费用——这是让 R1 可比的前提。

    口径与 :func:`deepquant.gates.l3_portfolio.schedule_from_states` 一致：超过上限
    就压到上限，**不再二次归一**（二次归一等于把权重挪给别人，是偷偷改持仓数）。
    """
    schedule: list[TargetPortfolio] = []
    for day in signal_dates:
        pos = calendar.searchsorted(pd.Timestamp(day), side="right")
        if pos >= len(calendar):
            continue
        exec_day = calendar[pos]
        available = [
            symbol
            for symbol in symbols
            if _valid_price(frames[symbol]["open"].reindex([exec_day]).iloc[0]) is not None
        ]
        if not available:
            # 当期一只都买不进：发空目标会变成「清仓」。跳过本期，保持原持仓。
            continue
        weight = 1.0 / len(available)
        if max_weight_per_asset is not None:
            weight = min(weight, max_weight_per_asset)
        schedule.append(
            TargetPortfolio(
                signal_date=pd.Timestamp(day),
                weights={s: weight for s in available},
            )
        )
    return tuple(schedule)


# --------------------------------------------------------------------------- #
# 四、逐期截面效应
# --------------------------------------------------------------------------- #
@dataclass
class PeriodEffect:
    """一个调仓期的截面效应（毛收益，等权，无费用、无整手）。"""

    period_start: pd.Timestamp
    period_end: pd.Timestamp
    #: 目标持仓数（策略「想买的」）
    n_strategy: int
    #: 两端都有价、被计入的策略标的数
    n_strategy_priced: int
    #: 两端都有价、被计入的可成交池标的数
    n_universe: int
    r_strategy: float | None
    r_universe: float | None
    delta: float | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "start": str(self.period_start.date()),
            "end": str(self.period_end.date()),
            "n_strategy": self.n_strategy,
            "n_strategy_priced": self.n_strategy_priced,
            "n_universe": self.n_universe,
            "r_strategy": self.r_strategy,
            "r_universe": self.r_universe,
            "delta": self.delta,
        }


def _price_pair(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> tuple[float, float] | None:
    """期初开盘价与期末价的配对；任一端缺失返回 None（匹配剔除）。"""
    open_series = frame["open"]
    close_series = frame["close"]
    start_px = _valid_price(open_series.get(start))
    if start_px is None:
        return None
    end_px = _valid_price(open_series.get(end))
    if end_px is None:
        end_px = _valid_price(close_series.get(end))
    if end_px is None:
        return None
    return start_px, end_px


def period_effects(
    strategy_schedule: Sequence[TargetPortfolio],
    *,
    symbols: Sequence[str],
    calendar: pd.DatetimeIndex,
    frames: Mapping[str, pd.DataFrame],
    window_end: pd.Timestamp,
) -> list[PeriodEffect]:
    """计算每个调仓期的截面效应。

    Args:
        strategy_schedule: 策略目标权重序列（信号日 + 权重）。
        symbols: 池内全部标的。
        calendar: 该折的交易日历。
        frames: 各标的行情。
        window_end: 该折最后一个交易日（末期用它当期末）。

    Returns:
        :class:`PeriodEffect` 列表。``delta`` 为 None 表示该期无法比较
        （策略空仓或可成交池不足 2 只），这些期不参与统计但在报告里计数。
    """
    targets: list[tuple[pd.Timestamp, dict[str, float]]] = []
    for target in strategy_schedule:
        pos = calendar.searchsorted(pd.Timestamp(target.signal_date), side="right")
        if pos >= len(calendar):
            continue
        targets.append((calendar[pos], dict(target.weights)))
    if not targets:
        return []

    effects: list[PeriodEffect] = []
    for i, (start, weights) in enumerate(targets):
        end = targets[i + 1][0] if i + 1 < len(targets) else pd.Timestamp(window_end)
        if end <= start:
            continue

        # 可成交池：两端都有价
        universe_pairs = {
            s: pair
            for s in symbols
            if (pair := _price_pair(frames[s], start, end)) is not None
        }
        strategy_pairs = {
            s: universe_pairs[s] for s in weights if s in universe_pairs
        }
        r_universe = (
            statistics.fmean([(b / a - 1.0) for a, b in universe_pairs.values()])
            if len(universe_pairs) >= 2
            else None
        )
        r_strategy = (
            statistics.fmean([(b / a - 1.0) for a, b in strategy_pairs.values()])
            if strategy_pairs
            else None
        )
        delta = (
            r_strategy - r_universe
            if r_strategy is not None and r_universe is not None
            else None
        )
        effects.append(
            PeriodEffect(
                period_start=start,
                period_end=end,
                n_strategy=len(weights),
                n_strategy_priced=len(strategy_pairs),
                n_universe=len(universe_pairs),
                r_strategy=r_strategy,
                r_universe=r_universe,
                delta=delta,
            )
        )
    return effects


# --------------------------------------------------------------------------- #
# 五、汇总与判定
# --------------------------------------------------------------------------- #
def summarise_effects(effects: Sequence[PeriodEffect]) -> dict[str, Any]:
    """把逐期效应汇总成显著性所需的一组量。"""
    deltas = [e.delta for e in effects if e.delta is not None]
    if not deltas:
        # 键必须齐全：调用方（报告渲染）不区分「没数据」与「键缺失」，
        # 少一个键就是 KeyError，而它会掩盖真正的原因（可比期数为 0）。
        return {
            "periods": len(effects),
            "periods_compared": 0,
            "mean": None,
            "median": None,
            "sd": None,
            "se_iid": None,
            "t_iid": None,
            "se_nw": None,
            "t_nw3": None,
            "nw_variance_fell_back": False,
            "win_rate": None,
            "mde": None,
            "power": None,
            "ci_low": None,
            "ci_high": None,
            "mean_n_strategy": None,
            "mean_n_universe": None,
            "mean_coverage": None,
        }
    base = t_stat(deltas)
    nw = newey_west_mean(deltas, lag=3)
    ci = bootstrap_mean_ci(deltas)
    return {
        "periods": len(effects),
        "periods_compared": base["n"],
        "mean": base["mean"],
        "median": statistics.median(deltas),
        "sd": statistics.stdev(deltas) if len(deltas) > 1 else float("nan"),
        "se_iid": base["se"],
        "t_iid": base["t"],
        "se_nw": nw["se_nw"],
        "t_nw3": nw["t_nw"],
        "nw_variance_fell_back": nw["variance_fell_back_to_g0"],
        "win_rate": sum(1 for d in deltas if d > 0) / len(deltas),
        "mde": minimum_detectable_effect(base["se"]),
        "power": observed_power(base["t"]),
        "ci_low": ci["low"],
        "ci_high": ci["high"],
        "mean_n_strategy": statistics.fmean(
            [e.n_strategy for e in effects if e.delta is not None]
        ),
        "mean_n_universe": statistics.fmean(
            [e.n_universe for e in effects if e.delta is not None]
        ),
        "mean_coverage": statistics.fmean(
            [e.n_strategy_priced / e.n_strategy for e in effects if e.n_strategy and e.delta is not None]
        ),
    }


def decide_selection(
    summary: Mapping[str, Any], *, fold_win_rate: float | None
) -> tuple[str, str]:
    """按预登记的顺序给出（初判, 依据）。不在这里做任何阈值调整。

    Returns:
        ``(verdict, reason)``。
    """
    mean = summary.get("mean")
    t_iid = summary.get("t_iid")
    t_nw3 = summary.get("t_nw3")
    mde = summary.get("mde")
    if mean is None or t_iid is None or mean != mean or t_iid != t_iid:
        return INCONCLUSIVE_POWER, "可比期数为 0，无法判定"

    if (
        mean > 0
        and t_iid >= 2.0
        and (t_nw3 is not None and t_nw3 == t_nw3 and t_nw3 >= 1.645)
        and (fold_win_rate is not None and fold_win_rate >= 0.60)
    ):
        return SELECTION_EFFECT_DETECTED, (
            f"R2 月均 {mean * 100:+.3f}pp、t_iid={t_iid:.3f}、t_nw3={t_nw3:.3f}，"
            f"R1 折胜率 {fold_win_rate:.1%}：双口径与折级一致"
        )
    if (
        mean <= 0
        and t_iid <= -2.0
        and (t_nw3 is not None and t_nw3 == t_nw3 and t_nw3 <= -1.645)
    ):
        return SELECTION_EFFECT_NEGATIVE, (
            f"R2 月均 {mean * 100:+.3f}pp、t_iid={t_iid:.3f}、t_nw3={t_nw3:.3f}："
            "选股显著劣于池内等权"
        )
    if mde is not None and mde == mde and mde <= MDE_THRESHOLD:
        return NO_SELECTION_EFFECT, (
            f"R2 月均 {mean * 100:+.3f}pp、t_iid={t_iid:.3f}，MDE {mde * 100:.3f}pp "
            f"≤ {MDE_THRESHOLD * 100:.1f}pp：有功效地排除"
        )
    mde_text = "不可计算" if mde is None or mde != mde else f"{mde * 100:.3f}pp"
    return INCONCLUSIVE_POWER, (
        f"R2 月均 {mean * 100:+.3f}pp、t_iid={t_iid:.3f}，但 MDE {mde_text} "
        f"> {MDE_THRESHOLD * 100:.1f}pp：样本量不足以排除可交易的效应"
    )


def to_selection_verdict(verdict: str, *, prior_passed: bool) -> str:
    """前序未通过时一律降级为 BLOCKED（与 L3/L3p 同一条规矩）。"""
    if verdict not in SELECTION_VERDICTS:
        raise SelectionError(f"未知判定：{verdict!r}")
    if verdict == BLOCKED:
        return BLOCKED
    return verdict if prior_passed else BLOCKED
