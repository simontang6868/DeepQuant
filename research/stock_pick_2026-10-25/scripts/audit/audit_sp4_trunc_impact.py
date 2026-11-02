# -*- coding: utf-8 -*-
"""P0-3 量化：把「退市前最后 20 天」的损失计入后，SP-4 的收益变化

原面板用 `sell < len(dates)` 把退市前 20 天整段剔除（fwd20 无法计算）。
对照面板 `panel_monthly_trunc.parquet` 改为 `sell_c = min(sell, len-1)`，
即"持有到最后一根可用K线"，把退市损失计入 fwd20。

同一套模拟、同一随机种子，唯一差异是 fwd20 的定义。

输出：audit_out/sp4_trunc_impact.json
运行：python scripts/audit/audit_sp4_trunc_impact.py
"""
import os, json
import numpy as np
import pandas as pd

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
AOUT = os.path.join(RD, "audit_out")
K, THRESH, LIQ_MAX = 5, 0.70, 0.20
N_PATHS, SEED, COST = 200, 20261026, 0.000204


def ann(r):
    r = np.asarray(r, float); eq = np.cumprod(1 + r)
    return float(eq[-1] ** (12.0 / len(r)) - 1) if eq[-1] > 0 else -1.0


def mdd(r):
    eq = np.concatenate([[1.0], np.cumprod(1 + np.asarray(r, float))])
    return float(np.min(eq / np.maximum.accumulate(eq) - 1))


def build(panel_path):
    panel = pd.read_parquet(panel_path)
    p = panel[panel["ok_buy"] & panel["f3"].notna() & panel["fwd20"].notna()].copy()
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
                 "cmap": {c: i for i, c in enumerate(codes)},
                 "band_fwd": float(g["fwd20"].to_numpy(float)[lq < LIQ_MAX].mean()) if (lq < LIQ_MAX).sum() else np.nan}
    return by, sorted(by)


def run(by, months, mode):
    out = []
    for path in range(N_PATHS):
        rng = np.random.default_rng(SEED + path)
        b0 = by[months[0]]
        if len(b0["pool"]) < K:
            continue
        hold = [b0["codes"][i] for i in rng.choice(b0["pool"], K, replace=False)]
        rets = []
        for t, d in enumerate(months):
            b = by[d]; n = 0
            if t > 0 and mode != "hold":
                sell = [c for c in hold if c in b["cmap"] and b["pct"][b["cmap"][c]] > THRESH]
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
        out.append(rets)
    return out


def main():
    base, m0 = build(os.path.join(RD, "data", "panel_monthly.parquet"))
    tr, m1 = build(os.path.join(RD, "data", "panel_monthly_trunc.parquet"))
    print(f"[panel] 原 {len(m0)} 月（{m0[0]}~{m0[-1]}）｜对照 {len(m1)} 月（{m1[0]}~{m1[-1]}）")
    common = [m for m in m0 if m in set(m1)]
    print(f"[panel] 共同月 {len(common)}")

    res = {"common_months": len(common), "first": common[0], "last": common[-1], "groups": {}}
    for mode in ("f3", "hold"):
        A = run(base, common, mode)
        B = run(tr, common, mode)
        n = min(len(A), len(B))
        a = np.array([ann(r) for r in A[:n]])
        b = np.array([ann(r) for r in B[:n]])
        ma = np.array([mdd(r) for r in A[:n]])
        mb = np.array([mdd(r) for r in B[:n]])
        d = b - a
        se = float(np.std(d, ddof=1) / np.sqrt(n))
        key = "C2_F3" if mode == "f3" else "C0_hold"
        res["groups"][key] = {
            "orig_ann": float(np.median(a)), "trunc_ann": float(np.median(b)),
            "diff_median_pp": float(np.median(d)), "diff_mean_pp": float(np.mean(d)),
            "diff_t": float(np.mean(d) / se) if se > 0 else 0.0,
            "share_worse": float((d < 0).mean()),
            "orig_mdd": float(np.median(ma)), "trunc_mdd": float(np.median(mb)),
        }
        print(f"\n{key}: 原 {np.median(a)*100:+.2f}% -> 含退市损失 {np.median(b)*100:+.2f}%"
              f"（中位差 {np.median(d)*100:+.2f}pp，均值 {np.mean(d)*100:+.2f}pp, t={np.mean(d)/se:+.2f}）"
              f"\n       变差的路径占比 {(d<0).mean()*100:.1f}%｜MDD {np.median(ma)*100:+.2f}% -> {np.median(mb)*100:+.2f}%")

    # 分段
    res["segments"] = {}
    for lo, lab in (("2016", "2016起（退市数据完整）"), ("2005", "全样本")):
        ii = [i for i, m in enumerate(common) if m >= lo + "0101"]
        for mode in ("f3",):
            A = run(base, common, mode); B = run(tr, common, mode)
            n = min(len(A), len(B))
            a = np.array([ann(np.array(A[k])[ii]) for k in range(n)])
            b = np.array([ann(np.array(B[k])[ii]) for k in range(n)])
            res["segments"][lab] = {"orig": float(np.median(a)), "trunc": float(np.median(b)),
                                    "diff_pp": float(np.median(b - a)), "months": len(ii)}
            print(f"  {lab:<24} 原 {np.median(a)*100:+.2f}%  含退市 {np.median(b)*100:+.2f}%"
                  f"  差 {np.median(b-a)*100:+.2f}pp（{len(ii)} 月）")

    json.dump(res, open(os.path.join(AOUT, "sp4_trunc_impact.json"), "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)
    print(f"\n[saved] {AOUT}/sp4_trunc_impact.json")


if __name__ == "__main__":
    main()
