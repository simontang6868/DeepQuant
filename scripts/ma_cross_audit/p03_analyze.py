# -*- coding: utf-8 -*-
"""MA-CROSS 独立复核 / P0-2 + P0-3 + P0-4：对账、v3 对比、逐窗配对。

输入（只读）：
  ../b50_dist/step75_research_summary.json     原始 v2 主结果（哈希已核）
  ma_cross_audit/runs/v2/step75_research_summary.json   本次复现 v2
  ma_cross_audit/runs/v3/step75_research_summary.json   本次 v3（启用涨跌停）
  ma_cross_audit/runs/{v2,v3}/windows/*/windows.csv     逐窗明细（W1）

输出：
  reports/MA_CROSS_V2_RECONCILIATION.csv
  reports/MA_CROSS_V2_WINDOW_RESULTS.csv
  reports/MA_CROSS_V3_WINDOW_RESULTS.csv
  reports/MA_CROSS_V2_V3_COMPARISON.csv
  reports/MA_CROSS_PAIRED_WINDOW_RESULTS.csv
  ma_cross_audit/analysis_summary.json      （供报告引用的全部数字）
"""
from __future__ import annotations

import glob
import json
import os
import statistics as st

import numpy as np
import pandas as pd

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
OUTSIDE = r"D:/DeepQuant power by txm"
AUDIT = os.path.join(ROOT, "ma_cross_audit")
REPORTS = os.path.join(ROOT, "reports")
ORIG = os.path.join(OUTSIDE, "b50_dist/step75_research_summary.json")

FEES = ["simulation_placeholder", "broker_reference_common_2024", "research_scenario_high_cost"]
FEE_SHORT = {"simulation_placeholder": "sim", "broker_reference_common_2024": "broker",
             "research_scenario_high_cost": "high_cost"}

WIN_COLS = [
    "symbol", "strategy_name", "fee_source", "window_index", "oos_start", "oos_end",
    "params_json", "strategy_cumulative_return", "strategy_max_drawdown", "trade_count",
    "total_fees", "benchmark_cumulative_return", "excess_return", "price_limit_mode",
    "status", "selection_success", "oos_success", "oos_is_complete", "benchmark_executable",
    "design_version", "window_key",
]
COMPARE_FIELDS = ["n_windows", "n_sel_ok", "n_oos_ok", "positive_ratio", "median_ret",
                  "median_mdd", "total_trades", "total_fees", "transition_rate"]


def load_summary(path: str) -> dict:
    j = json.load(open(path, encoding="utf-8"))
    out = {}
    for r in j["results"]:
        out[(r["symbol"], r["strategy"], r["fee_source"])] = r
    return out


def load_windows(tag: str) -> pd.DataFrame:
    files = glob.glob(os.path.join(AUDIT, "runs", tag, "windows", "*", "windows.csv"))
    frames = []
    for f in files:
        try:
            df = pd.read_csv(f, usecols=WIN_COLS)
        except Exception:
            continue
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=WIN_COLS)
    return pd.concat(frames, ignore_index=True)


def agg_cells(d: dict, strat: str) -> dict:
    """按费率档汇总（中位数口径，与 B18/B20 一致）。"""
    out = {}
    for fee in FEES:
        sub = [r for (s, stg, f), r in d.items() if stg == strat and f == fee]
        if not sub:
            continue
        out[fee] = {
            "n": len(sub),
            "median_of_median_ret": st.median([r["median_ret"] for r in sub]),
            "median_of_loss_ratio": st.median([
                (r.get("window_stats") or {}).get("loss_window_ratio") for r in sub]),
            "median_of_positive_ratio": st.median([r["positive_ratio"] for r in sub]),
            "median_n_oos_ok": st.median([r["n_oos_ok"] for r in sub]),
            "median_total_trades": st.median([r["total_trades"] for r in sub]),
            "median_total_fees": st.median([r["total_fees"] for r in sub]),
        }
    return out


def cluster_bootstrap(df: pd.DataFrame, stat: str = "mean", n_boot: int = 2000,
                      seed: int = 20261027) -> tuple[float, float, float]:
    """按**股票**聚类的 bootstrap（窗口之间高度相关，不能按窗口独立重抽样）。"""
    rng = np.random.default_rng(seed)
    groups = [g["excess_return"].to_numpy() for _, g in df.groupby("symbol")]
    if not groups:
        return (float("nan"),) * 3
    point = (st.mean([g.mean() for g in groups]) if stat == "mean"
             else st.median([np.median(g) for g in groups]))
    boots = []
    k = len(groups)
    for _ in range(n_boot):
        idx = rng.integers(0, k, k)
        sample = [groups[i] for i in idx]
        vals = [g.mean() if stat == "mean" else float(np.median(g)) for g in sample]
        boots.append(st.mean(vals) if stat == "mean" else st.median(vals))
    lo, hi = np.percentile(boots, [2.5, 97.5])
    return float(point), float(lo), float(hi)


def main() -> int:
    os.makedirs(REPORTS, exist_ok=True)
    summary = {}

    # ---------------- P0-2：v2 对账 ----------------
    orig = load_summary(ORIG)
    v2 = load_summary(os.path.join(AUDIT, "runs/v2/step75_research_summary.json"))
    rows, mism = [], []
    for k in sorted(orig):
        o, n = orig[k], v2.get(k)
        if n is None:
            mism.append({"key": "|".join(k), "field": "MISSING_IN_REPRO", "orig": "", "repro": ""})
            continue
        rec = {"symbol": k[0], "strategy": k[1], "fee": k[2]}
        for f in COMPARE_FIELDS:
            ov, nv = o.get(f), n.get(f)
            rec[f"{f}_orig"] = ov
            rec[f"{f}_repro"] = nv
            same = (ov == nv) if not isinstance(ov, float) else abs((ov or 0) - (nv or 0)) < 1e-12
            rec[f"{f}_equal"] = bool(same)
            if not same:
                mism.append({"key": "|".join(k), "field": f, "orig": ov, "repro": nv})
        rows.append(rec)
    cf = pd.DataFrame(rows)
    cf.to_csv(os.path.join(REPORTS, "MA_CROSS_V2_RECONCILIATION.csv"),
              index=False, encoding="utf-8-sig")

    n_cells = len(cf)
    n_field_cmp = n_cells * len(COMPARE_FIELDS)
    n_equal = int(sum(cf[f"{f}_equal"].sum() for f in COMPARE_FIELDS))
    eq_by_field = {f: int(cf[f"{f}_equal"].sum()) for f in COMPARE_FIELDS}
    summary["p02"] = {
        "n_cells": n_cells, "n_field_cmp": n_field_cmp, "n_equal": n_equal,
        "equal_ratio": n_equal / n_field_cmp if n_field_cmp else None,
        "by_field": eq_by_field, "n_mismatch": len(mism),
        "mismatch_sample": mism[:10],
        "orig_agg_ma": agg_cells(orig, "ma_cross"),
        "repro_agg_ma": agg_cells(v2, "ma_cross"),
        "orig_agg_mr": agg_cells(orig, "mean_reversion"),
        "repro_agg_mr": agg_cells(v2, "mean_reversion"),
    }

    # ---------------- 逐窗明细 ----------------
    w2 = load_windows("v2")
    w3 = load_windows("v3")
    for tag, w in (("v2", w2), ("v3", w3)):
        p = os.path.join(REPORTS, f"MA_CROSS_{tag.upper()}_WINDOW_RESULTS.csv")
        w.to_csv(p, index=False, encoding="utf-8-sig")
        summary.setdefault("windows", {})[tag] = {
            "n_rows": int(len(w)),
            "n_symbols": int(w["symbol"].nunique()) if len(w) else 0,
            "price_limit_modes": sorted(w["price_limit_mode"].dropna().unique().tolist()) if len(w) else [],
            "design_versions": sorted(w["design_version"].dropna().unique().tolist()) if len(w) else [],
            "n_zero_trade_windows": int((w["trade_count"].fillna(0) == 0).sum()) if len(w) else 0,
        }

    # ---------------- P0-3：v3 vs v2 ----------------
    v3p = os.path.join(AUDIT, "runs/v3/step75_research_summary.json")
    if os.path.exists(v3p):
        v3 = load_summary(v3p)
        cmp_rows = []
        for k in sorted(set(v2) & set(v3)):
            a, b = v2[k], v3[k]
            cmp_rows.append({
                "symbol": k[0], "strategy": k[1], "fee": k[2],
                "v2_median_ret": a["median_ret"], "v3_median_ret": b["median_ret"],
                "d_median_ret_pp": (b["median_ret"] - a["median_ret"]) * 100,
                "v2_positive_ratio": a["positive_ratio"], "v3_positive_ratio": b["positive_ratio"],
                "v2_total_trades": a["total_trades"], "v3_total_trades": b["total_trades"],
                "d_trades": b["total_trades"] - a["total_trades"],
                "v2_total_fees": a["total_fees"], "v3_total_fees": b["total_fees"],
                "d_fees": b["total_fees"] - a["total_fees"],
                "v2_loss_ratio": (a.get("window_stats") or {}).get("loss_window_ratio"),
                "v3_loss_ratio": (b.get("window_stats") or {}).get("loss_window_ratio"),
                "v2_n_oos_ok": a["n_oos_ok"], "v3_n_oos_ok": b["n_oos_ok"],
            })
        cdf = pd.DataFrame(cmp_rows)
        cdf.to_csv(os.path.join(REPORTS, "MA_CROSS_V2_V3_COMPARISON.csv"),
                   index=False, encoding="utf-8-sig")
        summary["p03"] = {
            "v2_agg_ma": agg_cells(v2, "ma_cross"),
            "v3_agg_ma": agg_cells(v3, "ma_cross"),
            "n_cells": len(cdf),
            "ma_cells": int((cdf["strategy"] == "ma_cross").sum()),
            "sign_flip_cells": int(((cdf["v2_median_ret"] < 0) & (cdf["v3_median_ret"] > 0)).sum()),
            "median_d_ret_pp_ma": float(cdf[cdf["strategy"] == "ma_cross"]["d_median_ret_pp"].median()),
            "median_d_trades_ma": float(cdf[cdf["strategy"] == "ma_cross"]["d_trades"].median()),
            "n_cells_trades_down": int((cdf[cdf["strategy"] == "ma_cross"]["d_trades"] < 0).sum()),
            "n_cells_trades_up": int((cdf[cdf["strategy"] == "ma_cross"]["d_trades"] > 0).sum()),
        }

    # ---------------- P0-4：逐窗配对（ma_cross） ----------------
    paired = {}
    for tag, w in (("v2", w2), ("v3", w3)):
        if not len(w):
            continue
        base = w[w["strategy_name"] == "ma_cross"].copy()
        # 有效窗口条件与官方 window_return_stats 完全一致：
        #   selection_success & oos_success & final_equity 非空 & oos_is_complete
        valid = base[
            (base["selection_success"] == True) & (base["oos_success"] == True)   # noqa: E712
            & base["strategy_cumulative_return"].notna()
            & (base["oos_is_complete"] == True)                                   # noqa: E712
        ].copy()
        n_invalid = int(len(base) - len(valid))
        # 配对还需要基准可执行（买不起 1 手 → 基准不存在，无法配对）
        d = valid[valid["benchmark_executable"] == True].copy()                   # noqa: E712
        n_no_benchmark = int(len(valid) - len(d))
        out = {"n_windows": int(len(d)), "n_symbols": int(d["symbol"].nunique()),
               "n_invalid_windows": n_invalid, "n_windows_without_benchmark": n_no_benchmark,
               "by_fee": {}}
        for fee in FEES:
            sub = d[d["fee_source"] == fee]
            if not len(sub):
                continue
            m, lo, hi = cluster_bootstrap(sub, "mean")
            med, mlo, mhi = cluster_bootstrap(sub, "median")
            stock_med = sub.groupby("symbol")["excess_return"].median()
            out["by_fee"][fee] = {
                "n_windows": int(len(sub)), "n_symbols": int(sub["symbol"].nunique()),
                "pooled_mean": float(sub["excess_return"].mean()),
                "pooled_median": float(sub["excess_return"].median()),
                "stock_level_median_of_medians": float(stock_med.median()),
                "stock_level_mean_of_medians": float(stock_med.mean()),
                "cluster_boot_mean": [m, lo, hi],
                "cluster_boot_median": [med, mlo, mhi],
                "n_pos": int((sub["excess_return"] > 0).sum()),
                "n_neg": int((sub["excess_return"] < 0).sum()),
                "n_zero": int((sub["excess_return"] == 0).sum()),
                "share_neg": float((sub["excess_return"] < 0).mean()),
                "median_trades_per_window": float(sub["trade_count"].fillna(0).median()),
                "zero_trade_share": float((sub["trade_count"].fillna(0) == 0).mean()),
                "strategy_median": float(sub["strategy_cumulative_return"].median()),
                "benchmark_median": float(sub["benchmark_cumulative_return"].median()),
            }
        # 合并三档
        m, lo, hi = cluster_bootstrap(d, "mean")
        med, mlo, mhi = cluster_bootstrap(d, "median")
        sm = d.groupby("symbol")["excess_return"].median()
        out["all_fees"] = {
            "n_windows": int(len(d)), "n_symbols": int(d["symbol"].nunique()),
            "pooled_mean": float(d["excess_return"].mean()),
            "pooled_median": float(d["excess_return"].median()),
            "stock_level_median_of_medians": float(sm.median()),
            "cluster_boot_mean": [m, lo, hi], "cluster_boot_median": [med, mlo, mhi],
            "n_pos": int((d["excess_return"] > 0).sum()),
            "n_neg": int((d["excess_return"] < 0).sum()),
            "share_neg": float((d["excess_return"] < 0).mean()),
        }
        paired[tag] = out
    summary["p04"] = paired

    if paired.get("v2"):
        keep = ["symbol", "fee_source", "window_index", "oos_start", "oos_end", "params_json",
                "strategy_cumulative_return", "benchmark_cumulative_return", "excess_return",
                "trade_count", "total_fees", "window_key", "price_limit_mode"]
        pw = w2[(w2["strategy_name"] == "ma_cross")][keep].copy()
        pw["design"] = "v2"
        if len(w3):
            pw3 = w3[(w3["strategy_name"] == "ma_cross")][keep].copy()
            pw3["design"] = "v3"
            pw = pd.concat([pw, pw3], ignore_index=True)
        pw.to_csv(os.path.join(REPORTS, "MA_CROSS_PAIRED_WINDOW_RESULTS.csv"),
                  index=False, encoding="utf-8-sig")

    with open(os.path.join(AUDIT, "analysis_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)

    # ---------------- 控制台摘要 ----------------
    print("=== P0-2 复现对账 ===")
    print(f"  单元 {n_cells} × 字段 {len(COMPARE_FIELDS)} = {n_field_cmp} 项，"
          f"逐位一致 {n_equal}（{n_equal/n_field_cmp*100:.2f}%），不一致 {len(mism)}")
    print("  分字段:", eq_by_field)
    print("=== P0-4 配对（ma_cross）===")
    for tag in ("v2", "v3"):
        if tag not in paired:
            continue
        a = paired[tag].get("all_fees", {})
        print(f"  {tag}: 窗口 {a.get('n_windows')} / 股票 {a.get('n_symbols')} | "
              f"配对差 pooled median {a.get('pooled_median', float('nan'))*100:+.2f}pp | "
              f"股票级中位 {a.get('stock_level_median_of_medians', float('nan'))*100:+.2f}pp | "
              f"聚类 CI(mean) [{a['cluster_boot_mean'][1]*100:+.2f}, {a['cluster_boot_mean'][2]*100:+.2f}] | "
              f"负占比 {a.get('share_neg', float('nan'))*100:.1f}%")
    if "p03" in summary:
        p = summary["p03"]
        print("=== P0-3 v3 vs v2（ma_cross）===")
        for fee in FEES:
            if fee in p["v2_agg_ma"] and fee in p["v3_agg_ma"]:
                print(f"  {FEE_SHORT[fee]:10s} v2 {p['v2_agg_ma'][fee]['median_of_median_ret']*100:+.2f}% "
                      f"→ v3 {p['v3_agg_ma'][fee]['median_of_median_ret']*100:+.2f}% | "
                      f"亏损窗 {p['v2_agg_ma'][fee]['median_of_loss_ratio']:.3f}→"
                      f"{p['v3_agg_ma'][fee]['median_of_loss_ratio']:.3f}")
        print(f"  符号翻转单元 {p['sign_flip_cells']} | 成交下降单元 {p['n_cells_trades_down']} / "
              f"上升 {p['n_cells_trades_up']}")
    print("[saved] analysis_summary.json 与各 CSV")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
