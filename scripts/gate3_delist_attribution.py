#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Gate 3 专项归因：那 57% 来自退市股的效应，到底是真反弹还是价格伪影？

## 背景

`docs/GATE3_RESULT_REPORT_2026-10-20.md` §4 记录：把 240 只"最终退市"的股票整体剔出池后，
2007-2015 的 mean(d) 从 +0.4841 掉到 +0.2084（t 2.12→1.04），即 **57% 的效应来自退市股**。
报告当时只能给出这个占比，**没有回答"为什么"**。本脚本回答三件事：

1. **时间结构**：这些股票被选进多头腿时，距离最终退市还有多久？
2. **价格路径**：从退市日往回看，−24/−12/−6/−3 个月的累计超额收益是什么样的？
3. **真反弹还是伪影**：
   - 若为真反弹 → 退市前应有"急跌后短期反弹"的模式，且这些持仓期的收益分布正常；
   - 若为伪影 → 应看到 ADJ/EXEC 比值在末期异常、或持仓期收益集中在退市那一个月。

## 口径

- 退市日 = 该股 EXEC 序列的最后一个交易日（与 ADJ 序列终点一致）
- 超额收益 = 个股 ADJ 收益 − 同池等权收益（同月）
- 分桶 = 距退市月数：0~3 / 4~12 / 13~24 / >24 / 未退市
- 分类 = 退市前 12 个月累计 ADJ 收益 < −50% 记为"崩盘式"，否则"温和式"

用法：
  python scripts/gate3_delist_attribution.py
"""

from __future__ import annotations

import glob
import json
import math
import os
import statistics
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage3_sigma_d import (  # noqa: E402
    MIN_VALID_RATIO,
    load_close_matrix,
    month_end_positions,
    period_return,
    scores_at,
)
from run_gate3_longsample import EXCLUDED_SYMBOLS, K_MAIN, LOOKBACK, POOL_DIR, SKIP  # noqa: E402

START, END_SIGNAL, END_HOLD = "2007-01-01", "2015-12-31", "2016-02-29"
BUCKETS = [(0, 3), (4, 12), (13, 24), (25, 999), (None, None)]  # 最后一档 = 未退市


def bucket_of(months: int | None) -> str:
    if months is None:
        return "未退市"
    for lo, hi in BUCKETS[:4]:
        if lo <= months <= hi:
            return f"{lo}~{hi}月" if hi < 999 else f">{lo}月"
    return ">25月"


def main() -> int:
    close_all, _ = load_close_matrix(POOL_DIR)
    close = close_all.drop(columns=[c for c in EXCLUDED_SYMBOLS if c in close_all.columns])
    cal = close.index
    delist_set = {os.path.basename(p).replace(".qfq.csv", "") for p in glob.glob("data/qfq_mult/delist/*.qfq.csv")}

    # 退市日与月末索引
    last_date = {c: close[c].dropna().index[-1] for c in close.columns}
    delisted = [c for c in close.columns if c in delist_set]
    print(f"[attr] 池 {close.shape[1]} 只 | 其中退市 {len(delisted)} 只")

    # 退市年份分布
    yr: dict[str, int] = {}
    for c in delisted:
        yr[str(last_date[c].year)] = yr.get(str(last_date[c].year), 0) + 1
    print("[attr] 退市年份分布：" + ", ".join(f"{y}:{n}" for y, n in sorted(yr.items())))

    # 退市前 12 个月累计收益（ADJ）
    pre12 = {}
    for c in delisted:
        s = close[c].dropna()
        if len(s) > 250:
            pre12[c] = float(s.iloc[-1] / s.iloc[-250] - 1)
    crash = {c for c, v in pre12.items() if v < -0.50}
    print(f"[attr] 退市前 12 个月跌幅 < −50% 的「崩盘式」退市：{len(crash)}/{len(pre12)} 只"
          f"（中位 {statistics.median(pre12.values())*100:.1f}%）")

    # 逐月持仓，记录每个持仓位的「距退市月数」与持有期收益
    min_valid = math.ceil(LOOKBACK * MIN_VALID_RATIO)
    positions = month_end_positions(cal, START, END_HOLD)
    recs = []
    ew_month = {}
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        if str(cal[pos].date()) > END_SIGNAL:
            continue
        sc = scores_at(pos, close, lookback=LOOKBACK, skip=SKIP, min_valid_days=min_valid)
        if len(sc) < 3:
            continue
        rets = period_return(pos, nxt, close.ffill())
        pool = sc.index.intersection(rets.dropna().index)
        kk = min(K_MAIN, len(pool) // 2)
        if kk < 1:
            continue
        ranked = sc.loc[pool].sort_values(ascending=True)
        longs, shorts = list(ranked.index[:kk]), list(ranked.index[-kk:])
        month = str(cal[nxt].date())[:7]
        ew_month[month] = float(rets.loc[pool].mean())
        for leg, names in (("long", longs), ("short", shorts)):
            for c in names:
                ld = last_date.get(c)
                m2d = None
                if c in delist_set and ld is not None:
                    m2d = (ld.year - cal[pos].year) * 12 + (ld.month - cal[pos].month)
                    if m2d < 0:
                        m2d = 0
                recs.append({
                    "leg": leg, "code": c, "month": month,
                    "ret": float(rets[c]),
                    "m2d": m2d,
                    "crash": (c in crash) if c in delist_set else None,
                })
    df = pd.DataFrame(recs)
    df["bucket"] = df["m2d"].apply(lambda x: bucket_of(int(x) if x == x and x is not None else None))
    df["excess"] = df["ret"] - df["month"].map(ew_month)
    print(f"[attr] 持仓位 {len(df)} 个（多 {int((df.leg=='long').sum())} / 空 {int((df.leg=='short').sum())}）")

    # 各桶：占位比例、平均收益、平均超额
    print()
    print("=== 按「距退市月数」分桶 ===")
    print(f"{'桶':<10}{'多头占比':>9}{'空头占比':>9}{'多头收益':>10}{'空头收益':>10}{'多-空':>9}{'多头超额':>10}")
    tbl = {}
    order = ["0~3月", "4~12月", "13~24月", ">25月", "未退市"]
    for b in order:
        gl = df[(df.bucket == b) & (df.leg == "long")]
        gs = df[(df.bucket == b) & (df.leg == "short")]
        if len(gl) == 0 and len(gs) == 0:
            continue
        share_l = len(gl) / max(1, int((df.leg == "long").sum()))
        share_s = len(gs) / max(1, int((df.leg == "short").sum()))
        ml = gl["ret"].mean() if len(gl) else float("nan")
        ms = gs["ret"].mean() if len(gs) else float("nan")
        el = gl["excess"].mean() if len(gl) else float("nan")
        tbl[b] = {"share_long": share_l, "share_short": share_s,
                  "mean_long": ml, "mean_short": ms, "spread": ml - ms, "excess_long": el}
        print(f"{b:<10}{share_l*100:>8.1f}%{share_s*100:>8.1f}%{ml*100:>9.2f}%{ms*100:>9.2f}%"
              f"{(ml-ms)*100:>8.2f}%{el*100:>9.2f}%")

    # 崩盘式 vs 温和式
    print()
    print("=== 退市股再分：崩盘式（退市前 12 月 < −50%）vs 温和式 ===")
    dl = df[df.code.isin(delist_set)]
    for tag in (True, False):
        g = dl[dl.crash == tag]
        if len(g) == 0:
            continue
        gl = g[g.leg == "long"]
        gs = g[g.leg == "short"]
        print(f"  {'崩盘式' if tag else '温和式'}: 持仓位 {len(g)}（多 {len(gl)} / 空 {len(gs)}）"
              f"  多头收益 {gl['ret'].mean()*100:+.2f}%  空头收益 {gs['ret'].mean()*100:+.2f}%"
              f"  多-空 {(gl['ret'].mean()-gs['ret'].mean())*100:+.2f}%")

    # 时间结构：退市股进入多头腿时距退市的月数分布
    print()
    gl_dl = df[(df.leg == "long") & df.code.isin(delist_set)]["m2d"].dropna()
    if len(gl_dl):
        qs = gl_dl.quantile([0.25, 0.5, 0.75]).to_dict()
        print(f"=== 退市股进入多头腿时的「距退市月数」分布（n={len(gl_dl)}）===")
        print(f"  中位 {qs[0.5]:.0f} 月 | P25 {qs[0.25]:.0f} | P75 {qs[0.75]:.0f} | "
              f"≤3 月占 {(gl_dl<=3).mean()*100:.1f}%")

    # 追加：把 240 只退市股整体剔出池后，d 为什么会从 0.484 掉到 0.208？
    # 两种可能：(a) 它们自身贡献大（本脚本上面已否证：spread 与未退市组相同）；
    #           (b) 池从 532 缩到 292，k=110 的相对宽度变了（构造效应）。
    # 检验：幸存者池上把 k 按 k/N 比例缩放，看 d 是否回到 0.48 附近。
    surv = [c for c in close.columns if c not in delist_set]
    base_frac = K_MAIN / 317.0   # Gate 3 实测 k/n_pool 中位
    pos2 = month_end_positions(cal, START, END_HOLD)
    d_fixed, d_prop, fracs = [], [], []
    for i in range(len(pos2) - 1):
        pos, nxt = pos2[i], pos2[i + 1]
        if str(cal[pos].date()) > END_SIGNAL:
            continue
        sc = scores_at(pos, close[surv], lookback=LOOKBACK, skip=SKIP, min_valid_days=min_valid)
        if len(sc) < 3:
            continue
        rets = period_return(pos, nxt, close[surv].ffill())
        pool = sc.index.intersection(rets.dropna().index)
        ranked = sc.loc[pool].sort_values(ascending=True)
        for tag, kk in (("fixed", min(K_MAIN, len(pool) // 2)),
                        ("prop", max(1, min(int(round(base_frac * len(pool))), len(pool) // 2)))):
            short_k = int(min(kk, len(pool) // 2))
            if short_k < 1:
                continue
            rl = float(rets.loc[ranked.index[:short_k]].mean())
            rs = float(rets.loc[ranked.index[-short_k:]].mean())
            (d_fixed if tag == "fixed" else d_prop).append(0.5 * (rl - rs))
        fracs.append(len(pool))
    print()
    print("=== 幸存者池（292 只）上的构造效应检验 ===")
    print(f"  k=110 固定      : n={len(d_fixed)}  mean(d)={statistics.mean(d_fixed)*100:+.4f} pp"
          f"  k/N≈{K_MAIN/statistics.mean(fracs):.1%}")
    print(f"  k=比例({base_frac:.2f}×N): n={len(d_prop)}  mean(d)={statistics.mean(d_prop)*100:+.4f} pp")
    print(f"  参照：全池 532 只 k=110 = +0.4841 pp（Gate 3 主检验）")
    print(f"  读法：若「比例 k」把 d 拉回 0.48 附近，说明原 0.208 主要是 k/N 构造效应，不是退市股自身效应。")

    # 价格伪影检查：退市前 6 个月 ADJ/EXEC 比值稳定性
    print()
    print("=== 伪影检查：退市前 6 个月 ADJ/EXEC 比值 ===")
    ratios = []
    for c in delisted:
        adj = close[c].dropna().iloc[-6:]
        # EXEC 用未复权价（真价）
        p = f"data/exec_long/{c}.exec.csv"
        if not os.path.exists(p) or len(adj) < 3:
            continue
        ex = pd.read_csv(p, usecols=["date", "close"])
        ex["date"] = pd.to_datetime(ex["date"])
        ex = ex.set_index("date")["close"].astype(float)
        common = adj.index.intersection(ex.index)
        if len(common) < 3:
            continue
        r = (adj.loc[common] / ex.loc[common]).values
        if r[0] > 0:
            ratios.append(float(np.nanmax(r) / np.nanmin(r)))
    if ratios:
        print(f"  比值极差（max/min，6 个月内）中位 {statistics.median(ratios):.4f} | "
              f"P90 {np.percentile(ratios, 90):.4f} | 最大 {max(ratios):.4f}")
        print("  读法：非除权月内 ADJ/EXEC 应严格为常数，极差 > 1.01 说明末期有未登记的除权或数据跳变")

    out = {
        "window": [START, END_SIGNAL],
        "pool": int(close.shape[1]),
        "n_delisted": len(delisted),
        "delist_by_year": yr,
        "crash_share": {"n_crash": len(crash), "n_all": len(pre12),
                        "median_pre12": statistics.median(pre12.values()) if pre12 else None},
        "buckets": tbl,
        "m2d_quantiles": {str(k): float(v) for k, v in qs.items()} if len(gl_dl) else None,
        "m2d_le3_share": float((gl_dl <= 3).mean()) if len(gl_dl) else None,
        "adj_exec_ratio_range_median": statistics.median(ratios) if ratios else None,
        "n_positions": int(len(df)),
        "contribution_pp": {b: 0.5 * (v["share_long"] * v["mean_long"] - v["share_short"] * v["mean_short"]) * 100
                            for b, v in tbl.items()},
        "survivor_test": {"d_fixed_k110_pp": statistics.mean(d_fixed) * 100,
                          "d_prop_k_pp": statistics.mean(d_prop) * 100,
                          "median_n_pool": statistics.median(fracs)},
    }
    path = "docs/GATE3_DELIST_ATTRIBUTION_2026-10-20.json"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"\n[attr] report -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
