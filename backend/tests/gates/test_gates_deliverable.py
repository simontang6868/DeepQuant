"""L3y 定稿渲染与数字闸门的单元测试。

夹具刻意用**合成数据**：这一层测的是「抄写有没有出错」的机制，
不应当依赖真实产物的具体数值——那样数值一变测试就红，而机制没变。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepquant.gates import deliverable as dlv

CLIP = dlv.CLIP_TIERS


# --------------------------------------------------------------------------
# 夹具
# --------------------------------------------------------------------------


def make_row(key="k1", family="A_cross_spread", unit="pp", series_n=237, tier="QUOTABLE",
             recomputed_n=None, comparison=None):
    return {
        "key": key,
        "family": family,
        "unit": unit,
        "series_n": series_n,
        "series": f"{key}_out/abcdef123456/monthly.csv::d",
        "grade": {"tier": tier},
        "comparison": comparison or {"class": "MATCH"},
        "recomputed": {"n": series_n if recomputed_n is None else recomputed_n, "mean": 0.5},
    }


def make_claim(key="k1", quotability="QUOTABLE", headline="效应为 +0.50pp（t=+2.00）。",
               numbers=None, literals=None, limits=None, tier_basis=None):
    return {
        "key": key,
        "tier_basis": tier_basis,
        "headline": headline,
        "numbers": numbers
        if numbers is not None
        else [
            {"display": "0.50", "field": "mean", "decimals": 2},
            {"display": "2.00", "field": "t_nw3", "decimals": 2},
        ],
        "literals": literals or [],
        "limits_of_use": limits or [],
        "window": "single_window",
        "estimator_for_verdict": "nw3",
        "quotability": quotability,
    }


def make_l3v(tier_counts=None, n_claims=1, n_independent=1, n_unseal=0):
    return {
        "n_claims": n_claims,
        "n_l3u_rows": n_claims,
        "n_independent_numbers": n_independent,
        "n_quoted_via_unseal": n_unseal,
        "tier_counts": tier_counts or {"QUOTABLE": 1},
    }


def codes(violations):
    return {v["code"] for v in violations}


# --------------------------------------------------------------------------
# facts 结构校验
# --------------------------------------------------------------------------


def test_load_facts_ok(tmp_path):
    p = tmp_path / "f.json"
    p.write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "key": "a",
                        "display": "1",
                        "note": "n",
                        "source": {"path": "x.json", "pointer": "a.b"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert dlv.load_facts(p)["facts"][0]["key"] == "a"


def test_load_facts_rejects_empty(tmp_path):
    p = tmp_path / "f.json"
    p.write_text(json.dumps({"facts": []}), encoding="utf-8")
    with pytest.raises(dlv.DeliverableError):
        dlv.load_facts(p)


def test_load_facts_rejects_duplicate_key(tmp_path):
    p = tmp_path / "f.json"
    fact = {"key": "a", "display": "1", "note": "n", "source": {"path": "x", "pointer": "p"}}
    p.write_text(json.dumps({"facts": [fact, dict(fact)]}), encoding="utf-8")
    with pytest.raises(dlv.DeliverableError):
        dlv.load_facts(p)


def test_load_facts_rejects_both_pointers(tmp_path):
    p = tmp_path / "f.json"
    p.write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "key": "a",
                        "display": "1",
                        "note": "n",
                        "source": {"path": "x", "pointer": "p", "pointers": ["q"]},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(dlv.DeliverableError):
        dlv.load_facts(p)


def test_load_facts_rejects_unknown_op(tmp_path):
    p = tmp_path / "f.json"
    p.write_text(
        json.dumps(
            {
                "facts": [
                    {
                        "key": "a",
                        "display": "1",
                        "note": "n",
                        "source": {"path": "x", "pointer": "p", "op": "median"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(dlv.DeliverableError):
        dlv.load_facts(p)


def test_load_facts_missing_file():
    with pytest.raises(dlv.DeliverableError):
        dlv.load_facts("does/not/exist.json")


# --------------------------------------------------------------------------
# facts 回源实算
# --------------------------------------------------------------------------


def _write_source(tmp_path, name, payload):
    p = tmp_path / name
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def test_verify_facts_passes(tmp_path):
    _write_source(tmp_path, "s.json", {"meta": {"n_symbols": 5472}})
    facts = [
        {
            "key": "n",
            "display": "5472",
            "decimals": 0,
            "note": "note",
            "source": {"path": "s.json", "pointer": "meta.n_symbols"},
        }
    ]
    verified, problems = dlv.verify_facts(facts, tmp_path)
    assert problems == []
    assert len(verified) == 1
    assert verified[0]["computed"] == "5472"
    assert verified[0]["source_sha256"]


def test_verify_facts_drift(tmp_path):
    """事实表断言与源文件不符 —— display 只是断言，值以源文件为准。"""
    _write_source(tmp_path, "s.json", {"meta": {"n_symbols": 5472}})
    facts = [
        {
            "key": "n",
            "display": "5444",
            "note": "note",
            "source": {"path": "s.json", "pointer": "meta.n_symbols"},
        }
    ]
    verified, problems = dlv.verify_facts(facts, tmp_path)
    assert verified == []
    assert "FACT_DRIFT" in codes(problems)
    assert "5472" in problems[0]["detail"]


def test_verify_facts_missing_source(tmp_path):
    facts = [
        {
            "key": "n",
            "display": "1",
            "note": "note",
            "source": {"path": "nope.json", "pointer": "a"},
        }
    ]
    verified, problems = dlv.verify_facts(facts, tmp_path)
    assert verified == []
    assert "FACT_SOURCE_MISSING" in codes(problems)


def test_verify_facts_missing_pointer_is_error_not_silent_none(tmp_path):
    _write_source(tmp_path, "s.json", {"meta": {"n": 1}})
    facts = [
        {
            "key": "n",
            "display": "1",
            "note": "note",
            "source": {"path": "s.json", "pointer": "meta.nope"},
        }
    ]
    verified, problems = dlv.verify_facts(facts, tmp_path)
    assert verified == []
    assert "FACT_POINTER_MISSING" in codes(problems)


def test_verify_facts_sum_op(tmp_path):
    _write_source(tmp_path, "s.json", {"t": {"QUOTABLE": 1, "MARGINAL": 4}})
    facts = [
        {
            "key": "clip",
            "display": "5",
            "note": "note",
            "source": {"path": "s.json", "pointers": ["t.QUOTABLE", "t.MARGINAL"], "op": "sum"},
        }
    ]
    verified, problems = dlv.verify_facts(facts, tmp_path)
    assert problems == []
    assert verified[0]["computed"] == "5"


def test_verify_facts_first_nonzero_key(tmp_path):
    """年份是「键」不是值：取第一个非零年份，让年份也有可实算的出处。"""
    _write_source(
        tmp_path, "s.json", {"by_year": {"2004": 0, "2005": 6, "2006": 1177}}
    )
    facts = [
        {
            "key": "y",
            "display": "2005",
            "note": "note",
            "source": {"path": "s.json", "pointer": "by_year", "op": "first_nonzero_key"},
        }
    ]
    verified, problems = dlv.verify_facts(facts, tmp_path)
    assert problems == []
    assert verified[0]["value"] == "2005"


def test_verify_facts_first_nonzero_key_all_zero(tmp_path):
    _write_source(tmp_path, "s.json", {"by_year": {"2004": 0}})
    facts = [
        {
            "key": "y",
            "display": "2005",
            "note": "note",
            "source": {"path": "s.json", "pointer": "by_year", "op": "first_nonzero_key"},
        }
    ]
    verified, problems = dlv.verify_facts(facts, tmp_path)
    assert verified == []
    assert "FACT_POINTER_MISSING" in codes(problems)


def test_format_value_scale_and_decimals():
    assert dlv.format_value(0.169, decimals=0, scale=100) == "17"
    assert dlv.format_value(1.94, decimals=2) == "1.94"


# --------------------------------------------------------------------------
# allowed 集合
# --------------------------------------------------------------------------


def test_collect_allowed_includes_all_four_sources():
    claims = {
        "claims": [make_claim(numbers=[{"display": "0.50", "field": "mean", "decimals": 2}])]
    }
    l3v = make_l3v(tier_counts={"QUOTABLE": 1}, n_claims=1, n_independent=1)
    facts = [{"key": "f", "display": "518", "note": "n"}]
    rows = [make_row(series_n=237)]
    allowed = dlv.collect_allowed(claims, l3v, rows, facts)
    for token in ("0.50", "518", "237", "1"):
        assert token in allowed, token
    # 口径常量必须来自常量表，不能是手写白名单
    assert "0.5" in allowed
    assert any("schema:" in r for r in allowed["0.5"])


def test_collect_allowed_ignores_non_numeric_display():
    claims = {"claims": []}
    l3v = make_l3v()
    allowed = dlv.collect_allowed(claims, l3v, [], [{"key": "f", "display": "m1", "note": "n"}])
    assert "m1" not in allowed


# --------------------------------------------------------------------------
# 全文数字扫描
# --------------------------------------------------------------------------


def test_scan_numbers_flags_unsourced():
    allowed = {"0.50": ["card:k1:mean"]}
    out = dlv.scan_numbers("效应为 0.50pp，另有 3.14 处未登记。", allowed)
    assert [v["token"] for v in out] == ["3.14"]
    assert out[0]["code"] == "UNSOURCED_NUMBER"


def test_scan_numbers_uses_same_token_rule_as_cards():
    """紧贴字母的数字是口径标签（NW3 / MAX5 / m1），不是统计量。"""
    allowed: dict = {}
    assert dlv.scan_numbers("NW3 与 MAX5 与 m1", allowed) == []


def test_scan_numbers_matches_on_stripped_sign():
    allowed = {"0.40": ["card:k1:l3t_mean"]}
    assert dlv.scan_numbers("值为 -0.40pp", allowed) == []


def test_scan_numbers_counts_repeats_once():
    allowed: dict = {}
    out = dlv.scan_numbers("3.14 出现一次，又出现 3.14", allowed)
    assert len(out) == 1
    assert "2" in out[0]["detail"]


# --------------------------------------------------------------------------
# 封印泄漏 / window n / 分级表
# --------------------------------------------------------------------------


def test_sealed_leak_detected():
    sealed = [make_claim(key="s1", quotability="NOT_GRADED",
                         headline="净 alpha 为 +0.0424pp（t=+1.23），与序列不同源。")]
    md = "# 定稿\n\n净 alpha 为 +0.0424pp（t=+1.23），与序列不同源。"
    out = dlv.check_sealed_leak(md, sealed)
    assert "SEALED_LEAK" in codes(out)


def test_sealed_leak_ignores_short_headlines():
    """「无法判断」这类短句做子串匹配会满篇误报。"""
    sealed = [make_claim(quotability="NOT_GRADED", headline="无法判断")]
    md = "无法判断"
    assert dlv.check_sealed_leak(md, sealed) == []


def test_sealed_leak_is_punctuation_insensitive():
    sealed = [make_claim(quotability="NOT_GRADED", headline="净 alpha 为 +0.0424pp 与序列不同源，不可引用。")]
    md = "净 alpha 为 +0.0424pp、与序列不同源。不可引用"
    assert dlv.check_sealed_leak(md, sealed)


def test_window_n_mismatch():
    assert dlv.check_window_n("样本数：n=108", [237, 108]) == []
    out = dlv.check_window_n("样本数：n=999", [237, 108])
    assert "WINDOW_N_MISMATCH" in codes(out)


def test_tier_table_missing_and_drift():
    md = "| `QUOTABLE` | 2 |\n| `UNDERPOWERED` | 12 |"
    out = dlv.check_tier_table(md, {"QUOTABLE": 1, "UNDERPOWERED": 12})
    assert "TIER_COUNT_DRIFT" in codes(out)
    assert "TIER_ROW_MISSING" in codes(dlv.check_tier_table(md, {"NOT_GRADED": 6}))


def test_tier_recount_matches():
    claims = {"claims": [make_claim(quotability="QUOTABLE"), make_claim(key="k2", quotability="UNDERPOWERED")]}
    out = dlv.check_tier_recount(claims, {"QUOTABLE": 1, "UNDERPOWERED": 1})
    assert out["ok"] and out["violations"] == []


def test_tier_recount_detects_card_changed_without_rerun():
    claims = {"claims": [make_claim(quotability="QUOTABLE"), make_claim(key="k2", quotability="QUOTABLE")]}
    out = dlv.check_tier_recount(claims, {"QUOTABLE": 1, "UNDERPOWERED": 1})
    assert not out["ok"]
    assert "TIER_RECOUNT_DRIFT" in codes(out["violations"])


# --------------------------------------------------------------------------
# 封印原因码与解封索引
# --------------------------------------------------------------------------


def test_sealed_reason_prefers_unseal_decision():
    sealed = [make_claim(key="s1", quotability="NOT_GRADED")]
    rows = [make_row(key="s1", comparison={"class": "NOT_APPLICABLE"})]
    decisions = {"s1": {"status": "STAY_SEALED", "class": "B_different_budget"}}
    assert dlv.sealed_reason_codes(sealed, rows, decisions)["s1"] == "B_different_budget"


def test_sealed_reason_falls_back_to_l3u():
    sealed = [make_claim(key="s1", quotability="NOT_GRADED")]
    rows = [make_row(key="s1", comparison={"class": "NOT_APPLICABLE"})]
    assert dlv.sealed_reason_codes(sealed, rows, {})["s1"] == "NOT_APPLICABLE"


def test_sealed_reason_unknown_when_row_missing():
    sealed = [make_claim(key="s1", quotability="NOT_GRADED")]
    assert dlv.sealed_reason_codes(sealed, [], {})["s1"] == "UNKNOWN"


def test_decision_index_reads_unseal_sources(tmp_path):
    p = tmp_path / "u.json"
    p.write_text(json.dumps({"decisions": [{"key": "s1", "class": "B_missing_stat"}]}),
                 encoding="utf-8")
    idx = dlv.decision_index({"unseal_l3x": "u.json"}, tmp_path)
    assert idx["s1"]["class"] == "B_missing_stat"


def test_decision_index_skips_non_unseal_sources(tmp_path):
    p = tmp_path / "u.json"
    p.write_text(json.dumps({"decisions": [{"key": "s1"}]}), encoding="utf-8")
    assert dlv.decision_index({"l3v_result": "u.json"}, tmp_path) == {}


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------


def test_render_does_not_emit_series_path_or_notes():
    """序列路径的 hash 前缀与内部分级依据都不该进对外文档。"""
    row = make_row(series_n=237)
    claim = make_claim()
    block = dlv.render_claim_block(claim, row)
    assert "abcdef123456" not in block
    assert "n=237" in block


def test_render_marks_unseal_source():
    """解封条目的分级来源必须点明「最初核对未通过」，不能写成一开始就通过。"""
    row = make_row()
    claim = make_claim(tier_basis="l3w_unseal")
    block = dlv.render_claim_block(claim, row)
    assert "核对未通过" in block
    assert "l3w_unseal" in block


def test_render_marks_audit_downgrade():
    """审计降级与解封是两回事：前者是把分级往下调，后者是把封印解开。"""
    row = make_row()
    claim = make_claim(quotability="EXPLORATORY", tier_basis="l3z_audit")
    block = dlv.render_claim_block(claim, row)
    assert "审计降级" in block
    assert "解封" not in block


def test_render_marks_l3u_source():
    row = make_row()
    block = dlv.render_claim_block(make_claim(), row)
    assert "最初核对即通过" in block


def test_render_keeps_limits_verbatim():
    row = make_row()
    claim = make_claim(limits=["这是限定，必须原样保留"])
    assert "- 限定：这是限定，必须原样保留" in dlv.render_claim_block(claim, row)


def test_render_splits_sealed_and_weak():
    claims = {
        "claims": [
            make_claim(key="a", quotability="QUOTABLE"),
            make_claim(key="b", quotability="NOT_GRADED"),
            make_claim(key="c", quotability="UNDERPOWERED"),
        ]
    }
    l3u = {"rows": [make_row(key="a"), make_row(key="b"), make_row(key="c")]}
    l3v = make_l3v(tier_counts={"QUOTABLE": 1, "NOT_GRADED": 1, "UNDERPOWERED": 1}, n_claims=3)
    md = dlv.render_document(
        claims_payload=claims,
        l3u=l3u,
        l3v=l3v,
        facts=[],
        tier_counts=l3v["tier_counts"],
        reason_codes={"b": "B_missing_stat"},
    )
    assert "| `b` |" in md
    assert "| `c` |" in md
    assert "无分辨力" in md


def test_document_has_no_numbered_headings():
    """章节序号会被数字扫描当成统计量（首跑就抓到了 4.1 / 4.2）。"""
    claims = {"claims": [make_claim()]}
    l3u = {"rows": [make_row()]}
    l3v = make_l3v(n_claims=1)
    md = dlv.render_document(
        claims_payload=claims,
        l3u=l3u,
        l3v=l3v,
        facts=[],
        tier_counts=l3v["tier_counts"],
        reason_codes={},
    )
    import re

    assert not re.search(r"^### \d", md, flags=re.M)


# --------------------------------------------------------------------------
# assemble 端到端
# --------------------------------------------------------------------------


def test_assemble_consistent(tmp_path):
    _write_source(tmp_path, "s.json", {"n": 518})
    facts = {
        "facts": [
            {"key": "p", "display": "518", "decimals": 0, "note": "池规模",
             "source": {"path": "s.json", "pointer": "n"}}
        ]
    }
    claims = {"claims": [make_claim()]}
    l3u = {"rows": [make_row()]}
    l3v = make_l3v(n_claims=1, n_independent=1)
    out = dlv.assemble(
        claims_payload=claims,
        l3u=l3u,
        l3v=l3v,
        facts_payload=facts,
        root=tmp_path,
        prereg_sha="abc",
        sources={},
    )
    assert out["status"] == "DELIVERABLE_CONSISTENT"
    assert out["n_violations"] == 0
    assert out["n_facts"] == 1


def test_assemble_flags_fact_drift(tmp_path):
    _write_source(tmp_path, "s.json", {"n": 518})
    facts = {
        "facts": [
            {"key": "p", "display": "519", "note": "池规模",
             "source": {"path": "s.json", "pointer": "n"}}
        ]
    }
    out = dlv.assemble(
        claims_payload={"claims": [make_claim()]},
        l3u={"rows": [make_row()]},
        l3v=make_l3v(n_claims=1),
        facts_payload=facts,
        root=tmp_path,
        prereg_sha="abc",
        sources={},
    )
    assert out["status"] == "DELIVERABLE_INCONSISTENT"
    assert "FACT_DRIFT" in out["violation_counts"]


def test_assemble_flags_unsourced_number_in_document(tmp_path):
    claims = {"claims": [make_claim(headline="效应为 +0.50pp，另有 9.99pp 无处可查。")]}
    out = dlv.assemble(
        claims_payload=claims,
        l3u={"rows": [make_row()]},
        l3v=make_l3v(n_claims=1),
        facts_payload={"facts": []},
        root=tmp_path,
        prereg_sha="abc",
        sources={},
    )
    assert "UNSOURCED_NUMBER" in out["violation_counts"]


def test_assemble_reports_schema_tokens(tmp_path):
    out = dlv.assemble(
        claims_payload={"claims": [make_claim()]},
        l3u={"rows": [make_row()]},
        l3v=make_l3v(n_claims=1),
        facts_payload={"facts": []},
        root=tmp_path,
        prereg_sha="abc",
        sources={},
    )
    assert out["schema_tokens"] == dlv.SCHEMA_TOKENS
    # 上限是刻意的：每加一条就是开一个后门，超过这个数说明该找实算出处了
    assert len(dlv.SCHEMA_TOKENS) <= 8


def test_clip_tiers_and_verdict_agree():
    """CLIP_TIERS 与 CLIP_VERDICT 是同一个判据的两处表达，必须一致。"""
    assert set(CLIP) <= set(dlv.CLIP_VERDICT)
    for tier in ("UNDERPOWERED", "NOT_GRADED"):
        assert tier not in CLIP
        assert dlv.CLIP_VERDICT[tier].startswith("不可")
