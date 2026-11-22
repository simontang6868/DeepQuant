#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""专家第11轮 §五：连通性探针必须回显掩码账户并硬校账户身份。"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import probe_connectivity_check as pc  # noqa: E402

ACCT = "0a4706b2-db2c-11f1-937c-00163e022aa6"


class _Cash:
    def __init__(self, aid):
        self.account_id = aid
        self.account_name = aid
        self.nav = 100000.0
        self.available = 100000.0


class _FakeApi:
    acct = ACCT

    @staticmethod
    def set_token(t): pass
    @staticmethod
    def set_endpoint(e): pass
    @staticmethod
    def account(**kw): return kw
    @staticmethod
    def login(a): pass
    @staticmethod
    def get_cash(): return [_Cash(_FakeApi.acct)]
    @staticmethod
    def get_positions(): return []


def _install(monkeypatch, tmp_path):
    monkeypatch.setattr(pc, "_gm_module", lambda: _FakeApi)
    monkeypatch.setenv("DQ_GM_TOKEN", "TEST_ONLY")
    monkeypatch.setenv("DQ_GM_ACCOUNT_ID", ACCT)
    monkeypatch.setattr(pc, "OUT", tmp_path / "old.json")
    monkeypatch.setattr(pc, "IDENTITY_OUT", tmp_path / "identity.json")


def test_ok_includes_masked_account(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path)
    assert pc.main() == 0
    rec = json.loads(pc.IDENTITY_OUT.read_text(encoding="utf-8"))
    assert rec["verdict"] == "CONNECT_OK"
    assert rec["account_id_mask"] == "0a47****************************2aa6"
    assert rec["account_env_match"] and rec["account_mask_ok"]
    assert ACCT not in json.dumps(rec, ensure_ascii=False)


def test_wrong_account_hard_fails(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path)
    _FakeApi.acct = "1b111111-db2c-11f1-937c-00163e022aa6"
    try:
        assert pc.main() == 4
        rec = json.loads(pc.IDENTITY_OUT.read_text(encoding="utf-8"))
        assert rec["verdict"] == "CONNECT_IDENTITY_MISMATCH"
        assert not rec["account_env_match"]
    finally:
        _FakeApi.acct = ACCT


def test_missing_env_still_fails_closed(monkeypatch, tmp_path):
    _install(monkeypatch, tmp_path)
    monkeypatch.delenv("DQ_GM_TOKEN")
    assert pc.main() == 1
    rec = json.loads(pc.OUT.read_text(encoding="utf-8"))
    assert rec["verdict"] == "CONNECT_FAIL" and rec["stage"] == "ENV"
