#!/usr/bin/env python
"""当前状态（2026-10）诊断：这批票现在贵不贵、趋势是否还在。

【为什么做】
作者已经明确"持有一段就卖"，那么进场时点的位置比长期年化更重要。
2023-01 建仓的 +45.9% 是已经发生的事实；现在（2026-10）买入面对的是完全不同的位置。

【三项刻画】
  C1 位置：当前后复权价在窗口内的百分位（是否已在高位）
  C2 趋势：当前价 vs MA6/MA12/MA24，以及距窗口最高点的回撤
  C3 历史类比：把历史上"处于类似位置"的时点找出来，看随后 12 个月的实际收益分布
      —— 这是对"现在买入会怎样"唯一诚实的回答方式（不是预测，是条件分布）

⚠️ 本文件只做历史状态描述，不构成任何买卖建议。
"""
from __future__ import annotations
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
import bt_diverse_diag as D
import panel_fix as PF

WARM = "20071201"


def main():
    pool = [c for cs in D.SECTORS.values() for c in cs if c != "601098"]
    series = D.load_monthly(set(pool))
    codes = [c for c in pool if c in series]
    dates, panel = PF.build_panel({c: series[c] for c in codes}, start=WARM)
    px = {c: [panel[c][d] for d in dates] for c in codes}
    last = len(dates) - 1
    print(f"数据 {dates[0]} → {dates[-1]}  共 {len(dates)} 月末  N={len(codes)}")

    def ma(c, i, k):
        vals = [v for v in px[c][max(0, i - k + 1):i + 1] if v is not None]
        return statistics.fmean(vals) if len(vals) >= k else None

    # ── C1/C2 当前位置 ──────────────────────────────────────────
    print("\n" + "=" * 76)
    print("C1/C2 当前状态（2026-10-23 收盘）")
    print("=" * 76)
    print(f"  {'名称':8s} {'现价':>10s} {'历史分位':>8s} {'vs MA6':>8s} {'vs MA12':>8s} "
          f"{'vs MA24':>8s} {'距最高点':>9s}")
    rows = []
    for c in codes:
        hist = [v for v in px[c][:last + 1] if v is not None]
        cur = hist[-1]
        pct = sum(1 for v in hist if v <= cur) / len(hist)
        m6, m12, m24 = ma(c, last, 6), ma(c, last, 12), ma(c, last, 24)
        hi = max(hist)
        rec = {
            "code": c, "name": D.NAMES[c], "cur": cur, "pct": pct,
            "vs_ma6": cur / m6 - 1 if m6 else None,
            "vs_ma12": cur / m12 - 1 if m12 else None,
            "vs_ma24": cur / m24 - 1 if m24 else None,
            "from_high": cur / hi - 1,
        }
        rows.append(rec)
        print(f"  {D.NAMES[c]:8s} {cur:10.1f} {pct * 100:7.1f}% "
              f"{rec['vs_ma6'] * 100:+7.1f}% {rec['vs_ma12'] * 100:+7.1f}% "
              f"{rec['vs_ma24'] * 100:+7.1f}% {rec['from_high'] * 100:+8.1f}%")

    pcts = [r["pct"] for r in rows]
    above12 = sum(1 for r in rows if r["vs_ma12"] > 0)
    print(f"\n  历史分位中位 {statistics.median(pcts) * 100:.1f}%  "
          f"（>80% 的有 {sum(1 for p in pcts if p > 0.8)} 只，<30% 的有 {sum(1 for p in pcts if p < 0.3)} 只）")
    print(f"  站上 MA12 的 {above12}/{len(rows)} 只；距历史最高点中位回撤 "
          f"{statistics.median([r['from_high'] for r in rows]) * 100:.1f}%")

    # ── C3 条件分布：历史上"类似位置"之后 12 个月怎么走 ────────
    print("\n" + "=" * 76)
    print("C3 条件分布：历史上全池处于「相似位置」时，随后 12 个月的实际收益")
    print("=" * 76)
    # 用「全池等权价 / 其 MA12」作为统一的状态变量（>1 = 整体在均线上方）
    port_lvl = []
    for i in range(len(dates)):
        vals = [px[c][i] for c in codes if px[c][i] is not None]
        port_lvl.append(statistics.fmean(vals) if vals else None)

    def port_ma(i, k):
        vals = [v for v in port_lvl[max(0, i - k + 1):i + 1] if v is not None]
        return statistics.fmean(vals) if len(vals) >= k else None

    state = []
    for i in range(len(dates)):
        m = port_ma(i, 12)
        state.append(port_lvl[i] / m - 1 if (m and port_lvl[i]) else None)

    cur_state = state[last]
    print(f"  当前状态值（全池价/MA12 − 1）= {cur_state * 100:+.2f}%")

    buckets = [("远低于均线 <−8%", -9.99, -0.08), ("略低 −8~−2%", -0.08, -0.02),
               ("均线附近 −2~+2%", -0.02, 0.02), ("略高 +2~+8%", 0.02, 0.08),
               ("远高于均线 >+8%", 0.08, 9.99)]
    print(f"\n  {'区间':18s} {'样本':>5s} {'后续12月中位':>12s} {'亏损概率':>8s} {'P25':>8s} {'P75':>8s}")
    cond = {}
    for lab, lo, hi in buckets:
        xs = []
        for i in range(len(dates) - 12):
            if state[i] is None or not (lo <= state[i] < hi):
                continue
            a, b = port_lvl[i], port_lvl[i + 12]
            if a and b:
                xs.append(b / a - 1)
        if not xs:
            continue
        xs.sort()
        cond[lab] = {"n": len(xs), "median": statistics.median(xs),
                     "neg": sum(1 for x in xs if x < 0) / len(xs),
                     "p25": xs[len(xs) // 4], "p75": xs[len(xs) * 3 // 4]}
        print(f"  {lab:18s} {len(xs):5d} {statistics.median(xs) * 100:+11.2f}% "
              f"{cond[lab]['neg'] * 100:7.0f}% {xs[len(xs) // 4] * 100:+7.2f}% "
              f"{xs[len(xs) * 3 // 4] * 100:+7.2f}%")

    cur_bucket = next((lab for lab, lo, hi in buckets if lo <= cur_state < hi), None)
    print(f"\n  当前落在区间：**{cur_bucket}**")
    if cur_bucket and cur_bucket in cond:
        c = cond[cur_bucket]
        print(f"  该区间历史上（n={c['n']}）后续 12 个月：中位 {c['median'] * 100:+.2f}%，"
              f"亏损概率 {c['neg'] * 100:.0f}%，区间 [{c['p25'] * 100:+.2f}%, {c['p75'] * 100:+.2f}%]")

    # ── 当前位置买入的三年路径分布 ──────────────────────────────
    print("\n" + "=" * 76)
    print("C4 若在「当前位置」买入，历史相似时点的 12/24 个月结果")
    print("=" * 76)
    if cur_bucket in cond:
        lo, hi = next((l, h) for lab, l, h in buckets if lab == cur_bucket)
        for N in (12, 24):
            xs = []
            for i in range(len(dates) - N):
                if state[i] is None or not (lo <= state[i] < hi):
                    continue
                a, b = port_lvl[i], port_lvl[i + N]
                if a and b:
                    xs.append(b / a - 1)
            if xs:
                xs.sort()
                print(f"  持有 {N} 月 (n={len(xs):2d}): 最差 {xs[0] * 100:+7.2f}%  "
                      f"中位 {statistics.median(xs) * 100:+7.2f}%  最好 {xs[-1] * 100:+7.2f}%  "
                      f"亏损概率 {sum(1 for x in xs if x < 0) / len(xs) * 100:.0f}%")

    outp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "pool", "current_state.json")
    with open(outp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"asof": dates[last], "rows": rows,
                   "pct_median": statistics.median(pcts),
                   "above_ma12": above12, "n": len(rows),
                   "cur_state": cur_state, "cur_bucket": cur_bucket,
                   "conditional": cond}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"\n落盘 {outp}")


if __name__ == "__main__":
    main()
