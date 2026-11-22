#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""H1 验收标准真变异：P-3 向下取整、X-1 反向卖出，撤掉必须检出。"""
from __future__ import annotations

import importlib.util
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "backend" / "src"))

SRC = (SCRIPTS / "probe_min_order.py").read_text(encoding="utf-8")
MUTATIONS = [
    ("M4_FLOOR_TO_HALF_UP", [("ROUND_FLOOR", "ROUND_HALF_UP")]),
    ("M5_X1_REVERSE_SELL_OFF", [
        ("            rev = gm.order_volume(symbol=CODE, volume=QTY, side=SIDE_SELL,\n"
         "                                  order_type=ORDER_TYPE_MARKET,\n"
         "                                  position_effect=POSITION_EFFECT_CLOSE, price=0)",
         "            rev = None"),
    ]),
]

ACCT = "0a4706b2-db2c-11f1-937c-00163e022aa6"


class FakeCash:
    account_id = ACCT
    account_name = ACCT
    available = 100000.0
    nav = 100000.0
    cum_commission = 0.0


class FakeGm:
    def __init__(self):
        self.sent = []
        self.orders = []
        self.positions = []
    def set_token(self, t): pass
    def set_endpoint(self, e): pass
    def account(self, **k): return k
    def login(self, a): pass
    def get_cash(self): return [FakeCash()]
    def get_orders(self): return list(self.orders)
    def get_positions(self): return list(self.positions)
    def order_volume(self, **kw):
        self.sent.append(kw)
        o = type("O", (), {"cl_ord_id": f"CL-{len(self.sent)}", "order_status": "filled"})()
        if kw.get("side") == 2:
            self.positions.clear()
        else:
            self.positions.append({"symbol": "601398", "volume": 100})
        return o
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
    p = tmp / f"_mut_min_{tag}.py"
    p.write_text(text, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location(f"_mut_min_{tag}", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return p, mod


def detects(tag, text, tmp):
    p, mod = _load(tag, text, tmp)
    try:
        if tag == "M4_FLOOR_TO_HALF_UP":
            return mod.limit_down_price("6.11") == "5.50"  # 应为 5.49
        os.environ["DQ_GM_TOKEN"] = "TEST_ONLY"
        os.environ["DQ_GM_ACCOUNT_ID"] = ACCT
        gm = FakeGm()
        r = mod.run(prev_close="9.40", today=mod.ORDER_DAY, dryrun=False, _gm=gm,
                    _auth_doc=_auth_doc(), _prev_source=("20261120", "9.40"),
                    check_prev=False)
        return r["decision"] == "X-FILLED" and len(gm.sent) == 1
    finally:
        sys.modules.pop(f"_mut_min_{tag}", None)
        p.unlink(missing_ok=True)


def main():
    results = []
    with tempfile.TemporaryDirectory() as d:
        for tag, pairs in MUTATIONS:
            mutant = SRC
            for old, new in pairs:
                assert old in mutant, f"假变异：锚点不存在 {tag}"
                mutant = mutant.replace(old, new)
            assert mutant != SRC, f"变异未改变代码 {tag}"
            hit = detects(tag, mutant, Path(d))
            results.append(hit)
            print(f"{tag}: {'DETECTED' if hit else 'MISSED'}")
    ok = all(results)
    print("MUTATION_RESULT:", "PASS" if ok else "FAIL", f"({sum(results)}/{len(results)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
