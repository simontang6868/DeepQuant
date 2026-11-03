"""阶段 V（成本口径一致性）单测。

运行：PYTHONPATH=scripts pytest scripts/tests/test_phase_v.py -q
（脚本模块导入无副作用：预登记校验只在 main() 内执行）
"""

from __future__ import annotations

import csv
import math
import os
import statistics

import pytest

import run_phase_v_cost_convention as V


REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(os.path.join(REPO, ".."))


def _rows() -> list[dict[str, str]]:
    with open(V.O_CSV, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def test_nw_constant_shift_only_scales_t():
    """逐月常数移位不改变方差结构：se 全 L 恒等，t 按均值比缩放。"""
    x = [0.3, -0.11, 0.07, 0.42, -0.05, 0.19, -0.23, 0.08, 0.31, -0.14] * 6
    shift = 0.05
    y = [v + shift for v in x]
    gx, gy = V.nw_profile(x), V.nw_profile(y)
    for L in gx:
        assert gy[L]["se_nw"] == pytest.approx(gx[L]["se_nw"], rel=0, abs=1e-15)
        assert gy[L]["t_nw"] == pytest.approx(
            gx[L]["t_nw"] * (gx[L]["mean"] + shift) / gx[L]["mean"], rel=1e-12)


def test_nw_lag0_relation_to_iid():
    """L=0 的 NW se 与 iid se 只差 √((n−1)/n)。

    推导：g0 = Σe²/n，se_nw0 = √(g0/n) = √(Σe²)/n；
          se_iid = sd/√n = √(Σe²/(n−1))/√n → se_nw0 = se_iid × √((n−1)/n)。
    """
    x = [0.5, -0.2, 0.31, 0.07, -0.44, 0.12, 0.28, -0.09, 0.55, -0.31] * 5
    g = V.nw_profile(x, lags=(0,))[0]
    n = len(x)
    assert g["se_nw"] == pytest.approx(g["se_iid"] * math.sqrt((n - 1) / n), rel=1e-12)


def test_scale_invariance_of_t():
    """pp → 小数：t 逐位不变、均值正好差 100 倍（G5 的核心）。"""
    x = [0.11, -0.35, 0.42, 0.08, -0.19, 0.27, 0.33, -0.06, 0.51, 0.02] * 12
    a, b = V.stats_of(x), V.stats_of([v / 100.0 for v in x])
    assert a["t_nw"] == pytest.approx(b["t_nw"], rel=0, abs=1e-12)
    assert a["mean_pp"] / b["mean_pp"] == pytest.approx(100.0, rel=1e-12)


def test_reproduce_phase_u_o_base():
    """独立重算阶段 U 的 U6（O BASE@3000万 全样本）：均值与 L 网格必须吻合。"""
    rows = _rows()
    net = [float(r["net_BASE_3000w"]) for r in rows]
    st = V.stats_of(net)
    assert st["n"] == 237
    assert st["mean_pp"] == pytest.approx(0.135959, abs=1e-5)
    assert st["grid_t"][3] == pytest.approx(4.0386, abs=1e-3)
    assert st["grid_t"][0] == pytest.approx(3.0385, abs=1e-3)


def test_reproduce_t1a_and_its_l4_edge():
    """T1a 的 L=4 原本只比阈值高 0.014——这是阶段 V 唯一翻转的格。"""
    rows = _rows()
    s = [float(r["net_BASE_3000w"]) + V.IMPACT_UPPER - V.SQRT_3000W
         for r in rows
         if r["signal_date"] >= V.MODERN_LO
         and r["signal_date"][:7] not in V.DROP6]
    st = V.stats_of(s)
    assert st["n"] == 123
    assert st["t_nw"] == pytest.approx(1.9207, abs=1e-3)     # L=3 不显著
    assert st["grid_t"][4] == pytest.approx(1.974, abs=2e-3)  # L=4 勉强显著
    assert st["grid_t"][4] - V.T_CRIT < 0.02                  # 余量不足 0.02


def test_prereg_and_inputs_hashes_are_frozen():
    """预登记与四个输入文件的 sha256 必须与硬编码一致。"""
    assert V.sha256_file(V.PREREG) == V.PREREG_SHA256
    for path, want in ((V.O_CSV, V.O_SHA256), (V.REB_BASE_CSV, V.REB_BASE_SHA256),
                       (V.REB_CONS_CSV, V.REB_CONS_SHA256),
                       (V.A5_SUMMARY, V.A5_SUMMARY_SHA256)):
        assert V.sha256_file(path) == want, path
