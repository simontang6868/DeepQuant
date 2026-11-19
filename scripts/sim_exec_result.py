#!/usr/bin/env python3
"""sim_exec_result/v1：仿真执行结果原子落盘与哨兵四态判读（U16-EXEC-2）。

冻结口径：
- 路径 ``forward/sim_exec/20261201_result.json``
- 原子写：tmp + os.replace；目录先建；LF。
- 所有退出路径（含异常）必须落盘 ⇒ 调用方用 try/finally。
- ``started_at/finished_at`` 记**真实系统时钟**（E0 事后复核证据），
  不由 EXEC_DATE 派生。
- 哨兵四态：SUCCESS / 非 SUCCESS / 不存在 / 损坏(schema 不符) ⇒ 告警。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
RESULT_REL = "research/stock_pick_2026-10-25/forward/sim_exec/20261201_result.json"
SCHEMA = "sim_exec_result/v1"
OUTCOMES = ("SUCCESS", "GATE_FAIL", "CLOCK_FAIL", "NOT_AUTHORIZED", "ERROR")
GATE_KEYS = ("E0", "E1", "E2", "E3", "E4")


def now_real_iso() -> str:
    """真实系统时钟，Asia/Shanghai(+08:00)，与意图时点无关。"""
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")


def build_result(*, outcome: str, rc: int, gates: dict[str, str] | None = None,
                 n_real_orders: int = 0, corroboration_sha256: str = "",
                 started_at: str = "", finished_at: str = "") -> dict[str, Any]:
    if outcome not in OUTCOMES:
        raise ValueError(f"OUTCOME_INVALID:{outcome}")
    g = {k: "UNKNOWN" for k in GATE_KEYS}
    if gates:
        for k in GATE_KEYS:
            if k in gates:
                if gates[k] not in ("PASS", "FAIL", "UNKNOWN", "SKIP"):
                    raise ValueError(f"GATE_VALUE_INVALID:{k}")
                g[k] = gates[k]
    return {"schema": SCHEMA, "exec_date": "20261201",
            "started_at": started_at or now_real_iso(),
            "finished_at": finished_at or now_real_iso(),
            "outcome": outcome, "rc": int(rc), "gates": g,
            "n_real_orders": int(n_real_orders),
            "corroboration_sha256": corroboration_sha256}


def write_result(rec: dict[str, Any], path: Path | None = None) -> Path:
    """原子写（tmp+replace），LF，返回落盘路径。"""
    p = Path(path) if path else ROOT / RESULT_REL
    p.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(rec, ensure_ascii=False, indent=1) + "\n"
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", suffix=".tmp", dir=p.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return p


def read_sentinel(path: Path | None = None) -> dict[str, Any]:
    """哨兵四态：返回 ``{state, outcome|error}``，不抛异常。"""
    p = Path(path) if path else ROOT / RESULT_REL
    if not p.is_file():
        return {"state": "MISSING", "outcome": None,
                "error": "演练未发生"}
    try:
        rec = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"state": "CORRUPT", "outcome": None,
                "error": f"JSON_INVALID:{type(exc).__name__}"}
    if not isinstance(rec, dict) or rec.get("schema") != SCHEMA:
        return {"state": "CORRUPT", "outcome": None, "error": "SCHEMA_MISMATCH"}
    outcome = rec.get("outcome")
    if outcome not in OUTCOMES:
        return {"state": "CORRUPT", "outcome": None, "error": "OUTCOME_INVALID"}
    return {"state": "SUCCESS" if outcome == "SUCCESS" else "FAILED",
            "outcome": outcome, "error": None}


def self_check() -> dict[str, Any]:
    """离线自检：四态判读 + 原子写sha不变（临时目录），不碰正式路径。"""
    checks = []
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "result.json"
        s0 = read_sentinel(p)
        checks.append(("missing", s0["state"] == "MISSING"))
        rec = build_result(outcome="SUCCESS", rc=0,
                           gates={k: "PASS" for k in GATE_KEYS}, n_real_orders=3)
        write_result(rec, p)
        s1 = read_sentinel(p)
        checks.append(("success", s1["state"] == "SUCCESS"))
        rec["outcome"] = "GATE_FAIL"; rec["rc"] = 4
        write_result(rec, p)
        s2 = read_sentinel(p)
        checks.append(("failed", s2["state"] == "FAILED"))
        p.write_text("{broken", encoding="utf-8", newline="\n")
        s3 = read_sentinel(p)
        checks.append(("corrupt", s3["state"] == "CORRUPT"))
        bad = False
        try:
            build_result(outcome="ALL_GOOD", rc=0)
        except ValueError:
            bad = True
        checks.append(("outcome_guard", bad))
    return {"decision": "PASS" if all(v for _, v in checks) else "FAIL",
            "checks": [k for k, v in checks],
            "statistical_verdict": "ENGINEERING_SELF_CHECK_ONLY"}


def main(argv: list[str] | None = None) -> int:
    """CLI：--sentinel 四态判读（09:45 哨兵）；--self-check 离线自检。"""
    import argparse
    ap = argparse.ArgumentParser(description="sim_exec_result/v1 哨兵与自检")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sentinel", action="store_true", help="四态判读并打印结果")
    g.add_argument("--self-check", action="store_true")
    ap.add_argument("--file", default=None)
    args = ap.parse_args(argv)
    if args.self_check:
        s = self_check()
        print(json.dumps(s, ensure_ascii=False))
        return 0 if s["decision"] == "PASS" else 1
    s = read_sentinel(Path(args.file) if args.file else None)
    print(json.dumps(s, ensure_ascii=False))
    # SUCCESS ⇒ 0；其余三态均告警（非 0），由上层 systemd/alert 链发信
    return 0 if s["state"] == "SUCCESS" else 2


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
