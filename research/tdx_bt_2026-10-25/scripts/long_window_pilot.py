#!/usr/bin/env python
"""长窗口月线择时试点：在一个"有真实下跌"的样本里，择时到底跑不跑得赢买入持有？

数据源：通达信 tdx_kline period=6（月线）wantNum=260，结果落盘 txt，按交接文档 §1.6 用 raw_decode 解析。
标的：601398 工商银行 / 601668 中国建筑 / 601699 潞安环能 / 601808 中海油服 / 600583 海油工程
      （剔除 600938 中国海油、600968 海油发展：上市太晚，会把窗口砍到 2022 年后）
规则（禁未来数据）：T 月末收盘算信号 → T+1 月开盘成交。
"""
from __future__ import annotations
import json, os, math, statistics, glob

TDIR = r"C:/Users/77436/.workbuddy/projects/d-DeepQuant power by txm-DeepQuant-main/33860a17-098b-4d49-954f-75fde862c20d/tool-results"
NAMES = {"601398": "工商银行", "601668": "中国建筑", "601699": "潞安环能",
         "601808": "中海油服", "600583": "海油工程"}

def parse(path):
    text = open(path, "r", encoding="utf-8").read()
    i = text.find("{")
    obj, _ = json.JSONDecoder().raw_decode(text[i:])   # 只取第一个 JSON
    return obj

# 1) 载入
series = {}
for f in sorted(glob.glob(os.path.join(TDIR, "mcp-tdx-connector-tdx_kline-1792863*.txt"))):
    obj = parse(f)
    code = obj.get("Code")
    rows = obj.get("Rows") or []
    if code not in NAMES or not rows:
        continue
    s = {}
    for r in rows:
        s[r["Data"]] = (float(r["Open"]), float(r["Close"]))
    series[code] = s
    print(f"载入 {code} {NAMES[code]}：{len(s)} 根月线  {min(s)} → {max(s)}")

dates = sorted(set.intersection(*[set(s) for s in series.values()]))
print(f"\n共同月线 {len(dates)} 根  {dates[0]} → {dates[-1]}")

# 2) 工具
def sma(v, k, i):
    if i + 1 < k:
        return None
    return statistics.fmean(v[i+1-k:i+1])

def stats(eq):
    n = len(eq)
    ret = eq[-1]/eq[0] - 1.0
    yrs = (n-1)/12.0
    cagr = (eq[-1]/eq[0])**(1/yrs) - 1.0 if yrs > 0 else float("nan")
    peak, mdd = eq[0], 0.0
    for x in eq:
        peak = max(peak, x)
        mdd = min(mdd, x/peak - 1.0)
    rets = [eq[i]/eq[i-1]-1.0 for i in range(1, n)]
    vol = statistics.pstdev(rets)*math.sqrt(12) if len(rets) > 1 else float("nan")
    return ret, cagr, vol, mdd, (cagr/abs(mdd) if mdd else float("nan"))

def run(code, short, long_, dates):
    s = series[code]
    cl = [s[d][1] for d in dates]
    op = [s[d][0] for d in dates]
    eq_bh, eq_st, pos, trades = [1.0], [1.0], 0, 0
    held = 1.0   # 买入持有净值
    nav = 1.0
    for t in range(long_, len(dates)-1):
        ma_s = sma(cl, short, t); ma_l = sma(cl, long_, t)
        sig = 1 if (ma_s is not None and ma_l is not None and ma_s > ma_l) else 0
        # T+1 月开盘执行
        px_in, px_out = op[t+1], op[t+1]
        if sig == 1 and pos == 0:
            nav *= (1 - 0.00051)          # 买入成本
            pos = 1; trades += 1
        elif sig == 0 and pos == 1:
            nav *= (1 - 0.00051 - 0.00050)  # 卖出成本（含印花）
            pos = 0; trades += 1
        # 盯市：本月末收盘
        r = cl[t+1]/cl[t] - 1.0
        if pos == 1:
            nav *= (1 + r)
        held *= (1 + r)
        eq_st.append(nav); eq_bh.append(held)
    return stats(eq_bh), stats(eq_st), trades

print("\n" + "=" * 78)
print("月线 MA 择时 vs 买入持有（成本：等价单边 0.051%，月频，T月末信号→次月开盘成交）")
print("=" * 78)
for short, long_ in ((6, 18), (12, 36)):
    print(f"\n■ MA({short},{long_})")
    print(f"  {'标的':10s} {'买入持有累计':>12s} {'年化':>8s} {'回撤':>8s} | "
          f"{'策略累计':>10s} {'年化':>8s} {'回撤':>8s} {'Calmar':>7s} {'交易':>5s}")
    agg = {"bh": [], "st": []}
    for c in ["601398", "601668", "601699", "601808", "600583"]:
        bh, st, tr = run(c, short, long_, dates)
        agg["bh"].append(bh[1]); agg["st"].append(st[1])
        print(f"  {NAMES[c]:10s} {bh[0]*100:+11.1f}% {bh[1]*100:+7.2f}% {bh[3]*100:+7.1f}% | "
              f"{st[0]*100:+9.1f}% {st[1]*100:+7.2f}% {st[3]*100:+7.1f}% "
              f"{st[4]:7.2f} {tr:5d}")
    print(f"  {'等权平均年化':10s} {statistics.fmean(agg['bh'])*100:+7.2f}%"
          f"          → 策略 {statistics.fmean(agg['st'])*100:+7.2f}%"
          f"   超额 {(statistics.fmean(agg['st'])-statistics.fmean(agg['bh']))*100:+.2f}pp")

# 3) 关键：下跌年表现（买入持有 vs 策略）
print("\n" + "=" * 78)
print("关键年份检验：MA(6,18) 策略在真实下跌段能否少亏")
print("=" * 78)
segs = [("2015 股灾", "20150601", "20160131"), ("2018 全年", "20180101", "20181231"),
        ("2022 全年", "20220101", "20221231"), ("2024 全年", "20240101", "20241231")]
for label, a, b in segs:
    sub = [d for d in dates if a <= d <= b]
    if len(sub) < 2:
        print(f"  {label}: 数据不足"); continue
    out = []
    for c in ["601398", "601668", "601699", "601808", "600583"]:
        s = series[c]
        r_bh = s[sub[-1]][1]/s[sub[0]][1] - 1.0
        # 策略：区间内持仓比例（用区间前的信号近似，仅作指示）
        cl = [s[d][1] for d in dates]
        i0 = dates.index(sub[0])
        ma_s = sma(cl, 6, i0-1); ma_l = sma(cl, 18, i0-1)
        in_pos = 1 if (ma_s and ma_l and ma_s > ma_l) else 0
        out.append((NAMES[c], r_bh, in_pos))
    avg = statistics.fmean(x[1] for x in out)
    exposed = sum(x[2] for x in out)
    print(f"  {label:9s} 等权买入持有 {avg*100:+7.2f}%   区间初持仓 {exposed}/5 只")
