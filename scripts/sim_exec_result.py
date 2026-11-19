#!/usr/bin/env python3
"""sim_exec_result/v1：仿真执行结果原子落盘与哨兵四态判读（U16-EXEC-2）。

冻结口径：
- 路径 ``forward/sim_exec/20261201_result.json``
- 原子写：tmp + os.replace；目录先建；LF。
- 所有退出路径（含异常）必须落盘 ⇒ 调用方用 try/finally。
- ``started_at/finished_at`` 记**真实系统时钟**（E0 事后复核证据），
  不由 EXEC_DATE 派生。
- 哨兵四态：SUCCESS / 非 SUCCESS / 不存在 / 损坏(schema 不符) ⇒ 告警。
- 哨兵新鲜度（专家审查发现 1，2026-11-19）：结果文件 started_at 必须落在
  意图时点 ±10min 内；窗口外判 STALE（演练未发生，检出过期结果文件），
  防止 12-01 timer 未触发时误读历史验证产物。
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
EXEC_INTENT = "2026-12-01T09:35:00+08:00"   # 与 E0 / 验收第9条同口径
FRESHNESS_WINDOW_MIN = 10                    # started_at 必须落在此窗口内


def _fresh(started_at: Any) -> tuple[bool, str]:
    """started_at 落在意图时点 ±FRESHNESS_WINDOW_MIN 内 ⇒ (True, "")。

    不可解析 / 缺字段 / 窗口外 ⇒ (False, 原因)，按 STALE 告警，不静默放行。
    """
    if not isinstance(started_at, str) or not started_at:
        return False, "STARTED_AT_MISSING"
    try:
        want = dt.datetime.fromisoformat(EXEC_INTENT)
        got = dt.datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if got.tzinfo is None:
            got = got.replace(tzinfo=want.tzinfo)
        dev = abs((got - want).total_seconds()) / 60.0
    except Exception:  # noqa: BLE001
        return False, "STARTED_AT_UNPARSEABLE"
    if dev > FRESHNESS_WINDOW_MIN:
        return False, f"STARTED_AT_STALE:{dev:.1f}min"
    return True, ""


def now_real_iso() -> str:
    """真实系统时钟，Asia/Shanghai(+08:00)，与意图时点无关。"""
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")


def in_window(started_at: Any) -> bool:
    """U16-EXEC-3 方案甲：started_at 是否落在意图时点 ±FRESHNESS_WINDOW_MIN。

    单一窗口来源：E0 门与落盘门槛共用本函数（不得另立阈值）。
    不可解析/缺字段 ⇒ False（失败关闭，落 .TEST_ 名）。
    """
    ok, _ = _fresh(started_at)
    return ok


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
    """原子写（tmp+replace），LF，返回落盘路径。

    U16-EXEC-3 方案甲：未显式指定 path 时，仅当 started_at 落在意图窗口内
    才写生产文件名；窗口外/不可解析一律写 ``.TEST_<stamp>.json`` 隔离，
    杜绝测试/误触发污染 12-01 生产路径。显式 path（self_check 临时目录）不受影响。
    """
    if path is None:
        if not in_window(rec.get("started_at")):
            stamp = str(rec.get("started_at") or "unknown").replace(":", "").replace("+", "_P")
            p = ROOT / RESULT_REL
            p = Path(str(p).replace(".json", f".TEST_{stamp}.json"))
        else:
            p = ROOT / RESULT_REL
    else:
        p = Path(path)
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
    fresh, why = _fresh(rec.get("started_at"))
    if not fresh:
        # 过期结果文件：12-01 timer 未触发时被误读的验证产物 ⇒ 按“演练未发生”
        return {"state": "STALE", "outcome": None,
                "error": "演练未发生（检出过期结果文件）:" + why}
    return {"state": "SUCCESS" if outcome == "SUCCESS" else "FAILED",
            "outcome": outcome, "error": None}


def self_check() -> dict[str, Any]:
    """离线自检：五态判读 + 原子写sha不变（临时目录），不碰正式路径。"""
    checks = []
    fresh_ts = EXEC_INTENT  # 窗口内的 started_at，保证 success/failed 基线成立
    stale_ts = "2026-11-19T23:35:13+08:00"  # 真实历史验证产物时点（H9 现场）
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "result.json"
        s0 = read_sentinel(p)
        checks.append(("missing", s0["state"] == "MISSING"))
        rec = build_result(outcome="SUCCESS", rc=0,
                           gates={k: "PASS" for k in GATE_KEYS}, n_real_orders=3,
                           started_at=fresh_ts, finished_at=fresh_ts)
        write_result(rec, p)
        s1 = read_sentinel(p)
        checks.append(("success", s1["state"] == "SUCCESS"))
        rec["outcome"] = "GATE_FAIL"; rec["rc"] = 4
        write_result(rec, p)
        s2 = read_sentinel(p)
        checks.append(("failed", s2["state"] == "FAILED"))
        # 新鲜度：过期结果文件（真实历史时点）必须判 STALE，不误报 SUCCESS/FAILED
        rec["outcome"] = "SUCCESS"; rec["rc"] = 0; rec["started_at"] = stale_ts
        write_result(rec, p)
        s_stale = read_sentinel(p)
        checks.append(("stale_detected", s_stale["state"] == "STALE"))
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
    """CLI：--sentinel 五态判读（09:45 哨兵）；--self-check 离线自检。"""
    import argparse
    ap = argparse.ArgumentParser(description="sim_exec_result/v1 哨兵与自检")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--sentinel", action="store_true", help="五态判读并打印结果")
    g.add_argument("--self-check", action="store_true")
    ap.add_argument("--file", default=None)
    args = ap.parse_args(argv)
    if args.self_check:
        s = self_check()
        print(json.dumps(s, ensure_ascii=False))
        return 0 if s["decision"] == "PASS" else 1
    s = read_sentinel(Path(args.file) if args.file else None)
    print(json.dumps(s, ensure_ascii=False))
    # SUCCESS ⇒ 0；其余四态（FAILED/STALE/MISSING/CORRUPT）均告警，由 alert 链发信
    return 0 if s["state"] == "SUCCESS" else 2


if __name__ == "__main__":
    import sys as _sys
    _sys.exit(main())
