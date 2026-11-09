#!/usr/bin/env python3
"""PA-OCT2026-1 §6：as-of 截断守卫（独立工具，不参与回测计算）。

确认单窗口回测读取的 qfq 行情中不含 asof 之后的行，且 17 个交易日收盘价齐全。

⚠️ 项目铁律（复发 12 次）：
  - 「文件不存在」≠「确认无事件」：缺文件判 FAIL:MISSING_FILE，不静默跳过。
  - 「输出为空 / 集合为空」必须判 FAIL 关闭，不得 all({}.values()) 恒真。
  - qfq_all 的 date 是 YYYY-MM-DD，日历/台账是 YYYYMMDD；必须显式归一化，
    直接字符串比较时 '2026-11-09' < '20261030' 恒真 ⇒ 未来行会全部过关。
  - qfq_all 列序 date,open,close,high,low,volume ⇒ 按列名取 close，禁止按位置。
"""
from __future__ import annotations

import argparse
import json
import pathlib

ASOF = "20261030"
TRADE_FIRST = "20261008"
TRADE_LAST = "20261030"
N_TRADE_DAYS = 17
CODES = ("603193", "301336", "301202", "000597", "002692")
CALENDAR_REL = "research/stock_pick_2026-10-25/forward/trade_calendar.json"


def normalize_date(s: str) -> str:
    """qfq YYYY-MM-DD / 日历 YYYYMMDD → 统一 8 位数字串。"""
    t = s.strip().replace("-", "")
    if len(t) != 8 or not t.isdigit():
        raise ValueError("BAD_DATE:" + s)
    return t


def trade_days(repo_root: pathlib.Path) -> list[str]:
    """独立交易日历推导 10 月交易日；形状不符即失败关闭。"""
    p = repo_root / CALENDAR_REL
    if not p.exists():
        raise RuntimeError("FAIL:NO_CALENDAR")
    raw = json.loads(p.read_text(encoding="utf-8"))["dates"]
    ds = [normalize_date(str(x)) for x in raw]
    oct_ = [d for d in ds if d.startswith("202610")]
    if len(oct_) != N_TRADE_DAYS or oct_[0] != TRADE_FIRST or oct_[-1] != TRADE_LAST:
        raise RuntimeError("FAIL:CALENDAR_SHAPE")
    return oct_


def evaluate(root, codes, days, asof=ASOF) -> dict:
    """返回 as-of 判定。ok 只由 reasons/future 决定，不依赖任何外部状态。"""
    reasons: list[str] = []
    max_seen = ""
    n_future = 0
    n_files = 0
    closes_by_code: dict[str, dict[str, float]] = {}
    if not codes or not days:
        return {"ok": False, "reasons": ["FAIL:EMPTY_INPUT"],
                "max_date_seen": max_seen, "future_rows": n_future,
                "n_files": n_files, "closes": closes_by_code}
    for code in codes:
        fs = sorted(pathlib.Path(root).glob(code + ".*.qfq.csv"))
        if not fs:
            reasons.append("FAIL:MISSING_FILE:" + code)
            continue
        n_files += 1
        p = fs[0]
        with p.open("r", encoding="utf-8") as g:
            header = g.readline().strip().split(",")
        if "close" not in header:
            reasons.append("FAIL:NO_CLOSE_COLUMN:" + code)
            continue
        ci = header.index("close")
        closes: dict[str, float] = {}
        future: list[str] = []
        with p.open("r", encoding="utf-8") as g:
            g.readline()
            for ln in g:
                z = ln.strip().split(",")
                if len(z) <= ci or not z[0].strip():
                    continue
                try:
                    d = normalize_date(z[0])
                except ValueError:
                    reasons.append("FAIL:BAD_DATE:" + code + ":" + z[0])
                    continue
                if d > max_seen:
                    max_seen = d
                if d > asof:
                    future.append(d)
                    continue
                try:
                    closes[d] = float(z[ci])
                except ValueError:
                    reasons.append("FAIL:BAD_PRICE:" + code + ":" + d)
        closes_by_code[code] = closes
        if future:
            n_future += len(future)
            reasons.append("FAIL:FUTURE_ROW:" + code + ":" + max(future))
        missing = [d for d in days if d not in closes]
        if missing:
            reasons.append("FAIL:MISSING_TRADE_DAY:" + code + ":" + ",".join(missing[:3]))
        bad = [d for d in days if d in closes and not (closes[d] > 0)]
        if bad:
            reasons.append("FAIL:NONPOSITIVE_CLOSE:" + code + ":" + ",".join(bad[:3]))
    ok = len(reasons) == 0 and n_future == 0
    return {"ok": ok, "reasons": reasons, "max_date_seen": max_seen,
            "future_rows": n_future, "n_files": n_files, "closes": closes_by_code}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=".")
    ap.add_argument("--root", default=None, help="默认 <repo>/data/qfq_all")
    ap.add_argument("--out", default=None, help="写 asof_guard.json（显式 LF）")
    a = ap.parse_args(argv)
    repo = pathlib.Path(a.repo)
    root = pathlib.Path(a.root) if a.root else repo / "data" / "qfq_all"
    try:
        days = trade_days(repo)
    except RuntimeError as exc:
        print("[asof-guard] " + str(exc))
        return 1
    r = evaluate(root, CODES, days, ASOF)
    payload = {k: v for k, v in r.items() if k != "closes"}
    payload.update({"asof": ASOF, "root": str(root), "n_trade_days": len(days),
                    "codes": list(CODES),
                    "verdict": "ASOF_OK" if r["ok"] else "ASOF_FAIL"})
    if a.out:
        pathlib.Path(a.out).write_bytes(
            (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    print("[asof-guard] " + payload["verdict"] + " | files " + str(r["n_files"])
          + " | max_date " + r["max_date_seen"] + " | future " + str(r["future_rows"]))
    for x in r["reasons"]:
        print("  " + x)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
