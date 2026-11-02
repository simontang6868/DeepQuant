# -*- coding: utf-8 -*-
"""P1-3 回填/盯市的行为测试（不触网：假 Bars + 桩 loader）。"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import p13b_forward_track as tb  # noqa: E402
import fill_engine as fe  # noqa: E402


class FakeBars:
    """仿 fe.Bars 的最小实现，满足 judge/deferred/close_at 所需接口。"""

    def __init__(self, dates, closes, highs=None, lows=None, vols=None, code="X"):
        self.code = code
        self.d = list(dates)
        self.pos = {d: i for i, d in enumerate(dates)}
        self.qc = np.array(closes, float)
        self.hi = np.array(highs if highs is not None else closes, float)
        self.lo = np.array(lows if lows is not None else closes, float)
        self.vol = np.array(vols if vols is not None else [1e6] * len(dates), float)

    def ret(self, i):
        if i <= 0:
            return np.nan
        if not np.isfinite(self.qc[i - 1]) or self.qc[i - 1] <= 0:
            return np.nan
        return float(self.qc[i] / self.qc[i - 1] - 1.0)

    def close_at(self, date):
        i = self.pos.get(date)
        return float(self.qc[i]) if i is not None else np.nan


def _free_bar(dates, closes):
    """每个交易日都"自由成交"的假 Bars（高低不一、量>0、有前一日）。"""
    hi = [c * 1.01 for c in closes]
    lo = [c * 0.99 for c in closes]
    return FakeBars(dates, closes, hi, lo)


def _lockup_first_then_free():
    """建仓日一字涨停（买不进），次日恢复正常。"""
    d = ["20261007", "20261008", "20261009", "20261010", "20261011", "20261012"]
    # 20261008 一字涨停：hi==lo 且 r=+0.11 (>10% 主板限价)
    c = [10.0, 11.1, 10.0, 10.0, 10.0, 10.0]
    hi = [10.0, 11.1, 10.05, 10.05, 10.05, 10.05]
    lo = [10.0, 11.1, 9.95, 9.95, 9.95, 9.95]
    return FakeBars(d, c, hi, lo)


# ----------------------------------------------------------------- 成交判定
def test_fill_filled_when_free(monkeypatch):
    bar = _free_bar(["20261007", "20261008", "20261009"], [10.0, 10.0, 11.0])
    monkeypatch.setattr(fe, "load_bars", lambda c: bar)
    cal = ["20261007", "20261008", "20261009"]
    pos = {d: i for i, d in enumerate(cal)}
    fd, fp, stt = tb.fill_one("X", "20261008", fe.load_bars, False, cal, pos)
    assert stt == "FILLED"
    assert fd == "20261008"
    assert fp == 10.0


def test_fill_defers_when_limit_up(monkeypatch):
    bar = _lockup_first_then_free()
    monkeypatch.setattr(fe, "load_bars", lambda c: bar)
    cal = ["20261007", "20261008", "20261009", "20261010", "20261011", "20261012"]
    pos = {d: i for i, d in enumerate(cal)}
    fd, fp, stt = tb.fill_one("X", "20261008", fe.load_bars, False, cal, pos)
    assert stt == "DEFERRED"
    assert fd == "20261009"        # 顺延到次日自由成交
    assert fp == 10.0


def test_fill_no_bar(monkeypatch):
    monkeypatch.setattr(fe, "load_bars", lambda c: None)
    cal = ["20261008", "20261009"]
    pos = {d: i for i, d in enumerate(cal)}
    fd, fp, stt = tb.fill_one("X", "20261008", fe.load_bars, False, cal, pos)
    assert stt == "NO_BAR"
    assert fd is None and not np.isfinite(fp)


# ----------------------------------------------------------------- 折算与费用
def test_shares_for_rounds_to_lot():
    assert tb.shares_for(5000, 10.0, 100) == 500
    assert tb.shares_for(5000, 10.3, 100) == 400   # 5000//1030=4 → 400
    assert tb.shares_for(100, 10.0, 100) == 0       # 目标市值不足一手
    assert tb.shares_for(5000, np.nan, 100) == 0


def test_buy_cost_has_min_commission():
    # 5000 元名义：佣金 max(5, 1.25)=5；过户 0.05；半边价差 2.5
    assert abs(tb.buy_cost(500, 10.0) - (5 + 0.05 + 2.5)) < 1e-9
    # 极小名义：佣金触底 5 元
    assert tb.buy_cost(10, 1.0) == 5 + 10 * 1e-5 + 10 * 0.001 * 0.5


# ----------------------------------------------------------------- 逐日盯市
def test_track_nav_cash_and_stock(monkeypatch):
    bar = _free_bar(["20261007", "20261008", "20261009", "20261010", "20261011", "20261012"],
                    [10.0, 10.0, 11.0, 12.0, 11.0, 10.0])
    monkeypatch.setattr(fe, "load_bars", lambda c: bar)
    cal = ["20261007", "20261008", "20261009", "20261010", "20261011", "20261012"]
    pos = {d: i for i, d in enumerate(cal)}
    holds = ["A", "B", "C", "D", "E"]
    # 每只目标市值 = 100000*0.25/5 = 5000 → 500 股 @10
    fills = {c: ("20261008", 10.0) for c in holds}
    df = tb.track_nav(holds, fills, "20261008", "20261012", fe.load_bars,
                      capital=100_000.0, weight=0.25, cal=cal, cal_pos=pos)
    sh = tb.shares_for(100_000.0 * 0.25 / 5, 10.0)
    sv0 = sh * 10.0 * len(holds)
    assert df.iloc[0]["stock_value"] == sv0
    assert abs(df.iloc[0]["cash"] - (100_000 - sv0 - tb.buy_cost(sh, 10.0) * len(holds))) < 1e-4
    nav0 = df.iloc[0]["nav"]
    assert df.iloc[1]["stock_value"] == sh * 11.0 * len(holds)   # 次日价格 11
    assert df.iloc[1]["nav"] > nav0
    assert len(df) == 5


# ----------------------------------------------------------------- 端到端落盘
def test_run_writes_nav_files(monkeypatch, tmp_path):
    bar = _free_bar(["20261007", "20261008", "20261009", "20261010", "20261011", "20261012"],
                    [10.0, 10.0, 11.0, 12.0, 11.0, 10.0])
    monkeypatch.setattr(fe, "load_bars", lambda c: bar)
    monkeypatch.setattr(fe, "market_calendar",
                        lambda: ["20261007", "20261008", "20261009", "20261010", "20261011", "20261012"])
    monkeypatch.setattr(tb, "load_st_months", lambda: {})
    # 指向临时 nav 目录
    monkeypatch.setattr(tb, "NAV_DIR", tmp_path)

    dec = {"date": "20261008", "mode": "FORWARD", "counts_toward_observation": True,
           "target_holds": ["X"], "target_holds_rand5": ["X"]}
    dec_path = tmp_path / "20261008.json"
    dec_path.write_text(json.dumps(dec), encoding="utf-8")

    # 2026-11-02 更正：原先传 selftest=True，与台账 counts_toward_observation=True 矛盾。
    # 那条「显式开关覆盖台账」的行为正是 T7 缺陷（已产出过矛盾文件），
    # 现在 selftest 由台账推导；本测试要验的是 nav 文件是否写出，故不声明。
    rc = tb.run(str(dec_path), end_d="20261012")
    assert rc == 0
    nav_csv = tmp_path / "20261008_nav.csv"
    track_json = tmp_path / "20261008_track.json"
    assert nav_csv.exists() and track_json.exists()
    nav = pd.read_csv(nav_csv)
    assert "nav_top5" in nav.columns and len(nav) == 4  # 建仓日 20261009 → 4 个交易日
    tj = json.loads(track_json.read_text(encoding="utf-8"))
    assert tj["execution_day"] == "20261009"
    assert tj["fills"]["top5:X"]["status"] == "FILLED"
