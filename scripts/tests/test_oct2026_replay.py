"""PA-OCT2026-1 回测产物的独立算术闸门。

⚠️ 期望值全部来自产物 CSV 的独立重算或显式公式，不调用被测回测函数
   （L3ac M6 / L3ae M3：闸门禁止调用被测函数）。
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
OUT = REPO / "research/stock_pick_2026-10-25/oct2026_out/cd5d016b8c9f"
PREREG = REPO / "research/stock_pick_2026-10-25/SP5_OCT2026_DOWNTURN_PREREG.md"
SIDECAR = PREREG.with_suffix(".md.sha256")
CAPITAL = 100000.0
RATE = 0.00051


def _csv(name):
    with (OUT / name).open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _metrics():
    return json.loads((OUT / "metrics.json").read_text(encoding="utf-8"))


def test_prereg_frozen_and_matches_sidecar():
    expected = SIDECAR.read_text(encoding="utf-8").split()[0]
    assert hashlib.sha256(PREREG.read_bytes()).hexdigest() == expected
    assert _metrics()["prereg_sha256"] == expected
    assert PREREG.read_bytes().count(b"\r\n") == 0


def test_all_products_explicit_lf_and_json_strict():
    for p in OUT.iterdir():
        assert p.read_bytes().count(b"\r\n") == 0, p.name
    m = _metrics()   # 含 NaN 的非法 JSON 会在部分解析器上失败
    assert m["conclusion_tag"] == "DESCRIPTIVE_SINGLE_MONTH"
    assert m["statistical_verdict"] == "NOT_TESTED_SINGLE_PATH"
    assert m["maturity_level"] == "EXPLORATORY_ONLY"
    for tag in ("A", "B"):
        assert m["arms"][tag]["t_net"] is None


def test_asof_guard_product_ok():
    d = json.loads((OUT / "asof_guard.json").read_text(encoding="utf-8"))
    assert d["verdict"] == "ASOF_OK"
    assert d["max_date_seen"] == "20261030"
    assert d["future_rows"] == 0
    assert d["n_files"] == 5


def test_nav_recomputed_from_csv_independently():
    m = _metrics()
    for tag, w in (("A", 0.25), ("B", 0.20)):
        rows = _csv("nav_daily_%s.csv" % tag)
        assert len(rows) == 17
        assert rows[0]["date"] == "20261008" and rows[-1]["date"] == "20261030"
        r = float(rows[-1]["nav_gross"]) / CAPITAL - 1.0
        assert abs(r - m["arms"][tag]["r_gross"]) < 1e-9
        mdd = min(float(x["dd"]) for x in rows)
        assert abs(mdd - m["arms"][tag]["mdd"]) < 1e-9
        # 逐日分量恒等式：nav = cash + stock_value
        # ⚠️ 容差按产物存储精度定：三个量各存 6 位小数 ⇒ 最大舍入 3×0.5e-6=1.5e-6；
        #    写 1e-6 会永远不可满足（项目 §4.8 已复发多次的判据错误）。
        TOL_COMPONENTS = 2e-6
        for x in rows:
            assert abs(float(x["nav_gross"])
                       - float(x["cash"]) - float(x["stock_value"])) < TOL_COMPONENTS
        # 首日必须不含成本、等于本金
        assert abs(float(rows[0]["nav_gross"]) - CAPITAL) < 1e-6


def test_p1_identity_from_independent_arithmetic():
    m = _metrics()
    a = _csv("nav_daily_A.csv")
    b = _csv("nav_daily_B.csv")
    r_a = float(a[-1]["nav_gross"]) / CAPITAL - 1.0
    r_b = float(b[-1]["nav_gross"]) / CAPITAL - 1.0
    r_cash = float(a[-1]["cash"]) / (0.75 * CAPITAL) - 1.0
    r_stock = float(b[-1]["px_index"]) - 1.0
    delta = r_b - r_a
    identity = (0.25 - 0.20) * (r_cash - r_stock)
    assert abs(delta - identity) < 1e-9
    assert abs(delta - m["predictions"]["P1"]["delta_pp"] / 100.0) < 1e-9
    assert delta > 0


def test_cost_matches_explicit_formula_not_the_function():
    m = _metrics()
    for tag, w in (("A", 0.25), ("B", 0.20)):
        expected = 2.0 * w * RATE * 100.0
        assert abs(m["arms"][tag]["cost_pp_month"] - expected) < 1e-12
        net = m["arms"][tag]["r_gross"] * 100.0 - expected
        assert abs(m["arms"][tag]["net_pp_month"] - net) < 1e-9


def test_p3_literal_refutation_is_preserved():
    """缺陷记录段：按预登记负值字面 P3 被反驳，不得悄悄放宽判据。"""
    p = _metrics()["predictions"]["P3"]
    assert p["B_less_than_A"] is False
    assert p["B_gt_minus5pct"] is True
    # 幅度口径确实相反（如实补记，但不改变判定）
    assert abs(p["mdd_B_pct"]) < abs(p["mdd_A_pct"])


def test_mde_not_estimated_and_borrowed_is_labelled():
    m = _metrics()["mde"]
    assert m["this_experiment"] == "MDE_NOT_ESTIMABLE_FROM_THIS_EXPERIMENT"
    assert m["borrowed_not_estimated_pp"]["L3r"] == 0.926
    assert m["borrowed_not_estimated_pp"]["L3t_range"] == [0.087, 0.955]
    assert m["daily_noise_not_mde_pp"] > 0
    assert _metrics()["predictions"]["P4"]["verdict"] == "NOT_TESTABLE_SINGLE_PATH"


def test_old_nav_independent_crosscheck_is_tight():
    c = _metrics()["old_nav_crosscheck"]
    assert c["n_rows"] == 7
    assert c["max_abs_diff"] < 0.1, c["max_abs_diff"]


def test_min_commission_effect_is_reported_not_hidden():
    e = _metrics()["lot_execution_A"]
    assert e["fee_with_min_commission"] == 25.0
    assert e["ratio"] > 1.0
    assert e["buy_amount"] == 20971.0
    # 整手实际仓位低于 25% 目标仓，属浪费，不得当「更好」
    assert e["buy_amount"] / CAPITAL < 0.25


def test_market_crosscheck_and_defect_boundary():
    m = _metrics()
    assert m["market"]["n_pool_valid"] > 5000
    assert abs(m["market"]["r_pool_samewindow_pct"]
               - m["market"]["r_pool_exec_crosscheck_pct"]) < 0.2
    assert max(abs(x["qfq_pct"] - x["exec_pct"])
               for x in m["market"]["spot_check"]) < 0.01
    assert sorted(m["config_diff_keys"]) == ["rebalance_rule", "stock_weight"]
