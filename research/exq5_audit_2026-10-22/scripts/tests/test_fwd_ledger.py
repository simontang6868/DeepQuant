#!/usr/bin/env python
"""纸面前瞻台账引擎的单测（费率边界 / 拒单顺序 / T+1 / 整手 / 状态哈希）。"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import fwd_ledger as L  # noqa: E402

D = Decimal


def acct(cash="1000000", qty="0", bought="0"):
    a = L.Account(cash=D(cash))
    if D(qty) > 0:
        p = a.pos("600000.SH")
        p.qty = D(qty)
        p.bought_today = D(bought)
    return a


def bar(px="10.00"):
    return L.Bar(D(px), D(px), D(px), D(px))


# ==================== 费用 ====================
def test_base_fee_no_minimum():
    """BASE 无最低佣金：1000 股 @10 → 佣金 2.5 + 过户费 0.1 = 2.6。"""
    f = L.fee(L.BASE, "buy", D("1000"), D("10"))
    assert f == D("2.60"), f
    # 卖出多一笔印花税 0.05% = 5.0
    f2 = L.fee(L.BASE, "sell", D("1000"), D("10"))
    assert f2 == D("7.60"), f2


def test_conservative_min_commission_boundary():
    """CONS 的 5 元下限：小额触底、大额按费率。"""
    small = L.fee(L.CONSERVATIVE, "buy", D("1000"), D("10"))       # 名义 1 万，费率佣金 2.5 < 5
    assert small == D("5.10"), small                                # 5 + 过户费 0.1
    large = L.fee(L.CONSERVATIVE, "buy", D("1000000"), D("10"))    # 名义 1000 万，佣金 2500
    assert large == D("2600.00"), large


def test_decimal_half_up_not_bankers():
    """2.675 必须进到 2.68（内置 round 会给 2.67）。"""
    assert L.q2(D("2.675")) == D("2.68")
    assert round(2.675, 2) == 2.67  # 记录内置行为，证明两者不同


# ==================== 涨跌停 band ====================
def test_board_ratios_and_st_override():
    up, down = L.limit_band(D("10.00"), "600000.SH")
    assert (up, down) == (D("11.00"), D("9.00"))
    up2, down2 = L.limit_band(D("10.00"), "300001.SZ")
    assert (up2, down2) == (D("12.00"), D("8.00"))
    up3, down3 = L.limit_band(D("10.00"), "830001.BJ")
    assert (up3, down3) == (D("13.00"), D("7.00"))
    up4, down4 = L.limit_band(D("10.00"), "600000.SH", is_st=True)
    assert (up4, down4) == (D("10.50"), D("9.50"))


# ==================== 拒单顺序 ====================
def test_limit_down_before_t_plus_1():
    """跌停卖出先命中 limit_down，而不是 t_plus_1（顺序即口径）。"""
    a = acct(qty="1000", bought="1000")
    reason = L.decide("sell", "600000.SH", D("1000"), D("9.00"), a, bar("9.00"), D("10.00"))
    assert reason == "limit_down", reason


def test_t_plus_1():
    a = acct(qty="1000", bought="1000")
    reason = L.decide("sell", "600000.SH", D("1000"), D("10.20"), a, bar("10.20"), D("10.00"))
    assert reason == "t_plus_1", reason


def test_insufficient_position_beats_t_plus_1_when_over_total():
    a = acct(qty="1000", bought="500")
    reason = L.decide("sell", "600000.SH", D("2000"), D("10.20"), a, bar("10.20"), D("10.00"))
    assert reason == "insufficient_position", reason


def test_lot_size_buy_but_sell_odd_allowed():
    a = acct(qty="150")
    assert L.decide("buy", "600000.SH", D("150"), D("10.00"), a, bar(), D("10.00")) == "lot_size"
    assert L.decide("sell", "600000.SH", D("150"), D("10.00"), a, bar(), D("10.00")) is None


def test_insufficient_cash():
    a = acct(cash="1000")
    assert L.decide("buy", "600000.SH", D("1000"), D("10.00"), a, bar(), D("10.00")) == "insufficient_cash"


def test_unknown_symbol_and_suspended():
    assert L.decide("buy", "999999.SH", D("100"), D("10"), acct(), None, None) == "unknown_symbol"
    assert L.decide("buy", "600000.SH", D("100"), D("10"), acct(), None, D("10")) == "suspended"


def test_non_trading_day():
    r = L.decide("buy", "600000.SH", D("100"), D("10"), acct(), bar(), D("10"),
                 is_trading_day=False)
    assert r == "non_trading_day"


# ==================== 状态哈希 ====================
def test_state_hash_deterministic_and_order_insensitive():
    a = {"cash": "100.00", "n": 3, "model": "BASE"}
    b = {"model": "BASE", "n": 3, "cash": "100.00"}
    assert L.state_hash(a) == L.state_hash(b)
    assert L.state_hash(a) == L.state_hash(a)
    assert len(L.state_hash(a)) == 64


def test_canonical_has_no_timestamp():
    """同输入必须同字节：两次 canonical 完全相等。"""
    obj = {"month": "2026-08", "entry": "2026-08-31", "cash": "30000000.00"}
    assert L.canonical(obj) == L.canonical(dict(reversed(list(obj.items()))))


# ==================== 台账行 ====================
def test_ledger_row_fields_and_uninvested():
    row = L.ledger_row(signal_date="2026-08-31", exec_date="2026-09-30", n_pool=50,
                       n_keep=49, gross_alpha_pp=0.11, fee_total=D("1234.56"), net_pp=0.09,
                       cash=D("1500000"), initial_cash=D("30000000"),
                       rejected={"limit_up": 2, "suspended": 1, "lot_size": 0},
                       snapshot={"month": "2026-08"})
    assert set(row) == set(L.LEDGER_FIELDS)
    assert row["uninvested"] == 0.05
    assert '"limit_up": 2' in row["rejected_breakdown"]
    assert '"lot_size"' not in row["rejected_breakdown"]  # 零值不入账
