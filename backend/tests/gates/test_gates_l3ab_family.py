"""L3ab 检验家族审计的不变量测试。

审计提纲第七节要求覆盖十项。本文件逐项对应，且每项都尽量用「可以证伪的判据」写，
而不是「跑一遍没报错」：

1. 家族成员与预登记一致 —— 清单的 key 集合必须**等于**预登记 key 集合，不是包含
2. 重复登记不被误认为独立交叉验证 —— 逐位相同的配对必须被判出来，且其判定字符串不同
3. 同源子窗口不因数据重叠被自动合并 —— 成员数不因「同源」而减少
4. m 的变化有可追溯依据 —— 四个口径的 m 与判定记录一致，且都能落到具体成员规则
5. 校正结果可由原始 p 值复现 —— 用清单里的 p 独立重算四种校正，与产物逐条比对
6. BY 与 BH 满足数学性质 —— 在真实 20 个 p 上验证 BY ⊆ BH
7. 当前清单不含已降级的旧状态 —— 清单 key 集合与卡片可引集合完全相同
8. 统计显著不自动等于经济有效 —— 价差类条目一律不得带「扣成本正收益」标记
9. 生成文件与权威产物一致 —— 清单结论句与卡片 headline 逐字相同
10. 缺失证据不被静默补全 —— UNKNOWN 必须留 UNKNOWN；无序列条目不得算作复现成功
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepquant.gates import current_sheet as cs
from deepquant.gates import family as fam
from deepquant.gates import multiplicity as mp

REPO = Path(__file__).resolve().parents[3]
FAMILY_JSON = REPO / "reports" / "gates" / "H-L3AB-FAMILY" / "l3ab_family.json"
CLAIMS_JSON = REPO / "reports" / "gates" / "H-L3AB-FAMILY" / "l3ab_current_claims.json"
SHEET_MD = REPO / "docs" / "history" / "DELIVERABLE_QUOTE_SHEET_CURRENT_L3AB-v1.md"

needs_artifacts = pytest.mark.skipif(
    not (FAMILY_JSON.exists() and CLAIMS_JSON.exists()),
    reason="L3ab 产物不在（先跑 l3ab_family_audit.py 与 l3ab_current_claims.py）",
)


@pytest.fixture(scope="module")
def family_payload() -> dict:
    return json.loads(FAMILY_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def claims_payload() -> dict:
    return json.loads(CLAIMS_JSON.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# 1. 家族成员与预登记一致
# --------------------------------------------------------------------------


@needs_artifacts
def test_family_members_equal_prereg(family_payload):
    prereg = json.loads(
        (REPO / "docs/gates/l3u_prereg.json").read_text(encoding="utf-8")
    )["entries"]
    expected = {e["key"] for e in prereg}
    got = {r["key"] for r in family_payload["inventory"]}
    assert got == expected, f"差集：缺 {sorted(expected - got)}，多 {sorted(got - expected)}"


@needs_artifacts
def test_inventory_fields_complete(family_payload):
    required = {
        "key", "hypothesis", "statistic", "series", "window", "n", "p",
        "se_method", "class", "cluster", "in_family", "reason", "tier", "counts_only",
    }
    for row in family_payload["inventory"]:
        missing = required - set(row)
        assert not missing, f"{row['key']} 缺字段 {missing}"
        assert row["hypothesis"], f"{row['key']} 假设为空"
        assert row["class"] in {"A", "B", "C", "D", "E"}, row["class"]


# --------------------------------------------------------------------------
# 2. 重复登记不被误认为独立交叉验证
# --------------------------------------------------------------------------


@needs_artifacts
def test_exact_pairs_detected_and_distinguished(family_payload):
    """逐位相同的配对必须被检出；且每对的登记判定必须不同（否则就是 A 类重复登记）。"""
    pairs = family_payload["exact_pairs"]
    assert len(pairs) == 5, f"逐位相同配对从 5 变成 {len(pairs)}，需重审家族"
    verdict = {r["key"]: r["verdict_original"] for r in family_payload["inventory"]}
    same_verdict = [p for p in pairs if verdict[p["a"]] == verdict[p["b"]]]
    assert same_verdict == [], f"出现同判定的同源配对（A 类）：{same_verdict}"
    # 判定记录里的 A 类计数必须与这个事实一致
    a_class = [c for c in family_payload["decisions"]["per_claim"] if c["class"] == "A"]
    assert a_class == []


@needs_artifacts
def test_duplicate_data_is_not_double_counted_as_cross_validation(family_payload):
    """同源配对必须被标出来，而不是当成两次独立验证。

    ⚠️ 这里钉住一个**容易算错**的地方，因为它本身就是一次真实错误的来源：
    5 对配对里有两组是「三条互相同值」（每两条都成一对），所以
    7 条落在 3 份序列上，按数据同一性合并是 27 − 7 + 3 = **23**，
    而不是 27 − 5 = 22、也不是「27 − 3 对」= 24。
    m=24 之所以是 24，是因为 L3z 的指纹含 n，phasep_late(n=129) 与 phaseo(n=237) 指纹不同。
    """
    pairs = family_payload["exact_pairs"]
    keys = {x for p in pairs for x in (p["a"], p["b"])}
    assert len(pairs) == 5
    assert len(keys) == 7, f"同源条目从 7 变成 {len(keys)}"
    assert len(family_payload["inventory"]) - len(keys) + 3 == 23
    # 主口径是「指纹去重」，不是「数据去重」，两者差 1（phasep_late）
    assert family_payload["specs"][0]["m"] == 24
    m23 = next(s for s in family_payload["specs"] if s["id"] == "S3_m23")
    assert m23["m"] == 23


# --------------------------------------------------------------------------
# 3. 同源子窗口不因数据重叠被自动合并
# --------------------------------------------------------------------------


@needs_artifacts
def test_subwindow_reported_not_merged(family_payload):
    sub = family_payload["subwindow"]
    assert "phasep_late" in sub, "phasep_late 应被识别为子窗口"
    # 三个条目逐位相同 ⇒ phasep_late 有两个合法超集，必须都报出来
    assert sub["phasep_late"] == ["phaseo_netBASE", "phasev_net"]
    # 识别出子窗口 ≠ 把它从家族里删掉
    row = next(r for r in family_payload["inventory"] if r["key"] == "phasep_late")
    assert row["in_family"] is True
    assert sum(1 for r in family_payload["inventory"] if r["in_family"]) == 27


# --------------------------------------------------------------------------
# 4. m 的变化有可追溯依据
# --------------------------------------------------------------------------


@needs_artifacts
def test_m_specs_match_decisions(family_payload):
    declared = {s["id"]: s["m"] for s in family_payload["decisions"]["m_specs"]}
    computed = {s["id"]: s["m"] for s in family_payload["specs"]}
    # S4 的 m 是算出来的（假设簇数），其余是写死的
    for spec_id, m in computed.items():
        if declared.get(spec_id) == "computed":
            clusters = {r["cluster"] for r in family_payload["inventory"] if r["in_family"]}
            assert m == len(clusters), f"S4 应等于假设簇数 {len(clusters)}，实得 {m}"
        else:
            assert m == declared[spec_id], f"{spec_id}: 声明 {declared[spec_id]} 实得 {m}"


@needs_artifacts
def test_only_one_primary_spec(family_payload):
    adopted = [s["id"] for s in family_payload["specs"] if s["adopted"]]
    assert adopted == ["S1_m24"], f"主口径应唯一，实得 {adopted}"
    # 分族口径必须明示不采用，否则会被当成结论
    s4 = next(s for s in family_payload["specs"] if s["id"] == "S4_hypothesis")
    assert s4["adopted"] is False
    assert "不采用" in s4["limitation"]


# --------------------------------------------------------------------------
# 5. 校正结果可由原始 p 值复现
# --------------------------------------------------------------------------


@needs_artifacts
def test_corrections_reproduce_from_pvalues(family_payload):
    for spec in family_payload["specs"]:
        pvals = [i["p"] for i in spec["items"]]
        m = spec["m"]
        padded = pvals + [1.0] * (m - len(pvals))
        expected = {
            "bonferroni": mp.bonferroni(padded, m=m)[: len(pvals)],
            "holm": mp.holm(padded, m=m)[: len(pvals)],
            "bh": mp.bh(padded, m=m)[: len(pvals)],
            "by": mp.benjamini_yekutieli(padded, m=m)[: len(pvals)],
        }
        for method, flags in expected.items():
            got = [i[method] for i in spec["items"]]
            assert got == flags, f"{spec['id']}/{method} 不能由 p 值复现"


@needs_artifacts
def test_reproduction_matches_l3u(family_payload):
    """从序列重算的均值/标准误/t 必须与 L3u 的 recomputed 一致。"""
    bad = [r["key"] for r in family_payload["repro"] if r["match"] is False]
    assert bad == [], f"以下条目的重算与 L3u 不一致：{bad}"
    scored = [r for r in family_payload["repro"] if r["match"] is not None]
    assert len(scored) == 22, f"可重算条目从 22 变成 {len(scored)}"
    worst = max(r["worst"] for r in scored)
    assert worst <= 1e-9, f"最大偏差 {worst}"


# --------------------------------------------------------------------------
# 6. BY 与 BH 的数学性质
# --------------------------------------------------------------------------


@needs_artifacts
def test_by_subset_of_bh_on_real_pvalues(family_payload):
    for spec in family_payload["specs"]:
        by = {i["key"] for i in spec["items"] if i["by"]}
        bh = {i["key"] for i in spec["items"] if i["bh"]}
        assert by <= bh, f"{spec['id']}：BY 不是 BH 的子集，多出 {sorted(by - bh)}"


def test_by_stricter_when_hm_grows():
    """H_m 随 m 增长 ⇒ 单档门槛单调下降；且 m=1 时退化为单条检验。"""
    thr = [mp.by_unit_threshold(q=0.05, m=m) for m in (1, 5, 24, 27)]
    assert all(a > b for a, b in zip(thr, thr[1:], strict=False))
    assert thr[0] == pytest.approx(0.05)


# --------------------------------------------------------------------------
# 7. 当前清单不含已降级的旧状态
# --------------------------------------------------------------------------


@needs_artifacts
def test_current_sheet_excludes_downgraded(claims_payload):
    cards = json.loads((REPO / "docs/gates/l3v_claims.json").read_text(encoding="utf-8"))
    quotable = {
        c["key"] for c in cards["claims"]
        if c["quotability"].startswith("QUOTABLE") or c["quotability"] == "NULL_CONFIRMED"
    }
    got = {c["key"] for c in claims_payload["claims"]}
    assert got == quotable, f"清单与卡片可引集合不一致：差 {got ^ quotable}"
    for c in claims_payload["claims"]:
        assert c["tier"] not in {"EXPLORATORY", "UNDERPOWERED", "NOT_GRADED", "DIRECTION_ONLY"}


@needs_artifacts
def test_sheet_marks_version_and_status(claims_payload):
    assert claims_payload["sheet_version"].startswith("L3AB-CURRENT")
    assert claims_payload["sheet_status"] == "CURRENT_WITH_DOCUMENTED_LIMITATIONS"
    assert claims_payload["generated_at"]
    text = SHEET_MD.read_text(encoding="utf-8")
    assert claims_payload["sheet_version"] in text
    assert "禁止手工编辑" in text


# --------------------------------------------------------------------------
# 8. 统计显著不自动等于经济有效
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,expected",
    [
        ("phaseo_netBASE", "net_return"),
        ("phasel_alpha", "gross_alpha"),
        ("phasec_d", "cross_section_spread"),
        ("gate6_d", "cross_section_spread"),
        ("phasec2_d", "cross_section_spread"),
        ("phasef_delta", "cross_section_spread"),
        ("phaseq_max5", "regression_coef"),
        ("l3s_ic", "regression_coef"),
    ],
)
def test_economic_basis_classification(key, expected):
    assert cs.economic_basis(key) == expected


@needs_artifacts
def test_only_net_returns_carry_cost_evidence(claims_payload):
    for c in claims_payload["claims"]:
        should = c["economic_basis"] == "net_return"
        assert c["cost_adjusted_positive_evidence"] is should, (
            f"{c['key']}：经济属性 {c['economic_basis']} 与扣成本证据标记不一致"
        )
    n_with = sum(1 for c in claims_payload["claims"] if c["cost_adjusted_positive_evidence"])
    assert n_with == 1, f"带扣成本正收益证据的应只有 1 条，实得 {n_with}"


@needs_artifacts
def test_no_claim_has_out_of_sample_evidence(claims_payload):
    for c in claims_payload["claims"]:
        assert c["out_of_sample_evidence"] is False
        assert "样本内" in c["out_of_sample_note"]


@needs_artifacts
def test_null_claim_flagged(claims_payload):
    nulls = [c["key"] for c in claims_payload["claims"] if c["null_claim"]]
    assert nulls == ["phasel_alpha"]
    text = SHEET_MD.read_text(encoding="utf-8")
    assert "零效应判定" in text


# --------------------------------------------------------------------------
# 9. 生成文件与权威产物一致
# --------------------------------------------------------------------------


@needs_artifacts
def test_headline_copied_verbatim(claims_payload):
    cards = {
        c["key"]: c for c in json.loads(
            (REPO / "docs/gates/l3v_claims.json").read_text(encoding="utf-8")
        )["claims"]
    }
    for c in claims_payload["claims"]:
        assert c["allowed_wording"] == cards[c["key"]]["headline"]
        assert c["limits_of_use"] == list(cards[c["key"]].get("limits_of_use") or [])


@needs_artifacts
def test_sheet_md_contains_every_claim(claims_payload):
    text = SHEET_MD.read_text(encoding="utf-8")
    for c in claims_payload["claims"]:
        assert f"`{c['key']}`" in text, f"{c['key']} 未出现在清单里"


def test_cell_escapes_pipes():
    """结论句里的 |效应|/MDE 必须转义，否则表格会错列。"""
    href = "|效应|/MDE 2.27 倍"
    escaped = href.replace("|", "\\|")
    assert escaped.count("\\|") == 2
    # 未转义时 2 个竖线会把一格切成 3 段，表格因此错列——这正是要防的
    assert len(href.split("|")) == 3


# --------------------------------------------------------------------------
# 10. 缺失证据不被静默补全
# --------------------------------------------------------------------------


def test_decisions_sha_enforced(tmp_path):
    """判定记录被改动必须拒跑，而不是带着新分类继续。"""
    bad = tmp_path / "docs" / "gates"
    bad.mkdir(parents=True)
    (bad / "l3ab_family_decisions.json").write_text("{}", encoding="utf-8")
    with pytest.raises(fam.FamilyError):
        fam.load_decisions(tmp_path)


@needs_artifacts
def test_unknowns_preserved(family_payload):
    unknowns = family_payload["decisions"]["unknowns"]
    assert len(unknowns) >= 4
    statuses = {u["id"]: u["status"] for u in unknowns}
    assert statuses["U1"] == "UNKNOWN"
    assert statuses["U2"] == "UNKNOWN"
    for u in unknowns:
        assert u["impact"], f"{u['id']} 未说明对结论的影响"


@needs_artifacts
def test_missing_series_not_counted_as_reproduced(family_payload):
    """没有序列的条目必须标 NO_SERIES，不能算成「复现成功」。"""
    no_series = [r for r in family_payload["repro"] if r["status"] == "NO_SERIES"]
    assert len(no_series) == 5, f"无序列条目从 5 变成 {len(no_series)}"
    for r in no_series:
        assert r["match"] is None
    matched = [r for r in family_payload["repro"] if r["match"] is True]
    assert len(matched) == 22


@needs_artifacts
def test_coverage_gap_reported_not_hidden(family_payload):
    cov = family_payload["coverage"]
    assert cov["n_hypothesis_unregistered"] == 1
    flagged = [u for u in cov["unregistered"] if u["has_hypothesis_conclusion"]]
    assert [u["dir"] for u in flagged] == ["phasei_out"]
    # 只读审计不得被算成缺口
    audit_only = [u for u in cov["unregistered"] if u["audit_only"]]
    assert [u["dir"] for u in audit_only] == ["phaseu_out"]


@needs_artifacts
def test_retraction_recorded(family_payload):
    rets = family_payload["decisions"]["retractions"]
    assert rets, "必须保留对 L3aa 的撤回记录"
    assert rets[0]["id"].startswith("R-L3AA")
    assert "偏大" in rets[0]["retracted_statement"]
    # 原文保留：L3aa 报告文件仍存在且未被改写
    assert (REPO / "reports" / "L3AA_ARBITRARY_DEPENDENCE_AUDIT.md").exists()
