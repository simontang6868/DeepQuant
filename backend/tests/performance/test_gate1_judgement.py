"""Gate 1 判定规则的测试。

重点不是覆盖行数，而是三件事：

1. **t 分布是真 t 分布，不是正态近似。** 用 ν=1（柯西）与 ν=2 两个有解析解的特例
   钉死：ν=1, t=1 时双侧 p 精确等于 0.5；ν=2, t=1 时精确等于 1-1/√3。
   正态近似在这两点分别给出 0.317 和 0.317，差一个量级，测不过就说明实现错了。
2. **三档判定逐条对得上预登记 10.2**，包括阈值边界（`>=` 而非 `>`）。
3. **协议没写清的地方已被显式固定**：失败 > 证据不足 > 成功。
"""

from __future__ import annotations

import math

import pytest

from deepquant.performance.gate1_judgement import (
    FAILURE_MAX_POSITIVE,
    FAILURE_T_STAT,
    N_SENSITIVITY,
    SUCCESS_MEAN_D,
    SUCCESS_MIN_POSITIVE,
    SUCCESS_T_STAT,
    VERDICT_FAILURE,
    VERDICT_INSUFFICIENT,
    VERDICT_SUCCESS,
    Judgement,
    PairedStats,
    SensitivityOutcome,
    attach_bh,
    benjamini_hochberg,
    judge,
    paired_stats,
    student_t_two_sided_p,
)


# --------------------------------------------------------------------------- #
# t 分布
# --------------------------------------------------------------------------- #


def test_t_distribution_matches_cauchy_closed_form():
    """ν=1 时 t 分布即柯西分布：P(|T| > 1) = 0.5（精确值）。"""
    assert student_t_two_sided_p(1.0, df=1) == pytest.approx(0.5, abs=1e-12)


def test_t_distribution_matches_df2_closed_form():
    """ν=2 时 P(|T| > t) = 1 - t/√(2+t²)，t=1 时 = 1-1/√3。"""
    expected = 1.0 - 1.0 / math.sqrt(3.0)
    assert student_t_two_sided_p(1.0, df=2) == pytest.approx(expected, abs=1e-12)


def test_t_distribution_is_fatter_tailed_than_normal():
    """df=47, t=2.0 的 p 应略大于 0.05（临界值 2.0117），且明显大于正态的 0.0455。"""
    p = student_t_two_sided_p(2.0, df=47)
    assert 0.0500 < p < 0.0520
    assert p > 0.0455


def test_t_distribution_is_symmetric_and_bounded():
    assert student_t_two_sided_p(0.0, df=10) == pytest.approx(1.0, abs=1e-12)
    assert student_t_two_sided_p(-2.5, df=10) == pytest.approx(
        student_t_two_sided_p(2.5, df=10), rel=1e-12
    )
    assert student_t_two_sided_p(1e6, df=10) == pytest.approx(0.0, abs=1e-10)


def test_t_distribution_rejects_invalid_df():
    with pytest.raises(ValueError, match="自由度"):
        student_t_two_sided_p(1.0, df=0)


# --------------------------------------------------------------------------- #
# 配对统计
# --------------------------------------------------------------------------- #


def test_paired_stats_basic():
    """手算可验证：d = [0.01, 0.03]，mean=0.02，s=0.014142，t=2.0。"""
    s = paired_stats([0.01, 0.03])
    assert s.n == 2
    assert s.mean_d == pytest.approx(0.02)
    assert s.std_d == pytest.approx(0.02 / math.sqrt(2))
    assert s.t_stat == pytest.approx(2.0)
    assert s.win_ratio == pytest.approx(1.0)


def test_paired_stats_win_ratio_counts_strictly_positive():
    s = paired_stats([0.01, 0.0, -0.01, 0.02])
    assert s.win_ratio == pytest.approx(0.5)


def test_paired_stats_zero_variance_is_infinite_not_faked():
    """全部差额相同 → 标准差 0 → t 无定义。必须返回 inf，不能编一个有限数。"""
    s = paired_stats([0.001] * 5)
    assert s.std_d == 0.0
    assert math.isinf(s.t_stat)
    assert s.p_value == 0.0


def test_paired_stats_rejects_empty_and_nan():
    with pytest.raises(ValueError, match="为空"):
        paired_stats([])
    with pytest.raises(ValueError, match="非有限"):
        paired_stats([0.01, float("nan")])


# --------------------------------------------------------------------------- #
# BH 校正
# --------------------------------------------------------------------------- #


def test_benjamini_hochberg_matches_hand_calculation():
    """p=[0.01, 0.04], m=2 → q=[0.02, 0.04]。

    rank2: 0.04*2/2 = 0.04
    rank1: min(0.04, 0.01*2/1) = 0.02
    """
    assert benjamini_hochberg([0.01, 0.04]) == pytest.approx([0.02, 0.04])


def test_benjamini_hochberg_preserves_input_order():
    """输入乱序时，q 必须跟着原位置走，不能按排序后的顺序返回。"""
    q = benjamini_hochberg([0.05, 0.001])
    assert q[1] < q[0]


def test_benjamini_hochberg_is_monotone_in_p():
    """p 更小不能得到更大的 q——单调性靠「取右侧最小值」保证。"""
    ps = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205,
          0.212, 0.216, 0.222, 0.251]
    qs = benjamini_hochberg(ps)
    for a, b in zip(qs, qs[1:]):
        assert a <= b + 1e-15, f"单调性被破坏：{qs}"


def test_benjamini_hochberg_clips_to_one():
    assert max(benjamini_hochberg([0.9, 0.95, 0.99])) <= 1.0


def test_benjamini_hochberg_rejects_invalid_p():
    with pytest.raises(ValueError, match="p 值"):
        benjamini_hochberg([0.5, 1.5])


def test_benjamini_hochberg_empty():
    assert benjamini_hochberg([]) == []


# --------------------------------------------------------------------------- #
# 三档判定
# --------------------------------------------------------------------------- #


def _stats(mean_d: float, t_stat: float) -> PairedStats:
    """只关心 mean 与 t 的构造器（p 用不到主判据）。"""
    return PairedStats(n=48, mean_d=mean_d, std_d=0.06, t_stat=t_stat,
                       p_value=0.5, win_ratio=0.5)


def test_judge_success_requires_all_three():
    j = judge(_stats(0.004, 2.5), n_positive_sensitivity=9)
    assert j.verdict == VERDICT_SUCCESS
    assert len(j.rule_hits) == 3


def test_judge_fails_when_mean_not_positive():
    j = judge(_stats(-0.001, -0.5), n_positive_sensitivity=10)
    assert j.verdict == VERDICT_FAILURE


def test_judge_fails_when_t_below_one_even_if_mean_positive():
    j = judge(_stats(0.002, 0.8), n_positive_sensitivity=10)
    assert j.verdict == VERDICT_FAILURE
    assert any("t=" in h for h in j.rule_hits)


def test_judge_fails_when_sensitivity_collapses():
    """主实验很漂亮，但 12 组敏感性只有 3 组为正 → 失败（不是成功）。"""
    j = judge(_stats(0.008, 4.0), n_positive_sensitivity=3)
    assert j.verdict == VERDICT_FAILURE


def test_judge_insufficient_when_mean_below_threshold_but_t_decent():
    j = judge(_stats(0.002, 1.5), n_positive_sensitivity=9)
    assert j.verdict == VERDICT_INSUFFICIENT


def test_judge_insufficient_when_sensitivity_in_middle():
    """mean 与 t 都过线，但敏感性只有 6 组为正（5~7 区间）→ 证据不足。"""
    j = judge(_stats(0.005, 2.5), n_positive_sensitivity=6)
    assert j.verdict == VERDICT_INSUFFICIENT


def test_judge_thresholds_are_inclusive():
    """预登记写的是 `>=`：恰好卡在阈值上应判成功，不是证据不足。"""
    j = judge(_stats(SUCCESS_MEAN_D, SUCCESS_T_STAT), n_positive_sensitivity=SUCCESS_MIN_POSITIVE)
    assert j.verdict == VERDICT_SUCCESS


def test_judge_zero_mean_is_failure():
    """`mean(d) <= 0` 判失败——恰好为 0 也算，不能四舍五入成「接近零所以证据不足」。"""
    j = judge(_stats(0.0, 0.0), n_positive_sensitivity=10)
    assert j.verdict == VERDICT_FAILURE


def test_judge_failure_boundary_on_sensitivity_count():
    j = judge(_stats(0.005, 2.5), n_positive_sensitivity=FAILURE_MAX_POSITIVE)
    assert j.verdict == VERDICT_FAILURE
    j = judge(_stats(0.005, 2.5), n_positive_sensitivity=FAILURE_MAX_POSITIVE + 1)
    assert j.verdict == VERDICT_INSUFFICIENT


# --------------------------------------------------------------------------- #
# 协议未写清的优先级（本实现的显式解读）
# --------------------------------------------------------------------------- #


def test_insufficient_flags_downgrade_success():
    """成功条件全满足，但「排除创业板后符号翻转」成立 → 降级为证据不足。"""
    j = judge(_stats(0.005, 3.0), n_positive_sensitivity=10,
              insufficient_flags=("排除创业板后 mean(d) 由正转负",))
    assert j.verdict == VERDICT_INSUFFICIENT
    assert j.insufficient_flags


def test_insufficient_flags_do_not_upgrade_failure():
    """失败就是失败：近似偏差方向是「让策略显得更好」，带偏差仍跑输则证据更强。"""
    j = judge(_stats(-0.002, -1.0), n_positive_sensitivity=2,
              insufficient_flags=("成本敏感性由正转负",))
    assert j.verdict == VERDICT_FAILURE


def test_judge_rejects_out_of_range_sensitivity_count():
    with pytest.raises(ValueError, match="0~12"):
        judge(_stats(0.004, 2.5), n_positive_sensitivity=N_SENSITIVITY + 1)


def test_judgement_serializes_without_losing_verdict():
    j = Judgement(verdict=VERDICT_INSUFFICIENT, n_positive_sensitivity=6,
                  insufficient_flags=("a",))
    d = j.as_dict()
    assert d["verdict"] == VERDICT_INSUFFICIENT
    assert d["label"] == "证据不足"
    assert d["n_positive_sensitivity"] == 6


# --------------------------------------------------------------------------- #
# 敏感性挂 q 值
# --------------------------------------------------------------------------- #


def test_attach_bh_keeps_order_and_adds_q():
    a = SensitivityOutcome("S1", "lookback", "20", _stats(0.01, 3.0))
    b = SensitivityOutcome("S2", "lookback", "120", _stats(-0.01, -3.0))
    out = attach_bh([a, b])
    assert [x.name for x in out] == ["S1", "S2"]
    assert out[0].q_value is not None and out[1].q_value is not None
    assert out[0].is_positive and not out[1].is_positive


def test_attach_bh_empty():
    assert attach_bh([]) == ()


# --------------------------------------------------------------------------- #
# 阈值常量必须与预登记一致
# --------------------------------------------------------------------------- #


def test_thresholds_match_preregistration_text():
    """这些数字在 docs/GATE1_PREREGISTRATION.md 10.2 里逐字写着，改代码前先改文档。"""
    assert SUCCESS_MEAN_D == 0.0030
    assert SUCCESS_T_STAT == 2.0
    assert SUCCESS_MIN_POSITIVE == 8
    assert FAILURE_MAX_POSITIVE == 4
    assert FAILURE_T_STAT == 1.0
    assert N_SENSITIVITY == 12
