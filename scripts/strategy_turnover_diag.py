#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""补测：反转策略（Gate 2/3 构造）的**真实换手率**，以及"月度全换手"假设高估了多少成本。

## 为什么做这个

Gate 2/3 的成本档都写成「月度全换手，双边 = 2c」（c = 单边费率），即假设每月把整个组合换掉。
这是**保守假设**，但从来没验证过。4.1 里对"成交额前 k"这种稳定集合实测只有 25~34% 单边/月，
说明 100% 的假设可能高估 3 倍。反转策略按月重排、信号是 21 日收益，换手确实更高——
**但到底多高，必须测**。

## 口径（写死）

- 持仓：与 Gate 3 完全相同的构造（lookback=21, skip=1, k=110, 反转，多空各 0.5 权重）
- 换手（单边，占组合总值）：
    `TO = 0.5 × [0.5 × Σ_long|w_new − w_old_drift| + 0.5 × Σ_short|w_new − w_old_drift|]`
  其中 `w_old_drift` 是上月末权重按本月收益漂移后的值（含退出的置 0、新进的从 0 起）
- 成本：`2 × TO × c`（TO 已是单边成交额占比，买卖各一次 → ×2）
- 对照假设：`TO = 100%` → 成本 `2c/月 = 24c/年`

## 输出

- 各窗口的月度单边换手（均值/中位/P90）、年化换手、年化成本（c=0.1% / 0.3%）
- Gate 3 主结果在"实测换手成本"下的净效应（**描述性，不改判定**）

用法：
  python scripts/strategy_turnover_diag.py
"""

from __future__ import annotations

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
from run_gate3_longsample import EXCLUDED_SYMBOLS, K_MAIN, LOOKBACK, POOL_DIR, SKIP  # noqa: E402

WINDOWS = {
    "gate3_2007_2015": ("2007-01-01", "2015-12-31", "2016-02-29"),
    "gate2_2016_2026": ("2016-05-01", "2026-10-31", "2026-10-31"),
}
COST_TIERS = [0.001, 0.003]


def monthly_legs(close_df, calendar, positions):
    """逐月返回 (longs, shorts, rets_at_next)：与 Gate 3 的 run_combo 完全同一套选股。"""
    min_valid = math.ceil(LOOKBACK * MIN_VALID_RATIO)
    out = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sc = scores_at(pos, close_df, lookback=LOOKBACK, skip=SKIP, min_valid_days=min_valid)
        if len(sc) < 3:
            continue
        rets = period_return(pos, nxt, close_df.ffill())
        pool = sc.index.intersection(rets.dropna().index)
        kk = min(K_MAIN, len(pool) // 2)
        if kk < 1:
            continue
        ranked = sc.loc[pool].sort_values(ascending=True)  # 反转：低分在前
        out.append({
            "signal_date": str(calendar[pos].date()),
            "month": str(calendar[nxt].date()),
            "longs": list(ranked.index[:kk]),
            "shorts": list(ranked.index[-kk:]),
            "rets": rets.loc[pool],
            "k": kk,
        })
    return out


def turnover_of(curr: list[str], prev_w: dict[str, float], rets: pd.Series) -> float:
    """单边换手（占该腿总值）：|w_new − w_old_drift| 之和的一半。

    腿内等权，故 w_new = 1/k；`prev_w` 是上月末该腿各只的权重（腿内归一）。
    """
    if not curr:
        return float("nan")
    # 漂移：上月末权重 × (1+r)，再在"仍在本腿"的集合内归一
    drift = {}
    tot = 0.0
    for c, w in prev_w.items():
        r = rets.get(c, float("nan"))
        if not (r == r):
            continue  # 退市无价：该腿内按"无法交易"处理，不计漂移
        v = w * (1.0 + r)
        drift[c] = v
        tot += v
    if tot > 0:
        drift = {c: v / tot for c, v in drift.items()}
    w_new = 1.0 / len(curr)
    names = set(drift) | set(curr)
    return 0.5 * sum(abs((w_new if c in curr else 0.0) - drift.get(c, 0.0)) for c in names)


def main() -> int:
    close_all, _ = load_close_matrix(POOL_DIR)
    close_df = close_all.drop(columns=[c for c in EXCLUDED_SYMBOLS if c in close_all.columns])
    cal = close_df.index
    print(f"[TO] 池 {close_df.shape[1]} 只")

    for tag, (start, end_sig, end_hold) in WINDOWS.items():
        positions = month_end_positions(cal, start, end_hold)
        rows = [r for r in monthly_legs(close_df, cal, positions) if r["signal_date"] <= end_sig]
        if len(rows) < 3:
            print(f"[TO] {tag}: 观测不足")
            continue
        tos, to_long, to_short = [], [], []
        prev_long: list[str] = []
        prev_short: list[str] = []
        d_list = []
        for r in rows:
            w_long = {c: 1.0 / len(prev_long) for c in prev_long} if prev_long else {}
            w_short = {c: 1.0 / len(prev_short) for c in prev_short} if prev_short else {}
            tl = turnover_of(r["longs"], w_long, r["rets"])
            ts = turnover_of(r["shorts"], w_short, r["rets"])
            to_long.append(tl)
            to_short.append(ts)
            # 组合层：两条腿各占 0.5（自融资），组合单边换手 = 0.5×腿长 + 0.5×腿空
            tos.append(0.5 * tl + 0.5 * ts if (tl == tl and ts == ts) else float("nan"))
            prev_long, prev_short = r["longs"], r["shorts"]
            rl = sum(r["rets"].get(c, 0.0) for c in r["longs"]) / len(r["longs"])
            rs = sum(r["rets"].get(c, 0.0) for c in r["shorts"]) / len(r["shorts"])
            d_list.append(0.5 * (rl - rs))
        # 第一个月无上月持仓，剔除（建仓成本不计，两边一致）
        tos = [x for i, x in enumerate(tos) if i > 0 and x == x]
        to_long = [x for i, x in enumerate(to_long) if i > 0 and x == x]
        to_short = [x for i, x in enumerate(to_short) if i > 0 and x == x]
        mean_d = sum(d_list) / len(d_list)
        print()
        print(f"=== {tag}  n={len(rows)} ===")
        print(f"  单边换手/月  均值 {statistics.mean(tos)*100:.1f}%  中位 {statistics.median(tos)*100:.1f}%  "
              f"P90 {sorted(tos)[int(len(tos)*0.9)]*100:.1f}%  最大 {max(tos)*100:.1f}%")
        print(f"    多头腿 {statistics.mean(to_long)*100:.1f}%  空头腿 {statistics.mean(to_short)*100:.1f}%")
        print(f"  年化换手（单边）{statistics.mean(tos)*12*100:.0f}%   年化双边成交额 {2*statistics.mean(tos)*12*100:.0f}%")
        ref = "+0.4841（Gate 3，532 池）" if tag.startswith("gate3") else "-0.3982（Gate 2 修复后重跑，548 池）"
        print(f"  mean(d) = {mean_d*100:+.4f} pp/月   [参照 {ref}]")
        print("     注：本脚本用 532 只研究池，Gate 2 重跑用 548 只（只剔 600515），故 gate2 段差 0.006pp 属池差异。")
        for c in COST_TIERS:
            cost_m = 2 * statistics.mean(tos) * c * 100
            assumed = 2 * 1.0 * c * 100
            print(f"  成本档 单边 {c*100:.1f}%: 实测 {cost_m:.3f} pp/月（{cost_m*12:.2f} pp/年）"
                  f" vs 全换手假设 {assumed:.3f} pp/月（{assumed*12:.2f} pp/年）"
                  f" → 假设高估 {assumed/cost_m:.1f} 倍")
            net = mean_d * 100 - cost_m
            print(f"      净效应 {net:+.4f} pp/月（{net*12:+.2f} pp/年）"
                  f"  t≈{net/ (statistics.stdev(d_list)*100/math.sqrt(len(d_list))):+.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
