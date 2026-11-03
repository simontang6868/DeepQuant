"""L3ad 检验独立性的不变量测试。

本阶段的核心风险不是算错，而是**判据被 p 值牵着走**。所以测试里专门有一条
「判据中立性」：把判据应用到一个全部不显著的子集上，合并必须照常发生。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepquant.gates import independence as ind

REPO = Path(__file__).resolve().parents[3]
L3AD_JSON = REPO / "reports" / "gates" / "H-L3AD-INDEPENDENCE" / "l3ad_independence.json"
SHEET_MD = REPO / "docs" / "DELIVERABLE_QUOTE_SHEET_L3AD.md"

needs_artifacts = pytest.mark.skipif(
    not L3AD_JSON.exists(), reason="L3ad 产物不在（先跑 scripts/l3ad_independence.py）"
)


@pytest.fixture(scope="module")
def res() -> dict:
    return json.loads(L3AD_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def built() -> dict:
    return ind.build(REPO)


# ---------------------------------------------------------------- 1
def test_three_groups_seven_members(built: dict) -> None:
    """三组、7 条、合并掉 4 条 ⇒ 独立随机变量 23 份，加 phasei 2 条 = m 25。"""
    part = built["partition"]
    multi = [g for g in part["groups"] if len(g) > 1]
    assert len(multi) == 3, f"多成员分组应恰为 3 个，实际 {len(multi)}"
    assert sum(len(g) for g in multi) == 7
    assert part["n_merged_away"] == 4
    assert built["n_registered"] - part["n_merged_away"] + built["n_added"] == 25
    assert built["runs"]["IND_m25"]["m"] == 25


# ---------------------------------------------------------------- 2
def test_criterion_requires_full_coverage(built: dict) -> None:
    """判据要求较短序列被完全覆盖；只重叠一部分不算同一变量。"""
    for p in built["partition"]["pairs"]:
        assert p["coverage"] >= 1.0, f"{p['a']}~{p['b']} 覆盖率 {p['coverage']}"
        assert p["n_common"] == min(p["n_a"], p["n_b"])
        assert p["max_abs_diff"] <= ind.TOL


# ---------------------------------------------------------------- 3
def test_equal_length_pairs_have_identical_p(built: dict) -> None:
    """等长同值的两条必须给出完全相同的 p（否则就不是同一个随机变量）。"""
    equal_pairs = [p for p in built["partition"]["pairs"] if p["equal_length"]]
    # 三组里只有 {phasel,phaser}、{phaseo,phasev}、{phases,phaset} 是等长同值；
    # {phasep,phaseo} 与 {phasep,phasev} 是子窗口，长度不同。
    assert len(equal_pairs) == 3, f"等长同值对应为 3，实际 {len(equal_pairs)}"
    for p in equal_pairs:
        pa = built["stats"][p["a"]]["p"]
        pb = built["stats"][p["b"]]["p"]
        assert abs(pa - pb) <= ind.TOL, f"{p['a']}~{p['b']} p 不同"


# ---------------------------------------------------------------- 4
def test_representative_never_shorter(built: dict) -> None:
    """代表不得比被吸收者短——别拿片段当代表。"""
    for absorbed, rep in built["merged_into"].items():
        if absorbed in built["stats"] and rep in built["stats"]:
            assert built["stats"][rep]["n"] >= built["stats"][absorbed]["n"], f"{absorbed}→{rep}"


# ---------------------------------------------------------------- 5
@needs_artifacts
def test_window_pair_p_differs_as_expected(res: dict) -> None:
    """子窗口的 p 必然与被吸收者不同（样本量不同），这是正常而非缺陷。

    这条与第 3 条配对：等长同值 p 必须相同，子窗口 p 必须不同。
    两条一起才能说明「合并类型」被正确区分。
    """
    x = next(i for i in res["p_unchanged"] if i["absorbed"] == "phasep_late")
    assert not x["identical"], "phasep_late 只有 129 个月，p 不可能与 237 个月的相同"
    assert res["stats"]["phasep_late"]["n"] < res["stats"]["phaseo_netBASE"]["n"]


# ---------------------------------------------------------------- 6
def test_m_recomputable_and_variants_reported(built: dict) -> None:
    ids = {v["id"] for v in built["variants"]}
    assert ids == {"IND_m25", "L3AC_m27", "FP_m26", "ALL_m29"}
    m = {v["id"]: v["m"] for v in built["variants"]}
    assert m["IND_m25"] == 25 and m["L3AC_m27"] == 27 and m["FP_m26"] == 26 and m["ALL_m29"] == 29


# ---------------------------------------------------------------- 7
def test_by_subset_of_bh(built: dict) -> None:
    for vid, run in built["runs"].items():
        assert set(run["rejected"]["by"]) <= set(run["rejected"]["bh"]), vid


# ---------------------------------------------------------------- 8
def test_criterion_is_neutral_to_p(built: dict) -> None:
    """判据中立性：把判据应用到一个全部不显著的子集上，合并仍应发生。

    这是本阶段最重要的一条。如果判据只在「显著」的地方生效，它就成了
    「为提高通过数而设的规则」。
    """
    series = {}
    from deepquant.gates import dependence as dp
    from deepquant.gates import citation as ct

    entries = {e["key"]: e for e in ct.load_prereg(REPO / "docs/gates/l3u_prereg.json")["entries"]}
    probe = ["phases_net", "phaset_net", "gate1_d", "gate4_d", "gate5_d"]
    for k in probe:
        try:
            series[k] = dp.load_entry(REPO, entries[k])
        except dp.DependenceError:
            pytest.skip(f"{k} 无法加载")
    # 探针子集里没有一条在任何口径/校正下通过——它们在「结论上都不显著」。
    # phases/phaset 单条 p=0.028 看似显著，但全族看从未通过，正是本判据要处理的典型。
    for k in series:
        assert not any(
            k in run["rejected"][n]
            for run in built["runs"].values()
            for n in ("bonferroni", "holm", "bh", "by")
        ), f"{k} 在某个组合里通过了，不适合做中立性探针"
    part = ind.identical_groups(sorted(series), series)
    assert part["n_merged_away"] >= 1, "不显著的子集里没有发生合并——判据被 p 影响了"
    assert "phaset_net" in built["merged_into"]
    # 更强的证据：判据函数根本不接触 p——它的入参只有序列
    import inspect

    sig = inspect.signature(ind.identical_pairs)
    assert "p" not in {x for x in sig.parameters}, "判据不应接收 p 值"


# ---------------------------------------------------------------- 9
@needs_artifacts
def test_sheet_separates_sensitive_item(res: dict) -> None:
    """仅在某一口径下通过的条目必须单列，不能混进可引集合。"""
    inter = None
    union = set()
    for run in res["runs"].values():
        for n in ("bonferroni", "holm", "bh", "by"):
            s = set(run["rejected"][n])
            inter = s if inter is None else (inter & s)
            union |= s
    text = SHEET_MD.read_text(encoding="utf-8")
    head = text.split("### 2.1")[0]
    for k in union - inter:
        assert k not in head, f"{k} 只在部分口径通过，却进了可引集合"
    sens = text.split("### 2.1")[1] if "### 2.1" in text else ""
    for k in union - inter:
        assert k in sens, f"{k} 应出现在「对口径敏感」一节"


# ---------------------------------------------------------------- 10
@needs_artifacts
def test_zero_effect_claim_not_upgraded() -> None:
    """`phasel_alpha` 的判定是净效应被吸收，不得被写成正面结论。"""
    text = SHEET_MD.read_text(encoding="utf-8")
    assert "SURGE_AVOID_ABSORBED" in text
    # 可引集合必须明确写成 7 条且说明全是正面结论
    assert "这 7 条**全部是正面结论**" in text
    # 禁止把 phasel 计入正面结论的表述：不能出现「8 条……正面」这样的肯定句
    assert "**8 条正面结论**" not in text
    assert "正面结论 8 条" not in text
    # 且必须显式写明「不得改成 8 条正面结论」这条禁令
    assert "不得**被当作正面收益证据" in text or "不能改成「8 条正面结论」" in text


# ---------------------------------------------------------------- 11
def test_prereg_hash_enforced() -> None:
    original = ind.PREREG_SHA
    try:
        ind.PREREG_SHA = "0" * 64
        with pytest.raises(ind.IndependenceError):
            ind.load_prereg(REPO)
    finally:
        ind.PREREG_SHA = original


# ---------------------------------------------------------------- 12
def test_fwer_simulation_matches_theory(built: dict) -> None:
    """FWER 模拟必须落在理论值附近：计 2 个名额 ≈ α/2，计 1 个 ≈ α。"""
    fw = built["fwer"]
    assert abs(fw["reject_rate_m2"] - 0.025) < 0.004, fw["reject_rate_m2"]
    assert abs(fw["reject_rate_m1"] - 0.05) < 0.004, fw["reject_rate_m1"]


# ---------------------------------------------------------------- 13
def test_merge_does_not_touch_p(built: dict) -> None:
    """合并只动名额，不动任何一条的 p/均值/标准误。"""
    for absorbed, rep in built["merged_into"].items():
        if absorbed not in built["stats"] or rep not in built["stats"]:
            continue
        a, r = built["stats"][absorbed], built["stats"][rep]
        if a["n"] == r["n"]:
            assert abs(a["mean_pp"] - r["mean_pp"]) <= ind.TOL
            assert abs(a["se_nw_pp"] - r["se_nw_pp"]) <= ind.TOL
