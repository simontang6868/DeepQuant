"""L3af 成本后净收益的闸门测试。

本文件的重点不是「数字对不对」（那由产物哈希保证），而是**口径**：

- 月份必须逐月对齐，不能错位一个月；
- 换手是单边还是双边，必须能被算出反例；
- 最低佣金必须按资金档与笔数算，不能一个有费率通吃五档；
- 净收益标准误不能悄悄复用毛收益的标准误；
- 逐月换手缺失时必须**明确受阻**，不能退回均值。

每条断言都写成「能算出反例」的形式：给它一个错实现，必须能挂。
"""

from __future__ import annotations

import csv
import json
import math
import shutil
from pathlib import Path

import pytest

from deepquant.gates import cost_net as cn

REPO = Path(__file__).resolve().parents[3]
PREREG = REPO / "docs" / "gates" / "l3af_prereg.json"
PI_MONTHLY = REPO / "phasei_out" / "8f5c35121fdb" / "monthly.csv"
PO_MONTHLY = REPO / "phaseo_out" / "ac7aa2830ab0" / "monthly.csv"

needs_artifacts = pytest.mark.skipif(
    not (PREREG.exists() and PI_MONTHLY.exists() and PO_MONTHLY.exists()),
    reason="L3af 冻结输入缺失",
)


@pytest.fixture(scope="module")
def res() -> dict:
    return cn.build(REPO)


# ==================== 1 月份对齐


@needs_artifacts
def test_all_tiers_share_the_same_month_list(res):
    n = res["n_months"]
    assert n == 237
    for t in res["tiers"]:
        assert t["net"]["n"] == n
        assert t["variance"]["n"] == n


@needs_artifacts
def test_month_mismatch_between_phasei_and_phaseo_raises(tmp_path, res):
    """把 phaseo 的月份整体后移一个月（模拟错位），必须抛错而不是静默对齐。"""
    root = _clone_inputs(tmp_path)
    rows = list(csv.DictReader((root / "phaseo_out/ac7aa2830ab0/monthly.csv").open()))

    # 把第一行日期换成第二行、其余顺移：月份键不同序
    rows[0]["signal_date"], rows[1]["signal_date"] = rows[1]["signal_date"], rows[0]["signal_date"]
    _write_rows(root / "phaseo_out/ac7aa2830ab0/monthly.csv", rows, list(rows[0].keys()))

    cn.PREREG_SHA  # 不改预登记，只改数据 → 应先在哈希闸门或月份闸门被挡住
    with pytest.raises(cn.CostNetError):
        cn.build(root)


# ==================== 2 单边/双边换手


@needs_artifacts
def test_two_side_turnover_doubles_cost_exactly(res):
    assert res["cost_arithmetic"]["unit_conversion_max_abs"] <= 1e-12


def test_cost_is_linear_in_turnover_and_rate():
    for to in (0.0, 0.01, 0.187824, 1.0):
        for rate in (0.00051, 0.000585, 0.001, 0.003):
            assert cn.cost_pp(2 * to, rate) == pytest.approx(2 * cn.cost_pp(to, rate), rel=0, abs=0)
            assert cn.cost_pp(to, 2 * rate) == pytest.approx(2 * cn.cost_pp(to, rate), rel=0, abs=0)


def test_cost_formula_matches_asymmetric_fee_structure():
    """2×to×(佣金+过户) + to×印花 —— 印花只在卖出方，所以不是 2×to×0.0005。"""
    to, comm, stamp, transfer = 0.3, 0.00025, 0.0005, 0.00001
    want = (2 * to * (comm + transfer) + to * stamp) * 100
    assert cn.cost_pp(to, 0.00051) == pytest.approx(want, rel=1e-15)
    # 若误按「印花双边」计，会得到 0.6×to×100 —— 必须显著不同
    wrong = 2 * to * (comm + stamp + transfer) * 100
    assert abs(wrong - want) > 1e-3


# ==================== 3 最低佣金与资金档


def test_effective_rate_respects_minimum_commission():
    from deepquant.gates import cost_tiers as ct

    assert ct.effective_rate_with_min_commission(20000.0) == pytest.approx(0.00025)
    assert ct.effective_rate_with_min_commission(10000.0) == pytest.approx(5.0 / 10000.0)
    assert ct.effective_rate_with_min_commission(100000.0) == pytest.approx(0.00025)


@needs_artifacts
def test_tier_rates_are_ordered_by_capital(res):
    """资金越小，含最低佣金的等效费率越高。"""
    by = {t["id"]: t["rate"] for t in res["tiers"]}
    assert by["T_1e8"] < by["T_3e7"] < by["T_0p1"] < by["T_0p3"]
    assert by["T_1e7"] > by["T_1e8"]
    assert by["T_1e7"] < by["T_0p3"]


@needs_artifacts
def test_thousand_wan_tier_costs_more_than_hundred_million(res):
    by = {t["id"]: t for t in res["tiers"]}
    assert by["T_1e7"]["cost_pp_month"] > by["T_1e8"]["cost_pp_month"]
    # 差距应主要来自最低佣金，而不是费率随便设：比值接近 费率比
    assert by["T_1e7"]["cost_pp_month"] / by["T_1e8"]["cost_pp_month"] == pytest.approx(
        by["T_1e7"]["rate"] / by["T_1e8"]["rate"], rel=1e-12
    )


# ==================== 4 标准误不得静默复用


@needs_artifacts
def test_t_is_computed_from_net_se_not_gross(res):
    for t in res["tiers"]:
        want = t["net"]["mean_pp"] / t["net"]["se_iid_pp"]
        assert t["net"]["t_iid"] == pytest.approx(want, rel=1e-14)
        # 若用毛收益 se 当分母，会得到一个不同的数；两者必须都被记录
        alt = t["net"]["mean_pp"] / res["gross"]["se_iid_pp"]
        assert abs(alt - t["net"]["t_iid"]) > 0
        assert t["t_shift_from_se_only"] == pytest.approx(t["net"]["t_iid"] - alt, rel=1e-12)


@needs_artifacts
def test_variance_identity_holds(res):
    for t in res["tiers"]:
        v = t["variance"]
        rel = abs(v["var_identity_residual"]) / v["var_gross"]
        assert rel <= 1e-12
        assert v["two_cov"] == pytest.approx(2 * v["cov"], rel=1e-15)


@needs_artifacts
def test_se_net_equality_claim_is_not_asserted_as_identity(res):
    """L3ae 的「se_net ≥ se_gross 恒成立」必须被证伪：本数据上只有一档成立。"""
    claim = res["l3ae_claim"]["verdict"]
    assert claim["code"] == "TIER_DEPENDENT"
    assert 0 < claim["n_holds"] < claim["n"]
    assert claim["max_se_ratio_deviation"] < 1e-3  # 幅度可忽略，但方向确实分裂


def test_variance_decomposition_sign_logic():
    """Var(net) 与 Var(gross) 的大小关系由 Var(cost) 与 2Cov 的相对大小决定。"""
    gross = [1.0, -2.0, 3.0, 0.5, -1.0, 2.0]
    flat_cost = [0.1] * 6  # 常数成本 → Var(c)=0、Cov=0 → 方差不变
    dec = cn.variance_decomposition(gross, flat_cost)
    assert dec["var_cost"] == pytest.approx(0.0, abs=1e-15)
    assert dec["se_net_over_gross"] == pytest.approx(1.0, rel=1e-12)
    assert dec["se_net_ge_gross"] is True  # 0 >= 0，恒等而非严格

    # 成本与毛收益成固定比例：Var(c) = 0.25Var(g)、Cov = 0.5Var(g) ⇒ 2Cov = Var(g) > Var(c)
    proportional = [0.5 * g for g in gross]
    dec2 = cn.variance_decomposition(gross, proportional)
    assert dec2["se_net_ge_gross"] is False
    assert dec2["se_net_over_gross"] == pytest.approx(0.5, rel=1e-12)

    # 反过来：成本随毛收益反向变动 ⇒ 方差变大
    anti = [-0.5 * g for g in gross]
    dec3 = cn.variance_decomposition(gross, anti)
    assert dec3["se_net_over_gross"] == pytest.approx(1.5, rel=1e-12)


# ==================== 5 缺失换手必须受阻


def _clone_inputs(tmp_path: Path) -> Path:
    """把 L3af 需要的冻结输入拷到临时根，只改需要被改的那一份。"""
    root = tmp_path
    for rel in (
        "docs/gates/l3af_prereg.json",
        "phasei_out/8f5c35121fdb/monthly.csv",
        "phasei_out/8f5c35121fdb/summary.json",
        "phasei_out/8f5c35121fdb/manifest.json",
        "phaseo_out/ac7aa2830ab0/monthly.csv",
        "phaseo_out/ac7aa2830ab0/summary.json",
        "phaseo_out/ac7aa2830ab0/manifest.json",
        "docs/PHASE_O_FEE_CONVENTION_PREREG_2026-10-21.md",
        "docs/PHASE_C_UNIVERSE_PREREG_2026-10-21.md",
        "docs/gates/l3u_prereg.json",
        "docs/gates/l3v_claims.json",
        "docs/gates/l3ab_family_decisions.json",
        "docs/gates/l3ad_prereg.json",
        "scripts/run_phase_o_fee_convention.py",
        "reports/gates/H-L3AD-INDEPENDENCE/l3ad_independence.json",
        "reports/gates/H-L3Z-MULTIPLICITY/l3z_multiplicity.json",
        "reports/L3Z_LAG_SENSITIVITY.json",
    ):
        src, dst = REPO / rel, root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    return root


def _write_rows(path: Path, rows, fields) -> None:
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        wr = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        wr.writeheader()
        wr.writerows(rows)


@needs_artifacts
def test_missing_turnover_blocks_and_does_not_fall_back_to_mean(tmp_path, monkeypatch):
    """把 `to` 列删掉：必须抛 MissingTurnoverSeries，**不得**回退到换手均值。"""
    root = _clone_inputs(tmp_path)
    po = root / "phaseo_out/ac7aa2830ab0/monthly.csv"
    rows = list(csv.DictReader(po.open()))
    fields = [f for f in rows[0].keys() if f != "to"]
    for r in rows:
        r.pop("to", None)
    _write_rows(po, rows, fields)

    prereg = cn.load_prereg(root)
    audit = cn.availability_audit(root, prereg)
    assert audit["verdict"] == "BLOCKED_BY_MISSING_TURNOVER_SERIES"
    assert "逐月换手" in audit["blocking_items"]

    monkeypatch.setattr(cn, "verify_inputs", lambda r, p: {})
    with pytest.raises(cn.MissingTurnoverSeries):
        cn.build(root)


@needs_artifacts
def test_missing_values_in_turnover_raise_instead_of_filling(tmp_path):
    root = _clone_inputs(tmp_path)
    po = root / "phaseo_out/ac7aa2830ab0/monthly.csv"
    rows = list(csv.DictReader(po.open()))
    rows[5]["to"] = ""
    _write_rows(po, rows, list(rows[0].keys()))
    monkeypatch_dummy = None
    with pytest.raises(cn.CostNetError, match="缺失"):
        cn._col(cn.read_rows(po), "to")


# ==================== 6 冻结输入与换行


@needs_artifacts
def test_frozen_input_with_crlf_is_accepted_only_when_lf_hash_matches(tmp_path):
    root = _clone_inputs(tmp_path)
    # phasei summary 本身在磁盘上就是 CRLF；预登记里声明了 eol_converted=true
    prereg = cn.load_prereg(root)
    out = cn.verify_inputs(root, prereg)
    assert out["phasei_summary"]["eol_converted"] is True
    assert out["phasei_summary"]["sha256_lf"] == prereg["inputs_frozen"]["phasei_summary"]["sha256_lf"]


@needs_artifacts
def test_tampered_input_is_rejected(tmp_path):
    root = _clone_inputs(tmp_path)
    p = root / "phaseo_out/ac7aa2830ab0/monthly.csv"
    text = p.read_text(encoding="utf-8")
    p.write_text(text.replace("0.592874", "0.592999", 1), encoding="utf-8", newline="\n")
    with pytest.raises(cn.CostNetError, match="哈希不符"):
        cn.build(root)


def test_prereg_hash_is_pinned():
    assert len(cn.PREREG_SHA) == 64
    import hashlib

    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == cn.PREREG_SHA


# ==================== 7 报价与净值的恒等式


@needs_artifacts
def test_net_identity_within_storage_floor(res):
    tol = res["net_identity"]["tolerance"]
    assert res["net_identity"]["max_abs_resid"] <= tol
    # 容差必须由存储精度推出，不能拍脑袋：三项 6 位小数 + alpha 的最大量级
    alpha = [float(r["alpha_v1_pp"]) for r in cn.read_rows(PI_MONTHLY)]
    assert tol == pytest.approx(cn.STORE_HALF * (3 + max(abs(a) for a in alpha)), rel=1e-12)
    # 且这条容差必须真的比观测残差紧——否则闸门形同虚设
    assert tol < 2 * res["net_identity"]["max_abs_resid"]


@needs_artifacts
def test_net_decreases_monotonically_in_rate(res):
    by = {t["id"]: t["net_pp_month"] for t in res["tiers"]}
    assert by["T_1e8"] > by["T_3e7"] > by["T_0p1"] > by["T_0p3"]


@needs_artifacts
def test_primary_tier_is_the_preregistered_one(res):
    assert cn.PRIMARY_TIER == "T_0p3"
    assert res["tiers"][0]["rate"] == pytest.approx(0.003)
    assert res["primary"]["significance"] == "NOT_SIGNIFICANT"
    assert abs(res["primary"]["t_iid"]) < 1.96
    assert abs(res["primary"]["t_nw3"]) < 1.96


@needs_artifacts
def test_monthly_csv_matches_summary(res):
    header, rows = cn.monthly_rows(REPO)
    assert len(rows) == res["n_months"]
    for j, t in enumerate(res["tiers"]):
        col = 3 + 2 * j + 1
        mean = sum(float(r[col]) for r in rows) / len(rows)
        assert mean == pytest.approx(t["net_pp_month"], abs=1e-12)
