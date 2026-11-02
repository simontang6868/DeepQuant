#!/usr/bin/env python
"""按「页并集」重算日线覆盖度，输出还缺谁。

关键：同一只票可能有多个 startxh 页（page0 近700根 + page1 前700根），
kline_inventory.py 只取根数最多的一页，会误判。这里取并集。
"""
from __future__ import annotations
import csv
import glob
import json
import os

TDIR = (r"C:\Users\77436\.workbuddy\projects\d-DeepQuant power by txm-DeepQuant-main"
        r"\33860a17-098b-4d49-954f-75fde862c20d\tool-results")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", "pool")
POOL = os.path.join(OUT, "000922_components.csv")

NEED_START = "20220201"   # MA12 在 2023-01 需要 2022-02 起的 12 个月末


def parse(p):
    t = open(p, encoding="utf-8", errors="replace").read()
    i = t.find("{")
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(t[i:])[0]
    except Exception:
        return None


def main():
    comp = list(csv.DictReader(open(POOL, encoding="utf-8")))
    want = sorted({r["code"] for r in comp})
    print(f"成分股 {len(want)} 只")

    # code -> list of (start, end, n)
    pages = {}
    files = sorted(glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")))
    for p in files:
        d = parse(p)
        if not d or "Rows" not in d:
            continue
        if str(d.get("Period")) not in ("4", "4.0"):
            continue
        code = str(d.get("Code", ""))
        rows = d["Rows"]
        if not rows:
            continue
        ds = [r.get("Data", "") for r in rows]
        pages.setdefault(code, []).append((min(ds), max(ds), len(rows)))

    ok, short, none = [], [], []
    detail = {}
    for code in want:
        pg = pages.get(code)
        if not pg:
            none.append(code)
            detail[code] = {"pages": 0}
            continue
        s = min(x[0] for x in pg)
        e = max(x[1] for x in pg)
        detail[code] = {"pages": len(pg), "start": s, "end": e,
                        "n": sum(x[2] for x in pg)}
        if s <= NEED_START:
            ok.append(code)
        else:
            short.append(code)

    print(f"日线覆盖到 {NEED_START} 之前 : {len(ok)}")
    print(f"仍偏短(需补 startxh=700)   : {len(short)}  {short}")
    print(f"完全没有日线               : {len(none)}  {none}")
    print(f"\n合计缺 {len(short) + len(none)} 只")

    json.dump({"ok": ok, "short": short, "none": none, "detail": detail},
              open(os.path.join(OUT, "kline_coverage.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("→ pool/kline_coverage.json")


if __name__ == "__main__":
    main()
