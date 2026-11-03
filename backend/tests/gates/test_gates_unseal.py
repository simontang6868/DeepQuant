"""L3w 解封复核的单元测试。

重点钉住四件事：
1. 三道同源闸门缺一不可，且容差要能把「同一个数的舍入」与「不同资金档」分开；
2. 网格跨桶必须拒绝解封——否则「换个滞后」就成了挑显著性；
3. A 类与 B 类要能自动分开，报告里不能把两种拒绝写成同一句；
4. 合并时要能说清「解封前几条 QUOTABLE、解封后几条」。
"""

from __future__ import annotations

import pytest

from deepquant.gates import citation as cit
from deepquant.gates import unseal as uns

RULES: dict = {}


def make_row(**over):
    row = {
        "key": "k1",
        "family": "A_cross_spread",
        "unit": "pp",
        "detected": True,
        "series_n": 237,
        "comparison": {"class": "MISMATCH_MEAN", "t_only": False, "detail": ""},
        "stat": {"applicable": True, "mean": 1.94, "t": 6.37, "n": 237},
        "recomputed": {
            "n": 237,
            "mean": 1.94,
            "t_iid": 6.19,
            "t_nw3": 6.37,
            "mde": 0.854,
            "power": 1.0,
        },
        "grade": {"tier": "NOT_GRADED", "reference_tier": "QUOTABLE"},
        "l3t": {"n": 237, "mean": 0.01, "t_nw3": 0.09, "mde": 0.45},
    }
    row.update(over)
    return row


def fake_grid(mdes, powers=None):
    powers = powers or {}
    return {
        lag: {
            "n": 237,
            "mean": 1.94,
            "se_nw": mde / cit.MDE_FACTOR,
            "mde": mde,
            "power": powers.get(lag, 0.9),
            "t_nw3": 1.94 / (mde / cit.MDE_FACTOR),
            "t_iid": 6.19,
        }
        for lag, mde in mdes.items()
    }


# --------------------------------------------------------------------------
# 选条目
# --------------------------------------------------------------------------


def test_select_sealed_skips_t_only():
    rows = [
        make_row(key="a"),
        make_row(key="b", comparison={"class": "MISMATCH_MEAN", "t_only": True}),
        make_row(key="c", comparison={"class": "MATCH", "t_only": False}),
        make_row(key="d", comparison={"class": "MISMATCH_N", "t_only": False}),
    ]
    assert [r["key"] for r in uns.select_sealed(rows)] == ["a", "d"]


def test_select_sealed_skips_not_applicable():
    rows = [make_row(comparison={"class": "NOT_APPLICABLE", "t_only": False})]
    assert uns.select_sealed(rows) == []


# --------------------------------------------------------------------------
# 同源三关
# --------------------------------------------------------------------------


def test_same_source_all_match():
    out = uns.check_same_source(make_row())
    assert out["ok"] is True
    assert out["t_match"] == "t_nw3"
    assert out["reasons"] == []


def test_same_source_prefers_iid_when_matching():
    row = make_row(stat={"applicable": True, "mean": 1.94, "t": 6.19, "n": 237})
    assert uns.check_same_source(row)["t_match"] == "t_iid"


def test_same_source_mean_different_budget():
    row = make_row(stat={"applicable": True, "mean": 1.13, "t": 6.37, "n": 237})
    out = uns.check_same_source(row)
    assert out["ok"] is False
    assert out["mean_match"] is False
    assert any("不同源" in r for r in out["reasons"])


def test_same_source_tolerance_accepts_rounding():
    """同一个数被 summary 截断到 4 位，相对差 1e-5，必须仍算同源。"""
    row = make_row(stat={"applicable": True, "mean": 1.94002, "t": 6.37, "n": 237})
    assert uns.check_same_source(row)["mean_match"] is True


def test_same_source_t_rejects_other_model():
    row = make_row(stat={"applicable": True, "mean": 1.94, "t": -0.21, "n": 237})
    out = uns.check_same_source(row)
    assert out["ok"] is False
    assert out["t_match"] is None
    assert any("不是这条序列均值的 t" in r for r in out["reasons"])


def test_same_source_n_mismatch():
    row = make_row(stat={"applicable": True, "mean": 1.94, "t": 6.37, "n": 129})
    out = uns.check_same_source(row)
    assert out["ok"] is False
    assert out["n_match"] is False


def test_same_source_missing_stat():
    row = make_row(stat={})
    out = uns.check_same_source(row)
    assert out["ok"] is False
    assert len(out["reasons"]) >= 2


# --------------------------------------------------------------------------
# 网格
# --------------------------------------------------------------------------


def test_mde_grid_lag0_is_iid_up_to_dof():
    """lag=0 只剩 g0，与 iid 的差别只有自由度因子 √((n−1)/n)。

    NW 用有偏方差（÷n），``t_stat`` 用样本方差（÷(n−1)），所以两者**不严格相等**。
    n=10 时差 5%，n=237 时差 0.2%——把 lag=0 说成「就是 iid」是错的。
    """
    import math

    vals = [1.0, -0.5, 0.8, 0.2, -0.3, 1.1, 0.4, -0.9, 0.6, 0.3]
    grid = uns.mde_grid(vals, lags=(0, 3))
    base = cit.summarise_series(vals, nw_lag=0)
    ratio = grid[0]["se_nw"] / base["se_iid"]
    assert ratio == pytest.approx(math.sqrt((len(vals) - 1) / len(vals)))
    assert grid[0]["mde"] == pytest.approx(base["mde"])


def test_mde_grid_length():
    vals = [float(i % 7) - 3.0 for i in range(40)]
    assert sorted(uns.mde_grid(vals, lags=(0, 1, 6))) == [0, 1, 6]


def test_tier_grid_changes_with_mde():
    grid = fake_grid({0: 0.80, 3: 0.98, 6: 1.10})
    tiers = uns.tier_grid(grid, effect=1.94, detected=True, rules=RULES)
    assert tiers[0] == "QUOTABLE"  # 2.43
    assert tiers[6] == "QUOTABLE_MARGINAL"  # 1.76


def test_tier_grid_uses_power_when_not_detected():
    grid = fake_grid({0: 0.5}, powers={0: 0.05})
    tiers = uns.tier_grid(grid, effect=0.02, detected=False, rules=RULES)
    assert tiers[0] == "UNDERPOWERED"


# --------------------------------------------------------------------------
# 稳定性
# --------------------------------------------------------------------------


def test_stability_same_tier():
    out = uns.stability({0: "QUOTABLE", 3: "QUOTABLE", 6: "QUOTABLE"})
    assert out["stable"] is True
    assert out["primary_tier"] == "QUOTABLE"


def test_stability_crossed_bucket():
    out = uns.stability({0: "QUOTABLE", 3: "QUOTABLE_MARGINAL", 6: "QUOTABLE"})
    assert out["stable"] is False
    assert out["distinct"] == ["QUOTABLE", "QUOTABLE_MARGINAL"]


# --------------------------------------------------------------------------
# 决策
# --------------------------------------------------------------------------


def test_decide_unseal_when_same_source_and_stable():
    d = uns.decide(make_row(), fake_grid({0: 0.87, 3: 0.854, 6: 0.90}), rules=RULES)
    assert d["status"] == "UNSEALED"
    assert d["class"] == "A_same_source"
    assert d["tier"] == "QUOTABLE"
    assert d["ratio_primary"] == pytest.approx(1.94 / 0.854)


def test_decide_stay_sealed_when_unstable():
    d = uns.decide(make_row(), fake_grid({0: 0.80, 3: 0.854, 6: 1.10}), rules=RULES)
    assert d["status"] == "STAY_SEALED"
    assert d["class"] == "A_but_unstable"
    assert d["tier"] == "NOT_GRADED"
    assert any("跨桶" in r for r in d["reasons"])


def test_decide_classifies_budget_mismatch():
    row = make_row(stat={"applicable": True, "mean": 1.13, "t": 6.37, "n": 237})
    d = uns.decide(row, fake_grid({3: 0.854}), rules=RULES)
    assert d["status"] == "STAY_SEALED"
    assert d["class"] == "B_different_budget"


def test_decide_classifies_regression_coef():
    row = make_row(stat={"applicable": True, "mean": 1.94, "t": -0.21, "n": 129})
    d = uns.decide(row, fake_grid({3: 0.854}), rules=RULES)
    assert d["status"] == "STAY_SEALED"
    assert d["class"] == "B_regression_coef"


def test_decide_never_rescues_on_large_ratio():
    """ratio 再大，只要不同源就不解封——不允许「方向一致所以放行」。"""
    row = make_row(
        stat={"applicable": True, "mean": 1.13, "t": 6.37, "n": 237},
        recomputed={"n": 237, "mean": 1.94, "t_iid": 6.19, "t_nw3": 6.37, "mde": 0.10, "power": 1.0},
    )
    d = uns.decide(row, fake_grid({3: 0.10}), rules=RULES)
    assert d["status"] == "STAY_SEALED"


# --------------------------------------------------------------------------
# 合并
# --------------------------------------------------------------------------


def test_merge_counts_and_changes():
    rows = [
        make_row(key="a", grade={"tier": "NOT_GRADED"}),
        make_row(key="b", grade={"tier": "QUOTABLE_MARGINAL"}),
        make_row(key="c", grade={"tier": "NOT_GRADED"}),
    ]
    decisions = [
        {"key": "a", "status": "UNSEALED", "tier": "QUOTABLE"},
        {"key": "c", "status": "STAY_SEALED", "tier": "NOT_GRADED"},
    ]
    out = uns.merge_project_tiers(rows, decisions)
    assert out["counts_before"] == {"NOT_GRADED": 2, "QUOTABLE_MARGINAL": 1}
    assert out["counts_after"] == {"NOT_GRADED": 1, "QUOTABLE": 1, "QUOTABLE_MARGINAL": 1}
    assert out["n_changed"] == 1
    assert out["quotable_before"] == 0
    assert out["quotable_after"] == 1


def test_merge_ignores_sealed_decision():
    rows = [make_row(key="a", grade={"tier": "NOT_GRADED"})]
    decisions = [{"key": "a", "status": "STAY_SEALED", "tier": "QUOTABLE"}]
    out = uns.merge_project_tiers(rows, decisions)
    assert out["n_changed"] == 0
    assert out["quotable_after"] == 0


def test_select_sealed_t_only_mode():
    rows = [
        make_row(key="a", comparison={"class": "MISMATCH_MEAN", "t_only": True}),
        make_row(key="b", comparison={"class": "MISMATCH_MEAN", "t_only": False}),
        make_row(key="c", comparison={"class": "MATCH", "t_only": False}),
    ]
    assert [r["key"] for r in uns.select_sealed(rows, mode="t_only")] == ["a"]
    assert [r["key"] for r in uns.select_sealed(rows, mode="mismatch")] == ["b"]


def test_select_sealed_unknown_mode():
    with pytest.raises(uns.UnsealError):
        uns.select_sealed([make_row()], mode="whatever")


def test_decide_classifies_missing_stat():
    """summary 连 n 都没给、t 又对不上任何口径 —— 不是回归，是没有可核对的分母。"""
    row = make_row(stat={"applicable": True, "mean": 1.94, "t": 1.35, "n": None})
    d = uns.decide(row, fake_grid({3: 0.854}), rules=RULES)
    assert d["status"] == "STAY_SEALED"
    assert d["class"] == "B_missing_stat"


def test_same_source_reports_stat_n():
    assert uns.check_same_source(make_row())["stat_n"] == 237
    assert uns.check_same_source(make_row(stat={}))["stat_n"] is None
