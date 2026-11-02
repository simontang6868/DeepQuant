#!/usr/bin/env python
"""红利税（按实际持有期逐仓计税）——差别化税率的正确建模。

税率：持股 ≤1 个月 20% / 1 个月~1 年 10% / >1 年 0%（暂免）。
股息：按「池级年化股息率 / 12」每月对各持仓计提（近似，非逐笔除权日）。
      后复权序列已含股息再投资 → 这里只扣税，不重复计收益。

关键：**买入持有持股 >1 年后免税，频繁调仓则长期停留在 10%~20% 档**。
      这正是差别化税率对高换手策略的惩罚机制。
"""
from __future__ import annotations
import statistics
import bt_pool17 as B
import bt_s3_robust as R

SERIES, CODES, DATES, CLOSES = R.build()
N = len(DATES)
I_SPLIT = next(i for i, d in enumerate(DATES) if d >= "20250101")

YIELD = 0.050
COST = 0.00076


def tax_rate(months_held: int) -> float:
    if months_held <= 1:
        return 0.20
    if months_held <= 12:
        return 0.10
    return 0.0


def run(rule, a, b, yld=YIELD, with_tax=True):
    """通用月频引擎：rule(t) -> set(codes)。返回 (equity, trades, avg_hold_months, tax_paid)"""
    nav, eq, prev = 1.0, [1.0], set()
    since, trades, tax_paid = {}, 0, 0.0
    hold_samples = []
    for t in range(a, b):
        on = rule(t)
        # 调仓成本
        if on != prev:
            trades += len(on ^ prev) if prev else len(on)
            nav *= (1 - COST)
        # 更新持有期
        for c in list(since):
            if c not in on:
                hold_samples.append(t - since.pop(c))
        for c in on:
            since.setdefault(c, t)
        # 当月收益（后复权，含股息再投资）
        if on:
            r = statistics.fmean([SERIES[c][DATES[t + 1]][1] / SERIES[c][DATES[t]][1] - 1
                                  for c in on])
            nav *= (1 + r)
            # 股息税：按每只持仓的持有月数判档
            if with_tax:
                monthly_div = yld / 12.0
                w = 1.0 / len(on)
                drag = 0.0
                for c in on:
                    held = t - since[c]
                    drag += w * monthly_div * tax_rate(held)
                nav *= (1 - drag)
                tax_paid += drag
        prev = on
        eq.append(nav)
    if since:
        hold_samples += [b - since[c] for c in since]
    avg_hold = statistics.fmean(hold_samples) if hold_samples else float("inf")
    return eq, trades, avg_hold, tax_paid


def show(seg, a, b):
    print(f"\n【{seg}】{len(DATES[a:b])} 个月  {DATES[a]} → {DATES[max(a, b-1)]}")
    print(f"  {'策略':14s} {'税前年化':>9s} {'税后年化':>9s} {'税负':>7s} {'平均持有':>9s} "
          f"{'税后回撤':>9s} {'Calmar':>7s} {'调仓':>5s}")
    out = {}
    for name, rule in CANDIDATES.items():
        eq0, tr, ah, _ = run(rule, a, b, with_tax=False)
        eq1, _, _, tp = run(rule, a, b, with_tax=True)
        m0, m1 = B.metrics(eq0), B.metrics(eq1)
        out[name] = (m0, m1, ah, tr)
        ahtxt = f"{ah:8.1f}月" if ah != float("inf") else "     从不卖"
        print(f"  {name:14s} {m0['cagr']*100:+8.2f}% {m1['cagr']*100:+8.2f}% "
              f"{(m1['cagr']-m0['cagr'])*100:+6.2f}pp {ahtxt} "
              f"{m1['mdd']*100:+8.2f}% {m1['calmar']:7.2f} {tr:5d}")
    return out


def make_rules():
    rules = {"买入持有": lambda t: set(CODES)}
    rules["MA(6,18)择时"] = lambda t: {
        c for c in CODES
        if B.sma(CLOSES[c], 6, t) and B.sma(CLOSES[c], 18, t)
        and B.sma(CLOSES[c], 6, t) > B.sma(CLOSES[c], 18, t)}

    def lowvol(t):
        if t < 6:
            return set(CODES)          # 预热期直接持有全池，避免空仓失真
        v = {c: statistics.pstdev([CLOSES[c][i] / CLOSES[c][i - 1] - 1
                                   for i in range(t - 5, t + 1)]) for c in CODES}
        k = max(1, int(round(len(CODES) * 0.5)))
        return set(sorted(v, key=lambda c: v[c])[:k])
    rules["低波动50%"] = lowvol
    return rules


CANDIDATES = None


def main():
    global CANDIDATES
    CANDIDATES = make_rules()
    print(f"池 {len(CODES)} 只  月末 {N}  池级股息率 {YIELD*100:.1f}%/年")
    print("税率：持有 ≤1月 20% / 1月~1年 10% / >1年 0%（差别化，逐仓判定）")

    show("全窗", 18, N - 1)
    show("样本内", 18, I_SPLIT - 1)
    res_out = show("样本外", I_SPLIT, N - 1)

    print("\n" + "=" * 72)
    print("【样本外超额（税后）】")
    bh = res_out["买入持有"][1]["cagr"]
    for name, (m0, m1, ah, tr) in res_out.items():
        print(f"  {name:14s} 税后年化 {m1['cagr']*100:+6.2f}%   超额 {(m1['cagr']-bh)*100:+6.2f}pp")
    print("\n【股息率敏感性（样本外，税后超额 vs 买入持有）】")
    for yld in (0.03, 0.05, 0.08, 0.10):
        base = B.metrics(run(CANDIDATES["买入持有"], I_SPLIT, N - 1, yld)[0])["cagr"]
        s = f"  股息率 {yld*100:4.0f}%: 持有 {base*100:+6.2f}%"
        for name in ("MA(6,18)择时", "低波动50%"):
            m = B.metrics(run(CANDIDATES[name], I_SPLIT, N - 1, yld)[0])
            s += f" | {name} {m['cagr']*100:+6.2f}% ({(m['cagr']-base)*100:+5.2f}pp)"
        print(s)


if __name__ == "__main__":
    main()
