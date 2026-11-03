"""全池截面信息含量检验（L3s）。

回答的问题：`ma_cross` 的指标在**横截面**上有没有排序能力——把池内 500 余只标的
按「快慢线乖离」排序，排名靠前的下一期是否真的涨得更多。

与 L3r 的区别（也是本模块存在的理由）：

* L3r 比较的是「12 只组合 vs 24 只等权」的收益差，逐期噪声 sd≈4.6pp，MDE 0.93pp；
* 本模块用**全部可定价标的**算秩相关，se(IC)≈1/√(N−1)，N 从 24 涨到数百，
  折回收益口径的 MDE 才能压到 0.2pp 量级。

⚠️ 本模块是**诊断**：IC 显著不等于组合有 alpha（还要过成本、整手、涨跌停、
现金约束那一层）。任何结论都不得当可用性证据引用。
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import pandas as pd

from deepquant.gates.selection import (
    SelectionError,
    bootstrap_mean_ci,
    minimum_detectable_effect,
    newey_west_mean,
    observed_power,
    t_stat,
)

#: 标准正态上 20% 尾的均值 φ(z_0.8)/0.2 = 0.27996/0.2，用于把 IC 折算成收益差
TAIL_EXPECTATION_TOP20 = 1.400


class ICTestError(ValueError):
    """截面信息含量检验的前置条件不满足。"""


# ---------------------------------------------------------------- 池与行情


def load_pool(path: str | Any) -> tuple[str, ...]:
    """读冻结池文件：单行逗号分隔，或每行一个代码（两种都容忍）。"""
    from pathlib import Path

    file = Path(path)
    if not file.exists():
        raise ICTestError(f"池文件不存在：{file}")
    raw = file.read_text(encoding="utf-8")
    codes: list[str] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        codes.extend(part.strip() for part in line.split(",") if part.strip())
    seen: dict[str, None] = {}
    for code in codes:
        seen.setdefault(code, None)
    if not seen:
        raise ICTestError(f"池文件为空：{file}")
    return tuple(seen)


def load_pool_frames(
    codes: Sequence[str],
    *,
    data_dir: str | Any,
    span_start: str,
    span_end: str,
    min_rows_in_span: int = 60,
) -> tuple[dict[str, pd.DataFrame], dict[str, Any]]:
    """加载池内行情（研究层 qfq）。缺文件/缺列/跨度内行数不足者**如实剔除并记账**。"""
    from pathlib import Path

    qfq_dir = Path(data_dir)
    start = pd.Timestamp(span_start)
    end = pd.Timestamp(span_end)
    frames: dict[str, pd.DataFrame] = {}
    dropped: list[dict[str, str]] = []

    for code in codes:
        file = qfq_dir / f"{code}.qfq.csv"
        if not file.exists():
            dropped.append({"symbol": code, "reason": "缺少 qfq 文件"})
            continue
        frame = pd.read_csv(file, parse_dates=["date"]).set_index("date")
        frame = frame.loc[(frame.index >= start) & (frame.index <= end)]
        if not {"open", "close"} <= set(frame.columns):
            dropped.append({"symbol": code, "reason": "缺少 open/close 列"})
            continue
        if len(frame) < min_rows_in_span:
            dropped.append(
                {"symbol": code, "reason": f"跨度内仅 {len(frame)} 行 < {min_rows_in_span}"}
            )
            continue
        frames[code] = frame

    if not frames:
        raise ICTestError("池内所有标的都不可用")
    meta = {
        "requested": len(codes),
        "loaded": len(frames),
        "dropped": dropped,
    }
    return frames, meta


# ---------------------------------------------------------------- 分数与收益


def ma_gap_scores(
    frames: Mapping[str, pd.DataFrame], *, fast: int, slow: int
) -> dict[str, pd.Series]:
    """逐标的预计算「快慢线乖离」序列：fastMA/slowMA − 1。

    滚动均值只用当前与过去数据，无未来函数。预热期（slow 未填满）为 NaN，
    由取用方自然剔除。
    """
    if fast < 1 or slow <= fast:
        raise ICTestError(f"均线参数非法：fast={fast}, slow={slow}")
    out: dict[str, pd.Series] = {}
    for symbol, frame in frames.items():
        close = frame["close"]
        fast_ma = close.rolling(fast, min_periods=fast).mean()
        slow_ma = close.rolling(slow, min_periods=slow).mean()
        out[symbol] = fast_ma / slow_ma - 1.0
    return out


def _value_at(series: pd.Series, day: pd.Timestamp) -> float | None:
    """取某交易日的值；当天没有该行（停牌/未上市）返回 None，不做前值填充。

    不允许 ffill：前值填充会把「停牌中」当成「分数仍然有效」，等于假装当天能排序。
    """
    try:
        value = series.get(day)
    except (KeyError, TypeError):
        return None
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return value


def _close_at(frame: pd.DataFrame, day: pd.Timestamp) -> float | None:
    value = _value_at(frame["close"], day)
    if value is None or value <= 0:
        return None
    return value


@dataclass(frozen=True)
class ICPeriod:
    """一期的截面观测。"""

    signal_date: pd.Timestamp
    exec_date: pd.Timestamp
    next_exec_date: pd.Timestamp
    n_scored: int
    n_used: int
    ic: float | None
    spread: float | None
    quintile_returns: tuple[float, ...] | None
    cross_section_sd: float | None


def build_period(
    *,
    score_by_symbol: Mapping[str, pd.Series],
    frames: Mapping[str, pd.DataFrame],
    signal_date: pd.Timestamp,
    exec_date: pd.Timestamp,
    next_exec_date: pd.Timestamp,
    quintiles: int = 5,
    min_names: int = 30,
) -> ICPeriod:
    """算一期的截面 IC。

    纳入规则：**分数有效且两端都有价**才计入——两端配对剔除，使「缺一端」的标的
    不会只影响某一侧。
    """
    scores: dict[str, float] = {}
    returns: dict[str, float] = {}
    n_scored = 0
    for symbol, series in score_by_symbol.items():
        score = _value_at(series, signal_date)
        if score is None:
            continue
        n_scored += 1
        p0 = _close_at(frames[symbol], exec_date)
        p1 = _close_at(frames[symbol], next_exec_date)
        if p0 is None or p1 is None:
            continue
        scores[symbol] = score
        returns[symbol] = p1 / p0 - 1.0

    n_used = len(scores)
    if n_used < min_names:
        return ICPeriod(
            signal_date=signal_date,
            exec_date=exec_date,
            next_exec_date=next_exec_date,
            n_scored=n_scored,
            n_used=n_used,
            ic=None,
            spread=None,
            quintile_returns=None,
            cross_section_sd=None,
        )

    score_s = pd.Series(scores, dtype="float64")
    ret_s = pd.Series(returns, dtype="float64")
    ic = spearman(score_s.to_numpy(), ret_s.to_numpy())

    order = score_s.rank(method="first").to_numpy()
    # 分组标签必须按**位置**对齐：把 Series 直接丢给 groupby 会按索引匹配，
    # 而这里的索引是股票代码、标签是 0..k−1，两者对不上就会静默产出一堆 NaN。
    bucket_labels = pd.qcut(pd.Series(order), quintiles, labels=False).to_numpy()
    grouped = ret_s.groupby(bucket_labels).mean().sort_index()
    quintile_returns = tuple(float(v) for v in grouped.to_numpy())
    spread = (
        float(quintile_returns[-1] - quintile_returns[0])
        if len(quintile_returns) == quintiles
        else None
    )

    return ICPeriod(
        signal_date=signal_date,
        exec_date=exec_date,
        next_exec_date=next_exec_date,
        n_scored=n_scored,
        n_used=n_used,
        ic=ic,
        spread=spread,
        quintile_returns=quintile_returns,
        cross_section_sd=float(ret_s.std(ddof=1)),
    )


def spearman(x: Iterable[float], y: Iterable[float]) -> float:
    """秩相关（平均秩处理并列）。样本不足或方差为 0 时返回 0.0。"""
    sx = pd.Series(list(x), dtype="float64")
    sy = pd.Series(list(y), dtype="float64")
    if len(sx) != len(sy) or len(sx) < 3:
        return 0.0
    rx = sx.rank()
    ry = sy.rank()
    if rx.std(ddof=0) == 0 or ry.std(ddof=0) == 0:
        return 0.0
    return float(rx.corr(ry))


# ---------------------------------------------------------------- 汇总与判定


def summarise_ic(
    periods: Sequence[ICPeriod],
    *,
    nw_lag: int = 3,
    bootstrap_reps: int = 2000,
    bootstrap_block: int = 3,
    seed: int = 20261028,
) -> dict[str, Any]:
    """把逐期 IC 汇总成可判定的统计量。"""
    ics = [p.ic for p in periods if p.ic is not None]
    used = [p for p in periods if p.ic is not None]
    out: dict[str, Any] = {
        "periods": len(periods),
        "periods_with_ic": len(ics),
        "mean_names": (
            sum(p.n_used for p in used) / len(used) if used else None
        ),
    }
    if not ics:
        out.update(
            {
                "mean": None,
                "median": None,
                "t_iid": None,
                "t_nw3": None,
                "se_nw": None,
                "win_rate": None,
                "sd": None,
                "mde_ic": None,
                "power": None,
                "ci_low": None,
                "ci_high": None,
                "mde_return": None,
                "mean_cross_section_sd": None,
                "nw_variance_fell_back": None,
            }
        )
        return out

    base = t_stat(ics)
    # t_stat 只给 se，不给 sd；本模块要报 sd（P5 拿它和 1/√(N−1) 比），自己算
    sd = statistics.stdev(ics) if len(ics) >= 2 else float("nan")
    nw = newey_west_mean(ics, lag=nw_lag)
    ci = bootstrap_mean_ci(
        ics, reps=bootstrap_reps, block=bootstrap_block, seed=seed
    )
    mde_ic = minimum_detectable_effect(nw["se_nw"])
    sd_cs = [p.cross_section_sd for p in used if p.cross_section_sd is not None]
    mean_cs = sum(sd_cs) / len(sd_cs) if sd_cs else None
    out.update(
        {
            "mean": base["mean"],
            "median": float(pd.Series(ics).median()),
            "t_iid": base["t"],
            "t_nw3": nw["t_nw"],
            "se_nw": nw["se_nw"],
            "win_rate": sum(1 for v in ics if v > 0) / len(ics),
            "sd": sd,
            "mde_ic": mde_ic,
            "power": observed_power(nw["t_nw"]),
            "ci_low": ci["low"],
            "ci_high": ci["high"],
            "mde_return": (
                mde_ic * mean_cs * TAIL_EXPECTATION_TOP20
                if mean_cs is not None
                else None
            ),
            "mean_cross_section_sd": mean_cs,
            "nw_variance_fell_back": nw["variance_fell_back_to_g0"],
            "n_required_for_target": (
                None
                if not ics
                else _n_required(sd, target=0.0030, mean_cs=mean_cs)
            ),
        }
    )
    return out


def _n_required(sd: float, *, target: float, mean_cs: float | None) -> float | None:
    """把折算 MDE 压到 ``target`` 所需的期数（按 se(IC) 的缩放关系）。"""
    if mean_cs is None or mean_cs <= 0 or sd <= 0:
        return None
    per_period_return_sd = sd * mean_cs * TAIL_EXPECTATION_TOP20
    return (2.8016 * per_period_return_sd / target) ** 2


def quintile_profile(periods: Sequence[ICPeriod]) -> dict[str, Any]:
    """各五分位的逐期均值收益的等权平均，以及单调性。"""
    rows = [p for p in periods if p.quintile_returns is not None]
    if not rows:
        return {"quintiles": 0, "means": [], "monotonicity": None, "spread_mean": None,
                "spread_t": None, "periods": 0}
    width = len(rows[0].quintile_returns)
    means = [
        sum(p.quintile_returns[i] for p in rows) / len(rows) for i in range(width)
    ]
    spreads = [p.spread for p in rows if p.spread is not None]
    mono = spearman(list(range(1, width + 1)), means)
    spread_t = t_stat(spreads)["t"] if len(spreads) >= 3 else None
    return {
        "quintiles": width,
        "means": means,
        "monotonicity": mono,
        "spread_mean": (sum(spreads) / len(spreads)) if spreads else None,
        "spread_t": spread_t,
        "spread_nw": newey_west_mean(spreads, lag=3)["t_nw"] if len(spreads) >= 3 else None,
        "periods": len(rows),
    }


def decide_ic(
    summary: Mapping[str, Any],
    profile: Mapping[str, Any],
    *,
    target_mde: float = 0.0030,
) -> tuple[str, str]:
    """按预登记规则出判定。**先看功效，再看符号**——功效不足时不得报「无效应」。"""
    mean = summary.get("mean")
    t_nw = summary.get("t_nw3")
    mde_return = summary.get("mde_return")
    if mean is None or t_nw is None:
        return "IC_INCONCLUSIVE_POWER", "没有可用的截面期，无法判定"
    if mde_return is None:
        return "IC_INCONCLUSIVE_POWER", "缺少截面收益标准差，无法把 MDE 折回收益口径"

    mono = profile.get("monotonicity")
    if mean > 0 and t_nw >= 1.96 and (mono is None or mono >= 0.8):
        return (
            "IC_INFORMATION_DETECTED",
            f"mean IC {mean:+.4f}，NW(3) t={t_nw:+.3f}，单调性 {mono}",
        )
    if mean < 0 and t_nw <= -1.96:
        return (
            "IC_SIGN_REVERSED",
            f"mean IC {mean:+.4f} 显著为负，NW(3) t={t_nw:+.3f}",
        )
    if abs(t_nw) < 1.96 and mde_return <= target_mde:
        return (
            "IC_NO_INFORMATION_DETECTED",
            f"mean IC {mean:+.4f}（t={t_nw:+.3f}），且折算 MDE {mde_return * 100:.3f}pp "
            f"<= {target_mde * 100:.2f}pp：这个分辨率下没检出排序能力",
        )
    if mde_return > target_mde:
        return (
            "IC_INCONCLUSIVE_POWER",
            f"折算 MDE {mde_return * 100:.3f}pp > {target_mde * 100:.2f}pp，仍答不了",
        )
    return (
        "IC_INCONCLUSIVE",
        f"mean IC {mean:+.4f}（t={t_nw:+.3f}），单调性 {mono}：不满足任一确定态",
    )


def check_prereg_sha(path: str | Any, expected: str) -> str:
    """脚本启动时校预登记未被改动；不一致直接拒跑。"""
    import hashlib
    from pathlib import Path

    file = Path(path)
    if not file.exists():
        raise ICTestError(f"预登记不存在：{file}")
    actual = hashlib.sha256(file.read_bytes()).hexdigest()
    if not actual.startswith(expected):
        raise ICTestError(
            f"预登记被改动：期望 {expected}…，实际 {actual}…；拒绝运行以免事后改判据"
        )
    return actual


__all__ = [
    "ICPeriod",
    "ICTestError",
    "SelectionError",
    "TAIL_EXPECTATION_TOP20",
    "build_period",
    "check_prereg_sha",
    "decide_ic",
    "load_pool",
    "load_pool_frames",
    "ma_gap_scores",
    "quintile_profile",
    "spearman",
    "summarise_ic",
]
