#!/usr/bin/env python3
"""ECR-P0-SNAPSHOT-1 / R7-5：11-27 月末 DRYRUN 必须覆盖本修复的自检项。

由 ``daily_chain.sh --dry-run`` 调用；只读 + 临时目录，不碰真实 forward 产物。
四项：跨日推进、REV_CONFLICT、阶段 rc 闸门、迁移幂等。

退出码 0 = 四项全过；1 = 任一失败（DRYRUN 应判失败）。
"""

from __future__ import annotations

import json
import pathlib
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import paper_account_adapter as paa  # noqa: E402
import shadow_daily_reconcile as sdr  # noqa: E402
import stage_rc_gate as srg  # noqa: E402


def rec(day: str, available: float = 100000.0) -> dict:
    return {"asof": day, "source": "gm-paper",
            "cash": {"available": available, "frozen": 0.0, "total": available},
            "positions": []}


def main() -> int:
    checks: list[dict] = []
    with tempfile.TemporaryDirectory() as td:
        d = pathlib.Path(td)

        # 1 跨日推进必须写成功
        r1, p1, _ = paa.write_daily_snapshot(rec("20261109"), directory=d)
        r2, p2, _ = paa.write_daily_snapshot(rec("20261110"), directory=d)
        checks.append({"name": "cross_day_advance",
                       "ok": r1 == 0 and r2 == 0 and p1 != p2
                       and p1.name.endswith("20261109_rev1.json")
                       and p2.name.endswith("20261110_rev1.json")})

        # 2 同日多 rev 账户内容不一致 ⇒ REV_CONFLICT
        d2 = d / "revcase"; d2.mkdir()
        (d2 / "account_snapshot_20261120_rev1.json").write_text(
            json.dumps(rec("20261120", 100000.0)) + "\n", encoding="utf-8")
        (d2 / "account_snapshot_20261120_rev2.json").write_text(
            json.dumps(rec("20261120", 90000.0)) + "\n", encoding="utf-8")
        why, payload, _ = sdr.load_daily_snapshot("20261120", d2)
        checks.append({"name": "rev_conflict",
                       "ok": why == sdr.REV_CONFLICT and payload is None})

        # 3 关键阶段 rc≠0 ⇒ ok=false 且告警
        r = srg.evaluate("update=0:关键;paper_snapshot=4:关键;backup=0:关键")
        allgreen = srg.evaluate("update=0:关键;paper_snapshot=0:关键;backup=0:关键")
        checks.append({"name": "stage_rc_gate",
                       "ok": r["ok"] is False and r["alert"] is True
                       and allgreen["ok"] is True})

        # 4 迁移幂等 + 原文件冻结
        d3 = d / "migrate"; d3.mkdir()
        legacy = d3 / "account_snapshot.json"
        legacy.write_bytes((json.dumps(rec("20261109"), indent=1,
                                       sort_keys=True) + "\n").encode("utf-8"))
        before = legacy.read_bytes()
        m1 = paa.migrate_legacy_snapshot(directory=d3, legacy=legacy)
        target = d3 / "account_snapshot_20261109_rev1.json"
        t_before = target.read_bytes()
        m2 = paa.migrate_legacy_snapshot(directory=d3, legacy=legacy)
        checks.append({"name": "migration_idempotent",
                       "ok": m1["status"] == "migrated"
                       and m2["status"] == "already_migrated"
                       and legacy.read_bytes() == before
                       and target.read_bytes() == t_before})

    ok = all(c["ok"] for c in checks)
    print("[r7_dryrun] " + json.dumps({"ok": ok, "checks": checks},
                                      ensure_ascii=False))
    for c in checks:
        print(f"  [{'PASS' if c['ok'] else 'FAIL'}] {c['name']}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
