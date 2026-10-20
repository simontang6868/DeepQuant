#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Gate 3 设计前置：长样本（2005 起）逐月**池覆盖度**诊断。

只回答一个设计问题：**从哪一年开始，这个池才够宽，能支撑 k=110 的 quintile 构造？**
本脚本**不计算任何收益**（不产出任何收益/价差/收益类统计），因此不构成"跑前看结果"。

输出：
  逐月 n_pool（当日可算信号且可定价的标的数）、k_used（= min(110, n_pool//2)）、
  按年中位数、以及 n_pool >= 220（k=110 的两倍，保证两侧各约 1/4 以上）的月份占比。

用法：
  python scripts/gate3_pool_coverage.py --pool-dir data/qfq_rebuilt_broad_long
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage3_sigma_d import MIN_VALID_RATIO, load_close_matrix, month_end_positions, scores_at  # noqa: E402

LOOKBACK = 21
SKIP = 1
K_MAIN = 110


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Gate 3 池覆盖度诊断（不算收益）")
    ap.add_argument("--pool-dir", default="data/qfq_rebuilt_broad_long")
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument("--end", default="2026-10-31")
    ap.add_argument("--report", default="docs/GATE3_POOL_COVERAGE_2026-10-20.json")
    ap.add_argument("--exclude", default=None, help="逗号分隔的剔除代码（与研究池一致）")
    args = ap.parse_args(argv)

    close_df, _ = load_close_matrix(args.pool_dir)
    if args.exclude:
        ex = [c.strip().upper() for c in args.exclude.split(",") if c.strip()]
        drop = [c for c in ex if c in close_df.columns]
        close_df = close_df.drop(columns=drop)
        print(f"[cov] 按清单剔除 {len(drop)} 只")
    calendar = close_df.index
    positions = month_end_positions(calendar, args.start, args.end)
    min_valid = math.ceil(LOOKBACK * MIN_VALID_RATIO)

    rows = []
    for pos in positions:
        sc = scores_at(pos, close_df, lookback=LOOKBACK, skip=SKIP, min_valid_days=min_valid)
        n_pool = int(len(sc))
        rows.append(
            {
                "month": str(calendar[pos].date())[:7],
                "n_pool": n_pool,
                "k_used": int(min(K_MAIN, n_pool // 2)),
                "frac_used": round(min(K_MAIN, n_pool // 2) / n_pool, 4) if n_pool else None,
            }
        )

    df = pd.DataFrame(rows)
    by_year = []
    for y, g in df.groupby(df["month"].str[:4]):
        by_year.append(
            {
                "year": y,
                "months": int(len(g)),
                "n_pool_median": float(g["n_pool"].median()),
                "n_pool_min": int(g["n_pool"].min()),
                "k_used_median": float(g["k_used"].median()),
            }
        )

    print(f"[cov] 池 {close_df.shape[1]} 只 | 日历 {calendar[0].date()} ~ {calendar[-1].date()}")
    print(f"[cov] 月度信号日 {len(df)} 个（{df['month'].iloc[0]} ~ {df['month'].iloc[-1]}）")
    print(f"{'year':<6}{'m':>4}{'n_pool_med':>12}{'n_pool_min':>12}{'k_used_med':>12}")
    for r in by_year:
        print(f"{r['year']:<6}{r['months']:>4}{r['n_pool_median']:>12.0f}{r['n_pool_min']:>12}{r['k_used_median']:>12.0f}")

    for th in (220, 300, 400):
        n_ge = int((df["n_pool"] >= th).sum())
        first = df.loc[df["n_pool"] >= th, "month"].min() if n_ge else None
        print(f"[cov] n_pool >= {th}: {n_ge}/{len(df)} 个月, 首次达到 {first}")

    out = {
        "pool_dir": args.pool_dir,
        "n_symbols": int(close_df.shape[1]),
        "params": {"lookback": LOOKBACK, "skip": SKIP, "k_main": K_MAIN, "min_valid_ratio": MIN_VALID_RATIO},
        "n_months": int(len(df)),
        "months": rows,
        "by_year": by_year,
        "note": "本诊断只统计池覆盖与信号可用数，不含任何收益计算。",
    }
    os.makedirs(os.path.dirname(args.report) or ".", exist_ok=True)
    with open(args.report, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"[cov] report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
