#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""H1 验收真变异：M4 向下取整、M5 T+1 D0 不卖、M6 D1 失败保持 carry 且升 P0。"""
from __future__ import annotations

import datetime as dt
import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "backend" / "src"))

MIN_SRC = (SCRIPTS / "probe_min_order.py").read_text(encoding="utf-8")
AUTO_SRC = (SCRIPTS / "probe_automation.py").read_text(encoding="utf-8")

MIN_MUTATIONS = [
    ("M4_FLOOR_TO_HALF_UP", [
        ("from decimal import Decimal, ROUND_FLOOR",
         "from decimal import Decimal, ROUND_HALF_UP"),
        ("rounding=ROUND_FLOOR", "rounding=ROUND_HALF_UP"),
    ]),
    # 在 D0 分支注入一笔卖单：原实现无卖单，变异体必须有卖单才叫检出
    ("M5_D0_SELL_INJECTED", [
        ('        rec["include_in_1126_decontam"] = True\n',
         '        rec["include_in_1126_decontam"] = True\n'
         '        gm.order_volume(symbol=CODE, volume=QTY, side=SIDE_SELL,\n'
         '                        order_type=ORDER_TYPE_MARKET,\n'
         '                        position_effect=POSITION_EFFECT_CLOSE, price=0)\n'),
    ]),
]
AUTO_MUTATIONS = [
    ("M6_D1_FAIL_KEEPS_CARRY_OFF", [
        ('        rec.update({"probe_carry": True, "decision": "LIQUIDATION_FAILED",\n'
         '                    "reason": "REJECTED_OR_STILL_HELD"})',
         '        rec.update({"probe_carry": False, "decision": "LIQUIDATION_FAILED",\n'
         '                    "reason": "REJECTED_OR_STILL_HELD"})'),
    ]),
]

ACCT = "0a4706b2-db2c-11f1-937c-00163e022aa6"


class Cash:
    account_id = ACCT
    account_name = ACCT
    available = 100000.0
    nav = 100000.0
    cum_commission = 0.0


class Pos:
    symbol = "601398"
    volume = 100
    cost_price = 8.46
    created_at = "2026-11-23T09:35:10+08:00"


class Gm:
    def __init__(self, raise_sell=False, initial_positions=True):
        self.sent = []
        self.orders = []
        self.positions = [Pos()] if initial_positions else []
        self.raise_sell = raise_sell

    def set_token(self, t): pass
    def set_endpoint(self, e): pass
    def account(self, **k): return k
    def login(self, a): pass
    def get_cash(self): return [Cash()]
    def get_orders(self): return list(self.orders)
    def get_positions(self): return list(self.positions)

    def order_volume(self, **kw):
        self.sent.append(kw)
        if kw.get("side") == 2:
            if self.raise_sell:
                raise RuntimeError("BROKER_REJECT")
            self.positions.clear()
        elif not self.positions:
            self.positions.append(Pos())
        return type("O", (), {"cl_ord_id": f"CL-{len(self.sent)}",
                              "order_status": "filled"})()

    def order_cancel(self, r): self.orders.clear()


def _auth_doc():
    import paper_authorization_guard as guard
    d = {"schema_version": "paper_auth_v1", "channel": "gm-paper", "max_notional": 100000,
         "not_after": "2027-01-31T23:59:00+08:00",
         "scopes": ["query_cash", "query_positions", "query_execution_reports",
                    "sim_rebalance_order"],
         "account_ref": "虚拟盘测试202611", "statement_ref": "TEST",
         "issued_at": "2026-11-22T00:00:00+08:00", "issued_by": "TEST"}
    d["sha256"] = guard.compute_hash(d)
    return d


def _load(tag, text, tmp):
    p = tmp / f"_mut_h1_{tag}.py"
    p.write_text(text, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location(f"_mut_h1_{tag}", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return p, mod


def _min_detects(tag, text, tmp):
    p, mod = _load(tag, text, tmp)
    try:
        if tag == "M4_FLOOR_TO_HALF_UP":
            return mod.limit_down_price("6.11") == "5.50"  # 应为 5.49
        os.environ["DQ_GM_TOKEN"] = "TEST_ONLY"
        os.environ["DQ_GM_ACCOUNT_ID"] = ACCT
        gm = Gm(initial_positions=False)
        mod.run(prev_close="9.40", today=mod.ORDER_DAY, dryrun=False, _gm=gm,
                _auth_doc=_auth_doc(), _prev_source=("20261120", "9.40"),
                check_prev=False)
        return any(x.get("side") == 2 for x in gm.sent)
    finally:
        sys.modules.pop(f"_mut_h1_{tag}", None)
        p.unlink(missing_ok=True)


def _auto_detects(tag, text, tmp):
    p, mod = _load(tag, text, tmp)
    try:
        for name in ("CARRY_STATE", "MIN_ORDER_OUT", "LIQUIDATION_OUT", "ACK_DIR", "FW"):
            setattr(mod, name, tmp / name)
        mod._write(mod.CARRY_STATE, {"probe_carry": True})
        mod._write(mod.MIN_ORDER_OUT, {"exceptions": [
            {"class": "X-FILLED-D0", "symbol": "601398", "qty": 100}]})
        os.environ["DQ_GM_TOKEN"] = "TEST_ONLY"
        os.environ["DQ_GM_ACCOUNT_ID"] = ACCT
        alerts = []
        r = mod.liquidate_probe_carry(
            gm=Gm(raise_sell=True), alert_fn=lambda: alerts.append(1) or {"mocked": True},
            now=dt.datetime(2026, 11, 24, 9, 35,
                            tzinfo=dt.timezone(dt.timedelta(hours=8))))
        return r.get("probe_carry") is False or alerts == []
    finally:
        sys.modules.pop(f"_mut_h1_{tag}", None)
        p.unlink(missing_ok=True)


def _apply(src, pairs, tag):
    out = src
    for old, new in pairs:
        assert old in out, f"假变异：锚点不存在 {tag}"
        out = out.replace(old, new, 1)
    assert out != src, f"变异未改变代码 {tag}"
    return out


def main():
    results = []
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for tag, pairs in MIN_MUTATIONS:
            hit = _min_detects(tag, _apply(MIN_SRC, pairs, tag), tmp)
            results.append(hit)
            print(f"{tag}: {'DETECTED' if hit else 'MISSED'}")
        for tag, pairs in AUTO_MUTATIONS:
            hit = _auto_detects(tag, _apply(AUTO_SRC, pairs, tag), tmp)
            results.append(hit)
            print(f"{tag}: {'DETECTED' if hit else 'MISSED'}")
    ok = all(results)
    print("MUTATION_RESULT:", "PASS" if ok else "FAIL", f"({sum(results)}/{len(results)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
