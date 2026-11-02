# -*- coding: utf-8 -*-
"""SP-2：F3 作为卖出信号的组合策略

按 SP2_预登记.md 执行（sha 1e045e59…）：
  C2  持仓股 F3 全池百分位 > 70% → 卖出，随机换入一只 ≤70% 的
  C1  同换手次数（配对）的随机换股  ← 最关键对照
  C0  买入后永不换

200 条并行 5 只路径，比较分布中位，不看单条路径。
成本：换 1 只 = 20.4 元 / 10 万 = 0.0204%
"""
import os, json, hashlib, sys
import numpy as np
import pandas as pd

ROOT = r"D:\DeepQuant power by txm\DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
PANEL = os.path.join(RD, "data", "panel_monthly.parquet")
OUT = os.path.join(RD, "out")

K = 5
THRESH = 0.70
N_PATHS = 200
SEED = 20261026
COST_PER_SWAP = 0.000204
SEGS = [("200501", "201712", "IS 2005-2017"),
        ("201801", "202112", "OOS1 2018-2021"),
        ("202201", "202612", "OOS2 2022-2026"),
        ("200501", "202612", "全样本")]


def check_prereg():
    p = os.path.join(RD, "sp2_prereg.sha256")
    if not os.path.exists(p):
        print("!! 缺少预登记哈希"); sys.exit(1)
    want = open(p, encoding="utf-8").read().split()[0]
    got = hashlib.sha256(open(os.path.join(RD, "SP2_预登记.md"), "rb").read()).hexdigest()
    print(f"预登记校验 {want[:16]}… vs {got[:16]}…  {'一致' if want == got else '不一致!'}")
    if want != got:
        sys.exit(1)
    return want


def perf(r):
    r = np.asarray(r, float)
    eq = np.cumprod(1 + r)
    yrs = len(r) / 12
    ann = eq[-1] ** (1 / yrs) - 1 if eq[-1] > 0 else -1
    peak = np.maximum.accumulate(eq)
    return float(ann), float(np.min(eq / peak - 1))


def build():
    panel = pd.read_parquet(PANEL)
    p = panel[panel["ok_buy"] & panel["f3"].notna() & panel["fwd20"].notna()].copy()
    p["f3pct"] = p.groupby("date")["f3"].rank(pct=True, method="average")
    p = p[p["f3pct"].notna()]
    by = {}
    for d, g in p.groupby("date"):
        codes = g["code"].to_numpy()
        fwd = g["fwd20"].to_numpy(float)
        pct = g["f3pct"].to_numpy(float)
        if len(codes) < 100:
            continue
        elig = np.where(pct <= THRESH)[0]
        by[d] = (codes, fwd, pct, elig, {c: i for i, c in enumerate(codes)})
    return by, sorted(by)


def simulate(by, months, mode, rng, ns_fixed=None):
    """mode: 'f3' | 'random' | 'hold'。返回 (月收益列表, 换手数列表, 未定价次数)"""
    d0 = months[0]
    codes0 = by[d0][0]
    hold = [codes0[i] for i in rng.choice(len(codes0), K, replace=False)]
    rets, ns, miss = [], [], 0
    for t, d in enumerate(months):
        codes, fwd, pct, elig, cmap = by[d]
        n_swap = 0
        if t > 0 and mode != "hold":
            if mode == "f3":
                sell = [c for c in hold if c in cmap and pct[cmap[c]] > THRESH]
            else:
                k = ns_fixed[t] if ns_fixed is not None else 0
                cand_sell = [c for c in hold if c in cmap]
                sell = list(rng.choice(cand_sell, min(k, len(cand_sell)), replace=False)) if k else []
            n_swap = len(sell)
            if n_swap:
                held_idx = [cmap[c] for c in hold if c in cmap]
                cand = elig[~np.isin(elig, held_idx)] if len(elig) else np.array([], int)
                if len(cand) >= n_swap:
                    new_codes = [codes[i] for i in rng.choice(cand, n_swap, replace=False)]
                    hold = [c for c in hold if c not in sell] + new_codes
                else:
                    n_swap = 0
        vals = [fwd[cmap[c]] for c in hold if c in cmap]
        if len(vals) < len(hold):
            miss += 1
        r = float(np.mean(vals)) if vals else 0.0
        rets.append(r - n_swap * COST_PER_SWAP)
        ns.append(n_swap)
    return rets, ns, miss


def main():
    sha = check_prereg()
    by, months = build()
    print(f"月份 {len(months)}：{months[0]} ~ {months[-1]}")

    A2, A1, A0 = [], [], []
    M2, M1, M0 = [], [], []
    NS = []
    miss2 = 0
    for path in range(N_PATHS):
        rng = np.random.default_rng(SEED + path)
        r2, ns2, m2 = simulate(by, months, "f3", rng)
        miss2 += m2
        rng1 = np.random.default_rng(SEED + path)
        r1, _, _ = simulate(by, months, "random", rng1, ns_fixed=ns2)
        rng0 = np.random.default_rng(SEED + path)
        r0, _, _ = simulate(by, months, "hold", rng0)
        a2, d2 = perf(r2); a1, d1 = perf(r1); a0, d0_ = perf(r0)
        A2.append(a2); A1.append(a1); A0.append(a0)
        M2.append(d2); M1.append(d1); M0.append(d0_)
        NS.append(np.mean(ns2))

    A2, A1, A0 = map(np.array, (A2, A1, A0))
    M2, M1, M0 = map(np.array, (M2, M1, M0))
    NS = np.array(NS)

    def show(nm, A, M):
        print(f"  {nm:<22}{np.median(A)*100:>+9.2f}%{np.percentile(A,5)*100:>+9.2f}%"
              f"{np.percentile(A,95)*100:>+9.2f}%{np.median(M)*100:>+10.2f}%")

    print("\n" + "=" * 88)
    print(f"200 条并行 5 只路径（{months[0]}~{months[-1]}，{len(months)} 月）")
    print("=" * 88)
    print(f"  {'组':<22}{'年化中位':>10}{'5%分位':>10}{'95%分位':>10}{'MDD中位':>11}")
    show("C2 F3触发卖出", A2, M2)
    show("C1 同换手随机换", A1, M1)
    show("C0 买入不换", A0, M0)

    diff = A2 - A1
    print(f"\n  C2 − C1（配对）：中位差 {(np.median(A2)-np.median(A1))*100:+.2f}pp"
          f"｜{int((diff>0).sum())}/{N_PATHS} 条路径 C2 更好（{float((diff>0).mean())*100:.1f}%）")
    print(f"  平均月换手 {np.median(NS)/K*100:.1f}%（{np.median(NS):.2f} 只/月）"
          f"｜年成本 {np.median(NS)*COST_PER_SWAP*12*100:.2f}%")
    print(f"  持仓缺价次数合计 {miss2}（占比 {miss2/(N_PATHS*len(months))*100:.3f}%）")

    # 分段
    print("\n" + "=" * 88)
    print("分段（每条路径按区间重算，取 200 条中位）")
    print("=" * 88)
    print(f"  {'区间':<20}{'C2年化':>10}{'C1年化':>10}{'C0年化':>10}{'C2−C1':>10}")
    seg = {}
    idxs = {lab: [i for i, d in enumerate(months) if lo <= d <= hi]
            for lo, hi, lab in SEGS}
    # 需要保留每条路径的月度序列 → 重跑一遍并存储
    R2, R1, R0 = [], [], []
    for path in range(N_PATHS):
        rng = np.random.default_rng(SEED + path)
        r2, ns2, _ = simulate(by, months, "f3", rng)
        rng1 = np.random.default_rng(SEED + path)
        r1, _, _ = simulate(by, months, "random", rng1, ns_fixed=ns2)
        rng0 = np.random.default_rng(SEED + path)
        r0, _, _ = simulate(by, months, "hold", rng0)
        R2.append(r2); R1.append(r1); R0.append(r0)
    R2, R1, R0 = map(np.array, (R2, R1, R0))

    for lab, ii in idxs.items():
        if len(ii) < 12:
            continue
        a2 = np.array([perf(R2[k, ii])[0] for k in range(N_PATHS)])
        a1 = np.array([perf(R1[k, ii])[0] for k in range(N_PATHS)])
        a0 = np.array([perf(R0[k, ii])[0] for k in range(N_PATHS)])
        d2 = np.array([perf(R2[k, ii])[1] for k in range(N_PATHS)])
        print(f"  {lab:<20}{np.median(a2)*100:>+9.2f}%{np.median(a1)*100:>+9.2f}%"
              f"{np.median(a0)*100:>+9.2f}%{(np.median(a2)-np.median(a1))*100:>+9.2f}pp")
        seg[lab] = {"c2_annual": float(np.median(a2)), "c1_annual": float(np.median(a1)),
                    "c0_annual": float(np.median(a0)), "c2_mdd": float(np.median(d2)),
                    "win_rate_c2_gt_c1": float((a2 > a1).mean())}

    # 判据
    P1 = np.median(A2) > np.median(A1)
    P2 = np.median(A2) > 0.08
    P3 = np.median(M2) >= -0.20
    P4 = (np.median(NS) / K) <= 0.30
    o2 = seg.get("OOS2 2022-2026", {})
    P5 = (o2.get("c2_annual", -1) > o2.get("c1_annual", 1)) and (o2.get("c2_annual", -1) > 0.08)

    print("\n" + "=" * 88)
    print("判据")
    print("=" * 88)
    for lab, v in (("P1 C2>C1（F3卖出有效）", P1), ("P2 C2年化>8%", P2),
                   ("P3 C2 MDD≥−20%", P3), ("P4 月换手≤30%", P4),
                   ("P5 OOS2 且 C2>C1 且 >8%", P5)):
        print(f"  {lab:<28}{'过' if v else '不过'}")

    if not P1:
        verdict = "F3_SELL_SIGNAL_NO_VALUE"
    elif P1 and P2 and P3 and P4:
        verdict = "SP2_USABLE" if P5 else "SP2_USABLE_BUT_MODERN_WEAK"
    else:
        verdict = "SELL_SIGNAL_VALID_TARGET_MISSED"
    print(f"\n【判定】{verdict}")

    os.makedirs(OUT, exist_ok=True)
    json.dump({"prereg_sha256": sha, "months": len(months), "range": [months[0], months[-1]],
               "n_paths": N_PATHS, "K": K, "thresh": THRESH,
               "c2": {"annual_median": float(np.median(A2)),
                      "annual_p5": float(np.percentile(A2, 5)),
                      "annual_p95": float(np.percentile(A2, 95)),
                      "mdd_median": float(np.median(M2))},
               "c1": {"annual_median": float(np.median(A1)),
                      "mdd_median": float(np.median(M1))},
               "c0": {"annual_median": float(np.median(A0)),
                      "mdd_median": float(np.median(M0))},
               "c2_minus_c1_median_pp": float(np.median(A2) - np.median(A1)),
               "win_rate": float((diff > 0).mean()),
               "turnover_per_month": float(np.median(NS) / K),
               "segments": seg,
               "gates": {"P1": bool(P1), "P2": bool(P2), "P3": bool(P3),
                         "P4": bool(P4), "P5": bool(P5)},
               "verdict": verdict},
              open(os.path.join(OUT, "sp2_result.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("saved out/sp2_result.json")


if __name__ == "__main__":
    main()
