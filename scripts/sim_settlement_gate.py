#!/usr/bin/env python3
"""T3：结算完成性判别（U16-EXEC-2，18:10+ 对账前置）。

判据（全部满足才算结算完成）：
1. 外部快照的 ``settlement_verified`` 明确为 settled/true（unknown ⇒ 未完成）
2. ``unfinished_orders`` 为空（无未终态委托）
3. 18:00 之后（系统真实时钟）

未完成 ⇒ 退避重试（默认每 5 分钟，最多 6 次 = 18:40）；超时 ⇒ rc=2 告警停机。
**不得静默降级**用结算前快照当正式对账。
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

SETTLED_VALUES = {"settled", "true", True, 1, "yes"}
SETTLE_HOUR = 18
MAX_RETRY = 6
RETRY_SEC = 300


def is_settled(snap: dict) -> tuple[bool, str]:
    v = snap.get("settlement_verified")
    if v not in SETTLED_VALUES:
        return False, f"settlement_verified={v!r}"
    unfin = snap.get("unfinished_orders")
    if unfin is None:
        return False, "unfinished_orders:MISSING"
    if isinstance(unfin, list) and unfin:
        return False, f"unfinished_orders:n={len(unfin)}"
    if not isinstance(unfin, (list, dict)) and unfin not in (0, None, ""):
        return False, f"unfinished_orders:type={type(unfin).__name__}"
    return True, ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="结算完成性判别 + 退避重试")
    ap.add_argument("--file", default=None, help="外部快照 JSON；不给则找最新 rev1")
    ap.add_argument("--snapshot-dir", default=None)
    ap.add_argument("--max-retry", type=int, default=MAX_RETRY)
    ap.add_argument("--retry-sec", type=int, default=RETRY_SEC)
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args(argv)

    if args.self_check:
        ok, why = is_settled({"settlement_verified": "settled",
                              "unfinished_orders": []})
        assert ok, why
        bad = [({"settlement_verified": "unknown", "unfinished_orders": []}, "unknown"),
               ({"settlement_verified": "settled", "unfinished_orders": [{"id": 1}]}, "unfinished"),
               ({"settlement_verified": "settled"}, "missing")]
        for s, tag in bad:
            ok2, _ = is_settled(s)
            assert not ok2, tag
        print(json.dumps({"decision": "PASS", "checks": 4}, ensure_ascii=False))
        return 0

    fwd = Path(args.snapshot_dir) if args.snapshot_dir else Path(
        "research/stock_pick_2026-10-25/forward")
    p = Path(args.file) if args.file else (
        sorted(fwd.glob("account_snapshot_*_rev1.json"))[-1]
        if list(fwd.glob("account_snapshot_*_rev1.json")) else None)
    for attempt in range(args.max_retry + 1):
        now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
        if p and p.is_file() and now.hour >= SETTLE_HOUR:
            try:
                snap = json.loads(p.read_text(encoding="utf-8"))
                ok, why = is_settled(snap)
                if ok:
                    print(json.dumps({"decision": "SETTLED", "file": str(p),
                                      "checked_at": now.isoformat(timespec="seconds")},
                                     ensure_ascii=False))
                    return 0
            except Exception as exc:  # noqa: BLE001
                why = f"READ_ERROR:{type(exc).__name__}"
        else:
            why = "BEFORE_1800" if now.hour < SETTLE_HOUR else "SNAPSHOT_MISSING"
        print(f"[settle] attempt={attempt+1}/{args.max_retry+1} NOT_SETTLED:{why}",
              file=sys.stderr)
        if attempt < args.max_retry:
            time.sleep(args.retry_sec)
    print(json.dumps({"decision": "TIMEOUT", "reason": why}, ensure_ascii=False))
    return 2   # 告警停机，不静默降级


if __name__ == "__main__":
    sys.exit(main())
