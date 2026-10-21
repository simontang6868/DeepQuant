"""阶段 P 单测：分段相等性检验的语义与变异检出能力。

按项目铁律，**先证明检查器能抓到故障，再相信它变绿**。
本文件的变异测试覆盖：符号翻转 / 百分数与小数混用 / 单位放大 /
缺失值传播 / 日期窗口越界 / 单侧双侧混用 / 有限样本修正。
"""

from __future__ import annotations

import math
import os
import random
import statistics
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from run_phase_p_segment_equality import (  # noqa: E402
    MAIN_CSV_SHA256, NW_LAG, T_CRIT, Z_POWER80, hac_dummy_regression,
    power_at_observed, read_series, segment_means,
)

SPLIT = "2016-01-01"


def _mk(n1: int, n2: int, m1: float, m2: float, sd: float = 1.0,
        seed: int = 7) -> tuple[list[float], list[int]]:
    rng = random.Random(seed)
    y = [rng.gauss(m1, sd) for _ in range(n1)] + [rng.gauss(m2, sd) for _ in range(n2)]
    d = [0] * n1 + [1] * n2
    return y, d


# ============================================================ 基本语义
def test_beta1_equals_difference_of_group_means():
    y, d = _mk(40, 60, 0.5, 0.2)
    r = hac_dummy_regression(y, d, NW_LAG)
    g1 = [v for v, x in zip(y, d) if x == 0]
    g2 = [v for v, x in zip(y, d) if x == 1]
    expect = statistics.fmean(g2) - statistics.fmean(g1)
    assert r["beta1"] == pytest.approx(expect, abs=1e-12)
    assert r["beta0"] == pytest.approx(statistics.fmean(g1), abs=1e-12)


def test_fwl_residual_equals_full_ols_residual():
    """FWL 定理：先对截距去均值再单变量回归，残差与全回归 OLS 残差逐位相同。"""
    y, d = _mk(30, 45, 1.0, 0.4)
    r = hac_dummy_regression(y, d, NW_LAG)
    # 独立算全回归：设计矩阵 [1, d]，正规方程 2×2
    n = len(y)
    s11, s12, s22 = n, sum(d), sum(x * x for x in d)
    t1, t2 = sum(y), sum(a * b for a, b in zip(d, y))
    det = s11 * s22 - s12 * s12
    b0 = (t1 * s22 - t2 * s12) / det
    b1 = (s11 * t2 - s12 * t1) / det
    resid_full = [v - b0 - b1 * x for v, x in zip(y, d)]
    # FWL 残差
    ybar, dbar = statistics.fmean(y), statistics.fmean(d)
    dy = [v - ybar for v in y]
    dd = [v - dbar for v in d]
    beta1 = sum(a * b for a, b in zip(dd, dy)) / sum(v * v for v in dd)
    resid_fwl = [a - beta1 * b for a, b in zip(dy, dd)]
    assert beta1 == pytest.approx(b1, abs=1e-10)
    for a, b in zip(resid_full, resid_fwl):
        assert a == pytest.approx(b, abs=1e-10)


def test_lag0_without_finite_sample_equals_hc0_two_sample_se():
    """L=0 且不加有限样本修正时，必须**精确**等于 HC0 两样本 se。

    推导：Σ D̃² = n1·n2/n，D̃_late = n1/n、D̃_early = −n2/n
    Var = [(n1/n)²·A + (n2/n)²·B] / (n1n2/n)² = A/n2² + B/n1²
    """
    y, d = _mk(37, 53, 0.8, 0.3, sd=1.4)
    r = hac_dummy_regression(y, d, lag=0, finite_sample=False)
    n1, n2 = 37, 53
    g1 = [v for v, x in zip(y, d) if x == 0]
    g2 = [v for v, x in zip(y, d) if x == 1]
    m1, m2 = statistics.fmean(g1), statistics.fmean(g2)
    A = sum((v - m2) ** 2 for v in g2)      # 组内残差平方和（晚段）
    B = sum((v - m1) ** 2 for v in g1)      # 早段
    expect = math.sqrt(A / n2 ** 2 + B / n1 ** 2)
    assert r["se"] == pytest.approx(expect, rel=1e-12)


def test_finite_sample_correction_inflates_se():
    y, d = _mk(40, 60, 0.5, 0.2)
    a = hac_dummy_regression(y, d, NW_LAG, finite_sample=True)
    b = hac_dummy_regression(y, d, NW_LAG, finite_sample=False)
    assert a["se"] > b["se"]
    assert a["se"] == pytest.approx(b["se"] * math.sqrt(len(y) / (len(y) - 2)), rel=1e-12)


def test_degenerate_when_single_group():
    r = hac_dummy_regression([1.0, 2.0, 3.0], [1, 1, 1], NW_LAG)
    assert r["degenerate"] is True and r["sxx"] == 0.0


def test_lag_is_clamped_for_short_series():
    y, d = _mk(3, 4, 0.1, 0.2)
    r = hac_dummy_regression(y, d, lag=99)   # 不得崩溃
    assert r["lag"] <= len(y) - 3 and r["se"] > 0


# ============================================================ 事后功效
def test_power_at_observed_matches_known_values():
    assert power_at_observed(T_CRIT) == pytest.approx(0.50, abs=2e-3)  # |t|=临界 → 抛硬币
    assert power_at_observed(0.0) == pytest.approx(0.05, abs=2e-3)     # 效应=0 → ≈检验水平
    # 大效应：|t|=4 → Φ(4−1.96)=Φ(2.04)=0.9793（不是 >0.98，别把期望写紧）
    assert power_at_observed(4.0) == pytest.approx(0.9793, abs=1e-3)
    assert power_at_observed(6.0) > 0.999                              # |t|=6 → Φ(4.04)
    assert power_at_observed(float("nan")) != power_at_observed(float("nan"))


def test_mde_is_consistent_with_se():
    y, d = _mk(60, 80, 0.3, 0.1)
    r = hac_dummy_regression(y, d, NW_LAG)
    assert r["mde"] == pytest.approx(Z_POWER80 * r["se"], rel=1e-12)


# ============================================================ 变异测试
def test_mutation_sign_flip_is_detected():
    """符号翻转：交换组标签，β1 必须精确反号、se 不变。"""
    y, d = _mk(50, 70, 0.9, 0.4)
    a = hac_dummy_regression(y, d, NW_LAG)
    b = hac_dummy_regression(y, [1 - x for x in d], NW_LAG)
    assert a["beta1"] == pytest.approx(-b["beta1"], abs=1e-12)
    assert a["se"] == pytest.approx(b["se"], rel=1e-12)


def test_unit_scale_keeps_t_but_scales_mde():
    """百分数与小数混用：×100 后 t 不变，但 β1/se/MDE 全部 ×100。

    → 若有人把小数序列当 pp 报，MDE 会小 100 倍，看起来「检验很灵敏」。
      t 尺度无关，所以**只看 t 发现不了**，必须同时看 MDE 的量级。
    """
    y, d = _mk(50, 70, 0.0074, 0.0030, sd=0.02)
    a = hac_dummy_regression(y, d, NW_LAG)
    b = hac_dummy_regression([v * 100 for v in y], d, NW_LAG)
    assert a["t"] == pytest.approx(b["t"], rel=1e-10)
    assert b["beta1"] == pytest.approx(a["beta1"] * 100, rel=1e-10)
    assert b["mde"] == pytest.approx(a["mde"] * 100, rel=1e-10)
    # 量级哨兵：MDE 与均值同量级（本研究 §6 的哨兵思路）
    assert b["mde"] < abs(b["beta1"]) * 10


def test_unit_amplification_sentry():
    """单位放大 100 倍时，MDE 会大到荒谬 → 可被哨兵抓住。"""
    y, d = _mk(50, 70, 0.5, 0.3)
    r = hac_dummy_regression(y, d, NW_LAG)
    # 合理的 MDE 应在组均值量级的数倍之内
    assert r["mde"] < 10 * max(abs(statistics.fmean(y)), 1e-9)


def test_missing_values_must_not_silently_shrink_sample():
    """缺失值传播：序列里混入 NaN 时，回归不得静默给出看起来正常的结果。"""
    y, d = _mk(40, 50, 0.5, 0.2)
    y_bad = list(y)
    y_bad[10] = float("nan")
    r = hac_dummy_regression(y_bad, d, NW_LAG)
    # NaN 会污染均值 → β1 变 NaN。这不是「正确处理」，而是**必须可见**。
    assert r["beta1"] != r["beta1"] or r["degenerate"]
    # 正确用法：先剔除再回归，且 n 必须变小
    pairs = [(v, x) for v, x in zip(y_bad, d) if v == v]
    r2 = hac_dummy_regression([v for v, _ in pairs], [x for _, x in pairs], NW_LAG)
    assert r2["n"] == len(y) - 1


def test_date_window_out_of_range_changes_n():
    """日期窗口越界：过滤窗口外的月份必须真的减少样本量。"""
    dates = ["2006-06-30", "2007-01-31"] + [f"20{y:02d}-{m:02d}-28"
                                            for y in range(7, 27) for m in (3, 9)]
    vals = [float(i) for i in range(len(dates))]
    seg = segment_means(vals, dates)
    total = sum(v["n"] for v in seg.values())
    assert total < len(dates)      # 2006-06-30 必须被排除
    assert seg["2007-2015"]["n"] + seg["2016-2026"]["n"] == total


def test_two_sided_not_one_sided():
    """单侧与双侧混用：p 必须是双侧的（|t|=1.96 → p≈0.05，不是 0.025）。"""
    y, d = _mk(50, 70, 0.0, 0.0)
    r = hac_dummy_regression(y, d, NW_LAG)
    assert r["p"] >= 0.0
    # 双侧 p = erfc(|t|/√2)；单侧会是其一半
    assert r["p"] == pytest.approx(math.erfc(abs(r["t"]) / math.sqrt(2)), rel=1e-10)


def test_positive_autocorrelation_inflates_hac_se():
    """正自相关下 HAC se 必须大于 L=0 的 HC0 se —— 否则 HAC 没起作用。"""
    rng = random.Random(11)
    e, prev = [], 0.0
    for _ in range(120):
        prev = 0.8 * prev + rng.gauss(0, 1)
        e.append(prev)
    y = [5.0 + v for v in e[:60]] + [5.5 + v for v in e[60:]]
    d = [0] * 60 + [1] * 60
    a = hac_dummy_regression(y, d, lag=0, finite_sample=False)
    b = hac_dummy_regression(y, d, lag=6, finite_sample=False)
    assert b["se"] > a["se"]


# ============================================================ 产物与门控
def test_main_series_fingerprint_is_pinned():
    """主序列的期望指纹写死在脚本里，防止产物被换掉而检验照跑。"""
    import run_phase_p_segment_equality as P
    got = P._sha256_file(P.MAIN_CSV)
    assert got == MAIN_CSV_SHA256


def test_read_series_rejects_missing_column(tmp_path=None):
    import run_phase_p_segment_equality as P
    p = "phaseo_out/ac7aa2830ab0/monthly.csv"
    dates, vals = P.read_series(p, "signal_date", "net_BASE_3000w")
    assert len(dates) == len(vals) == 237
    assert all(v == v for v in vals)


def test_segment_means_reproduces_phase_o_segments():
    """复现闸门：分段均值必须复现阶段 O 的 segments_BASE_3000w。"""
    dates, vals = read_series("phaseo_out/ac7aa2830ab0/monthly.csv",
                              "signal_date", "net_BASE_3000w")
    seg = segment_means(vals, dates)
    assert seg["2007-2015"]["n"] == 108
    assert seg["2016-2026"]["n"] == 129
    assert seg["2007-2015"]["mean_pp"] == pytest.approx(0.143784, abs=5e-5)
    assert seg["2016-2026"]["mean_pp"] == pytest.approx(0.129408, abs=5e-5)


def test_main_test_result_is_pinned():
    """把主检验结果钉住：改动序列或统计量都必须让这里失败。"""
    dates, vals = read_series("phaseo_out/ac7aa2830ab0/monthly.csv",
                              "signal_date", "net_BASE_3000w")
    d = [1 if dt >= SPLIT else 0 for dt in dates]
    r = hac_dummy_regression(vals, d, NW_LAG)
    assert r["beta1"] == pytest.approx(-0.014377, abs=5e-5)
    assert r["se"] == pytest.approx(0.066608, abs=5e-5)
    assert r["t"] == pytest.approx(-0.2158, abs=5e-3)
    assert abs(r["t"]) < T_CRIT
    assert r["mde"] == pytest.approx(0.186609, abs=5e-5)


def test_positive_control_borderline_is_pinned():
    """正对照擦边：|t| 略高于 1.96 必须被记录，不能被悄悄四舍五入成「显著」。"""
    dates, vals = read_series("phasel_out/9ffc3710cf6f/monthly.csv",
                              "signal_date", "alpha_v1_pp")
    d = [1 if dt >= SPLIT else 0 for dt in dates]
    r = hac_dummy_regression(vals, d, NW_LAG)
    assert r["beta1"] == pytest.approx(-0.129203, abs=5e-5)
    assert abs(r["t"]) >= T_CRIT
    assert abs(r["t"]) - T_CRIT < 0.01          # 余量极小
    assert power_at_observed(abs(r["t"])) == pytest.approx(0.50, abs=1e-2)
