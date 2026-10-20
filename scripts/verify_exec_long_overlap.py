#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""交叉验证：长区间 EXEC（2005-2026）与既有短区间 EXEC（2016-2026）在重叠段是否逐字节一致。

为什么必须做这件事（方法论要求，不是洁癖）：
  回补 2005-2015 的数据是**新抓的一批**。如果新抓的批次在**已知区间**上与旧批次不一致，
  说明抓取口径变了（列序、复权、分页、接口切换任意一环），那么新增的 2005-2015 段同样不可信。
  在**重叠区间**上做逐行比对，是唯一能用既有（已验证过的）数据去校准新批次的办法。

判据（机械可执行）：
  V1 重叠日期集合完全相同（新批次不多也不少）
  V2 每个重叠日的 open/high/low/close/volume 全部相同（容差 1e-9，理论上应完全相等）
  V3 反向：旧批次有的日期，新批次必须有（退市股止于退市日，两侧都止于同一天）

用法：
  python scripts/verify_exec_long_overlap.py
  python scripts/verify_exec_long_overlap.py --short-dir data/exec --long-dir data/exec_long
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import sys

FIELDS = ["date", "open", "high", "low", "close", "volume"]


def read_csv(path: str) -> dict[str, tuple]:
    with open(path, encoding="utf-8") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        return {}
    hdr = [h.strip().lower() for h in rows[0]]
    idx = {h: hdr.index(h) for h in FIELDS if h in hdr}
    out: dict[str, tuple] = {}
    for r in rows[1:]:
        if not r:
            continue
        d = r[idx["date"]]
        out[d] = tuple(float(r[idx[k]]) if r[idx[k]] not in ("", None) else float("nan") for k in FIELDS[1:])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="长区间 EXEC 与既有 EXEC 的重叠段一致性交叉验证")
    ap.add_argument("--short-dir", default="data/exec")
    ap.add_argument("--long-dir", default="data/exec_long")
    ap.add_argument("--overlap-start", default="2016-01-04", help="重叠段起点（旧批次的首个交易日）")
    ap.add_argument("--tol", type=float, default=1e-9)
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    codes = sorted(
        os.path.basename(p)[: -len(".exec.csv")]
        for p in os.listdir(args.short_dir)
        if p.endswith(".exec.csv")
    )
    if not codes:
        print("短区间目录为空", file=sys.stderr)
        return 2

    results = []
    for code in codes:
        sp = os.path.join(args.short_dir, f"{code}.exec.csv")
        lp = os.path.join(args.long_dir, f"{code}.exec.csv")
        rec: dict = {"code": code}
        if not os.path.exists(lp):
            rec.update(ok=False, error="missing_in_long")
            results.append(rec)
            continue
        short = {d: v for d, v in read_csv(sp).items() if d >= args.overlap_start}
        long_ = {d: v for d, v in read_csv(lp).items() if d >= args.overlap_start}
        if not short:
            rec.update(ok=False, error="short_empty")
            results.append(rec)
            continue
        miss = sorted(set(short) - set(long_))
        extra = sorted(set(long_) - set(short))
        diffs = []
        for d in sorted(set(short) & set(long_)):
            a, b = short[d], long_[d]
            if any(abs(x - y) > args.tol for x, y in zip(a, b)):
                diffs.append({"date": d, "short": a, "long": b})
        rec.update(
            ok=not (miss or extra or diffs),
            n_overlap=len(set(short) & set(long_)),
            missing_dates=miss[:10],
            extra_dates=extra[:10],
            n_missing=len(miss),
            n_extra=len(extra),
            n_value_diff=len(diffs),
            value_diff_sample=diffs[:5],
        )
        results.append(rec)

    bad = [r for r in results if not r.get("ok")]
    report = {
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "short_dir": args.short_dir,
        "long_dir": args.long_dir,
        "overlap_start": args.overlap_start,
        "tol": args.tol,
        "n_codes": len(results),
        "n_ok": len(results) - len(bad),
        "n_bad": len(bad),
        "bad": bad,
        "results": results,
    }
    path = args.report or os.path.join(args.long_dir, "_overlap_check.json")
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    print(f"[overlap] {len(results)} 只 | 一致 {report['n_ok']} | 不一致 {report['n_bad']}")
    for r in bad:
        print(
            f"  [BAD] {r['code']:<12} {r.get('error','')} "
            f"n_overlap={r.get('n_overlap','-')} miss={r.get('n_missing','-')} "
            f"extra={r.get('n_extra','-')} valuediff={r.get('n_value_diff','-')}"
        )
    print(f"[overlap] report -> {path}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
