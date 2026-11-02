# -*- coding: utf-8 -*-
"""月线覆盖度并集盘点：100 只成分股，月线 period=6 是否覆盖到 2022-01 之前。

与 kline_coverage.py 同法（多页取并集），但只看月线。
输出 pool/monthly_coverage.json
"""
import json, os, glob, csv

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main\research\tdx_bt_2026-10-25"
TDIR = r"C:\Users\77436\.workbuddy\projects\d-DeepQuant power by txm-DeepQuant-main\33860a17-098b-4d49-954f-75fde862c20d\tool-results"
NEED_START = "202201"          # MA12 在 2023-01 需要 2022-02 之前，留余量
NEED_END = "202609"            # 至少要到 2026-09（2026-10 为未完成月）

comps = []
with open(os.path.join(ROOT, "pool", "000922_components.csv"), encoding="utf-8-sig") as f:
    for r in csv.DictReader(f):
        comps.append(r)
codes = [c["code"] for c in comps]
print("成分股数:", len(codes))

pages = {}
for p in glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")):
    try:
        with open(p, encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        continue
    if not isinstance(d, dict) or "Rows" not in d:
        continue
    if str(d.get("Period")) not in ("6", "6.0"):
        continue
    code = str(d.get("Code", "")).strip()
    rows = d["Rows"] or []
    if not rows:
        continue
    ds = [str(r.get("Data", "")) for r in rows if r.get("Data")]
    if not ds:
        continue
    pages.setdefault(code, []).append((min(ds), max(ds), len(rows)))

ok, short, none, detail = [], [], [], {}
for c in codes:
    pg = pages.get(c)
    if not pg:
        none.append(c)
        continue
    s = min(x[0] for x in pg)
    e = max(x[1] for x in pg)
    n = sum(x[2] for x in pg)          # 多页有重叠时略高估，仅参考
    detail[c] = {"start": s, "end": e, "rows": n, "pages": len(pg)}
    if s[:6] <= NEED_START and e[:6] >= NEED_END:
        ok.append(c)
    else:
        short.append(c)

print(f"月线 ok={len(ok)}  short={len(short)}  none={len(none)}")
print("SHORT:", " ".join(short))
print("NONE :", " ".join(none))
out = {"need_start": NEED_START, "need_end": NEED_END,
       "ok": ok, "short": short, "none": none, "detail": detail}
with open(os.path.join(ROOT, "pool", "monthly_coverage.json"), "w", encoding="utf-8") as f:
    json.dump(out, f, ensure_ascii=False, indent=1)
print("saved pool/monthly_coverage.json")
