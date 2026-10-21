"""阶段 C-2 的单元测试：剔除语义。

测的是**规则语义**，不是市场结果：
V1 相对 V0 只允许**多剔** `no_bar` / `after_last_data` 两类，
**绝不能**顺带剔掉"方向不匹配的涨跌停"（跌停能买入、涨停能卖出）。

运行：
    cd "D:/DeepQuant power by txm/DeepQuant-main"
    PYTHONPATH=scripts python -m pytest scripts/tests/test_phase_c2.py -q
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_phase_c2_suspend import DROP_REASONS, apply_v1, judge  # noqa: E402


def _rec(o, h, l, c, prev_close=10.0, idx=100, prev_date="2020-01-06"):
    return {"bar": (o, h, l, c, 1e6), "prev": (prev_close,) * 4 + (1e6,),
            "prev_date": prev_date, "idx": idx}


SPAN = {
    "600001.SH": {"first": "2005-01-04", "last": "2020-12-31", "n_days": 3000},
    "600002.SH": {"first": "2005-01-04", "last": "2020-12-31", "n_days": 3000},
    "600003.SH": {"first": "2005-01-04", "last": "2020-01-02", "n_days": 100},   # 已终止
    "600004.SH": {"first": "2005-01-04", "last": "2020-12-31", "n_days": 3000},  # 真停牌
}

BARS = {
    "600001.SH": {"2020-06-01": _rec(10.1, 10.3, 10.0, 10.2),          # 正常（建仓日）
                  "2020-06-30": _rec(10.2, 10.4, 10.2, 10.3,           # 平仓日也要有 bar，
                                     prev_close=10.2, prev_date="2020-06-01")},
    "600002.SH": {"2020-06-01": _rec(9.2, 9.3, 9.0, 9.0)},              # 跌停（应保留）
    "600003.SH": {},                                                     # 无 bar，且已终止
    "600004.SH": {},                                                     # 无 bar，区间内 → 停牌
}
EX = {}


def _row(longs, shorts, fwd_map):
    import pandas as pd
    return {
        "signal_date": "2020-05-29", "month": "2020-06-30", "n_pool": 100, "k_used": 4,
        "longs_c": list(longs), "shorts_c": list(shorts),
        "entry": "2020-06-01", "exit": "2020-06-30",
        "fwd": pd.Series(fwd_map),
        "d": 0.01, "n_drop_long": 0, "n_drop_short": 0,
        "kept_blocked_long": {}, "kept_blocked_short": {},
    }


FWD = {"600001.SH": 0.05, "600002.SH": -0.02, "600003.SH": 0.30, "600004.SH": 0.00}


def test_drop_reasons_are_exactly_two():
    """V1 只允许剔两类。多一个就是越权（预登记只授权这两类）。"""
    assert DROP_REASONS == ("no_bar", "after_last_data")


def test_apply_v1_keeps_limit_down_and_drops_suspension():
    """跌停**能买入**，必须保留；停牌与序列终止必须剔除。"""
    rows = [_row(["600001.SH", "600002.SH", "600003.SH", "600004.SH"], ["600001.SH"], FWD)]
    out = apply_v1(rows, BARS, EX, SPAN)
    r = out[0]
    assert "600002.SH" in r["longs_v1"], "跌停被误剔（跌停可以买入）"
    assert "600001.SH" in r["longs_v1"]
    assert "600003.SH" not in r["longs_v1"], "序列终止未被剔除"
    assert "600004.SH" not in r["longs_v1"], "真停牌未被剔除"
    # 剔除原因要分开计数，不能合并成 no_bar
    assert r["drop2_long_reasons"].get("after_last_data") == 1
    assert r["drop2_long_reasons"].get("no_bar") == 1
    assert r["n_drop2_long"] == 2


def test_apply_v1_recomputes_mean_without_dropped():
    """剔除后要重算腿均值，不能沿用 V0 的结果。"""
    rows = [_row(["600001.SH", "600003.SH"], ["600001.SH"], FWD)]
    r = apply_v1(rows, BARS, EX, SPAN)[0]
    # 只剩 600001（+5%），而 V0 的两只均值是 (0.05+0.30)/2 = 0.175
    assert r["r_long_v1"] == pytest.approx(0.05)
    assert r["r_long_v1"] != pytest.approx(0.175)


def test_apply_v1_empty_leg_gives_nan_not_crash():
    """腿被剔空时必须给 NaN 并保留记录，不能崩也不能当成 0。"""
    rows = [_row(["600003.SH", "600004.SH"], ["600001.SH"], FWD)]
    r = apply_v1(rows, BARS, EX, SPAN)[0]
    assert r["longs_v1"] == []
    assert r["d_v1"] != r["d_v1"]           # NaN


def test_apply_v1_does_not_mutate_input():
    """V0 的行是阶段 C 的产物，必须原样保留以便复现校验。"""
    rows = [_row(["600001.SH", "600004.SH"], ["600001.SH"], FWD)]
    before = (list(rows[0]["longs_c"]), rows[0]["d"])
    apply_v1(rows, BARS, EX, SPAN)
    assert (list(rows[0]["longs_c"]), rows[0]["d"]) == before
    assert "longs_v1" not in rows[0]


# ============================================================ 四档判据

def _nw(n, t, mean_pp, eff=None):
    return {"n": n, "n_eff": n - 3 if eff is None else eff,
            "t_nw": t, "mean_pp": mean_pp, "mean": mean_pp / 100.0}


def test_judge_material():
    assert judge(_nw(237, 2.5, 0.40))["verdict"] == "SUSPENSION_EFFECT_MATERIAL"


def test_judge_negligible_when_t_small():
    assert judge(_nw(237, 1.2, 0.90))["verdict"] == "SUSPENSION_EFFECT_NEGLIGIBLE"


def test_judge_small_but_significant():
    """t 够但效应小于经济门槛 —— 这正是本轮实测落到的档位。"""
    v = judge(_nw(237, 5.01, 0.1069))
    assert v["verdict"] == "SUSPENSION_EFFECT_SMALL_BUT_SIGNIFICANT"


def test_judge_inconclusive():
    assert judge(_nw(149, 5.0, 1.0))["verdict"] == "INCONCLUSIVE"
    assert judge(_nw(237, 5.0, 1.0, eff=99))["verdict"] == "INCONCLUSIVE"


def test_judge_negative_direction_reported():
    v = judge(_nw(237, 3.0, -0.50))
    assert v["verdict"] == "SUSPENSION_EFFECT_MATERIAL"
    assert "变小" in v["direction"]
