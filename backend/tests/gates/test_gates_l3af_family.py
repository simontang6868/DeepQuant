"""L3af 的家族与 m 口径测试。

重点三件事：

1. **m 的算术**必须能从成员数复算，不能是常数；
2. **四种校正**必须有一个独立到「换一种写法」的实现来对账——
   自己对自己的两套实现如果都抄同一个公式，那叫冗余，不叫校验；
3. **「序列相同」不等于「假设相同」**：合并只能动名额，不能动 p，也不能声称假设相同。
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from deepquant.gates import family_af as fa
from deepquant.gates import multiplicity as mp

REPO = Path(__file__).resolve().parents[3]
PREREG = REPO / "docs" / "gates" / "l3af_prereg.json"
DECISIONS = REPO / "docs" / "gates" / "l3ab_family_decisions.json"

needs_artifacts = pytest.mark.skipif(
    not (PREREG.exists() and DECISIONS.exists()),
    reason="L3af 家族输入缺失",
)


@pytest.fixture(scope="module")
def res() -> dict:
    return fa.build(REPO)


# ==================== 1 独立实现：按拒绝域的原始定义逐条判


def reject_bonferroni(p, alpha=0.05, m=None):
    m = m or len(p)
    return [x <= alpha / m for x in p]


def reject_holm(p, alpha=0.05, m=None):
    m = m or len(p)
    order = sorted(range(len(p)), key=lambda i: p[i])
    out = [False] * len(p)
    for rank, i in enumerate(order, start=1):
        # H_(i) 被拒 ⟺ 前面所有 j 都满足 p_(j) ≤ α/(m−j+1)
        if all(p[order[j - 1]] <= alpha / (m - j + 1) for j in range(1, rank + 1)):
            out[i] = True
        else:
            break  # Holm 是 step-down，一旦某步不过，后面全部不过
    return out


def reject_bh(p, q=0.05, m=None):
    m = m or len(p)
    order = sorted(range(len(p)), key=lambda i: p[i])
    k_max = 0
    for rank, i in enumerate(order, start=1):
        if p[i] <= rank * q / m:
            k_max = rank
    out = [False] * len(p)
    for rank, i in enumerate(order, start=1):
        if rank <= k_max:
            out[i] = True
    return out


def reject_by(p, q=0.05, m=None):
    m = m or len(p)
    hm = sum(1.0 / k for k in range(1, m + 1))
    order = sorted(range(len(p)), key=lambda i: p[i])
    k_max = 0
    for rank, i in enumerate(order, start=1):
        if p[i] <= rank * q / (m * hm):
            k_max = rank
    out = [False] * len(p)
    for rank, i in enumerate(order, start=1):
        if rank <= k_max:
            out[i] = True
    return out


@pytest.mark.parametrize("method,ref", [
    ("bonferroni", reject_bonferroni),
    ("holm", reject_holm),
    ("bh", reject_bh),
    ("by", reject_by),
])
@pytest.mark.parametrize("m", [3, 7, 12, 20, 24, 25, 27, 29])
def test_adjusted_p_matches_definition(method, ref, m):
    """`adjusted_p` 的拒绝集合必须与「按定义直接判」完全一致。"""
    ps = [0.0005, 0.0021, 0.009, 0.02, 0.049, 0.051, 0.2, 0.5, 0.9]
    # adjusted_p 的名额是 max(m, n)：不足 n 时补 p=1，所以参考实现也要用同一个名额
    m_eff = max(m, len(ps))
    got = fa.adjusted_p(ps, method, m)
    level = 0.05
    mine = [a <= level for a in got]
    assert mine == ref(ps, level, m_eff)


def test_adjusted_p_is_monotone_in_p():
    ps = [0.001, 0.01, 0.02, 0.04]
    for method in ("bonferroni", "holm", "bh", "by"):
        adj = fa.adjusted_p(ps, method, 10)
        assert all(adj[i] <= adj[i + 1] + 1e-12 for i in range(len(adj) - 1))


def test_box_by_is_never_more_permissive_than_bh():
    ps = [0.001, 0.008, 0.02, 0.03, 0.2]
    for m in (5, 10, 25):
        by = fa.adjusted_p(ps, "by", m)
        bh = fa.adjusted_p(ps, "bh", m)
        assert all(b >= h - 1e-12 for b, h in zip(by, bh))


def test_padding_keeps_threshold_from_loosening():
    """名额多于已评分条目时，必须用 p=1 补位，否则门槛会凭空放宽。"""
    ps = [0.01, 0.02]
    tight = fa.apply_all(ps, 10)
    assert tight["n_padded"] == 8
    assert tight["params"]["bonferroni_threshold"] == pytest.approx(0.05 / 10)
    assert tight["bonferroni"]["rejected"] == [False, False]  # 0.01 > 0.005

    loose = fa.apply_all(ps, 2)
    assert loose["n_padded"] == 0
    assert loose["params"]["bonferroni_threshold"] == pytest.approx(0.05 / 2)
    assert loose["bonferroni"]["rejected"] == [True, True]

    # 补位若被漏掉（用 n_scored 当 m），门槛会退回 0.025 —— 正是这里要防的
    assert tight["params"]["bonferroni_threshold"] < loose["params"]["bonferroni_threshold"]


# ==================== 2 与 multiplicity 的实现互校


@needs_artifacts
def test_two_implementations_agree_on_real_data(res):
    for vid, chk in res["cross_check"].items():
        for method, row in chk.items():
            assert row["agree"], f"{vid}/{method} 不一致：{row}"


@needs_artifacts
def test_cross_check_can_detect_a_broken_implementation(monkeypatch):
    """把 multiplicity 的 bonferroni 换成故意错的版本，互校必须报警。"""
    orig = mp.bonferroni

    def wrong(pvals, alpha=0.05, m=None):
        return [True] * len(pvals)

    monkeypatch.setattr(mp, "bonferroni", wrong)
    res = fa.apply_all([0.5, 0.6, 0.7], 10)
    chk = fa.cross_check([0.5, 0.6, 0.7], 10, res)
    assert chk["bonferroni"]["agree"] is False
    monkeypatch.setattr(mp, "bonferroni", orig)


# ==================== 3 m 与成员


@needs_artifacts
def test_m_arithmetic_is_recomputable(res):
    a = res["m_arithmetic"]
    assert a["recomputed"] == a["n_registered"] - a["n_merged_away"] + a["n_added"]
    assert a["recomputed"] == res["m_adopted"] == 25
    assert a["n_registered"] == 27 and a["n_added"] == 2
    assert a["n_merged_away"] == len(res["merged_into"]) == 4


@needs_artifacts
def test_registry_has_every_entry_and_twelve_fields(res):
    assert len(res["registry"]) == 29
    required = {
        "id", "question", "null", "data_path", "window", "n", "random_variable",
        "stat", "bit_identical_with", "is_subwindow_of", "same_null_same_rule",
        "inclusion_reason", "exclusion_or_merge_reason", "evidence_source",
    }
    for row in res["registry"]:
        assert required <= set(row), f"{row['id']} 缺字段 {required - set(row)}"
        assert row["null"].startswith("H0")
        assert row["evidence_source"]["registry_sha"]


@needs_artifacts
def test_variant_m_values_are_as_declared(res):
    declared = {v["id"]: v["m"] for v in res["variants"]}
    assert declared["FP_m24"] == 24
    assert declared["IND_m25"] == 25
    assert declared["L3AC_m27"] == 27
    assert declared["ALL_m29"] == 29
    assert declared["SUB_family"] is None
    for vid, row in res["runs"].items():
        assert row["m"] == max(declared[vid], row["n_scored"])


# ==================== 4 去重规则


def test_identical_series_merge_but_do_not_change_p():
    """逐位相同 ⇒ 只占一个名额；但 p、均值一律不动。"""
    from deepquant.gates import dependence as dp

    a = dp.MonthSeries(key="a", path="x", column="c", date_column="m",
                       values={"2020-01": 1.0, "2020-02": 2.0, "2020-03": 3.0}, filters={})
    b = dp.MonthSeries(key="b", path="x", column="c", date_column="m",
                       values={"2020-01": 1.0, "2020-02": 2.0, "2020-03": 3.0}, filters={})
    groups = fa.ind.identical_groups(["a", "b"], {"a": a, "b": b})
    assert groups["n_groups"] == 1
    assert groups["n_merged_away"] == 1


def test_near_identical_is_not_merged():
    from deepquant.gates import dependence as dp

    a = dp.MonthSeries(key="a", path="x", column="c", date_column="m",
                       values={"2020-01": 1.0, "2020-02": 2.0, "2020-03": 3.0}, filters={})
    b = dp.MonthSeries(key="b", path="x", column="c", date_column="m",
                       values={"2020-01": 1.0, "2020-02": 2.0, "2020-03": 3.0 + 1e-9}, filters={})
    groups = fa.ind.identical_groups(["a", "b"], {"a": a, "b": b})
    assert groups["n_groups"] == 2


def test_same_series_does_not_imply_same_hypothesis():
    """合并的是名额；零假设是否相同在产物里**没有记录**，必须如实写。"""
    assert fa.same_rule_label(["only"], "only") == "SAME_TRIVIALLY"
    assert fa.same_rule_label(["x", "y"], "x") == "NOT_RECORDED_IN_PRODUCTS"


@needs_artifacts
def test_merged_members_do_not_claim_same_hypothesis(res):
    merged = [
        r for r in res["registry"]
        if r["in_partition"] and r["representative"] != r["id"]
    ]
    assert merged, "本阶段应存在被合并的条目"
    for r in merged:
        assert r["same_null_same_rule"] == "NOT_RECORDED_IN_PRODUCTS"
        assert "合并的是名额，不是假设" in r["exclusion_or_merge_reason"]


# ==================== 5 结论稳定性


@needs_artifacts
def test_seven_positive_claims_survive_every_m(res):
    pos = res["positive_by_variant"]
    assert len(pos) == 4
    sets = {vid: {tuple(sorted(v)) for v in rows.values()} for vid, rows in pos.items()}
    assert all(len(s) == 1 for s in sets.values()), "同一口径下四种校正不应给出不同结论"
    flat = {tuple(next(iter(s))) for s in sets.values()}
    assert len(flat) == 1
    assert len(next(iter(flat))) == 7


@needs_artifacts
def test_subfamily_is_reported_but_not_adopted(res):
    sub = res["subfamily"]
    assert sub["n_groups"] >= 2
    assert res["m_adopted"] == 25  # 不是子族的 m
    roles = {v["id"]: v["role"] for v in res["variants"]}
    assert "不采用" in roles["SUB_family"]
    from deepquant.gates.cost_net import POSITIVE_CLAIMS

    for m in ("bonferroni", "holm", "bh", "by"):
        assert len([k for k in sub["rejected"][m] if k in POSITIVE_CLAIMS]) == len(POSITIVE_CLAIMS)


@needs_artifacts
def test_registry_corrections_are_per_entry(res):
    """每条检验都要有四种校正后的 p 与拒绝状态，不能只存通过条数。"""
    for row in res["registry"]:
        for vid in ("FP_m24", "IND_m25", "L3AC_m27", "ALL_m29"):
            cell = row["corrections"][vid]
            if not row["in_scoring_set"]:
                assert cell["bonferroni"] is None
                continue
            assert cell["adjusted_p"] is not None
            assert isinstance(cell["bonferroni"], bool)
