"""哨兵新鲜度检查真变异：把 _fresh 改成恒真，过期文件必须不再被检出。

阴性对照：过期结果文件（started_at=2026-11-19）在基线必须判 STALE；
变异（_fresh 恒返回 True）后若判成 SUCCESS/FAILED ⇒ 检出（变异有效）。
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

OLD = '''    if dev > FRESHNESS_WINDOW_MIN:
        return False, f"STARTED_AT_STALE:{dev:.1f}min"
    return True, ""'''
NEW = '''    return True, ""  # MUT: 新鲜度恒真，过期文件不再检出'''


def _load(source: str):
    p = Path(tempfile.mkdtemp(prefix="dq_freshmut_")) / "sim_exec_result.py"
    p.write_text(source, encoding="utf-8", newline="\n")
    spec = importlib.util.spec_from_file_location("sim_exec_result", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["sim_exec_result"] = mod
    spec.loader.exec_module(mod)
    return mod


def _probe(mod) -> bool:
    """过期结果文件是否被判 STALE（基线 True；变异后 False）。"""
    import json
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "r.json"
        rec = {"schema": mod.SCHEMA, "exec_date": "20261201",
               "started_at": "2026-11-19T23:35:13+08:00",
               "finished_at": "2026-11-19T23:35:13+08:00",
               "outcome": "SUCCESS", "rc": 0,
               "gates": {k: "PASS" for k in mod.GATE_KEYS},
               "n_real_orders": 0, "corroboration_sha256": ""}
        p.write_text(json.dumps(rec), encoding="utf-8", newline="\n")
        return mod.read_sentinel(p)["state"] == "STALE"


def main() -> int:
    original = SRC.read_text(encoding="utf-8")
    assert original.count(OLD) == 1, f"锚点应唯一，实际 {original.count(OLD)}"
    mutated = original.replace(OLD, NEW)
    assert mutated != original

    base = _probe(_load(original))
    mut = _probe(_load(mutated))
    detected = base and not mut
    print("BASE_STALE_DETECTED:", base)
    print("M1_FRESHNESS_ALWAYS_TRUE_DETECTED:", detected)
    if not base:
        print("!! VOID：基线未判 STALE，实验无效（阴性对照失败）")
    sha_ok = hashlib.sha256(SRC.read_bytes()).hexdigest() == TARGET_SHA
    print("SHA_STABLE:", sha_ok)
    return 0 if (base and detected and sha_ok) else 1


if __name__ == "__main__":
    sys.exit(main())
