#!/usr/bin/env python
"""盘点 tool-results 里所有 tdx_kline 落盘文件：代码 / 周期 / 根数 / 起止日期。

产出 pool/kline_manifest.json，供后续决定"还缺谁、要不要重拉"。
"""
from __future__ import annotations
import csv
import glob
import json
import os
from collections import defaultdict

TDIR = (r"C:\Users\77436\.workbuddy\projects\d-DeepQuant power by txm-DeepQuant-main"
        r"\33860a17-098b-4d49-954f-75fde862c20d\tool-results")
OUT = os.path.join(os.path.dirname(__file__), "..", "pool")
POOL = os.path.join(OUT, "000922_components.csv")


def parse(p: str):
    t = open(p, encoding="utf-8", errors="replace").read()
    i = t.find("{")
    if i < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(t[i:])[0]
    except Exception:
        return None


def main() -> None:
    comp = list(csv.DictReader(open(POOL, encoding="utf-8")))
    want = {r["code"] for r in comp}
    print(f"成分股 {len(comp)} 只")

    # code -> period -> list of (nrows, first, last, path, nbytes)
    found = defaultdict(lambda: defaultdict(list))
    files = sorted(glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")))
    print(f"扫描 kline 落盘文件 {len(files)} 个")
    bad = 0
    for p in files:
        d = parse(p)
        if not d or "Rows" not in d:
            bad += 1
            continue
        code = str(d.get("Code", ""))
        per = d.get("Period")
        rows = d["Rows"]
        if not rows:
            continue
        ds = [r.get("Data", "") for r in rows]
        found[code][per].append(
            (len(rows), min(ds), max(ds), os.path.basename(p), os.path.getsize(p))
        )
    print(f"解析失败 {bad} 个")

    manifest = {}
    for code, permap in found.items():
        e = {}
        for per, lst in permap.items():
            lst.sort(key=lambda x: -x[0])
            n, d0, d1, fn, nb = lst[0]
            e[str(per)] = {"n": n, "start": d0, "end": d1, "file": fn, "bytes": nb,
                           "dups": len(lst)}
        manifest[code] = e

    # 需要什么：日线(4) 覆盖到 2022-02 之前 且 末端最新
    NEED_START = "20220201"
    have_long_daily, have_short_daily, have_monthly, missing = [], [], [], []
    for code in sorted(want):
        e = manifest.get(code)
        if not e:
            missing.append(code)
            continue
        d = e.get("4")
        m = e.get("6")
        if d and d["start"] <= NEED_START:
            have_long_daily.append(code)
        elif d:
            have_short_daily.append(code)
        if m:
            have_monthly.append(code)
        if not d and not m:
            missing.append(code)

    print(f"\n日线已够长(≤{NEED_START}起) : {len(have_long_daily)}")
    print(f"日线偏短(需重拉)        : {len(have_short_daily)}  {have_short_daily}")
    print(f"有月线(period=6)        : {len(have_monthly)}  {sorted(have_monthly)}")
    print(f"完全缺失                : {len(missing)}")
    print("缺失清单:", " ".join(missing))

    json.dump({"manifest": manifest,
               "long_daily": sorted(have_long_daily),
               "short_daily": sorted(have_short_daily),
               "monthly": sorted(have_monthly),
               "missing": sorted(missing)},
              open(os.path.join(OUT, "kline_manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("→ pool/kline_manifest.json")


if __name__ == "__main__":
    main()
