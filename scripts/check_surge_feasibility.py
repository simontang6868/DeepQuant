#!/usr/bin/env python
"""异常放量 SURGE 的可行性检查（**只验变量，不算任何未来收益**）。

## 为什么先做这个

纪律（候选文档 §0.2）：不许"跑一下看哪个 t 高"再决定测哪个。
所以本脚本只回答三个问题：

1. SURGE 能不能算？覆盖率多少？
2. 横截面区分度够不够（若大家的 SURGE 都≈1，五分位就没内容）？
3. ⚠️ **SURGE 与 MAX5 / 波动率 / 21 日反转的共线性有多高？**
   —— MAX5 与波动率秩相关 0.88，差点让阶段 F 变成"波动率的影子"。
   若 SURGE 与它们高度共线，那它不是新信号，本轮就该停。

**本脚本不计算任何未来收益**，不构成"试跑"。

用法：
    python scripts/check_surge_feasibility.py
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_c import (  # noqa: E402
    END_HOLD, MIN_LISTED_DAYS, START, build_mask, load_close,
)
from run_phase_j_impact import load_dollar_volume  # noqa: E402
from stage3_sigma_d import month_end_positions  # noqa: E402

WIN = 21                 # 近 21 日
WIN_PRIOR = 21           # 前 21 日
MIN_VALID = 19           # 与阶段 F 一致


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print("异常放量 SURGE 可行性检查（不算未来收益）")
    print("=" * 78)

    close = load_close()
    cal = close.index
    mask = build_mask(close, MIN_LISTED_DAYS)
    positions = month_end_positions(cal, START, END_HOLD)
    ret = close.pct_change(fill_method=None).to_numpy(dtype="float32", copy=True)
    arr_c = close.to_numpy()
    cols = np.array(close.columns)
    dv = load_dollar_volume().reindex(index=close.index, columns=close.columns).to_numpy()
    del close

    cover, n_pool_m, dist_q = [], [], defaultdict(list)
    corr_vol, corr_max5, corr_rev = [], [], []
    months = 0
    for pos in positions:
        sdate = str(cal[pos].date())
        if sdate > "2026-09-30" or pos - WIN - WIN_PRIOR + 1 < 0:
            continue
        ok = mask.iloc[pos].to_numpy()
        if ok.sum() < 100:
            continue
        idx = np.where(ok)[0]
        cur = dv[pos - WIN + 1: pos + 1][:, idx]
        pri = dv[pos - WIN - WIN_PRIOR + 1: pos - WIN + 1][:, idx]
        with np.errstate(invalid="ignore", divide="ignore"):
            m_cur = np.nanmean(np.where(cur > 0, cur, np.nan), axis=0)
            m_pri = np.nanmean(np.where(pri > 0, pri, np.nan), axis=0)
            surge = m_cur / m_pri
        good = np.isfinite(surge) & (surge > 0) & (m_pri > 0)
        if good.sum() < 100:
            continue
        cover.append(float(good.sum()) / ok.sum())
        n_pool_m.append(int(ok.sum()))
        months += 1

        s = surge[good]
        dist_q["min"].append(float(np.min(s)))
        dist_q["p25"].append(float(np.percentile(s, 25)))
        dist_q["med"].append(float(np.median(s)))
        dist_q["p75"].append(float(np.percentile(s, 75)))
        dist_q["max"].append(float(np.max(s)))

        sub = idx[good]
        w = ret[pos - WIN + 1: pos + 1][:, sub]
        vol = np.nanstd(np.where(np.isfinite(w), w, np.nan), axis=0, ddof=1)
        top5 = np.sort(np.where(np.isfinite(w), w, -np.inf), axis=0)[-5:, :].mean(axis=0)
        rev = arr_c[pos, sub] / arr_c[pos - WIN, sub] - 1.0          # 过去 21 日收益

        corr_vol.append(spearman(s, vol))
        corr_max5.append(spearman(s, top5))
        finite = np.isfinite(rev)
        if finite.sum() > 50:
            corr_rev.append(spearman(s[finite], rev[finite]))

    def q(v, p):
        s = sorted(v)
        return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]

    print()
    print("① 覆盖率与样本")
    print(f"   月份 {months}   池中位 {statistics.median(n_pool_m):.0f} 只")
    print(f"   SURGE 可算比例：中位 {statistics.median(cover)*100:.2f}%   "
          f"最低 {min(cover)*100:.2f}%")

    print()
    print("② 横截面分布（逐月中位，再看中位数的分布）")
    for k in ("min", "p25", "med", "p75", "max"):
        v = dist_q[k]
        print(f"   {k:<4} 中位 {statistics.median(v):.3f}   "
              f"(月度值范围 {min(v):.3f} ~ {max(v):.3f})")

    print()
    print("③ ⚠️ 共线性（Spearman 秩相关，逐月中位）")
    rows = [("波动率(21日)", corr_vol), ("MAX5", corr_max5), ("21日反转(过去收益)", corr_rev)]
    out_corr = {}
    for name, v in rows:
        m = statistics.median(v)
        out_corr[name] = round(m, 6)
        flag = "  ⚠️ 高" if abs(m) > 0.8 else ("  ⚠️ 偏高" if abs(m) > 0.6 else "")
        print(f"   SURGE vs {name:<20} {m:+.4f}{flag}")

    verdict = {
        "script": "check_surge_feasibility",
        "purpose": "只验变量可构造性与共线性；**未计算任何未来收益**",
        "definition": "SURGE = mean(近21日成交额) / mean(前21日成交额)",
        "n_months": months,
        "n_pool_median": statistics.median(n_pool_m),
        "coverage_median": round(statistics.median(cover), 6),
        "coverage_min": round(min(cover), 6),
        "distribution": {k: {"median": round(statistics.median(v), 6),
                             "min": round(min(v), 6), "max": round(max(v), 6)}
                         for k, v in dist_q.items()},
        "spearman_median": out_corr,
        "collinearity_warning": {k: bool(abs(v) > 0.8) for k, v in out_corr.items()},
        "elapsed_sec": round(time.time() - t0, 1),
    }
    os.makedirs("reports", exist_ok=True)
    p = "reports/SURGE_FEASIBILITY.json"
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(verdict, ensure_ascii=False, indent=1) + "\n")
    print()
    print(f"[产物] {p}   耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
