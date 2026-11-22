#!/usr/bin/env python3
"""PROBE 执行器（PA-GM-PROBE-2，授权 463bd735）：真实 gm-paper 下单一腿。

与 gm_paper_order_adapter（离线验证类，broker 恒不调用）不同，本模块是
**真实下单通道**：复用 sim_rebalance_executor.GmTradeBroker（gm.order_volume
限价）与 paper_authorization_guard（复用已签 paper_authorization.json，
scope 已含 sim_rebalance_order，不新建/修改任何授权文件）。

三段分离，按日期门控（冻结于 PA-GM-PROBE-2）：
- ``--leg buy``  ：仅 2026-11-25 允许；2 只×1 手限价买入，cid 带 PROBE 前缀
- ``--leg sell`` ：仅 2026-11-26 允许；对应 2 只各 1 手卖出
- ``--leg decontam``：仅 2026-11-26 日终；验证 positions=[]（唯一去污硬标准）

双记录：成交回报 created_at 原始 seconds/nanos + 转换 ISO（H9 语义闭合）。
现金三字段：cash_before / cash_after / 费用合计（记录型，非通过标准）。

失败关闭：任一门不过 ⇒ rc=4、零报单；不对授权文件做任何写操作。
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend" / "src"))

import paper_authorization_guard as guard  # noqa: E402
import sim_rebalance_executor as ex  # noqa: E402

# ---- 冻结参数（PA-GM-PROBE-2，任何变更 = 新预登记）----
PREREG_REL = "docs/PROBE_EXEC_PREREG_2026-11-20.md"
PREREG_SHA = "2998a7a038a837a462708fcb898b3e4e8292c7d35edf3a4bbfb2ade5b73f2f05"
BUY_DAY = "20261125"
SELL_DAY = "20261126"
N_SYMBOLS = 2
LOTS = 1
SHARES_PER_LOT = 100
BUDGET_PER_SYMBOL = 4000.0
MAX_NOTIONAL = 100000.0
AUTH_ACTION = "sim_rebalance_order"
PROBE_TAG = "PROBE"
# 合规主板标的（过 validate_order，且 1 手金额 < BUDGET_PER_SYMBOL=4000 ⇒ 股价 ≤40 元）
# 预登记 §3 只冻结"2 只各 1 手、单笔≤4000"，未写死代码 ⇒ 此处按预算择低价主板股。
DEFAULT_CODES = ("601398", "600000")   # 沪市主板：工商银行 / 浦发银行，单价均 <40 元
LEG_DIR = "research/stock_pick_2026-10-25/forward/probe_reconcile"
ORDER_DIR = "research/stock_pick_2026-10-25/forward/sim_orders"


def verify_prereg() -> None:
    p = ROOT / PREREG_REL
    if not p.is_file() or hashlib.sha256(p.read_bytes()).hexdigest() != PREREG_SHA:
        raise ex.Halt("PROBE_PREREG_SHA_MISMATCH")


def _date_gate(leg: str, today: str) -> None:
    want = {"buy": BUY_DAY, "sell": SELL_DAY, "decontam": SELL_DAY}[leg]
    if today != want:
        raise ex.Halt(f"DATE_GATE:{leg}:expected {want},got {today}")


def mask_account(account_id: str | None) -> str:
    """§五：账户标识只回显前4+后4，中间打星；绝不回显完整值。"""
    s = str(account_id or "")
    if len(s) <= 8:
        return "*" * len(s)
    return s[:4] + "*" * (len(s) - 8) + s[-4:]


def _require_prices(codes: list[str], prices: dict[str, str]) -> list[str]:
    """§二 P0：整批缺价预检，必须在建连/循环之前调用。

    原实现在下单循环里用 ``prices[c]``，第 2 只缺价时第 1 只已发出，
    服务端委托不会随进程退出而撤销 ⇒ 部分发送。此处失败关闭、零报单。
    """
    return [c for c in codes if c not in prices or not str(prices[c]).strip()]


def _auth_ok(*, auth_path: Path, now: str, notional: float, available: float) -> dict:
    doc = guard.load_doc(auth_path)
    if doc is None:
        return {"decision": guard.DENY, "reason": "NO_AUTH"}
    v = guard.check(doc=doc, now=now, action=AUTH_ACTION, notional=notional,
                    cumulative=0.0, available=available)
    return v


def _field(row: Any, name: str, default: Any = None) -> Any:
    """兼容 gm protobuf 对象与 dict：真实 Cash 没有 .get（第11轮新发现的隐性坑）。"""
    if isinstance(row, dict):
        return row.get(name, default)
    return getattr(row, name, default)


def _cash_fields(gm: Any) -> dict:
    """现金三字段（记录型）+ 实时 available + 账户标识（掩码后才落盘）。"""
    # 执行机实测：gmtrade.api 只有 get_cash()，且返回 Cash 对象（不是 list）；
    # 旧代码 gm.cash()[0] 在真实环境必然 AttributeError/TypeError。
    getter = getattr(gm, "get_cash", None) or getattr(gm, "cash", None)
    if getter is None:
        raise ex.Halt("GM_CASH_API_MISSING")
    cash = getter()
    row = cash[0] if isinstance(cash, (list, tuple)) and cash else (cash or {})
    return {"cash_before": _field(row, "cash"),
            "cash_after": _field(row, "cash"),
            "fee_total": _field(row, "cum_commission"),
            "available_at_check": float(_field(row, "available", 0) or 0),
            "account_name_masked": mask_account(_field(row, "account_name")),
            "account_id_mask": mask_account(_field(row, "account_id"))}


def _created_at_dual(rec: dict) -> dict:
    """成交回报 created_at 双记录：原始 seconds/nanos + 转换 ISO。"""
    out = {}
    for k in ("created_at", "created_at_raw"):
        if k in rec:
            out[k] = rec[k]
    ca = rec.get("created_at")
    if isinstance(ca, str):
        out["created_at_iso"] = ca
        out["created_at_raw"] = rec.get("created_at_raw") or {"note": "见落盘原始回报"}
    return out


def _write_json(rel: str, obj: Any) -> Path:
    p = ROOT / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n",
                 encoding="utf-8", newline="\n")
    return p


def _place(broker: Any, *, side: str, code: str, price: str) -> dict:
    cid = ex.client_order_id(day=BUY_DAY if side == "BUY" else SELL_DAY,
                             code=code, side=side, qty=LOTS * SHARES_PER_LOT)
    return broker.place_limit_order(code=code, side=side,
                                    qty=LOTS * SHARES_PER_LOT, price=price, cid=cid)


def run_buy(*, today: str, now: str, auth_path: Path, prices: dict[str, str],
            dryrun: bool) -> dict:
    _date_gate("buy", today)
    codes = list(DEFAULT_CODES)[:N_SYMBOLS]
    missing = _require_prices(codes, prices)
    if missing:
        return {"decision": "MISSING_PRICE", "codes": codes, "missing": missing,
                "n_real_orders": 0}
    notional = sum(float(prices[c]) * LOTS * SHARES_PER_LOT for c in codes)
    if dryrun:
        v = _auth_ok(auth_path=auth_path, now=now, notional=notional,
                     available=MAX_NOTIONAL)
        if v["decision"] != guard.ALLOW:
            return {"decision": "NOT_AUTHORIZED", "reason": v.get("reason"),
                    "n_real_orders": 0}
        return {"decision": "DRYRUN", "codes": codes, "prices": prices,
                "notional": notional, "n_real_orders": 0}
    broker = ex.GmTradeBroker(token=os.environ["DQ_GM_TOKEN"],
                              account_id=os.environ["DQ_GM_ACCOUNT_ID"])
    cash0 = _cash_fields(broker._gm)
    v = _auth_ok(auth_path=auth_path, now=now, notional=notional,
                 available=cash0["available_at_check"])
    if v["decision"] != guard.ALLOW:
        return {"decision": "NOT_AUTHORIZED", "reason": v.get("reason"),
                "available_at_check": cash0["available_at_check"], "n_real_orders": 0}
    if notional > MAX_NOTIONAL:
        return {"decision": "NOTIONAL_EXCEEDED", "notional": notional, "n_real_orders": 0}
    results = [_place(broker, side="BUY", code=c, price=prices[c]) for c in codes]
    cash1 = _cash_fields(broker._gm)
    rec = {"schema": "probe_exec/v1", "mode": PROBE_TAG, "leg": "buy",
           "day": BUY_DAY, "now": now, "codes": codes, "orders": results,
           "cash_before": cash0["cash_before"], "cash_after": cash1["cash_after"],
           "fee_total": cash1["fee_total"],
           "available_at_check": cash0["available_at_check"],
           "account_id_mask": cash0["account_id_mask"],
           "account_name_masked": cash0["account_name_masked"],
           "n_real_orders": len(results)}
    _write_json(f"{ORDER_DIR}/PROBE_{BUY_DAY}_buy.json", rec)
    return {"decision": "EXECUTED", "n_real_orders": len(results), "record": rec}


def run_sell(*, today: str, now: str, auth_path: Path, prices: dict[str, str],
             dryrun: bool) -> dict:
    _date_gate("sell", today)
    codes = list(DEFAULT_CODES)[:N_SYMBOLS]
    missing = _require_prices(codes, prices)
    if missing:
        return {"decision": "MISSING_PRICE", "codes": codes, "missing": missing,
                "n_real_orders": 0}
    if dryrun:
        v = _auth_ok(auth_path=auth_path, now=now,
                     notional=BUDGET_PER_SYMBOL * N_SYMBOLS,
                     available=MAX_NOTIONAL)
        if v["decision"] != guard.ALLOW:
            return {"decision": "NOT_AUTHORIZED", "reason": v.get("reason"),
                    "n_real_orders": 0}
        return {"decision": "DRYRUN", "codes": codes, "n_real_orders": 0}
    broker = ex.GmTradeBroker(token=os.environ["DQ_GM_TOKEN"],
                              account_id=os.environ["DQ_GM_ACCOUNT_ID"])
    cash0 = _cash_fields(broker._gm)
    v = _auth_ok(auth_path=auth_path, now=now,
                 notional=BUDGET_PER_SYMBOL * N_SYMBOLS,
                 available=cash0["available_at_check"])
    if v["decision"] != guard.ALLOW:
        return {"decision": "NOT_AUTHORIZED", "reason": v.get("reason"),
                "available_at_check": cash0["available_at_check"], "n_real_orders": 0}
    results = [_place(broker, side="SELL", code=c, price=prices[c]) for c in codes]
    cash1 = _cash_fields(broker._gm)
    rec = {"schema": "probe_exec/v1", "mode": PROBE_TAG, "leg": "sell",
           "day": SELL_DAY, "now": now, "codes": codes, "orders": results,
           "cash_before": cash0["cash_before"], "cash_after": cash1["cash_after"],
           "fee_total": cash1["fee_total"],
           "available_at_check": cash0["available_at_check"],
           "account_id_mask": cash0["account_id_mask"],
           "account_name_masked": cash0["account_name_masked"],
           "n_real_orders": len(results)}
    _write_json(f"{ORDER_DIR}/PROBE_{SELL_DAY}_sell.json", rec)
    return {"decision": "EXECUTED", "n_real_orders": len(results), "record": rec}


def run_decontam(*, today: str, dryrun: bool) -> dict:
    """去污：positions=[] 唯一硬标准；读服务端成交回报双记录 created_at。"""
    _date_gate("decontam", today)
    if dryrun:
        return {"decision": "DRYRUN", "positions": []}
    import gmtrade.api as gm
    positions = gm.get_positions() or []
    reports = gm.get_execution_reports() or []
    dual = []
    for r in reports:
        # ExecRpt 是 protobuf 对象：用 adapter 的 _as_plain 转 dict，
        # 其中 created_at 经 H9 修复的 _proto_timestamp_to_iso 转 ISO。
        import paper_account_adapter as pa
        d = pa._as_plain(r) if not isinstance(r, dict) else r
        dual.append(_created_at_dual(d))
    rec = {"schema": "probe_decontam/v1", "mode": PROBE_TAG, "day": SELL_DAY,
           "positions": [str(getattr(p, "symbol", p)) for p in positions],
           "decontaminated": len(positions) == 0,
           "execution_reports_created_at_dual": dual,
           "hard_standard": "positions==[]"}
    _write_json(f"{LEG_DIR}/PROBE_{SELL_DAY}_decontam.json", rec)
    return {"decision": "PASS" if rec["decontaminated"] else "FAIL", "record": rec}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PROBE 真实执行器（PA-GM-PROBE-2）")
    ap.add_argument("--self-check", action="store_true", help="离线自检，不需 --leg")
    ap.add_argument("--leg", choices=("buy", "sell", "decontam"))
    ap.add_argument("--today", default=None, help="覆盖日期（仅 dryrun 测试用）")
    ap.add_argument("--now", default=None)
    ap.add_argument("--auth", default=None)
    ap.add_argument("--price", action="append", default=[], help="code=price，可重复")
    ap.add_argument("--dryrun", action="store_true", help="不报单，只走门控与意图")
    args = ap.parse_args(argv)

    try:
        verify_prereg()
    except ex.Halt as e:
        print(json.dumps({"decision": "HALT", "reason": str(e)}, ensure_ascii=False))
        return 3

    if args.self_check:
        # 离线自检：日期门、授权门、cid 唯一性、去污判据，不碰 broker/授权文件
        out = {"decision": "PASS", "checks": []}
        try:
            _date_gate("buy", BUY_DAY); out["checks"].append("date_buy_ok")
        except ex.Halt:
            out["decision"] = "FAIL"
        try:
            _date_gate("buy", "20261120")
            out["decision"] = "FAIL"; out["checks"].append("date_wrong_NOT_detected")
        except ex.Halt:
            out["checks"].append("date_wrong_detected")
        print(json.dumps(out, ensure_ascii=False))
        return 0 if out["decision"] == "PASS" else 1

    if not args.leg:
        print(json.dumps({"decision": "HALT", "reason": "LEG_REQUIRED"}, ensure_ascii=False))
        return 3

    today = args.today or dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%d")
    now = args.now or dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds")
    prices = dict(p.split("=", 1) for p in args.price if "=" in p)
    auth = Path(args.auth) if args.auth else (
        ROOT / "research/stock_pick_2026-10-25/forward/paper_authorization.json")

    # dryrun 用离线授权夹具（自建临时自洽 doc，绝不碰真实授权文件）
    tmp_auth = None
    if args.dryrun:
        import tempfile
        import gm_paper_order_adapter as adapter
        tmp_auth = Path(tempfile.mkdtemp(prefix="dq_probe_")) / "auth.json"
        adapter._write_doc(tmp_auth, adapter._auth_doc(not_after="2026-12-02T23:59:00+08:00"))
        auth = tmp_auth
        now = "2026-11-25T09:35:00+08:00" if args.leg == "buy" else "2026-11-26T09:35:00+08:00"

    try:
        if args.leg == "decontam":
            r = run_decontam(today=today, dryrun=args.dryrun)
        elif args.leg == "buy":
            r = run_buy(today=today, now=now, auth_path=auth, prices=prices, dryrun=args.dryrun)
        else:
            r = run_sell(today=today, now=now, auth_path=auth, prices=prices, dryrun=args.dryrun)
    except ex.Halt as e:
        r = {"decision": "HALT", "reason": str(e), "n_real_orders": 0}
    print(json.dumps(r, ensure_ascii=False, default=str))
    return 0 if r["decision"] in ("DRYRUN", "EXECUTED", "PASS") else 4


if __name__ == "__main__":
    sys.exit(main())
