#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PA-GM-PROBE-3 H1 验收标准 TDD 测试（假 gm 模块，零真实报单）。"""
from __future__ import annotations

import json
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
    def __init__(self, cl_ord_id="CL-1", status="pending"):
        self.cl_ord_id = cl_ord_id
        self.order_id = cl_ord_id
        self.order_status = status


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
        self.available_after = None
        self.cash_calls = 0

    def set_token(self, tok): pass
    def set_endpoint(self, ep): pass
    def account(self, **kw): return kw
    def login(self, acc): pass

    def get_cash(self):
        self.cash_calls += 1
        if self.available_after is None or self.cash_calls == 1:
            return [self.cash_obj]
        after = FakeCash(available=self.available_after)
        after.account_id = self.cash_obj.account_id
        after.account_name = self.cash_obj.account_name
        return [after]

    def get_orders(self): return list(self.orders)
    def get_positions(self): return list(self.positions)

    def order_volume(self, **kw):
        self.sent.append(kw)
        if self.raise_order:
            raise RuntimeError("BROKER_REJECT")
        if kw.get("side") == pmo.SIDE_SELL and kw.get("position_effect") == pmo.POSITION_EFFECT_CLOSE:
            self.positions.clear()
            return FakeOrder(f"CL-{len(self.sent)}", "filled")
        filled = self.fill_after_order and len(self.sent) == 1
        o = FakeOrder(f"CL-{len(self.sent)}", "filled" if filled else "pending")
        if filled:
            self.positions.append({"symbol": "601398", "volume": 100})
        else:
            self.orders.append(o)
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


def test_limit_down_floor_not_round_half_up():
    # P-3：0.90 后向下取整；6.11×0.9=5.499 ⇒ 5.49（不是 5.50）
    assert pmo.limit_down_price("6.11") == "5.49"
    assert pmo.limit_down_price("6.10") == "5.49"
    assert pmo.limit_down_price("9.40") == "8.46"
    with pytest.raises(ValueError):
        pmo.limit_down_price("0")


def test_prev_close_from_independent_data(tmp_path):
    p = tmp_path / "px.csv"
    p.write_text("date,open,high,low,close,volume\n"
                 "2026-11-19,1,1,1,9.100,1\n"
                 "2026-11-20,1,1,1,9.400,1\n"
                 "2026-11-21,1,1,1,9.900,1\n", encoding="utf-8")
    # 11-21 是周六，必须跳过；取上一交易日 11-20
    assert pmo.prev_close_from_data(p) == ("2026-11-20", "9.400")


def test_p1_prev_close_mismatch_halts():
    with pytest.raises(ValueError, match="PREV_CLOSE_MISMATCH"):
        pmo.run(prev_close="9.50", today=pmo.ORDER_DAY, dryrun=True,
                _prev_source=("20261120", "9.40"), check_prev=True)


def test_dryrun_never_connects(monkeypatch):
    def boom():
        raise AssertionError("dryrun must not touch gm")
    monkeypatch.setattr(pmo, "_gm_module", boom)
    r = pmo.run(prev_close="9.40", today=pmo.ORDER_DAY, dryrun=True,
                _prev_source=("20261120", "9.40"))
    assert r["decision"] == "DRYRUN"
    assert r["price"] == "8.46" and r["qty"] == 100
    assert round(r["notional"], 2) == 846.0
    assert r["acceptance"]["P-3"]["observed"]["rounding"] == "ROUND_FLOOR(0.01)"


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
    kw.setdefault("_prev_source", ("20261120", "9.40"))
    kw.setdefault("check_prev", False)
    return pmo.run(prev_close="9.40", today=pmo.ORDER_DAY, dryrun=False,
                   _gm=fake, **kw)


def test_full_path_send_then_cancel_passes_all_v_gates():
    gm = FakeGm()
    r = _run_with(gm)
    assert r["decision"] == "MIN_ORDER_OK", r
    assert len(gm.sent) == 1 and gm.sent[0]["symbol"] == "601398"
    assert gm.sent[0]["price"] == 8.46 and gm.sent[0]["volume"] == 100
    assert len(gm.cancelled) == 1 and gm.orders == [] and gm.positions == []
    for k in ("P-1", "P-2", "P-3", "E-1", "E-2", "E-3", "V-1", "V-2", "V-3"):
        assert r["acceptance"][k]["status"] == "PASS", (k, r["acceptance"][k])
    assert r["account_id_mask"] == "0a47****************************2aa6"
    assert ACCT not in str(r), "完整账号不得落盘"


def test_preexisting_orders_blocks_before_any_send():
    gm = FakeGm()
    gm.orders.append(FakeOrder("OTHER"))
    r = _run_with(gm)
    assert r["decision"] == "PREEXISTING_ORDERS" and gm.sent == []
    assert gm.cancelled == [] and gm.cancel_all_calls == 0


def test_preexisting_positions_blocks():
    gm = FakeGm()
    gm.positions.append({"symbol": "601398", "volume": 100})
    assert _run_with(gm)["decision"] == "PREEXISTING_POSITIONS"


def test_account_mismatch_blocks_before_any_send():
    gm = FakeGm()
    gm.cash_obj = FakeCash(account_id="0a4706b2-db2c-11f1-937c-00163e022ab0")
    assert _run_with(gm)["decision"] == "ACCOUNT_MISMATCH" and gm.sent == []


def test_order_rejected_no_second_try():
    gm = FakeGm()
    gm.raise_order = True
    r = _run_with(gm)
    assert r["decision"] == "ORDER_REJECTED"
    assert len(gm.sent) == 1 and gm.cancelled == []
    assert r["acceptance"]["E-1"]["status"] == "FAIL"


def test_x1_filled_d0_never_sells_t1():
    gm = FakeGm()
    gm.fill_after_order = True
    r = _run_with(gm)
    assert r["decision"] == "X-FILLED-D0"
    # T+1：11-23 当日绝不产生卖单
    assert len(gm.sent) == 1 and gm.sent[0]["side"] == pmo.SIDE_BUY
    assert [x["class"] for x in r["exceptions"]] == ["X-FILLED-D0"]
    assert r["probe_carry"] is True and r["carry_label"] == "PROBE_CARRY"
    assert r["include_in_1126_decontam"] is True
    assert r["acceptance"]["V-1"]["status"] == "SKIP"


def test_x2_cancel_failure():
    gm = FakeGm()
    gm.raise_cancel = True
    r = _run_with(gm)
    assert r["decision"] == "X-CANCEL-FAIL"
    assert r["acceptance"]["E-3"]["status"] == "FAIL"
    assert any(x["code"] == "X-2" for x in r["exceptions"])


def test_x3_available_not_restored():
    gm = FakeGm()
    gm.available_after = 99000.0
    r = _run_with(gm)
    assert r["decision"] == "X-FREEZE"
    assert r["acceptance"]["V-2"]["status"] == "FAIL"
    assert any(x["code"] == "X-3" for x in r["exceptions"])


def test_v4_filled_only_after_artifact_write(tmp_path, monkeypatch):
    out = tmp_path / "probe_min_order_2026-11-23.json"
    monkeypatch.setattr(pmo, "OUT_DRYRUN_REL", str(out))
    rc = pmo.main(["--prev-close", "9.40", "--dryrun", "--skip-prev-data-check"])
    assert rc == 0
    rec = json.loads(out.read_text(encoding="utf-8"))
    assert rec["acceptance"]["V-4"]["status"] == "PASS"
