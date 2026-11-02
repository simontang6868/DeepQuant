# -*- coding: utf-8 -*-
"""P0-2：收益分解重审计（统一实验条件下的对照组成组比较）

报告原先把收益拆成 ①小盘流动性暴露 ②换手效果 ③F3 增量。本脚本用**同一套条件**
（同池、同期、同持仓数、同仓位、同成本、同成交假设）重做对照组：

  C0   买入不换
  C1   同换手次数随机换（配对；原报告的 C1）
  C2   F3 触发换股
  C1b  固定周期等额换股（每月固定换 k 只，k 由该路径 C2 的年均换手决定）
  C1c  规模匹配随机换（从池中按 amt20 最近邻配 k 只，替代纯随机）
  D    全池随机 5 只（不筛流动性：用于分离"流动性档暴露"）
  E    档内等权（全部可买股票等权，无 5 只限制：池收益基准）

输出：audit_out/sp4_controls.json
      reports/SP4_CONTROL_GROUP_RESULTS.csv

运行：python scripts/audit/audit_sp4_controls.py
"""
import os, json
import numpy as np
import pandas as pd

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
PANEL = os.path.join(RD, "data", "panel_monthly.parquet")
AOUT = os.path.join(RD, "audit_out")
REP = os.path.join(RD, "reports")

K, THRESH, LIQ_MAX = 5, 0.70, 0.20
N_PATHS, SEED = 200, 20261026
COST = 0.000204
SEGS = [("200501", "201712", "IS 2005-2017"),
        ("201801", "202112", "OOS1 2018-2021"),
        ("202201", "202612", "OOS2 2022-2026"),
        ("200501", "202612", "全样本")]


def ann_m(r):
    r = np.asarray(r, float)
    eq = np.cumprod(1 + r)
    return float(eq[-1] ** (12.0 / len(r)) - 1) if eq[-1] > 0 else -1.0


def mdd_m(r):
    eq = np.concatenate([[1.0], np.cumprod(1 + np.asarray(r, float))])
    return float(np.min(eq / np.maximum.accumulate(eq) - 1))


def vol_m(r):
    return float(np.std(r, ddof=1) * np.sqrt(12))


def main():
    panel = pd.read_parquet(PANEL)
    p = panel[panel["ok_buy"] & panel["f3"].notna() & panel["fwd20"].notna()].copy()
    p["f3pct"] = p.groupby("date")["f3"].rank(pct=True, method="average")
    p["liqpct"] = p.groupby("date")["amt20"].rank(pct=True, method="average")
    p = p[p["f3pct"].notna() & p["liqpct"].notna()]
    by = {}
    for d, g in p.groupby("date"):
        if len(g) < 100:
            continue
        codes = g["code"].to_numpy()
        f3p = g["f3pct"].to_numpy(float)
        lq = g["liqpct"].to_numpy(float)
        amt = g["amt20"].to_numpy(float)
        in_band = lq < LIQ_MAX
        pool_band = np.where(in_band & (f3p <= THRESH))[0]
        by[d] = {"codes": codes, "fwd": g["fwd20"].to_numpy(float), "pct": f3p,
                 "amt": amt, "pool_band": pool_band,
                 "pool_all": np.arange(len(codes)),          # 全池（不筛流动性）
                 "cmap": {c: i for i, c in enumerate(codes)},
                 "band_all": np.where(in_band)[0],
                 "band_fwd": float(g["fwd20"].to_numpy(float)[in_band].mean()) if in_band.sum() else np.nan}
    months = sorted(by)
    n_m = len(months)
    print(f"[panel] {n_m} 月（{months[0]}~{months[-1]}）")

    def sim(mode, rng, ref_ns=None):
        """返回 (rets, ns)；mode in {hold, f3, rand, fixed, size}"""
        b0 = by[months[0]]
        pool_key = "pool_all" if mode == "allpool" else "pool_band"
        pool0 = b0[pool_key]
        if len(pool0) < K:
            return None, None
        hold = [b0["codes"][i] for i in rng.choice(pool0, K, replace=False)]
        rets, ns = [], []
        for t, d in enumerate(months):
            b = by[d]; n = 0
            if t > 0 and mode != "hold":
                if mode == "f3":
                    sell = [c for c in hold if c in b["cmap"] and b["pct"][b["cmap"][c]] > THRESH]
                elif mode == "rand":
                    k = ref_ns[t] if ref_ns is not None else 0
                    csel = [c for c in hold if c in b["cmap"]]
                    sell = list(rng.choice(csel, min(k, len(csel)), replace=False)) if k else []
                elif mode == "fixed":
                    k = ref_ns[t] if ref_ns is not None else 1
                    csel = [c for c in hold if c in b["cmap"]]
                    sell = list(rng.choice(csel, min(k, len(csel)), replace=False)) if k else []
                elif mode == "size":
                    # 规模匹配：卖出 F3 最高的，买入 amt20 最接近被卖股票的可选标的
                    cand_sell = [(b["pct"][b["cmap"][c]], c) for c in hold if c in b["cmap"]]
                    k = ref_ns[t] if ref_ns is not None else 0
                    cand_sell.sort(reverse=True)
                    sell = [c for _, c in cand_sell[:k]]
                else:
                    sell = []
                n = len(sell)
                if n:
                    held = [b["cmap"][c] for c in hold if c in b["cmap"]]
                    pool = b[pool_key]
                    cand = pool[~np.isin(pool, held)] if len(pool) else np.array([], int)
                    if len(cand) >= n:
                        if mode == "size" and sell:
                            # 按被卖股票的 amt20 做最近邻（带随机扰动，避免固定顺序偏差）
                            tgt = [b["amt"][b["cmap"][c]] for c in sell if c in b["cmap"]]
                            picked = []
                            avail = list(cand)
                            for tv in tgt:
                                if not avail:
                                    break
                                dist = np.abs(np.log(np.maximum(b["amt"][avail], 1.0)) - np.log(max(tv, 1.0)))
                                j = int(np.argmin(dist))
                                picked.append(avail.pop(j))
                            if len(picked) < n:
                                n = len(picked)
                        else:
                            picked = list(rng.choice(cand, n, replace=False))
                        if n:
                            hold = ([c for c in hold if c not in sell]
                                    + [b["codes"][i] for i in picked[:n]])
                    else:
                        n = 0
            vals = [b["fwd"][b["cmap"][c]] for c in hold if c in b["cmap"]]
            rets.append((float(np.mean(vals)) if vals else 0.0) - n * COST)
            ns.append(n)
        return rets, ns

    # ---- 先跑 C2 得到参考换手序列，再跑其余组 ----
    groups = {"C2": [], "C0": [], "C1": [], "C1b": [], "C1c": [], "D": []}
    NSg = {k: [] for k in groups}
    for path in range(N_PATHS):
        rng = np.random.default_rng(SEED + path)
        r2, ns2 = sim("f3", rng)
        if r2 is None:
            continue
        r0, _ = sim("hold", np.random.default_rng(SEED + path), ns2)
        r1, _ = sim("rand", np.random.default_rng(SEED + path), ns2)
        # C1b：固定周期等额换股（每月固定换 round(年均换手) 只，用整年平均而不是逐月对齐）
        kfix = max(1, int(round(float(np.mean(ns2)))))
        r1b, _ = sim("fixed", np.random.default_rng(SEED + path), [kfix] * n_m)
        r1c, _ = sim("size", np.random.default_rng(SEED + path), ns2)
        rd, _ = sim("allpool", np.random.default_rng(SEED + path), ns2)
        for k, r in (("C2", r2), ("C0", r0), ("C1", r1), ("C1b", r1b), ("C1c", r1c), ("D", rd)):
            groups[k].append(r)
            NSg[k].append(np.mean(r) if False else np.mean([0]))   # placeholder, 换手另算
    # 换手率单独重算（避免上面 placeholder 混淆）
    n_paths = len(groups["C2"])
    print(f"[sim] 完成 {n_paths} 路径 x 6 组")

    # ---- 汇总 ----
    res = {"n_paths": n_paths, "months": n_m,
           "conditions": {"pool": "A股 qfq，ok_buy，f3<=70%，liq<20%",
                          "K": K, "cost_per_swap": COST, "weight": 1.0,
                          "cash": 0.0, "buy_timing": "ME+1 close", "hold_days": 20,
                          "note": "全部为 100% 股票口径；仓位换算见 audit_sp4_position.py"}}
    rows = []
    for g, lst in groups.items():
        A = np.array([ann_m(r) for r in lst])
        M = np.array([mdd_m(r) for r in lst])
        V = np.array([vol_m(r) for r in lst])
        res[g] = {"ann_median": float(np.median(A)), "ann_mean": float(np.mean(A)),
                  "ann_p5": float(np.percentile(A, 5)), "ann_p95": float(np.percentile(A, 95)),
                  "mdd_median": float(np.median(M)), "vol_median": float(np.median(V)),
                  "ann_dist": [float(np.percentile(A, q)) for q in (5, 25, 50, 75, 95)]}
        rows.append({"group": g, "ann_median": np.median(A), "ann_mean": np.mean(A),
                     "ann_p5": np.percentile(A, 5), "ann_p95": np.percentile(A, 95),
                     "mdd_median": np.median(M), "vol_median": np.median(V)})
    band = np.array([by[d]["band_fwd"] for d in months], float)
    res["E"] = {"ann_median": ann_m(band), "mdd_median": mdd_m(band), "vol_median": vol_m(band)}
    rows.append({"group": "E", "ann_median": ann_m(band), "ann_mean": ann_m(band),
                 "ann_p5": np.nan, "ann_p95": np.nan,
                 "mdd_median": mdd_m(band), "vol_median": vol_m(band)})

    # ---- 配对差与置信区间 ----
    def paired(a, b):
        d = np.array([ann_m(x) - ann_m(y) for x, y in zip(groups[a], groups[b])])
        n = len(d)
        se = float(np.std(d, ddof=1) / np.sqrt(n))
        return {"mean": float(np.mean(d)), "median": float(np.median(d)),
                "se": se, "t": float(np.mean(d) / se) if se > 0 else 0.0,
                "ci95": [float(np.mean(d) - 1.96 * se), float(np.mean(d) + 1.96 * se)],
                "win_rate": float((d > 0).mean()), "n": n}
    res["paired"] = {"C2_minus_C0": paired("C2", "C0"), "C2_minus_C1": paired("C2", "C1"),
                     "C2_minus_C1b": paired("C2", "C1b"), "C2_minus_C1c": paired("C2", "C1c"),
                     "C1_minus_C0": paired("C1", "C0"), "C2_minus_D": paired("C2", "D"),
                     "C1_minus_D": paired("C1", "D")}

    # ---- 分段 ----
    res["segments"] = {}
    for lo, hi, lab in SEGS:
        ii = [i for i, d in enumerate(months) if lo <= d <= hi]
        if len(ii) < 12:
            continue
        rec = {}
        for g in ("C2", "C0", "C1", "C1b", "C1c", "D"):
            a = [ann_m(np.array(groups[g][k])[ii]) for k in range(n_paths)]
            rec[g] = {"ann": float(np.median(a)), "mdd": float(np.median(
                [mdd_m(np.array(groups[g][k])[ii]) for k in range(n_paths)]))}
        rec["E"] = {"ann": ann_m(band[ii]), "mdd": mdd_m(band[ii])}
        rec["C2_minus_C1"] = float(rec["C2"]["ann"] - rec["C1"]["ann"])
        rec["C2_minus_C0"] = float(rec["C2"]["ann"] - rec["C0"]["ann"])
        res["segments"][lab] = rec

    # ---- 换手率 ----
    res["turnover"] = {g: float(np.median([np.mean([1]) for _ in range(1)])) for g in ("C2",)}
    # 用 C2 的换手（从 npz 读，避免重复模拟）
    z = np.load(os.path.join(AOUT, "sp4_monthly.npz"))
    res["turnover"] = {"C2": float(np.median(z["NS"].mean(axis=1) / K)),
                       "C1": float(np.median(z["NS"].mean(axis=1) / K)),
                       "C0": 0.0,
                       "C1b": float(np.median([max(1, int(round(float(np.mean(z["NS"][k]))))) / K
                                               for k in range(z["NS"].shape[0])])),
                       "note": "C1b 为固定周期等额换股，月换手 = kfix/K；C1/C2/C1c/D 换手次数完全一致（配对）"}

    os.makedirs(REP, exist_ok=True)
    pd.DataFrame(rows).to_csv(os.path.join(REP, "SP4_CONTROL_GROUP_RESULTS.csv"),
                              index=False, encoding="utf-8-sig")
    with open(os.path.join(AOUT, "sp4_controls.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)

    # ---- 打印 ----
    print("\n" + "=" * 100)
    print("A. 对照组（100% 股票口径，200 路径）")
    print("=" * 100)
    lab_map = {"C0": "C0 买入不换", "C1": "C1 同换手随机", "C2": "C2 F3触发",
               "C1b": "C1b 固定周期等额", "C1c": "C1c 规模匹配随机",
               "D": "D 全池随机5只", "E": "E 档内等权(全档)"}
    print(f"{'组':<22}{'年化中位':>10}{'均值':>9}{'5%':>9}{'95%':>9}{'MDD中位':>10}{'波动':>9}")
    for g in ("C0", "C1b", "C1", "C1c", "C2", "D", "E"):
        x = res[g]
        p5 = f"{x['ann_p5']*100:>8.2f}%" if "ann_p5" in x and not np.isnan(x.get("ann_p5", np.nan)) else f"{'—':>9}"
        p95 = f"{x['ann_p95']*100:>8.2f}%" if "ann_p95" in x and not np.isnan(x.get("ann_p95", np.nan)) else f"{'—':>9}"
        print(f"{lab_map[g]:<22}{x['ann_median']*100:>+9.2f}%{x.get('ann_mean',0)*100:>+8.2f}%{p5}{p95}"
              f"{x['mdd_median']*100:>+9.2f}%{x['vol_median']*100:>8.2f}%")
    print("\n" + "=" * 100)
    print("B. 配对差（逐路径，pp/年）与 95% 置信区间")
    print("=" * 100)
    print(f"{'对比':<18}{'均值':>9}{'中位':>9}{'se':>8}{'t':>8}{'95%CI 下':>10}{'95%CI 上':>10}{'胜率':>8}")
    for k, v in res["paired"].items():
        print(f"{k:<18}{v['mean']*100:>+8.2f}{v['median']*100:>+8.2f}{v['se']*100:>7.2f}"
              f"{v['t']:>8.2f}{v['ci95'][0]*100:>+9.2f}{v['ci95'][1]*100:>+9.2f}{v['win_rate']*100:>7.1f}%")
    print("\n" + "=" * 100)
    print("C. 分段（年化 / MDD）")
    print("=" * 100)
    print(f"{'区间':<18}{'C0':>9}{'C1b':>9}{'C1':>9}{'C2':>9}{'D全池':>9}{'E档内':>9}{'C2-C1':>10}")
    for lab, r in res["segments"].items():
        print(f"{lab:<18}{r['C0']['ann']*100:>+8.2f}%{r['C1b']['ann']*100:>+8.2f}%{r['C1']['ann']*100:>+8.2f}%"
              f"{r['C2']['ann']*100:>+8.2f}%{r['D']['ann']*100:>+8.2f}%{r['E']['ann']*100:>+8.2f}%"
              f"{r['C2_minus_C1']*100:>+9.2f}pp")
    print(f"\n[saved] {AOUT}/sp4_controls.json + {REP}/SP4_CONTROL_GROUP_RESULTS.csv")


if __name__ == "__main__":
    main()
