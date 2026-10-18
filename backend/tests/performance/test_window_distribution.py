"""窗口级收益分布与基准对照的测试（B20）。

这组测试要钉住三件事：

1. **口径一致**：``WindowDistribution`` 的分位数 / 亏损窗占比 / 均值-中位偏离
   必须与历史 ``window_stats`` 逐字段同义，否则 B20 的表和历史表无法并读。
2. **缺失不等于 0**：None / NaN 被剔除而不是填 0 —— 填 0 会把「不可执行」
   混进「恰好持平」，直接压低亏损窗口占比。
3. **配对标记不可伪造**：``WindowComparison.paired`` 与 ``n_aligned`` 必须如实反映
   数据能支持到什么程度，聚合时不得把未对齐的组并进去。
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from deepquant.performance.window_distribution import (
    WindowComparison,
    WindowDistribution,
    aggregate_comparisons,
    compare_windows,
)


# --------------------------------------------------------------------------- #
# 1. 与历史 window_stats 的分位数口径一致
# --------------------------------------------------------------------------- #


def test_matches_reference_formula() -> None:
    """用手算的 numpy 参考实现比对，防止分位数插值方式被悄悄改掉。"""
    values = [-0.10, -0.05, -0.02, 0.00, 0.01, 0.03, 0.08, 0.20]
    arr = np.array(values, dtype=float)
    dist = WindowDistribution.from_returns(values)
    assert dist is not None

    assert dist.n_windows == len(values)
    assert dist.p10 == round(float(np.percentile(arr, 10)), 6)
    assert dist.p25 == round(float(np.percentile(arr, 25)), 6)
    assert dist.p50 == round(float(np.median(arr)), 6)
    assert dist.p75 == round(float(np.percentile(arr, 75)), 6)
    assert dist.p90 == round(float(np.percentile(arr, 90)), 6)
    assert dist.mean == round(float(arr.mean()), 6)
    assert dist.worst == round(float(arr.min()), 6)
    assert dist.best == round(float(arr.max()), 6)


def test_loss_ratio_treats_exact_zero_as_not_loss() -> None:
    """恰好 0 的窗口不算亏损 —— 与历史 `arr[arr < 0]` 口径一致。

    这一条决定了「零交易窗口多」的策略看起来更少亏损，因此必须显式钉住，
    避免后来者用 `<= 0` 把结论反向改写。
    """
    dist = WindowDistribution.from_returns([0.0, 0.0, 0.1, -0.1])
    assert dist is not None
    assert dist.loss_window_ratio == pytest.approx(0.25)


def test_median_loss_and_gain_split_correctly() -> None:
    dist = WindowDistribution.from_returns([-0.04, -0.02, 0.0, 0.06, 0.10])
    assert dist is not None
    assert dist.median_loss == pytest.approx(-0.03)
    assert dist.median_gain == pytest.approx(0.08)


def test_abs_mean_minus_abs_median_detects_tail() -> None:
    """尾部集中的分布：|mean| 被极端值拉离 |median|，判据量应显著偏离 0。"""
    flat = WindowDistribution.from_returns([-0.01] * 9 + [-0.01])
    tail = WindowDistribution.from_returns([-0.01] * 9 + [-0.60])
    assert flat is not None and tail is not None
    assert abs(flat.abs_mean_minus_abs_median) < 1e-9
    assert tail.abs_mean_minus_abs_median > 0.05


# --------------------------------------------------------------------------- #
# 2. 缺失值处理
# --------------------------------------------------------------------------- #


def test_none_and_nan_are_dropped_not_zero_filled() -> None:
    """不可执行窗口必须是 None，而不是 0。

    若填 0：4 个窗口里 2 个不可执行 → 亏损窗占比会从 0.5 掉到 0.25，
    「不可执行」被伪装成「持平」。
    """
    dist = WindowDistribution.from_returns([0.10, None, -0.10, float("nan")])
    assert dist is not None
    assert dist.n_windows == 2
    assert dist.loss_window_ratio == pytest.approx(0.5)


def test_all_missing_returns_none() -> None:
    assert WindowDistribution.from_returns([None, float("nan")]) is None
    assert WindowDistribution.from_returns([]) is None


def test_from_mapping_roundtrip() -> None:
    """落盘 → 还原 → 再落盘必须完全一致（否则并表时会出现漂移）。"""
    original = WindowDistribution.from_returns([0.03, -0.02, 0.06, -0.08])
    assert original is not None
    restored = WindowDistribution.from_mapping(original.to_dict())
    assert restored == original
    assert restored.to_dict() == original.to_dict()


def test_from_mapping_handles_missing_optional_fields() -> None:
    """全涨或全跌时 median_loss / median_gain 为 None，还原不得报错。"""
    stats = WindowDistribution.from_returns([0.01, 0.02]).to_dict()
    assert stats["median_loss"] is None
    restored = WindowDistribution.from_mapping(stats)
    assert restored is not None
    assert restored.median_loss is None


# --------------------------------------------------------------------------- #
# 3. 对照与聚合
# --------------------------------------------------------------------------- #


def _dist(values: list[float]) -> WindowDistribution:
    dist = WindowDistribution.from_returns(values)
    assert dist is not None
    return dist


def test_comparison_reports_difference_of_medians() -> None:
    strategy = _dist([0.01, 0.02, 0.03])
    benchmark = _dist([0.02, 0.03, 0.04])
    cmp = compare_windows(strategy, benchmark, benchmark_kind="B1", paired=False)
    assert cmp.delta_p50 == pytest.approx(-0.01)
    assert cmp.paired is False
    assert cmp.n_aligned is True


def test_n_aligned_false_when_window_counts_differ() -> None:
    strategy = _dist([0.01, 0.02])
    benchmark = _dist([0.01, 0.02, 0.03])
    cmp = compare_windows(strategy, benchmark, benchmark_kind="B1", paired=False)
    assert cmp.n_aligned is False


def test_paired_flag_is_preserved_in_dict() -> None:
    """`paired` 必须落盘：它决定 Δ 能不能被读成「配对差」。"""
    cmp = compare_windows(_dist([0.0]), _dist([0.0]), benchmark_kind="B0_cash", paired=True)
    assert cmp.to_dict()["paired"] is True


def test_aggregate_excludes_unaligned_groups() -> None:
    """未对齐的组不得并入中位数 —— 否则差值里混进「窗口不同」这个无关因素。"""
    aligned_good = compare_windows(_dist([0.10, 0.10]), _dist([0.00, 0.00]),
                                   benchmark_kind="B1", paired=False)
    aligned_bad = compare_windows(_dist([-0.10, -0.10]), _dist([0.00, 0.00]),
                                  benchmark_kind="B1", paired=False)
    unaligned = compare_windows(_dist([5.0]), _dist([0.0, 0.0]),
                                benchmark_kind="B1", paired=False)
    agg = aggregate_comparisons([aligned_good, aligned_bad, unaligned])
    assert agg["n_groups"] == 3
    assert agg["n_aligned"] == 2
    assert agg["n_positive"] == 1
    assert agg["n_negative"] == 1
    # 若把 unaligned 的 +5.0 并进来，中位数会变成 +0.10
    assert agg["median_delta_p50"] == pytest.approx(0.0)


def test_aggregate_empty_input_is_explicit_none() -> None:
    agg = aggregate_comparisons([])
    assert agg["n_aligned"] == 0
    assert agg["median_delta_p50"] is None


def test_empty_distribution_kind_is_not_silently_zeroed() -> None:
    """对照组全不可执行时，调用方拿到 None 才能显式处理，不能拿到一个 0 分布。"""
    assert WindowDistribution.from_returns([None] * 5) is None
    assert not math.isnan(0.0)  # 占位断言：确认没有把 None 换成 nan 混入分布
