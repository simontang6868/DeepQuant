"""掘金仿真只读适配器的测试。

用 fake gmtrade 注入，**不需要真实账号、不需要网络**。
重点是：失败关闭、不猜协议、绝不下单、与对账脚本的输入契约兼容。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import paper_account_adapter as paa  # noqa: E402

DAY = "20261109"        # 交易日历里的周一
SATURDAY = "20261108"
ENV = {paa.ENV_TOKEN: "FAKE-TOKEN-DO-NOT-LOG",
       paa.ENV_ACCOUNT: "fake-account-id"}


class Cash:
    """仿官方 Cash：**故意没有 total**，但有 nav。"""

    def __init__(self, available=18000.0, frozen=200.0):
        self.available = available
        self.frozen = frozen
        self.nav = 120000.0
        self.order_frozen = 0.0


class Pos:
    def __init__(self, symbol="SHSE.600990", volume=700, available=700, vwap=5.41):
        self.symbol = symbol
        self.volume = volume
        self.available = available
        self.vwap = vwap


class FakeGM:
    def __init__(self, cash=None, poses=None, cash_none=False,
                 plural=True, calls=None):
        self._cash = Cash() if cash is None else cash
        self._poses = [Pos()] if poses is None else poses
        self._cash_none = cash_none
        self._plural = plural
        self.calls = calls if calls is not None else []

    def set_token(self, v):
        self.calls.append("set_token")

    def set_endpoint(self, v):
        self.calls.append("set_endpoint")

    def account(self, account_id, account_alias=""):
        self.calls.append("account")
        return object()

    def login(self, a):
        self.calls.append("login")

    def get_cash(self):
        self.calls.append("get_cash")
        return None if self._cash_none else self._cash

    def get_positions(self):
        self.calls.append("get_positions")
        return self._poses

    def get_position(self):
        self.calls.append("get_position")
        return self._poses

    def order_volume(self, **kw):
        raise AssertionError("适配器绝不能调用下单函数")

    def order_cancel(self, **kw):
        raise AssertionError("适配器绝不能调用下单函数")


def install(monkeypatch, gm, env=None):
    # ⚠️ 必须先存原函数：写成 lambda: paa.read_credentials(...) 会无限递归，
    # 递归异常被 main 的兜底 except 吞成 rc=2，让所有"期望失败"的测试假绿。
    real = paa.read_credentials
    monkeypatch.setattr(paa, "load_gmtrade", lambda: gm)
    monkeypatch.setattr(paa, "read_credentials",
                        lambda: real(ENV if env is None else env))


# ---------------------------------------------------------------- 失败关闭

def test_missing_token_fails_without_leaking_values(monkeypatch, capsys):
    monkeypatch.delenv(paa.ENV_TOKEN, raising=False)
    monkeypatch.delenv(paa.ENV_ACCOUNT, raising=False)
    rc = paa.main(["--date", DAY, "--no-write"])
    out = capsys.readouterr()
    assert rc == paa.EXIT_FAIL
    assert "DQ_GM_TOKEN" in out.err
    assert "FAKE-TOKEN" not in (out.out + out.err)
    assert not paa.OUT.exists()


def test_non_trading_day_fails_closed(monkeypatch, capsys):
    install(monkeypatch, FakeGM())
    rc = paa.main(["--date", SATURDAY, "--no-write"])
    assert rc == paa.EXIT_FAIL
    assert "不是交易日历" in capsys.readouterr().err


def test_unusable_calendar_fails_closed(tmp_path):
    bad = tmp_path / "cal.json"
    bad.write_text(json.dumps({"dates": []}), encoding="utf-8")
    with pytest.raises(paa.AdapterError, match="交易日历为空"):
        paa.is_trading_day(DAY, bad)


def test_missing_sdk_fails_closed(monkeypatch, capsys):
    real = paa.read_credentials
    monkeypatch.setattr(paa, "read_credentials", lambda: real(ENV))

    def boom():
        raise paa.AdapterError("gmtrade 未安装")

    monkeypatch.setattr(paa, "load_gmtrade", boom)
    assert paa.main(["--date", DAY, "--no-write"]) == paa.EXIT_FAIL
    assert "gmtrade 未安装" in capsys.readouterr().err   # 明确原因，防假绿


def test_empty_cash_fails_closed(monkeypatch, capsys):
    install(monkeypatch, FakeGM(cash_none=True))
    assert paa.main(["--date", DAY, "--no-write"]) == paa.EXIT_FAIL
    assert "get_cash() 返回空" in capsys.readouterr().err


def test_unexpected_sdk_exception_does_not_write(monkeypatch, capsys):
    class Broken(FakeGM):
        def get_cash(self):
            raise RuntimeError("含 token 的原始异常不许打印")

    install(monkeypatch, Broken())
    rc = paa.main(["--date", DAY, "--no-write"])
    err = capsys.readouterr().err
    assert rc == paa.EXIT_FAIL
    assert "含 token 的原始异常" not in err
    assert "RuntimeError" in err            # 确实走到了 SDK 异常分支
    assert not paa.OUT.exists()


# ---------------------------------------------------------------- 不猜协议

def test_total_is_arithmetic_not_nav():
    c = paa.cash_of(Cash(available=18000.0, frozen=200.0))
    assert c == {"available": 18000.0, "frozen": 200.0, "total": 18200.0}
    assert c["total"] != Cash().nav


def test_symbol_with_exchange_prefix_is_normalized():
    assert paa.normalize_symbol("SHSE.600990") == "600990"
    assert paa.normalize_symbol("SZSE.000001") == "000001"
    assert paa.normalize_symbol("600990") == "600990"


def test_unknown_symbol_format_is_rejected():
    with pytest.raises(paa.AdapterError, match="无法识别"):
        paa.normalize_symbol("AAPL")
    with pytest.raises(paa.AdapterError):
        paa.build_snapshot(DAY, Cash(), [Pos(symbol="???")])


def test_invalid_available_quantity_is_rejected():
    with pytest.raises(paa.AdapterError, match="可卖数量异常"):
        paa.positions_of([Pos(volume=100, available=200)])


def test_zero_volume_position_is_dropped():
    assert paa.positions_of([Pos(volume=0, available=0)]) == []


def test_missing_field_lists_actual_fields_not_values():
    with pytest.raises(paa.AdapterError, match="实际字段"):
        paa.cash_of(object())


def test_singular_get_position_is_supported(monkeypatch):
    gm = FakeGM(plural=False)
    gm.get_positions = None          # 只剩单数形式
    cash, poses = paa.read_account(gm, paa.read_credentials(ENV))
    assert paa.positions_of(poses)[0]["code"] == "600990"


# ---------------------------------------------------------------- 绝不下单

def test_read_account_never_calls_order_functions():
    gm = FakeGM()
    paa.read_account(gm, paa.read_credentials(ENV))
    assert order_free(gm.calls)


def order_free(calls) -> bool:
    return not [c for c in calls if c.startswith("order_")]


def test_source_has_no_order_calls():
    src = (ROOT / "scripts" / "paper_account_adapter.py").read_text(encoding="utf-8")
    banned = ["order_volume(", "order_batch(", "order_cancel(",
              "order_close_all(", ".place_order(", "send_order("]
    assert not [b for b in banned if b in src]


def test_probe_prints_field_names_only(monkeypatch, capsys):
    install(monkeypatch, FakeGM())
    rc = paa.main(["--date", DAY, "--probe"])
    out = capsys.readouterr()
    assert rc == paa.EXIT_OK
    assert "available" in out.out and "symbol" in out.out
    assert "5.41" not in out.out          # 不打印值
    assert "FAKE-TOKEN" not in out.out


# ---------------------------------------------------------------- 写入与契约

def test_write_is_lf_and_never_overwrites(tmp_path):
    p = tmp_path / "snap.json"
    rec = paa.build_snapshot(DAY, Cash(), [Pos()])
    assert paa.write_snapshot(rec, p) == paa.EXIT_OK
    assert b"\r" not in p.read_bytes()
    assert paa.write_snapshot(rec, p) == paa.EXIT_OK
    rec2 = paa.build_snapshot(DAY, Cash(available=1.0), [])
    assert paa.write_snapshot(rec2, p) == paa.EXIT_CONFLICT
    assert json.loads(p.read_text(encoding="utf-8"))["cash"]["available"] == 18000.0


def test_output_satisfies_shadow_reconcile_contract(tmp_path):
    """⭐ 两端必须咬合：适配器产出的文件要能被对账脚本直接接受。"""
    import shadow_daily_reconcile as sdr
    p = tmp_path / "account_snapshot.json"
    paa.write_snapshot(paa.build_snapshot(DAY, Cash(), [Pos()]), p)
    why, payload = sdr.load_account_snapshot(DAY, p)
    assert why == "ok" and payload["source"] == "gm-paper"
    snap, mode = sdr.make_snapshot(payload, DAY,
                                   {"sleeve": 20000.0})
    assert mode == sdr.REAL_PAPER
    assert snap.cash.total == 18200.0
    assert snap.position_of("600990").quantity == 700


def test_snapshot_asof_is_the_requested_day():
    assert paa.build_snapshot(DAY, Cash(), [])["asof"] == DAY
