"""R8 §5：``gm_paper_order_adapter.py`` 单测（离线、零真实委托）。"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import gm_paper_order_adapter as a  # noqa: E402


@pytest.fixture
def auth_ok(tmp_path):
    p = tmp_path / "auth.json"
    a._write_doc(p, a._auth_doc())
    return p


def test_self_check_passes():
    r = a.self_check()
    assert r["decision"] == "PASS" and r["n_real_orders"] == 0
    assert r["statistical_verdict"] == "ENGINEERING_SELF_CHECK_ONLY"


def test_execute_cli_is_always_not_authorized(capsys):
    assert a.main(["--execute"]) == 4
    assert json.loads(capsys.readouterr().out)["decision"] == "NOT_AUTHORIZED"


def test_prereg_is_frozen_and_matches_sidecar():
    assert a.verify_prereg() == a.PREREG_SHA256
    side = (ROOT / a.PREREG_REL).with_suffix(".md.sha256").read_text(encoding="utf-8")
    assert side.startswith(a.PREREG_SHA256)


def test_prereg_missing_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(a, "ROOT", tmp_path)
    with pytest.raises(a.Halt):
        a.verify_prereg()


def test_board_d4_rules():
    assert a.board_ok("600519") and a.board_ok("000001")
    for bad in ("301329", "300750", "688001", "689009", "830001", "430047"):
        assert not a.board_ok(bad), bad
    assert not a.board_ok("SHSE.603977")   # 旧台账整组禁用
    assert a.board_ok("SHSE.600519")       # 带交易所前缀也可解析
    assert not a.board_ok("60051") and not a.board_ok("")


def test_legacy_five_plan_orders_all_forbidden():
    for code in ("600990", "603977", "002012", "301329", "600191"):
        assert not a.board_ok(code), code


def test_client_order_id_is_idempotent_and_independent():
    c1 = a.client_order_id(probe_id="P", code="600519", side="BUY",
                           day="20261117", lots=1)
    c2 = a.client_order_id(probe_id="P", code="SHSE.600519", side="BUY",
                           day="20261117", lots=1)
    c3 = a.client_order_id(probe_id="P", code="600519", side="SELL",
                           day="20261117", lots=1)
    raw = b"P|600519|BUY|20261117|1"
    assert c1 == c2 == "PROBE-" + hashlib.sha256(raw).hexdigest()[:24]
    assert c3 != c1


def test_validate_order_fails_closed_on_each_rule(auth_ok):
    cases = {
        "market": a._req(auth_ok, price=None),
        "big": a._req(auth_ok, price=99.0, notional=9900.0),
        "gem": a._req(auth_ok, code="301329"),
        "st": a._req(auth_ok, is_st=True),
        "illiquid": a._req(auth_ok, liquidity_ok=False),
        "lots": a._req(auth_ok, lots=2),
        "n_symbols": a._req(auth_ok, n_symbols=1),
        "naive_now": a._req(auth_ok, now="2026-11-17T09:35:00"),
    }
    for name, req in cases.items():
        r = a.validate_order(req)
        assert not r["ok"], name


def test_evaluate_allows_only_with_valid_doc(auth_ok, tmp_path):
    ad = a.GmPaperProbeAdapter(broker=object())
    ok = ad.evaluate(a._req(auth_ok))
    assert ok["decision"] == "ALLOW"
    assert ok["intent"]["order_type"] == "LIMIT"
    assert ok["intent"]["mode"] == "PROBE"
    assert ok["intent"]["status"] == "INTENT_ONLY_NOT_SENT"
    assert ok["broker_called"] is False and ad.broker_calls == 0
    # 授权文件缺失 ⇒ DENY:NO_AUTH
    assert ad.evaluate(a._req(tmp_path / "missing.json"))["reason"] == "NO_AUTH"


def test_six_auth_denies(auth_ok, tmp_path):
    ad = a.GmPaperProbeAdapter()
    docs = {
        "schema": a._auth_doc(schema="paper_auth_v0"),
        "channel": a._auth_doc(channel="other"),
        "expiry": a._auth_doc(not_after="2026-11-01T00:00:00+08:00"),
        "scope": a._auth_doc(scopes=["query_cash"]),
        "notional": a._auth_doc(max_notional=100),
        "hash": a._auth_doc(tamper=True),
    }
    for key, doc in docs.items():
        p = tmp_path / f"{key}.json"
        a._write_doc(p, doc)
        r = ad.evaluate(a._req(p))
        assert r["decision"] == "DENY" and r["reason"].startswith("AUTH_"), key


def test_execute_never_calls_broker(auth_ok):
    ad = a.GmPaperProbeAdapter(broker=object())
    r = ad.execute(a._req(auth_ok))
    assert r["decision"] == "NOT_AUTHORIZED" and r["broker_called"] is False
    assert ad.broker_calls == 0
    assert ad.execute(a._req(auth_ok, price=None))["decision"] == "DENY"


def test_source_has_no_real_order_calls():
    """隔离检查：源码里不得出现任何 ``order_*(`` 调用（SDK 函数名只作字符串常量）。"""
    import re
    s = (ROOT / "scripts" / "gm_paper_order_adapter.py").read_text(encoding="utf-8")
    assert not re.findall(r"\border_[A-Za-z_][A-Za-z0-9_]*\s*\(", s)
    for name in a.SDK_ORDER_FUNCTIONS:
        assert f'"{name}"' in s
