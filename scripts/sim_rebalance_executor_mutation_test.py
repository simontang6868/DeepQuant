#!/usr/bin/env python3
"""U16-EXEC-1 执行器**真变异**测试：八条，每条必须真改行为并被检出。

纪律：
- 每条变异都让某个**闸门/判据真失效**（放行本该拒绝的，或算错本该正确的）；
- 期望值来自**独立算术/独立实现**，不调用被测函数推导；
- 变异脚本原地改源码期间**不得**并发跑 pytest / 提交；
- 跑完必恢复并复核 sha256。
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "sim_rebalance_executor.py"


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # dataclass 需要模块已注册
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------ 检测器（独立于被测实现）
def d1_idempotent(mod) -> bool:
    a = mod.client_order_id(day="20261201", code="600036", side="BUY", qty=100)
    b = mod.client_order_id(day="20261201", code="600036", side="BUY", qty=100)
    return a != b                    # 不等 ⇒ 变异被检出


def d2_gate_ledger(mod) -> bool:
    with tempfile.TemporaryDirectory() as td:
        g = mod.gate_ledger(Path(td) / "nope.json")
        return g["ok"] is True       # 不存在却放行 ⇒ 检出


def d3_gate_auth(mod) -> bool:
    with tempfile.TemporaryDirectory() as td:
        g = mod.gate_auth(Path(td) / "nope.json", now="2026-12-01T09:35:00+08:00",
                          notional=5000, cumulative=0, available=100000)
        return g["ok"] is True       # 无授权却放行 ⇒ 检出


def d4_lot_floor(mod) -> bool:
    px = lambda c: {"600519": 1500.0, "600036": 35.0, "601318": 45.0,
                    "600276": 40.0, "601888": 70.0}.get(c)   # noqa: E731
    led = {"mode": "FORWARD",
           "target_holds_d4": ["600519", "600036", "601318", "600276", "601888"]}
    plan = mod.build_plan(ledger=led, positions={}, cash=100000.0, price_of=px)
    q = {i.code: i.qty for i in plan}
    return q.get("600036") != 100    # 应为 floor(5000/35/100)*100 = 100


def d5_exit_non_target(mod) -> bool:
    px = lambda c: {"600519": 1500.0, "600036": 35.0, "601318": 45.0,
                    "600276": 40.0, "601888": 70.0, "000001": 12.0}.get(c)  # noqa: E731
    led = {"mode": "FORWARD",
           "target_holds_d4": ["600519", "600036", "601318", "600276", "601888"]}
    plan = mod.build_plan(ledger=led, positions={"000001": 800}, cash=100000.0, price_of=px)
    return not any(i.code == "000001" and i.side == "SELL" and i.qty == 800 for i in plan)


def d6_over_budget_warning(mod) -> bool:
    px = lambda c: {"600519": 1500.0, "600036": 35.0, "601318": 45.0,
                    "600276": 40.0, "601888": 70.0}.get(c)   # noqa: E731
    led = {"mode": "FORWARD",
           "target_holds_d4": ["600519", "600036", "601318", "600276", "601888"]}
    warns: list[str] = []
    mod.build_plan(ledger=led, positions={}, cash=100000.0, price_of=px, warnings=warns)
    return not any("OVER_BUDGET:600519" in w for w in warns)


def d7_execute_rc(mod) -> bool:
    mod.gate_isolation_manifest = lambda runner=None: {"gate": "E4", "ok": False,
                                                       "reason": "FAILED:X"}
    return mod.main(["--execute"]) != 4          # 门控不过却非 4 ⇒ 检出


def d8_gate_e4(mod) -> bool:
    g = mod.gate_isolation_manifest(runner=lambda c: (7, "FAIL 2 项"))
    return g["ok"] is True                       # 隔离失败却通过 ⇒ 检出


def d9_d4_price_coverage(mod) -> bool:
    """M-D4A 检测：d4 目标缺价格，E1 应失败关闭；若放行 ⇒ 检出。"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        led = mod._fake_ledger()
        led["ref_close_d4"].pop(next(iter(led["ref_close_d4"])))  # 删一个价 ⇒ 覆盖不全
        p = Path(td) / "led.json"
        p.write_text(json.dumps(led), encoding="utf-8")
        g = mod.gate_ledger(p)
        return g["ok"] is True                   # 覆盖不全却放行 ⇒ 检出


def d10_d4_price_source(mod) -> bool:
    """M-D4B 检测：ref_close_d4 字段被换成空 dict，E1 应失败；若放行 ⇒ 检出。"""
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        led = mod._fake_ledger()
        led["ref_close_d4"] = {}                 # d4 价格映射丢失
        p = Path(td) / "led.json"
        p.write_text(json.dumps(led), encoding="utf-8")
        g = mod.gate_ledger(p)
        return g["ok"] is True                   # 无 d4 价格却放行 ⇒ 检出


MUTATIONS = [
    ("M1_IDEMPOTENT",
     'raw = f"{day}|{code}|{side}|{qty}".encode("utf-8")',
     'raw = f"{day}|{code}|{side}|{qty}|{__import__(\'os\').urandom(4).hex()}".encode("utf-8")',
     d1_idempotent, "幂等键掺入随机盐 ⇒ 同一四元组 cid 变化"),
    ("M2_GATE_LEDGER_MISSING_PASS",
     '    if not p.is_file():\n        return {"gate": "E1", "ok": False, "reason": "LEDGER_MISSING", "path": str(p)}',
     '    if not p.is_file():\n        return {"gate": "E1", "ok": True, "reason": None, "path": str(p)}',
     d2_gate_ledger, "台账不存在也放行"),
    ("M3_GATE_AUTH_NO_FILE_ALLOW",
     '        return {"gate": "E3", "ok": False, "reason": "NO_AUTH", "path": str(p)}',
     '        return {"gate": "E3", "ok": True, "reason": None, "path": str(p)}',
     d3_gate_auth, "授权文件缺失也放行（NO_AUTH 分支失效）"),
    ("M4_LOT_CEIL_INSTEAD_OF_FLOOR",
     "        target = int(math.floor(per / px_f / LOT)) * LOT",
     "        target = int(math.ceil(per / px_f / LOT)) * LOT",
     d4_lot_floor, "向上取整 ⇒ 超出每只预算"),
    ("M5_SKIP_EXIT_NON_TARGET",
     "        if not c or c in codes or int(qty) <= 0:",
     "        if True:",
     d5_exit_non_target, "非目标持仓不清仓"),
    ("M6_SILENT_OVER_BUDGET",
     "            if warnings is not None:",
     "            if False:",
     d6_over_budget_warning, "超预算静默 ⇒ 无警告"),
    ("M7_EXECUTE_RC_ZERO",
     '                              "n_real_orders": 0,\n'
     '                              "limitation": IMPLEMENTATION_LIMITATION},\n'
     '                             ensure_ascii=False))\n'
     '            return 4',
     '                              "n_real_orders": 0,\n'
     '                              "limitation": IMPLEMENTATION_LIMITATION},\n'
     '                             ensure_ascii=False))\n'
     '            return 0',
     d7_execute_rc, "门控不过仍返回 0 ⇒ 假成功"),
    ("M8_E4_ISOLATION_ALWAYS_OK",
     '    return {"gate": "E4", "ok": not missing,',
     '    return {"gate": "E4", "ok": True,',
     d8_gate_e4, "E4 整体恒通过 ⇒ 隔离/清单失败也放行"),
    ("M9_D4_COVERAGE_ALWAYS_OK",
     "    if missing_px:\n        return {\"gate\": \"E1\", \"ok\": False, \"reason\": \"NO_PRICE_COVERAGE_D4\",",
     "    if False:\n        return {\"gate\": \"E1\", \"ok\": False, \"reason\": \"NO_PRICE_COVERAGE_D4\",",
     d9_d4_price_coverage, "D4 价格覆盖校验恒放行 ⇒ 缺价台账被放行"),
    ("M10_D4_TARGET_MISSING_IGNORED",
     "            if nc is None or nc not in norm_px:\n                missing_px.append(str(c))",
     "            if False:\n                missing_px.append(str(c))",
     d10_d4_price_source, "D4 目标缺价不计入 missing ⇒ 空价格映射被放行"),
]


def main() -> int:
    baseline = hashlib.sha256(SRC.read_bytes()).hexdigest()
    backup = SRC.read_bytes()
    results = []
    try:
        for name, old, new, check, why in MUTATIONS:
            text = backup.decode("utf-8")
            if text.count(old) != 1:
                results.append((name, "ANCHOR_INVALID", f"命中 {text.count(old)} 次", why))
                continue
            mut = text.replace(old, new, 1)
            tag = hashlib.sha256(name.encode()).hexdigest()[:8]
            target = HERE / f"_mut_exec_{tag}.py"
            target.write_bytes(mut.encode("utf-8"))
            try:
                mod = load(target, f"_mut_exec_{tag}")
                with contextlib.redirect_stdout(io.StringIO()):
                    detected = bool(check(mod))
            except Exception as exc:            # 变异导致崩溃也算被检出
                detected = True
                results.append((name, "CRASH_DETECTED", f"{type(exc).__name__}", why))
                target.unlink(missing_ok=True)
                continue
            finally:
                target.unlink(missing_ok=True)
            # 复核：变异后源码确实与基线不同（防「假变异」）
            if target.exists():
                target.unlink()
            if hashlib.sha256(mut.encode()).hexdigest() == baseline:
                results.append((name, "FAKE_MUTATION", "变异后哈希未变", why))
                continue
            results.append((name, "DETECTED" if detected else "MISSED", "", why))
    finally:
        SRC.write_bytes(backup)
        after = hashlib.sha256(SRC.read_bytes()).hexdigest()
        if after != baseline:
            print(f"[mut] FATAL 恢复失败 {baseline[:12]} -> {after[:12]}")
            return 3

    n_ok = sum(1 for _, s, _, _ in results if s == "DETECTED")
    for name, status, extra, why in results:
        print(f"  {status:16s} {name:32s} {why} {extra}")
    print(f"[mut] 真变异 {n_ok}/{len(MUTATIONS)} 检出｜恢复后 sha256 {baseline[:12]} 一致")
    return 0 if n_ok == len(MUTATIONS) else 1


if __name__ == "__main__":
    sys.exit(main())
