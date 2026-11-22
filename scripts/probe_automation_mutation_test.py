#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""施工单三道新闸门真变异：每条变异必须被检出（假变异禁止）。"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "backend" / "src"))

SRC = (SCRIPTS / "probe_automation.py").read_text(encoding="utf-8")

MUTATIONS = [
    ("M1_MISSING_PRICE_OFF", [
        ("    missing = [] if rec is None else sorted(c for c in BUY_PRICES\n"
         "                                            if c not in rec or not str(rec[c]).strip())\n"
         "    if rec is None:\n"
         "        missing = sorted(BUY_PRICES)",
         "    missing = []"),
        ("    prices = {c: str(rec[c]).strip() for c in BUY_PRICES}",
         "    prices = {c: str(rec.get(c, '0')).strip() for c in BUY_PRICES}"),
    ]),
    ("M2_NO_MARKET_EVIDENCE_ACCEPTED", [
        ('            rec["decision"] = "GATE_FAIL_TECHNICAL"\n'
         '            rec["reason"] = "NO_MARKET_EVIDENCE"',
         '            rec["decision"] = "GATE_NO_GO_MARKET"\n'
         '            rec["reason"] = "GUESSED_MARKET"'),
    ]),
    ("M3_GATE_ALL_OFF", [
        ("          and all(isinstance(g, dict) and g.get(\"ok\") is True for g in gates)",
         "          and True"),
    ]),
]


def load(tag: str, text: str, tmp: Path):
    p = tmp / f"_mut_auto_{tag}.py"
    p.write_text(text, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location(f"_mut_auto_{tag}", p)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def detects(tag: str, text: str, tmp: Path) -> bool:
    mod = load(tag, text, tmp)
    for n in ("PRICE_FILE", "PREFLIGHT_OUT", "DECONTAM_STATE", "MARKET_CAUSE_FILE",
              "DECONTAM_RESULT", "PREDICT_OUT", "CARD_DIR", "ACK_DIR"):
        setattr(mod, n, tmp / n)
    if tag == "M1_MISSING_PRICE_OFF":
        mod.PRICE_FILE.write_text(json.dumps({"601398": "6.10"}), encoding="utf-8")
        import probe_executor as pe
        saved = pe.run_buy
        seen = []
        pe.run_buy = lambda **kw: seen.append(kw) or {"decision": "FAKE", "n_real_orders": 0}
        try:
            mod.buy_from_file()
            return len(seen) == 1  # 缺价仍下单 ⇒ 变异被检出
        finally:
            pe.run_buy = saved
    if tag == "M2_NO_MARKET_EVIDENCE_ACCEPTED":
        st = {"attempts": [{"decision": "PASS", "positions": []},
                           {"decision": "FAIL", "positions": ["601398"]}]}
        return mod.classify(state=st)["decision"] == "GATE_NO_GO_MARKET"
    gates = {"now": mod.EXEC_NOW, "gates": [{"gate": f"G{i}", "ok": False} for i in range(6)],
             "account": {"error": None}, "plan_error": None}
    r = mod.predict(run_gates=lambda: gates,
                    systemctl=lambda a: type("P", (), {"returncode": 0})())
    return r["decision"] == "ARMED"


def main() -> int:
    out = []
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        for tag, pairs in MUTATIONS:
            mutant = SRC
            for old, new in pairs:
                assert old in mutant, f"假变异：锚点不存在 {tag}"
                mutant = mutant.replace(old, new, 1)
            assert mutant != SRC
            hit = detects(tag, mutant, tmp)
            out.append((tag, hit))
            print(f"{tag}: {'DETECTED' if hit else 'MISSED'}")
    ok = all(h for _, h in out)
    print("MUTATION_RESULT:", "PASS" if ok else "FAIL", f"({sum(h for _, h in out)}/{len(out)})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
