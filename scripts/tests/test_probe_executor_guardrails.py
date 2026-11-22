#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""专家第11轮 §二/§四/§五：PROBE 执行器三道护栏的 TDD 测试。

红-绿顺序：先写本文件（红），再改 scripts/probe_executor.py（绿）。
不碰真实授权文件、不发任何单：broker 与 _auth_ok 全部替换为本地假对象。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend" / "src"))

import probe_executor as pe  # noqa: E402


class _FakeCash:
    nav = 100000.0
    available = 1234.56
    cum_commission = 0.0


class _FakeGm:
    def cash(self):
        return [_FakeCash()]

    def get_cash(self):
        # 模拟真实 gmtrade：返回 Cash 对象而非 list
        return _FakeCash()


class _FakeBroker:
    instances: list["_FakeBroker"] = []
    placed: list[dict] = []

    def __init__(self, **kwargs):
        self._gm = _FakeGm()
        self.__class__.instances.append(self)

    def place_limit_order(self, *, code, side, qty, price, cid):
        self.__class__.placed.append(
            {"code": code, "side": side, "qty": qty, "price": price, "cid": cid})
        return {"decision": "FAKE_SENT", "client_order_id": cid}


@pytest.fixture(autouse=True)
def _reset_fake(monkeypatch):
    _FakeBroker.instances.clear()
    _FakeBroker.placed.clear()
    # 仅占位（假 broker 不读取值）；真实令牌只由执行机 env 注入
    monkeypatch.setenv("DQ_GM_TOKEN", "TEST_ONLY")
    monkeypatch.setenv("DQ_GM_ACCOUNT_ID", "0a4706b2-db2c-11f1-937c-00163e022aa6")
    # 单测绝不能落 PROBE 委托台账（曾误生成 PROBE_20261125_buy.json）
    monkeypatch.setattr(pe, "_write_json", lambda rel, obj: ROOT / "TEST_ONLY")


def _capture_auth(monkeypatch, available_out):
    def fake_auth(*, auth_path, now, notional, available):
        available_out.append(available)
        return {"decision": "ALLOW"}
    monkeypatch.setattr(pe, "_auth_ok", fake_auth)


@pytest.mark.parametrize("leg,today", [("buy", pe.BUY_DAY), ("sell", pe.SELL_DAY)])
def test_missing_price_exits_before_any_broker_or_order(monkeypatch, leg, today):
    """§二 P0：缺价必须在循环/建连之前整批退出，不能靠循环内 KeyError。"""
    monkeypatch.setattr(pe.ex, "GmTradeBroker", _FakeBroker)
    seen = []
    _capture_auth(monkeypatch, seen)
    run = pe.run_buy if leg == "buy" else pe.run_sell
    r = run(today=today, now="2026-11-25T09:35:00+08:00",
            auth_path=ROOT / "nonexistent_auth.json",
            prices={"600000": "9.80"}, dryrun=False)
    assert r["decision"] == "MISSING_PRICE", r
    assert r["missing"] == ["601398"]
    assert r["n_real_orders"] == 0
    assert _FakeBroker.instances == [], "缺价时不得实例化 broker（连会话都不建）"
    assert _FakeBroker.placed == []
    assert seen == [], "缺价时不得进入授权门"


def test_second_symbol_missing_also_blocks_before_send(monkeypatch):
    """§二最危险场景：第 1 只正常、第 2 只缺价，绝不允许部分发送。"""
    monkeypatch.setattr(pe.ex, "GmTradeBroker", _FakeBroker)
    seen = []
    _capture_auth(monkeypatch, seen)
    r = pe.run_buy(today=pe.BUY_DAY, now="2026-11-25T09:35:00+08:00",
                   auth_path=ROOT / "nonexistent_auth.json",
                   prices={"601398": "6.10"}, dryrun=False)
    assert r["decision"] == "MISSING_PRICE" and r["missing"] == ["600000"]
    assert _FakeBroker.instances == [] and _FakeBroker.placed == []


@pytest.mark.parametrize("leg,today", [("buy", pe.BUY_DAY), ("sell", pe.SELL_DAY)])
def test_dryrun_also_refuses_missing_price(monkeypatch, leg, today):
    seen = []
    _capture_auth(monkeypatch, seen)
    run = pe.run_buy if leg == "buy" else pe.run_sell
    r = run(today=today, now="2026-11-25T09:35:00+08:00",
            auth_path=ROOT / "nonexistent_auth.json",
            prices={"600000": "9.80"}, dryrun=True)
    assert r["decision"] == "MISSING_PRICE"
    assert r["n_real_orders"] == 0


def test_auth_uses_live_available_not_constant_cap(monkeypatch):
    """§四：guard 的 available 必须来自账户快照，不能写死 100000。"""
    monkeypatch.setattr(pe.ex, "GmTradeBroker", _FakeBroker)
    seen = []
    _capture_auth(monkeypatch, seen)
    r = pe.run_buy(today=pe.BUY_DAY, now="2026-11-25T09:35:00+08:00",
                   auth_path=ROOT / "nonexistent_auth.json",
                   prices={"601398": "6.10", "600000": "9.80"}, dryrun=False)
    assert r["decision"] == "EXECUTED", r
    assert seen == [1234.56], seen
    assert r["record"]["available_at_check"] == 1234.56


def test_low_available_blocks_all_orders(monkeypatch):
    monkeypatch.setattr(pe.ex, "GmTradeBroker", _FakeBroker)

    def deny(*, auth_path, now, notional, available):
        return {"decision": "DENY", "reason": "NOTIONAL"}

    monkeypatch.setattr(pe, "_auth_ok", deny)
    r = pe.run_buy(today=pe.BUY_DAY, now="2026-11-25T09:35:00+08:00",
                   auth_path=ROOT / "nonexistent_auth.json",
                   prices={"601398": "6.10", "600000": "9.80"}, dryrun=False)
    assert r["decision"] == "NOT_AUTHORIZED"
    assert _FakeBroker.placed == []


def test_mask_account_never_returns_middle():
    """§五：只回显前4+后4，中间打星；短串全部打星。"""
    assert pe.mask_account("0a4706b2-db2c-11f1-937c-00163e022aa6") == "0a47****************************2aa6"
    assert pe.mask_account("abc") == "***"
    assert "06b2" not in pe.mask_account("0a4706b2-db2c-11f1-937c-00163e022aa6")
