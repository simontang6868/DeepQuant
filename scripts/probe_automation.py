#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PROBE→12-01 施工单自动化入口（2026-11-22）。

只做施工单 [AUTO] 项；[HUMAN] 四项只生成卡片/提醒，绝不代执行、不代填。
**不改动任何既有门控顺序、落盘位置或幂等逻辑**：本模块全部是外围编排，
真正下单/去污/六门判定仍分别调用 probe_executor.py 与 sim_rebalance_executor.py。

子命令：
  preflight      11-25 09:30 前置自检
  buy-from-file  11-25 09:35 从本人填写的价格文件取价后调用 probe_executor.run_buy
  decontam       11-26 10:00/14:00/14:55 单次去污并累计状态
  classify       11-26 15:00 市场原因/技术失败分类（判据只许变严）
  predict        11-30 出证后 M-1201-PREDICT，全 PASS 才 enable sim timer
  alert          P0/P1/P2 告警（邮件 + 企业微信 webhook；电话属 HUMAN）
  watch-lock     P0 15 分钟无确认 ⇒ disable 全部 sim timer
  snapshot       只读委托/持仓快照（卡片里的命令，人工执行）
  gen-cards      生成 P0/P1/P2 告警卡片

执行机仓库根是 `/opt/quant`（**不是** `/opt/quant/DeepQuant-main`）。
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend" / "src"))

FW = ROOT / "research/stock_pick_2026-10-25/forward"
PRICE_FILE = FW / "probe_price_20261125.json"
PREFLIGHT_OUT = FW / "probe_preflight_20261125.json"
DECONTAM_STATE = FW / "probe_decontam_state_20261126.json"
MARKET_CAUSE_FILE = FW / "probe_market_cause_20261126.json"
DECONTAM_RESULT = FW / "probe_decontam_classified_20261126.json"
PREDICT_OUT = FW / "m1201_predict_20261130.json"
CARD_DIR = FW / "alert_cards"
ACK_DIR = FW / "alert_acks"

PROBE_NOW = "2026-11-25T09:35:00+08:00"
EXEC_NOW = "2026-12-01T09:35:00+08:00"
BUY_PRICES = {"601398", "600000"}
BUY_NOTIONAL = 1590.0
SIM_TIMER_PREFIX = "deepquant-sim-"
POST_CHAIN_TIMERS = ("deepquant-sim-exec.timer", "deepquant-sim-sentinel.timer",
                     "deepquant-sim-reconcile.timer")


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _write(path: Path, obj: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, default=str) + "\n",
                    encoding="utf-8", newline="\n")
    return path


def _read_json(path: Path) -> dict | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def _gm():
    import gmtrade.api as gm
    return gm


def _login_gm(gm: Any) -> str:
    tok, acc = _env("DQ_GM_TOKEN"), _env("DQ_GM_ACCOUNT_ID")
    if not tok or not acc:
        raise RuntimeError("ENV_MISSING:DQ_GM_TOKEN/DQ_GM_ACCOUNT_ID")
    gm.set_token(tok)
    gm.set_endpoint(_env("DQ_GM_ENDPOINT") or "api.myquant.cn:9000")
    gm.login(gm.account(account_id=acc, account_alias=""))
    return acc


# ---------------------------------------------------------------- A1 前置自检
def preflight(*, today: str = "20261125") -> dict:
    """NTP 偏差只警告；venv/env/授权/台账任一失败即硬失败。"""
    hard: list[str] = []
    warn: list[str] = []

    try:
        import gmtrade.api  # noqa: F401
    except Exception as e:
        hard.append(f"VENV_NO_GMTRADE:{type(e).__name__}")
    if sys.executable.find(".venv-gm") < 0:
        hard.append("VENV_NOT_GM:" + sys.executable)
    for k in ("DQ_GM_TOKEN", "DQ_GM_ACCOUNT_ID"):
        if not _env(k):
            hard.append("ENV_MISSING:" + k)

    import paper_authorization_guard as guard
    auth = ROOT / "research/stock_pick_2026-10-25/forward/paper_authorization.json"
    doc = guard.load_doc(auth)
    available = 0.0
    if doc is None:
        hard.append("NO_AUTH")
    try:
        gm = _gm()
        _login_gm(gm)
        cash = gm.get_cash()
        row = cash[0] if isinstance(cash, (list, tuple)) and cash else cash
        available = float(getattr(row, "available", 0) or 0)
    except Exception as e:
        hard.append("ACCOUNT_UNREADABLE:" + type(e).__name__)
    if doc is not None:
        v = guard.check(doc=doc, now=PROBE_NOW, action="sim_rebalance_order",
                        notional=BUY_NOTIONAL, cumulative=0.0, available=available)
        if v["decision"] != guard.ALLOW:
            hard.append("AUTH_DENY:" + str(v.get("reason")))

    ledger = ROOT / "research/stock_pick_2026-10-25/forward/ledger/20261124.json"
    if not ledger.is_file():
        hard.append("LEDGER_MISSING:" + _rel(ledger))

    try:
        p = subprocess.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
                           capture_output=True, text=True, timeout=8)
        if p.stdout.strip() != "yes":
            warn.append("NTP_NOT_SYNCHRONIZED")
    except Exception as e:
        warn.append("NTP_CHECK_UNAVAILABLE:" + type(e).__name__)

    rec = {"at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
           "day": today, "hard": hard, "warn": warn,
           "available_at_check": available,
           "decision": "PREFLIGHT_PASS" if not hard else "PREFLIGHT_FAIL",
           "n_real_orders": 0}
    _write(PREFLIGHT_OUT, rec)
    return rec


# ---------------------------------------------------------------- A2/A3 买腿
def buy_from_file(*, dryrun: bool = False) -> dict:
    """价格文件缺任一 code ⇒ MISSING_PRICE、零报单；不猜价、不补价。"""
    rec = _read_json(PRICE_FILE)
    missing = [] if rec is None else sorted(c for c in BUY_PRICES
                                            if c not in rec or not str(rec[c]).strip())
    if rec is None:
        missing = sorted(BUY_PRICES)
    if missing:
        out = {"decision": "MISSING_PRICE", "missing": missing,
               "price_file": _rel(PRICE_FILE),
               "n_real_orders": 0}
        _write(FW / "probe_buy_source_20261125.json", out)
        return out
    prices = {c: str(rec[c]).strip() for c in BUY_PRICES}
    # 与清单一致：执行人只从价格文件取价，仍走 probe_executor 的循环外硬门
    import probe_executor as pe
    r = pe.run_buy(today="20261125", now=PROBE_NOW,
                   auth_path=ROOT / "research/stock_pick_2026-10-25/forward/paper_authorization.json",
                   prices=prices, dryrun=dryrun)
    _write(FW / "probe_buy_source_20261125.json",
           {"decision": r["decision"], "prices": prices,
            "price_file": _rel(PRICE_FILE),
            "n_real_orders": r.get("n_real_orders", 0)})
    return r


# ---------------------------------------------------------------- A4 去污三次
def slot_from_clock(now: dt.datetime | None = None) -> int:
    """0=自动：10:00→1、14:00→2、14:55→3；其余时段拒绝（失败关闭）。"""
    n = now or dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))
    key = n.strftime("%H:%M")
    return {"10:00": 1, "14:00": 2, "14:55": 3}.get(key, 0)


def decontam_once(*, slot: int) -> dict:
    if slot == 0:
        slot = slot_from_clock()
        if slot == 0:
            return {"decision": "SLOT_NOT_RECOGNIZED", "n_real_orders": 0}
    import probe_executor as pe
    r = pe.run_decontam(today="20261126", dryrun=False)
    state = _read_json(DECONTAM_STATE) or {"day": "20261126", "attempts": []}
    state["attempts"].append({"slot": slot,
                              "at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
                              "decision": r.get("decision"),
                              "positions": (r.get("record") or {}).get("positions", []),
                              "error_type": r.get("error_type")})
    state["n_attempts"] = len(state["attempts"])
    _write(DECONTAM_STATE, state)
    if slot == 3:
        return classify()
    return {"decision": "RECORDED", "slot": slot, "n_attempts": state["n_attempts"]}


def classify(*, state: dict | None = None) -> dict:
    """技术失败优先于市场原因；市场原因须有独立证据文件，否则按技术失败处理。"""
    st = state if state is not None else (_read_json(DECONTAM_STATE) or {})
    attempts = st.get("attempts", [])
    last = attempts[-1] if attempts else {}
    positions = last.get("positions") or []
    tech = [a for a in attempts if a.get("error_type")
            or str(a.get("decision", "")).startswith("HALT")]
    rec = {"day": "20261126", "n_attempts": len(attempts),
           "positions_after": positions, "hard_standard": "positions==[]"}
    if not positions and last.get("decision") == "PASS":
        rec["decision"] = "DECONTAM_OK"
    elif tech:
        rec["decision"] = "GATE_FAIL_TECHNICAL"
        rec["reason"] = "SCRIPT_OR_NETWORK_OR_CREDENTIAL"
        rec["evidence"] = {"error_types": sorted({a["error_type"] for a in tech if a.get("error_type")})}
        rec["action"] = "NO_POSTPONE_ESCALATE_HUMAN"
    else:
        cause = _read_json(MARKET_CAUSE_FILE) or {}
        if cause.get("reason") in ("SUSPENDED", "LIMIT_UP_DOWN") and cause.get("symbols"):
            rec["decision"] = "GATE_NO_GO_MARKET"
            rec["reason"] = cause["reason"]
            rec["evidence"] = {"file": _rel(MARKET_CAUSE_FILE),
                               "symbols": cause["symbols"]}
            rec["action"] = "POSTPONE_BRANCH_B"
        else:
            # 没有独立市场证据 ⇒ 不许猜成市场原因（判据只许变严）
            rec["decision"] = "GATE_FAIL_TECHNICAL"
            rec["reason"] = "NO_MARKET_EVIDENCE"
            rec["action"] = "NO_POSTPONE_ESCALATE_HUMAN"
    _write(DECONTAM_RESULT, rec)
    return rec


# ---------------------------------------------------------------- A5 六门预测
def predict(*, run_gates=None, systemctl=None) -> dict:
    """调既有 --gates，按产物判 ALLOW；任一门 FAIL ⇒ 不 enable、写顺延记录。"""
    if run_gates is None:
        py = ROOT / ".venv-gm/bin/python"
        cmd = [str(py if py.is_file() else Path("/opt/quant/.venv-gm/bin/python")),
               "-u", str(ROOT / "scripts/sim_rebalance_executor.py"), "--gates"]
        p = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=900)
        raw = p.stdout.strip()
        if p.returncode not in (0, 4) or not raw:
            return _predict_write({"decision": "POSTPONED", "reason": "GATES_EXEC_ERROR",
                                   "rc": p.returncode, "stderr": p.stderr[-400:]})
        try:
            out = json.loads(raw[raw.rfind("{"):])
        except Exception as e:
            return _predict_write({"decision": "POSTPONED",
                                   "reason": "GATES_OUTPUT_UNPARSEABLE:" + type(e).__name__})
    else:
        out = run_gates()

    gates = out.get("gates") or []
    ok = (out.get("now") == EXEC_NOW
          and len(gates) == 6
          and all(isinstance(g, dict) and g.get("ok") is True for g in gates)
          and not out.get("plan_error")
          and not (out.get("account") or {}).get("error"))
    rec = {"at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
           "milestone": "M-1201-PREDICT", "injected_now": out.get("now"),
           "gates": [{"gate": g.get("gate"), "ok": g.get("ok") is True} for g in gates],
           "account_error": (out.get("account") or {}).get("error"),
           "plan_error": out.get("plan_error")}
    if not ok:
        rec.update({"decision": "POSTPONED", "enabled": [],
                    "reason": "SIX_GATES_NOT_ALL_PASS"})
        return _predict_write(rec)

    if systemctl is None:
        systemctl = lambda args: subprocess.run(["systemctl", *args], capture_output=True,
                                                text=True, timeout=60)
    enabled, failed = [], []
    for unit in POST_CHAIN_TIMERS:
        # ⚠️ enable ≠ active：现场实测 enable 后 list-timers 仍是 n/a，
        # 少一步 start 则 12-01 09:35 根本不会触发。
        pe = systemctl(["enable", unit])
        ps = systemctl(["start", unit]) if getattr(pe, "returncode", 1) == 0 else pe
        if getattr(ps, "returncode", 1) == 0:
            enabled.append(unit)
        else:
            failed.append(unit)
    rec.update({"decision": "ARMED" if not failed else "PARTIAL",
                "armed": enabled, "arm_failed": failed,
                "note": "enable + start 双步；只 enable 不 start 不会触发"})
    return _predict_write(rec)


def _predict_write(rec: dict) -> dict:
    _write(PREDICT_OUT, rec)
    return rec


# ---------------------------------------------------------------- A6 告警
def gen_cards() -> list[Path]:
    CARD_DIR.mkdir(parents=True, exist_ok=True)
    snap = "cd /opt/quant && set -a; . /opt/quant/env; set +a; /opt/quant/.venv-gm/bin/python scripts/probe_automation.py snapshot"
    cancel = (snap + " --cancel-cl-ord-id '<从快照复制 cl_ord_id>'")
    cards = {
        "P0_ORDER_FAIL": ("P0 下单失败/部分发送/持仓不一致", "电话+邮件+webhook", "15 分钟",
            ["现象：下单返回非成功、循环异常、服务端持仓与本地预期不一致",
             "字段：decision / n_real_orders / order_id_mask / positions_after / error_type",
             f"持仓快照命令：`{snap}`", f"撤单命令（人工确认后）：`{cancel}`",
             "联系电话：[HUMAN-FILL: 本人填写]；第二人：[HUMAN-FILL: 本人填写]",
             "15 分钟无确认 ⇒ 自动 disable 全部 deepquant-sim-*.timer，等待人工"]),
        "P1_FILL_SLIP": ("P1 成交率<95%/滑点>0.3%/对账差异", "邮件", "当日",
            ["现象：成交回报与计划不一致、滑点超阈值、对账差异",
             "字段：fill_rate / slippage_pct / reconcile_diff",
             f"持仓快照命令：`{snap}`", "联系电话：[HUMAN-FILL: 本人填写]"]),
        "P2_SELFCHECK": ("P2 自检警告（NTP/磁盘/陈旧文件）", "邮件", "次日",
            ["现象：NTP 未同步、磁盘余量低、输入文件陈旧",
             "字段：warn / available_at_check / ledger_mtime",
             "无需立即撤单；下一交易日复跑前置自检"]),
    }
    paths = []
    for name, (title, channel, sop, lines) in cards.items():
        body = "\n".join([f"# {title}", "", f"- 通知：{channel}｜响应：{sop}", ""] + lines) + "\n"
        p = CARD_DIR / f"{name}.md"
        p.write_text(body, encoding="utf-8", newline="\n")
        paths.append(p)
    return paths


def send_webhook(text: str) -> bool:
    url = _env("DEEPQUANT_ALERT_WEBHOOK")
    if not url:
        return False
    try:
        req = urllib.request.Request(url, data=json.dumps({"text": text}, ensure_ascii=False).encode(),
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        return True
    except Exception as e:
        print(f"[alert] webhook failed: {type(e).__name__}", file=sys.stderr)
        return False


def send_mail(subject: str, body: str) -> bool:
    sys.path.insert(0, str(ROOT / "deploy/bin"))
    import notify_mail  # noqa: WPS433
    cfg = notify_mail.read_config(dict(os.environ))
    if notify_mail.missing_keys(cfg):
        return False
    try:
        notify_mail.send(notify_mail.build_message(cfg, subject, body, []), cfg)
        return True
    except Exception as e:
        print(f"[alert] mail failed: {type(e).__name__}", file=sys.stderr)
        return False


def alert(*, level: str, card: Path, alert_id: str, watch_seconds: int = 900,
          mail_fn=None, webhook_fn=None, spawn=None) -> dict:
    body = card.read_text(encoding="utf-8") if card.is_file() else f"[CARD_MISSING] {card}"
    mf = mail_fn or send_mail
    wf = webhook_fn or send_webhook
    ok_mail = mf(f"[DeepQuant {level}] 施工单告警 {alert_id}", body)
    ok_hook = wf(f"[DeepQuant {level}] {alert_id} {body[:160]}")
    rec = {"id": alert_id, "level": level, "at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
           "mail": ok_mail, "webhook": ok_hook, "phone": "HUMAN",
           "ack_file": _rel(ACK_DIR / f"{alert_id}.ack")}
    if level == "P0":
        sp = spawn or (lambda: subprocess.Popen(
            [sys.executable, "-u", str(Path(__file__)), "watch-lock",
             "--id", alert_id, "--wait-seconds", str(watch_seconds)],
            start_new_session=True))
        sp()
        rec["lockdown_armed_seconds"] = watch_seconds
    _write(FW / f"alert_{alert_id}.json", rec)
    rec["decision"] = "SENT" if (ok_mail or ok_hook) else "DELIVERY_FAILED"
    return rec


def sim_timers(systemctl=None) -> list[str]:
    """现场枚举 deepquant-sim-*.timer，不硬编码清单（避免漏 disable）。"""
    if systemctl is None:
        systemctl = lambda args: subprocess.run(["systemctl", *args], capture_output=True,
                                                text=True, timeout=60)
    p = systemctl(["list-unit-files", "--type=timer", "--no-legend"])
    return [line.split()[0] for line in (p.stdout or "").splitlines()
            if line.strip() and line.split()[0].startswith(SIM_TIMER_PREFIX)]


def watch_lock(*, alert_id: str, wait_seconds: int = 900, sleep_fn=None,
               systemctl=None, mail_fn=None) -> dict:
    ack = ACK_DIR / f"{alert_id}.ack"
    sl = sleep_fn or time.sleep
    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if ack.is_file():
            rec = {"id": alert_id, "decision": "ACKNOWLEDGED", "locked": False}
            _write(FW / f"lockdown_{alert_id}.json", rec)
            return rec
        sl(min(30, max(1, deadline - time.monotonic())))
    units = sim_timers(systemctl)
    disabled, failed = [], []
    sc = systemctl or (lambda args: subprocess.run(["systemctl", *args], capture_output=True,
                                                   text=True, timeout=60))
    for u in units:
        # disable 只删软链；正在 active 的 timer 不 stop 仍会触发 ⇒ 必须双步
        pd = sc(["disable", u])
        ps = sc(["stop", u]) if getattr(pd, "returncode", 1) == 0 else pd
        (disabled if getattr(ps, "returncode", 1) == 0 else failed).append(u)
    rec = {"id": alert_id, "decision": "LOCKDOWN", "locked": not failed,
           "disabled": disabled, "failed": failed, "window_seconds": wait_seconds}
    _write(FW / f"lockdown_{alert_id}.json", rec)
    mf = mail_fn or send_mail
    mf(f"[DeepQuant P0] LOCKDOWN {alert_id}", json.dumps(rec, ensure_ascii=False, indent=2))
    return rec


# ---------------------------------------------------------------- 人工卡片命令
def snapshot(*, cancel_cl_ord_id: str | None = None, gm: Any = None) -> dict:
    gm = gm or _gm()
    _login_gm(gm)
    orders = [{"cl_ord_id": getattr(o, "cl_ord_id", None),
               "symbol": getattr(o, "symbol", None),
               "volume": getattr(o, "volume", None)} for o in (gm.get_orders() or [])]
    positions = [{"symbol": getattr(p, "symbol", None),
                  "volume": getattr(p, "volume", None)} for p in (gm.get_positions() or [])]
    rec = {"at": dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).isoformat(timespec="seconds"),
           "orders": orders, "positions": positions, "n_real_orders": 0}
    if cancel_cl_ord_id:
        if not any(o["cl_ord_id"] == cancel_cl_ord_id for o in orders):
            rec["decision"] = "CANCEL_TARGET_NOT_FOUND"
            return rec
        gm.order_cancel([{"cl_ord_id": cancel_cl_ord_id, "account_id": _env("DQ_GM_ACCOUNT_ID")}])
        rec["cancelled"] = cancel_cl_ord_id
        rec["decision"] = "CANCEL_DONE_BY_HUMAN"
    else:
        rec["decision"] = "SNAPSHOT_READONLY"
    return rec


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PROBE→12-01 施工单自动化")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("preflight")
    b = sub.add_parser("buy-from-file"); b.add_argument("--dryrun", action="store_true")
    d = sub.add_parser("decontam"); d.add_argument("--slot", type=int, choices=(0, 1, 2, 3), required=True)
    sub.add_parser("classify")
    sub.add_parser("predict")
    a = sub.add_parser("alert")
    a.add_argument("--level", choices=("P0", "P1", "P2"), required=True)
    a.add_argument("--card", required=True)
    a.add_argument("--id", required=True)
    a.add_argument("--wait-seconds", type=int, default=900)
    w = sub.add_parser("watch-lock")
    w.add_argument("--id", required=True)
    w.add_argument("--wait-seconds", type=int, default=900)
    s = sub.add_parser("snapshot")
    s.add_argument("--cancel-cl-ord-id", default=None)
    sub.add_parser("gen-cards")
    args = ap.parse_args(argv)

    if args.cmd == "preflight":
        r = preflight()
    elif args.cmd == "buy-from-file":
        r = buy_from_file(dryrun=args.dryrun)
    elif args.cmd == "decontam":
        r = decontam_once(slot=args.slot)
    elif args.cmd == "classify":
        r = classify()
    elif args.cmd == "predict":
        r = predict()
    elif args.cmd == "alert":
        r = alert(level=args.level, card=Path(args.card), alert_id=args.id,
                  watch_seconds=args.wait_seconds)
    elif args.cmd == "watch-lock":
        r = watch_lock(alert_id=args.id, wait_seconds=args.wait_seconds)
    elif args.cmd == "snapshot":
        r = snapshot(cancel_cl_ord_id=args.cancel_cl_ord_id)
    else:
        r = {"decision": "CARDS", "paths": [_rel(p) for p in gen_cards()]}

    print(json.dumps(r, ensure_ascii=False, default=str))
    good = {"PREFLIGHT_PASS", "DRYRUN", "EXECUTED", "RECORDED", "DECONTAM_OK",
            "GATE_NO_GO_MARKET", "GATE_FAIL_TECHNICAL", "ARMED", "POSTPONED", "SENT",
            "ACKNOWLEDGED", "LOCKDOWN", "SNAPSHOT_READONLY", "CANCEL_DONE_BY_HUMAN",
            "CANCEL_TARGET_NOT_FOUND", "CARDS", "PARTIAL"}
    return 0 if r.get("decision") in good else 4


if __name__ == "__main__":
    sys.exit(main())
