#!/usr/bin/env python
"""A2 收益时间集中度分析（任务 §四）。输入：冻结 phases/phasei monthly.csv。

全部结果为**诊断性/描述性**，不参与任何判定；阶段 T 的 6 个极端月名单原样使用。
输出：out/a2_time/（concentration.json / yearly.csv / rolling.csv / monthly_table.csv / run.log）
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_common as A  # noqa: E402

OUT = os.path.join(A.OUT_DIR, "a2_time")
MODERN_LO = "2016-01-01"
DROP6 = set(A.DROP6)
ROLL = 60


def dist_stats(x, name):
    a = np.asarray([v for v in x if v == v], dtype=float)
    n = a.size
    pos = float((a > 0).sum()) / n
    srt = np.sort(a)
    return {
        "series": name, "n": n, "mean_pp": float(a.mean()), "median_pp": float(np.median(a)),
        "std_pp": float(a.std(ddof=1)), "min_pp": float(a.min()), "max_pp": float(a.max()),
        "p10_pp": float(np.quantile(a, 0.10)), "p25_pp": float(np.quantile(a, 0.25)),
        "p75_pp": float(np.quantile(a, 0.75)), "p90_pp": float(np.quantile(a, 0.90)),
        "positive_share": pos, "negative_share": 1 - pos,
        "total_sum_pp": float(a.sum()),
    }


def concentration(a, label):
    """集中度：top-k 正贡献月占正贡献总和；bottom-k 负贡献占负贡献总和；Gini。"""
    a = np.asarray([v for v in a if v == v], dtype=float)
    pos = np.sort(a[a > 0])[::-1]
    neg = np.sort(a[a < 0])
    pos_sum, neg_sum = float(pos.sum()), float(neg.sum())
    out = {"series": label}
    for k in (1, 3, 5, 10):
        out[f"top{k}_pos_share"] = float(pos[:k].sum() / pos_sum) if pos_sum > 0 else None
        out[f"bottom{k}_neg_share"] = float(neg[:k].sum() / neg_sum) if neg_sum < 0 else None
    # Gini（对 |alpha| 分布）
    srt = np.sort(np.abs(a))
    n = srt.size
    cum = np.cumsum(srt)
    gini = float(1.0 - 2.0 * cum.sum() / (n * cum[-1]) + 1.0 / n)
    out["gini_abs"] = gini
    # 达到累计 50% 所需月份数（按贡献降序）
    order = np.sort(a)[::-1]
    c = np.cumsum(order)
    half = c[-1] * 0.5
    out["months_to_50pct_of_sum"] = int(np.searchsorted(c, half) + 1)
    out["n_months"] = n
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    log = []

    def say(s):
        print(s)
        log.append(s)

    say("A2 收益时间集中度（诊断性；输入=冻结 CSV）")

    ps = A.read_csv_rows(A.FROZEN["phases_monthly"])
    pi = A.read_csv_rows(A.FROZEN["phasei_monthly"])
    dates = [r["signal_date"] for r in ps]
    seg = [r["segment"] for r in ps]
    net_s = A.col(ps, "net_S_3000w")
    alpha = A.col(ps, "alpha_pp")
    v0 = A.col(pi, "alpha_v0_pp")
    v1 = A.finite(A.col(pi, "alpha_v1_pp"))

    # ---------- 1. 分布 ----------
    say("\n[1] 月度分布")
    series = {
        "V0 alpha 全样本(237)": v0,
        "V1 alpha 全样本(237)": v1,
        "net_S_3000w 全样本(237)": net_s,
        "net_S_3000w 现代段(129)": [v for v, s in zip(net_s, seg) if s == "2016-2026"],
        "net_S_3000w 前段(108)": [v for v, s in zip(net_s, seg) if s == "2007-2015"],
    }
    dists = []
    for name, x in series.items():
        d = dist_stats(x, name)
        dists.append(d)
        say(f"  {name}: 均值 {d['mean_pp']:+.4f} 中位 {d['median_pp']:+.4f} 标准差 {d['std_pp']:.4f} "
            f"正收益月占比 {d['positive_share']*100:.1f}% 合计 {d['total_sum_pp']:+.2f}pp "
            f"[{d['min_pp']:+.3f}, {d['max_pp']:+.3f}]")

    # ---------- 2. 集中度 ----------
    say("\n[2] 收益集中度")
    conc = []
    for name, x in series.items():
        c = concentration(x, name)
        conc.append(c)
        say(f"  {name}: top1 {c['top1_pos_share']*100:.1f}% top3 {c['top3_pos_share']*100:.1f}% "
            f"top5 {c['top5_pos_share']*100:.1f}% top10 {c['top10_pos_share']*100:.1f}% "
            f"| Gini {c['gini_abs']:.3f} | 50%累计需 {c['months_to_50pct_of_sum']} 月")

    # ---------- 3. 极端月 ----------
    say("\n[3] 阶段 T 的 6 个先验极端月（名单原样，赛后不改）")
    mod = [(d, v) for d, v, s in zip(dates, net_s, seg) if s == "2016-2026"]
    mod_mean = statistics.mean(v for _, v in mod)
    ext = [(d, v) for d, v in mod if d[:7] in DROP6]
    ext_sum = sum(v for _, v in ext)
    say(f"  现代段月均 {mod_mean:+.6f}pp；6 个极端月合计贡献 {ext_sum:+.6f}pp "
        f"= 月均的 {ext_sum/129/mod_mean*100:.1f}%")
    for d, v in ext:
        say(f"    {d}: {v:+.6f}pp（月均的 {v/mod_mean:.2f} 倍）")
    kept = [v for d, v in mod if d[:7] not in DROP6]
    pk = A.nw_t(kept, 3)
    say(f"  剔除后 n={pk['n']} 均值 {pk['mean']:+.6f}pp t={pk['t_nw']:+.4f}"
        f"（阶段 T 记录 +1.9207）")

    # ---------- 4. 分年度 ----------
    say("\n[4] 分年度（net_S_3000w）")
    by_year = {}
    for d, v, s in zip(dates, net_s, seg):
        by_year.setdefault(d[:4], []).append((v, s))
    yearly = []
    for y in sorted(by_year):
        allv = [v for v, _ in by_year[y]]
        modv = [v for v, s in by_year[y] if s == "2016-2026"]
        row = {"year": y, "n": len(allv), "mean_pp": statistics.mean(allv),
               "modern": bool(modv), "modern_mean_pp": statistics.mean(modv) if modv else None}
        yearly.append(row)
        say(f"  {y}: n={row['n']:>2} 全样本 {row['mean_pp']:+.4f}pp"
            + (f"  现代段 {row['modern_mean_pp']:+.4f}pp" if modv else ""))

    # ---------- 5. 滚动 60 月 ----------
    say("\n[5] 滚动 60 个月窗口（NW(3) t；诊断性，不当成独立重复实验）")
    def rolling(x, label):
        rows = []
        for i in range(len(x) - ROLL + 1):
            win = x[i:i + ROLL]
            pr = A.nw_t(win, 3)
            rows.append({"start": i, "n": ROLL, "mean_pp": pr["mean"], "t_nw": pr["t_nw"],
                         "sig": bool(abs(pr["t_nw"]) >= A.T_CRIT)})
        sig = sum(1 for r in rows if r["sig"])
        ts = [r["t_nw"] for r in rows]
        say(f"  {label}: {len(rows)} 窗，显著 {sig}（{sig/len(rows)*100:.1f}%），"
            f"t 范围 [{min(ts):+.3f}, {max(ts):+.3f}]")
        return rows, sig

    mod_net = [v for v, s in zip(net_s, seg) if s == "2016-2026"]
    roll_mod, sig_mod = rolling(mod_net, "现代段内滚动（70 窗）")
    roll_full, sig_full = rolling(net_s, "全样本滚动（178 窗）")
    roll_v0, sig_v0 = rolling(v0, "V0 alpha 全样本滚动（178 窗）")

    # ---------- 6. 累计曲线与回撤 ----------
    say("\n[6] 累计 alpha 曲线（Σ月 alpha，非复利）")
    cum = np.cumsum(net_s)
    peak = np.maximum.accumulate(cum)
    dd = cum - peak
    say(f"  全样本累计 {cum[-1]:+.2f}pp；最大回撤（累计口径）{dd.min():+.2f}pp "
        f"（发生于 {dates[int(np.argmin(dd))]}）")
    cum_m = np.cumsum(mod_net)
    say(f"  现代段累计 {cum_m[-1]:+.2f}pp")

    # ---------- 产物 ----------
    A.write_json(os.path.join(OUT, "concentration.json"), {
        "distributions": dists, "concentration": conc,
        "extreme_months": {"months": ext, "sum_pp": ext_sum,
                           "share_of_modern_mean": ext_sum / 129 / mod_mean,
                           "modern_mean_pp": mod_mean,
                           "after_drop": {"n": pk["n"], "mean_pp": pk["mean"], "t_nw": pk["t_nw"]}},
        "rolling": {"modern_60m": {"n_windows": len(roll_mod), "n_sig": sig_mod},
                    "full_60m": {"n_windows": len(roll_full), "n_sig": sig_full},
                    "v0_full_60m": {"n_windows": len(roll_v0), "n_sig": sig_v0}},
        "cumulative": {"full_sum_pp": float(cum[-1]), "max_drawdown_pp": float(dd.min()),
                       "max_dd_date": dates[int(np.argmin(dd))],
                       "modern_sum_pp": float(cum_m[-1])},
        "note": "全部为诊断性/描述性；极端月名单来自阶段 T 预登记（跑前写死），未作任何修改",
    })
    A.write_csv(os.path.join(OUT, "yearly.csv"),
                ["year", "n", "mean_pp", "modern", "modern_mean_pp"],
                [[r["year"], r["n"], round(r["mean_pp"], 6), r["modern"],
                  round(r["modern_mean_pp"], 6) if r["modern_mean_pp"] is not None else ""]
                 for r in yearly])
    A.write_csv(os.path.join(OUT, "rolling.csv"),
                ["scope", "start_idx", "n", "mean_pp", "t_nw", "sig"],
                [["modern_60m", r["start"], r["n"], round(r["mean_pp"], 6), round(r["t_nw"], 4), r["sig"]]
                 for r in roll_mod]
                + [["full_60m", r["start"], r["n"], round(r["mean_pp"], 6), round(r["t_nw"], 4), r["sig"]]
                   for r in roll_full]
                + [["v0_full_60m", r["start"], r["n"], round(r["mean_pp"], 6), round(r["t_nw"], 4), r["sig"]]
                   for r in roll_v0])
    A.write_csv(os.path.join(OUT, "monthly_table.csv"),
                ["signal_date", "segment", "net_S_3000w_pp", "alpha_pp", "is_extreme_T",
                 "cum_net_S_pp"],
                [[d, s, round(v, 6), round(a, 6), d[:7] in DROP6, round(c, 6)]
                 for d, s, v, a, c in zip(dates, seg, net_s, alpha, cum)])
    with open(os.path.join(OUT, "run.log"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(log) + "\n")
    print(f"\n[产物] {OUT}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
