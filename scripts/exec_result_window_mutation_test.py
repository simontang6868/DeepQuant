"""U16-EXEC-3 方案甲双向变异：落盘窗口门槛必须真改行为。

M10A：把窗口外分流删掉（总是写生产名）⇒ 窗口外测试必须检出。
M10B：把窗口内也改成写 .TEST_ 名 ⇒ 窗口内测试必须检出。
探针直接调 importlib 加载的变异模块（不经 pytest 收集，避假变异）。
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "scripts" / "sim_exec_result.py"
TARGET_SHA = hashlib.sha256(SRC.read_bytes()).hexdigest()

OLD_SPLIT = '''    if path is None:
        if not in_window(rec.get("started_at")):
            stamp = str(rec.get("started_at") or "unknown").replace(":", "").replace("+", "_P")
            p = ROOT / RESULT_REL
            p = Path(str(p).replace(".json", f".TEST_{stamp}.json"))
        else:
            p = ROOT / RESULT_REL
    else:
        p = Path(path)'''
NEW_ALWAYS_PROD = '''    p = Path(path) if path else ROOT / RESULT_REL  # MUT: 窗口外也写生产名'''
NEW_ALWAYS_TEST = '''    p = Path(path) if path else Path(str(ROOT / RESULT_REL).replace(".json", ".TEST_MUT.json"))  # MUT: 窗口内也写TEST名'''


def _load(source: str):
    p = Path(tempfile.mkdtemp(prefix="dq_winmut_")) / "sim_exec_result.py"
    p.write_text(source, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location("sim_exec_result", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sim_exec_result"] = mod
    spec.loader.exec_module(mod)
    return mod


def _probe(mod, started_at: str) -> bool:
    """返回落盘文件名是否含 .TEST_。"""
    import json
    with tempfile.TemporaryDirectory() as td:
        mod.ROOT = Path(td)
        rec = {"schema": mod.SCHEMA, "exec_date": "20261201",
               "started_at": started_at, "finished_at": started_at,
               "outcome": "GATE_FAIL", "rc": 4,
               "gates": {k: "FAIL" for k in mod.GATE_KEYS},
               "n_real_orders": 0, "corroboration_sha256": ""}
        p = mod.write_result(rec)
        return ".TEST_" in p.name


def main() -> int:
    original = SRC.read_text(encoding="utf-8")
    assert original.count(OLD_SPLIT) == 1, f"锚点应唯一，实际 {original.count(OLD_SPLIT)}"
    outside = "2026-11-20T00:00:16+08:00"
    inside = "2026-12-01T09:35:00+08:00"

    base = _load(original)
    b_out = _probe(base, outside)   # 期望 True（窗口外→TEST）
    b_in = _probe(base, inside)     # 期望 False（窗口内→生产）
    print("BASE_OUTSIDE_GOES_TEST:", b_out)
    print("BASE_INSIDE_GOES_PROD:", not b_in)

    mut_out_mod = _load(original.replace(OLD_SPLIT, NEW_ALWAYS_PROD))
    m10a = not _probe(mut_out_mod, outside)  # 变异后窗口外写生产名 ⇒ 检出
    print("M10A_OUTSIDE_TO_PROD_DETECTED:", m10a)

    mut_in_mod = _load(original.replace(OLD_SPLIT, NEW_ALWAYS_TEST))
    m10b = _probe(mut_in_mod, inside)  # 变异后窗口内写TEST名 ⇒ 检出
    print("M10B_INSIDE_TO_TEST_DETECTED:", m10b)

    sha_ok = hashlib.sha256(SRC.read_bytes()).hexdigest() == TARGET_SHA
    print("SHA_STABLE:", sha_ok)
    return 0 if (b_out and not b_in and m10a and m10b and sha_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
