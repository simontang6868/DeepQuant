"""窗口级收益分布的统计，以及与基准的**同窗口**对照（只诊断，不选参）。

为什么单独一个模块：

1. 历史研究脚本里的 ``window_return_stats`` 是**脚本内私有函数**，无法被复用、
   也无法被单测直接覆盖。B20 需要用同一套分位数口径去描述基准分布，才能和
   已经落盘的 ``window_stats`` 横向比较；两份实现各写一遍必然会在分位数插值
   方式（linear / nearest）、亏损窗口判定（``< 0`` 还是 ``<= 0``）、样本过滤
   条件上分叉，最终产出一张「看起来能对上、其实尺度不同」的表。
   因此这里把口径固化成一份实现，并保持与历史 ``window_stats`` **逐字段同名同义**。

2. 「窗口级配对」这件事有一个必须先说清楚的分界：

   - **B0（现金）**：基准每个窗口的收益恒为 0，所以配对差
     ``strategy_i - 0 = strategy_i``。此时「配对差的中位数」与
     「策略收益的中位数」是同一个数 —— **配对是精确的**，不需要逐窗明细。
   - **B1（买入持有）**：基准逐窗收益随行情变化，要算配对差就必须有
     **策略在同一批窗口上的逐窗收益**。历史结果只落盘了分位数
     （``window_stats``），没有逐窗明细，因此**配对不可重建**。
     这种情况下只能做「分布级对照」（比较同窗口集合上的中位数/分位数/亏损占比），
     并且必须显式声明：*差值的中位数 ≠ 中位数的差*。

   本模块提供 ``WindowComparison`` 来承载这个区分：``paired`` 字段标明该对照
   是否真的逐窗配对，避免调用方（或后来的读者）把分布级差值误读成配对差。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

# 与历史 window_stats 一致的字段名与保留位数（6 位小数）。
# 改这里就等于改口径，必须同步 bump 使用方的版本标识。
_RETURN_DECIMALS = 6
_RATIO_DECIMALS = 4


@dataclass(frozen=True)
class WindowDistribution:
    """一组窗口收益的分布摘要（同名同义于历史 ``window_stats``）。"""

    n_windows: int
    p10: float
    p25: float
    p50: float
    p75: float
    p90: float
    mean: float
    loss_window_ratio: float
    median_loss: float | None
    median_gain: float | None
    worst: float
    best: float
    abs_mean_minus_abs_median: float

    @classmethod
    def from_returns(
        cls,
        values: Iterable[float | None],
        *,
        decimals: int = _RETURN_DECIMALS,
    ) -> "WindowDistribution | None":
        """由逐窗收益构造分布；非有限值与 None 被剔除，全空则返回 None。

        剔除 None 而不是填 0：填 0 会把「该窗口无法估值 / 不可执行」混进
        「该窗口恰好持平」，直接压低亏损窗口占比。
        """
        arr = np.array(
            [float(v) for v in values if v is not None and math.isfinite(float(v))],
            dtype=float,
        )
        if arr.size == 0:
            return None

        losses, gains = arr[arr < 0], arr[arr > 0]
        med = float(np.median(arr))
        mean = float(arr.mean())
        return cls(
            n_windows=int(arr.size),
            p10=round(float(np.percentile(arr, 10)), decimals),
            p25=round(float(np.percentile(arr, 25)), decimals),
            p50=round(med, decimals),
            p75=round(float(np.percentile(arr, 75)), decimals),
            p90=round(float(np.percentile(arr, 90)), decimals),
            mean=round(mean, decimals),
            # 亏损判定严格用 < 0：恰好 0 的窗口记作「未亏」，与历史口径一致。
            loss_window_ratio=round(float(losses.size / arr.size), _RATIO_DECIMALS),
            median_loss=round(float(np.median(losses)), decimals) if losses.size else None,
            median_gain=round(float(np.median(gains)), decimals) if gains.size else None,
            worst=round(float(arr.min()), decimals),
            best=round(float(arr.max()), decimals),
            # |均值| − |中位数|：越大说明极端值把均值拉得越远（尾部集中度佐证）。
            abs_mean_minus_abs_median=round(abs(mean) - abs(med), decimals),
        )

    @classmethod
    def from_mapping(cls, stats: dict | None) -> "WindowDistribution | None":
        """从已落盘的 ``window_stats`` 原样还原（不做任何重算）。"""
        if not stats:
            return None
        return cls(
            n_windows=int(stats["n_windows_valid"]),
            p10=float(stats["p10"]),
            p25=float(stats["p25"]),
            p50=float(stats["p50"]),
            p75=float(stats["p75"]),
            p90=float(stats["p90"]),
            mean=float(stats["mean"]),
            loss_window_ratio=float(stats["loss_window_ratio"]),
            median_loss=None if stats.get("median_loss") is None else float(stats["median_loss"]),
            median_gain=None if stats.get("median_gain") is None else float(stats["median_gain"]),
            worst=float(stats["worst"]),
            best=float(stats["best"]),
            abs_mean_minus_abs_median=float(stats["abs_mean_minus_abs_median"]),
        )

    def to_dict(self) -> dict[str, object]:
        """输出为与历史 ``window_stats`` 同键的字典（便于直接并表）。"""
        return {
            "n_windows_valid": self.n_windows,
            "p10": self.p10,
            "p25": self.p25,
            "p50": self.p50,
            "p75": self.p75,
            "p90": self.p90,
            "mean": self.mean,
            "loss_window_ratio": self.loss_window_ratio,
            "median_loss": self.median_loss,
            "median_gain": self.median_gain,
            "worst": self.worst,
            "best": self.best,
            "abs_mean_minus_abs_median": self.abs_mean_minus_abs_median,
        }


@dataclass(frozen=True)
class WindowComparison:
    """策略与基准在同一窗口集合上的对照。

    ``paired`` 是硬标记，不是注释：它为 False 时，``delta_p50`` 的含义是
    「两个中位数之差」，**不是**「逐窗差值的中位数」。这两者在基准分布偏斜时
    会给出不同的数值，读表的人必须能一眼看出用的是哪一种。
    """

    strategy: WindowDistribution
    benchmark: WindowDistribution
    benchmark_kind: str
    paired: bool
    # 窗口集合是否严格同一批（数量相等只是必要条件，不是充分条件；
    # 历史结果无逐窗明细，因此这里只能校验数量）
    n_aligned: bool

    @property
    def delta_p50(self) -> float:
        return round(self.strategy.p50 - self.benchmark.p50, _RETURN_DECIMALS)

    @property
    def delta_mean(self) -> float:
        return round(self.strategy.mean - self.benchmark.mean, _RETURN_DECIMALS)

    @property
    def delta_loss_ratio(self) -> float:
        return round(
            self.strategy.loss_window_ratio - self.benchmark.loss_window_ratio,
            _RATIO_DECIMALS,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "benchmark_kind": self.benchmark_kind,
            "paired": self.paired,
            "n_aligned": self.n_aligned,
            "strategy": self.strategy.to_dict(),
            "benchmark": self.benchmark.to_dict(),
            "delta_p50": self.delta_p50,
            "delta_mean": self.delta_mean,
            "delta_loss_ratio": self.delta_loss_ratio,
        }


def compare_windows(
    strategy: WindowDistribution,
    benchmark: WindowDistribution,
    *,
    benchmark_kind: str,
    paired: bool,
) -> WindowComparison:
    """构造一次对照（只计算差值，**不做显著性判断**）。

    刻意不提供 p 值：窗口之间高度重叠（step == oos_window 时相邻窗口虽不重叠，
    但同一标的的窗口共享训练段与市场状态），把 n_windows 当独立样本量做检验
    会系统性高估显著性。这里只报描述统计，显著性留给有连续净值之后的阶段。
    """
    return WindowComparison(
        strategy=strategy,
        benchmark=benchmark,
        benchmark_kind=benchmark_kind,
        paired=paired,
        n_aligned=strategy.n_windows == benchmark.n_windows,
    )


def aggregate_comparisons(comparisons: Sequence[WindowComparison]) -> dict[str, object]:
    """把若干组对照汇总成「有多少组跑赢 / 跑输」的计数与幅度分位数。

    只统计 ``n_aligned`` 为真的组：窗口集合数量不一致时，差值里混进了
    「窗口不同」这个与策略无关的因素，不能与对齐的组合并求中位数。
    """
    aligned = [c for c in comparisons if c.n_aligned]
    deltas = np.array([c.delta_p50 for c in aligned], dtype=float) if aligned else np.array([])
    if deltas.size == 0:
        return {
            "n_groups": len(comparisons),
            "n_aligned": 0,
            "n_positive": 0,
            "n_negative": 0,
            "n_zero": 0,
            "median_delta_p50": None,
            "p25_delta_p50": None,
            "p75_delta_p50": None,
        }
    return {
        "n_groups": len(comparisons),
        "n_aligned": int(deltas.size),
        "n_positive": int((deltas > 0).sum()),
        "n_negative": int((deltas < 0).sum()),
        "n_zero": int((deltas == 0).sum()),
        "median_delta_p50": round(float(np.median(deltas)), _RETURN_DECIMALS),
        "p25_delta_p50": round(float(np.percentile(deltas, 25)), _RETURN_DECIMALS),
        "p75_delta_p50": round(float(np.percentile(deltas, 75)), _RETURN_DECIMALS),
    }
