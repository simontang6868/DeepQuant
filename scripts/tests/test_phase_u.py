"""阶段 U 单测：NW 滞后审计的语义、单位铁律、闸门逻辑与判定规则。

按项目铁律，**先证明检查器能抓到故障，再相信它变绿**。
变异覆盖：pp 未除 100 / segment_drop 不剔月 / 翻转检测跳过 L=0 /
判定忽略 A 类 / 独立 NW 权重写错 / iid 口径被换成 NW L=0 /
U14 精确预测被换成模板 / L 网格缺 L=0。
"""

from __future__ import annotations

import importlib.util
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import run_phase_u_nw_lag_audit as U  # noqa: E402


def _spec(sid: str) -> dict:
    return next(s for s in U.SERIES if s["id"] == sid)


# ============================================================ 序列表完整性
def test_series_table_frozen_sixteen_unique_classes():
    assert len(U.SERIES) == 16
    ids = [s["id"] for s in U.SERIES]
    assert len(set(ids)) == 16
    cls = {}
    for s in U.SERIES:
        cls[s["cls"]] = cls.get(s["cls"], 0) + 1
        assert set(s) >= {"id", "name", "csv", "col", "unit", "est",
                          "filt", "n", "t3", "mean_pp", "cls"}
        assert s["unit"] in ("dec", "pp")
        assert s["est"] in ("nw3", "iid")
    assert cls == {"A": 8, "B": 4, "C": 4}


def test_only_u1_uses_iid_record_estimator():
    """更正说明钉住：Gate 6 记录口径是 iid，其余 15 条是 NW(3)。"""
    iid = [s["id"] for s in U.SERIES if s["est"] == "iid"]
    assert iid == ["U1"]


def test_lag_grid_and_crit_frozen():
    assert U.LAG_GRID == (0, 1, 2, 3, 4, 5, 6)
    assert U.T_CRIT == 1.96


# ============================================================ 单位与加载
def test_to_decimal_pp_divided_dec_not():
    rows = [{"signal_date": "2020-01-31", "v": "0.23"}]
    assert U.to_decimal(rows, "v", "pp") == [0.0023]
    assert U.to_decimal(rows, "v", "dec") == [0.23]


def test_to_decimal_rejects_nan():
    """NaN 会让 newey_west 静默丢月 → 必须显式炸，不能静默。"""
    rows = [{"signal_date": "2020-01-31", "v": "nan"}]
    with pytest.raises(SystemExit):
        U.to_decimal(rows, "v", "pp")


def test_apply_filter_full_segment_and_drop():
    rows = [
        {"signal_date": "2015-12-31", "segment": "2007-2015"},
        {"signal_date": "2016-01-31", "segment": "2016-2026"},
        {"signal_date": "2016-02-29", "segment": "2016-2026"},
        {"signal_date": "2020-02-28", "segment": "2016-2026"},
        {"signal_date": "2026-09-30", "segment": "2016-2026"},
    ]
    assert len(U.apply_filter(rows, ("full",))) == 5
    assert len(U.apply_filter(rows, ("segment", "2016-2026"))) == 4
    got = U.apply_filter(rows, ("segment_drop", "2016-2026", ("2016-01", "2020-02")))
    # 前缀匹配：2016-01-31 与 2020-02-28 都被剔
    assert [r["signal_date"] for r in got] == ["2016-02-29", "2026-09-30"]


# ============================================================ 两套 NW 实现
def test_independent_nw_matches_canonical_negative_autocorr():
    """负自相关（本项目已知结构）：两套实现必须在全网格一致。"""
    rng = __import__("random").Random(7)
    x = []
    prev = 0.0
    for _ in range(200):
        prev = -0.5 * prev + rng.gauss(0.0, 1.0)
        x.append(prev)
    for lag in U.LAG_GRID:
        rc = U.nw_canonical(x, lag)
        m, se, t = U.nw_independent(x, lag)
        assert abs(t - rc["t_nw"]) <= U.G2_TOL
        assert abs(se - rc["se_nw"]) <= 1e-15


def test_independent_nw_matches_canonical_mixed_autocorr():
    """混合自相关（审计实测 U1/U4 就非单调）：同样必须一致。"""
    rng = __import__("random").Random(11)
    x = [rng.gauss(0.0, 1.0) for _ in range(150)]
    for lag in U.LAG_GRID:
        rc = U.nw_canonical(x, lag)
        _, _, t = U.nw_independent(x, lag)
        assert abs(t - rc["t_nw"]) <= U.G2_TOL


def test_iid_t_is_stdev_over_sqrt_n_not_nw():
    """钉住 ddof 差异：NW(L=0) 用 /n，Gate 6 的 iid 用 /(n−1)，两者不同。"""
    x = [0.01, -0.02, 0.03, 0.005, -0.01, 0.02, -0.005, 0.015]
    n = len(x)
    m = sum(x) / n
    sd = __import__("statistics").stdev(x)
    assert U.iid_t(x) == pytest.approx(m / (sd / math.sqrt(n)), rel=1e-12)
    nw0 = U.nw_canonical(x, lag=0)["t_nw"]
    assert abs(U.iid_t(x) - nw0) > 1e-6   # 两者不是同一个数


# ============================================================ 闸门逻辑
def test_g1_gate_catches_wrong_recorded_t():
    spec = dict(_spec("U6"))
    spec["t3"] = spec["t3"] + 1e-3          # 超出 G1_TOL=1e-4
    with pytest.raises(SystemExit):
        U.profile_series(spec)


def test_g1_gate_catches_wrong_recorded_mean_even_if_t_right():
    """单位铁律：t 尺度无关，错 100 倍的均值只有同时校量级才抓得到。"""
    spec = dict(_spec("U2"))
    spec["mean_pp"] = spec["mean_pp"] * 100.0
    with pytest.raises(SystemExit):
        U.profile_series(spec)


def test_g1_gate_u1_passes_under_iid_and_would_fail_under_nw3():
    """U1 的记录值是 iid 口径：按 iid 复现必过；按 NW(3) 比必挂。"""
    U.profile_series(_spec("U1"))           # 不炸
    spec = dict(_spec("U1"))
    spec["est"] = "nw3"
    with pytest.raises(SystemExit):
        U.profile_series(spec)


def test_profile_series_row_count_mismatch_raises():
    spec = dict(_spec("U12"))
    spec["n"] = 129                          # 真值 123
    with pytest.raises(SystemExit):
        U.profile_series(spec)


# ============================================================ G3 恒等式
def test_gate_g3_passes_on_real_data_and_pins_shift():
    rows = U.load_rows(U.S_CSV)
    g3 = U.gate_g3(rows)
    assert g3["const_shift"]["value"] == pytest.approx(-0.01821185, abs=1e-9)
    assert g3["const_shift"]["max_minus_min"] <= U.G3_TOL
    assert g3["se_nw_max_abs_diff_U11_U14"] <= U.G3_TOL


def test_gate_g3_rejects_non_constant_shift():
    rows = [{"signal_date": f"2016-{m:02d}-28", "segment": "2016-2026",
             "net_S_3000w": 0.1 + 0.01 * m, "net_BASE_3000w": 0.2}
            for m in range(1, 13)]
    with pytest.raises(SystemExit):
        U.gate_g3(rows)


# ============================================================ 判定与预测
def test_verdict_of_three_tiers():
    assert U.verdict_of({}, {}) == "NW_LAG_ROBUST_ALL"
    assert U.verdict_of({"U11": [0]}, {"U11": "B"}) == "NW_LAG_SENSITIVITY_CONFIRMED"
    assert U.verdict_of({"U15": [5]}, {"U15": "C"}) == "NW_LAG_SENSITIVITY_CONFIRMED"
    assert U.verdict_of({"U6": [0], "U11": [0]},
                        {"U6": "A", "U11": "B"}) == "NW_LAG_SENSITIVITY_LOAD_BEARING"


def test_predicted_profile_u14_is_exact_identity_on_real_data():
    """恒等式预测必须逐位等于实测（这是预测里唯一'精确'的一条）。"""
    u11 = U.profile_series(_spec("U11"))["profile"]
    p = U.predicted_profile(_spec("U14"), u11)
    actual = U.profile_series(_spec("U14"))["profile"]
    for L in U.LAG_GRID:
        assert p["t"][L] == pytest.approx(actual[L]["t_nw"], abs=1e-12)


def test_predicted_profile_template_uses_frozen_ratios():
    u11 = U.profile_series(_spec("U11"))["profile"]
    p = U.predicted_profile(_spec("U6"), u11)
    assert p["kind"] == "template"
    r3 = U.T1C_KNOWN[3]
    for L in U.LAG_GRID:
        assert p["t"][L] == pytest.approx(_spec("U6")["t3"] * U.T1C_KNOWN[L] / r3)


def test_predicted_profile_u11_is_anchor_not_prediction():
    u11 = U.profile_series(_spec("U11"))["profile"]
    p = U.predicted_profile(_spec("U11"), u11)
    assert p["kind"] == "anchor"


# ============================================================ 实测结果钉住
def test_measured_flips_match_frozen_expectations():
    """输入有 sha256 校验 → 结果确定；把关键翻转钉住，防回归被悄悄改掉。"""
    prof = {sid: U.profile_series(_spec(sid))["flip_lags"]
            for sid in ("U1", "U6", "U11", "U12", "U14", "U15", "U16")}
    assert prof["U1"] == []      # A 类稳健
    assert prof["U6"] == []      # A 类稳健
    assert prof["U11"] == [0, 1]
    assert prof["U12"] == [4, 5, 6]
    assert prof["U14"] == [0]
    assert prof["U15"] == [5, 6]
    assert prof["U16"] == []


def test_u11_profile_reproduces_phase_t_t1c_table():
    prof = U.profile_series(_spec("U11"))["profile"]
    for L in U.LAG_GRID:
        assert abs(prof[L]["t_nw"] - U.T1C_KNOWN[L]) <= U.T1C_TOL


def test_u12_drops_exactly_the_six_apriori_months():
    res = U.profile_series(_spec("U12"))
    months = {d[:7] for d in res["dates"]}
    for m in ("2016-01", "2016-02", "2020-02", "2020-03", "2024-01", "2024-02"):
        assert m not in months
    assert len(res["dates"]) == 123


def test_profile_series_deterministic():
    a = U.profile_series(_spec("U6"))
    b = U.profile_series(_spec("U6"))
    for L in U.LAG_GRID:
        assert a["profile"][L]["t_nw"] == b["profile"][L]["t_nw"]


def test_monotonicity_is_not_assumed():
    """审计实测 U1/U4 非单调——描述性读数必须允许为假。"""
    prof = U.profile_series(_spec("U4"))["profile"]
    ts = [prof[L]["t_nw"] for L in U.LAG_GRID]
    assert ts != sorted(ts)      # U4：3.4088 → 3.3891 → … 非单调


# ============================================================ 预登记与源完整性
def test_prereg_and_inputs_verify_clean():
    U.verify_prereg()             # 不炸即过
    got = U.verify_inputs()
    assert len(got) == 8


def test_sha256_file_matches_sha256sum():
    import hashlib
    b = open(U.PREREG_MD, "rb").read()
    assert U.sha256_file(U.PREREG_MD) == hashlib.sha256(b).hexdigest()
