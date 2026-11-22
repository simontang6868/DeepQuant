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

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "research/stock_pick_2026-10-25/forward/probe_connectivity_2026-11-22.json"
# 第11轮 §五：保留原产物不改写；账户身份字段另存新文件
IDENTITY_OUT = ROOT / "research/stock_pick_2026-10-25/forward/probe_connectivity_identity_2026-11-22.json"
EXPECTED_ACCT_MASK = "0a47****************************2aa6"


def _gm_module():
    import gmtrade.api as gm
    return gm


def _mask(v) -> str:
    s = str(v or "")
    return "*" * len(s) if len(s) <= 8 else s[:4] + "*" * (len(s) - 8) + s[-4:]


def main() -> int:
    try:
        gm = _gm_module()
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
        cash_rows = gm.get_cash()
        # 实测真实 gmtrade 返回 Cash 对象（不是 list）；本地假模块曾返回 list。
        # 两种形态都兼容，否则执行机 TypeError:'Cash' object is not subscriptable。
        c = cash_rows[0] if isinstance(cash_rows, (list, tuple)) else cash_rows
        pos = gm.get_positions() or []
        acct_from_sdk = getattr(c, "account_id", None)
        mask = _mask(acct_from_sdk)
        env_match = str(acct_from_sdk or "") == acc
        mask_ok = mask == EXPECTED_ACCT_MASK
        auth_ref = None
        auth_path = ROOT / "research/stock_pick_2026-10-25/forward/paper_authorization.json"
        if auth_path.is_file():
            try:
                auth_ref = str(__import__("json").loads(auth_path.read_text(encoding="utf-8")).get("account_ref"))
            except Exception:
                auth_ref = None
        rec = {
            "verdict": "CONNECT_OK" if (env_match and mask_ok) else "CONNECT_IDENTITY_MISMATCH",
            "at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
            "nav": float(getattr(c, "nav", 0) or 0),
            "available": float(getattr(c, "available", 0) or 0),
            "account_id_mask": mask,
            "expected_account_id_mask": EXPECTED_ACCT_MASK,
            "account_env_match": env_match,
            "account_mask_ok": mask_ok,
            "account_name_masked": _mask(getattr(c, "account_name", None)),
            "auth_account_ref_label": auth_ref,
            "channel_hint": "SIM" if auth_ref and "虚拟盘" in auth_ref else "UNKNOWN",
            "positions_with_volume": [
                {"symbol": getattr(p, "symbol", ""), "volume": int(getattr(p, "volume", 0) or 0)}
                for p in pos if int(getattr(p, "volume", 0) or 0) > 0
            ],
            "n_real_orders": 0,
            "note": "只读探针：login + get_cash + get_positions，未发任何单；账户只回显前4+后4",
        }
        rc = 0 if rec["verdict"] == "CONNECT_OK" else 4
        IDENTITY_OUT.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8", newline="\n")
    except Exception as e:
        rec = {"verdict": "CONNECT_FAIL", "stage": "SDK",
               "error_type": type(e).__name__, "error": str(e)[:160]}
        rc = 1
    OUT.write_text(json.dumps(rec, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(rec, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
