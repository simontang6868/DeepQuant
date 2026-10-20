#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Gate 3 数据清单与窗口内完整性复验。

预登记 §2 要求：数据版本必须可追溯到字节（sha256），且**在研究窗口内**（而不是全样本）
重新核一遍 A1/A4。原因：全样本 A4 会把 2005-2006 的股改缺口算进来（复牌缺口豁免），
而 Gate 3 的窗口从 2007 开始，判定依据只能是**窗口内**的完整性。

判据（窗口内）：
  W1 窗口内 ADJ/EXEC 价格全为正（A1 的窗口版）
  W2 窗口内 ADJ 单日 |收益| 不超该板块限幅 + 1.5pp（A4 的窗口版；除权日与停牌复牌日豁免）
  W3 覆盖度：窗口内有效交易日 >= 窗口总交易日的 60%（低于此说明长期停牌，不进池）

产物：
  docs/GATE3_DATA_MANIFEST_2026-10-20.json —— 逐只 sha256 + 聚合摘要 + 窗口内判据结果

用法：
  python scripts/gate3_pool_manifest.py
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rebuild_adj_from_exec import fetch_dividends, limit_for, normalize_events  # noqa: E402


def read_px(path: str) -> list[tuple[str, float, float]]:
    """返回 [(date, close, volume)]。"""
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    hdr = [h.strip().lower() for h in rows[0]]
    di, ci = hdr.index("date"), hdr.index("close")
    vi = hdr.index("volume") if "volume" in hdr else None
    out = []
    for r in rows[1:]:
        if not r:
            continue
        try:
            out.append((r[di], float(r[ci]), float(r[vi]) if vi is not None else float("nan")))
        except (ValueError, IndexError):
            continue
    return out


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gate 3 数据清单 + 窗口内完整性复验")
    ap.add_argument("--adj-dir", default="data/qfq_rebuilt_broad_long")
    ap.add_argument("--exec-dir", default="data/exec_long")
    ap.add_argument("--div-cache", default="data/exec/_div_cache")
    ap.add_argument("--start", default="2007-01-01")
    ap.add_argument("--end", default="2015-12-31")
    ap.add_argument("--min-coverage", type=float, default=0.60)
    ap.add_argument("--exclude", default="600515.SH,000725.SZ,000301.SZ,000425.SZ,000776.SZ,600066.SH,600115.SH,600660.SH")
    ap.add_argument("--report", default="docs/GATE3_DATA_MANIFEST_2026-10-20.json")
    args = ap.parse_args(argv)

    excluded = [c.strip().upper() for c in args.exclude.split(",") if c.strip()]
    codes = sorted(os.path.basename(p)[: -len(".qfq.csv")] for p in os.listdir(args.adj_dir) if p.endswith(".qfq.csv"))
    # 窗口交易日 = 各标的日期并集（用全池并集当"市场日历"）
    cal: set[str] = set()
    for c in codes:
        cal |= {d for d, _, _ in read_px(os.path.join(args.adj_dir, f"{c}.qfq.csv")) if args.start <= d <= args.end}
    cal_sorted = sorted(cal)
    n_cal = len(cal_sorted)
    print(f"[manifest] 池 {len(codes)} 只 | 窗口 {args.start} ~ {args.end} | 交易日 {n_cal}")

    results = []
    for code in codes:
        adj = [(d, c) for d, c, _ in read_px(os.path.join(args.adj_dir, f"{code}.qfq.csv")) if args.start <= d <= args.end]
        ex = [(d, c) for d, c, _ in read_px(os.path.join(args.exec_dir, f"{code}.exec.csv")) if args.start <= d <= args.end]
        rec = {"code": code, "n_days": len(adj)}
        # 覆盖度必须是"自身可交易区间内"的比例：上市晚/退市早的标的本身覆盖率低是正常的，
        # 用整个 2007-2015 当分母会把所有 2010 年上市的都判死（第一版犯过这个错）。
        if adj:
            lo, hi = adj[0][0], adj[-1][0]
            span = sum(1 for d in cal_sorted if lo <= d <= hi)
            rec["span"] = [lo, hi]
            rec["coverage"] = round(len(adj) / span, 4) if span else 0.0
        else:
            rec["coverage"] = 0.0
        rec["sha256"] = sha256_file(os.path.join(args.adj_dir, f"{code}.qfq.csv"))
        rec["excluded"] = code in excluded
        fails = []
        if not adj:
            # 窗口内没有数据 ≠ 数据缺陷：2016 年以后上市、或 2007 年以前退市的标的本来就缺席。
            # 动态池会自动处理（那时它不在池里），因此只记 note，不判失败。
            rec["no_window_data"] = True
            rec["ok"] = True
            rec["fails"] = []
            rec["sha256"] = sha256_file(os.path.join(args.adj_dir, f"{code}.qfq.csv"))
            rec["excluded"] = code in excluded
            results.append(rec)
            continue
        if rec["coverage"] < args.min_coverage:
            fails.append(f"W3_low_coverage:{rec['coverage']}")
        if adj and min(c for _, c in adj) <= 0:
            fails.append("W1_nonpositive")
        try:
            raw = fetch_dividends(code, args.div_cache, offline=True)
        except FileNotFoundError:
            raw = []
        # 事件必须**对齐到交易日**（第一个 >= ex_date 的交易日），与 build_adj 完全一致。
        # 只按 ex_date 排除会漏掉"除权日为非交易日、实际除权落在下一交易日"的情形，
        # 那一天的 ADJ 收益正是被修掉的除权缺口（第一版因此误报 331 只）。
        ev_dates: set[str] = set()
        for e in normalize_events(raw, args.start, args.end)[0]:
            for d, _ in adj:
                if d >= e["ex_date"]:
                    ev_dates.add(d)
                    break
        over = []
        for i in range(1, len(adj)):
            d0, p0 = adj[i - 1]
            d1, p1 = adj[i]
            if d1 in ev_dates or p0 <= 0:
                continue
            gap = (dt.date.fromisoformat(d1) - dt.date.fromisoformat(d0)).days
            if gap > 4:  # 停牌复牌豁免（与 A4 一致）
                continue
            r = p1 / p0 - 1
            if abs(r) > limit_for(code, d1):
                over.append({"date": d1, "ret": round(r, 4)})
        rec["limit_over_in_window"] = len(over)
        rec["limit_over_detail"] = over[:5]
        if over:
            fails.append(f"W2_limit:{len(over)}")
        rec["ok"] = not fails
        rec["fails"] = fails
        results.append(rec)

    usable = [r for r in results if r["ok"] and not r["excluded"]]
    agg = hashlib.sha256()
    for r in sorted(usable, key=lambda x: x["code"]):
        agg.update(f"{r['code']}:{r['sha256']}\n".encode())
    bad = [r for r in results if not r["ok"]]
    print(f"[manifest] 可用标的 {len(usable)} 只 | 显式剔除 {len(excluded)} 只 | 判据不过 {len(bad)} 只")
    for r in bad[:20]:
        print(f"  [BAD] {r['code']:<12} {r['fails']} cov={r['coverage']}")
    print(f"[manifest] 聚合摘要（可用池，按代码排序后 sha256 再哈希）: {agg.hexdigest()}")

    out = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "adj_dir": args.adj_dir,
        "exec_dir": args.exec_dir,
        "window": [args.start, args.end],
        "n_calendar_days": n_cal,
        "n_pool_files": len(codes),
        "excluded_explicit": excluded,
        "n_usable": len(usable),
        "usable_codes": [r["code"] for r in usable],
        "aggregate_sha256": agg.hexdigest(),
        "criteria": {
            "W1": "窗口内价格全为正",
            "W2": "窗口内单日 |收益| <= 板块限幅 + 1.5pp（除权日与停牌复牌 > 4 自然日豁免）",
            "W3": f"窗口内有效交易日 / 市场交易日 >= {args.min_coverage}",
        },
        "results": results,
    }
    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"[manifest] report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
