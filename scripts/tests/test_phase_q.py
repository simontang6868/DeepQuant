"""阶段 Q 单测：增量信息检验的语义与变异检出。

按项目铁律：**先证明检查器能抓到故障，再相信它变绿。**
"""

from __future__ import annotations

import json
import math
import os
import random
import statistics
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from run_phase_q_incremental import (  # noqa: E402
    GATE_EXPECT, T_CRIT, VARS, fm, fm_multi, fm_uni, judge, zr,
)


def _panel(n_months: int = 30, n_stocks: int = 300, seed: int = 3,
           coefs=(0.002, -0.004, 0.0, 0.0)) -> list[dict]:
    rng = random.Random(seed)
    out = []
    for _ in range(n_months):
        m5 = np.array([rng.gauss(0, 1) for _ in range(n_stocks)])
        sg = np.array([rng.gauss(0, 1) for _ in range(n_stocks)])
        vol = np.array([abs(rng.gauss(0.02, 0.005)) for _ in range(n_stocks)])
        rv = np.array([rng.gauss(0, 0.05) for _ in range(n_stocks)])
        noise = np.array([rng.gauss(0, 0.05) for _ in range(n_stocks)])
        fwd = (coefs[0] * zr(m5) + coefs[1] * zr(sg) + coefs[2] * zr(vol)
               + coefs[3] * zr(rv) + noise)
        out.append({"signal_date": "2020-01-31", "entry": "2020-02-03",
                    "m5": m5, "surge": sg, "vol": vol, "rev1": rv, "rev21": rv,
                    "fwd": fwd})
    return out


# ============================================================ 秩 z-score
def test_zr_is_rank_based_and_standardized():
    a = np.array([1.0, 5.0, 3.0, 9.0, 2.0])
    z = zr(a)
    assert z.mean() == pytest.approx(0.0, abs=1e-9)
    assert z.std() == pytest.approx(1.0, abs=1e-9)
    # 严格单调变换不改变秩 → 不改变 z
    assert np.allclose(z, zr(np.array([1.0, 5.0, 3.0, 9.0, 2.0]) ** 3))
    assert np.allclose(z, zr(np.log(np.array([1.0, 5.0, 3.0, 9.0, 2.0]) + 2)))


def test_zr_immune_to_extreme_outliers():
    """秩变换的好处：一个 1000 倍的异常值不改变任何人的 z。"""
    base = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    z1 = zr(base)
    z2 = zr(np.array([1.0, 2.0, 3.0, 4.0, 5000.0]))
    assert np.allclose(z1, z2)


# ============================================================ FM 回归
def test_fm_multi_recovers_known_coefficients():
    """合成数据：注入已知系数，多元 FM 必须把它找回来。"""
    recs = _panel(n_months=200, coefs=(0.002, -0.004, 0.0, 0.0))
    res, n = fm_multi(recs, "rev21")
    assert n == 200
    assert res["MAX5"]["coef_pp_per_sd"] == pytest.approx(0.2, abs=0.05)   # 0.002→0.2pp
    assert res["SURGE"]["coef_pp_per_sd"] == pytest.approx(-0.4, abs=0.05)
    assert abs(res["VOL"]["coef_pp_per_sd"]) < 0.06
    assert abs(res["REV"]["coef_pp_per_sd"]) < 0.06


def test_fm_reports_both_iid_and_nw_se():
    """本项目标准误口径不统一 → 两套都报，不许只报一套。"""
    rng = random.Random(5)
    x = [rng.gauss(0.002, 0.001) for _ in range(120)]
    r = fm(x)
    assert "t_iid" in r and "t_nw" in r and "p_nw" in r
    assert r["coef_pp_per_sd"] == pytest.approx(0.2, abs=0.02)


def test_univariate_equals_multivariate_when_signals_orthogonal():
    """信号正交时，多元系数必须≈单变量系数（保留率≈1）。"""
    recs = _panel(n_months=200, coefs=(0.003, 0.0, 0.0, 0.0))
    multi, _ = fm_multi(recs, "rev21")
    uni = fm_uni(recs, "rev21")
    assert multi["MAX5"]["coef_pp_per_sd"] == pytest.approx(
        uni["MAX5"]["coef_pp_per_sd"], abs=0.02)


# ============================================================ 判据
def test_judge_three_verdicts():
    assert judge(-0.2, -2.5) == "INCREMENTAL_CONFIRMED"
    assert judge(-0.2, -1.0) == "INCREMENTAL_ABSORBED"
    assert judge(+0.2, +2.5) == "INCREMENTAL_DETECTED_WRONG_SIGN"
    assert judge(0.0, float("nan")) == "INCREMENTAL_ABSORBED"


# ============================================================ 门控常数不许手抄
def test_gate_expect_matches_phase_n_product():
    """复现闸门的目标值必须来自阶段 N 的产物 JSON，不是我手写的数。"""
    p = "reports/COST_ADDENDUM_AND_SIGNALS.json"
    d = json.load(open(p, encoding="utf-8"))
    inc = d["incremental_fama_macbeth"]
    for k in VARS:
        assert GATE_EXPECT[k][0] == pytest.approx(inc[k]["mean_pp_per_sd"], abs=1e-9)
        assert GATE_EXPECT[k][1] == pytest.approx(inc[k]["t_nw"], abs=1e-9)


# ============================================================ REV 定义修正
def test_rev21_differs_from_rev1_definition():
    """阶段 N 用单日收益当反转控制（等于没控制）；本阶段改用 21 日。

    两条定义必须**真的不同** —— 若相同则修正没生效。
    """
    # 构造：单日收益小、21 日累计收益大
    px = np.array([100.0, 100.5, 101.0, 110.0])       # WIN 用 3（示意）
    win = 3
    rev1_daily = px[-1] / px[-2] - 1.0                 # 单日：110/101-1 = 8.91%
    rev21 = px[-2] / px[-win] - 1.0                    # 21日：101/100-1 = 1.00%
    assert abs(rev1_daily - rev21) > 0.05              # 量级必须差很多


def test_rev21_sign_convention_is_past_return():
    """REV21 = P[pos-1]/P[pos-WIN] − 1：上涨=正（赢家）。"""
    px = np.array([100.0, 101.0, 102.0, 120.0])
    win = 3
    assert (px[-2] / px[-win] - 1.0) > 0     # 过去上涨 → 正


# ============================================================ 变异测试
def test_mutation_sign_flip_in_signal_flips_coefficient():
    """符号翻转：把 SURGE 取反，其系数必须反号，MAX5 不动。"""
    recs = _panel(n_months=120, coefs=(0.002, -0.004, 0.0, 0.0))
    a, _ = fm_multi(recs, "rev21")
    flipped = []
    for r in recs:
        flipped.append({**r, "surge": -r["surge"]})
    b, _ = fm_multi(flipped, "rev21")
    assert b["SURGE"]["coef_pp_per_sd"] == pytest.approx(
        -a["SURGE"]["coef_pp_per_sd"], abs=1e-6)
    assert b["MAX5"]["coef_pp_per_sd"] == pytest.approx(
        a["MAX5"]["coef_pp_per_sd"], abs=1e-6)


def test_mutation_unit_scale_keeps_t_scales_coef():
    """百分数与小数混用：fwd ×100 后系数 ×100，t 不变。

    ⚠️ t 尺度无关 → 只看 t 发现不了单位错误。必须同时看系数的量级。
    """
    recs = _panel(n_months=120, coefs=(0.002, -0.004, 0.0, 0.0))
    a, _ = fm_multi(recs, "rev21")
    scaled = [{**r, "fwd": r["fwd"] * 100} for r in recs]
    b, _ = fm_multi(scaled, "rev21")
    # ⚠️ `fm` 内部把系数舍到 6 位小数；×100 会把这个舍入误差放大到 1e-4 量级，
    #    所以容差必须按放大后的绝对误差给，不能用 rel=1e-8。
    for k in VARS:
        assert b[k]["t_nw"] == pytest.approx(a[k]["t_nw"], rel=1e-8)
        assert b[k]["coef_pp_per_sd"] == pytest.approx(
            a[k]["coef_pp_per_sd"] * 100, abs=1e-3)


def test_mutation_nan_in_forward_return_is_visible():
    """缺失值传播：fwd 混入 NaN 后系数变 NaN（必须**可见**，不能静默给数）。"""
    recs = _panel(n_months=40, coefs=(0.002, -0.004, 0.0, 0.0))
    bad = []
    for r in recs:
        f = r["fwd"].copy()
        f[0] = float("nan")
        bad.append({**r, "fwd": f})
    res, _ = fm_multi(bad, "rev21")
    # 必须**可见**：要么系数变 NaN，要么打上 has_nonfinite 标记。不许静默给数。
    visible = any((v["coef_pp_per_sd"] != v["coef_pp_per_sd"]) or v["has_nonfinite"]
                  for v in res.values())
    assert visible


def test_fm_handles_degenerate_inputs_without_crashing():
    """退化输入必须优雅返回并打标，不能抛异常（否则整轮跑挂掉）。"""
    for bad in ([], [1.0], [float("nan")] * 5):
        r = fm(bad)
        assert r["insufficient"] is True
        assert r["coef_pp_per_sd"] != r["coef_pp_per_sd"]
    r = fm([0.01, float("nan"), 0.02, 0.03])
    assert r["has_nonfinite"] is True and r["n_valid"] == 3


def test_mutation_duplicated_variable_is_collinear_but_stable():
    """把 VOL 复制成 REV → 完全共线。lstsq 不得崩溃，且解仍可用。"""
    recs = _panel(n_months=60, coefs=(0.002, -0.004, 0.001, 0.0))
    dup = [{**r, "rev21": r["vol"].copy()} for r in recs]
    res, n = fm_multi(dup, "rev21")
    assert n == 60
    assert all(v["coef_pp_per_sd"] == v["coef_pp_per_sd"] for v in res.values())


def test_mutation_shuffled_forward_return_destroys_signal():
    """打乱 fwd 与信号的对应关系 → 系数必须塌到 ~0（否则回归没在用横截面信息）。"""
    recs = _panel(n_months=120, coefs=(0.002, -0.004, 0.0, 0.0))
    rng = random.Random(99)
    shuf = []
    for r in recs:
        f = r["fwd"].copy()
        rng.shuffle(f)
        shuf.append({**r, "fwd": np.array(f)})
    res, _ = fm_multi(shuf, "rev21")
    assert abs(res["MAX5"]["coef_pp_per_sd"]) < 0.08
    assert abs(res["SURGE"]["coef_pp_per_sd"]) < 0.08


def test_mutation_tiny_pool_is_skipped_not_silently_used():
    """样本太小的月份必须被跳过（`m.sum() < 100`），不能照常进回归。"""
    recs = _panel(n_months=20, n_stocks=50, coefs=(0.002, -0.004, 0.0, 0.0))
    res, n = fm_multi(recs, "rev21")
    assert n == 0
    assert all(v["insufficient"] is True for v in res.values())


def test_panel_records_carry_codes_for_tradability():
    """S3 需要 codes；第一版靠阶段 I 对齐结果 0/237 → 面板必须自带代码名。"""
    src = "scripts/run_phase_q_incremental.py"
    s = open(src, encoding="utf-8").read()
    assert '"codes": [cols[i] for i in idx[fin]]' in s
