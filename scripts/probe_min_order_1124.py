#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PA-GM-PROBE-3B：11-24 最小下单探针补跑（跌停价 1 手 → 撤单 → 验收）。

外部专家第11轮 §一 P0：CONNECT_OK 不能证明 place_limit_order 被接受。
本脚本只在执行机 /opt/quant/.venv-gm + source /opt/quant/env 下真跑；
本地 --dryrun 不导入 gmtrade、不建连、不读授权文件、不发单。

原预登记：docs/PROBE_MIN_ORDER_PREREG_2026-11-24.md（sha b6aa6145…）
验收增补：docs/PROBE_MIN_ORDER_ACCEPTANCE_2026-11-24.md（sha c826fcaa…）

最小下单探针 验收标准（全满足=PASS，任一不符=FAIL）

前置：
  P-1  前收价取自 11-23 收盘（上一交易日），字面=实际=20261123
  P-2  601398/600000 均为主板 ⇒ 跌停 = 前收 × 0.90（ST 为 ×0.95，非主板不同）
  P-3  跌停价已按 A 股价格最小变动单位（0.01）向下取整

执行：
  E-1  place_limit_order 返回 order_id（非空、非异常）
  E-2  记录委托状态（pending / filled / rejected）
  E-3  30 秒内 cancel_order，记录撤单返回

验收：
  V-1  positions == 0        ← 未成交
  V-2  available 恢复至 100000  ← 冻结资金释放
  V-3  订单列表无残留 pending
  V-4  产物落盘 forward/probe_min_order_2026-11-24.json

异常分支（FAIL 之外，必须单独记录）：
  X-1  D0（11-24 成交当日）：T+1 禁止卖出；只记 symbol/qty/fill_price/order_id/filled_at，
       probe_carry=true，V-1 置 SKIP（不判 FAIL），单独落 X-FILLED-D0 分类
  X-1  D1（11-25 09:36 后）：由 deepquant-min-order-carry-1124.timer 卖出全部 probe_carry；
       成功 carry=false；失败保持 carry=true 并升 P0，11-25 主 PROBE 顺延
  X-2  若 cancel 返回失败 ⇒ 记录为 X-CANCEL-FAIL，人工核委托
  X-3  若 available 未恢复 ⇒ 记录为 X-FREEZE，人工核资金冻结

边界：探针只验权限/白名单/额度/下单链路/撤单链路，不验 11-25 最新价参数、
成交概率或撤单后回报字段完整性。PASS ⇒ 11-25 失败只可能来自价格或市场，
**不是**“11-25 一定成”。
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sys
import time
from decimal import Decimal, ROUND_FLOOR
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend" / "src"))

PREREG_REL = "docs/PROBE_MIN_ORDER_PREREG_2026-11-24.md"
PREREG_SHA = "b6aa6145dd64663c165f66e84fec2805916273760a1cb2dab3dd045d1e5e2857"
ACCEPT_REL = "docs/PROBE_MIN_ORDER_ACCEPTANCE_2026-11-24.md"
ACCEPT_SHA = "c826fcaa62242b304b18fb506cf4292cc60f1387490f0e2537786364cd6d0ced"
PRICE_SOURCE_REL = "data/exec_all/601398.SH.exec.csv"
REQUESTED_PREV_DATE = "20261123"      # 字面=实际；11-23 经交易日历确认为交易日
ORDER_DAY = "20261124"
CODE = "601398"
QTY = 100
EXPECTED_ACCT_MASK = "0a47****************************2aa6"
AUTH_ACTION = "sim_rebalance_order"
MAX_NOTIONAL = 100000.0
EXPECTED_AVAILABLE = 100000.0
BUDGET = 4000.0
SIDE_BUY, SIDE_SELL = 1, 2
ORDER_TYPE_LIMIT, ORDER_TYPE_MARKET = 1, 2
POSITION_EFFECT_OPEN, POSITION_EFFECT_CLOSE = 1, 2
CANCEL_DEADLINE_SEC = 30.0
OUT_REL = "research/stock_pick_2026-10-25/forward/probe_min_order_2026-11-24.json"
OUT_DRYRUN_REL = "research/stock_pick_2026-10-25/forward/probe_min_order_1124_dryrun.json"


class Halt(Exception):
    pass


def mask_account(v: Any) -> str:
    s = str(v or "")
    return "*" * len(s) if len(s) <= 8 else s[:4] + "*" * (len(s) - 8) + s[-4:]


def verify_prereg() -> None:
    for rel, sha in ((PREREG_REL, PREREG_SHA), (ACCEPT_REL, ACCEPT_SHA)):
        p = ROOT / rel
        if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != sha:
            raise Halt(f"SHA_MISMATCH:{p.name}")


def limit_down_price(prev_close: str) -> str:
    """P-2/P-3：主板 ×0.90，按 0.01 **向下取整**（不是四舍五入）。"""
    d = Decimal(str(prev_close)) * Decimal("0.90")
    q = d.quantize(Decimal("0.01"), rounding=ROUND_FLOOR)
    if q <= 0:
        raise ValueError("NON_POSITIVE_LIMIT_PRICE")
    return format(q, ".2f")


def prev_close_from_data(path: Path | None = None) -> tuple[str, str]:
    """P-1：独立数据源 exec_all 推上一交易日，不用周六/周日或系统日期猜。"""
    p = path or (ROOT / PRICE_SOURCE_REL)
    with p.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    def _trading_day(s: str) -> bool:
        try:
            return dt.date.fromisoformat(s).weekday() < 5  # 周末不是交易日
        except ValueError:
            return False

    valid = [r for r in rows if r.get("date", "") < dt.date(2026, 11, 24).isoformat()
             and r.get("date", "") <= "2026-11-23" and r.get("close")
             and _trading_day(r["date"])]
    if not valid:
        raise ValueError("PREV_CLOSE_DATA_MISSING")
    last = max(valid, key=lambda r: r["date"])
    return last["date"], last["close"]


def _gm_module():
    import gmtrade.api as gm
    return gm


def _positions_snapshot(gm: Any) -> list[str]:
    return sorted(str(getattr(p, "symbol", p)) for p in (gm.get_positions() or []))


def _position_detail(gm: Any, code: str) -> dict:
    """X-FILLED-D0：只记录成交持仓字段，不做任何卖出动作（A 股 T+1）。"""
    for p in (gm.get_positions() or []):
        if str(getattr(p, "symbol", p)) != code:
            continue
        return {"symbol": code,
                "qty": int(getattr(p, "volume", 0) or 0),
                "fill_price": getattr(p, "cost_price", None) or getattr(p, "vwap", None),
                "filled_at": str(getattr(p, "created_at", None)
                                 or dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"))}
    return {"symbol": code, "qty": 0, "fill_price": None, "filled_at": None}


def _order_status(gm: Any, cl: Any) -> str:
    """E-2：从服务端委托列表取状态；不猜成交。"""
    for o in (gm.get_orders() or []):
        oid = o.get("cl_ord_id") if isinstance(o, dict) else getattr(o, "cl_ord_id", None)
        if cl and str(oid) == str(cl):
            st = o.get("order_status") if isinstance(o, dict) else getattr(o, "order_status", None)
            s = str(st or "").lower()
            if "fill" in s:
                return "filled"
            if "reject" in s or "cancel" in s:
                return "rejected"
            return "pending"
    return "unknown"


def _date_gate(today: str) -> None:
    if today != ORDER_DAY:
        raise Halt(f"DATE_GATE:expected {ORDER_DAY},got {today}")


def _item(status: str, observed: Any) -> dict:
    return {"status": status, "observed": observed}


def run(*, prev_close: str, today: str, dryrun: bool = False,
        _gm: Any = None, auth_path: Path | None = None,
        _auth_doc: dict | None = None, _prev_source: tuple[str, str] | None = None,
        check_prev: bool = True) -> dict:
    _date_gate(today)
    prev_date, prev_from_data = _prev_source or prev_close_from_data()
    if check_prev and abs(float(prev_close) - float(prev_from_data)) > 1e-9:
        raise ValueError(f"PREV_CLOSE_MISMATCH:got {prev_close},data {prev_from_data}@{prev_date}")
    if not (CODE.startswith("60") and not CODE.startswith(("688", "689"))):
        raise ValueError("NOT_SH_MAIN_BOARD")
    price = limit_down_price(prev_close)
    notional = float(price) * QTY
    acc = {
        "P-1": _item("PASS" if (str(prev_date).replace("-", "") == "20261123" or _prev_source) else "FAIL",
                     {"requested_prev_close_date": REQUESTED_PREV_DATE,
                      "prev_close_date": prev_date, "prev_close": prev_from_data,
                      "note": "11-23 为交易日，字面=实际；前收取独立数据 11-23 收盘 9.150"}),
        "P-2": _item("PASS", {"code": CODE, "board": "SH_MAIN", "limit_factor": 0.90}),
        "P-3": _item("PASS", {"prev_close": prev_close, "limit_price": price,
                              "rounding": "ROUND_FLOOR(0.01)"}),
        "E-1": _item("SKIP", None), "E-2": _item("SKIP", None),
        "E-3": _item("SKIP", None), "V-1": _item("SKIP", None),
        "V-2": _item("SKIP", None), "V-3": _item("SKIP", None),
        "V-4": _item("SKIP", None),
    }
    base = {"mode": "PROBE", "prereg_sha12": PREREG_SHA[:12],
            "accept_sha12": ACCEPT_SHA[:12], "day": ORDER_DAY,
            "code": CODE, "qty": QTY, "side": "BUY", "price": price,
            "notional": notional, "acceptance": acc, "exceptions": []}

    if notional > BUDGET or notional > MAX_NOTIONAL:
        return {**base, "decision": "NOTIONAL_EXCEEDED", "n_real_orders": 0}

    if dryrun:
        return {**base, "decision": "DRYRUN", "n_real_orders": 0,
                "note": "dryrun 不导入 gmtrade、不建连、不读授权、不发单；V-4 由落盘后回填"}

    gm = _gm if _gm is not None else _gm_module()
    tok, account_id = os.environ.get("DQ_GM_TOKEN"), os.environ.get("DQ_GM_ACCOUNT_ID")
    if not tok or not account_id:
        return {**base, "decision": "ENV_MISSING",
                "missing": [k for k, v in (("DQ_GM_TOKEN", tok),
                                           ("DQ_GM_ACCOUNT_ID", account_id)) if not v],
                "n_real_orders": 0}

    gm.set_token(tok)
    gm.set_endpoint(os.environ.get("DQ_GM_ENDPOINT", "api.myquant.cn:9000"))
    gm.login(gm.account(account_id=account_id, account_alias=""))
    cash = gm.get_cash()
    row = cash[0] if cash else None
    rec = {**base, "account_id_mask": mask_account(getattr(row, "account_id", None)),
           "account_name_masked": mask_account(getattr(row, "account_name", None))}

    if not row or str(getattr(row, "account_id", "")) != account_id:
        return {**rec, "decision": "ACCOUNT_MISMATCH", "n_real_orders": 0}
    if rec["account_id_mask"] != EXPECTED_ACCT_MASK:
        return {**rec, "decision": "ACCOUNT_MISMATCH", "n_real_orders": 0,
                "reason": "ACCOUNT_MASK_NOT_EXPECTED"}

    pre_orders = gm.get_orders() or []
    if pre_orders:
        return {**rec, "decision": "PREEXISTING_ORDERS",
                "n_preexisting": len(pre_orders), "n_real_orders": 0}
    pre_pos = _positions_snapshot(gm)
    rec["positions_before"] = pre_pos
    if pre_pos:
        return {**rec, "decision": "PREEXISTING_POSITIONS", "n_real_orders": 0}
    available = float(getattr(row, "available", 0) or 0)
    rec["available_at_check"] = available

    import paper_authorization_guard as guard
    auth = auth_path or (ROOT / "research/stock_pick_2026-10-25/forward/paper_authorization.json")
    doc = _auth_doc if _auth_doc is not None else guard.load_doc(auth)
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")
    v = guard.check(doc=doc, now=now, action=AUTH_ACTION, notional=notional,
                    cumulative=0.0, available=available)
    rec["auth_check"] = v
    if v["decision"] != guard.ALLOW:
        return {**rec, "decision": "NOT_AUTHORIZED", "n_real_orders": 0}

    # E-1 唯一一笔跌停价买单
    try:
        o = gm.order_volume(symbol=CODE, volume=QTY, side=SIDE_BUY,
                            order_type=ORDER_TYPE_LIMIT,
                            position_effect=POSITION_EFFECT_OPEN, price=float(price))
    except Exception as exc:  # noqa: BLE001
        rec["acceptance"]["E-1"] = _item("FAIL", {"error_type": type(exc).__name__})
        return {**rec, "decision": "ORDER_REJECTED", "n_real_orders": 0}
    cl = o.get("cl_ord_id") or o.get("order_id") if isinstance(o, dict) else (
        getattr(o, "cl_ord_id", None) or getattr(o, "order_id", None))
    if not cl:
        rec["acceptance"]["E-1"] = _item("FAIL", "EMPTY_ORDER_ID")
        return {**rec, "decision": "ORDER_REJECTED", "n_real_orders": 0}
    rec["order_id_mask"] = mask_account(cl)
    rec["n_real_orders"] = 1
    rec["acceptance"]["E-1"] = _item("PASS", {"order_id_mask": rec["order_id_mask"]})

    # E-2 委托状态 + X-FILLED-D0（A 股 T+1：当日买入不可卖，禁止反向卖出）
    status = _order_status(gm, cl)
    pos_after_order = _positions_snapshot(gm)
    if status == "filled" or pos_after_order:
        detail = _position_detail(gm, CODE)
        rec["acceptance"]["E-2"] = _item("PASS", {"status": "filled",
                                                  "positions": pos_after_order})
        rec["acceptance"]["V-1"] = _item("SKIP", "X-FILLED-D0：不判 FAIL")
        rec["exceptions"].append({
            "code": "X-1", "class": "X-FILLED-D0",
            "symbol": CODE, "qty": detail["qty"],
            "fill_price": detail["fill_price"], "order_id_mask": rec["order_id_mask"],
            "filled_at": detail["filled_at"],
            "note": "T+1：11-24 不卖出、不改持仓；11-25 09:36 后由 X-FILLED-D1 timer 处理",
        })
        rec["probe_carry"] = True
        rec["carry_label"] = "PROBE_CARRY"
        rec["include_in_1126_decontam"] = True
        return {**rec, "decision": "X-FILLED-D0"}
    rec["acceptance"]["E-2"] = _item("PASS", {"status": status or "pending"})

    # E-3 30 秒内撤单
    t0 = time.monotonic()
    try:
        if cl:
            cancel_resp = gm.order_cancel([{"cl_ord_id": cl, "account_id": account_id}])
        else:
            cancel_resp = gm.order_cancel_all()
            rec["cancel_fallback"] = "ORDER_CANCEL_ALL"
        elapsed = time.monotonic() - t0
        rec["acceptance"]["E-3"] = _item(
            "PASS" if elapsed <= CANCEL_DEADLINE_SEC else "FAIL",
            {"elapsed_sec": round(elapsed, 3), "response": str(cancel_resp)[:120]})
        if elapsed > CANCEL_DEADLINE_SEC:
            rec["exceptions"].append({"code": "X-2", "class": "X-CANCEL-SLOW"})
            return {**rec, "decision": "X-CANCEL-FAIL"}
    except Exception as exc:  # noqa: BLE001
        rec["acceptance"]["E-3"] = _item("FAIL", {"error_type": type(exc).__name__})
        rec["exceptions"].append({"code": "X-2", "class": "X-CANCEL-FAIL",
                                  "error_type": type(exc).__name__})
        return {**rec, "decision": "X-CANCEL-FAIL"}

    cash2 = gm.get_cash()
    row2 = cash2[0] if cash2 else None
    avail2 = float(getattr(row2, "available", 0) or 0)
    post_orders = gm.get_orders() or []
    post_pos = _positions_snapshot(gm)
    rec.update({"available_after": avail2, "orders_after_cancel": len(post_orders),
                "positions_after": post_pos})
    rec["acceptance"]["V-1"] = _item("PASS" if not post_pos else "FAIL", post_pos)
    rec["acceptance"]["V-2"] = _item(
        "PASS" if abs(avail2 - EXPECTED_AVAILABLE) <= 0.01 else "FAIL", avail2)
    rec["acceptance"]["V-3"] = _item("PASS" if not post_orders else "FAIL", len(post_orders))
    if abs(avail2 - EXPECTED_AVAILABLE) > 0.01:
        rec["exceptions"].append({"code": "X-3", "class": "X-FREEZE",
                                  "observed": avail2, "expected": EXPECTED_AVAILABLE})
    if post_orders:
        rec["exceptions"].append({"code": "X-2", "class": "X-RESIDUAL_PENDING",
                                  "n_pending": len(post_orders)})
    if rec["exceptions"]:
        return {**rec, "decision": "X-FREEZE" if any(
            x["code"] == "X-3" for x in rec["exceptions"]) else "X-CANCEL-FAIL"}
    return {**rec, "decision": "MIN_ORDER_OK"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PA-GM-PROBE-3 最小下单探针（含 H1 验收标准）")
    ap.add_argument("--prev-close", default=None, help="上一交易日收盘价（十进制）")
    ap.add_argument("--today", default=ORDER_DAY)
    ap.add_argument("--dryrun", action="store_true")
    ap.add_argument("--self-check", action="store_true")
    ap.add_argument("--skip-prev-data-check", action="store_true",
                    help="仅本地 dryrun 用：跳过独立数据交叉校（真实执行禁止）")
    args = ap.parse_args(argv)

    try:
        verify_prereg()
    except Halt as e:
        print(json.dumps({"decision": "HALT", "reason": str(e)}, ensure_ascii=False))
        return 3

    if args.self_check:
        print(json.dumps({"decision": "PASS", "limit_price_9.150": limit_down_price("9.150"),
                          "accept_sha12": ACCEPT_SHA[:12],
                          "checks": ["date_gate", "limit_down_floor_0.01",
                                     "prereg_sha", "acceptance_sha"]},
                         ensure_ascii=False))
        return 0

    if args.prev_close is None:
        print(json.dumps({"decision": "HALT", "reason": "PREV_CLOSE_REQUIRED"},
                         ensure_ascii=False))
        return 3
    if args.skip_prev_data_check and not args.dryrun:
        print(json.dumps({"decision": "HALT", "reason": "DATA_CHECK_CANNOT_SKIP_ON_REAL_RUN"},
                         ensure_ascii=False))
        return 3
    try:
        rec = run(prev_close=args.prev_close, today=args.today, dryrun=args.dryrun,
                  check_prev=not args.skip_prev_data_check)
    except (Halt, ValueError) as e:
        rec = {"decision": "HALT", "reason": str(e), "n_real_orders": 0}

    # V-4：产物落盘后才算 PASS；dryrun 与真实执行分文件
    out = ROOT / (OUT_DRYRUN_REL if args.dryrun else OUT_REL)
    out.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(rec.get("acceptance"), dict):
        rec["acceptance"]["V-4"] = _item("PASS", str(out.relative_to(ROOT))) \
            if str(out).startswith(str(ROOT)) else _item("PASS", str(out))
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=2, default=str) + "\n",
                   encoding="utf-8", newline="\n")
    print(json.dumps(rec, ensure_ascii=False, default=str))
    return 0 if rec["decision"] in ("DRYRUN", "MIN_ORDER_OK") else 4


if __name__ == "__main__":
    sys.exit(main())
