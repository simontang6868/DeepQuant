"""阶段 F 的单元测试：等频分层与五档判据。

运行：
    cd "D:/DeepQuant power by txm/DeepQuant-main"
    PYTHONPATH=scripts python -m pytest scripts/tests/test_phase_f.py -q
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_phase_f_max5 import judge, split_equal  # noqa: E402


# ============================================================ 等频分组

def test_split_equal_sizes_differ_by_at_most_one():
    for n in (10, 11, 13, 100, 101, 487):
        g = split_equal(np.arange(n, dtype=float), 5)
        cnt = np.bincount(g, minlength=5)
        assert cnt.sum() == n
        assert cnt.max() - cnt.min() <= 1, f"n={n} 组大小 {cnt}"


def test_split_equal_is_monotone_in_value():
    """组号必须随值单调不减——否则"Q1 最低"就是假的。"""
    rng = np.random.default_rng(20261021)
    x = rng.normal(size=500)
    g = split_equal(x, 5)
    order = np.argsort(x, kind="stable")
    assert np.all(np.diff(g[order]) >= 0)


def test_split_equal_handles_ties():
    """大量重复值也不能把某一组撑爆。"""
    x = np.array([0.0] * 50 + [1.0] * 50)
    g = split_equal(x, 5)
    cnt = np.bincount(g, minlength=5)
    assert cnt.sum() == 100
    assert cnt.max() - cnt.min() <= 1


def test_split_equal_single_value_edge():
    g = split_equal(np.array([3.14] * 7), 5)
    assert np.bincount(g, minlength=5).sum() == 7


# ============================================================ 五档判据

def _nw(n, t, d_pp, eff=None):
    return {"n": n, "n_eff": n - 3 if eff is None else eff,
            "t_nw": t, "mean_pp": d_pp, "mean": d_pp / 100.0}


def test_judge_confirmed_positive():
    v = judge(_nw(237, 3.4086, 0.7405))
    assert v["verdict"] == "MAX5_EFFECT_CONFIRMED"


def test_judge_reversed_when_negative_and_big():
    assert judge(_nw(237, -3.0, -0.60))["verdict"] == "MAX5_EFFECT_REVERSED"


def test_judge_small_but_significant():
    assert judge(_nw(237, 2.5, 0.10))["verdict"] == "MAX5_EFFECT_SMALL_BUT_SIGNIFICANT"


def test_judge_absorbed_by_vol():
    """分层后不显著 —— 预写档位，语义比 NOT_DETECTED 准。"""
    v = judge(_nw(237, 1.2, 0.90))
    assert v["verdict"] == "MAX_EFFECT_ABSORBED_BY_VOL"
    assert "note" in v


def test_judge_inconclusive():
    assert judge(_nw(149, 5.0, 1.0))["verdict"] == "INCONCLUSIVE"
    assert judge(_nw(237, 5.0, 1.0, eff=99))["verdict"] == "INCONCLUSIVE"


def test_judge_boundary_at_econ_threshold():
    """|t|=1.96 且 Δ=+0.30pp → CONFIRMED（判据写的是 ≥）。"""
    assert judge(_nw(150, 1.96, 0.30))["verdict"] == "MAX5_EFFECT_CONFIRMED"


def test_judge_sign_matters_at_boundary():
    """同样 |Δ|=0.30 但为负 → REVERSED，不是 CONFIRMED。"""
    assert judge(_nw(150, 1.96, -0.30))["verdict"] == "MAX5_EFFECT_REVERSED"
