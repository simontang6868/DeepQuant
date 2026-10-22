"""阶段 S 单测：现代段结项检验的语义、单位铁律与变异检出能力。

按项目铁律，**先证明检查器能抓到故障，再相信它变绿**。
变异覆盖：百分数/小数混用（放大 100 倍）/ 冲击项符号 / 冲击项缺失 /
分段边界 / 判据忽略均值符号 / se_iid 冒充 se_nw / bootstrap 失去确定性 /
幂公式错写 / 逐月序列身份。
"""

from __future__ import annotations

import math
import os
import random
import statistics
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from run_phase_s_exq5_modern import (  # noqa: E402
    BOOT_SEED, IMPACT_UPPER, J_CSV, J_SHA256, NW_LAG, O_CSV, O_SHA256,
    PRIMARY, SCALE_3M_OVER_1M, SPLIT_DATE, SQRT_1000W, SQRT_3000W,
    T_CRIT, Z_POWER80, boot_ci_pp, build_series, judge, load_merged,
    nw_stats_pp, power_at_observed, segment, sha256_file,
)

import run_phase_p_segment_equality as P  # noqa: E402  （交叉实现：幂公式）


# ============================================================ 单位铁律
def test_pp_series_not_amplified_100x():
    """⚠️ newey_west 入参必须是小数；传 pp 会放大 100 倍（踩过两次）。"""
    vals = [0.12, -0.05, 0.08, 0.15, -0.02, 0.10, 0.07, -0.09, 0.11, 0.03]
    st = nw_stats_pp(vals)
    assert st["mean_pp"] == pytest.approx(statistics.fmean(vals), abs=1e-9)
    assert abs(st["mean_pp"]) < 1.0            # 若被放大 100 倍，这里必炸


def test_se_iid_matches_sample_sd_over_sqrt_n():
    vals = [0.1 * math.sin(i) + 0.02 * (i % 5) for i in range(40)]
    st = nw_stats_pp(vals)
    sd = statistics.pstdev(vals) * math.sqrt(len(vals) / (len(vals) - 1))
    assert st["se_iid_pp"] == pytest.approx(sd / math.sqrt(len(vals)), abs=1e-12)


def test_se_nw_exceeds_se_iid_on_positively_autocorrelated():
    """强正自相关序列上 se_nw 必须明显大于 se_iid。
    这条同时钉住『se_iid 冒充 se_nw』的变异。
    注意 L=3 的 Bartlett 截断只能抓住 AR(1) φ=0.9 长程方差的一部分
    （未截断理论比 ≈√((1+φ)/(1−φ))≈4.4，L=3 截断后 ≈1.9），
    所以阈值取 1.5——足以抓住"冒充"（比值恰为 1.0），又不臆造截断做不到的事。"""
    rng = random.Random(11)
    cur = 0.0
    for _ in range(300):                       # 燃烧段
        cur = 0.9 * cur + rng.gauss(0.0, 0.1)
    vals = []
    for _ in range(300):
        cur = 0.9 * cur + rng.gauss(0.0, 0.1)
        vals.append(cur)
    st = nw_stats_pp(vals)
    assert st["se_nw_pp"] > 1.5 * st["se_iid_pp"]


def test_se_nw_close_to_se_iid_on_iid_series():
    """反向钉住：iid 序列上 se_nw 不应偏离 se_iid 太远（防任意放大 se_nw 的变异）。"""
    rng = random.Random(23)
    vals = [rng.gauss(0.0, 0.5) for _ in range(400)]
    st = nw_stats_pp(vals)
    assert 0.8 < st["se_nw_pp"] / st["se_iid_pp"] < 1.25


def test_mde_is_28016_times_se_nw():
    vals = [rng_v for rng_v in (random.Random(3).gauss(0.05, 0.5) for _ in range(100))]
    st = nw_stats_pp(vals)
    assert st["mde_pp"] == pytest.approx(Z_POWER80 * st["se_nw_pp"], abs=1e-12)


def test_power_at_crit_is_coin_flip_and_matches_phase_p():
    # |t|=1.96 时 = 0.5 + Φ(−3.92) ≈ 0.50004（≈抛硬币；余项来自检出"反向"的概率）
    assert power_at_observed(1.96) == pytest.approx(0.5, abs=1e-3)
    for t in (0.26, 1.17, 2.20, 2.58, 4.04):
        assert power_at_observed(t) == pytest.approx(P.power_at_observed(t), abs=1e-12)


# ============================================================ 主序列公式
def test_primary_formula_exact():
    """net_S_3000w = net_BASE_3000w + 0.00078376 − 0.01899561（缺项/错号必炸）。"""
    rows = [{"signal_date": "2020-01-31", "net_BASE_3000w": 0.10,
             "net_BASE_1000w": 0.09, "impact_B_eta1_pp": 0.005,
             "net_STRESS_3000w": 0.0, "net_CONS_1000w": 0.0,
             "net_CONS_3000w": 0.0, "alpha_pp": 0.16,
             "finv_1000w": 0.9, "finv_3000w": 0.95, "n_keep": 3000.0,
             "to": 0.18, "impact_A_pp": 0.0002}]
    S = build_series(rows)
    assert S[PRIMARY][0] == pytest.approx(0.10 + IMPACT_UPPER - SQRT_3000W, abs=1e-12)
    assert S["net_S_1000w_const"][0] == pytest.approx(0.09 + IMPACT_UPPER - SQRT_1000W, abs=1e-12)
    assert S["net_S_1000w_pm"][0] == pytest.approx(0.09 + IMPACT_UPPER - 0.005, abs=1e-12)
    assert S["net_S_3000w_pm"][0] == pytest.approx(
        0.10 + IMPACT_UPPER - 0.005 * SCALE_3M_OVER_1M, abs=1e-12)
    # 缩放系数身份（预登记 §4 冻结）
    assert SCALE_3M_OVER_1M == pytest.approx(SQRT_3000W / SQRT_1000W, abs=1e-12)


def test_scale_factor_value():
    assert SCALE_3M_OVER_1M == pytest.approx(1.6792901856, abs=1e-9)


# ============================================================ 分段
def test_segment_boundary_is_inclusive_of_2016():
    assert segment(["2015-12-31"]) == ["2007-2015"]
    assert segment(["2016-01-01"]) == ["2016-2026"]
    assert SPLIT_DATE == "2016-01-01"


def test_real_segment_counts_108_129():
    rows = load_merged()
    segs = segment([r["signal_date"] for r in rows])
    assert (segs.count("2007-2015"), segs.count("2016-2026")) == (108, 129)


# ============================================================ 判据三档
def test_judge_three_buckets():
    assert judge(+0.11, 2.2)["verdict"] == "MODERN_ERA_ALPHA_DETECTED"
    assert judge(+0.11, 1.5)["verdict"] == "MODERN_ERA_ALPHA_NOT_DETECTED"
    assert judge(-0.11, 2.2)["verdict"] == "MODERN_ERA_ALPHA_NEGATIVE"
    assert judge(+0.11, -2.2)["verdict"] == "MODERN_ERA_ALPHA_DETECTED"   # 双侧
    assert judge(-0.11, 1.5)["verdict"] == "MODERN_ERA_ALPHA_NOT_DETECTED"


def test_judge_never_calls_negative_mean_detected():
    """变异：判据只看 |t| 不看均值符号 → 负 alpha 会被误判为 DETECTED。"""
    assert judge(-0.5, 3.0)["verdict"] != "MODERN_ERA_ALPHA_DETECTED"


# ============================================================ bootstrap
def test_bootstrap_deterministic_and_reasonable():
    rng = random.Random(5)
    vals = [rng.gauss(0.1, 0.5) for _ in range(129)]
    a = boot_ci_pp(vals, n_draws=400, seed=BOOT_SEED)
    b = boot_ci_pp(vals, n_draws=400, seed=BOOT_SEED)
    assert a == b                                    # 失去确定性必炸
    assert a[0] < statistics.fmean(vals) < a[1]
    c = boot_ci_pp(vals, n_draws=400, seed=BOOT_SEED + 1)
    assert c != a                                    # 种子确实在起作用


# ============================================================ 数据闸门（集成）
@pytest.mark.skipif(not os.path.exists(O_CSV) or not os.path.exists(J_CSV),
                    reason="冻结产物不在（新鲜 clone）")
def test_input_sha256_gates():
    assert sha256_file(O_CSV) == O_SHA256
    assert sha256_file(J_CSV) == J_SHA256


@pytest.mark.skipif(not os.path.exists(O_CSV) or not os.path.exists(J_CSV),
                    reason="冻结产物不在（新鲜 clone）")
def test_reproduction_gates_g3_g4_g5():
    """G3/G4/G5：复现阶段 O 已披露数字；不过则本阶段结论无效。"""
    rows = load_merged()
    S = build_series(rows)
    segs = segment([r["signal_date"] for r in rows])
    full = nw_stats_pp(S["net_BASE_3000w"])
    late = nw_stats_pp([v for v, g in zip(S["net_BASE_3000w"], segs) if g == "2016-2026"])
    assert full["t_nw"] == pytest.approx(4.0386, abs=5e-4)      # G3
    assert late["t_nw"] == pytest.approx(2.5813, abs=5e-4)      # G4
    assert statistics.fmean(S["impact_B_eta1_pp"]) == pytest.approx(SQRT_1000W, abs=1e-6)  # G5
    # 逐月 √ 冲击：后段必须显著低于前段（主检验用常数因而是保守的）
    e = statistics.fmean([v for v, g in zip(S["impact_B_eta1_pp"], segs) if g == "2007-2015"])
    l = statistics.fmean([v for v, g in zip(S["impact_B_eta1_pp"], segs) if g == "2016-2026"])
    assert l < 0.5 * e


@pytest.mark.skipif(not os.path.exists(O_CSV) or not os.path.exists(J_CSV),
                    reason="冻结产物不在（新鲜 clone）")
def test_primary_series_matches_formula_on_real_data():
    rows = load_merged()
    S = build_series(rows)
    for a, b in zip(S[PRIMARY], rows):
        assert a == pytest.approx(b["net_BASE_3000w"] + IMPACT_UPPER - SQRT_3000W, abs=1e-9)
