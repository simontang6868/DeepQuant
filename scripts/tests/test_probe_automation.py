#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""施工单 A2/A3/A4/A5/A6 的 TDD 测试：失败关闭、不得放宽判据。"""
from __future__ import annotations

import datetime as dt
import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend" / "src"))

import probe_automation as pa  # noqa: E402


@pytest.fixture(autouse=True)
def _tmp_paths(tmp_path, monkeypatch):
    for name in ("PRICE_FILE", "PREFLIGHT_OUT", "DECONTAM_STATE", "MARKET_CAUSE_FILE",
                 "DECONTAM_RESULT", "PREDICT_OUT", "CARD_DIR", "ACK_DIR",
                 "MIN_ORDER_OUT", "CARRY_STATE", "LIQUIDATION_OUT",
                 "PRICE_MODE_FILE", "PRICE_MODE_AUDIT"):
        monkeypatch.setattr(pa, name, tmp_path / name)
    monkeypatch.setattr(pa, "FW", tmp_path)
    monkeypatch.setenv("DQ_GM_TOKEN", "TEST_ONLY")
    monkeypatch.setenv("DQ_GM_ACCOUNT_ID", "0a4706b2-db2c-11f1-937c-00163e022aa6")
    # 既有 A2 测试默认在 H3-A 模式下跑；默认未选定另测
    pa._write(pa.PRICE_MODE_FILE, {"mode": "A", "selected_by": "TEST",
                                    "basis": "TEST", "buy_factor": 1.01,
                                    "sell_factor": 0.99})


# ---------------------------------------------------------------- A2/A3
def test_buy_missing_price_zero_orders(monkeypatch):
    pa.PRICE_FILE.write_text(json.dumps({"601398": "6.10"}), encoding="utf-8")

    def boom(**kw):
        raise AssertionError("缺价时不得调用下单")

    import probe_executor as pe
    monkeypatch.setattr(pe, "run_buy", boom)
    r = pa.buy_from_file()
    assert r["decision"] == "MISSING_PRICE" and r["missing"] == ["600000"]
    assert r["n_real_orders"] == 0


def test_buy_passes_exact_prices(monkeypatch):
    pa.PRICE_FILE.write_text(json.dumps({"601398": "6.10", "600000": "9.80"}), encoding="utf-8")
    import probe_executor as pe
    seen = {}

    def fake(**kw):
        seen.update(kw)
        return {"decision": "DRYRUN", "n_real_orders": 0}

    monkeypatch.setattr(pe, "run_buy", fake)
    r = pa.buy_from_file(dryrun=True)
    assert r["decision"] == "DRYRUN"
    assert seen["prices"] == {"601398": "6.10", "600000": "9.80"}
    assert seen["dryrun"] is True


# ---------------------------------------------------------------- A4
def test_slot_from_clock_strict():
    tz = dt.timezone(dt.timedelta(hours=8))
    assert pa.slot_from_clock(dt.datetime(2026, 11, 26, 10, 0, tzinfo=tz)) == 1
    assert pa.slot_from_clock(dt.datetime(2026, 11, 26, 14, 0, tzinfo=tz)) == 2
    assert pa.slot_from_clock(dt.datetime(2026, 11, 26, 14, 55, tzinfo=tz)) == 3
    assert pa.slot_from_clock(dt.datetime(2026, 11, 26, 11, 0, tzinfo=tz)) == 0


def test_classify_technical_beats_market(monkeypatch):
    pa.MARKET_CAUSE_FILE.write_text(json.dumps({"reason": "LIMIT_UP_DOWN",
                                                "symbols": ["601398"]}), encoding="utf-8")
    st = {"attempts": [{"decision": "PASS", "positions": []},
                       {"decision": "FAIL", "positions": ["601398"],
                        "error_type": "KeyError"}]}
    r = pa.classify(state=st)
    assert r["decision"] == "GATE_FAIL_TECHNICAL"
    assert r["action"] == "NO_POSTPONE_ESCALATE_HUMAN"


def test_classify_market_requires_independent_preobserved_evidence():
    st = {"attempts": [{"decision": "PASS", "positions": []},
                       {"decision": "FAIL", "positions": ["601398"]}]}
    assert pa.classify(state=st)["decision"] == "GATE_FAIL_TECHNICAL"  # 无证据 ⇒ 变严
    good = {"reason": "SUSPENDED", "symbols": ["601398"],
            "source": "exec_all", "producer": "market_data_watch",
            "observed_at": "2026-11-26T14:30:00+08:00"}
    pa.MARKET_CAUSE_FILE.write_text(json.dumps(good), encoding="utf-8")
    r = pa.classify(state=st)
    assert r["decision"] == "GATE_NO_GO_MARKET" and r["action"] == "POSTPONE_BRANCH_B"

    for bad in ({**good, "observed_at": "2026-11-26T15:01:00+08:00"},
                {**good, "producer": "probe_executor"},
                {**good, "source": "probe_executor"},
                {k: v for k, v in good.items() if k != "producer"}):
        pa.MARKET_CAUSE_FILE.write_text(json.dumps(bad), encoding="utf-8")
        assert pa.classify(state=st)["decision"] == "GATE_FAIL_TECHNICAL", bad


def test_classify_clean():
    st = {"attempts": [{"decision": "PASS", "positions": []}]}
    assert pa.classify(state=st)["decision"] == "DECONTAM_OK"


# ---------------------------------------------------------------- A5
def _gates(ok=True, **over):
    d = {"now": pa.EXEC_NOW,
         "gates": [{"gate": f"G{i}", "ok": ok} for i in range(6)],
         "account": {"error": None}, "plan_error": None}
    d.update(over)
    return d


def test_predict_arms_only_when_all_pass():
    calls = []
    r = pa.predict(run_gates=lambda: _gates(), systemctl=lambda a: calls.append(a) or types.SimpleNamespace(returncode=0))
    assert r["decision"] == "ARMED"
    assert calls == [cmd for u in pa.POST_CHAIN_TIMERS for cmd in (["enable", u], ["start", u])]
    assert r["armed"] == list(pa.POST_CHAIN_TIMERS)


def test_predict_mock_e2_pass_and_no_systemctl(tmp_path):
    dry = tmp_path / "DRYRUN_20261127_MOCK.json"
    pre = tmp_path / "preflight_20261130_MOCK.json"
    dry.write_text("{}", encoding="utf-8")
    pre.write_text("{}", encoding="utf-8")
    out = tmp_path / "m1201_predict_MOCK.json"
    r = pa.predict_mock(dryrun_file=dry, preflight_file=pre, out_path=out)
    assert r["E2"]["ok"] is True and r["decision"] == "MOCK_PASS"
    assert r["systemctl_calls_MUST_BE_EMPTY"] == []
    assert json.loads(out.read_text(encoding="utf-8"))["mock"] is True


def test_predict_never_arms_on_fail_or_wrong_time():
    calls = []
    for g in (_gates(ok=False), _gates(now="2026-12-01T09:36:00+08:00"),
              _gates(plan_error="PLAN_FAILED"), _gates(account={"error": "SDK:KeyError"})):
        r = pa.predict(run_gates=lambda g=g: g,
                       systemctl=lambda a: calls.append(a) or types.SimpleNamespace(returncode=0))
        assert r["decision"] == "POSTPONED", r
    assert calls == []


# ---------------------------------------------------------------- A6
def test_cards_keep_human_fields_blank():
    paths = pa.gen_cards()
    assert len(paths) == 3
    for p in paths:
        text = p.read_text(encoding="utf-8")
        if not p.name.startswith("P2"):
            assert "[HUMAN-FILL:" in text
        assert ("快照命令" in text or "撤单命令" in text or p.name.startswith("P2"))


def test_alert_p0_arms_lockdown_with_900s():
    seen = []
    r = pa.alert(level="P0", card=Path("x.md"),
                 alert_id="T1", mail_fn=lambda *a: True, webhook_fn=lambda *a: True,
                 spawn=lambda: seen.append("spawn"))
    assert r["decision"] == "SENT" and r["phone"] == "HUMAN"
    assert r["lockdown_armed_seconds"] == 900 and seen == ["spawn"]


def test_alert_p1_never_arms_lockdown():
    seen = []
    r = pa.alert(level="P1", card=Path("x.md"), alert_id="T2",
                 mail_fn=lambda *a: False, webhook_fn=lambda *a: False,
                 spawn=lambda: seen.append("spawn"))
    assert r["decision"] == "DELIVERY_FAILED" and seen == []


def test_watch_lock_ack_and_disable():
    pa.ACK_DIR.mkdir(parents=True, exist_ok=True)
    (pa.ACK_DIR / "T3.ack").write_text("ok", encoding="utf-8")
    calls = []
    r = pa.watch_lock(alert_id="T3", wait_seconds=1,
                      sleep_fn=lambda s: None,
                      systemctl=lambda a: calls.append(a) or types.SimpleNamespace(stdout=""),
                      mail_fn=lambda *a: True)
    assert r["decision"] == "ACKNOWLEDGED" and calls == []

    (pa.ACK_DIR / "T3.ack").unlink()

    def sc(args):
        calls.append(args)
        out = "deepquant-sim-exec.timer enabled\nother.timer enabled\n" if args[0] == "list-unit-files" else ""
        return types.SimpleNamespace(stdout=out, returncode=0)

    r = pa.watch_lock(alert_id="T3", wait_seconds=1, sleep_fn=lambda s: None,
                      systemctl=sc, mail_fn=lambda *a: True)
    assert r["decision"] == "LOCKDOWN" and r["disabled"] == ["deepquant-sim-exec.timer"]


def test_unlock_requires_written_confirmation_and_reviewed_snapshot():
    pa.ACK_DIR.mkdir(parents=True, exist_ok=True)
    (pa.ACK_DIR / "LOCK1.ack").write_text(
        json.dumps({"written_confirmation": "CONFIRMED_BY_HOLDER", "reviewed_snapshot": False}),
        encoding="utf-8")
    called = []
    assert pa.unlock(alert_id="LOCK1", predict_fn=lambda: called.append(1))["decision"] == "UNLOCK_DENIED"
    assert called == []

    (pa.ACK_DIR / "LOCK1.ack").write_text(
        json.dumps({"written_confirmation": "CONFIRMED_BY_HOLDER", "reviewed_snapshot": True}),
        encoding="utf-8")
    pa._write(pa.FW / "alert_snapshot_LOCK1.json", {"decision": "SNAPSHOT_READONLY"})
    r = pa.unlock(alert_id="LOCK1", predict_fn=lambda: {"decision": "ARMED"})
    assert r["decision"] == "UNLOCK_VIA_A5" and r["a5"]["decision"] == "ARMED"


def test_snapshot_cancel_unknown_id_is_noop():
    class G:
        def set_token(self, t): pass
        def set_endpoint(self, e): pass
        def account(self, **kw): return kw
        def login(self, a): pass
        def get_orders(self): return []
        def get_positions(self): return []
    r = pa.snapshot(cancel_cl_ord_id="NOPE", gm=G())
    assert r["decision"] == "CANCEL_TARGET_NOT_FOUND"


# ---------------------------------------------------------------- P0-2/P0-3
class _Pos:
    symbol = "601398"
    volume = 100
    cost_price = 8.46
    created_at = "2026-11-23T09:35:10+08:00"


class _LiquidationGm:
    def __init__(self, fail=False):
        self.fail = fail
        self.positions = [_Pos()]
        self.sent = []

    def set_token(self, t): pass
    def set_endpoint(self, e): pass
    def account(self, **k): return k
    def login(self, a): pass
    def get_cash(self): return [types.SimpleNamespace(available=100000.0,
                                                       account_id="x", account_name="x")]
    def get_orders(self): return []
    def get_positions(self): return list(self.positions)

    def order_volume(self, **kw):
        self.sent.append(kw)
        if self.fail:
            raise RuntimeError("BROKER_REJECT")
        self.positions.clear()
        return {"cl_ord_id": "SELL-1"}


def _seed_carry():
    pa._write(pa.CARRY_STATE, {"probe_carry": True})
    pa._write(pa.MIN_ORDER_OUT, {"exceptions": [
        {"class": "X-FILLED-D0", "symbol": "601398", "qty": 100,
         "fill_price": 8.46, "order_id_mask": "masked"}]})


def test_liquidation_d1_success_clears_carry():
    _seed_carry()
    gm = _LiquidationGm()
    alerts = []
    r = pa.liquidate_probe_carry(
        gm=gm, alert_fn=lambda: alerts.append(1),
        now=dt.datetime(2026, 11, 24, 9, 35, tzinfo=dt.timezone(dt.timedelta(hours=8))))
    assert r["decision"] == "LIQUIDATED" and r["probe_carry"] is False
    assert gm.sent[0]["side"] == 2 and gm.sent[0]["order_type"] == 2
    assert alerts == [] and not probe_carry_state()


def test_liquidation_d1_failure_keeps_carry_and_raises_p0():
    _seed_carry()
    gm = _LiquidationGm(fail=True)
    alerts = []
    r = pa.liquidate_probe_carry(
        gm=gm, alert_fn=lambda: alerts.append(1) or {"mocked": True},
        now=dt.datetime(2026, 11, 24, 9, 35, tzinfo=dt.timezone(dt.timedelta(hours=8))))
    assert r["decision"] == "LIQUIDATION_FAILED" and r["probe_carry"] is True
    assert alerts == [1] and probe_carry_state()


def test_liquidation_too_early_never_sells():
    _seed_carry()
    gm = _LiquidationGm()
    r = pa.liquidate_probe_carry(
        gm=gm, now=dt.datetime(2026, 11, 23, 9, 36,
                               tzinfo=dt.timezone(dt.timedelta(hours=8))))
    assert r["decision"] == "HALT_TOO_EARLY" and gm.sent == []


def test_positions_labelled_separates_carry():
    _seed_carry()
    gm = _LiquidationGm()
    rows = pa.positions_labelled(gm)
    assert rows[0]["label"] == "PROBE_CARRY" and rows[0]["qty"] == 100


def test_preflight_hard_gate_on_probe_carry(monkeypatch):
    _seed_carry()
    monkeypatch.setattr(pa, "_gm", lambda: _LiquidationGm())
    r = pa.preflight()
    assert "GATE_HALT_PROBE_CARRY" in r["hard"]
    assert r["decision"] == "PREFLIGHT_FAIL"


def probe_carry_state():
    return pa.probe_carry_state()


# ---------------------------------------------------------------- P1-2 H3 modes
def test_default_no_price_mode_halts():
    pa.PRICE_MODE_FILE.unlink(missing_ok=True)
    r = pa.buy_from_file()
    assert r["decision"] == "NO_PRICE_MODE" and r["n_real_orders"] == 0


def test_h3_b_static_price_is_configurable(monkeypatch):
    monkeypatch.setattr(pa, "_close_on_or_before", lambda code, day: (day, 9.40))
    doc = {"buy_factor": 1.01, "sell_factor": 0.99}
    b = pa.static_price(code="601398", day="20261124", mode_doc=doc, side="BUY")
    s = pa.static_price(code="601398", day="20261126", mode_doc=doc, side="SELL")
    assert b["limit_price"] == "9.50" and s["limit_price"] == "9.30"
    assert "不作为成本基线" in b["note"]


def test_price_mode_switch_is_audited():
    r = pa.set_price_mode(mode="B", by="账户持有人", basis="书面确认")
    assert r["decision"] == "MODE_SET" and r["selected_by"] == "账户持有人"
    lines = pa.PRICE_MODE_AUDIT.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and '"mode": "B"' in lines[0]
