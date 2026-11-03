"""L3z 多重检验校正的单元测试。"""

from __future__ import annotations

import math

import pytest

from deepquant.gates import multiplicity as mp


# --------------------------------------------------------------------------
# t 分布 p 值
# --------------------------------------------------------------------------


def test_t_p_matches_known_values():
    """scipy 不可用，这里的自实现必须用已知值钉住。"""
    assert mp.t_two_sided_p(1.96, 236) == pytest.approx(0.051173, abs=1e-4)
    assert mp.t_two_sided_p(2.0, 236) == pytest.approx(0.046646, abs=1e-4)
    assert mp.t_two_sided_p(3.0, 236) == pytest.approx(0.002989, abs=1e-4)


def test_t_p_converges_to_normal_for_large_df():
    """大样本下 t 应趋近正态：p(|z|>1.96) = 0.05。"""
    assert mp.t_two_sided_p(1.96, 10**6) == pytest.approx(0.05, abs=1e-4)


def test_t_p_zero_and_symmetry():
    assert mp.t_two_sided_p(0.0, 10) == 1.0
    assert mp.t_two_sided_p(-2.5, 236) == pytest.approx(mp.t_two_sided_p(2.5, 236))


def test_t_p_monotone_in_abs_t():
    vals = [mp.t_two_sided_p(t, 236) for t in (0.5, 1.0, 1.5, 2.0, 2.5, 3.0)]
    assert all(a > b for a, b in zip(vals, vals[1:]))


def test_t_p_rejects_bad_df():
    with pytest.raises(mp.MultiplicityError):
        mp.t_two_sided_p(1.0, 0)
    with pytest.raises(mp.MultiplicityError):
        mp.t_two_sided_p(1.0, -5)


def test_t_p_rejects_non_finite():
    with pytest.raises(mp.MultiplicityError):
        mp.t_two_sided_p(float("nan"), 10)


def test_betai_edges():
    assert mp.betai(2.0, 0.5, 0.0) == 0.0
    assert mp.betai(2.0, 0.5, 1.0) == 1.0


def test_p_to_t_inverts():
    for p in (0.10, 0.05, 0.01, 0.001):
        assert mp.t_two_sided_p(mp.p_to_t(p, 236), 236) == pytest.approx(p, abs=1e-6)


def test_p_to_t_rejects_out_of_range():
    for p in (0.0, 1.0, 1.5, -0.1):
        with pytest.raises(mp.MultiplicityError):
            mp.p_to_t(p, 236)


def test_p_to_t_known_value():
    """df=236 的 t_{0.975} 由 Cornish–Fisher 展开约 1.9700（比正态 1.9600 略宽）。"""
    assert mp.p_to_t(0.05, 236) == pytest.approx(1.9700, abs=1e-3)


# --------------------------------------------------------------------------
# 三种校正
# --------------------------------------------------------------------------


def test_bonferroni_basic():
    # m=5, alpha=0.05 → 门槛 0.01
    assert mp.bonferroni([0.001, 0.009, 0.011, 0.5, 1.0]) == [True, True, False, False, False]


def test_bonferroni_larger_m_is_stricter():
    pvals = [0.01] * 3
    assert all(mp.bonferroni(pvals, m=3))
    # 同样三个 p，但声称同时报告了 10 条 → 门槛从 0.0167 降到 0.005
    assert not any(mp.bonferroni(pvals, m=10))


def test_bonferroni_empty():
    assert mp.bonferroni([]) == []


def test_holm_step_down_stops_after_first_failure():
    """step-down 的定义：一旦某步不拒绝，后续全部不拒绝。"""
    # 排序后 p = 0.001, 0.02, 0.03；m=3 → 门槛 0.0167, 0.025, 0.05
    # 第 1 个 0.001 <= 0.0167 通过；第 2 个 0.02 <= 0.025 通过；第 3 个 0.03 <= 0.05 通过
    assert mp.holm([0.001, 0.02, 0.03]) == [True, True, True]
    # 换成 0.04：排序 0.001, 0.04, 0.9 → 第 2 个 0.04 > 0.025 不通过，第 3 个直接不通过
    assert mp.holm([0.001, 0.04, 0.9]) == [True, False, False]
    # 这条才是真正能区分 step-down 的例子：第 3 个 p=0.045 单独看满足自己的门槛 0.05，
    # 但 Holm 在第 2 步就停了，后面一律不拒绝。只测上面那种「后面 p 也很大」的例子，
    # 就算把 step-down 去掉结果也一样——变异测试就是靠这个例子才抓得住。
    assert mp.holm([0.001, 0.04, 0.045]) == [True, False, False]


def test_holm_is_uniformly_at_least_as_liberal_as_bonferroni():
    pvals = [0.001, 0.008, 0.012, 0.02, 0.03, 0.4]
    b = mp.bonferroni(pvals)
    h = mp.holm(pvals)
    assert sum(h) >= sum(b)


def test_bh_step_up():
    """BH：找最大的 k 使 p_(k) <= k/m*q，然后 1..k 全部拒绝。"""
    # m=4, q=0.05 → 门槛 0.0125, 0.025, 0.0375, 0.05
    # p 排序 0.01, 0.02, 0.03, 0.04 → 全部满足
    assert mp.bh([0.01, 0.02, 0.03, 0.04]) == [True, True, True, True]
    # p 排序 0.01, 0.03, 0.04, 0.9 → k=1: 0.01<=0.0125 ✓; k=2: 0.03>0.025 ✗
    # k=3: 0.04<=0.0375? 否; k=4: 0.9>0.05 否 → 只拒绝第 1 个
    out = mp.bh([0.01, 0.03, 0.04, 0.9])
    assert out == [True, False, False, False]


def test_bh_threshold_reports_largest_passing_p():
    assert mp.bh_threshold([0.01, 0.02, 0.9]) == pytest.approx(0.02)


def test_no_correction_releases_anything_when_all_null():
    zeros = [1.0] * 10
    assert not any(mp.bonferroni(zeros))
    assert not any(mp.holm(zeros))
    assert not any(mp.bh(zeros))


def test_all_tiny_p_all_released():
    tiny = [1e-9] * 10
    assert all(mp.bonferroni(tiny))
    assert all(mp.holm(tiny))
    assert all(mp.bh(tiny))


# --------------------------------------------------------------------------
# 参与集合与 m
# --------------------------------------------------------------------------


def _row(key="k1", mean=0.5, t=2.0, n=237, family="A", applicable=True, series=None):
    return {
        "key": key,
        "family": family,
        "series": series or f"{key}_out/abcdef/monthly.csv::d",
        "series_n": n,
        "stat": {"applicable": applicable, "mean": mean, "t": t, "n": n},
        "recomputed": {"mean": mean, "t_iid": t, "t_nw3": t, "n": n, "power": 0.9},
    }


def test_eligible_marks_counts_only_when_stat_not_applicable():
    out = mp.eligible([_row(applicable=False)])
    assert out[0]["_counts_only"] is True


def test_eligible_marks_counts_only_without_t():
    row = _row()
    row["recomputed"] = {"mean": 0.5, "n": 237}
    assert mp.eligible([row])[0]["_counts_only"] is True


def test_count_independent_uses_numeric_fingerprint_not_path():
    """共享同一份数字的条目路径不同，只能按数值去重。"""
    a = _row(key="phasel", mean=0.106057, t=3.1295, series="phasel_out/aaa/m.csv::x")
    b = _row(key="phaser", mean=0.106057, t=3.1295, series="phaser_out/bbb/m.csv::y")
    c = _row(key="other", mean=0.9, t=1.0, series="other_out/ccc/m.csv::z")
    assert mp.count_independent_tests([a, b, c]) == 2


def test_count_independent_falls_back_to_key_without_numbers():
    rows = [_row(key="x"), _row(key="y")]
    for r in rows:
        r["recomputed"] = {}
    assert mp.count_independent_tests(rows) == 2


def test_family_m():
    rows = [_row(key="a", family="A"), _row(key="b", family="A"), _row(key="c", family="B")]
    assert mp.family_m(rows) == {"A": 2, "B": 1}


# --------------------------------------------------------------------------
# 判定：多重性与功效分开
# --------------------------------------------------------------------------


def test_powerless_not_significant_is_neither_not_null():
    """功效不足的「未检出」不得读作「无效应」（R6）。"""
    out = mp.classify_one(
        p=0.4, df=236, power=0.16, raw_significant=False,
        bonf=False, holm_ok=False, bh_ok=False, counts_only=False,
    )
    assert out["verdict"] == "NEITHER"


def test_well_powered_not_significant_is_null():
    out = mp.classify_one(
        p=0.4, df=236, power=0.95, raw_significant=False,
        bonf=False, holm_ok=False, bh_ok=False, counts_only=False,
    )
    assert out["verdict"] == "NULL_CONFIRMED"


def test_counts_only_is_not_applicable():
    out = mp.classify_one(
        p=0.0001, df=236, power=0.99, raw_significant=True,
        bonf=True, holm_ok=True, bh_ok=True, counts_only=True,
    )
    assert out["verdict"] == "NOT_APPLICABLE"


def test_survives_all_requires_all_three():
    out = mp.classify_one(
        p=0.0001, df=236, power=1.0, raw_significant=True,
        bonf=True, holm_ok=True, bh_ok=True, counts_only=False,
    )
    assert out["verdict"] == "SURVIVES_ALL"


def test_downgraded_when_bh_fails():
    out = mp.classify_one(
        p=0.03, df=236, power=0.6, raw_significant=True,
        bonf=False, holm_ok=False, bh_ok=False, counts_only=False,
    )
    assert out["verdict"] == "DOWNGRADED"


# --------------------------------------------------------------------------
# apply_corrections
# --------------------------------------------------------------------------


def test_counts_only_are_not_scored_but_still_count():
    rows = [
        _row(key="a", t=4.0),
        _row(key="b", t=4.0, applicable=False),
    ]
    run = mp.apply_corrections(mp.eligible(rows), m=2)
    assert [i["key"] for i in run["items"]] == ["a"]
    assert run["n_counts_only"] == 1
    assert run["m_used"] == 2


def test_m_larger_than_pvals_is_stricter():
    rows = [_row(key="a", t=2.5, n=237)]  # p ≈ 0.0129
    loose = mp.apply_corrections(mp.eligible(rows), m=1)
    strict = mp.apply_corrections(mp.eligible(rows), m=10)
    assert loose["items"][0]["bonferroni"] is True
    assert strict["items"][0]["bonferroni"] is False


def test_estimator_switch_changes_result():
    row = _row(key="a", n=237)
    row["recomputed"] = {"mean": 0.1, "t_iid": 1.5, "t_nw3": 2.6, "n": 237, "power": 0.7}
    nw = mp.apply_corrections(mp.eligible([row]), m=1, estimator="t_nw3")
    iid = mp.apply_corrections(mp.eligible([row]), m=1, estimator="t_iid")
    assert nw["items"][0]["raw_significant"] is True
    assert iid["items"][0]["raw_significant"] is False


# --------------------------------------------------------------------------
# 汇总
# --------------------------------------------------------------------------


def test_expected_false_positives():
    assert mp.expected_false_positives(24) == pytest.approx(1.2)
    assert mp.expected_false_positives(27, alpha=0.05) == pytest.approx(1.35)


def test_assemble_reports_three_m_and_two_estimators():
    rows = [_row(key=f"k{i}", mean=0.1 * i, t=1.0 + i) for i in range(1, 6)]
    out = mp.assemble(rows)
    assert out["m_all"] == 5
    assert "m_all" in out["runs"] and "m_independent" in out["runs"]
    assert set(out["by_family"]) == {"A"}


def test_assemble_m_independent_equals_distinct_fingerprints():
    dup = _row(key="x", mean=0.5, t=2.0)
    dup2 = _row(key="y", mean=0.5, t=2.0)
    other = _row(key="z", mean=0.9, t=1.0)
    out = mp.assemble([dup, dup2, other])
    assert out["m_all"] == 3
    assert out["m_independent"] == 2
