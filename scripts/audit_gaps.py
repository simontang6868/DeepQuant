#!/usr/bin/env python
"""长空洞归因：区分"真实长期停牌"与"抓取漏段"（阶段 B3）。

为什么需要
----------
`exec_all` 里 1,111 只标的存在 >120 自然日的空洞（占 20%）。两种可能：
  (a) 真实长期停牌（万科 2015-12-18→2016-07-04 的 199 天就是宝万之争）；
  (b) 分页抓取被截断（002916.SZ 曾少 861 个交易日）。
**二者在 CSV 里长得一模一样**，只能靠"往缺口里再请求一次"来区分：
  - 缺口内返回数据 → 抓取漏段（严重，必须重抓）
  - 缺口内无数据   → 真实停牌（可接受，但要在池规则里当作不可交易）

判据与 `fetch_exec_prices.probe_gap` 相同，但此处独立实现并**批量抽样**，
因为它要回答的是"整个数据集有没有系统性漏段"，不是单只。

用法
----
    python scripts/audit_gaps.py                       # 抽样 40 只
    python scripts/audit_gaps.py --sample 200          # 抽更多
    python scripts/audit_gaps.py --min-days 120 --report reports/GAP_AUDIT.json
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import sys
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/kline/kline"


def symbol_of(code: str) -> str:
    c, mk = code.split(".")
    return ("sh" if mk.upper() == "SH" else "sz") + c


def probe(sym: str, start: str, end: str) -> tuple[str, int]:
    """在 [start, end] 区间请求一次。返回 (状态, 行数)。"""
    url = f"{KLINE_URL}?param={sym},day,{start},{end},640"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        raw = urllib.request.urlopen(req, timeout=25).read().decode("utf-8", "ignore")
        d = json.loads(raw)
    except Exception as exc:  # noqa: BLE001
        return f"ERR:{type(exc).__name__}", 0
    batch = ((d.get("data") or {}).get(sym) or {}).get("day") or []
    return "OK", len(batch)


def read_dates(path: str) -> list[str]:
    import csv
    out = []
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.reader(f)
        header = next(rd, None) or []
        try:
            di = [h.strip().lower() for h in header].index("date")
        except ValueError:
            di = 0
        for row in rd:
            if len(row) > di and row[di]:
                out.append(row[di][:10])
    out.sort()
    return out


def find_gaps(dates: list[str], min_days: int) -> list[tuple[str, str, int]]:
    import datetime as dt
    out = []
    for a, b in zip(dates, dates[1:]):
        try:
            gap = (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days
        except ValueError:
            continue
        if gap >= min_days:
            out.append((a, b, gap))
    out.sort(key=lambda x: -x[2])
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exec-dir", default="data/exec_all")
    ap.add_argument("--min-days", type=int, default=120)
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--seed", type=int, default=20261021)
    ap.add_argument("--report", default="reports/GAP_AUDIT.json")
    args = ap.parse_args(argv)

    # 1) 全量统计空洞分布（不联网）
    rows = []
    for p in sorted(glob.glob(os.path.join(args.exec_dir, "*.exec.csv"))):
        code = os.path.basename(p).replace(".exec.csv", "")
        dates = read_dates(p)
        if len(dates) < 100:
            continue
        g = find_gaps(dates, args.min_days)
        if g:
            rows.append({"code": code, "n_gaps": len(g), "max_gap": g[0][2],
                         "worst": g[0], "all": g[:5]})
    rows.sort(key=lambda r: -r["max_gap"])
    print(f"[统计] {len(rows)} 只存在 >={args.min_days} 天的空洞")
    if rows:
        gaps = [r["max_gap"] for r in rows]
        print(f"[统计] 最大空洞 {max(gaps)} 天 | 中位 {sorted(gaps)[len(gaps)//2]} 天")

    # 2) 抽样做缺口探针
    rng = random.Random(args.seed)
    sample = rng.sample(rows, min(args.sample, len(rows))) if rows else []
    print(f"[探针] 抽样 {len(sample)} 只，往空洞里再请求一次……")
    leak, suspend, err = [], 0, []
    for r in sample:
        a, b, days = r["worst"]
        # 在空洞内取一个中点日期，只请求 [a+1, b-1]
        import datetime as dt
        try:
            d1 = (dt.date.fromisoformat(a) + dt.timedelta(days=1)).isoformat()
            d2 = (dt.date.fromisoformat(b) - dt.timedelta(days=1)).isoformat()
        except ValueError:
            continue
        if d1 > d2:
            suspend += 1
            continue
        st, n = probe(symbol_of(r["code"]), d1, d2)
        if st != "OK":
            err.append({"code": r["code"], "err": st, "gap": r["worst"]})
        elif n > 0:
            leak.append({"code": r["code"], "gap": r["worst"], "found_rows": n,
                         "probe_window": [d1, d2]})
        else:
            suspend += 1
    print(f"[探针] 真实停牌 {suspend} | **抓取漏段 {len(leak)}** | 探针失败 {len(err)}")
    for x in leak:
        print(f"  ⚠️ 漏段 {x['code']} 空洞 {x['gap']} → 缺口内竟有 {x['found_rows']} 行")

    verdict = {
        "n_symbols_with_gap": len(rows),
        "min_gap_days": args.min_days,
        "sampled": len(sample),
        "confirmed_suspension": suspend,
        "confirmed_fetch_leak": len(leak),
        "probe_error": len(err),
        "leak_detail": leak,
        "error_detail": err[:10],
        "top_gaps": rows[:20],
        "conclusion": (
            "未发现系统性抓取漏段" if not leak
            else f"发现 {len(leak)} 处抓取漏段，必须重抓后再用于实验"),
    }
    if os.path.dirname(args.report):
        os.makedirs(os.path.dirname(args.report), exist_ok=True)
    json.dump(verdict, open(args.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[结论] {verdict['conclusion']}")
    print(f"report -> {args.report}")
    return 0 if not leak else 1


if __name__ == "__main__":
    raise SystemExit(main())
