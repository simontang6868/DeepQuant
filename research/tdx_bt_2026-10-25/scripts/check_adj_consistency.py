#!/usr/bin/env python
"""核对：交接 CSV 的前复权 与 通达信自带前复权 是否一致。

若两者差异显著 → 交接数据集的复权口径有问题，其基线（B1 +115%）不可直接引用。
"""
from __future__ import annotations
import json, os, glob, csv
from collections import defaultdict

TDIR = r"C:\Users\77436\.workbuddy\projects\d-DeepQuant power by txm-DeepQuant-main\33860a17-098b-4d49-954f-75fde862c20d\tool-results"


def parse(p):
    t = open(p, encoding="utf-8").read()
    return json.JSONDecoder().raw_decode(t[t.find("{"):])[0]


def scan(code, per):
    """返回该 code/period 的所有候选文件（不同复权）。"""
    out = []
    for f in glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")):
        try:
            o = parse(f)
        except Exception:
            continue
        if o.get("Code") == code and o.get("Period") == per:
            rows = o["Rows"]
            out.append({r["Data"]: float(r["Close"]) for r in rows})
    return out


def main():
    cands = scan("601398", 6)
    print(f"601398 月线候选 {len(cands)} 份（前复权/后复权各一）")
    raw_m = {}
    raw = {}
    for fn in ("mcp-tdx-connector-tdx_kline-1792863978914-624545.txt",
               "mcp-tdx-connector-tdx_kline-1792863993823-de395a.txt"):
        for r in parse(os.path.join(TDIR, fn))["Rows"]:
            raw[r["Data"]] = float(r["Close"])
    bm = defaultdict(list)
    for d in sorted(raw):
        bm[d[:6]].append(d)
    for m in bm:
        raw_m[bm[m][-1]] = raw[bm[m][-1]]

    csv_q = {}
    with open(r"D:/workbuddy de gongzuo/2026-10-25-00-25-03/cycle_dividend_bt/data/601398.csv",
              encoding="utf-8") as f:
        for r in csv.DictReader(f):
            csv_q[r["date"]] = float(r["close"])

    # 前复权 = 末月收盘 应为 8.32（与不复权一致）；后复权 = 末月 13.54
    qfq = None
    for c in cands:
        last = max(c)
        if abs(c[last] - 8.32) < 0.05:
            qfq = c
    if qfq is None:
        print("未识别出前复权序列，候选末日收盘：", [(max(c), c[max(c)]) for c in cands])
        return

    print(f"\n{'月末':>9s} {'通达信前复权':>12s} {'交接CSV':>9s} {'不复权':>8s} "
          f"{'TDX/raw':>9s} {'CSV/raw':>9s} {'差异':>8s}")
    prev = {}
    for d in sorted(raw_m):
        if d < "20220101":
            continue
        a = qfq.get(d)
        c = csv_q.get(d)
        r = raw_m[d]
        if a is None or c is None:
            continue
        print(f"{d:>9s} {a:12.3f} {c:9.3f} {r:8.3f} {a/r:9.4f} {c/r:9.4f} "
              f"{(c/a-1)*100:+7.2f}%")

    # 逐年累计分红因子（通达信 vs CSV）
    print("\n【逐年分红因子 增长率（应为该年股息率量级 ~5%）】")
    print(f"  {'年':>4s} {'通达信F':>9s} {'增速':>8s} {'CSV F':>9s} {'增速':>8s}")
    pt = pc = None
    for y in range(2022, 2027):
        ds = [d for d in sorted(raw_m) if d.startswith(str(y))]
        if not ds:
            continue
        d = ds[-1]
        a, c, r = qfq.get(d), csv_q.get(d), raw_m[d]
        if not a or not c:
            continue
        Ft, Fc = a / r, c / r
        st = f"{(Ft/pt-1)*100:+7.2f}%" if pt else "     —"
        sc = f"{(Fc/pc-1)*100:+7.2f}%" if pc else "     —"
        print(f"  {y:>4d} {Ft:9.4f} {st} {Fc:9.4f} {sc}")
        pt, pc = Ft, Fc


if __name__ == "__main__":
    main()
