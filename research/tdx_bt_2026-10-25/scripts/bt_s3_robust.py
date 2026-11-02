#!/usr/bin/env python
"""S3 低波动选股的稳健性：样本外切分 + 参数高原检验 + 成本敏感性。

复用 bt_pool17 的加载与指标函数。
"""
from __future__ import annotations
import statistics
import bt_pool17 as B

COST_PER_TURN = 0.00076   # 单边买 0.051% / 卖 0.101% 的近似平均


def build():
    daily, monthly = B.load_all()
    series = {}
    for c, b in daily.items():
        series[c] = B.to_monthly(b)
    for c, b in monthly.items():
        if c not in series:
            series[c] = b
    codes = sorted(series)
    dates = sorted(set.intersection(*[set(s) for s in series.values()]))
    dates = [d for d in dates if d >= "20220421"]
    closes = {c: [series[c][d][1] for d in dates] for c in codes}
    return series, codes, dates, closes


def run_lowvol(series, codes, dates, closes, lookback, keep, st, en, cost=COST_PER_TURN):
    """在 [st, en) 上跑低波动选股，返回 equity 序列。"""
    nav, eq, prev, trades = 1.0, [1.0], set(), 0
    for t in range(st, en):
        if t < lookback:
            on = set()
        else:
            v = {}
            for c in codes:
                r = [closes[c][i] / closes[c][i - 1] - 1 for i in range(t - lookback + 1, t + 1)]
                v[c] = statistics.pstdev(r)
            k = max(1, int(round(len(codes) * keep)))
            on = set(sorted(v, key=lambda c: v[c])[:k])
        if on:
            r = statistics.fmean([series[c][dates[t + 1]][1] / series[c][dates[t]][1] - 1
                                  for c in on])
            nav *= (1 + r)
        if on != prev:
            trades += len(on ^ prev) if prev else len(on)
            nav *= (1 - cost)
        prev = on
        eq.append(nav)
    return eq, trades


def buy_hold(series, codes, dates, st, en):
    base = {c: series[c][dates[st]][1] for c in codes}
    return [statistics.fmean([series[c][d][1] / base[c] for c in codes])
            for d in dates[st:en]]


def main():
    series, codes, dates, closes = build()
    n = len(dates)
    print(f"池 {len(codes)} 只，共同月末 {n} 个  {dates[0]} → {dates[-1]}\n")

    i_split = next(i for i, d in enumerate(dates) if d >= "20250101")
    print(f"样本内 0..{i_split-1}（{dates[0]}~{dates[i_split-1]}）  样本外 {i_split}..{n-1}（{dates[i_split]}~{dates[-1]}）\n")

    # 1) 参数高原：lookback × keep
    print("【参数高原检验】年化收益（同窗 st=18, en=n-1）")
    lbs = [3, 6, 9, 12]
    keeps = [0.3, 0.5, 0.7]
    print("  lookback\\keep " + "".join(f"{int(k*100):>8d}%" for k in keeps) + "    调仓次数@50%")
    for lb in lbs:
        cells = []
        tr50 = 0
        for k in keeps:
            eq, tr = run_lowvol(series, codes, dates, closes, lb, k, 18, n - 1)
            m = B.metrics(eq)
            cells.append(f"{m['cagr']*100:>+8.2f}%")
            if k == 0.5:
                tr50 = tr
        print(f"  {lb:>13d} " + "".join(cells) + f"    {tr50:>6d}")

    print("\n【对照：同窗买入持有】")
    m_bh = B.metrics(buy_hold(series, codes, dates, 18, n - 1))
    print(f"  年化 {m_bh['cagr']*100:+.2f}%  回撤 {m_bh['mdd']*100:+.2f}%  Calmar {m_bh['calmar']:.2f}")

    # 2) 样本外
    print("\n【样本外切分（lookback=6, keep=50%）】")
    for lab, (a, b) in {"样本内": (18, i_split), "样本外": (i_split, n - 1)}.items():
        eq, tr = run_lowvol(series, codes, dates, closes, 6, 0.5, a, b)
        m = B.metrics(eq)
        bh = B.metrics(buy_hold(series, codes, dates, a, b))
        print(f"  {lab}: 低波 年化 {m['cagr']*100:+6.2f}% 回撤 {m['mdd']*100:+7.2f}% Calmar {m['calmar']:5.2f}"
              f" | 买入持有 年化 {bh['cagr']*100:+6.2f}% 回撤 {bh['mdd']*100:+7.2f}%"
              f" | 超额 {(m['cagr']-bh['cagr'])*100:+5.2f}pp  调仓 {tr}")

    # 3) 成本敏感性
    print("\n【成本敏感性（lookback=6, keep=50%, 同窗）】")
    for cost, lab in ((0.0, "零成本"), (0.00076, "基准 0.076%/次"), (0.003, "压力 0.30%/次")):
        eq, tr = run_lowvol(series, codes, dates, closes, 6, 0.5, 18, n - 1, cost=cost)
        m = B.metrics(eq)
        print(f"  {lab:16s} 年化 {m['cagr']*100:+6.2f}%  回撤 {m['mdd']*100:+7.2f}%  Calmar {m['calmar']:5.2f}  ({tr} 次调仓)")


if __name__ == "__main__":
    main()
