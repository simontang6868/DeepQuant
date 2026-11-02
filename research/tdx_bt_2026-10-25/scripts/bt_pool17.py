#!/usr/bin/env python
"""17 只高股息池：买入持有 / 择时 / 横截面低波选股 + 样本外切分。

数据：通达信后复权（tqFlag=2）。日线(period=4)重采样到月末；月线(period=6)直接用。
规则：T 月末收盘算信号 → 次月首个交易日开盘成交（禁前视）。
"""
from __future__ import annotations
import json, os, glob, math, statistics
from collections import defaultdict

TDIR = r"C:\Users\77436\.workbuddy\projects\d-DeepQuant power by txm-DeepQuant-main\33860a17-098b-4d49-954f-75fde862c20d\tool-results"

POST_TS = ("1792863156436", "1792863156286", "1792863171555", "1792863171557",
           "1792863171394", "1792897853406", "1792897853407", "1792898597381",
           "1792898597577", "1792898597573", "1792898597574", "1792898597576",
           "1792898597571", "1792898616464", "1792898615995", "1792898616176",
           "1792898616178", "1792898616465", "1792898616466", "1792898633790",
           "1792898633791", "1792898633514", "1792898633516", "1792898633793",
           "1792898633342")


def parse(p):
    t = open(p, encoding="utf-8").read()
    return json.JSONDecoder().raw_decode(t[t.find("{"):])[0]


def load_all():
    daily, monthly = defaultdict(dict), defaultdict(dict)
    for f in glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")):
        base = os.path.basename(f)
        if base.split("-")[-2] not in POST_TS:
            continue
        try:
            o = parse(f)
        except Exception:
            continue
        code, per, rows = o.get("Code"), o.get("Period"), o.get("Rows") or []
        if not code or not rows:
            continue
        tgt = daily if per == 4 else (monthly if per == 6 else None)
        if tgt is None:
            continue
        for r in rows:
            tgt[code][r["Data"]] = (float(r["Open"]), float(r["Close"]))
    return daily, monthly


def to_monthly(bars):
    """日线 → {月末交易日: (次月首日开盘, 本月末收盘)}"""
    by_month = defaultdict(list)
    for d, oc in sorted(bars.items()):
        by_month[d[:6]].append((d, oc))
    months = sorted(by_month)
    out = {}
    for i, m in enumerate(months):
        last_d, (_, last_c) = by_month[m][-1]
        nxt_open = by_month[months[i + 1]][0][1][0] if i + 1 < len(months) else last_c
        out[last_d] = (nxt_open, last_c)
    return out


def sma(v, k, i):
    return statistics.fmean(v[i + 1 - k:i + 1]) if i + 1 >= k else None


def metrics(eq):
    n = len(eq)
    if n < 3:
        return {}
    yrs = (n - 1) / 12
    cagr = (eq[-1] / eq[0]) ** (1 / yrs) - 1
    peak, mdd = eq[0], 0.0
    for x in eq:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    r = [eq[i] / eq[i - 1] - 1 for i in range(1, n)]
    return {"total": eq[-1] / eq[0] - 1, "cagr": cagr, "mdd": mdd,
            "vol": statistics.pstdev(r) * math.sqrt(12),
            "calmar": cagr / abs(mdd) if mdd else float("nan"),
            "months": n, "years": yrs}


def corr(a, b):
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da and db else float("nan")


def main():
    daily, monthly = load_all()
    series = {}
    for c, b in daily.items():
        series[c] = to_monthly(b)
    for c, b in monthly.items():
        if c not in series:
            series[c] = b
    codes = sorted(series)
    print(f"载入 {len(codes)} 只：日线源 {len(daily)} 只、月线源 {len(monthly)} 只")
    for c in codes:
        s = series[c]
        print(f"  {c}: {len(s):3d} 月  {min(s)} → {max(s)}")

    dates = sorted(set.intersection(*[set(s) for s in series.values()]))
    dates = [d for d in dates if d >= "20220421"]
    print(f"\n共同月末 {len(dates)} 个  {dates[0]} → {dates[-1]}")

    rets = {c: [series[c][dates[i]][1] / series[c][dates[i - 1]][1] - 1
                for i in range(1, len(dates))] for c in codes}
    ps = [corr(rets[a], rets[b]) for i, a in enumerate(codes) for b in codes[i + 1:]]
    rho = statistics.fmean(ps)
    N = len(codes)
    print(f"\nN_eff = {N / (1 + (N - 1) * rho):.2f}  (N={N}, ρ̄={rho:.4f})")

    closes = {c: [series[c][d][1] for d in dates] for c in codes}

    def run(rule, label, st=18):
        nav, eq, prev, trades = 1.0, [1.0], set(), 0
        for t in range(st, len(dates) - 1):
            on = rule(t)
            if on:
                r = statistics.fmean([series[c][dates[t + 1]][1] / series[c][dates[t]][1] - 1
                                      for c in on])
                nav *= (1 + r)
            if on != prev:
                trades += len(on ^ prev) if prev else len(on)
                nav *= (1 - 0.00076)
            prev = on
            eq.append(nav)
        m = metrics(eq)
        print(f"{label:26s} {m['total'] * 100:+8.2f}% {m['cagr'] * 100:+7.2f}% "
              f"{m['mdd'] * 100:+8.2f}% {m['calmar']:6.2f} {trades:7d}  {m['months']:3d}月")
        return m

    def buy_hold(st=0):
        base = {c: series[c][dates[st]][1] for c in codes}
        return [statistics.fmean([series[c][d][1] / base[c] for c in codes])
                for d in dates[st:]]

    print(f"\n{'策略':26s} {'累计':>9s} {'年化':>8s} {'回撤':>9s} {'Calmar':>6s} {'调仓':>6s}  评估")
    print("-" * 86)
    for st, lab in ((0, "S0b 真买入持有(全窗)"), (18, "S0b 真买入持有(同窗)")):
        m = metrics(buy_hold(st))
        print(f"{lab:26s} {m['total'] * 100:+8.2f}% {m['cagr'] * 100:+7.2f}% "
              f"{m['mdd'] * 100:+8.2f}% {m['calmar']:6.2f} {0:7d}  {m['months']:3d}月")

    run(lambda t: set(codes), "S1a 月度再平衡")
    run(lambda t: {c for c in codes
                   if sma(closes[c], 6, t) and sma(closes[c], 18, t)
                   and sma(closes[c], 6, t) > sma(closes[c], 18, t)},
        "S1b MA(6,18) 择时")

    def lowvol(t, keep=0.5):
        if t < 6:
            return set()
        v = {}
        for c in codes:
            r = [closes[c][i] / closes[c][i - 1] - 1 for i in range(t - 5, t + 1)]
            v[c] = statistics.pstdev(r)
        k = max(1, int(len(codes) * keep))
        return set(sorted(v, key=lambda c: v[c])[:k])

    run(lowvol, "S3 低波动(后50%)")

    def highvol(t, keep=0.5):
        if t < 6:
            return set()
        v = {}
        for c in codes:
            r = [closes[c][i] / closes[c][i - 1] - 1 for i in range(t - 5, t + 1)]
            v[c] = statistics.pstdev(r)
        k = max(1, int(len(codes) * keep))
        return set(sorted(v, key=lambda c: -v[c])[:k])

    run(highvol, "S3' 高波动(前50%，对照)")

    print("\n【样本外切分：2024-12 分界，真买入持有】")
    i_split = next(i for i, d in enumerate(dates) if d >= "20250101")
    for lab, (a, b) in {"样本内 2022-04~2024-12": (0, i_split),
                        "样本外 2025-01~2026-10": (i_split, len(dates))}.items():
        sub = dates[a:b]
        if len(sub) < 6:
            continue
        base = {c: series[c][sub[0]][1] for c in codes}
        eq = [statistics.fmean([series[c][d][1] / base[c] for c in codes]) for d in sub]
        m = metrics(eq)
        print(f"  {lab}: 累计 {m['total'] * 100:+7.2f}%  年化 {m['cagr'] * 100:+6.2f}%  n={len(sub)}")


if __name__ == "__main__":
    main()
