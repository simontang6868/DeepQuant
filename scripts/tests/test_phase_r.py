"""阶段 R 单测：SURGE 冲击成本与真实费率复核。

按项目铁律：**先证明检查器能抓到故障，再相信它变绿。**
"""

from __future__ import annotations

import math
import os
import statistics
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from run_phase_r_surge_impact import (  # noqa: E402
    STAMP_RATE, T_CRIT, TRANSFER_RATE, commission_pp, judge_impact, local_of,
    net_stats,
)


# ============================================================ local_of（本阶段修的那个 bug）
def test_local_of_handles_unsorted_code_order():
    """⚠️ `codes` 顺序不是列号顺序。必须 argsort 后才能二分，否则静默取错股票。"""
    raw = np.array([500, 12, 3000, 7, 888])          # 未排序的全局列号
    order = np.argsort(raw)
    idx_sorted = raw[order]
    assert np.all(np.diff(idx_sorted) > 0)
    # 取第 0、2、4 个 code（列号 500 / 3000 / 888）
    keep = raw[[0, 2, 4]]
    loc = local_of(idx_sorted, keep)
    assert sorted(loc.tolist()) == [2, 3, 4]         # 排序后的局部位置
    assert sorted(idx_sorted[loc].tolist()) == sorted(keep.tolist())


def test_local_of_roundtrip_is_identity():
    rng = np.random.default_rng(7)
    raw = rng.choice(5000, size=200, replace=False)
    idx_sorted = np.sort(raw)
    loc = local_of(idx_sorted, raw)
    assert np.array_equal(idx_sorted[loc], raw)


def test_local_of_rejects_unsorted_index():
    """若传入未排序数组必须**报错**，不能给出看似合理的结果。"""
    with pytest.raises(ValueError):
        local_of(np.array([3, 1, 2]), np.array([1]))


def test_local_of_drops_missing_codes():
    idx = np.array([10, 20, 30])
    keep = np.array([10, 99, 30])       # 99 不在 idx 中
    loc = local_of(idx, keep)
    assert sorted(idx[loc].tolist()) == [10, 30]


def test_local_of_empty():
    assert local_of(np.array([1, 2, 3]), np.array([], dtype=int)).size == 0


# ============================================================ 佣金
def test_all_in_form_matches_phase_L_original():
    """阶段 L 原式：2×to×rate×100，不再另加印花税/过户费。"""
    got = commission_pp(0.2409, 3e7, 1900, 0.003, 0.0, all_in=True)
    assert got == pytest.approx(2 * 0.2409 * 0.003 * 100, rel=1e-12)


def test_split_form_adds_stamp_and_transfer():
    """拆分口径 = 佣金(双边) + 印花税(仅卖出) + 过户费(双边)。"""
    to, w, k, rate = 0.20, 1e7, 1000, 0.00025
    got = commission_pp(to, w, k, rate, 0.0, all_in=False)
    per_trade = w / k
    n_trades = 2 * to * k
    expect = (n_trades * per_trade * rate / w * 100
              + to * STAMP_RATE * 100
              + 2 * to * TRANSFER_RATE * 100)
    assert got == pytest.approx(expect, rel=1e-12)


def test_min_commission_binds_for_small_positions():
    """单笔 5,000 元 × 万2.5 = 1.25 元 < 5 元 → 按下限 5 元收。"""
    w, k = 1e7, 2000                     # 单笔 5,000 元
    with_min = commission_pp(0.2, w, k, 0.00025, 5.0, all_in=False)
    without = commission_pp(0.2, w, k, 0.00025, 0.0, all_in=False)
    assert with_min > without
    n_trades = 2 * 0.2 * k
    expect_min = n_trades * 5.0 / w * 100 + 0.2 * STAMP_RATE * 100 \
        + 2 * 0.2 * TRANSFER_RATE * 100
    assert with_min == pytest.approx(expect_min, rel=1e-9)


def test_lower_rate_does_not_help_when_floor_binds():
    """⚠️ 1,000 万资金下万3→万1 节省 0 —— 因为单笔仅约 5 千元，都被下限收走。"""
    w, k = 1e7, 1937
    a = commission_pp(0.2, w, k, 0.0003, 5.0, all_in=False)
    b = commission_pp(0.2, w, k, 0.0001, 5.0, all_in=False)
    assert a == pytest.approx(b, abs=1e-12)


def test_floor_escape_threshold():
    """摆脱下限需 单笔×费率 ≥ 5 → 单笔 ≥ 5/rate。"""
    for rate, expect_per in ((0.00025, 20000.0), (0.0001, 50000.0)):
        per = 5.0 / rate
        assert per == pytest.approx(expect_per)
        w_star = per * 1937
        # 刚好在门槛上：佣金应等于下限
        got = commission_pp(0.2, w_star, 1937, rate, 5.0, all_in=False)
        got_no_floor = commission_pp(0.2, w_star, 1937, rate, 0.0, all_in=False)
        assert got == pytest.approx(got_no_floor, rel=1e-6)


def test_zero_turnover_has_no_commission():
    assert commission_pp(0.0, 1e7, 100, 0.00025, 5.0, all_in=False) == 0.0


# ============================================================ 净值统计
def test_net_stats_uses_nw_se_not_pp_se():
    """⚠️ net 已是 pp。用 `se_nw_pp` 会把 CI 放大 100 倍（本项目已踩两次）。"""
    rng = np.random.default_rng(11)
    net = [rng.normal(0.07, 0.3) for _ in range(120)]
    st = net_stats(net)
    m = statistics.fmean(net)
    half = (st["ci95_pp"][1] - st["ci95_pp"][0]) / 2
    se_implied = half / T_CRIT
    # 半宽应与 |mean/t| 同量级；若用了 se_nw_pp，会大 100 倍
    assert 0 < se_implied < abs(m) * 20
    assert abs(st["net_pp"] - m) < 1e-5


def test_net_stats_reports_both_t_iid_and_t_nw():
    rng = np.random.default_rng(3)
    st = net_stats([rng.normal(0.05, 0.2) for _ in range(100)])
    assert "t_iid" in st and "t_nw" in st and "p_nw" in st


# ============================================================ 判据
def test_judge_impact_four_states():
    assert judge_impact([True, True], [True, True]) == \
        "SURGE_EXECUTABLE_UNDER_REAL_FEES"
    assert judge_impact([True, True], [False, False]) == \
        "SURGE_IMPACT_MODEL_DEPENDENT"
    assert judge_impact([True, False], [False, False]) == \
        "SURGE_SCALE_DEPENDENT"
    assert judge_impact([False, False], [False, False]) == \
        "SURGE_NOT_EXECUTABLE"


def test_judge_impact_model_dependent_is_conservative():
    """主口径过、悲观口径不过 → 必须判 MODEL_DEPENDENT，不许判 EXECUTABLE。"""
    v = judge_impact([True, True, True], [True, True, False])
    assert v == "SURGE_IMPACT_MODEL_DEPENDENT"


# ============================================================ 变异测试
def test_mutation_unit_scale_scales_commission_linearly():
    """单位放大：W×100 且 k 不变 → 单笔金额×100 → 若下限不绑定，佣金不变（按比例）。"""
    a = commission_pp(0.2, 1e7, 1000, 0.00025, 0.0, all_in=False)
    b = commission_pp(0.2, 1e9, 1000, 0.00025, 0.0, all_in=False)
    # 佣金部分与 W 无关（按成交额比例），印花税/过户费也只与换手有关
    assert a == pytest.approx(b, rel=1e-9)


def test_mutation_turnover_zero_kills_commission():
    assert commission_pp(0.0, 3e7, 1900, 0.003, 5.0, all_in=True) == 0.0


def test_mutation_sign_of_net_flips_with_alpha():
    """alpha 反号 → net 反号 → t 反号。"""
    rng = np.random.default_rng(5)
    base = [rng.normal(0.07, 0.3) for _ in range(120)]
    a = net_stats(base)
    b = net_stats([-x for x in base])
    assert a["t_nw"] == pytest.approx(-b["t_nw"], abs=1e-9)
    assert a["net_pp"] == pytest.approx(-b["net_pp"], abs=1e-9)


def test_mutation_nan_is_visible_not_silent():
    """NaN 必须**可见**（打标 + 用有效子集），不许抛异常后无声无息。"""
    net = [0.05, float("nan"), 0.06, 0.07]
    st = net_stats(net)
    assert st["has_nonfinite"] is True
    assert st["n_valid"] == 3
    assert abs(st["net_pp"] - statistics.fmean([0.05, 0.06, 0.07])) < 1e-9


def test_net_stats_degenerate_inputs_do_not_crash():
    for bad in ([], [1.0], [float("nan")] * 4):
        st = net_stats(bad)
        assert st["insufficient"] is True
        assert st["net_pp"] != st["net_pp"]


def test_f_inv_must_be_invested_capital_not_covered_names():
    """⚠️ `covered`（能买到的标的比例）≠ `1 − uninvested`（已投资资金比例）。

    第一版我误用 `cs["covered"]`（≈0.999）当 f_inv，把净值高估约 5%×alpha
    （≈0.005pp）。阶段 J/O 一律用 `1 − uninvested`。这里把两者钉死区分。
    """
    from run_phase_i_exq5 import capacity_of
    # 1000 只，每手 3,000 元；预算 1000 万 → 每只约 1 万 → 3 手 = 9,000 元
    lots = [3000.0] * 1000
    cs = capacity_of(lots, 1.0e7)
    assert cs["covered"] > 0.99            # 几乎每只都能买
    f_inv = 1.0 - cs["uninvested"]
    assert f_inv < cs["covered"]           # 但现金残留让已投资比例明显更低
    assert cs["uninvested"] > 0.05         # 整手取整留下的现金


def test_reproduction_gate_constants_match_phase_L():
    """复现闸门的目标来自阶段 L 产物文件，不是我手抄的数。"""
    import csv
    rows = list(csv.DictReader(
        open("phasel_out/9ffc3710cf6f/monthly.csv", encoding="utf-8")))
    assert len(rows) == 237
    vals = [float(r["alpha_v1_pp"]) for r in rows if r["alpha_v1_pp"]]
    assert abs(statistics.fmean(vals) - 0.106057) < 1e-4
