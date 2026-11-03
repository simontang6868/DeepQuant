"""L3af 的证据分级与当前清单测试。

这里要防的是**标签被人工调栏**：只要有一处允许「统计显著 ⇒ 经济有意义」或
「有净收益列 ⇒ 成本后证据」，整套四分法就退化成一句好听的话。
所以每条断言都要求「由字段机械推出」，并给出能算出反例的构造。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from deepquant.gates import cost_net as cn
from deepquant.gates import evidence as ev
from deepquant.gates import family_af as fa

REPO = Path(__file__).resolve().parents[3]
PREREG = REPO / "docs" / "gates" / "l3af_prereg.json"
SHEET = REPO / "docs" / "DELIVERABLE_QUOTE_SHEET_CURRENT.md"
HISTORY = REPO / "docs" / "history" / "DELIVERABLE_QUOTE_SHEET_CURRENT_L3AB-v1.md"

needs_artifacts = pytest.mark.skipif(
    not (PREREG.exists() and SHEET.exists()),
    reason="L3af 清单或预登记缺失",
)


@pytest.fixture(scope="module")
def bundle() -> dict:
    cost = cn.build(REPO)
    family = fa.build(REPO)
    evid = ev.build(REPO, cost=cost, family=family)
    return {"cost": cost, "family": family, "evidence": evid}


# ==================== 1 卡片结构与机械性


@needs_artifacts
def test_seven_cards_one_per_positive_claim(bundle):
    cards = bundle["evidence"]["cards"]
    assert [c["id"] for c in cards] == list(cn.POSITIVE_CLAIMS)
    assert len(cards) == 7


@needs_artifacts
def test_each_card_carries_four_labels(bundle):
    for c in bundle["evidence"]["cards"]:
        assert set(c["statistics"]) == {"S1", "S2", "S3", "S4"}
        for k in ("S1", "S2", "S3", "S4"):
            assert c["statistics"][k]["status"] in {
                "PASS", "FAIL", "ABSENT", "TIER_DEPENDENT", "NOT_GRADED"
            }


@needs_artifacts
def test_status_is_a_pure_function_of_the_four_labels(bundle):
    for c in bundle["evidence"]["cards"]:
        assert c["status"] == ev.status_of(c)


def test_status_function_semantics():
    def card(s1, s3):
        return {"statistics": {"S1": {"status": s1}, "S3": {"status": s3}}}

    assert ev.status_of(card("FAIL", "PASS")) == "NOT_QUOTABLE_STAT"
    assert ev.status_of(card("PASS", "PASS")) == "QUOTABLE_WITH_COST_EVIDENCE"
    assert ev.status_of(card("PASS", "FAIL")) == "QUOTABLE_STAT_WITH_NEGATIVE_COST"
    assert ev.status_of(card("PASS", "TIER_DEPENDENT")) == "QUOTABLE_STAT_WITH_TIER_DEPENDENT_COST"
    assert ev.status_of(card("PASS", "ABSENT")) == "QUOTABLE_STAT_ONLY"


@needs_artifacts
def test_s2_is_never_derived_from_mde_ratio(bundle):
    """经济门槛只看 |效应| 与事前门槛；MDE 比值不参与任何 S2 判定。"""
    for c in bundle["evidence"]["cards"]:
        s2 = c["statistics"]["S2"]
        if s2["status"] == "NOT_GRADED":
            continue
        expect = "PASS" if abs(c["effect_pp"]) >= ev.ECON_THRESHOLD_PP else "FAIL"
        assert s2["status"] == expect
        # 反例：phasec2_d 的 |效应|/MDE > 1.7 但效应本身 < 0.30
        if c["id"] == "phasec2_d":
            assert abs(c["effect_pp"]) / c["mde_pp"] > 1.5
            assert s2["status"] == "FAIL"


@needs_artifacts
def test_net_return_claims_are_not_graded_on_borrowed_threshold(bundle):
    for c in bundle["evidence"]["cards"]:
        if c["id"] == "phaseo_netBASE":
            assert c["statistics"]["S2"]["status"] == "NOT_GRADED"
            assert "不成立" in c["statistics"]["S2"]["reason"]


# ==================== 2 成本后证据


@needs_artifacts
def test_only_two_claims_have_cost_construct(bundle):
    have = [c["id"] for c in bundle["evidence"]["cards"]
            if c["statistics"]["S3"]["status"] != "ABSENT"]
    assert set(have) == {"phaseo_netBASE", "phasei_v1"}


@needs_artifacts
def test_phasei_v0_does_not_borrow_v1_turnover(bundle):
    c = next(x for x in bundle["evidence"]["cards"] if x["id"] == "phasei_v0")
    s3 = c["statistics"]["S3"]
    assert s3["status"] == "ABSENT"
    assert s3["path"] == "no_turnover_for_v0"
    assert "沿用别的信号" in s3["note"]


@needs_artifacts
def test_phasei_v1_cost_status_is_tier_dependent_with_full_table(bundle):
    c = next(x for x in bundle["evidence"]["cards"] if x["id"] == "phasei_v1")
    s3 = c["statistics"]["S3"]
    assert s3["status"] == "TIER_DEPENDENT"
    tiers = {t["tier"]: t for t in s3["tiers"]}
    assert len(tiers) == 5
    assert tiers["T_0p3"]["significant_iid"] is False
    assert tiers["T_1e8"]["significant_iid"] is True
    # 主档必须被标出来，否则读者会拿最有利的一档当结论
    assert s3["primary_tier"] == "T_0p3"
    assert s3["primary_significant"] is False


@needs_artifacts
def test_cost_evidence_flag_matches_s3(bundle):
    for c in bundle["evidence"]["cards"]:
        assert c["has_cost_evidence"] == (c["statistics"]["S3"]["status"] == "PASS")


@needs_artifacts
def test_tradability_is_never_allowed(bundle):
    for c in bundle["evidence"]["cards"]:
        assert c["tradability_allowed"] is False
        assert c["has_oos_evidence"] is False
        assert c["statistics"]["S4"]["status"] == "ABSENT"
        assert not any(c["statistics"]["S4"]["requirements"].values())


# ==================== 3 闸门：不许自动升级


def test_gate_catches_promotion_without_cost_evidence():
    bad = {
        "cards": [{
            "id": "x",
            "has_cost_evidence": True,
            "status": "QUOTABLE_WITH_COST_EVIDENCE",
            "tradability_allowed": False,
            "statistics": {
                "S1": {"status": "PASS", "four_corrections": {"a": True}},
                "S2": {"status": "NOT_GRADED"},
                "S3": {"status": "ABSENT"},
                "S4": {"status": "ABSENT", "requirements": {}},
            },
        }],
        "counts": {},
    }
    fam = {"positive_by_variant": {"IND_m25": {m: cn.POSITIVE_CLAIMS for m in
                                              ("bonferroni", "holm", "bh", "by")}}}
    codes = {g["code"] for g in ev.gate_codes(bad, fam)}
    assert "G10_NO_AUTO_PROMOTION" in codes


def test_gate_catches_tradability_without_four_requirements():
    bad = {
        "cards": [{
            "id": "x",
            "has_cost_evidence": False,
            "status": "QUOTABLE_STAT_ONLY",
            "tradability_allowed": True,
            "statistics": {
                "S1": {"status": "PASS", "four_corrections": {"a": True}},
                "S2": {"status": "NOT_GRADED"},
                "S3": {"status": "ABSENT"},
                "S4": {"status": "ABSENT",
                       "requirements": {"out_of_sample": True, "fill_model": False,
                                        "capacity": False, "risk_analysis": False}},
            },
        }],
        "counts": {},
    }
    fam = {"positive_by_variant": {"IND_m25": {m: cn.POSITIVE_CLAIMS for m in
                                              ("bonferroni", "holm", "bh", "by")}}}
    codes = {g["code"] for g in ev.gate_codes(bad, fam)}
    assert "G10_NO_AUTO_PROMOTION" in codes


# ==================== 4 最终状态由闸门决定


def test_stage_status_on_missing_turnover():
    cost = {"availability": {"verdict": "BLOCKED_BY_MISSING_TURNOVER_SERIES"}, "gates": []}
    assert ev.choose_status(cost, {}, {}) == "BLOCKED_BY_MISSING_TURNOVER_SERIES"


def test_stage_status_on_reproduction_failure():
    cost = {"availability": {"verdict": "TURNOVER_AVAILABLE_VIA_PHASE_O"},
            "gates": [{"code": "G1_ALPHA_IDENTITY", "detail": "x"}]}
    assert ev.choose_status(cost, {}, {}) == "BLOCKED_BY_STATISTICAL_REPRODUCTION"


def test_stage_status_on_family_ambiguity():
    fam = {"gates": [], "positive_by_variant": {
        "IND_m25": {"bonferroni": list(cn.POSITIVE_CLAIMS)},
        "L3AC_m27": {"bonferroni": list(cn.POSITIVE_CLAIMS)[:-1]},
    }}
    cost = {"availability": {"verdict": "TURNOVER_AVAILABLE_VIA_PHASE_O"}, "gates": []}
    assert ev.choose_status(cost, fam, {}) == "BLOCKED_BY_TEST_FAMILY_AMBIGUITY"


@needs_artifacts
def test_real_status_is_documented_limitations(bundle):
    st = ev.choose_status(bundle["cost"], bundle["family"], bundle["evidence"])
    assert st == "VERIFIED_WITH_DOCUMENTED_LIMITATIONS"
    assert st in cn.STATUS_ENUM


# ==================== 5 清单与机器状态一致


@needs_artifacts
def test_sheet_lists_every_card_with_its_status(bundle):
    text = SHEET.read_text(encoding="utf-8")
    for c in bundle["evidence"]["cards"]:
        assert f"`{c['id']}`" in text
        assert f"`{c['status']}`" in text


@needs_artifacts
def test_sheet_counts_match_json(bundle):
    text = SHEET.read_text(encoding="utf-8")
    cnt = bundle["evidence"]["counts"]
    m = re.search(r"通过多重检验的 \*\*(\d+) 条正面结论\*\*", text)
    assert m and int(m.group(1)) == cnt["n_positive"]
    for key, label in (("S1_PASS", "S1"), ("S2_PASS", "S2"), ("S3_PASS", "S3"), ("S4_PASS", "S4")):
        m = re.search(rf"\*\*{label} (\d+)/{cnt['n_positive']}\*\*", text)
        assert m, f"清单缺 {label} 通过数"
        assert int(m.group(1)) == cnt[key]


@needs_artifacts
def test_sheet_version_is_current_and_history_is_archived(bundle):
    text = SHEET.read_text(encoding="utf-8")
    assert f"`{ev.SHEET_VERSION}`" in text
    assert "L3AB-CURRENT-v1" not in text.split("## 7")[0]  # 正文不得再宣称旧版本
    assert HISTORY.exists(), "上一版清单必须归档，不能被覆盖丢失"
    assert "L3AB-CURRENT-v1" in HISTORY.read_text(encoding="utf-8")


@needs_artifacts
def test_sheet_has_no_hand_written_numbers(bundle):
    """把 JSON 里的一个效应量改掉再渲染，清单必须跟着变——证明没有硬编码。"""
    fam = json.loads(json.dumps(bundle["family"]))
    cost = bundle["cost"]
    evid = json.loads(json.dumps(bundle["evidence"]))
    evid["cards"][0]["effect_pp"] = 12.3456
    text = ev.render_sheet(evid, cost, fam, generated_at="T")
    assert "+12.3456" in text
    assert "+12.3456" not in SHEET.read_text(encoding="utf-8")


@needs_artifacts
def test_sheet_records_cost_and_oos_and_tradability_for_every_card(bundle):
    text = SHEET.read_text(encoding="utf-8")
    for field in ("是否具备成本后证据", "是否具备样本外证据", "是否允许讨论可交易性"):
        assert text.count(field) >= 7
