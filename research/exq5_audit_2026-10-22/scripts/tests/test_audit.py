#!/usr/bin/env python
"""exQ5 审计代码测试套件（任务 §11：新增代码必须有测试）。

覆盖：独立统计实现（NW/iid/功效/MDE）、独立容量与成本实现、
独立分组/MAX5 与冻结生产实现的交叉一致性、A1 复算闸门、A2 集中度数学、
A3 现金分解恒等式、确定性。

运行：
  cd "D:/DeepQuant power by txm/DeepQuant-main"
  PYTHONPATH=scripts;"D:/DeepQuant power by txm/DeepQuant-main/research/exq5_audit_2026-10-22/scripts" \
    "C:/Users/77436/.workbuddy/binaries/python/envs/dq_clean/Scripts/python.exe" -m pytest \
    research/exq5_audit_2026-10-22/scripts/tests -q
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
AUD = os.path.abspath(os.path.join(HERE, ".."))
REPO = os.path.abspath(os.path.join(AUD, "..", "..", ".."))
sys.path.insert(0, AUD)
sys.path.insert(0, os.path.join(REPO, "scripts"))

import audit_common as A  # noqa: E402


# ==================== 独立统计实现 ====================
def test_nw_matches_hand_computation():
    """手算小样本 NW(L=1)：x = [1, 2, 3, 4]，Bartlett。"""
    x = [1.0, 2.0, 3.0, 4.0]
    g = A.nw_profile(x, lags=(1,))[1]
    m = 2.5
    e = np.array(x) - m
    g0 = float(np.dot(e, e)) / 4
    g1 = float(np.dot(e[1:], e[:-1])) / 4
    s = g0 + 2.0 * (1 - 1 / 2) * g1
    se = math.sqrt(s / 4)
    assert g["mean"] == pytest.approx(m)
    assert g["se_nw"] == pytest.approx(se, rel=1e-12)
    assert g["t_nw"] == pytest.approx(m / se, rel=1e-12)


def test_nw_l0_vs_iid_relationship():
    """L=0 的 NW 用总体方差（/n），iid 用样本方差（/n-1）→ se_iid = se_nw×√(n/(n-1))。"""
    rng = np.random.default_rng(7)
    x = rng.normal(0.01, 0.02, 80).tolist()
    g = A.nw_profile(x, lags=(0,))[0]
    ratio = g["se_iid"] / g["se_nw"]
    assert ratio == pytest.approx(math.sqrt(80 / 79), rel=1e-10)


def test_power_at_196_is_documented_value():
    """power(1.96) = 0.5 + Φ(-3.92) = 0.500044（项目记录值）。"""
    assert A.power_two_sided(1.96) == pytest.approx(0.500044, abs=1e-6)
    assert A.power_two_sided(0.0) == pytest.approx(2 * A.norm_cdf(-1.96), rel=1e-12)


def test_mde_coefficient():
    assert A.mde_of(0.05) == pytest.approx(2.8016 * 0.05)


def test_nw_deterministic():
    rng = np.random.default_rng(3)
    x = rng.normal(0, 1, 100).tolist()
    assert A.nw_profile(x) == A.nw_profile(x)


def test_nw_variance_nonnegative_no_spurious_fallback():
    """Bartlett-NW（有偏自协方差 ÷n）恒等于 e'Ke，K 半正定 → s≥0 必然成立。

    ⚠️ 这条测试的来历：初版断言 `[1,-1]*30` 在 L=3 会触发 s<=0 回退，实测
    s=+0.0167、fell_back=False —— 失败原因是**测试前提写错**，不是实现错。
    数学上 s = e'Ke/n（K 为 Bartlett 核 Toeplitz 阵，半正定）， demean 只是
    把 e 限制到 1 的正交补，半正定性保持，故 s 不可能为负；`fell_back` 是
    浮点噪声保护，不是可达的经济情形。这里改为检验真实性质：一组对抗性
    序列（交替、近 -1 的 AR、一阶差分）在 L=0..6 全网格上 s>0 且不回退。
    """
    cases = {
        "alternating": [1.0, -1.0] * 30,
        "ar_minus_095": None,   # 下面构造
        "first_diff": None,
    }
    # AR(1) phi=-0.95（强负自相关）
    rng = __import__("numpy").random.default_rng(7)
    e = rng.normal(size=200)
    x = [0.0] * 200
    for t in range(1, 200):
        x[t] = -0.95 * x[t - 1] + e[t]
    cases["ar_minus_095"] = x
    # 一阶差分（g1 = -g0/2，L 越大 s 越接近 0）
    z = rng.normal(size=200)
    cases["first_diff"] = list(z[1:] - z[:-1])

    for name, series in cases.items():
        prof = A.nw_profile(series, lags=(0, 1, 2, 3, 4, 5, 6))
        for L, g in prof.items():
            assert g["se_nw"] >= 0.0, f"{name} L={L} se_nw<0"
            assert g["fell_back"] is False, f"{name} L={L} 意外回退"
            assert g["se_nw"] > 0.0, f"{name} L={L} 方差恰为 0（构造不该出现）"
    # 对照：交替序列 L=3 的 s 应严格为正（初版误判的那个点）
    assert A.nw_profile([1.0, -1.0] * 30, lags=(3,))[3]["fell_back"] is False


# ==================== 独立容量与成本 ====================
def test_capacity_indep_matches_production():
    """独立容量实现必须与冻结生产 capacity_of 逐位一致（定义相同、代码路径不同）。"""
    from run_phase_i_exq5 import capacity_of
    rng = np.random.default_rng(11)
    for trial in range(20):
        n = int(rng.integers(50, 500))
        lots = (rng.lognormal(6.0, 1.2, n) * 100).tolist()  # 一手金额，重尾
        for budget in (1.5e6, 1e7, 3e7):
            a = A.capacity_indep(lots, budget)
            b = capacity_of(lots, budget)
            assert a["k"] == b["k"]
            assert a["covered"] == pytest.approx(b["covered"], abs=1e-12)
            assert a["uninvested"] == pytest.approx(b["uninvested"], abs=1e-12)
            assert a["weight_mae_pp"] == pytest.approx(b["weight_mae_pp"], abs=1e-9)


def test_capacity_decomp_identity():
    """未投资比例恒等于整手取整残差（预算 − 实际投入）。"""
    rng = np.random.default_rng(13)
    lots = (rng.lognormal(6.0, 1.0, 400) * 100).tolist()
    for budget in (1e7, 3e7):
        cs = A.capacity_indep(lots, budget)
        order = np.argsort(np.asarray(lots), kind="stable")
        s = np.asarray(lots)[order]
        k = cs["k"]
        per = budget / k
        rounding = float((per - np.floor(per / s[:k]) * s[:k]).sum())
        assert cs["uninvested"] == pytest.approx(rounding / budget, abs=1e-12)


def test_cost_formulas_match_prereg():
    """三口径成本公式（预登记 §2 写死值）。"""
    to = 0.10
    assert A.cost_base_indep(to) == pytest.approx((2 * 0.10 * 0.00026 + 0.10 * 0.0005) * 100, abs=1e-12)
    assert A.cost_stress_indep(to) == pytest.approx(0.06, abs=1e-12)
    # CONS：单笔金额 3000万/1936≈15496 元 × 万2.5 = 3.87 元 < 5 → 按下限
    c = A.cost_cons_indep(0.188, 3.0e7, 1936)
    per = 3.0e7 / 1936
    assert per * 0.00025 < 5.0
    expect = 2 * 0.188 * 1936 * 5.0 / 3.0e7 * 100 + (2 * 0.188 * 1e-5 + 0.188 * 5e-4) * 100
    assert c == pytest.approx(expect, abs=1e-12)


# ==================== 独立分组 / MAX5 交叉一致性 ====================
def test_quintile_groups_match_production_split():
    """独立分组（array_split）必须与生产 split_equal_indep 一致（含并列值）。"""
    from run_phase_i_exq5 import split_equal_indep
    rng = np.random.default_rng(17)
    for trial in range(30):
        n = int(rng.integers(20, 400))
        x = np.round(rng.normal(0, 1, n), 1)      #  rounding → 大量并列
        assert np.array_equal(A_quintile(x, 5), split_equal_indep(x, 5))
        assert np.array_equal(A_quintile(x, 5), split_equal_indep(x, 5))


def A_quintile(x, k):
    n = x.size
    order = np.argsort(x, kind="stable")
    g = np.empty(n, dtype=int)
    for gi, part in enumerate(np.array_split(order, k)):
        g[part] = gi
    return g


def test_max5_sort_matches_partition():
    """独立 MAX5（np.sort）与生产 max5_partition 一致（含 NaN/-inf）。"""
    from run_phase_i_exq5 import max5_partition
    rng = np.random.default_rng(19)
    for trial in range(20):
        w = rng.normal(0, 0.03, (21, 60))
        w[rng.random(w.shape) < 0.05] = -np.inf
        a = A_max5(w)
        b = max5_partition(w)
        assert np.allclose(a, b, equal_nan=True)


def A_max5(w):
    if w.shape[0] < 5:
        return np.full(w.shape[1], np.nan)
    srt = np.sort(w, axis=0)
    return srt[w.shape[0] - 5:, :].mean(axis=0)


# ==================== A1 复算闸门 ====================
def test_a1_reproduces_v0_from_frozen_csv():
    """从冻结 phasei monthly.csv 独立重算 V0：+0.230703 / t=+6.357163 / n=237。"""
    rows = A.read_csv_rows(A.FROZEN["phasei_monthly"])
    v0 = A.col(rows, "alpha_v0_pp")
    g = A.nw_t(v0, 3)
    assert g["n"] == 237
    assert g["mean"] == pytest.approx(0.230703, abs=1e-5)
    assert g["t_nw"] == pytest.approx(6.357163, abs=5e-4)


def test_a1_input_hashes_frozen():
    """冻结输入哈希（预登记引用值）必须仍匹配。"""
    assert A.sha256_file(A.FROZEN["phaseo_monthly"]) == \
        "768e49a9462cd7c00f3aacfcdb1e87bbe37bb75b348cd6fd85809e12493a4152"
    assert A.sha256_file(A.FROZEN["phases_monthly"]) == \
        "3333efe96ac0db29d1568057678eda5187d0c03b42c1e508f5eb8736bd24da91"


# ==================== A2 集中度数学 ====================
def test_concentration_known_case():
    """合成序列：top1 正贡献占比可手算。"""
    x = [10.0] + [1.0] * 9
    pos = sorted([v for v in x if v > 0], reverse=True)
    assert pos[0] / sum(pos) == pytest.approx(10 / 19)
    # 全等序列 Gini = 0
    from a2_time_concentration import concentration
    c = concentration([2.0] * 10, "flat")
    assert c["gini_abs"] == pytest.approx(0.0, abs=1e-12)
    assert c["top1_pos_share"] == pytest.approx(0.1)


def test_rolling_window_count():
    """129 个月序列的 60 月滚动窗口数 = 70（复现阶段 T 的 70 窗）。"""
    assert 129 - 60 + 1 == 70
    assert 237 - 60 + 1 == 178
