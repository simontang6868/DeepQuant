# -*- coding: utf-8 -*-
"""时间维度稳健性审计（专家第 6 轮 P0）：分段时间 μ/t + 滚动窗口 + 四行口径表。

⚠️ 起因：上一轮把"200 条路径的分布窄"当成稳健性证据——那是错的。
   200 条路径共享同一段历史与同一批股票，分布窄只说明"抽哪 5 只差异小"，
   不说明"换个时间段 alpha 还在"。本脚本只回答时间维度问题。

输出：`d4_alpha_out/a1801ece1ad7/time_check.json`
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RD = HERE.parents[1]
REPO = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

import p03d_d4_strict as p03d  # noqa: E402
import run_phase_c as C  # noqa: E402

OUT = RD / "d4_alpha_out" / "a1801ece1ad7"
MODERN = "20160101"
SEGMENTS = {
    "2016_2019": ("20160101", "20191231"),
    "2020_2022": ("20200101", "20221231"),
    "2023_2026": ("20230101", "20991231"),
}


def seg_stats(x: np.ndarray, months: list[str], lo: str, hi: str) -> dict:
    mask = np.asarray([lo <= m <= hi for m in months])
    v = np.asarray(x)[mask]
    v = v[np.isfinite(v)]
    n = len(v)
    if n < 6:
        return {"n": n, "note": "样本不足 6 月，不判"}
    mu = float(np.mean(v))
    sd = float(np.std(v, ddof=1))
    se = sd / math.sqrt(n)
    nw = C.newey_west(list(map(float, v)), 3)
    return {
        "n": n, "mu_pp": mu * 100, "sigma_pp": sd * 100,
        "t_iid": mu / se if se > 0 else float("nan"),
        "t_nw3": nw["t_nw"], "se_nw_pp": nw["se_nw"] * 100,
        "mde_pp": 2.8016 * nw["se_nw"] * 100,
        "share_positive": float(np.mean(v > 0)),
    }


def main() -> int:
    panel = pd.read_parquet(p03d.PANEL)
    by, months, paths, ns_list, monthly, _ = p03d.build_decisions(panel, 200, "d4")
    bench = np.asarray([float(np.mean(by[d]["fwd"])) for d in months], float)

    # 路径平均的净超额：C0（回测成本）与 C1（实盘成本）
    exc_c0 = np.vstack([np.asarray(r, float) - bench for r in monthly]).mean(axis=0)
    exc_c1 = np.vstack([np.asarray(r, float) - 0.0004 * np.asarray(ns_list[p], float) - bench
                        for p, r in enumerate(monthly)]).mean(axis=0)

    # ---- 1. 时间分段 ----
    segments = {}
    for name, (lo, hi) in SEGMENTS.items():
        segments[name] = {
            "C0": seg_stats(exc_c0, months, lo, hi),
            "C1": seg_stats(exc_c1, months, lo, hi),
        }
    segments["full_modern"] = {
        "C0": seg_stats(exc_c0, months, MODERN, "20991231"),
        "C1": seg_stats(exc_c1, months, MODERN, "20991231"),
    }

    # ---- 2. 滚动 36 月窗口（现代段）----
    rolling = []
    for i in range(len(months) - 36 + 1):
        m = months[i]
        if m < MODERN:
            continue
        w = exc_c1[i:i + 36]
        mu = float(np.mean(w))
        sd = float(np.std(w, ddof=1))
        se = sd / math.sqrt(36)
        nw = C.newey_west(list(map(float, w)), 3)
        rolling.append({
            "end_month": months[i + 35], "start_month": m,
            "mu_pp": mu * 100, "t_iid": mu / se if se > 0 else float("nan"),
            "t_nw3": nw["t_nw"],
        })
    t_list = np.asarray([r["t_iid"] for r in rolling])
    t_nw_list = np.asarray([r["t_nw3"] for r in rolling])
    roll_summary = {
        "window_months": 36, "n_windows": len(rolling),
        "t_iid": {"min": float(np.min(t_list)), "median": float(np.median(t_list)),
                  "p05": float(np.percentile(t_list, 5)), "max": float(np.max(t_list)),
                  "share_below_1": float(np.mean(t_list < 1.0)),
                  "share_below_1_96": float(np.mean(t_list < 1.96))},
        "t_nw3": {"min": float(np.min(t_nw_list)), "median": float(np.median(t_nw_list)),
                  "p05": float(np.percentile(t_nw_list, 5)), "max": float(np.max(t_nw_list)),
                  "share_below_1": float(np.mean(t_nw_list < 1.0))},
        "worst_5": sorted(rolling, key=lambda r: r["t_iid"])[:5],
        "best_5": sorted(rolling, key=lambda r: r["t_iid"])[-5:],
    }

    # ---- 3. 四行口径表（C0/C1 × 路径平均/单账户）----
    single_sigma = float(np.median([np.std(np.asarray(r, float)[np.asarray([m >= MODERN for m in months])] - bench[np.asarray([m >= MODERN for m in months])], ddof=1)
                                    for r in monthly]))
    sel = np.asarray([m >= MODERN for m in months])
    path_sigma = float(np.std(exc_c0[sel], ddof=1))
    four_rows = []
    for tag, mu in (("C0", 0.009464), ("C1", 0.008760)):
        for series, sd in (("超额·路径平均", path_sigma), ("超额·单账户", single_sigma)):
            four_rows.append({
                "cost": tag, "series": series,
                "mu_pp": mu * 100, "sigma_pp": sd * 100,
                "n_t2": (2.0 * sd / mu) ** 2,
                "n_mde": (2.8016 * sd / mu) ** 2,
            })

    # ---- 4. ok_buy 判定日证据（静态代码定位）----
    ok_buy_evidence = {
        "backtest": {
            "source": "research/stock_pick_2026-10-25/scripts/build_panel.py:156,169-175,194-198",
            "pos": "pos = np.searchsorted(dates, ME_arr, side='right') - 1  ⇒ ME 当日或之前最近交易日",
            "amt20": "f['amt20'][pos_c[idx]]  ⇒ iloc[pos]，即**决策日 ME 当天收盘**的 20 日滚动额",
            "ok_buy": "close.between(1,200) & f2.notna() & vol20.notna() & amt20>5e6",
        },
        "live": {
            "source": "deploy/systemd/deepquant-sim-exec.timer:5；scripts/sim_rebalance_executor.py:86",
            "exec_time": "2026-12-01 09:35（FIRST_ORDER_TIME=09:35）",
            "t_minus_1": "2026-11-30 = 决策日 ME 当天收盘 ⇒ 与回测 iloc[pos] **同源**",
        },
        "verdict": "同源（前提：12-01 09:35 的下单对应台账决策日 11-30，"
                   "且 11-30 为独立交易日历推导的月末交易日）",
        "caveat": "若决策日因日历顺延不是 11-30，需重新核对；不得按星期几猜",
    }

    res = {
        "schema": "p03f_d4_alpha_time/v1",
        "prereg_sha256": "a1801ece1ad7",
        "note": "时间维度稳健性；200 条路径的分布窄**不是**稳健性证据（共享同一段历史）",
        "segments": segments,
        "rolling_36m": roll_summary,
        "four_row_table": four_rows,
        "ok_buy_same_source": ok_buy_evidence,
        "retraction": {
            "previous_claim": "路径异质性小（负值仅 0.5%）⇒ 单账户落在差路径上的风险不极端",
            "status": "撤出稳健性论证",
            "reason": "200 条路径共享同一段历史与同一批股票，分布窄只说明抽哪 5 只差异小，"
                      "不说明跨时间段/跨池/跨 k 稳健；该分布仍保留为'路径选择风险'的描述，"
                      "不作为'alpha 稳健'的证据",
        },
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "time_check.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    print("=== 时间分段（超额·路径平均，pp/月）===")
    for name, d in segments.items():
        for tag in ("C0", "C1"):
            r = d[tag]
            if "n" not in r:
                continue
            print(f"  {name:<12} {tag} n={r['n']:>3} μ={r['mu_pp']:+.4f} "
                  f"σ={r['sigma_pp']:.3f}% t_iid={r['t_iid']:+.2f} t_NW3={r['t_nw3']:+.2f} "
                  f"正收益月={r['share_positive']*100:.0f}%")
    r = roll_summary
    print(f"\n=== 滚动 36 月窗口（C1，共 {r['n_windows']} 个）===")
    for k in ("t_iid", "t_nw3"):
        v = r[k]
        print(f"  {k}: min {v['min']:+.2f} | p05 {v['p05']:+.2f} | 中位 {v['median']:+.2f} "
              f"| max {v['max']:+.2f} | t<1 占 {v['share_below_1']*100:.0f}%")
    print("\n=== 四行口径表 ===")
    for row in four_rows:
        print(f"  {row['cost']:<3} {row['series']:<12} μ={row['mu_pp']:.4f} σ={row['sigma_pp']:.3f}% "
              f"n(t2)={row['n_t2']:.0f} n(MDE)={row['n_mde']:.0f}")
    print(f"\n→ {OUT / 'time_check.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
