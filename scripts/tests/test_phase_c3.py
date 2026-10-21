"""阶段 C-3 的单元测试：成交延迟的重新定价语义。

测的是**规则语义**：
1. `first_tradable` 必须真的顺延到第一个可成交日，超时返回 None；
2. `reprice` 只改收益起算点，**持仓不变**（不剔不补）——这是"Δ 不含选股差异"的前提；
3. 顺延 0 天时 V1 与 V2 必须给出相同结果。

运行：
    cd "D:/DeepQuant power by txm/DeepQuant-main"
    PYTHONPATH=scripts python -m pytest scripts/tests/test_phase_c3.py -q
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from run_phase_c3_exec_lag import MAX_DELAY_DAYS, first_tradable, judge, reprice  # noqa: E402

CAL = pd.bdate_range("2020-01-27", periods=6)          # 0=pos, 1=pos+1, ..., 5=nxt
D0, D1, D2, D5 = [str(CAL[i].date()) for i in (0, 1, 2, 5)]
POS_OF = {str(CAL[i].date()): i for i in range(len(CAL))}

# A 每天 +2% 左右；B 每天 +1.5% 左右
FFILL = pd.DataFrame({
    "A": [10.0, 10.1, 10.3, 10.5, 10.8, 11.0],
    "B": [20.0, 20.2, 20.5, 20.8, 21.0, 21.5],
}, index=CAL)


def _rec(o, h, l, c, prev_close=10.0, idx=100, prev_date="2020-01-27"):
    return {"bar": (o, h, l, c, 1e6), "prev": (prev_close,) * 4 + (1e6,),
            "prev_date": prev_date, "idx": idx}


def _row(longs=("A",), shorts=("B",)):
    return {"signal_date": D0, "month": D5,
            "longs_c": list(longs), "shorts_c": list(shorts),
            "entry": D1, "exit": D5, "d": 0.0,
            "n_pool": 10, "k_used": 1}


# ============================================================ first_tradable

def test_first_tradable_zero_delay_when_next_day_ok():
    bars = {"A": {D1: _rec(10.05, 10.15, 10.0, 10.1)}}
    j, d, k = first_tradable("A", 1, CAL, bars, {}, max_days=5)
    assert (j, d, k) == (1, D1, 0)


def test_first_tradable_skips_suspended_days():
    """pos+1 停牌（无 bar）→ 顺延到 pos+2。"""
    bars = {"A": {D2: _rec(10.4, 10.5, 10.2, 10.3)}}
    j, d, k = first_tradable("A", 1, CAL, bars, {}, max_days=5)
    assert (j, d, k) == (2, D2, 1)


def test_first_tradable_timeout_returns_none():
    """全程无 bar → 必须返回 None（该标的当月剔除），不能退回 pos+1。"""
    j, d, k = first_tradable("A", 1, CAL, {"A": {}}, {}, max_days=5)
    assert j is None and d is None and k == MAX_DELAY_DAYS


def test_first_tradable_skips_limit_up():
    """涨停买不到 → 必须继续找下一天，不能当成可成交。"""
    bars = {"A": {D1: _rec(11.0, 11.0, 11.0, 11.0, prev_close=10.0),   # 一字涨停
                  D2: _rec(11.2, 11.4, 11.1, 11.3, prev_close=11.0,
                           prev_date=D1)}}
    j, d, k = first_tradable("A", 1, CAL, bars, {}, max_days=5)
    assert k == 1 and d == D2


# ============================================================ reprice

def test_reprice_v0_like_uses_pos_close():
    """V1 的第一天起算价必须是 pos+1 收盘，于是收益小于从 pos 起算。"""
    rows = [_row()]
    bars = {"A": {D1: _rec(10.05, 10.15, 10.0, 10.1)},
            "B": {D5: _rec(21.4, 21.6, 21.3, 21.5, prev_close=21.0,
                           prev_date=str(CAL[4].date()))}}
    out = reprice(rows, FFILL, CAL, "V1", bars, {}, POS_OF)
    r = out[0]
    assert r["r_long_rep"] == pytest.approx(11.0 / 10.1 - 1)
    assert r["r_short_rep"] == pytest.approx(21.5 / 20.2 - 1)
    # 从 pos 起算（V0 口径）会更高
    assert r["r_long_rep"] < 11.0 / 10.0 - 1


def test_reprice_keeps_holdings_unchanged():
    """reprice 不得剔补标的——腿成员必须与输入完全一致。"""
    rows = [_row(longs=("A",), shorts=("B",))]
    bars = {"A": {D1: _rec(10.05, 10.15, 10.0, 10.1)},
            "B": {D5: _rec(21.4, 21.6, 21.3, 21.5, prev_close=21.0,
                           prev_date=str(CAL[4].date()))}}
    r = reprice(rows, FFILL, CAL, "V1", bars, {}, POS_OF)[0]
    assert r["longs_c"] == ["A"] and r["shorts_c"] == ["B"]
    assert r["n_kept_long"] == 1 and r["n_kept_short"] == 1


def test_reprice_does_not_mutate_input():
    rows = [_row()]
    before = (list(rows[0]["longs_c"]), rows[0]["d"])
    bars = {"A": {D1: _rec(10.05, 10.15, 10.0, 10.1)},
            "B": {D5: _rec(21.4, 21.6, 21.3, 21.5, prev_close=21.0,
                           prev_date=str(CAL[4].date()))}}
    reprice(rows, FFILL, CAL, "V1", bars, {}, POS_OF)
    assert (list(rows[0]["longs_c"]), rows[0]["d"]) == before
    assert "d_repriced" not in rows[0]


def test_v1_equals_v2_when_next_day_tradable():
    """次日就能成交时，V1 与 V2 必须给出完全相同的 d（D3 显示 97% 属于这一类）。"""
    rows = [_row()]
    bars = {"A": {D1: _rec(10.05, 10.15, 10.0, 10.1)},
            "B": {D1: _rec(20.1, 20.3, 20.0, 20.2, prev_close=20.0,
                           prev_date=str(CAL[0].date()))}}
    r1 = reprice(rows, FFILL, CAL, "V1", bars, {}, POS_OF)[0]
    r2 = reprice(rows, FFILL, CAL, "V2", bars, {}, POS_OF)[0]
    assert r1["d_repriced"] == pytest.approx(r2["d_repriced"])


def test_reprice_drops_month_when_leg_timed_out():
    """顺延超时把整条腿削空 → 该月必须被跳过，不能留下 NaN 混进统计。"""
    rows = [_row(longs=("A",), shorts=("B",))]
    bars = {"A": {}, "B": {}}
    assert reprice(rows, FFILL, CAL, "V2", bars, {}, POS_OF) == []


# ============================================================ 四档判据

def _nw(n, t, mean_pp, eff=None):
    return {"n": n, "n_eff": n - 3 if eff is None else eff,
            "t_nw": t, "mean_pp": mean_pp, "mean": mean_pp / 100.0}


def test_judge_negligible():
    """本轮实测落到的档位：t=0.597。"""
    assert judge(_nw(237, 0.597, 0.0468))["verdict"] == "LAG_COST_NEGLIGIBLE"


def test_judge_material_and_direction():
    v = judge(_nw(237, 3.0, -0.50))
    assert v["verdict"] == "LAG_COST_MATERIAL"
    assert "变小" in v["direction"]


def test_judge_small_but_significant():
    assert judge(_nw(237, 2.5, 0.10))["verdict"] == "LAG_COST_SMALL_BUT_SIGNIFICANT"


def test_judge_inconclusive():
    assert judge(_nw(149, 5.0, 1.0))["verdict"] == "INCONCLUSIVE"
    assert judge(_nw(237, 5.0, 1.0, eff=99))["verdict"] == "INCONCLUSIVE"
