"""L3v 结论卡片注册表的单元测试。

重点钉住四件事：
1. 覆盖必须**双向**比：只比交集会让「砍掉一半条目」也通过（C 阶段犯过）；
2. 数字必须能回指重算字段，且符号写在字面里——``abs()`` 吃掉负号是复发坑；
3. 口径标签里的数字（``NW3``/``MAX5``/``m1``）不算统计量，这是首跑 13 处假阳性的根因；
4. 可引用强度只能降不能升，``NOT_GRADED`` 不得被直接引用。
"""

from __future__ import annotations

import json

import pytest

from deepquant.gates import claims as clm


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


def make_row(**over):
    row = {
        "key": "k1",
        "family": "A_cross_spread",
        "unit": "pp",
        "detected": False,
        "series": "x_out/abc/monthly.csv::d",
        "series_n": 24,
        "l3t": {"n": 24, "mean": -0.5, "t_nw3": -1.2, "mde": 0.9},
        "recomputed": {
            "n": 24,
            "mean": -0.5,
            "se_iid": 0.2,
            "se_nw": 0.21,
            "t_iid": -2.5,
            "t_nw3": -1.2,
            "mde": 0.9,
            "power": 0.25,
        },
        "stat": {"applicable": True, "mean": -0.5, "t": -1.2, "n": 24},
        "comparison": {"class": "MATCH", "t_only": False, "detail": ""},
        "grade": {"tier": "UNDERPOWERED", "ratio": None, "reason": "未检出，事后功效 25.00%"},
    }
    row.update(over)
    return row


def make_claim(**over):
    claim = {
        "key": "k1",
        "family": "A_cross_spread",
        "unit": "pp",
        "headline": "均值为 −0.50pp（t=−1.20），MDE 0.90pp。",
        "numbers": [
            {"display": "−0.50", "field": "mean", "decimals": 2},
            {"display": "−1.20", "field": "t_nw3", "decimals": 2},
            {"display": "0.90", "field": "mde", "decimals": 2},
        ],
        "literals": [],
        "window": "full_sample",
        "estimator_for_verdict": "nw3",
        "d_definition": "0.5*long-0.5*short",
        "limits_of_use": ["这是月度横截面差距，不是连续账户收益"],
        "quotability": "UNDERPOWERED",
    }
    claim.update(over)
    return claim


def codes(issues):
    return [i["code"] for i in issues]


@pytest.fixture()
def claims_file(tmp_path):
    def _write(claims, retractions=None):
        path = tmp_path / "claims.json"
        path.write_text(
            json.dumps(
                {"claims": claims, "retractions": retractions or []}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
        return path

    return _write


# --------------------------------------------------------------------------
# 结构校验
# --------------------------------------------------------------------------


def test_load_claims_ok(claims_file):
    payload = clm.load_claims(claims_file([make_claim()]))
    assert payload["claims"][0]["key"] == "k1"


def test_load_claims_missing_field(claims_file):
    claim = make_claim()
    del claim["window"]
    with pytest.raises(clm.ClaimsError, match="缺字段"):
        clm.load_claims(claims_file([claim]))


def test_load_claims_duplicate_key(claims_file):
    with pytest.raises(clm.ClaimsError, match="重复"):
        clm.load_claims(claims_file([make_claim(), make_claim()]))


def test_load_claims_unknown_field(claims_file):
    claim = make_claim(numbers=[{"display": "1", "field": "made_up", "decimals": 0}])
    with pytest.raises(clm.ClaimsError, match="未知字段"):
        clm.load_claims(claims_file([claim]))


def test_load_claims_bad_decimals(claims_file):
    claim = make_claim(numbers=[{"display": "1", "field": "n", "decimals": 9}])
    with pytest.raises(clm.ClaimsError, match="decimals"):
        clm.load_claims(claims_file([claim]))


def test_load_claims_bad_estimator(claims_file):
    with pytest.raises(clm.ClaimsError, match="estimator_for_verdict"):
        clm.load_claims(claims_file([make_claim(estimator_for_verdict="hac")]))


# --------------------------------------------------------------------------
# 覆盖：必须双向比
# --------------------------------------------------------------------------


def test_coverage_missing_detected():
    cov = clm.check_coverage([], [make_row()])
    assert cov["missing"] == ["k1"]
    assert cov["complete"] is False


def test_coverage_orphan_detected():
    cov = clm.check_coverage([make_claim(key="ghost")], [make_row()])
    assert cov["orphan"] == ["ghost"]
    assert cov["complete"] is False


def test_coverage_intersection_only_would_miss():
    """两边各有一条对方没有的，交集相同也不能算通过。"""
    cov = clm.check_coverage(
        [make_claim(key="a"), make_claim(key="b")],
        [make_row(key="a"), make_row(key="c")],
    )
    assert cov["missing"] == ["c"]
    assert cov["orphan"] == ["b"]
    assert cov["complete"] is False


def test_coverage_metadata_mismatch():
    cov = clm.check_coverage([make_claim(family="B_long_only")], [make_row()])
    assert cov["family_mismatch"] and cov["family_mismatch"][0]["key"] == "k1"


# --------------------------------------------------------------------------
# 字段解析
# --------------------------------------------------------------------------


def test_resolve_derived_fields():
    row = make_row()
    assert clm.resolve_field(row, "mean") == pytest.approx(-0.5)
    assert clm.resolve_field(row, "ratio") == pytest.approx(0.5 / 0.9)
    assert clm.resolve_field(row, "mde_over_mean") == pytest.approx(0.9 / 0.5)
    assert clm.resolve_field(row, "stat_n") == pytest.approx(24.0)


def test_resolve_missing_returns_none():
    row = make_row(recomputed={}, stat={})
    assert clm.resolve_field(row, "mean") is None
    assert clm.resolve_field(row, "ratio") is None
    assert clm.resolve_field(row, "mde_over_mean") is None
    assert clm.resolve_field(row, "stat_n") is None


def test_resolve_ratio_zero_mean_is_none():
    row = make_row(recomputed={"n": 24, "mean": 0.0, "mde": 0.5})
    assert clm.resolve_field(row, "mde_over_mean") is None


def test_format_expected_scales():
    assert clm.format_expected(-0.3961, 2) == "0.40"
    assert clm.format_expected(0.477, 0, 100) == "48"
    assert clm.format_expected(-0.0232, 3) == "0.023"


# --------------------------------------------------------------------------
# 数字校验
# --------------------------------------------------------------------------


def test_numbers_pass():
    assert clm.check_numbers(make_claim(), make_row()) == []


def test_numbers_value_drift():
    claim = make_claim(
        headline="均值为 −0.60pp（t=−1.20），MDE 0.90pp。",
        numbers=[
            {"display": "−0.60", "field": "mean", "decimals": 2},
            {"display": "−1.20", "field": "t_nw3", "decimals": 2},
            {"display": "0.90", "field": "mde", "decimals": 2},
        ],
    )
    assert "VALUE_DRIFT" in codes(clm.check_numbers(claim, make_row()))


def test_numbers_sign_must_be_in_display():
    claim = make_claim(
        numbers=[
            {"display": "0.50", "field": "mean", "decimals": 2},
            {"display": "−1.20", "field": "t_nw3", "decimals": 2},
            {"display": "0.90", "field": "mde", "decimals": 2},
        ]
    )
    assert "SIGN_MISMATCH" in codes(clm.check_numbers(claim, make_row()))


def test_numbers_sign_in_text():
    claim = make_claim(
        headline="均值为 +0.50pp（t=−1.20），MDE 0.90pp。",
        numbers=[
            {"display": "−0.50", "field": "mean", "decimals": 2},
            {"display": "−1.20", "field": "t_nw3", "decimals": 2},
            {"display": "0.90", "field": "mde", "decimals": 2},
        ],
    )
    assert "SIGN_IN_TEXT" in codes(clm.check_numbers(claim, make_row()))


def test_numbers_unregistered():
    claim = make_claim(headline="均值为 −0.50pp（t=−1.20），MDE 0.90pp，功效 25%。")
    assert "UNREGISTERED_NUMBER" in codes(clm.check_numbers(claim, make_row()))


def test_numbers_unused():
    claim = make_claim(
        numbers=[
            {"display": "−0.50", "field": "mean", "decimals": 2},
            {"display": "−1.20", "field": "t_nw3", "decimals": 2},
            {"display": "0.90", "field": "mde", "decimals": 2},
            {"display": "24", "field": "n", "decimals": 0},
        ]
    )
    assert "UNUSED_NUMBER" in codes(clm.check_numbers(claim, make_row()))


def test_numbers_literal_whitelist():
    claim = make_claim(
        headline="均值为 −0.50pp（t=−1.20），MDE 0.90pp；口径见 NW3 与 0.5 倍定义。",
        literals=[{"display": "0.5", "reason": "d 的定义系数"}],
    )
    issues = clm.check_numbers(claim, make_row())
    assert "UNREGISTERED_NUMBER" not in codes(issues)


def test_num_token_ignores_labels():
    """``NW3``/``MAX5``/``m1`` 里的数字是口径标签，不是统计量（首跑 13 处假阳性的根因）。"""
    tokens = clm.NUM_TOKEN.findall("t 用 NW3，分组用 MAX5，方案 m1，另有 0.90pp 与 24 期")
    assert tokens == ["0.90", "24"]


def test_num_token_keeps_signed_values():
    assert clm.NUM_TOKEN.findall("d 为 −0.40pp，t=+0.97，n=108") == ["−0.40", "+0.97", "108"]


# --------------------------------------------------------------------------
# 分级：只降不升
# --------------------------------------------------------------------------


def test_tier_same_level_allowed():
    assert clm.check_tier(make_claim(), make_row()) == []


def test_tier_downgrade_allowed():
    row = make_row(grade={"tier": "QUOTABLE_MARGINAL"})
    claim = make_claim(quotability="DIRECTION_ONLY")
    assert clm.check_tier(claim, row) == []


def test_tier_upgrade_rejected():
    row = make_row(grade={"tier": "QUOTABLE_MARGINAL"})
    claim = make_claim(quotability="QUOTABLE")
    assert "TIER_UPGRADE" in codes(clm.check_tier(claim, row))


def test_tier_not_graded_cannot_be_upgraded():
    row = make_row(grade={"tier": "NOT_GRADED"})
    for tier in ("QUOTABLE", "QUOTABLE_MARGINAL", "DIRECTION_ONLY", "NULL_CONFIRMED"):
        assert "TIER_UPGRADE" in codes(clm.check_tier(make_claim(quotability=tier), row))


def test_tier_null_confirmed_cannot_become_quotable():
    row = make_row(detected=False, grade={"tier": "NULL_CONFIRMED"})
    claim = make_claim(quotability="QUOTABLE_MARGINAL")
    assert "TIER_UPGRADE" in codes(clm.check_tier(claim, row))


# --------------------------------------------------------------------------
# 必带限定
# --------------------------------------------------------------------------


def test_limits_missing_window():
    claim = make_claim(window="")
    assert "MISSING_WINDOW" in codes(clm.check_required_limits(claim, make_row()))


def test_limits_unknown_window():
    claim = make_claim(window="whatever")
    assert "UNKNOWN_WINDOW" in codes(clm.check_required_limits(claim, make_row()))


def test_limits_other_window_allowed():
    claim = make_claim(window="other:2019 单年")
    assert "UNKNOWN_WINDOW" not in codes(clm.check_required_limits(claim, make_row()))


def test_limits_missing_d_definition():
    claim = make_claim(d_definition=None)
    assert "MISSING_D_DEFINITION" in codes(clm.check_required_limits(claim, make_row()))


def test_limits_missing_cost_id():
    row = make_row(family="B_long_only", detected=True)
    claim = make_claim(family="B_long_only", d_definition=None)
    assert "MISSING_COST_ID" in codes(clm.check_required_limits(claim, row))


def test_limits_cost_id_not_needed_when_not_detected():
    row = make_row(family="B_long_only", detected=False)
    claim = make_claim(family="B_long_only", d_definition=None)
    assert "MISSING_COST_ID" not in codes(clm.check_required_limits(claim, row))


def test_limits_missing_unit_note():
    row = make_row(family="E_fold", unit="pct_fold")
    claim = make_claim(family="E_fold", unit="pct_fold", d_definition="not_applicable")
    assert "MISSING_UNIT_NOTE" in codes(clm.check_required_limits(claim, row))


def test_limits_missing_resolution():
    row = make_row(detected=False)
    claim = make_claim(
        headline="均值为 −0.50pp（t=−1.20）。",
        numbers=[
            {"display": "−0.50", "field": "mean", "decimals": 2},
            {"display": "−1.20", "field": "t_nw3", "decimals": 2},
        ],
    )
    issues = codes(clm.check_required_limits(claim, row))
    assert "MISSING_RESOLUTION" in issues
    assert "MISSING_CORE_FIELD" in issues


def test_limits_thin_limit():
    claim = make_claim(limits_of_use=["仅供参考"])
    assert "THIN_LIMIT" in codes(clm.check_required_limits(claim, make_row()))


def test_limits_empty():
    claim = make_claim(limits_of_use=[])
    assert "MISSING_LIMITS" in codes(clm.check_required_limits(claim, make_row()))


def test_estimator_mismatch():
    """判定用 iid，正文只引 NW3 的 t ⇒ 挑显著性。"""
    row = make_row()
    claim = make_claim(
        headline="均值为 −0.50pp（t=−1.20），MDE 0.90pp。",
        estimator_for_verdict="iid",
        numbers=[
            {"display": "−0.50", "field": "mean", "decimals": 2},
            {"display": "−1.20", "field": "t_nw3", "decimals": 2},
            {"display": "0.90", "field": "mde", "decimals": 2},
        ],
    )
    assert "ESTIMATOR_MISMATCH" in codes(clm.check_required_limits(claim, row))


def test_estimator_both_listed_ok():
    row = make_row()
    claim = make_claim(
        headline="均值为 −0.50pp（t=−2.50 iid／−1.20 NW3），MDE 0.90pp。",
        estimator_for_verdict="iid",
        numbers=[
            {"display": "−0.50", "field": "mean", "decimals": 2},
            {"display": "−2.50", "field": "t_iid", "decimals": 2},
            {"display": "−1.20", "field": "t_nw3", "decimals": 2},
            {"display": "0.90", "field": "mde", "decimals": 2},
        ],
    )
    assert "ESTIMATOR_MISMATCH" not in codes(clm.check_required_limits(claim, row))


# --------------------------------------------------------------------------
# 撤回台账
# --------------------------------------------------------------------------


def test_retraction_banned_phrasing():
    claims = [make_claim(headline="该增量成立，均值为 −0.50pp（t=−1.20），MDE 0.90pp。")]
    rets = [{"claim_key": "k1", "banned_phrasing": "增量成立"}]
    assert "BANNED_PHRASING" in codes(clm.check_retractions(claims, rets))


def test_retraction_ignores_spaces():
    claims = [make_claim(headline="该增量 成立，均值为 −0.50pp（t=−1.20），MDE 0.90pp。")]
    rets = [{"claim_key": "k1", "banned_phrasing": "增量成立"}]
    assert "BANNED_PHRASING" in codes(clm.check_retractions(claims, rets))


def test_retraction_orphan():
    rets = [{"claim_key": "ghost", "banned_phrasing": "某措辞"}]
    assert "ORPHAN_RETRACTION" in codes(clm.check_retractions([make_claim()], rets))


def test_retraction_project_level_allowed():
    rets = [{"claim_key": None, "banned_phrasing": "某措辞"}]
    assert clm.check_retractions([make_claim()], rets) == []


def test_retraction_empty_ban():
    rets = [{"claim_key": "k1", "banned_phrasing": "  "}]
    assert "EMPTY_BAN" in codes(clm.check_retractions([make_claim()], rets))


# --------------------------------------------------------------------------
# 数值指纹
# --------------------------------------------------------------------------


def test_duplicate_groups():
    rows = [make_row(key="a"), make_row(key="b"), make_row(key="c", recomputed={"n": 24, "mean": 1.0, "t_nw3": 2.0, "mde": 0.5})]
    groups = clm.duplicate_groups(rows)
    assert len(groups) == 1
    assert groups[0]["keys"] == ["a", "b"]


def test_duplicate_groups_need_same_n():
    rows = [
        make_row(key="a"),
        make_row(key="b", recomputed={"n": 25, "mean": -0.5, "t_nw3": -1.2, "mde": 0.9}),
    ]
    assert clm.duplicate_groups(rows) == []


# --------------------------------------------------------------------------
# 渲染与整体
# --------------------------------------------------------------------------


def test_render_block_contains_parts():
    block = clm.render_citation_block(make_claim(), make_row(grade={"tier": "UNDERPOWERED"}))
    assert "full_sample" in block
    assert "0.5*long-0.5*short" in block
    assert "UNDERPOWERED" in block
    assert "monthly.csv::d" in block


def test_assemble_consistent():
    result = clm.assemble_registry({"claims": [make_claim()]}, {"rows": [make_row()]})
    assert result["status"] == "CLAIMS_CONSISTENT"
    assert result["n_violations"] == 0


def test_assemble_flags_missing_claim():
    result = clm.assemble_registry({"claims": []}, {"rows": [make_row()]})
    assert result["status"] == "CLAIMS_INCONSISTENT"
    assert "MISSING_CLAIM" in result["violation_counts"]


def test_assemble_counts_independent_numbers():
    rows = [make_row(key="a"), make_row(key="b")]
    claims = [make_claim(key="a"), make_claim(key="b")]
    result = clm.assemble_registry({"claims": claims}, {"rows": rows})
    assert result["n_duplicate_keys"] == 2
    assert result["n_independent_numbers"] == 1


# --------------------------------------------------------------------------
# 解封依据（tier_basis）
# --------------------------------------------------------------------------


def test_tier_basis_unseal_matches():
    row = make_row(grade={"tier": "NOT_GRADED"})
    claim = make_claim(quotability="QUOTABLE", tier_basis="l3w_unseal")
    index = {"k1": {"status": "UNSEALED", "tier": "QUOTABLE", "source": "H-L3W"}}
    assert clm.check_tier(claim, row, index) == []


def test_tier_basis_without_index():
    row = make_row(grade={"tier": "NOT_GRADED"})
    claim = make_claim(quotability="QUOTABLE", tier_basis="l3w_unseal")
    assert "UNKNOWN_TIER_BASIS" in codes(clm.check_tier(claim, row, None))


def test_tier_basis_rejects_sealed_entry():
    row = make_row(grade={"tier": "NOT_GRADED"})
    claim = make_claim(quotability="QUOTABLE", tier_basis="l3w_unseal")
    index = {"k1": {"status": "STAY_SEALED", "tier": "NOT_GRADED", "source": "H-L3W"}}
    assert "UNSEAL_NOT_APPROVED" in codes(clm.check_tier(claim, row, index))


def test_tier_basis_must_match_exactly():
    """解封只能照抄复核给的那一档，卡片不能自己抬一级。"""
    row = make_row(grade={"tier": "NOT_GRADED"})
    claim = make_claim(quotability="QUOTABLE", tier_basis="l3w_unseal")
    index = {"k1": {"status": "UNSEALED", "tier": "QUOTABLE_MARGINAL", "source": "H-L3W"}}
    assert "UNSEAL_TIER_MISMATCH" in codes(clm.check_tier(claim, row, index))


def test_tier_basis_does_not_apply_to_default():
    """没写 tier_basis 的条目仍走 L3u 规则，不能被解封清单「顺带」提级。"""
    row = make_row(grade={"tier": "NOT_GRADED"})
    claim = make_claim(quotability="QUOTABLE")
    index = {"k1": {"status": "UNSEALED", "tier": "QUOTABLE", "source": "H-L3W"}}
    assert "TIER_UPGRADE" in codes(clm.check_tier(claim, row, index))


def test_assemble_counts_unseal_quotes():
    row = make_row(grade={"tier": "NOT_GRADED"})
    claim = make_claim(quotability="QUOTABLE", tier_basis="l3w_unseal")
    index = {"k1": {"status": "UNSEALED", "tier": "QUOTABLE", "source": "H-L3W"}}
    out = clm.assemble_registry({"claims": [claim]}, {"rows": [row]}, index)
    assert out["status"] == "CLAIMS_CONSISTENT"
    assert out["n_quoted_via_unseal"] == 1
