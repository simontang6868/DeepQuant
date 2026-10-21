"""阶段 O（费率口径）的单元测试。

钉住三件事：

1. **税费不对称**：印花税**仅卖出方**，不许为保持对称而按双边计；
2. **最低佣金**：单笔取 max(从价, 5 元)，在标的数多、单笔金额小时会主导成本；
3. ⚠️ **单位坑**：`newey_west` 的 `se_nw_pp` 假设入参是小数。
   本阶段传入的是 **pp**，必须用 `se_nw`。这是本项目**第二次**踩同一个坑
   （第一次在阶段 J 的 `mean_pp`），所以必须有测试钉死。
"""

from __future__ import annotations

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from run_phase_o_fee_convention import (  # noqa: E402
    COMM_RATE, MIN_COMM_YUAN, STAMP_RATE, TRANSFER_RATE, cost_base,
    cost_conservative, cost_stress, judge,
)


# ==================== 1 税费不对称
def test_base_cost_is_asymmetric_stamp_only_on_sell():
    """印花税只收卖出方 → 成本 = 2×to×(佣金+过户费) + to×印花税。

    若错误地按双边对称计，会多收 `to×印花税`。
    """
    to = 0.10
    got = cost_base(to, 1e7, 1000)
    expect = 2 * to * (COMM_RATE + TRANSFER_RATE) * 100 + to * STAMP_RATE * 100
    assert got == pytest.approx(expect, rel=1e-12)
    # 对称错算会多出 to × stamp × 100
    assert got < 2 * to * (COMM_RATE + TRANSFER_RATE + STAMP_RATE) * 100


def test_base_cost_scales_linearly_in_turnover():
    assert cost_base(0.20, 1e7, 1000) == pytest.approx(2 * cost_base(0.10, 1e7, 1000),
                                                       rel=1e-12)


def test_equivalent_symmetric_rate_matches_prereg():
    """预登记 §2 声明 BASE 的等价对称单边费率 = 0.051%。"""
    eq = (2 * (COMM_RATE + TRANSFER_RATE) + STAMP_RATE) / 2
    assert eq == pytest.approx(0.00051, abs=1e-9)
    # 验证：对该等价对称费率，成本应与不对称 BASE 相同
    to = 0.12
    assert cost_stress(to, 1e7, 1000) == pytest.approx(2 * to * 0.003 * 100, rel=1e-12)
    assert 2 * to * eq * 100 == pytest.approx(cost_base(to, 1e7, 1000), rel=1e-9)


# ==================== 2 最低佣金
def test_min_commission_dominates_when_position_is_small():
    """单笔 5,000 元：从价佣金 1.25 元 < 5 元下限 → 实收 5 元。"""
    w, k = 1.0e7, 2000          # 单笔 5,000 元
    to = 0.10
    c_min = cost_conservative(to, w, k)
    # 实收 5 元/笔 → 每月 2×to×k 笔
    expect_comm = 2 * to * k * MIN_COMM_YUAN / w * 100
    expect = expect_comm + 2 * to * TRANSFER_RATE * 100 + to * STAMP_RATE * 100
    assert c_min == pytest.approx(expect, rel=1e-9)
    assert c_min > cost_base(to, w, k), "最低佣金应高于纯从价"


def test_min_commission_effect_shrinks_as_position_grows():
    """单笔变大后，从价佣金超过下限 → 最低佣金不再是主导。"""
    small = cost_conservative(0.10, 1.0e7, 2000) - cost_base(0.10, 1.0e7, 2000)
    big = cost_conservative(0.10, 1.0e7, 50) - cost_base(0.10, 1.0e7, 50)
    assert small > big > 0 or big == pytest.approx(0.0, abs=1e-9)


def test_no_double_charging_in_conservative():
    """CONSERVATIVE = 佣金(含下限) + 过户费 + 印花税，不得把佣金算两遍。"""
    to, w, k = 0.08, 3.0e7, 1900
    c = cost_conservative(to, w, k)
    per = w / k
    eff = max(per * COMM_RATE, MIN_COMM_YUAN)
    assert c == pytest.approx(2 * to * k * eff / w * 100
                              + 2 * to * TRANSFER_RATE * 100
                              + to * STAMP_RATE * 100, rel=1e-9)


# ==================== 3 ⚠️ 单位坑（本项目第二次踩）
def test_ci_is_consistent_with_t_when_input_is_pp():
    """传入 pp 时，CI 半宽必须 ≈ mean/t×1.96；用 `se_nw_pp` 会放大 100 倍。

    这是本项目**第二次**踩同一个坑（第一次是阶段 J 把 `mean_pp` 当 pp 用，
    净值被放大 100 倍）。这里必须用 `se_nw`（入参已是 pp）。
    """
    import run_phase_c as C
    nets_pp = [0.001 * 100] * 120 + [0.002 * 100] * 117     # 已是 pp
    nw = C.newey_west(nets_pp, 3)
    mean_pp = sum(nets_pp) / len(nets_pp)
    se_correct = nw["se_nw"]              # 入参是 pp → se_nw 就是 pp 单位
    half_ok = 1.96 * se_correct
    half_bad = 1.96 * nw["se_nw_pp"]      # 会放大 100 倍
    # 与 t 的一致性检验
    assert abs(half_ok - mean_pp / nw["t_nw"] * 1.96) < 1e-9
    assert half_bad == pytest.approx(half_ok * 100, rel=1e-6)


def test_ci_halfwidth_is_sane_relative_to_mean():
    """CI 宽度不应比均值大几十倍 —— 这是发现单位错误的直观哨兵。"""
    import run_phase_c as C
    nets_pp = [0.001 * 100] * 120 + [0.002 * 100] * 117
    nw = C.newey_west(nets_pp, 3)
    mean_pp = sum(nets_pp) / len(nets_pp)
    assert abs(1.96 * nw["se_nw"]) < abs(mean_pp), "CI 半宽应小于均值（量级哨兵）"


# ==================== 4 判据
def _segs(a=0.13, b=0.12, ta=3.3, tb=2.6):
    return {"2007-2015": {"n": 108, "net_pp": a, "t_nw": ta, "enough": True},
            "2016-2026": {"n": 129, "net_pp": b, "t_nw": tb, "enough": True}}


def test_executable_when_all_three_pass():
    j = judge({"t_nw": 4.04, "mean_pp": 0.136}, _segs(), 0.1315)
    assert j["verdict"] == "EXECUTABLE_UNDER_BASE_FEES"


def test_marginal_when_segments_fail():
    j = judge({"t_nw": 4.04, "mean_pp": 0.136}, _segs(tb=1.2), 0.1315)
    assert j["verdict"] == "MARGINAL_UNDER_BASE_FEES"
    assert j["A_stat"] and j["C_conservative_positive"] and not j["B_stable"]


def test_not_executable_when_conservative_is_negative():
    j = judge({"t_nw": 4.04, "mean_pp": 0.136}, _segs(), -0.01)
    assert j["verdict"] == "NOT_EXECUTABLE_UNDER_ANY_FEE"


def test_not_executable_when_A_fails():
    assert judge({"t_nw": 1.2, "mean_pp": 0.04}, _segs(ta=1.0, tb=0.9),
                 0.03)["verdict"] == "NOT_EXECUTABLE_UNDER_ANY_FEE"


def test_D_disclosure_is_always_required():
    """D 不是通过条件，是**披露义务** —— 必须恒为真。"""
    j = judge({"t_nw": 4.04, "mean_pp": 0.136}, _segs(), 0.1315)
    assert j["D_disclosure_required"] is True


def test_negative_mean_is_detected_in_sign_check():
    """全样本为负但两段 t 很大 → B 应判不过（符号不一致）。"""
    j = judge({"t_nw": -4.0, "mean_pp": -0.13}, _segs(a=0.13, b=0.12), -0.13)
    assert j["B_stable"] is False
