#!/usr/bin/env python
"""close>MA12 的稳健性检验：46 个月里唯一"通过四条"的策略是真的还是噪声？

【为什么必须做这一步】
短窗口里从 6 个候选挑出 1 个"通过"，在没有稳健性检验前不能采信：
  · 46 个月 ≈ 3.8 年，逐年独立样本很少
  · 2023-2026 是红利风格顺风期，趋势过滤在单边上涨里天然不吃亏
  · 单一参数（MA12）胜出可能只是这个参数下恰好躲过某次回调

【四项检验】
  R1 参数高原：MA 10/12/15/20/24/30 × 全池——要平台，不要孤立顶点
  R2 空仓解剖：这个策略每月到底持多少仓？空仓的月份市场怎么走？
  R3 剔月敏感性：剔掉最好的 1/2/3 个月后是否还成立
  R4 分年度：是否只靠某一年
"""
from __future__ import annotations
import json
import os
import statistics
import sys

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
import bt_diverse_diag as D
import panel_fix as PF
import bt_short2023 as S

BUY_COST, SELL_COST = 0.00051, 0.00101


def main():
    pool = [c for cs in D.SECTORS.values() for c in cs if c != "601098"]
    series = D.load_monthly(set(pool))
    codes = [c for c in pool if c in series]
    dates, panel = PF.build_panel({c: series[c] for c in codes}, start=S.WARM_START)
    i0 = next(i for i, d in enumerate(dates) if d >= S.EVAL_START)
    px = {c: [panel[c][d] for d in dates] for c in codes}
    rets = {}
    for c in codes:
        r = []
        for i in range(len(dates) - 1):
            a, b = px[c][i], px[c][i + 1]
            r.append(None if (a is None or b is None or a <= 0) else b / a - 1)
        rets[c] = r
    T = len(dates) - 1
    print(f"建仓 {dates[i0]} → {dates[-1]}  评估 {T - i0} 个月  N={len(codes)}")

    def sma_at(c, i, k):
        vals = [v for v in px[c][max(0, i - k + 1):i + 1] if v is not None]
        return statistics.fmean(vals) if len(vals) >= k else None

    def run_ma(k):
        """返回 (月度收益序列, 每月持仓比例, 每月是否调仓)"""
        rs, holds, flips = [], [], []
        prev = None
        for i in range(i0, T):
            sel = [c for c in codes if px[c][i] and sma_at(c, i, k) and px[c][i] > sma_at(c, i, k)]
            if not sel:
                holds.append(0.0)
                rs.append(0.0)
                flips.append(prev is not None and prev > 0)
                prev = 0.0
                continue
            r = statistics.fmean([rets[c][i] for c in sel if rets[c][i] is not None])
            cost = 0.0
            if prev is not None and abs(len(sel) / len(codes) - prev) > 1e-9:
                cost = 0.5 * (BUY_COST + SELL_COST)
            rs.append(r - cost)
            holds.append(len(sel) / len(codes))
            flips.append(True)
            prev = len(sel) / len(codes)
        return rs, holds, flips

    def run_bh():
        rs = []
        for i in range(i0, T):
            rs.append(statistics.fmean([rets[c][i] for c in codes if rets[c][i] is not None]))
        return rs

    r_bh = run_bh()
    p_bh = S.perf(r_bh)

    # ── R1 参数高原 ──────────────────────────────────────────────
    print("\n" + "=" * 74)
    print("R1 参数高原：MA 长度扫描（要平台，不要孤立顶点）")
    print("=" * 74)
    print(f"  {'MA':>4s} {'累计':>10s} {'年化':>8s} {'回撤':>9s} {'Calmar':>7s} "
          f"{'超额':>9s} {'平均持仓':>8s} {'调仓次数':>8s}")
    grid = {}
    for k in (10, 12, 15, 20, 24, 30):
        rs, holds, flips = run_ma(k)
        p = S.perf(rs)
        grid[k] = {"perf": p, "avg_hold": statistics.fmean(holds),
                   "flips": sum(1 for f in flips if f)}
        print(f"  {k:4d} {p['total'] * 100:+9.1f}% {p['cagr'] * 100:+7.2f}% "
              f"{p['mdd'] * 100:+8.2f}% {p['calmar']:7.2f} "
              f"{(p['cagr'] - p_bh['cagr']) * 100:+8.2f}pp "
              f"{grid[k]['avg_hold'] * 100:7.1f}% {grid[k]['flips']:8d}")
    pos = sum(1 for g in grid.values() if g["perf"]["cagr"] > p_bh["cagr"])
    print(f"\n  {pos}/{len(grid)} 个参数跑赢买入持有；"
          f"超额范围 {min((g['perf']['cagr'] - p_bh['cagr']) * 100 for g in grid.values()):+.2f} ~ "
          f"{max((g['perf']['cagr'] - p_bh['cagr']) * 100 for g in grid.values()):+.2f}pp")
    print(f"  → {'平台成立（参数不敏感）' if pos == len(grid) else '部分参数跑输，护栏不稳'}")

    # ── R2 空仓解剖 ──────────────────────────────────────────────
    print("\n" + "=" * 74)
    print("R2 空仓解剖：MA12 每月持多少仓？空仓的月份市场怎么走？")
    print("=" * 74)
    rs, holds, flips = run_ma(12)
    empty = [i for i, h in enumerate(holds) if h == 0]
    print(f"  平均持仓 {statistics.fmean(holds) * 100:.1f}%   "
          f"满仓月 {sum(1 for h in holds if h > 0.999)}/{len(holds)}   "
          f"空仓月 {len(empty)}/{len(holds)}   调仓 {sum(1 for f in flips if f)} 次")
    if empty:
        print(f"\n  空仓月份及当月全池平均收益：")
        for i in empty:
            print(f"    {dates[i0 + i + 1]}  全池 {r_bh[i] * 100:+7.2f}%")
        print(f"  空仓月合计避开 {sum(r_bh[i] for i in empty) * 100:+.2f}%（负=正确避开）")
    else:
        print("  → 从未空仓：该策略在 2023-2026 基本等于买入持有，超出来自个股选择而非择时")

    # ── R3 剔月敏感性 ───────────────────────────────────────────
    print("\n" + "=" * 74)
    print("R3 剔月敏感性：剔掉最好的 N 个月后是否还跑赢")
    print("=" * 74)
    order = sorted(range(len(rs)), key=lambda i: -rs[i])
    print(f"  {'剔除':22s} {'MA12 年化':>10s} {'买入持有年化':>12s} {'超额':>9s}")
    for n_drop in (0, 1, 2, 3):
        drop = set(order[:n_drop])
        a = [rs[i] for i in range(len(rs)) if i not in drop]
        b = [r_bh[i] for i in range(len(rs)) if i not in drop]
        pa, pb = S.perf(a), S.perf(b)
        print(f"  剔最好 {n_drop} 个月".ljust(24) +
              f"{pa['cagr'] * 100:+9.2f}% {pb['cagr'] * 100:+11.2f}% "
              f"{(pa['cagr'] - pb['cagr']) * 100:+8.2f}pp")

    # ── R4 分年度 ───────────────────────────────────────────────
    print("\n" + "=" * 74)
    print("R4 分年度：是否只靠某一年")
    print("=" * 74)
    print(f"  {'年份':6s} {'MA12':>9s} {'买入持有':>9s} {'超额':>8s} {'平均持仓':>8s}")
    for y in sorted({dates[i0 + 1 + i][:4] for i in range(len(rs))}):
        idx = [i for i in range(len(rs)) if dates[i0 + 1 + i][:4] == y]
        if len(idx) < 2:
            continue
        def cum(xs):
            v = 1.0
            for x in xs:
                v *= (1 + x)
            return v - 1
        a = cum([rs[i] for i in idx])
        b = cum([r_bh[i] for i in idx])
        print(f"  {y:6s} {a * 100:+8.2f}% {b * 100:+8.2f}% {(a - b) * 100:+7.2f}pp "
              f"{statistics.fmean([holds[i] for i in idx]) * 100:7.1f}%")

    # ── 结论 ────────────────────────────────────────────────────
    print("\n" + "=" * 74)
    print("结论")
    print("=" * 74)
    p12 = grid[12]["perf"]
    print(f"  MA12：年化 {p12['cagr'] * 100:+.2f}%（买入持有 {p_bh['cagr'] * 100:+.2f}%）"
          f"  回撤 {p12['mdd'] * 100:+.2f}%（{p_bh['mdd'] * 100:+.2f}%）")
    print(f"  超额 {(p12['cagr'] - p_bh['cagr']) * 100:+.2f}pp；"
          f"参数网格 {pos}/{len(grid)} 跑赢")

    outp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "pool", "short2023_robust.json")
    with open(outp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"window": [dates[i0], dates[-1]], "grid": {str(k): v for k, v in grid.items()},
                   "buyhold": p_bh, "empty_months": [dates[i0 + i + 1] for i in empty],
                   "avg_hold_ma12": statistics.fmean(holds),
                   "grid_wins": pos, "grid_n": len(grid)},
                  f, ensure_ascii=False, indent=2, default=str)
        f.write("\n")
    print(f"\n落盘 {outp}")


if __name__ == "__main__":
    main()
