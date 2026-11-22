#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PA-GM-PROBE-3 最小下单探针 TDD 测试（假 gm 模块，零真实报单）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend" / "src"))

import probe_min_order as pmo  # noqa: E402

ACCT = "0a4706b2-db2c-11f1-937c-00163e022aa6"


class FakeCash:
    def __init__(self, account_id=ACCT, available=100000.0, nav=100000.0):
        self.account_id = account_id
        self.account_name = account_id  # 真实 gm 的 account_name 就是 UUID
        self.available = available
        self.nav = nav
        self.cum_commission = 0.0


class FakeOrder:
    def __init__(self, cl_ord_id="CL-1"):
        self.cl_ord_id = cl_ord_id
        self.order_id = cl_ord_id


class FakeGm:
    def __init__(self):
        self.cash_obj = FakeCash()
        self.orders: list = []
        self.positions: list = []
        self.sent: list[dict] = []
        self.cancelled: list = []
        self.cancel_all_calls = 0
        self.raise_order = False
        self.raise_cancel = False
        self.fill_after_order = False

    def set_token(self, tok): pass
    def set_endpoint(self, ep): pass
    def account(self, **kw): return kw
    def login(self, acc): pass
    def get_cash(self): return [self.cash_obj]
    def get_orders(self): return list(self.orders)
    def get_positions(self): return list(self.positions)

    def order_volume(self, **kw):
        self.sent.append(kw)
        if self.raise_order:
            raise RuntimeError("BROKER_REJECT")
        o = FakeOrder(f"CL-{len(self.sent)}")
        self.orders.append(o)
        if self.fill_after_order:
            self.positions.append({"symbol": "601398", "volume": 100})
            self.orders.clear()
        return o

    def order_cancel(self, reqs):
        self.cancelled.append(reqs)
        if self.raise_cancel:
            raise RuntimeError("CANCEL_REJECT")
        self.orders.clear()

    def order_cancel_all(self):
        self.cancel_all_calls += 1
        self.orders.clear()


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("DQ_GM_TOKEN", "TEST_ONLY")
    monkeypatch.setenv("DQ_GM_ACCOUNT_ID", ACCT)


def test_limit_down_round_half_up():
    assert pmo.limit_down_price("6.11") == "5.50"   # 5.499 → 5.50
    assert pmo.limit_down_price("6.10") == "5.49"   # 5.490 → 5.49
    with pytest.raises(ValueError):
        pmo.limit_down_price("0")


def test_dryrun_never_connects(monkeypatch):
    def boom():
        raise AssertionError("dryrun must not touch gm")
    monkeypatch.setattr(pmo, "_gm_module", boom)
    r = pmo.run(prev_close="6.11", today=pmo.ORDER_DAY, dryrun=True)
    assert r["decision"] == "DRYRUN"
    assert r["price"] == "5.50" and r["qty"] == 100
    assert r["notional"] == 550.0


def _valid_auth_doc():
    import paper_authorization_guard as guard
    d = {"schema_version": "paper_auth_v1", "channel": "gm-paper",
         "max_notional": 100000, "not_after": "2027-01-31T23:59:00+08:00",
         "scopes": ["query_cash", "query_positions", "query_execution_reports",
                    "sim_rebalance_order"],
         "account_ref": "虚拟盘测试202611", "statement_ref": "TEST",
         "issued_at": "2026-11-22T00:00:00+08:00", "issued_by": "TEST"}
    d["sha256"] = guard.compute_hash(d)
    return d


def _run_with(fake: FakeGm, **kw):
    kw.setdefault("_auth_doc", _valid_auth_doc())
    return pmo.run(prev_close="6.11", today=pmo.ORDER_DAY, dryrun=False,
                   _gm=fake, **kw)


def test_full_path_send_then_cancel():
    gm = FakeGm()
    r = _run_with(gm)
    assert r["decision"] == "MIN_ORDER_OK", r
    assert len(gm.sent) == 1 and gm.sent[0]["symbol"] == "601398"
    assert gm.sent[0]["price"] == 5.50 and gm.sent[0]["volume"] == 100
    assert len(gm.cancelled) == 1 and gm.orders == [] and gm.positions == []
    assert r["account_id_mask"] == "0a47****************************2aa6"
    assert ACCT not in str(r), "完整账号不得落盘"


def test_preexisting_orders_blocks_before_any_send():
    gm = FakeGm()
    gm.orders.append(FakeOrder("OTHER"))
    r = _run_with(gm)
    assert r["decision"] == "PREEXISTING_ORDERS" and gm.sent == []
    assert gm.cancelled == [] and gm.cancel_all_calls == 0


def test_account_mismatch_blocks_before_any_send():
    gm = FakeGm()
    gm.cash_obj = FakeCash(account_id="0a4706b2-db2c-11f1-937c-00163e022ab0")
    r = _run_with(gm)
    assert r["decision"] == "ACCOUNT_MISMATCH" and gm.sent == []


def test_order_rejected_no_second_try():
    gm = FakeGm()
    gm.raise_order = True
    r = _run_with(gm)
    assert r["decision"] == "ORDER_REJECTED"
    assert len(gm.sent) == 1 and gm.cancelled == []


def test_cancel_failed_escalates():
    gm = FakeGm()
    gm.raise_cancel = True
    r = _run_with(gm)
    assert r["decision"] == "CANCEL_FAILED" and len(gm.cancelled) == 1


def test_unexpected_fill_escalates_no_auto_sell():
    gm = FakeGm()
    gm.fill_after_order = True
    r = _run_with(gm)
    assert r["decision"] == "UNEXPECTED_FILL"
    assert len(gm.sent) == 1 and len(gm.positions) == 1


def test_cancel_fallback_only_when_no_id_and_no_preexisting():
    class NoIdOrder:
        pass

    gm = FakeGm()
    gm.order_volume = lambda **kw: NoIdOrder()
    r = _run_with(gm)
    assert r["decision"] == "MIN_ORDER_OK"
    assert gm.cancel_all_calls == 1
    assert r["cancel_fallback"] == "ORDER_CANCEL_ALL"
