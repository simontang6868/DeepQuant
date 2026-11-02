#!/usr/bin/env python
"""回撤—年化前沿：在给定回撤上限下，各候选能拿到的最高年化。

方法：仓位缩放 w（其余持现金、零收益），w = min(目标回撤 / 该策略实际回撤, 1.5)。
近似关系：年化 ≈ w × 策略年化，回撤 ≈ w × 策略回撤（小 w 下成立）。
"""
from __future__ import annotations
import statistics
import bt_pool17 as B
import bt_s3_robust as R

SERIES, CODES, DATES, CLOSES = R.build()
N = len(DATES)
I_SPLIT = next(i for i, d in enumerate(DATES) if d >= "20250101")


def stats_seg(fn, a, b):
    eq = fn(a, b)
    return B.metrics(eq)


def main():
    print(f"池 {len(CODES)} 只  共同月末 {N}  {DATES[0]} → {DATES[-1]}")
    print(f"样本内 → {DATES[I_SPLIT-1]}   样本外 {DATES[I_SPLIT]} → {DATES[-1]}\n")

    segs = {"全窗(18→末)": (18, N - 1), "样本内": (18, I_SPLIT), "样本外": (I_SPLIT, N - 1)}
    cands = {
        "买入持有": lambda a, b: R.buy_hold(SERIES, CODES, DATES, a, b),
        "MA(6,18)择时": lambda a, b: R.run_lowvol(SERIES, CODES, DATES, CLOSES, 6, 1.0, a, b)[0],
        "低波动50%": lambda a, b: R.run_lowvol(SERIES, CODES, DATES, CLOSES, 6, 0.5, a, b)[0],
    }

    base = {}
    for seg, (a, b) in segs.items():
        print(f"【{seg}】")
        print(f"  {'策略':14s} {'年化':>8s} {'回撤':>8s} {'Calmar':>7s}")
        for name, fn in cands.items():
            m = stats_seg(fn, a, b)
            if name == "MA(6,18)择时":
                m = B.metrics(R.run_lowvol(SERIES, CODES, DATES, CLOSES, 6, 1.0, a, b)[0])
            base[(seg, name)] = m
            print(f"  {name:14s} {m['cagr']*100:+7.2f}% {m['mdd']*100:+7.2f}% {m['calmar']:7.2f}")
        print()

    print("=" * 74)
    print("回撤—年化前沿（仓位缩放，w ≤ 1.5；年化 ≈ w×策略年化）")
    print("=" * 74)
    for seg in ("全窗(18→末)", "样本外"):
        print(f"\n【{seg}】")
        print(f"  {'回撤上限':>8s} " + "".join(f"{n:>16s}" for n in cands))
        for cap in (0.10, 0.15, 0.20, 0.30):
            row = []
            for name in cands:
                m = base[(seg, name)]
                w = min(cap / abs(m["mdd"]), 1.5) if m["mdd"] else 0
                row.append(f"{m['cagr']*100*w:+15.2f}%")
            print(f"  {cap*100:>7.0f}% " + "".join(row))

    print("\n注：年化按位置缩放线性近似；w>1 表示加杠杆，实际不可得，仅用于比较风险调整后的效率。")
    print("等价读数用 Calmar：")
    for seg in ("全窗(18→末)", "样本内", "样本外"):
        s = "  " + f"{seg:10s}"
        for name in cands:
            s += f"  {name} {base[(seg,name)]['calmar']:5.2f}"
        print(s)


if __name__ == "__main__":
    main()
