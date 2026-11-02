#!/usr/bin/env python
"""候选池诊断：7 只票到底是几个独立样本？

输入：交接文档落盘的 CSV（前复权日线，D:/workbuddy de gongzuo/.../cycle_dividend_bt/data/）
输出：两两相关矩阵、平均相关、第一主成分方差解释率、有效独立样本数、逐票买入持有
"""
from __future__ import annotations
import csv, os, sys, math, statistics

DATA = r"D:/workbuddy de gongzuo/2026-10-25-00-25-03/cycle_dividend_bt/data"
CODES = ["600938","600968","601808","600583","601699","601668","601398","000300"]
NAMES = {"600938":"中国海油","600968":"海油发展","601808":"中海油服","600583":"海油工程",
         "601699":"潞安环能","601668":"中国建筑","601398":"工商银行","000300":"沪深300"}

def load(code):
    out = {}
    with open(os.path.join(DATA, f"{code}.csv"), encoding="utf-8") as f:
        rd = csv.DictReader(f)
        for r in rd:
            out[r["date"]] = float(r["close"])
    return out

series = {c: load(c) for c in CODES}
dates = sorted(set.intersection(*[set(s) for s in series.values()]))
print(f"共同交易日 {len(dates)}  {dates[0]} → {dates[-1]}")

# 日收益
rets = {}
for c, s in series.items():
    px = [s[d] for d in dates]
    rets[c] = [px[i]/px[i-1]-1.0 for i in range(1, len(px))]
n = len(dates)-1

# 买入持有
print("\n【买入持有 全窗口】")
bh = {}
for c in CODES:
    px0, px1 = series[c][dates[0]], series[c][dates[-1]]
    yrs = n/243.0
    bh[c] = (px1/px0-1.0)
    cagr = (px1/px0)**(1/yrs)-1.0
    print(f"  {c} {NAMES[c]:6s} 累计 {bh[c]*100:+7.2f}%  年化 {cagr*100:+6.2f}%")

def corr(a, b):
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x-ma)*(y-mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x-ma)**2 for x in a)); db = math.sqrt(sum((y-mb)**2 for y in b))
    return num/(da*db) if da and db else float("nan")

names7 = [c for c in CODES if c != "000300"]
print("\n【两两日收益相关（7 只）】")
print("        " + "".join(f"{NAMES[c][:4]:>7s}" for c in names7))
pairs = []
for i, a in enumerate(names7):
    row = []
    for b in names7:
        r = corr(rets[a], rets[b])
        row.append(r)
        if names7.index(a) < names7.index(b):
            pairs.append(r)
    print(f"{NAMES[a][:6]:6s} " + "".join(f"{v:+7.3f}" for v in row))

rho = statistics.fmean(pairs)
N = len(names7)
n_eff = N/(1+(N-1)*rho)
print(f"\n平均两两相关 ρ̄ = {rho:.4f}   共 {len(pairs)} 对")
print(f"有效独立样本 N_eff = N/(1+(N-1)ρ̄) = {n_eff:.2f}  （N={N}）")

# 第一主成分（幂迭代，纯 Python）
def pc1(mat, iters=300):
    k = len(mat)
    v = [1.0/math.sqrt(k)]*k
    for _ in range(iters):
        nv = [sum(mat[i][j]*v[j] for j in range(k)) for i in range(k)]
        nrm = math.sqrt(sum(x*x for x in nv))
        v = [x/nrm for x in nv]
    lam = sum(v[i]*sum(mat[i][j]*v[j] for j in range(k)) for i in range(k))
    return lam, v

mat = [[corr(rets[a], rets[b]) for b in names7] for a in names7]
lam, vec = pc1(mat)
print(f"第一主成分方差解释率 = {lam/N*100:.1f}%")
print("PC1 载荷：" + "  ".join(f"{NAMES[c][:4]}{w:+.2f}" for c, w in zip(names7, vec)))

# 与沪深300 的关系
print("\n【与沪深300 相关】")
for c in names7:
    print(f"  {NAMES[c][:6]:6s} {corr(rets[c], rets['000300']):+.3f}")
