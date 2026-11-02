#!/usr/bin/env python
"""长窗口检验：11 只高股息股，2007→2026（约 19 年），后复权月线 + 逐仓红利税。

为什么做这个：4.5 年窗口只有 36 个可评估月，样本外 21 个月，功效极低。
本项目此前已反复证明"短样本什么都证不了"。这里把窗口拉到 19 年（含 2008/2015/2018/2022 四轮下跌）。
"""
from __future__ import annotations
import json, os, glob, math, statistics
from collections import defaultdict

TDIR = r"C:\Users\77436\.workbuddy\projects\d-DeepQuant power by txm-DeepQuant-main\33860a17-098b-4d49-954f-75fde862c20d\tool-results"
# 后复权( tqFlag=2 ) 月线批次
POST_TS = {"1792863156436", "1792863156286", "1792863171555", "1792863171557",
           "1792863171394", "1792897853406", "1792897853407",
           "1792904303856", "1792904303857"}
NAMES = {"000983": "山西焦煤", "600177": "雅戈尔", "600188": "兖矿能源", "600256": "广汇能源",
         "600295": "鄂尔多斯", "600583": "海油工程", "601088": "中国神华", "601225": "陕西煤业",
         "601398": "工商银行", "601666": "平煤股份", "601668": "中国建筑", "601699": "潞安环能",
         "601808": "中海油服", "601857": "中国石油", "601919": "中远海控"}
YIELD, COST = 0.050, 0.00076


def parse(p):
    t = open(p, encoding="utf-8").read()
    return json.JSONDecoder().raw_decode(t[t.find("{"):])[0]


def load():
    out = {}
    for f in glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")):
        if os.path.basename(f).split("-")[-2] not in POST_TS:
            continue
        try:
            o = parse(f)
        except Exception:
            continue
        if o.get("Period") != 6:
            continue
        code, rows = o.get("Code"), o.get("Rows") or []
        if not rows or code in out or code not in NAMES:
            continue
        out[code] = {r["Data"]: (float(r["Open"]), float(r["Close"])) for r in rows}
    return out


def sma(v, k, i):
    return statistics.fmean(v[i + 1 - k:i + 1]) if i + 1 >= k else None


def met(eq):
    n = len(eq)
    yrs = (n - 1) / 12
    cagr = (eq[-1] / eq[0]) ** (1 / yrs) - 1
    peak, mdd = eq[0], 0.0
    for x in eq:
        peak = max(peak, x)
        mdd = min(mdd, x / peak - 1)
    r = [eq[i] / eq[i - 1] - 1 for i in range(1, n)]
    return {"total": eq[-1] / eq[0] - 1, "cagr": cagr, "mdd": mdd,
            "vol": statistics.pstdev(r) * math.sqrt(12),
            "calmar": cagr / abs(mdd) if mdd else float("nan"), "n": n, "yrs": yrs}


def tax_rate(m):
    return 0.20 if m <= 1 else (0.10 if m <= 12 else 0.0)


def engine(series, codes, dates, rule, a, b, with_tax=True, yld=YIELD):
    nav, eq, prev, since, trades, hold = 1.0, [1.0], set(), {}, 0, []
    for t in range(a, b):
        on = rule(t)
        if on != prev:
            trades += len(on ^ prev) if prev else len(on)
            nav *= (1 - COST)
        for c in list(since):
            if c not in on:
                hold.append(t - since.pop(c))
        for c in on:
            since.setdefault(c, t)
        if on:
            r = statistics.fmean([series[c][dates[t + 1]][1] / series[c][dates[t]][1] - 1
                                  for c in on])
            nav *= (1 + r)
            if with_tax:
                w = 1.0 / len(on)
                drag = sum(w * yld / 12 * tax_rate(t - since[c]) for c in on)
                nav *= (1 - drag)
        prev = on
        eq.append(nav)
    if since:
        hold += [b - since[c] for c in since]
    return eq, trades, (statistics.fmean(hold) if hold else float("inf"))


def main():
    S = load()
    codes = sorted(S)
    dates = sorted(set.intersection(*[set(S[c]) for c in codes]))
    print(f"载入 {len(codes)} 只后复权月线：" + "、".join(NAMES[c] for c in codes))
    print(f"共同月末 {len(dates)} 个  {dates[0]} → {dates[-1]}  "
          f"（{(len(dates)-1)/12:.1f} 年）\n")

    CL = {c: [S[c][d][1] for d in dates] for c in codes}

    def rules():
        r = {"S0b 买入持有": lambda t: set(codes)}
        r["S1 MA(6,18)"] = lambda t: {
            c for c in codes if sma(CL[c], 6, t) and sma(CL[c], 18, t)
            and sma(CL[c], 6, t) > sma(CL[c], 18, t)}

        def lv(t):
            if t < 6:
                return set(codes)
            v = {c: statistics.pstdev([CL[c][i] / CL[c][i - 1] - 1
                                       for i in range(t - 5, t + 1)]) for c in codes}
            k = max(1, round(len(codes) * 0.5))
            return set(sorted(v, key=lambda c: v[c])[:k])
        r["S3 低波动50%"] = lv

        def hv(t):
            if t < 6:
                return set(codes)
            v = {c: statistics.pstdev([CL[c][i] / CL[c][i - 1] - 1
                                       for i in range(t - 5, t + 1)]) for c in codes}
            k = max(1, round(len(codes) * 0.5))
            return set(sorted(v, key=lambda c: -v[c])[:k])
        r["S3' 高波动50%"] = hv
        return r

    R = rules()
    n = len(dates)
    # 分界：2017-01（前 10 年 / 后 10 年）
    isp = next(i for i, d in enumerate(dates) if d >= "20170101")

    for seg, a, b in (("全窗", 18, n - 1), ("前段", 18, isp), ("后段", isp, n - 1)):
        print(f"【{seg}】{len(dates[a:b])} 个月  {dates[a]} → {dates[max(a,b-1)]}")
        print(f"  {'策略':16s} {'税后年化':>9s} {'回撤':>9s} {'Calmar':>7s} "
              f"{'平均持有':>9s} {'调仓':>5s} {'累计':>9s}")
        base = None
        for name, rule in R.items():
            eq, tr, ah = engine(S, codes, dates, rule, a, b)
            m = met(eq)
            if base is None:
                base = m["cagr"]
            ahs = f"{ah:6.0f}月" if ah != float("inf") else "  从不卖"
            print(f"  {name:16s} {m['cagr']*100:+8.2f}% {m['mdd']*100:+8.2f}% "
                  f"{m['calmar']:7.2f} {ahs:>9s} {tr:5d} {m['total']*100:+8.1f}%")
        print()

    # N_eff
    rets = {c: [CL[c][i] / CL[c][i - 1] - 1 for i in range(1, n)] for c in codes}

    def corr(x, y):
        mx, my = statistics.fmean(x), statistics.fmean(y)
        nu = sum((p - mx) * (q - my) for p, q in zip(x, y))
        dx = math.sqrt(sum((p - mx) ** 2 for p in x))
        dy = math.sqrt(sum((q - my) ** 2 for q in y))
        return nu / (dx * dy)
    ps = [corr(rets[x], rets[y]) for i, x in enumerate(codes) for y in codes[i + 1:]]
    rho = statistics.fmean(ps)
    print(f"N_eff = {len(codes)/(1+(len(codes)-1)*rho):.2f}  (N={len(codes)}, ρ̄={rho:.3f})")


if __name__ == "__main__":
    main()
