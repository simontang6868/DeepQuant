#!/usr/bin/env python
"""MAX5 候选信号的**可行性检查**（不计算任何未来收益）。

## 这个脚本为什么不算收益

`docs/research/NEXT_SIGNAL_CANDIDATES_2026-10-21.md` §0.2 定了三条反 data-snooping 纪律：
先查文献 → 不许试跑挑因子 → 候选 ≤ 4。

**一算收益就变成"试跑挑因子"了。** 所以本脚本只回答五个"能不能算"的问题：

1. 日收益能算吗（ADJ 收盘是否连续、有无非正值）
2. 21 日窗口的有效日覆盖率（与阶段 C 同口径：≥19 日为有效）
3. MAX5 的横截面区分度（每月 MIN/P25/中位/P75/MAX）
4. MAX5 与 21 日波动率的横截面相关（**这是本次评估最关键的一项**）
5. 52 周高点（250 日）的窗口覆盖率

第 4 项的动机：低 MAX 的股票大概率是低波动股。Gate 4（低波动）已经 FAIL，
如果 MAX5 与波动率高度相关，那测 MAX5 很可能只是在重复 Gate 4。
**这个数字必须在预登记之前知道，不能等测完才发现。**

用法：
    python scripts/check_max5_feasibility.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from run_phase_c import END_HOLD, END_SIGNAL, MIN_LISTED_DAYS, Panels, START, build_mask, load_close  # noqa: E402
from stage3_sigma_d import month_end_positions  # noqa: E402

WIN = 21          # 21 个交易日
TOP_N = 5         # 取前 5 大日收益（A 股本地化口径）
HIGH_WIN = 250    # 52 周高点窗口
MIN_VALID = 19    # 21 日窗口内至少 19 个有效日（与阶段 C 一致）


def main() -> int:
    print("=" * 78)
    print("MAX5 可行性检查（不计算任何未来收益）")
    print("=" * 78)

    close = load_close()
    cal = close.index
    panels = Panels(close)
    positions = month_end_positions(cal, START, END_HOLD)
    mask = build_mask(close, MIN_LISTED_DAYS)
    ret = close.pct_change(fill_method=None)

    print(f"[数据] {close.shape[1]} 只 × {len(cal)} 天 ({cal[0].date()} ~ {cal[-1].date()})")
    print(f"[数据] 调仓点 {len(positions)} 个，窗口 {START} ~ {END_SIGNAL}")

    # ---- 1. 日收益可算性
    px = close.to_numpy()
    n_nonpos = int((px[np.isfinite(px)] <= 0).sum())
    r = ret.to_numpy()
    finite = np.isfinite(r)
    jump = float(np.nanmax(np.abs(r[finite]))) if finite.any() else float("nan")
    print()
    print("① 日收益可算性")
    print(f"   非正价个数 {n_nonpos}（应为 0）")
    print(f"   日收益 |r| 最大 {jump*100:.2f}%（含复牌/上市首日，非错误）")

    # ---- 2/3/4. 逐月：覆盖率、MAX5 分布、与波动率的相关
    cover, n_pool_med = [], []
    max5_q = {k: [] for k in ("min", "p25", "med", "p75", "max")}
    corr_vol, corr_rev, corr_high = [], [], []
    n_month = 0

    arr_r = r
    arr_close = close.to_numpy()
    cols = np.array(close.columns)

    for i in range(len(positions) - 1):
        pos = positions[i]
        sdate = str(cal[pos].date())
        if sdate > END_SIGNAL:
            continue
        if pos < HIGH_WIN or pos - WIN + 1 < 0:
            continue
        ok = mask.iloc[pos].to_numpy()
        if ok.sum() < 10:
            continue
        idx = np.where(ok)[0]
        win = arr_r[pos - WIN + 1: pos + 1][:, idx]        # (21, n_pool)
        valid_cnt = np.isfinite(win).sum(axis=0)
        good = valid_cnt >= MIN_VALID
        if good.sum() < 10:
            continue

        w = win[:, good]
        # 取每个股票最大的 TOP_N 个日收益
        w2 = np.where(np.isfinite(w), w, -np.inf)
        top = np.sort(w2, axis=0)[-TOP_N:, :]
        max5 = top.mean(axis=0)
        max5 = np.where(np.isfinite(max5), max5, np.nan)
        vol = np.nanstd(w, axis=0, ddof=1)                  # 21 日波动率
        # 21 日反转：close(pos-21)/close(pos-1) − 1，先按池取列再按 good 筛
        rev_full = arr_close[pos - WIN] / arr_close[pos - 1] - 1.0
        rev = rev_full[idx][good]

        m_good = np.isfinite(max5) & np.isfinite(vol)
        if m_good.sum() < 10:
            continue

        cover.append(float(good.sum()) / ok.sum())
        n_pool_med.append(int(ok.sum()))
        n_month += 1

        h = max5[m_good]
        max5_q["min"].append(float(np.min(h)))
        max5_q["p25"].append(float(np.percentile(h, 25)))
        max5_q["med"].append(float(np.median(h)))
        max5_q["p75"].append(float(np.percentile(h, 75)))
        max5_q["max"].append(float(np.max(h)))

        # Spearman（秩相关）：MAX5 vs 波动率 / 反转 / 52周高点距离
        s_m = pd.Series(h).rank()
        s_v = pd.Series(vol[m_good]).rank()
        if s_m.std() > 0 and s_v.std() > 0:
            corr_vol.append(float(np.corrcoef(s_m, s_v)[0, 1]))
        mrev = np.isfinite(rev) & np.isfinite(max5)
        if mrev.sum() > 10:
            a = pd.Series(max5[mrev]).rank()
            b = pd.Series(rev[mrev]).rank()
            if a.std() > 0 and b.std() > 0:
                corr_rev.append(float(np.corrcoef(a, b)[0, 1]))

        # 52 周高点距离（close / 过去 250 日最高）
        hi = arr_close[pos - HIGH_WIN + 1: pos + 1][:, idx[good]]
        hi_max = np.nanmax(hi, axis=0)
        pth = arr_close[pos][idx[good]] / hi_max
        pth = np.where(np.isfinite(pth) & (pth > 0), pth, np.nan)
        pm = np.isfinite(pth) & np.isfinite(max5)
        if pm.sum() > 10:
            a = pd.Series(max5[pm]).rank()
            b = pd.Series(pth[pm]).rank()
            if a.std() > 0 and b.std() > 0:
                corr_high.append(float(np.corrcoef(a, b)[0, 1]))

    print()
    print("② 21 日窗口有效日覆盖率")
    print(f"   逐月均值 {statistics.mean(cover)*100:.2f}%  最低 {min(cover)*100:.2f}%  "
          f"（阈值：窗口内 ≥{MIN_VALID}/{WIN} 日）")
    print(f"   参与月度 {n_month} 个，池规模中位 {statistics.median(n_pool_med):.0f} 只")

    print()
    print("③ MAX5 横截面分布（pp，逐月中位的均值）")
    for k in ("min", "p25", "med", "p75", "max"):
        print(f"   {k:<4} {statistics.mean(max5_q[k])*100:>+8.3f}")

    print()
    print("④ MAX5 与其他因子的横截面秩相关（逐月 Spearman 的均值）—— 本次评估最关键")
    if corr_vol:
        cv = statistics.mean(corr_vol)
        print(f"   vs 21 日波动率   {cv:+.4f}   （|r|>0.8 ⇒ 可能只是 Gate 4 的影子）")
    if corr_rev:
        print(f"   vs 21 日反转     {statistics.mean(corr_rev):+.4f}")
    if corr_high:
        print(f"   vs 52 周高点距离 {statistics.mean(corr_high):+.4f}")

    print()
    print("⑤ 250 日窗口（52 周高点）覆盖率")
    print(f"   已由 min_listed_days={MIN_LISTED_DAYS} 保证 ≥252 个有效交易日；"
          f"本检查在 pos≥{HIGH_WIN} 的月份上进行，共 {n_month} 个月")

    out = {
        "script": "check_max5_feasibility",
        "purpose": "只验变量可构造性，不计算任何未来收益（反 data-snooping）",
        "params": {"win": WIN, "top_n": TOP_N, "high_win": HIGH_WIN, "min_valid": MIN_VALID},
        "data": {"n_symbols": int(close.shape[1]), "n_days": int(len(cal)),
                 "range": [str(cal[0].date()), str(cal[-1].date())]},
        "R1_daily_return": {"n_nonpos_price": n_nonpos, "max_abs_ret": jump},
        "R2_coverage": {"mean": statistics.mean(cover), "min": min(cover),
                        "n_months": n_month, "n_pool_median": statistics.median(n_pool_med)},
        "R3_max5_distribution_pp": {k: statistics.mean(v) * 100 for k, v in max5_q.items()},
        "R4_rank_corr": {
            "vs_vol21": statistics.mean(corr_vol) if corr_vol else None,
            "vs_rev21": statistics.mean(corr_rev) if corr_rev else None,
            "vs_pth250": statistics.mean(corr_high) if corr_high else None,
            "note": "逐月横截面 Spearman 的算术平均；不是时间序列相关",
        },
        "verdict_hint": (
            "MAX5 与波动率的相关若 >0.8，必须先排除'只是 Gate 4 的影子'再预登记"
            if corr_vol and abs(statistics.mean(corr_vol)) > 0.8 else
            "MAX5 与波动率相关未达 0.8，可作为独立候选继续"),
    }
    os.makedirs("reports", exist_ok=True)
    p = "reports/MAX5_FEASIBILITY.json"
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print()
    print(f"[产物] {p}")
    print(f"[提示] {out['verdict_hint']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
