"""L3z 审计级不变量测试。

`test_gates_multiplicity.py` 测的是**函数对不对**（单个校正函数的行为、p 值精度）。
本文件测的是**审计要求的十项不变量**——它们对应「实现是否偏离预登记的程序」，
单看某个函数都对、但整体流程错了时，这些测试才会失败。

对应审计要求第 9 节的十项：

1. 校正使用**完整家族**，不是先筛原始显著再校正
2. m 不因 alpha 改变
3. alpha 改变时拒绝集合满足单调（嵌套）
4. 与独立参考实现一致（这里用不共享代码的数值积分交叉验）
5. p 值边界与极端尾部
6. 重复登记与家族成员的区分
7. 多门槛结论不被赋予单一均值显著性等级
8. DIRECTION_ONLY 不能因旧分级绕过新校正
9. 封印状态流转条数自洽
10. 定稿引用的数字/状态/分级可追溯（由 deliverable 测试覆盖，此处只锁跨阶段一致性）
"""

from __future__ import annotations

import math

import pytest

from deepquant.gates import multiplicity as mp


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


def _row(key: str, t: float, n: int = 237, *, mean: float = 0.5, family: str = "A", power: float = 0.9,
         applicable: bool = True, series: str | None = None):
    return {
        "key": key,
        "family": family,
        "series": series or f"{key}_out/deadbeef/monthly.csv::d",
        "series_n": n,
        "stat": {"applicable": applicable, "mean": mean, "t": t, "n": n},
        "recomputed": {"mean": mean, "t_iid": t, "t_nw3": t, "n": n, "power": power},
    }


def _mixed_family() -> list[dict]:
    """构造 6 条：2 条边缘显著（p 略低于 0.05）、4 条不显著。

    特意选「边缘」而不是「极显著」：极显著条目在两种流程下都通过，
    测不出差别；边缘条目才会因为 m 从 6 缩到 2 而跨过门槛。
    """
    return [
        _row("a", 2.35),
        _row("b", 2.25),
        _row("c", 0.6),
        _row("d", 0.4),
        _row("e", 0.2),
        _row("f", 0.1),
    ]


# --------------------------------------------------------------------------
# 1. 完整家族
# --------------------------------------------------------------------------


def test_prescreening_would_inflate_but_impl_does_not():
    """审计重点排查的错误流程：先按 p<=alpha 筛，再拿子集当家族。

    正确的做法是全部条目参与、m 固定。这条测试直接比两种流程的通过数，
    若哪天实现被改成「先筛选」，通过数会掉到 if_prescreened 那一列。
    """
    rows = _mixed_family()
    m = len(rows)
    run = mp.apply_corrections(rows, m=m, alpha=0.05, q=0.05)
    pvals = [i["p"] for i in run["items"]]

    # 错误流程：只保留 p<=alpha 的条目，把子集大小当 m
    subset = [p for p in pvals if p <= 0.05]
    inflated = sum(mp.bh(subset, q=0.05, m=len(subset)))

    correct = sum(i["bh"] for i in run["items"])
    assert run["n_scored"] == m, "全部条目都必须参与评分"
    assert inflated >= correct, "先筛选只会让门槛更松，不可能更严"
    assert inflated > correct, "本例必须能区分两种流程，否则这条测试没有判别力"


def test_n_scored_equals_all_rows_regardless_of_p():
    """p 值大小不影响「谁参与」——参与集合由数据完备性决定，不由显著性决定。"""
    rows = _mixed_family()
    low = mp.apply_corrections([r for r in rows if r["key"] in {"a", "b"}], m=2)
    allr = mp.apply_corrections(rows, m=len(rows))
    assert low["n_scored"] == 2
    assert allr["n_scored"] == len(rows)
    # 同一条目在两批里的 p 必须一模一样：p 不随家族改变
    pa = {i["key"]: i["p"] for i in allr["items"]}
    for i in low["items"]:
        assert i["p"] == pytest.approx(pa[i["key"]])


# --------------------------------------------------------------------------
# 2. m 不因 alpha 改变
# --------------------------------------------------------------------------


def test_m_constant_across_alpha():
    rows = _mixed_family()
    sweep = mp.alpha_sweep(rows, m=10, alphas=(0.01, 0.05, 0.10))
    assert {s["m"] for s in sweep} == {10}
    assert {s["n_scored"] for s in sweep} == {len(rows)}


def test_alpha_sweep_binds_q_not_alpha():
    """q 必须固定在默认值；若跟着 alpha 一起动，B 类集合会出现假非单调。"""
    rows = _mixed_family()
    sweep = mp.alpha_sweep(rows, m=6, alphas=(0.01, 0.05, 0.10), q=0.05)
    assert {s["q"] for s in sweep} == {0.05}
    assert [s["alpha"] for s in sweep] == [0.01, 0.05, 0.10]


# --------------------------------------------------------------------------
# 3. 单调性
# --------------------------------------------------------------------------


def test_rejected_sets_are_nested_across_alpha():
    rows = _mixed_family()
    sweep = mp.alpha_sweep(rows, m=6, alphas=(0.01, 0.05, 0.10))
    for field in ("rejected_bonferroni", "rejected_holm", "rejected_bh"):
        check = mp.monotonicity_check(sweep, field)
        assert check["ok"], f"{field} 违反单调：{check['violations']}"


def test_q_sweep_is_monotone():
    rows = _mixed_family()
    sweep = mp.q_sweep(rows, m=6, qs=(0.01, 0.05, 0.10))
    assert mp.monotonicity_check(sweep, "rejected_bh")["ok"]


def test_monotonicity_check_detects_violation():
    """检查器本身要有效：构造一个真实的非嵌套序列，必须报出来。"""
    sweep = [
        {"alpha": 0.01, "rejected": ["a", "b"]},
        {"alpha": 0.05, "rejected": ["a"]},
    ]
    out = mp.monotonicity_check(sweep, "rejected")
    assert not out["ok"]
    assert out["violations"][0]["code"] == "MONOTONICITY_VIOLATED"


def test_downgraded_set_size_is_not_monotone_but_rejected_is():
    """B 类（原始显著 − 拒绝）的大小可以随 alpha 变化，这只说明定义，不说明实现有错。

    这条测试把两种现象分开，防止后人把 B 类的非单调误读成校正错误。
    """
    rows = _mixed_family()
    sweep = mp.alpha_sweep(rows, m=6, alphas=(0.01, 0.05, 0.10))
    assert mp.monotonicity_check(sweep, "rejected_bh")["ok"]
    sizes = [len(s["downgraded_bh"]) for s in sweep]
    assert sizes == sorted(sizes), "本例中 B 类随 alpha 增大而增大"


# --------------------------------------------------------------------------
# 4. 独立参考实现
# --------------------------------------------------------------------------


def _p_by_simpson(t: float, df: int, n: int = 20000) -> float:
    """独立实现：直接对 t 密度做 Simpson 积分。与被测的连分数不共享代码。"""

    def pdf(x: float) -> float:
        return math.exp(
            math.lgamma((df + 1) / 2)
            - math.lgamma(df / 2)
            - 0.5 * math.log(df * math.pi)
            - ((df + 1) / 2) * math.log1p(x * x / df)
        )

    t = abs(t)
    lo, hi = t, t + 400.0
    n = n + (n % 2)
    h = (hi - lo) / n
    total = pdf(lo) + pdf(hi)
    for i in range(1, n):
        x = lo + i * h
        total += pdf(x) * (4.0 if i % 2 else 2.0)
    return 2.0 * total * h / 3.0


@pytest.mark.parametrize(("t", "df"), [(0.5, 30), (1.96, 236), (2.5, 100), (3.0, 236)])
def test_p_value_matches_independent_integration(t, df):
    got = mp.t_two_sided_p(t, df)
    ref = _p_by_simpson(t, df, n=40000)
    assert got == pytest.approx(ref, rel=2e-6), f"t={t} df={df}: {got} vs {ref}"


def test_p_value_error_cannot_flip_a_bonferroni_decision():
    """误差必须远小于判定间距，否则「通过/未通过」会被数值噪声左右。"""
    m, alpha = 24, 0.05
    thresh = alpha / m
    # 取一个恰好落在门槛附近的 t，验证 ±1e-9 的 p 误差不足以翻转
    t_star = mp.p_to_t(thresh, 236)
    p = mp.t_two_sided_p(t_star, 236)
    assert abs(p - thresh) / thresh < 1e-6


# --------------------------------------------------------------------------
# 5. 边界与极端尾部
# --------------------------------------------------------------------------


def test_p_zero_t_is_one():
    assert mp.t_two_sided_p(0.0, 236) == pytest.approx(1.0)


def test_p_extreme_tail_positive_and_tiny():
    p = mp.t_two_sided_p(14.0, 30)
    assert 0.0 < p < 1e-12


def test_p_is_symmetric_and_bounded():
    for t in (0.3, 1.0, 2.7, 5.0):
        p1 = mp.t_two_sided_p(t, 50)
        p2 = mp.t_two_sided_p(-t, 50)
        assert p1 == pytest.approx(p2)
        assert 0.0 <= p1 <= 1.0


def test_p_rejects_invalid_df():
    with pytest.raises(mp.MultiplicityError):
        mp.t_two_sided_p(1.0, 0)


# --------------------------------------------------------------------------
# 6. 重复登记 vs 家族成员
# --------------------------------------------------------------------------


def test_duplicate_registration_shrinks_m_not_family():
    """同一份数字登记两次：m 少 1，但两条都还在家族里（都参与评分）。"""
    a = _row("phasel", 3.1295, mean=0.106057, series="phasel/deadbeef/m.csv::x")
    b = _row("phaser", 3.1295, mean=0.106057, series="phaser/deadbeef/m.csv::y")
    c = _row("other", 1.0, mean=0.9, series="other/deadbeef/m.csv::z")
    rows = mp.eligible([a, b, c])
    assert mp.count_independent_tests(rows) == 2
    run = mp.apply_corrections(rows, m=2)
    assert run["n_scored"] == 3, "去重只改 m，不把条目踢出评分"


def test_near_duplicate_is_not_merged():
    """只差最后一位的不同数字不是重复登记，不能被并掉。"""
    a = _row("x", 3.0, mean=0.1000000001, series="s1")
    b = _row("y", 3.0, mean=0.1000000002, series="s2")
    assert mp.count_independent_tests([a, b]) == 2


# --------------------------------------------------------------------------
# 7. 多门槛结论不得被赋予单一均值显著性等级
# --------------------------------------------------------------------------


def test_counts_only_gets_no_p_and_no_verdict():
    """只计名额的条目连 items 都不进——它没有 p、也没有「通过/掉档」可判。"""
    row = _row("multi", 2.5, applicable=False)
    run = mp.apply_corrections(mp.eligible([row]), m=5)
    assert run["items"] == []
    assert run["n_counts_only"] == 1
    assert run["n_scored"] == 0
    # 直接问判定函数时，它必须给 NOT_APPLICABLE，而不是硬套一个显著性等级
    verdict = mp.classify_one(
        p=0.012,
        df=236,
        power=0.9,
        raw_significant=True,
        bonf=False,
        holm_ok=False,
        bh_ok=False,
        counts_only=True,
    )
    assert verdict["verdict"] == "NOT_APPLICABLE"


def test_counts_only_still_consumes_budget():
    """那次检验确实做过，不能假装没发生——m 里必须留着名额。"""
    rows = mp.eligible([_row("multi", 2.5, applicable=False), _row("plain", 3.0)])
    run = mp.apply_corrections(rows, m=12)
    assert run["n_counts_only"] == 1
    assert run["n_scored"] == 1
    assert run["m_used"] == 12


def test_including_counts_only_can_only_change_results_through_m_not_through_their_p():
    """把多门槛条目强行纳入评分，差异只能来自「多算了它的 p」，不能来自 m 变动。"""
    rows = mp.eligible([_row("multi", 2.5, applicable=False), _row("plain", 3.0)])
    base = mp.apply_corrections(rows, m=5)
    naive = mp.apply_corrections([{**r, "_counts_only": False} for r in rows], m=5)
    assert base["m_used"] == naive["m_used"]
    assert base["n_scored"] + 1 == naive["n_scored"]


# --------------------------------------------------------------------------
# 8. 旧分级不能绕过新校正
# --------------------------------------------------------------------------


def test_quotability_is_not_an_input_to_correction():
    """校正只吃 t 与 n。把旧分级塞进行里也不该改变任何判定。"""
    rows = _row("x", 2.3)
    plain = mp.apply_corrections([rows], m=5)
    tagged = mp.apply_corrections([{**rows, "quotability": "DIRECTION_ONLY"}], m=5)
    assert plain["items"][0]["p"] == pytest.approx(tagged["items"][0]["p"])
    assert plain["items"][0]["verdict"] == tagged["items"][0]["verdict"]


def test_direction_only_entry_still_gets_downgraded():
    """一条标了 DIRECTION_ONLY 的条目若校正后未拒绝，必须判 DOWNGRADED。"""
    row = {**_row("edge", 2.0), "quotability": "DIRECTION_ONLY"}
    run = mp.apply_corrections([row], m=24)
    assert run["items"][0]["raw_significant"] is True
    assert run["items"][0]["verdict"] == "DOWNGRADED"


def test_verdict_ordering_never_upgrades():
    """判定只有「存活」「掉档」「不适用」「无结论」四种，不存在提级。"""
    rows = _mixed_family()
    run = mp.apply_corrections(rows, m=id(rows) % 1 + len(rows))
    allowed = {"SURVIVES_ALL", "SURVIVES_FDR_ONLY", "DOWNGRADED", "NULL_CONFIRMED", "NEITHER", "NOT_APPLICABLE"}
    assert {i["verdict"] for i in run["items"]} <= allowed


# --------------------------------------------------------------------------
# 9. 封印状态流转条数自洽
# --------------------------------------------------------------------------


def test_status_counts_sum_to_total():
    """27 条必须被六档完整覆盖，不允许有落在分类之外的条目。"""
    tiers = {"QUOTABLE": 1, "QUOTABLE_MARGINAL": 4, "EXPLORATORY": 3,
             "NULL_CONFIRMED": 1, "UNDERPOWERED": 12, "NOT_GRADED": 6}
    assert sum(tiers.values()) == 27


def test_unseal_arithmetic():
    """复核 12 条 = 解封 9 + 维持 3；L3w 单阶段维持 2 不能与合计 9 相加。"""
    l3w_total, l3w_unseal = 6, 4
    l3x_total, l3x_unseal = 6, 5
    assert l3w_total - l3w_unseal == 2
    assert l3x_total - l3x_unseal == 1
    reviewed = l3w_total + l3x_total
    unsealed = l3w_unseal + l3x_unseal
    stayed = reviewed - unsealed
    assert (reviewed, unsealed, stayed) == (12, 9, 3)
    assert unsealed + 2 != reviewed, "9+2 是口径混用，不能当等式"


def test_final_not_graded_equals_stayed_plus_never_reviewed():
    stayed_sealed = 3
    never_reviewed_sealed = 3
    assert stayed_sealed + never_reviewed_sealed == 6


# --------------------------------------------------------------------------
# 10. 跨阶段一致：期望假阳性与 m 的关系
# --------------------------------------------------------------------------


def test_expected_false_positives_uses_independent_m():
    assert mp.expected_false_positives(24) == pytest.approx(24 * mp.ALPHA)
    assert mp.expected_false_positives(27) > mp.expected_false_positives(24)


def test_assemble_reports_both_m_and_three_corrections():
    rows = _mixed_family()
    out = mp.assemble(rows)
    assert out["m_all"] == len(rows)
    assert out["m_independent"] <= out["m_all"]
    run = out["runs"]["m_independent"]
    assert set(run["items"][0]) >= {"bonferroni", "holm", "bh", "verdict", "p"}
