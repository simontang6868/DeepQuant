# -*- coding: utf-8 -*-
"""P1-2 补：随机种子敏感性（用户明确要求"检查随机种子是否影响结论"）

对 4 个不同种子各跑 C2 与 C1（配对），比较配对差是否稳定。
不新增参数、不改定义，只换种子。

输出：audit_out/sp4_seed_sensitivity.json
运行：python scripts/audit/audit_sp4_seed.py
"""
import os, json
import numpy as np
import pandas as pd

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
AOUT = os.path.join(RD, "audit_out")
K, THRESH, LIQ_MAX = 5, 0.70, 0.20
COST = 0.000204
SEEDS = [20261026, 20261027, 11111, 99999]


def ann(r):
    r = np.asarray(r, float); eq = np.cumprod(1 + r)
    return float(eq[-1] ** (12.0 / len(r)) - 1) if eq[-1] > 0 else -1.0


def main():
    p = pd.read_parquet(os.path.join(RD, "data", "panel_monthly.parquet"))
    p = p[p["ok_buy"] & p["f3"].notna() & p["fwd20"].notna()].copy()
    p["f3pct"] = p.groupby("date")["f3"].rank(pct=True, method="average")
    p["liqpct"] = p.groupby("date")["amt20"].rank(pct=True, method="average")
    p = p[p["f3pct"].notna() & p["liqpct"].notna()]
    by = {}
    for d, g in p.groupby("date"):
        if len(g) < 100:
            continue
        codes = g["code"].to_numpy(); f3p = g["f3pct"].to_numpy(float); lq = g["liqpct"].to_numpy(float)
        by[d] = {"codes": codes, "fwd": g["fwd20"].to_numpy(float), "pct": f3p,
                 "pool": np.where((f3p <= THRESH) & (lq < LIQ_MAX))[0],
                 "cmap": {c: i for i, c in enumerate(codes)}}
    months = sorted(by); n_m = len(months)

    def sim(mode, rng, ref_ns=None):
        b0 = by[months[0]]
        if len(b0["pool"]) < K:
            return None, None
        hold = [b0["codes"][i] for i in rng.choice(b0["pool"], K, replace=False)]
        rets, ns = [], []
        for t, d in enumerate(months):
            b = by[d]; n = 0
            if t > 0 and mode != "hold":
                if mode == "f3":
                    sell = [c for c in hold if c in b["cmap"] and b["pct"][b["cmap"][c]] > THRESH]
                else:
                    k = ref_ns[t] if ref_ns is not None else 0
                    csel = [c for c in hold if c in b["cmap"]]
                    sell = list(rng.choice(csel, min(k, len(csel)), replace=False)) if k else []
                n = len(sell)
                if n:
                    held = [b["cmap"][c] for c in hold if c in b["cmap"]]
                    cand = b["pool"][~np.isin(b["pool"], held)]
                    if len(cand) >= n:
                        hold = ([c for c in hold if c not in sell]
                                + [b["codes"][i] for i in rng.choice(cand, n, replace=False)])
                    else:
                        n = 0
            vals = [b["fwd"][b["cmap"][c]] for c in hold if c in b["cmap"]]
            rets.append((float(np.mean(vals)) if vals else 0.0) - n * COST)
            ns.append(n)
        return rets, ns

    out = {"seeds": SEEDS, "n_paths": 200, "note": "每组种子独立跑 C2 与 C1，配对差 = 逐路径 C2 年化 - C1 年化"}
    print(f"{'seed':>10}{'C2中位':>10}{'C1中位':>10}{'配对差均值':>12}{'中位':>9}{'t':>7}{'胜率':>8}")
    details = []
    for sd in SEEDS:
        A, B = [], []
        for k in range(200):
            r2, ns2 = sim("f3", np.random.default_rng(sd + k))
            if r2 is None:
                continue
            r1, _ = sim("rand", np.random.default_rng(sd + k), ns2)
            A.append(ann(r2)); B.append(ann(r1))
        A = np.array(A); B = np.array(B)
        d = A - B
        se = float(np.std(d, ddof=1) / np.sqrt(len(d)))
        details.append({"seed": sd, "c2_median": float(np.median(A)), "c1_median": float(np.median(B)),
                        "diff_mean": float(d.mean()), "diff_median": float(np.median(d)),
                        "t": float(d.mean() / se), "win_rate": float((d > 0).mean())})
        print(f"{sd:>10}{np.median(A)*100:>+9.2f}%{np.median(B)*100:>+9.2f}%"
              f"{d.mean()*100:>+11.2f}pp{np.median(d)*100:>+8.2f}pp{d.mean()/se:>7.2f}{(d>0).mean()*100:>7.1f}%")
    dm = np.array([x["diff_mean"] for x in details])
    out["details"] = details
    out["stability"] = {"diff_mean_range_pp": [float(dm.min()), float(dm.max())],
                        "diff_mean_spread_pp": float(dm.max() - dm.min()),
                        "all_positive": bool((dm > 0).all()),
                        "verdict": ("种子不改变结论方向" if (dm > 0).all() and dm.max() - dm.min() < 0.02
                                    else "种子影响较大，需扩大路径数")}
    print(f"\n  配对差区间 [{dm.min()*100:+.2f}, {dm.max()*100:+.2f}]pp，极差 {np.ptp(dm)*100:.2f}pp")
    print(f"  判定：{out['stability']['verdict']}")
    json.dump(out, open(os.path.join(AOUT, "sp4_seed_sensitivity.json"), "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)
    print(f"[saved] {AOUT}/sp4_seed_sensitivity.json")


if __name__ == "__main__":
    main()
