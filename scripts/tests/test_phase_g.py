"""阶段 G 的单元测试：单边 alpha 的五档判据。

重点：`MAX5_LONGONLY_NOT_EXECUTABLE` 的判定依赖**外部对照**（阶段 F 的多空价差）。
必须验证"单边不显著 + 对照显著"才给这个档位，否则会误判。

运行：
    cd "D:/DeepQuant power by txm/DeepQuant-main"
    PYTHONPATH=scripts python -m pytest scripts/tests/test_phase_g.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_phase_g_max5_longonly import judge  # noqa: E402

SPREAD_F = 0.7405      # 阶段 F 的多空价差（pp）


def _nw(n, t, a_pp, eff=None):
    return {"n": n, "n_eff": n - 3 if eff is None else eff,
            "t_nw": t, "mean_pp": a_pp, "mean": a_pp / 100.0}


def test_judge_not_executable_at_1764():
    """本轮实测 t=1.7641 —— 不显著，且对照价差显著 → NOT_EXECUTABLE。"""
    v = judge(_nw(237, 1.7641, 0.1813), SPREAD_F)
    assert v["verdict"] == "MAX5_LONGONLY_NOT_EXECUTABLE"
    assert "note" in v


def test_judge_confirmed():
    assert judge(_nw(237, 2.5, 0.50), SPREAD_F)["verdict"] == "MAX5_LONGONLY_CONFIRMED"


def test_judge_small_but_significant():
    assert judge(_nw(237, 2.5, 0.10), SPREAD_F)["verdict"] == "MAX5_LONGONLY_SMALL_BUT_SIGNIFICANT"


def test_judge_inconclusive():
    assert judge(_nw(149, 5.0, 1.0), SPREAD_F)["verdict"] == "INCONCLUSIVE"
    assert judge(_nw(237, 5.0, 1.0, eff=99), SPREAD_F)["verdict"] == "INCONCLUSIVE"


def test_not_executable_requires_significant_control():
    """对照价差本身不显著时，不该用 NOT_EXECUTABLE 这个措辞——
    因为它暗示"价差是真的但单边拿不到"。"""
    v = judge(_nw(237, 1.0, 0.10), 0.05)
    assert v["verdict"] == "MAX5_LONGONLY_NOT_EXECUTABLE"
    assert "单边不显著" in v["note"]


def test_negative_alpha_confirmed_reports_direction():
    v = judge(_nw(237, 3.0, -0.60), SPREAD_F)
    assert v["verdict"] == "MAX5_LONGONLY_CONFIRMED"
    assert "跑输" in v["direction"]


def test_boundary_inclusive():
    assert judge(_nw(150, 1.96, 0.30), SPREAD_F)["verdict"] == "MAX5_LONGONLY_CONFIRMED"
