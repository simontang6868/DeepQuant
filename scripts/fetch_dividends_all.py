#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""为全 A（5,223 只）抓取分红送转事件：东财优先、东财为空则用新浪。

与 549 只研究池完全同一套口径（D-1 的教训）：**东财 `RPT_SHAREBONUS_DET` 对退市股返回空**，
所以必须有新浪兜底。两条源分开落盘，供 `rebuild_adj_from_exec.py --div-source merge` 使用。

用法：
  python scripts/fetch_dividends_all.py                      # 全 A（读 data/_all_a_manifest.json）
  python scripts/fetch_dividends_all.py --codes 000001.SZ    # 指定几只
  python scripts/fetch_dividends_all.py --only sina          # 只补新浪

产物：
  data/div_all_em/<code>.div.json     东财原始行（可能为空数组）
  data/div_all_sina/<code>.div.json   新浪解析结果（含 ex_date/div10/bonus10/transfer10）
  data/div_all_report.json            覆盖率统计
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rebuild_adj_from_exec import fetch_dividends, load_events_sina  # noqa: E402
from fetch_dividends_sina import fetch_page, parse_events  # noqa: E402

EM_DIR = "data/div_all_em"
SINA_DIR = "data/div_all_sina"


def load_codes(manifest: str, codes: str | None) -> list[str]:
    if codes:
        return [c.strip().upper() for c in codes.split(",") if c.strip()]
    with open(manifest, encoding="utf-8") as f:
        m = json.load(f)
    return sorted({s["code"] for s in m.get("symbols", []) if s.get("code")})


def work(code: str, args) -> dict:
    rec = {"code": code, "em": None, "sina": None, "err": None}
    if args.sleep:
        time.sleep(args.sleep)          # 限流：5223 只并发会把接口打挂（实测 58% 失败）
    if args.only in ("both", "em"):
        try:
            rows = fetch_dividends(code, args.em_dir, offline=False)
            rec["em"] = len(rows or [])
        except Exception as exc:  # noqa: BLE001
            rec["err"] = f"em:{type(exc).__name__}"
    if args.only in ("both", "sina"):
        try:
            ev = parse_events(fetch_page(code.split(".")[0]))
            rows = [{"ex_date": e["ex_date"], "div10": e["div10"], "bonus10": e["bonus10"],
                     "transfer10": e["transfer10"], "announce_date": e["announce_date"],
                     "progress": e["progress"]} for e in ev]
            path = os.path.join(args.sina_dir, f"{code}.div.json")
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
            os.replace(tmp, path)
            rec["sina"] = len(rows)
        except Exception as exc:  # noqa: BLE001
            rec["err"] = (rec["err"] or "") + f"|sina:{type(exc).__name__}"
    return rec


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="全 A 分红送转事件抓取（东财 + 新浪兜底）")
    ap.add_argument("--manifest", default="data/_all_a_manifest.json")
    ap.add_argument("--codes", default=None)
    ap.add_argument("--em-dir", default=EM_DIR)
    ap.add_argument("--sina-dir", default=SINA_DIR)
    ap.add_argument("--only", choices=["both", "em", "sina"], default="both")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--sleep", type=float, default=0.0, help="每只之间的间隔（秒），防限流")
    ap.add_argument("--report", default="data/div_all_report.json")
    args = ap.parse_args(argv)

    os.makedirs(args.em_dir, exist_ok=True)
    os.makedirs(args.sina_dir, exist_ok=True)
    codes = load_codes(args.manifest, args.codes)
    print(f"[div-all] {len(codes)} 只 | only={args.only} | workers={args.workers}")

    results = []
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futs = [pool.submit(work, c, args) for c in codes]
        for i, fut in enumerate(as_completed(futs), 1):
            results.append(fut.result())
            if i % 500 == 0:
                print(f"  ... {i}/{len(codes)}  ({time.time()-t0:.0f}s)")
    results.sort(key=lambda r: r["code"])

    n_em = sum(1 for r in results if r["em"])
    n_sina = sum(1 for r in results if r["sina"])
    n_none = sum(1 for r in results if not r["em"] and not r["sina"])
    n_err = sum(1 for r in results if r["err"])
    print(f"[div-all] 完成 {len(results)} 只 | 东财有事件 {n_em} | 新浪有事件 {n_sina} | "
          f"两者皆空 {n_none} | 出错 {n_err} | {time.time()-t0:.0f}s")

    out = {"n": len(results), "n_em": n_em, "n_sina": n_sina, "n_none": n_none, "n_err": n_err,
           "elapsed_sec": round(time.time() - t0),
           "empty_both": [r["code"] for r in results if not r["em"] and not r["sina"]],
           "errors": [r for r in results if r["err"]][:50]}
    with open(args.report, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"[div-all] report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
