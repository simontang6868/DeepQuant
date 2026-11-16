"""U16-EXEC-1 执行器单测（离线，不连仿真、不下单）。"""
from __future__ import annotations

import hashlib
import json
import sys
from fractions import Fraction
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import paper_authorization_guard as guard  # noqa: E402
import sim_rebalance_executor as ex  # noqa: E402

PXS = {"600519": 1500.0, "600036": 35.0, "601318": 45.0,
       "600276": 40.0, "601888": 70.0, "000001": 12.0}


def _px(c):
    return PXS.get(ex.normalize_code(c))


def _ledger(n=5):
    return {"date": ex.LEDGER_DATE, "mode": "FORWARD",
            "target_holds_d4": ["600519", "600036", "601318", "600276", "601888"][:n]}


def _auth_doc(**kw):
    d = {"schema_version": "paper_auth_v1", "channel": "gm-paper",
         "account_ref": "SELFTEST", "max_notional": 100000,
         "not_after": "2026-12-02T23:59:00+08:00",
         "scopes": sorted(guard.FROZEN_SCOPES), "statement_ref": "3207bff41a58",
         "issued_by": "SELFTEST", "issued_at": "2026-11-14T22:40:00+08:00"}
    d.update(kw)
    d["sha256"] = guard.compute_hash({k: v for k, v in d.items() if k != "sha256"})
    return d


# ------------------------------------------------------------ 冻结常量
def test_prereg_sha_matches():
    assert ex.verify_prereg() == ex.PREREG_SHA256


def test_canonical_constants_frozen():
    assert (ex.EXEC_DATE, ex.LEDGER_DATE, ex.FIRST_ORDER_TIME, ex.MAX_REPRICE,
            ex.CARRY_MAX_DAYS, ex.LOT, ex.TOTAL_WEIGHT) == (
        "20261201", "20261130", "09:35", 2, 10, 100, "0.25")


def test_limitation_is_documented_not_hidden():
    """预登记缺口必须显式登记，不得伪装成已实现重定价。"""
    assert ex.REPRICE_IMPLEMENTABLE is False
    assert ex.PRICE_SOURCE == "LEDGER_REF_CLOSE"
    assert "无 tick/quote" in ex.IMPLEMENTATION_LIMITATION


# ------------------------------------------------------------ 计划层
def test_budget_is_total_weight_over_k():
    """每只预算 = NAV × 0.25 ÷ K；K=5 ⇒ 5000 元（不是每只 25%）。"""
    per = Fraction("100000") * Fraction("0.25") / 5
    assert per == 5000


def test_plan_lot_floor_and_over_budget_warning():
    warns: list[str] = []
    plan = ex.build_plan(ledger=_ledger(), positions={}, cash=100000.0,
                         price_of=_px, warnings=warns)
    # 600036: floor(5000/35/100)*100 = 100
    i36 = next(i for i in plan if i.code == "600036")
    assert (i36.side, i36.qty, i36.price) == ("BUY", 100, "35")
    # 601318: floor(5000/45/100)*100 = 100
    assert next(i for i in plan if i.code == "601318").qty == 100
    # 600276: floor(5000/40/100)*100 = 100
    assert next(i for i in plan if i.code == "600276").qty == 100
    # 600519(一手 15 万)、601888(一手 7000) 超预算 ⇒ 无单 + 显式警告
    codes = {i.code for i in plan}
    assert "600519" not in codes and "601888" not in codes
    assert any("OVER_BUDGET:600519" in w for w in warns)
    assert any("OVER_BUDGET:601888" in w for w in warns)
    assert len(plan) == 3


def test_exit_non_target_sells_full_position():
    plan = ex.build_plan(ledger=_ledger(), positions={"000001": 800},
                         cash=100000.0, price_of=_px)
    exit_leg = [i for i in plan if i.code == "000001"]
    assert len(exit_leg) == 1
    assert exit_leg[0].side == "SELL" and exit_leg[0].qty == 800
    assert exit_leg[0].reason == "EXIT_NON_TARGET"


def test_rebalance_sell_when_overweight():
    plan = ex.build_plan(ledger=_ledger(), positions={"600036": 300},
                         cash=100000.0, price_of=_px)
    i36 = next(i for i in plan if i.code == "600036")
    assert i36.side == "SELL" and i36.qty == 200     # 300 → 100
    assert i36.target_qty == 100 and i36.cur_qty == 300


def test_no_price_fails_closed():
    with pytest.raises(ex.Halt) as ei:
        ex.build_plan(ledger=_ledger(), positions={}, cash=100000.0,
                      price_of=lambda c: None)
    assert "NO_PRICE" in str(ei.value)


def test_zero_cash_fails_closed():
    with pytest.raises(ex.Halt) as ei:
        ex.build_plan(ledger=_ledger(), positions={}, cash=0.0, price_of=_px)
    assert "ZERO_BUDGET" in str(ei.value)


def test_bad_target_codes_fail_closed():
    with pytest.raises(ex.Halt):
        ex.build_plan(ledger={"target_holds_d4": ["abc"]}, positions={},
                      cash=100000.0, price_of=_px)


# ------------------------------------------------------------ 幂等
def test_idempotent_cid_matches_independent_hash():
    cid = ex.client_order_id(day="20261201", code="600036", side="BUY", qty=100)
    raw = "20261201|600036|BUY|100".encode()
    assert cid == "SIM_DRILL-" + hashlib.sha256(raw).hexdigest()[:24]
    assert cid == ex.client_order_id(day="20261201", code="600036", side="BUY", qty=100)
    # 任一维度变化 ⇒ cid 变化
    assert cid != ex.client_order_id(day="20261201", code="600036", side="BUY", qty=200)


def test_append_orders_dedups_by_cid(tmp_path):
    p = tmp_path / "o.jsonl"
    rows = [{"client_order_id": "SIM_DRILL-a", "code": "600036"}]
    assert ex.append_orders(p, rows) == 1
    assert ex.append_orders(p, rows) == 0
    assert len(p.read_text(encoding="utf-8").splitlines()) == 1


# ------------------------------------------------------------ 四道门控
def test_gate_ledger_requires_forward_and_d4(tmp_path):
    assert ex.gate_ledger(tmp_path / "none.json")["ok"] is False
    bad = tmp_path / "m.json"
    bad.write_text(json.dumps({"mode": "LATE_RUN", "target_holds_d4": ["600036"]}),
                   encoding="utf-8")
    assert ex.gate_ledger(bad)["ok"] is False
    legacy = tmp_path / "legacy.json"
    legacy.write_text(json.dumps({"mode": "FORWARD", "target_holds": ["600036"]}),
                      encoding="utf-8")
    g = ex.gate_ledger(legacy)
    assert g["ok"] is False and g["reason"] == "NO_TARGET_HOLDS_D4"
    ok = tmp_path / "ok.json"
    ok.write_text(json.dumps(_ledger()), encoding="utf-8")
    assert ex.gate_ledger(ok)["ok"] is True


def test_gate_dryrun_needs_both_artifacts(tmp_path):
    assert ex.gate_dryrun()["ok"] is False
    d = tmp_path / "d.json"
    d.write_text("{}", encoding="utf-8")
    # 只有 DRYRUN，缺 preflight ⇒ 仍不过
    assert ex.gate_dryrun(patterns=(str(d),), preflight_patterns=("nope*",))["ok"] is False
    p = tmp_path / "p.json"
    p.write_text("{}", encoding="utf-8")
    assert ex.gate_dryrun(patterns=(str(d),), preflight_patterns=(str(p),))["ok"] is True


def test_gate_auth_deny_and_allow(tmp_path):
    assert ex.gate_auth(tmp_path / "none.json", now="2026-12-01T09:35:00+08:00",
                        notional=5000, cumulative=0, available=100000)["ok"] is False
    good = tmp_path / "a.json"
    good.write_text(json.dumps(_auth_doc(), ensure_ascii=False), encoding="utf-8")
    assert ex.gate_auth(good, now="2026-12-01T09:35:00+08:00", notional=5000,
                        cumulative=0, available=100000)["ok"] is True
    # 过期 ⇒ DENY
    exp = tmp_path / "e.json"
    exp.write_text(json.dumps(_auth_doc(not_after="2026-11-01T00:00:00+08:00"),
                              ensure_ascii=False), encoding="utf-8")
    assert ex.gate_auth(exp, now="2026-12-01T09:35:00+08:00", notional=5000,
                        cumulative=0, available=100000)["ok"] is False
    # 篡改哈希 ⇒ DENY
    t = tmp_path / "t.json"
    d = _auth_doc()
    d["sha256"] = "0" * 64
    t.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    assert ex.gate_auth(t, now="2026-12-01T09:35:00+08:00", notional=5000,
                        cumulative=0, available=100000)["ok"] is False


def test_gate_isolation_manifest_uses_runner():
    assert ex.gate_isolation_manifest(runner=lambda c: (7, "FAIL 2 项"))["ok"] is False
    assert ex.gate_isolation_manifest(runner=lambda c: (0, "PASS 57/57 ｜ FAIL 0 项"))["ok"] is True


def test_run_gates_all_deny_when_inputs_missing(tmp_path):
    gates = ex.run_gates(now="2026-12-01T09:35:00+08:00", notional=5000,
                         cumulative=0, available=100000,
                         ledger_path=tmp_path / "none.json",
                         auth_path=tmp_path / "none.json",
                         runner=lambda c: (7, "FAIL"))
    assert [g["ok"] for g in gates] == [False, False, False, False]


# ------------------------------------------------------------ CLI 闸门
def test_main_execute_is_not_authorized(monkeypatch):
    """门控未过 ⇒ --execute 恒 rc=4，且绝不触碰 broker。"""
    monkeypatch.setattr(ex, "gate_isolation_manifest",
                        lambda runner=None: {"gate": "E4", "ok": False, "reason": "FAILED:X"})
    touched = {"n": 0}

    class Boom:
        def __init__(self, **kw):
            touched["n"] += 1
            raise AssertionError("broker 不得被实例化")

    monkeypatch.setattr(ex, "GmTradeBroker", Boom)
    assert ex.main(["--execute"]) == 4
    assert touched["n"] == 0


def test_main_self_check():
    """--self-check 纯离线；内部 gate_e4 用注入 runner，不连云端。"""
    assert ex.main(["--self-check"]) == 0


def test_main_gates_and_plan_not_authorized(monkeypatch):
    monkeypatch.setattr(ex, "gate_isolation_manifest",
                        lambda runner=None: {"gate": "E4", "ok": False, "reason": "FAILED:X"})
    assert ex.main(["--gates"]) == 4
    assert ex.main(["--plan"]) == 4
