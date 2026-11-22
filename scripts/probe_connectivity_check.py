#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""gm-paper 只读连通性探针（2026-11-22，外部专家第10轮§一）。

只做 login + get_cash + get_positions，**不发任何单**，不回显凭证值。
必须在执行机、用专用 venv /opt/quant/.venv-gm、source /opt/quant/env 后运行。
产物：research/stock_pick_2026-10-25/forward/probe_connectivity_2026-11-22.json
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "research/stock_pick_2026-10-25/forward/probe_connectivity_2026-11-22.json"


def main() -> int:
    try:
        import gmtrade.api as gm
    except Exception as e:
        rec = {"verdict": "CONNECT_FAIL", "stage": "IMPORT", "error_type": type(e).__name__,
               "hint": "必须用 /opt/quant/.venv-gm/bin/python（不是 .venv）"}
        OUT.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(rec, ensure_ascii=False))
        return 1

    tok = os.environ.get("DQ_GM_TOKEN")
    acc = os.environ.get("DQ_GM_ACCOUNT_ID")
    if not tok or not acc:
        rec = {"verdict": "CONNECT_FAIL", "stage": "ENV", "missing":
               [k for k, v in (("DQ_GM_TOKEN", tok), ("DQ_GM_ACCOUNT_ID", acc)) if not v],
               "hint": "source /opt/quant/env（systemd EnvironmentFile）"}
        OUT.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(json.dumps(rec, ensure_ascii=False))
        return 1

    try:
        gm.set_token(tok)
        gm.set_endpoint(os.environ.get("DQ_GM_ENDPOINT", "api.myquant.cn:9000"))
        gm.login(gm.account(account_id=acc, account_alias=""))
        c = gm.get_cash()
        pos = gm.get_positions() or []
        rec = {
            "verdict": "CONNECT_OK",
            "at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
            "nav": float(getattr(c, "nav", 0) or 0),
            "available": float(getattr(c, "available", 0) or 0),
            "positions_with_volume": [
                {"symbol": getattr(p, "symbol", ""), "volume": int(getattr(p, "volume", 0) or 0)}
                for p in pos if int(getattr(p, "volume", 0) or 0) > 0
            ],
            "n_real_orders": 0,
            "note": "只读探针：login + get_cash + get_positions，未发任何单；不回显凭证值",
        }
        rc = 0
    except Exception as e:
        rec = {"verdict": "CONNECT_FAIL", "stage": "SDK",
               "error_type": type(e).__name__, "error": str(e)[:160]}
        rc = 1
    OUT.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(rec, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
