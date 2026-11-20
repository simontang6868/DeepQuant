#!/usr/bin/env python3
"""U16 执行器 + p13 台账**真变异**测试（评审整改版 2026-11-21）。

纪律（docs/CHECKER_AND_MUTATION_NORM_2026-11-19.md）：
- 每条变异都让某个**闸门/判据真失效**（放行本该拒绝的，或算错本该正确的）；
- **§2.1 金丝雀前置**：每条变异先在基线模块跑同一检测器，必须 False（基线干净），
  再在变异体跑，必须 True（检出）——否则判 VOID，不计入 DETECTED；
- **§2.2 直接调用被测单元**：importlib 按文件路径加载变异副本并直接调用，
  禁止经会 sys.path.insert 原文件的 pytest 收集路径；
- **§3 阴性对照**：每个检测器就是"已知坏输入必须被检出"的阴性对照；
- **判据邻接**：M-D4A 变异 p13（删 ref_close_d4 输出）、M-D4B 变异执行器价格源
  （改回读 orders）——二者来自预登记 D4_PRICE_COVERAGE_FIX_PREREG §4，
  上一版错把两条门控变异冒充它们，本版补齐；
- 期望值来自独立算术/夹具，不调用被测函数推导其判定结果；
- 跑完必恢复并复核 sha256（三合一退出条件 base_ok ∧ detected ∧ sha_stable）。
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SRC = HERE / "sim_rebalance_executor.py"
P13_SRC = (REPO / "research" / "stock_pick_2026-10-25" / "scripts" / "sp5"
           / "p13_forward.py")


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod          # dataclass 需要模块已注册
    spec.loader.exec_module(mod)
    return mod


# ------------------------------------------------------------ 检测器（独立于被测实现）
# 约定：检测器返回 True = "变异被检出"（坏行为出现）；基线必须 False。
PX = {"600519": 1500.0, "600036": 35.0, "601318": 45.0,
      "600276": 40.0, "601888": 70.0, "000001": 12.0}
CODES = ["600519", "600036", "601318", "600276", "601888"]


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
    px = lambda c: PX.get(c)  # noqa: E731
    led = {"mode": "FORWARD", "target_holds_d4": CODES}
    plan = mod.build_plan(ledger=led, positions={}, cash=100000.0, price_of=px)
    q = {i.code: i.qty for i in plan}
    return q.get("600036") != 100    # 应为 floor(5000/35/100)*100 = 100


def d5_exit_non_target(mod) -> bool:
    px = lambda c: PX.get(c)  # noqa: E731
    led = {"mode": "FORWARD", "target_holds_d4": CODES}
    plan = mod.build_plan(ledger=led, positions={"000001": 800}, cash=100000.0, price_of=px)
    return not any(i.code == "000001" and i.side == "SELL" and i.qty == 800 for i in plan)


def d6_over_budget_warning(mod) -> bool:
    px = lambda c: PX.get(c)  # noqa: E731
    led = {"mode": "FORWARD", "target_holds_d4": CODES}
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
    """门控变异检测：d4 目标缺价台账被 gate_ledger 放行 ⇒ 检出。"""
    with tempfile.TemporaryDirectory() as td:
        led = mod._fake_ledger()
        led["ref_close_d4"].pop(next(iter(led["ref_close_d4"])))
        p = Path(td) / "led.json"
        p.write_text(json.dumps(led), encoding="utf-8")
        return mod.gate_ledger(p)["ok"] is True


def d10_d4_target_missing_ignored(mod) -> bool:
    """门控变异检测：目标缺价不计入 missing ⇒ 空价格映射被放行 ⇒ 检出。"""
    with tempfile.TemporaryDirectory() as td:
        led = mod._fake_ledger()
        led["ref_close_d4"] = {}
        p = Path(td) / "led.json"
        p.write_text(json.dumps(led), encoding="utf-8")
        return mod.gate_ledger(p)["ok"] is True


def d_md4a_p13_no_price_field(mod) -> bool:
    """预登记 §4 M-D4A 检测：p13 的 build_d4_targets 不再输出 ref_close_d4 ⇒ 检出。

    真实数据 asof 20261030；基线应输出非空 ref_close_d4（⇒ False），
    变异体删掉输出后为 None（⇒ True）。
    """
    asof = "20261030"
    raw = mod.build_snapshot(asof)
    base = raw.attrs["base_filter"]
    out = mod.build_d4_targets(raw, base, asof)
    return not bool(out.get("ref_close_d4"))     # 无价格字段 ⇒ 检出


def d_md4b_price_source_orders(mod) -> bool:
    """预登记 §4 M-D4B 检测：价格源改回 orders ⇒ 检出。

    构造只有 orders 价格、无 ref_close_d4 的台账：
    - 基线 price_map_from_ledger 只读 ref_close_d4 ⇒ 返回空（⇒ False）
    - 变异体改回读 orders ⇒ 返回非空（⇒ True，说明价格源被改回主口径）
    """
    rec = {"target_holds_d4": CODES,
           "ref_close_d4": {},
           "orders": [{"code": c, "ref_close": PX[c]} for c in CODES]}
    return bool(mod.price_map_from_ledger(rec))  # 读到了 orders 价 ⇒ 检出


# 六元组：(name, source, old, new, check, why)
MUTATIONS = [
    ("M1_IDEMPOTENT", SRC,
     'raw = f"{day}|{code}|{side}|{qty}".encode("utf-8")',
     'raw = f"{day}|{code}|{side}|{qty}|{__import__(\'os\').urandom(4).hex()}".encode("utf-8")',
     d1_idempotent, "幂等键掺入随机盐 ⇒ 同一四元组 cid 变化"),
    ("M2_GATE_LEDGER_MISSING_PASS", SRC,
     '    if not p.is_file():\n        return {"gate": "E1", "ok": False, "reason": "LEDGER_MISSING", "path": str(p)}',
     '    if not p.is_file():\n        return {"gate": "E1", "ok": True, "reason": None, "path": str(p)}',
     d2_gate_ledger, "台账不存在也放行"),
    ("M3_GATE_AUTH_NO_FILE_ALLOW", SRC,
     '        return {"gate": "E3", "ok": False, "reason": "NO_AUTH", "path": str(p)}',
     '        return {"gate": "E3", "ok": True, "reason": None, "path": str(p)}',
     d3_gate_auth, "授权文件缺失也放行（NO_AUTH 分支失效）"),
    ("M4_LOT_CEIL_INSTEAD_OF_FLOOR", SRC,
     "        target = int(math.floor(per / px_f / LOT)) * LOT",
     "        target = int(math.ceil(per / px_f / LOT)) * LOT",
     d4_lot_floor, "向上取整 ⇒ 超出每只预算"),
    ("M5_SKIP_EXIT_NON_TARGET", SRC,
     "        if not c or c in codes or int(qty) <= 0:",
     "        if True:",
     d5_exit_non_target, "非目标持仓不清仓"),
    ("M6_SILENT_OVER_BUDGET", SRC,
     "            if warnings is not None:",
     "            if False:",
     d6_over_budget_warning, "超预算静默 ⇒ 无警告"),
    ("M7_EXECUTE_RC_ZERO", SRC,
     '                              "n_real_orders": 0,\n'
     '                              "limitation": IMPLEMENTATION_LIMITATION},\n'
     '                             ensure_ascii=False))\n'
     '            return 4',
     '                              "n_real_orders": 0,\n'
     '                              "limitation": IMPLEMENTATION_LIMITATION},\n'
     '                             ensure_ascii=False))\n'
     '            return 0',
     d7_execute_rc, "门控不过仍返回 0 ⇒ 假成功"),
    ("M8_E4_ISOLATION_ALWAYS_OK", SRC,
     '    return {"gate": "E4", "ok": not missing,',
     '    return {"gate": "E4", "ok": True,',
     d8_gate_e4, "E4 整体恒通过 ⇒ 隔离/清单失败也放行"),
    # 门控额外覆盖（不是预登记 §4 的 M-D4A/B，只是 E1 门的两条补充变异）
    ("M9_E1_COVERAGE_ALWAYS_OK", SRC,
     "    if missing_px:\n        return {\"gate\": \"E1\", \"ok\": False, \"reason\": \"NO_PRICE_COVERAGE_D4\",",
     "    if False:\n        return {\"gate\": \"E1\", \"ok\": False, \"reason\": \"NO_PRICE_COVERAGE_D4\",",
     d9_d4_price_coverage, "E1 覆盖校验恒放行 ⇒ 缺价台账被放行（额外门控变异）"),
    ("M10_E1_MISSING_IGNORED", SRC,
     "        if nc is None or nc not in norm_px:\n            missing_px.append(str(c))",
     "        if False:\n            missing_px.append(str(c))",
     d10_d4_target_missing_ignored, "目标缺价不计入 missing ⇒ 空映射被放行（额外门控变异）"),
    # 预登记 §4 指定的两条（上一版漏做，本版补齐）
    ("M-D4A_P13_NO_PRICE_FIELD", P13_SRC,
     '        out[f"ref_close_{key}"] = {\n'
     '            str(r["code"]): float(r["close"])\n'
     '            for _, r in top.iterrows()\n'
     '            if np.isfinite(r["close"]) and float(r["close"]) > 0\n'
     '        }\n',
     '',
     d_md4a_p13_no_price_field, "预登记§4 M-D4A：p13 删 ref_close_d4 输出 ⇒ E1 应拒绝"),
    ("M-D4B_PRICE_SOURCE_ORDERS", SRC,
     '    raw = rec.get("ref_close_d4")\n    if not isinstance(raw, dict):',
     '    raw = {o.get("code"): o.get("ref_close") for o in (rec.get("orders") or [])}\n'
     '    if not isinstance(raw, dict):',
     d_md4b_price_source_orders, "预登记§4 M-D4B：价格源改回 orders ⇒ 主口径价混入 D4"),
]


def main() -> int:
    # 每个变异源各自记录基线与恢复哈希（三合一：base_ok ∧ detected ∧ sha_stable）
    sources = {p: (hashlib.sha256(p.read_bytes()).hexdigest(), p.read_bytes())
               for p in {m[1] for m in MUTATIONS}}
    results: list[tuple[str, str, str, str]] = []
    try:
        for name, src, old, new, check, why in MUTATIONS:
            baseline, backup = sources[src]
            text = backup.decode("utf-8")
            if text.count(old) != 1:
                results.append((name, "ANCHOR_INVALID", f"命中 {text.count(old)} 次", why))
                continue

            # §2.1 金丝雀前置：基线模块必须先给出阴性（check=False）
            base_mod = load(src, f"_base_{name}")
            with contextlib.redirect_stdout(io.StringIO()):
                base_bad = bool(check(base_mod))
            if base_bad:
                results.append((name, "VOID_BASE_NOT_CLEAN", "基线即检出", why))
                continue

            mut = text.replace(old, new, 1)
            if hashlib.sha256(mut.encode()).hexdigest() == baseline:
                results.append((name, "FAKE_MUTATION", "变异后哈希未变", why))
                continue
            tag = hashlib.sha256(name.encode()).hexdigest()[:8]
            # p13 变异体必须写在其自身目录（顶部 sys.path.insert 依赖 __file__ 位置）
            target = (src.parent / f"_mut_{tag}.py") if src == P13_SRC else (HERE / f"_mut_{tag}.py")
            target.write_bytes(mut.encode("utf-8"))
            try:
                mod = load(target, f"_mut_{tag}")
                with contextlib.redirect_stdout(io.StringIO()):
                    detected = bool(check(mod))
            except Exception as exc:            # 变异导致崩溃也算被检出
                detected = True
                results.append((name, "CRASH_DETECTED", type(exc).__name__, why))
                continue
            finally:
                target.unlink(missing_ok=True)
            results.append((name, "DETECTED" if detected else "MISSED", "", why))
    finally:
        for src, (baseline, backup) in sources.items():
            src.write_bytes(backup)
            after = hashlib.sha256(src.read_bytes()).hexdigest()
            if after != baseline:
                print(f"[mut] FATAL 恢复失败 {src.name} {baseline[:12]} -> {after[:12]}")
                return 3

    n_ok = sum(1 for _, s, _, _ in results if s == "DETECTED")
    for name, status, extra, why in results:
        print(f"  {status:18s} {name:32s} {why} {extra}")
    sha_stable = all(hashlib.sha256(p.read_bytes()).hexdigest() == b
                     for p, (b, _) in sources.items())
    print(f"[mut] 真变异 {n_ok}/{len(MUTATIONS)} 检出｜金丝雀前置+基线阴性对照已执行｜"
          f"sha_stable={sha_stable}")
    return 0 if (n_ok == len(MUTATIONS) and sha_stable) else 1


if __name__ == "__main__":
    sys.exit(main())
