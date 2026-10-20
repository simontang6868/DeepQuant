#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Gate 3 设计前置诊断：2005-2006 长停牌复牌日的「未登记除权事件」筛查。

## 为什么要做这个

ADJ 的构造是 `ADJ(t) = EXEC(t) × Π f_s`，`f_s` 只来自**已登记的除权事件**（东财 `RPT_SHAREBONUS_DET`）。
**股权分置改革（2005-04 ~ 2006-12）的对价送股不在那张表里**——它是"股改对价"，不是利润分配。
后果：股改复牌日那根 bar 的原始跌幅看起来像"真实暴跌"（例如 600031 三一重工 2005-06-17
10送3.5派8 元，复牌日 23.82 → 16.61，−30.3%），ADJ 完全不修正它。

更麻烦的是：`rebuild_adj_from_exec.py` 的 A4 判据会给"停牌复牌缺口（> 4 自然日）"豁免，
于是**大部分股改缺口被静默放过**，只有停牌不足 4 天的那 9 只被 A4 抓到。
也就是说 A4 的通过数**高估**了 2005-2006 的数据质量。

## 本脚本做什么（只做诊断，不改数据）

对每只标的，在指定窗口内找出「前一根 bar 与本根相隔 > gap_min 自然日」的复牌点，
计算原始复牌涨跌幅，并与已登记事件比对：

| 情形 | 含义 |
|---|---|
| 有事件 | 正常除权复牌，ADJ 会修正 |
| 无事件 & 跌幅 ≤ −10% | **疑似未登记除权（股改对价/未登记的送转）** → ADJ 会留下假跌幅 |
| 无事件 & 涨幅 ≥ +15% | 复牌暴涨（重组/借壳），不是除权，ADJ **不需要**修正 |

输出按年统计"疑似未登记除权"的只数，用于判定**哪一年起数据可用**。

用法：
  python scripts/pre2016_event_gap_diag.py
  python scripts/pre2016_event_gap_diag.py --start 2005-01-01 --end 2007-06-30
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rebuild_adj_from_exec import fetch_dividends, normalize_events  # noqa: E402


def read_exec(path: str) -> list[tuple[str, float]]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    hdr = [h.strip().lower() for h in rows[0]]
    di, ci = hdr.index("date"), hdr.index("close")
    out = []
    for r in rows[1:]:
        if not r:
            continue
        try:
            out.append((r[di], float(r[ci])))
        except ValueError:
            continue
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="长停牌复牌日未登记除权筛查（诊断，不改数据）")
    ap.add_argument("--exec-dir", default="data/exec_long")
    ap.add_argument("--div-cache", default="data/exec/_div_cache")
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument("--end", default="2007-06-30")
    ap.add_argument("--gap-min", type=int, default=8, help="自然日间隔阈值")
    ap.add_argument("--drop-th", type=float, default=-0.10)
    ap.add_argument("--rise-th", type=float, default=0.15)
    ap.add_argument("--report", default="docs/GATE3_EVENT_GAP_DIAG_2026-10-20.json")
    args = ap.parse_args(argv)

    codes = sorted(os.path.basename(p)[: -len(".exec.csv")] for p in os.listdir(args.exec_dir) if p.endswith(".exec.csv"))
    per_code = []
    for code in codes:
        rows = read_exec(os.path.join(args.exec_dir, f"{code}.exec.csv"))
        if len(rows) < 30:
            continue
        try:
            raw = fetch_dividends(code, args.div_cache, offline=True)
        except FileNotFoundError:
            raw = []
        events, _ = normalize_events(raw, rows[0][0], rows[-1][0])
        ev_dates = {e["ex_date"] for e in events}
        hits = []
        for i in range(1, len(rows)):
            d0, p0 = rows[i - 1]
            d1, p1 = rows[i]
            if not (args.start <= d1 <= args.end):
                continue
            gap = (dt.date.fromisoformat(d1) - dt.date.fromisoformat(d0)).days
            if gap <= args.gap_min or p0 <= 0:
                continue
            r = p1 / p0 - 1
            if d1 in ev_dates:
                kind = "event_ok"
            elif r <= args.drop_th:
                kind = "suspect_missing_ex"
            elif r >= args.rise_th:
                kind = "resume_rally"
            else:
                kind = "resume_flat"
            hits.append({"date": d1, "gap_days": gap, "ret": round(r, 4), "kind": kind})
        if hits:
            per_code.append({"code": code, "hits": hits, "n_suspect": sum(1 for h in hits if h["kind"] == "suspect_missing_ex")})

    by_year: dict[str, dict] = {}
    for rec in per_code:
        for h in rec["hits"]:
            y = h["date"][:4]
            b = by_year.setdefault(y, {"suspect_missing_ex": 0, "event_ok": 0, "resume_rally": 0, "resume_flat": 0})
            b[h["kind"]] += 1

    codes_with_suspect = [r["code"] for r in per_code if r["n_suspect"] > 0]
    print(f"[gap] 窗口 {args.start} ~ {args.end} | 扫描 {len(codes)} 只 | 有复牌点的 {len(per_code)} 只")
    print(f"{'year':<6}{'suspect':>9}{'event_ok':>10}{'rally':>8}{'flat':>6}")
    for y in sorted(by_year):
        b = by_year[y]
        print(f"{y:<6}{b['suspect_missing_ex']:>9}{b['event_ok']:>10}{b['resume_rally']:>8}{b['resume_flat']:>6}")
    print(f"[gap] 含『疑似未登记除权』的标的：{len(codes_with_suspect)} 只 / {len(codes)}")
    print(f"[gap] 例：{codes_with_suspect[:12]}")

    out = {
        "window": [args.start, args.end],
        "params": {"gap_min": args.gap_min, "drop_th": args.drop_th, "rise_th": args.rise_th},
        "n_codes_scanned": len(codes),
        "n_codes_with_resume": len(per_code),
        "n_codes_with_suspect": len(codes_with_suspect),
        "codes_with_suspect": codes_with_suspect,
        "by_year": by_year,
        "per_code": per_code,
        "note": (
            "suspect_missing_ex = 复牌日原始跌幅 <= drop_th 且该日无已登记除权事件。"
            "2005-2006 的股改对价送股属此类，ADJ 不会修正，会在序列里留下假跌幅。"
        ),
    }
    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"[gap] report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
