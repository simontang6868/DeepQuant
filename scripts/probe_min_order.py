#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PA-GM-PROBE-3：11-23 最小下单探针（跌停价 1 手 → 撤单 → 去污）。

外部专家第11轮 §一 P0：CONNECT_OK 不能证明 place_limit_order 被接受。
本脚本只在执行机 /opt/quant/.venv-gm + source /opt/quant/env 下真跑；
本地 --dryrun 不导入 gmtrade、不建连、不读授权文件、不发单。

预登记：docs/PROBE_MIN_ORDER_PREREG_2026-11-23.md（sha ccbc420d…）
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend" / "src"))

PREREG_REL = "docs/PROBE_MIN_ORDER_PREREG_2026-11-23.md"
PREREG_SHA = "ccbc420d0761cb919fa24e9873cac424eaf6232db83dbcd84609628a7a653044"
ORDER_DAY = "20261123"
CODE = "601398"
QTY = 100
EXPECTED_ACCT_MASK = "0a47****************************2aa6"
AUTH_ACTION = "sim_rebalance_order"
MAX_NOTIONAL = 100000.0
BUDGET = 4000.0
SIDE_BUY = 1
ORDER_TYPE_LIMIT = 1
POSITION_EFFECT_OPEN = 1
OUT_REL = "research/stock_pick_2026-10-25/forward/probe_min_order_2026-11-23.json"
OUT_DRYRUN_REL = "research/stock_pick_2026-10-25/forward/probe_min_order_dryrun.json"


class Halt(Exception):
    pass


def mask_account(v: Any) -> str:
    s = str(v or "")
    return "*" * len(s) if len(s) <= 8 else s[:4] + "*" * (len(s) - 8) + s[-4:]


def verify_prereg() -> None:
    p = ROOT / PREREG_REL
    if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != PREREG_SHA:
        raise Halt("PREREG_SHA_MISMATCH")


def limit_down_price(prev_close: str) -> str:
    """跌停价 = 前收×0.9，ROUND_HALF_UP 两位；不用浮点、不看行情预测。"""
    d = Decimal(str(prev_close)) * Decimal("0.9")
    q = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if q <= 0:
        raise ValueError("NON_POSITIVE_LIMIT_PRICE")
    return format(q, ".2f")


def _gm_module():
    import gmtrade.api as gm
    return gm


def _positions_snapshot(gm: Any) -> list[str]:
    return sorted(str(getattr(p, "symbol", p)) for p in (gm.get_positions() or []))


def _date_gate(today: str) -> None:
    if today != ORDER_DAY:
        raise Halt(f"DATE_GATE:expected {ORDER_DAY},got {today}")


def run(*, prev_close: str, today: str, dryrun: bool = False,
        _gm: Any = None, auth_path: Path | None = None,
        _auth_doc: dict | None = None) -> dict:
    _date_gate(today)
    price = limit_down_price(prev_close)
    notional = float(price) * QTY
    base = {"mode": "PROBE", "prereg_sha12": PREREG_SHA[:12], "day": ORDER_DAY,
            "code": CODE, "qty": QTY, "side": "BUY", "price": price,
            "notional": notional}

    if notional > BUDGET or notional > MAX_NOTIONAL:
        return {**base, "decision": "NOTIONAL_EXCEEDED", "n_real_orders": 0}

    if dryrun:
        return {**base, "decision": "DRYRUN", "n_real_orders": 0,
                "note": "dryrun 不导入 gmtrade、不建连、不读授权、不发单"}

    gm = _gm if _gm is not None else _gm_module()
    tok = os.environ.get("DQ_GM_TOKEN")
    acc = os.environ.get("DQ_GM_ACCOUNT_ID")
    if not tok or not acc:
        return {**base, "decision": "ENV_MISSING",
                "missing": [k for k, v in (("DQ_GM_TOKEN", tok),
                                           ("DQ_GM_ACCOUNT_ID", acc)) if not v],
                "n_real_orders": 0}

    gm.set_token(tok)
    gm.set_endpoint(os.environ.get("DQ_GM_ENDPOINT", "api.myquant.cn:9000"))
    gm.login(gm.account(account_id=acc, account_alias=""))
    cash = gm.get_cash()
    row = cash[0] if cash else None
    acct_mask = mask_account(getattr(row, "account_id", None))
    rec = {**base, "account_id_mask": acct_mask,
           "account_name_masked": mask_account(getattr(row, "account_name", None))}

    # Q2 账户锚：服务端回显的 account_id 必须与 env 完全一致
    if not row or str(getattr(row, "account_id", "")) != acc:
        return {**rec, "decision": "ACCOUNT_MISMATCH", "n_real_orders": 0}
    if acct_mask != EXPECTED_ACCT_MASK:
        return {**rec, "decision": "ACCOUNT_MISMATCH", "n_real_orders": 0,
                "reason": "ACCOUNT_MASK_NOT_EXPECTED"}

    # Q3 在途单：不撤他人单
    pre_orders = gm.get_orders() or []
    if pre_orders:
        return {**rec, "decision": "PREEXISTING_ORDERS",
                "n_preexisting": len(pre_orders), "n_real_orders": 0}
    pre_pos = _positions_snapshot(gm)
    rec["positions_before"] = pre_pos
    available = float(getattr(row, "available", 0) or 0)
    rec["available_at_check"] = available

    # Q4 授权门：实时 available，禁止写死 100000
    import paper_authorization_guard as guard
    auth = auth_path or (ROOT / "research/stock_pick_2026-10-25/forward/paper_authorization.json")
    doc = _auth_doc if _auth_doc is not None else guard.load_doc(auth)
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")
    v = guard.check(doc=doc, now=now, action=AUTH_ACTION, notional=notional,
                    cumulative=0.0, available=available)
    rec["auth_check"] = v
    if v["decision"] != guard.ALLOW:
        return {**rec, "decision": "NOT_AUTHORIZED", "n_real_orders": 0}

    # Q5 唯一一笔跌停价买单
    try:
        o = gm.order_volume(symbol=CODE, volume=QTY, side=SIDE_BUY,
                            order_type=ORDER_TYPE_LIMIT,
                            position_effect=POSITION_EFFECT_OPEN,
                            price=float(price))
    except Exception as exc:  # noqa: BLE001
        return {**rec, "decision": "ORDER_REJECTED",
                "error_type": type(exc).__name__, "n_real_orders": 0}
    cl = o.get("cl_ord_id") or o.get("order_id") if isinstance(o, dict) else (
        getattr(o, "cl_ord_id", None) or getattr(o, "order_id", None))
    rec["order_id_mask"] = mask_account(cl)
    rec["n_real_orders"] = 1

    # Q6 撤单：优先按 cl_ord_id；取不到 id 且原本无单才用 cancel_all 兜底
    try:
        if cl:
            gm.order_cancel([{"cl_ord_id": cl, "account_id": acc}])
        else:
            gm.order_cancel_all()
            rec["cancel_fallback"] = "ORDER_CANCEL_ALL"
    except Exception as exc:  # noqa: BLE001
        return {**rec, "decision": "CANCEL_FAILED",
                "error_type": type(exc).__name__}

    post_orders = gm.get_orders() or []
    post_pos = _positions_snapshot(gm)
    rec.update({"orders_after_cancel": len(post_orders),
                "positions_after": post_pos})
    if post_orders:
        return {**rec, "decision": "CANCEL_FAILED"}
    if post_pos != pre_pos:
        return {**rec, "decision": "UNEXPECTED_FILL",
                "note": "跌停买单意外成交：升级账户持有人，脚本不自动卖出"}
    return {**rec, "decision": "MIN_ORDER_OK"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PA-GM-PROBE-3 最小下单探针")
    ap.add_argument("--prev-close", default=None, help="前一交易日收盘价（十进制）")
    ap.add_argument("--today", default=ORDER_DAY)
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args(argv)

    try:
        verify_prereg()
    except Halt as e:
        print(json.dumps({"decision": "HALT", "reason": str(e)}, ensure_ascii=False))
        return 3

    if args.self_check:
        print(json.dumps({"decision": "PASS", "price_6.11": limit_down_price("6.11"),
                          "checks": ["date_gate", "limit_down_decimal", "prereg_sha"]},
                         ensure_ascii=False))
        return 0

    if args.prev_close is None:
        print(json.dumps({"decision": "HALT", "reason": "PREV_CLOSE_REQUIRED"},
                         ensure_ascii=False))
        return 3
    try:
        rec = run(prev_close=args.prev_close, today=args.today, dryrun=args.dryrun)
    except (Halt, ValueError) as e:
        rec = {"decision": "HALT", "reason": str(e), "n_real_orders": 0}
    # dryrun 与真实执行分文件落盘：不得让 dryrun 写出看似真实委托的产物
    out = ROOT / (OUT_DRYRUN_REL if args.dryrun else OUT_REL)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str) + "\n",
                   encoding="utf-8", newline="\n")
    print(json.dumps(rec, ensure_ascii=False, default=str))
    return 0 if rec["decision"] in ("DRYRUN", "MIN_ORDER_OK") else 4


if __name__ == "__main__":
    sys.exit(main())
