#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Gate 3 描述性诊断：多空两腿拆解 + 退市股占比 + 剔除退市股后的效应量。

**不参与判定**（预登记 §4：补充报告不得推翻主判定）。目的只有一个：
Gate 2 报告里最关键的机制发现是"多头腿 42.6% 是最终退市的股票"，
本实验必须给出同样口径的数字，否则无法判断 2007-2015 的正效应是不是同一机制的镜像。

用法：
  python scripts/gate3_leg_diag.py
"""

from __future__ import annotations

import glob
import math
import os
import statistics
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage3_sigma_d import (  # noqa: E402
    MIN_VALID_RATIO,
    load_close_matrix,
    month_end_positions,
    period_return,
    scores_at,
)
from run_gate3_longsample import (  # noqa: E402
    END_HOLD,
    END_SIGNAL,
    EXCLUDED_SYMBOLS,
    K_MAIN,
    LOOKBACK,
    POOL_DIR,
    SKIP,
    START,
    stats_of,
)


def delisted_set() -> set[str]:
    return {os.path.basename(p).replace(".qfq.csv", "") for p in glob.glob("data/qfq_mult/delist/*.qfq.csv")}


def run(close_df, close_ffill, calendar, positions):
    min_valid = math.ceil(LOOKBACK * MIN_VALID_RATIO)
    out = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sc = scores_at(pos, close_df, lookback=LOOKBACK, skip=SKIP, min_valid_days=min_valid)
        if len(sc) < 3:
            continue
        rets = period_return(pos, nxt, close_ffill)
        pool = sc.index.intersection(rets.dropna().index)
        kk = min(K_MAIN, len(pool) // 2)
        if kk < 1:
            continue
        r_pool = rets.loc[pool]
        ranked = sc.loc[pool].sort_values(ascending=True)  # 反转：低分在前
        longs = list(ranked.index[:kk])
        shorts = list(ranked.index[-kk:])
        out.append(
            {
                "signal_date": str(calendar[pos].date()),
                "n_pool": len(pool),
                "k_used": kk,
                "r_long": float(r_pool.loc[longs].mean()),
                "r_short": float(r_pool.loc[shorts].mean()),
                "longs": longs,
                "shorts": shorts,
            }
        )
    return out


def main() -> int:
    close_all, _ = load_close_matrix(POOL_DIR)
    close_df = close_all.drop(columns=[c for c in EXCLUDED_SYMBOLS if c in close_all.columns])
    cal = close_df.index
    ff = close_df.ffill()
    positions = month_end_positions(cal, START, END_HOLD)
    rows = [r for r in run(close_df, ff, cal, positions) if r["signal_date"] <= END_SIGNAL]

    dl = delisted_set()
    d = [0.5 * (r["r_long"] - r["r_short"]) for r in rows]
    st = stats_of(d)
    m_long = sum(r["r_long"] for r in rows) / len(rows)
    m_short = sum(r["r_short"] for r in rows) / len(rows)

    long_slots = sum(len(r["longs"]) for r in rows)
    short_slots = sum(len(r["shorts"]) for r in rows)
    long_dl = sum(1 for r in rows for c in r["longs"] if c in dl)
    short_dl = sum(1 for r in rows for c in r["shorts"] if c in dl)

    # 剔除退市股：把退市标的从池里整体拿掉后重算（不是只从腿里剔除，避免条件选择）
    keep = [c for c in close_df.columns if c not in dl]
    df2 = close_df[keep]
    ff2 = df2.ffill()
    rows2 = [r for r in run(df2, ff2, cal, positions) if r["signal_date"] <= END_SIGNAL]
    st2 = stats_of([0.5 * (r["r_long"] - r["r_short"]) for r in rows2])

    print("=== Gate 3 描述性诊断（不参与判定）===")
    print(f"  主检验         : n={st['n']}  mean(d)={st['mean_d']*100:+.4f}pp  t={st['t_stat']:+.4f}")
    print(f"  多头腿均值     : {m_long*100:+.4f}pp/月")
    print(f"  空头腿均值     : {m_short*100:+.4f}pp/月")
    print(f"  多头腿中最终退市股占比 : {long_dl}/{long_slots} = {long_dl/long_slots*100:.1f}%")
    print(f"  空头腿中最终退市股占比 : {short_dl}/{short_slots} = {short_dl/short_slots*100:.1f}%")
    print(f"  池内退市股只数 : {sum(1 for c in close_df.columns if c in dl)} / {close_df.shape[1]}  （剔除退市股后池 {len(keep)} 只）")
    print(f"  剔除退市股后   : n={st2['n']}  mean(d)={st2['mean_d']*100:+.4f}pp  t={st2['t_stat']:+.4f}")
    print(f"  退市股贡献     : {(st['mean_d']-st2['mean_d'])*100:+.4f}pp"
          f"（占主效应 {(st['mean_d']-st2['mean_d'])/st['mean_d']*100:.1f}%）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
