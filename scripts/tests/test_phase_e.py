"""阶段 E 的单元测试：alpha 判定与 beta 估计。

测的是**规则语义**：
1. 四档判据（重点：t=1.90 必须落在 NOT_DETECTED，不能因为"接近"就放行）；
2. `ols_slope` 的斜率与相关系数（beta 是描述性指标，但算错了会误导结论）。

运行：
    cd "D:/DeepQuant power by txm/DeepQuant-main"
    PYTHONPATH=scripts python -m pytest scripts/tests/test_phase_e.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_phase_e_longonly import judge, ols_slope  # noqa: E402


def _nw(n, t, alpha_pp, eff=None):
    return {"n": n, "n_eff": n - 3 if eff is None else eff,
            "t_nw": t, "mean_pp": alpha_pp, "mean": alpha_pp / 100.0}


# ============================================================ 四档判据

def test_judge_not_detected_at_1901():
    """本轮实测 t=1.9013 —— 差 0.06 也要判 NOT_DETECTED，不许"接近就放行"。"""
    assert judge(_nw(237, 1.9013, 0.2767))["verdict"] == "LONGONLY_ALPHA_NOT_DETECTED"


def test_judge_confirmed():
    assert judge(_nw(237, 2.5, 0.50))["verdict"] == "LONGONLY_ALPHA_CONFIRMED"


def test_judge_small_but_significant():
    assert judge(_nw(237, 2.5, 0.10))["verdict"] == "LONGONLY_ALPHA_SMALL_BUT_SIGNIFICANT"


def test_judge_inconclusive():
    assert judge(_nw(149, 5.0, 1.0))["verdict"] == "INCONCLUSIVE"
    assert judge(_nw(237, 5.0, 1.0, eff=99))["verdict"] == "INCONCLUSIVE"


def test_judge_negative_alpha_reported():
    v = judge(_nw(237, 3.0, -0.60))
    assert v["verdict"] == "LONGONLY_ALPHA_CONFIRMED"
    assert "跑输" in v["direction"]


def test_judge_boundary_inclusive():
    """|t|=1.96 且 |alpha|=0.30pp 应算 CONFIRMED（判据写的是 ≥）。"""
    assert judge(_nw(150, 1.96, 0.30))["verdict"] == "LONGONLY_ALPHA_CONFIRMED"


# ============================================================ beta

def test_ols_slope_exact_linear():
    """y = 2x + 常数 → 斜率 2、相关 1。"""
    x = [0.01, 0.02, -0.01, 0.03, -0.02, 0.015]
    y = [2 * v + 0.005 for v in x]
    beta, corr = ols_slope(y, x)
    assert beta == pytest.approx(2.0)
    assert corr == pytest.approx(1.0)


def test_ols_slope_inverse_relation():
    x = [0.01, 0.02, -0.01, 0.03, -0.02, 0.015]
    y = [-1.5 * v for v in x]
    beta, corr = ols_slope(y, x)
    assert beta == pytest.approx(-1.5)
    assert corr == pytest.approx(-1.0)


def test_ols_slope_degenerate_inputs():
    beta, corr = ols_slope([0.01, 0.02], [0.01, 0.02])
    assert beta != beta            # 样本 < 3 → NaN
    beta2, corr2 = ols_slope([1.0, 2.0, 3.0], [0.01, 0.01, 0.01])
    assert beta2 != beta2          # x 无变异 → NaN，不许返回 0
