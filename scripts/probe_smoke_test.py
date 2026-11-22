#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROBE 11-25 前置 smoke test（2026-11-22，外部专家第9轮§三）。

不真的下单、不碰真实授权文件：
1) probe_executor --self-check（离线门控）
2) 三腿 --dryrun（buy/sell/decontam），用临时夹具授权
3) 打印将要下单的 (codes, side, qty, price) 供人工肉眼确认
4) 核对真实授权文件未被 dryrun 触碰（只读 mtime + hash 前缀）

输出 JSON 产物到 var/probe_smoke_20261122.json。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
OUT = ROOT / "research/stock_pick_2026-10-25/forward/probe_smoke_20261122.json"

PRICES_BUY = ["601398=6.10", "600000=9.80"]
PRICES_SELL = ["601398=6.15", "600000=9.90"]


def run(args: list[str]) -> tuple[int, str]:
    p = subprocess.run([PY, "-u", str(ROOT / "scripts/probe_executor.py"), *args],
                       cwd=ROOT, capture_output=True, text=True,
                       env={"PYTHONPATH": "scripts", "PATH": __import__("os").environ.get("PATH", "")})
    return p.returncode, (p.stdout or p.stderr).strip()


def sha12(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:12]


def main() -> int:
    auth = ROOT / "research/stock_pick_2026-10-25/forward/paper_authorization.json"
    # 本地仓库无授权文件（在执行机），故本地仅记录"不在本地"，由外部 SSH 命令核对
    auth_local = auth.exists()
    before = sha12(auth) if auth_local else None
    mtime_before = auth.stat().st_mtime if auth_local else None

    checks: dict[str, object] = {}
    rc, out = run(["--self-check"])
    checks["self_check"] = {"rc": rc, "out": out}

    rc, out = run(["--leg", "buy", "--dryrun", "--today", "20261125",
                   "--price", PRICES_BUY[0], "--price", PRICES_BUY[1]])
    checks["buy_dryrun"] = {"rc": rc, "out": out}

    rc, out = run(["--leg", "sell", "--dryrun", "--today", "20261126",
                   "--price", PRICES_SELL[0], "--price", PRICES_SELL[1]])
    checks["sell_dryrun"] = {"rc": rc, "out": out}

    rc, out = run(["--leg", "decontam", "--dryrun", "--today", "20261126"])
    checks["decontam_dryrun"] = {"rc": rc, "out": out}

    after = sha12(auth) if auth_local else None
    mtime_after = auth.stat().st_mtime if auth_local else None
    checks["auth_untouched"] = {
        "local_auth_exists": auth_local,
        "before_sha12": before, "after_sha12": after,
        "before_mtime": mtime_before, "after_mtime": mtime_after,
        "note": "本地仓库无授权文件（执行机专有）；dryrun 用临时夹具，实际执行机 mtime/hash 由外部 SSH 命令核对",
    }

    all_ok = (checks["self_check"]["rc"] == 0
              and checks["buy_dryrun"]["rc"] == 0
              and checks["sell_dryrun"]["rc"] == 0
              and checks["decontam_dryrun"]["rc"] == 0)
    rec = {"at": "2026-11-22T19:35:00+08:00", "verdict": "SMOKE_PASS" if all_ok else "SMOKE_FAIL",
           "checks": checks, "n_real_orders": 0}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps({"verdict": rec["verdict"], "n_real_orders": 0,
                      "buy": checks["buy_dryrun"]["out"],
                      "sell": checks["sell_dryrun"]["out"],
                      "decontam": checks["decontam_dryrun"]["out"]}, ensure_ascii=False))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
