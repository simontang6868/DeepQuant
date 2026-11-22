#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""专家第11轮护栏的真变异测试：每条变异必须被新测试检出。

M1 去掉整批缺价预检 ⇒ 复现「第1只已发出、第2只 KeyError」的部分发送；
M2 把 guard 的 available 写死回 100000 ⇒ 实时额度门失效；
M3 mask_account 回显完整账号 ⇒ 凭证级信息泄漏。

用法：PYTHONPATH="scripts;backend/src" python -u scripts/probe_guardrail_mutation_test.py
"""
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

import probe_executor as pe  # noqa: E402

SRC = (SCRIPTS / "probe_executor.py").read_text(encoding="utf-8")

MUTATIONS = [
    # M1 必须**同时**恢复旧的 `.get(c,0)` 名义金额口径，否则只会在建连前
    # KeyError（干净失败），复现不了「第1只已发出、第2只才崩」的部分发送。
    ("M1_PRECHECK_REMOVED", [
        ("    missing = _require_prices(codes, prices)", "    missing = []"),
        ("notional = sum(float(prices[c]) * LOTS * SHARES_PER_LOT for c in codes)",
         "notional = sum(float(prices.get(c, 0)) * LOTS * SHARES_PER_LOT for c in codes)"),
    ]),
    ("M2_AVAILABLE_HARDCODED", [
        ('available=cash0["available_at_check"]', "available=MAX_NOTIONAL"),
    ]),
    ("M3_ACCOUNT_MASK_REMOVED", [
        ('return s[:4] + "*" * (len(s) - 8) + s[-4:]', "return s"),
    ]),
]


class _FakeCash:
    nav = 100000.0
    available = 1234.56
    cum_commission = 0.0


class _FakeGm:
    def cash(self):
        return [_FakeCash()]


class _FakeBroker:
    placed: list[dict] = []

    def __init__(self, **kwargs):
        self._gm = _FakeGm()

    def place_limit_order(self, *, code, side, qty, price, cid):
        self.__class__.placed.append({"code": code, "price": price})
        return {"decision": "FAKE_SENT", "client_order_id": cid}


def _load(tag: str, text: str):
    p = SCRIPTS / f"_mut_probe_exec_{tag}.py"
    p.write_text(text, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location(f"_mut_probe_exec_{tag}", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return p, mod


def _detects(tag: str, text: str) -> bool:
    p, mod = _load(tag, text)
    saved = pe.ex.GmTradeBroker
    os.environ["DQ_GM_TOKEN"] = "TEST_ONLY"
    os.environ["DQ_GM_ACCOUNT_ID"] = "0a4706b2-db2c-11f1-937c-00163e022aa6"
    _FakeBroker.placed.clear()
    seen: list[float] = []
    try:
        pe.ex.GmTradeBroker = _FakeBroker
        mod._auth_ok = lambda *, auth_path, now, notional, available: (
            seen.append(available) or {"decision": "ALLOW"})
        if tag == "M1_PRECHECK_REMOVED":
            try:
                r = mod.run_buy(today=mod.BUY_DAY, now="2026-11-25T09:35:00+08:00",
                                auth_path=ROOT / "nope.json",
                                prices={"601398": "6.10"}, dryrun=False)
                return r["decision"] != "MISSING_PRICE"
            except KeyError:
                # 部分发送：第 1 只已发出，第 2 只才 KeyError
                return len(_FakeBroker.placed) >= 1
        if tag == "M2_AVAILABLE_HARDCODED":
            mod.run_buy(today=mod.BUY_DAY, now="2026-11-25T09:35:00+08:00",
                        auth_path=ROOT / "nope.json",
                        prices={"601398": "6.10", "600000": "9.80"}, dryrun=False)
            return seen == [100000.0]
        return mod.mask_account("0a4706b2-db2c-11f1-937c-00163e022aa6").count("*") < 24
    finally:
        pe.ex.GmTradeBroker = saved
        sys.modules.pop(f"_mut_probe_exec_{tag}", None)
        p.unlink(missing_ok=True)


def main() -> int:
    out = []
    for tag, pairs in MUTATIONS:
        mutant = SRC
        for old, new in pairs:
            assert old in mutant, f"变异锚点不存在（假变异禁止）: {tag} / {old[:40]}"
            mutant = mutant.replace(old, new, 1)
        assert mutant != SRC, f"变异未改变代码: {tag}"
        detected = _detects(tag, mutant)
        out.append({"mutation": tag, "detected": detected})
        print(f"{tag}: {'DETECTED' if detected else 'MISSED'}")
    ok = all(x["detected"] for x in out)
    print(f"MUTATION_RESULT: {'PASS' if ok else 'FAIL'} ({sum(x['detected'] for x in out)}/{len(out)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
