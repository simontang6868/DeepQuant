#!/usr/bin/env python
"""高股息池回测引擎（月频，T月末信号 → 次月开盘成交，禁前视）。

数据：通达信 tdx_kline period=6 (月线) tqFlag=2 (后复权)，结果落盘 txt。
  ⚠️ 长窗口必须用后复权：前复权在高股息标的上会穿零（见 TDX_POOL_STRATEGY_STUDY_2026-10-25.md §2）
"""
from __future__ import annotations
import json, os, glob, math, statistics

TDIR = r"C:/Users/77436/.workbuddy/projects/d-DeepQuant power by txm-DeepQuant-main/33860a17-098b-4d49-954f-75fde862c20d/tool-results"
# 后复权( tqFlag=2 ) 那几批的时间戳前缀
HQFQ_TS = ("1792863156436","1792863156286","1792863171555","1792863171557","1792863171394",
           "1792897853406","1792897853407")
NAMES = {"601398":"工商银行","601668":"中国建筑","601699":"潞安环能","601808":"中海油服",
         "600583":"海油工程","600188":"兖矿能源","600256":"广汇能源","601919":"中远海控"}

def parse(p):
    t = open(p, encoding="utf-8").read()
    i = t.find("{")
    return json.JSONDecoder().raw_decode(t[i:])[0]

def load_hqfq() -> dict[str, dict[str, tuple[float, float]]]:
    out = {}
    for f in glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-*.txt")):
        base = os.path.basename(f)
        ts = base.split("-")[-2]
        if ts not in HQFQ_TS:
            continue
        try:
            o = parse(f)
        except Exception:
            continue
        if o.get("Period") != 6:
            continue
        code = o.get("Code")
        rows = o.get("Rows") or []
        if not rows or code in out:
            continue
        out[code] = {r["Data"]: (float(r["Open"]), float(r["Close"])) for r in rows}
    return out

def sma(v, k, i):
    return statistics.fmean(v[i+1-k:i+1]) if i+1 >= k else None

def metrics(eq, periods_per_year=12):
    n = len(eq)
    if n < 3: return {}
    yrs = (n-1)/periods_per_year
    cagr = (eq[-1]/eq[0])**(1/yrs) - 1
    peak, mdd = eq[0], 0.0
    for x in eq:
        peak = max(peak, x); mdd = min(mdd, x/peak - 1)
    r = [eq[i]/eq[i-1]-1 for i in range(1, n)]
    vol = statistics.pstdev(r)*math.sqrt(periods_per_year)
    return {"total": eq[-1]/eq[0]-1, "cagr": cagr, "vol": vol, "mdd": mdd,
            "calmar": cagr/abs(mdd) if mdd else float("nan"), "years": yrs, "bars": n}

BUY_COST = 0.00051      # 等价单边 0.051%
SELL_COST = 0.00101     # 单边 + 印花税 0.05%

def backtest(series, dates, rule, start_idx):
    """rule(code, closes, t) -> 0/1 ; 组合等权持有所有 on 的标的。"""
    nav, prev_on, trades = 1.0, set(), 0
    eq = [1.0]
    for t in range(start_idx, len(dates)-1):
        on = set()
        for c, s in series.items():
            cl = [s[d][1] for d in dates]
            if rule(c, cl, t):
                on.add(c)
        nxt_on = set()
        for c, s in series.items():
            cl = [s[d][1] for d in dates]
            if rule(c, cl, t+1):
                nxt_on.add(c)
        # 次月开盘调仓
        w_old = 1.0/len(prev_on) if prev_on else 0.0
        w_new = 1.0/len(on) if on else 0.0
        turnover = 0.0
        if prev_on or on:
            if not prev_on and on:            turnover = 1.0
            elif prev_on and not on:          turnover = 1.0
            else:
                turnover = len(on - prev_on)/max(len(on),1) + len(prev_on - on)/max(len(prev_on),1)
                turnover = min(turnover, 2.0)/2
        cost = turnover*(BUY_COST+SELL_COST)/2
        if on:
            r = statistics.fmean([series[c][dates[t+1]][1]/series[c][dates[t]][1]-1 for c in on])
            nav *= (1 + r*(1-0.0))
        # 调仓成本
        nav *= (1 - cost)
        if on != prev_on:
            trades += len(on ^ prev_on)
        prev_on = on
        eq.append(nav)
    return eq, trades

def main():
    series = load_hqfq()
    series = {c: s for c, s in series.items() if c in NAMES}
    print(f"载入后复权月线 {len(series)} 只: " + " ".join(f"{c}{NAMES[c]}" for c in series))
    for c, s in series.items():
        print(f"  {c} {NAMES[c]:6s} {len(s)} 根  {min(s)} → {max(s)}")
    dates = sorted(set.intersection(*[set(s) for s in series.values()]))
    dates = [d for d in dates if d >= "20220421"]
    print(f"\n共同月线(窗口内) {len(dates)} 根  {dates[0]} → {dates[-1]}")
    start = 18   # 预热，够 MA(6,18)
    rules = {
        "S0 月度再平衡(同窗)": lambda c, cl, t: True,
        "S1 MA(6,18) 择时":  lambda c, cl, t: (sma(cl,6,t) is not None and sma(cl,18,t) is not None
                                              and sma(cl,6,t) > sma(cl,18,t)),
        "S4 close>MA12":     lambda c, cl, t: (sma(cl,12,t) is not None and cl[t] > sma(cl,12,t)),
    }
    print(f"\n{'策略':18s} {'累计':>9s} {'年化':>8s} {'波动':>7s} {'最大回撤':>9s} {'Calmar':>7s} {'调仓次数':>8s}")
    print("-"*72)
    for name, rule in rules.items():
        eq, tr = backtest(series, dates, rule, start)
        m = metrics(eq)
        print(f"{name:18s} {m['total']*100:+8.2f}% {m['cagr']*100:+7.2f}% "
              f"{m['vol']*100:6.2f}% {m['mdd']*100:+8.2f}% {m['calmar']:7.2f} {tr:8d}")

    # 真·买入持有：期初等权买入、权重随价格漂移、此后不再平衡（与"月度再平衡"严格区分）
    def buy_hold(series, dates, st):
        base = {c: series[c][dates[st]][1] for c in series}
        return [statistics.fmean([series[c][d][1]/base[c] for c in series]) for d in dates[st:]]

    for lab, st in (("S0b 真买入持有(全窗)", 0), ("S0b 真买入持有(同窗)", start)):
        eq = buy_hold(series, dates, st)
        m = metrics(eq)
        print(f"{lab:18s} {m['total']*100:+8.2f}% {m['cagr']*100:+7.2f}% "
              f"{m['vol']*100:6.2f}% {m['mdd']*100:+8.2f}% {m['calmar']:7.2f} "
              f"{0:8d}   评估 {m['bars']} 月 / {m['years']:.2f} 年")

    # 买入持有：全窗口（无预热）+ 同窗，两者并列，避免"预热吃掉 18 个月"的失真
    for lab, st in (("S0 买入持有(全窗)", 0), ("S0 买入持有(同窗)", start)):
        eq, tr = backtest(series, dates, lambda c, cl, t: True, st)
        m = metrics(eq)
        print(f"{lab:18s} {m['total']*100:+8.2f}% {m['cagr']*100:+7.2f}% "
              f"{m['vol']*100:6.2f}% {m['mdd']*100:+8.2f}% {m['calmar']:7.2f} "
              f"{tr:8d}   评估 {m['bars']} 月 / {m['years']:.2f} 年")

    # 逐只买入持有
    print(f"\n{'逐只买入持有':14s} {'累计':>9s} {'年化':>8s} {'最大回撤':>9s}")
    for c, s in series.items():
        px = [s[d][1] for d in dates]
        eq = [p/px[0] for p in px]
        m = metrics(eq)
        print(f"{NAMES[c]:6s} {m['total']*100:+8.2f}% {m['cagr']*100:+7.2f}% {m['mdd']*100:+8.2f}%"
              f"  Calmar {m['calmar']:5.2f}")

    # N_eff
    print("\n【N_eff 重算（8 只，月收益相关）】")
    codes = list(series)
    rets = {c: [series[c][dates[i]][1]/series[c][dates[i-1]][1]-1 for i in range(1, len(dates))] for c in codes}
    def corr(a,b):
        ma,mb=statistics.fmean(a),statistics.fmean(b)
        num=sum((x-ma)*(y-mb) for x,y in zip(a,b))
        da=math.sqrt(sum((x-ma)**2 for x in a)); db=math.sqrt(sum((y-mb)**2 for y in b))
        return num/(da*db) if da and db else float("nan")
    ps=[corr(rets[a],rets[b]) for i,a in enumerate(codes) for b in codes[i+1:]]
    rho=statistics.fmean(ps); N=len(codes)
    print(f"  ρ̄ = {rho:.4f}   N_eff = {N/(1+(N-1)*rho):.2f}  (N={N})")

if __name__ == "__main__":
    main()
