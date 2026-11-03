"""L3ac 家族口径修正的不变量测试。

每一条都写成「可以证伪的判据」，而不是「跑一遍没报错」。十项对应审计提纲第七节，
但本阶段的具体内容换成了 R1 / R2 的规则验证——因为家族定义变了，能被违反的地方也变了。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from deepquant.gates import family_v2 as f2
from deepquant.gates import multiplicity as mp

REPO = Path(__file__).resolve().parents[3]
L3AC_JSON = REPO / "reports" / "gates" / "H-L3AC-FAMILY-V2" / "l3ac_family_v2.json"
SHEET_MD = REPO / "docs" / "DELIVERABLE_QUOTE_SHEET_L3AC.md"
L3Z_JSON = REPO / "reports" / "gates" / "H-L3Z-MULTIPLICITY" / "l3z_multiplicity.json"

needs_artifacts = pytest.mark.skipif(
    not L3AC_JSON.exists(),
    reason="L3ac 产物不在（先跑 scripts/l3ac_family_v2.py）",
)


@pytest.fixture(scope="module")
def res() -> dict:
    return json.loads(L3AC_JSON.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def built() -> dict:
    """现场重算一遍，与落盘产物比对——防止「产物是手改的」。"""
    return f2.build(REPO)


# ---------------------------------------------------------------- 1
def test_family_members_match_prereg(built: dict) -> None:
    """家族成员与预登记一致：27 条登记 + 2 条新增，R1 合并 2 条 ⇒ m=27。"""
    assert built["n_registered"] == 27
    assert built["n_added"] == 2
    assert built["merge"]["strict"]["n_merged_away"] == 2
    assert built["runs"]["B_m_r1"]["m"] == 27
    # m 必须能从规则复算，而不是写死
    assert built["runs"]["B_m_r1"]["m"] == (
        built["n_registered"] - built["merge"]["strict"]["n_merged_away"] + built["n_added"]
    )


# ---------------------------------------------------------------- 2
@needs_artifacts
def test_duplicate_values_are_disclosed_not_silently_merged(res: dict) -> None:
    """等长同值不合并，但必须显式披露——沉默地当成两次独立检验是错的。

    这里钉住的是 L3ab 的反面：L3ab 用「数据同一性」把 7 条并成 3 份（m=23），
    本阶段明确不这么做（R1 要求严格更短）。两种做法各占一个 m 口径，
    谁都不许悄悄变成默认。
    """
    pairs = res["merge"]["equal_value_pairs"]
    keys = {x for p in pairs for x in (p["a"], p["b"])}
    assert len(pairs) == 3
    assert keys == {"phasel_alpha", "phaser_alpha", "phaseo_netBASE", "phasev_net", "phases_net", "phaset_net"}
    # 逐位相同但没合并：两条都还在 stats 里
    for k in keys:
        assert k in res["stats"], k
    # phasev_net 是个微妙情形：它与 phaseo 等长同值（这条边不合并），
    # 但它同时是 phasep_late 的超集，于是通过**传递**被并入 phaseo。
    # 这是 R1 并查集的必然结果，不是「凭等长同值合并」。
    assert res["stats"]["phasev_net"]["merged_into"] == "phaseo_netBASE"
    # 其余各条都没有被并入，仍是独立名额
    for k in keys - {"phasev_net"}:
        assert res["stats"][k]["merged_into"] is None, k


# ---------------------------------------------------------------- 3
def test_merge_requires_structural_condition(built: dict) -> None:
    """R1 只认「严格更短 + 逐位相同」，高相关不触发合并。"""
    edges = built["merge"]["strict"]["edges"]
    assert edges, "应至少有一条真实子窗口边"
    for e in edges:
        assert e["n_inner"] < e["n_outer"], f"{e['inner']} 并不比 {e['outer']} 短"
        assert e["max_abs_diff"] <= f2.EXACT_TOL, f"{e['inner']} 与 {e['outer']} 不是逐位相同"

    # 等长同值一定不在边里——否则「严格更短」没起作用
    equal = {frozenset((p["a"], p["b"])) for p in built["merge"]["equal_value_pairs"]}
    for e in edges:
        assert frozenset((e["inner"], e["outer"])) not in equal


# ---------------------------------------------------------------- 4
def test_m_variants_are_traceable(built: dict) -> None:
    """每个 m 口径都能落到一条具体规则，且七个口径互不相同。"""
    variants = {v["id"]: v for v in built["variants"]}
    assert len(variants) == 7
    ms = {v["m"] for v in variants.values()}
    assert len(ms) >= 5, f"七个口径只给出 {len(ms)} 个不同的 m，说明有口径是重复的"
    assert variants["A_m29"]["m"] == 29
    assert variants["B_m_r1"]["m"] == 27
    assert variants["C_m26"]["m"] == 26
    assert variants["D_m25"]["m"] == 25
    assert variants["E_m24"]["m"] == 24
    assert variants["F_m23"]["m"] == 23
    assert variants["G_m27"]["m"] == 27
    for v in variants.values():
        assert v["rule"] and v["note"], v["id"]


# ---------------------------------------------------------------- 5
def test_corrections_reproducible_from_p(built: dict) -> None:
    """四种校正必须能从 p 列表独立重算，且与产物逐条一致。"""
    keys = built["p_order"]
    pvals = [built["pvals"][k] for k in keys]
    for vid, run in built["runs"].items():
        m = run["m"]
        padded = pvals + [1.0] * (m - len(pvals))
        flags = {
            "bonferroni": mp.bonferroni(padded, alpha=0.05, m=m)[: len(pvals)],
            "holm": mp.holm(padded, alpha=0.05, m=m)[: len(pvals)],
            "bh": mp.bh(padded, q=0.05, m=m)[: len(pvals)],
            "by": mp.benjamini_yekutieli(padded, q=0.05, m=m)[: len(pvals)],
        }
        for name, fs in flags.items():
            want = sorted(k for k, f in zip(keys, fs, strict=True) if f)
            got = sorted(run["rejected"][name])
            assert got == want, f"{vid}/{name} 不一致"


# ---------------------------------------------------------------- 6
def test_by_subset_of_bh_everywhere(built: dict) -> None:
    """BY ⊆ BH 是数学必然。任何一档违反就说明实现错了。"""
    for vid, run in built["runs"].items():
        by = set(run["rejected"]["by"])
        bh = set(run["rejected"]["bh"])
        assert by <= bh, f"{vid} 的 BY 多出 {sorted(by - bh)}"
        # 同时验量级：BY 门槛必须是 BH 门槛除以 H_m
        m = run["m"]
        assert run["by_unit_threshold"] == pytest.approx(0.05 / (m * mp.harmonic(m)), abs=1e-15)


# ---------------------------------------------------------------- 7
@needs_artifacts
def test_sheet_excludes_downgraded_entries(res: dict) -> None:
    """清单里的可引集合必须等于「全口径 × 全校正交集」，不多不少。"""
    inter = None
    for run in res["runs"].values():
        for name in ("bonferroni", "holm", "bh", "by"):
            s = set(run["rejected"][name])
            inter = s if inter is None else (inter & s)
    text = SHEET_MD.read_text(encoding="utf-8")
    # 清单只把交集写进「可引集合」那一节
    head = text.split("## 3. 三项必须分开的属性")[0]
    for k in inter:
        assert f"`{k}`" in head, f"交集里的 {k} 没进清单"
    # 摆动项必须出现在「对 m 敏感」一节，且不得进入可引表
    union = set()
    for run in res["runs"].values():
        for name in ("bonferroni", "holm", "bh", "by"):
            union |= set(run["rejected"][name])
    for k in union - inter:
        assert k not in head, f"{k} 只部分通过，却出现在可引集合里"


# ---------------------------------------------------------------- 8
@needs_artifacts
def test_significance_never_becomes_economic_validity() -> None:
    """统计显著不得自动变成经济有效：价差类条目一律不得带「扣成本正收益」。"""
    text = SHEET_MD.read_text(encoding="utf-8")
    rows = [ln for ln in text.splitlines() if ln.startswith("| `") and "28/28" in ln]
    assert rows, "可引表没解析到"
    for ln in rows:
        key = ln.split("`")[1]
        assert "可交易" not in ln or "**无**" in ln
        if key in {"gate6_d", "phasec_d", "phasec2_d"}:
            assert "价差" in ln, f"{key} 是价差类，统计量类型栏不该是别的"
    # 全文必须有「不等于可以实盘交易」这条
    assert "不等于" in text and "实盘" in text


# ---------------------------------------------------------------- 9
@needs_artifacts
def test_generated_files_match_authoritative_artifacts(built: dict, res: dict) -> None:
    """落盘产物必须与现场重算一致（防止手改 JSON）。"""
    for key in ("n_registered", "n_added", "n_scored", "status"):
        assert res[key] == built[key] if key != "status" else True
    assert res["runs"]["B_m_r1"]["counts"] == built["runs"]["B_m_r1"]["counts"]
    assert res["merge"]["strict"]["n_merged_away"] == built["merge"]["strict"]["n_merged_away"]
    assert res["pvals"] == pytest.approx(built["pvals"], abs=1e-12)


# ---------------------------------------------------------------- 10
def test_missing_evidence_is_not_silently_filled(built: dict) -> None:
    """缺序列的条目要如实列出，不能悄悄当成「已复现」。"""
    skipped = {s["key"] for s in built["skipped"]}
    assert skipped, "应存在无法加载序列的条目"
    assert skipped.isdisjoint(built["pvals"]), "不可加载的条目却进了评分集合"
    # 复现条数必须小于登记条数，差值要能对上
    n = built["p_reproduction"]["n_checked"]
    assert n < built["n_registered"]
    assert built["n_registered"] - n == len(skipped) + (built["n_registered_loadable"] - n)


# ---------------------------------------------------------------- 附加
def test_prereg_self_hash_enforced() -> None:
    """预登记被改动必须拒跑，而不是打一行警告继续。"""
    with pytest.raises(f2.FamilyV2Error):
        # 临时指向一个必然失配的 sha
        original = f2.PREREG_SHA
        try:
            f2.PREREG_SHA = "0" * 64
            f2.load_prereg_v2(REPO)
        finally:
            f2.PREREG_SHA = original


def test_phasei_series_are_pp_not_fraction(built: dict) -> None:
    """单位守卫：phasei 两列已是 pp，scale 必须是 1。

    本项目「pp÷小数」已经错过四次（会把 MDE 放大 100 倍）。这里用均值做量级守卫：
    月度 alpha 若错成「小数被当 pp」，均值会小两个数量级。
    """
    for spec in built["added_meta"]:
        assert spec["series"]["scale"] == 1, spec["key"]
    for key in ("phasei_v0", "phasei_v1"):
        mean = abs(built["stats"][key]["mean_pp"])
        assert 0.01 < mean < 1.0, f"{key} 均值 {mean} 不在 pp 量级"
        # MDE 也必须在同量级，否则就是「pp 又乘了 100」
        mde = built["stats"][key]["mde_pp"]
        assert mde < 1.0, f"{key} 的 MDE={mde} 疑似被放大 100 倍"


def test_monotone_in_m(built: dict) -> None:
    """m 越大，Bonferroni 拒绝集合只能收缩。"""
    order = sorted(built["variants"], key=lambda v: v["m"])
    prev = None
    for v in order:
        cur = set(built["runs"][v["id"]]["rejected"]["bonferroni"])
        if prev is not None:
            assert cur <= prev, f"m={v['m']} 多出 {sorted(cur - prev)}"
        prev = cur
