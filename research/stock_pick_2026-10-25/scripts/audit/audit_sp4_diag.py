# -*- coding: utf-8 -*-
"""P0-1 补充诊断：MDD 首月偏差、NAV-A vs NAV-B、有效持股数坍缩、凸性分解

运行：python scripts/audit/audit_sp4_diag.py
"""
import os, json
import numpy as np

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
AOUT = os.path.join(RD, "audit_out")
CASH_M = 0.03 / 12


def perf_original(r):
    """原引擎实现（复制，用于定位差异）。"""
    r = np.asarray(r, float); eq = np.cumprod(1 + r)
    peak = np.maximum.accumulate(eq)
    return float(np.min(eq / peak - 1)), eq


def mdd_with_initial_one(r):
    """含起点 1.0 的实现。"""
    nav = np.concatenate([[1.0], np.cumprod(1 + np.asarray(r, float))])
    peak = np.maximum.accumulate(nav)
    return float(np.min(nav / peak - 1)), nav


def main():
    z = np.load(os.path.join(AOUT, "sp4_monthly.npz"), allow_pickle=True)
    months = [str(x) for x in z["months"]]
    R = {"c0": z["R_c0"], "c1": z["R_c1"], "c2": z["R_c2"]}
    NS = z["NS"]; NV = z["NV"]
    n_paths, n_m = R["c2"].shape
    res = {}

    # ---------- 1) 首月偏差的精确定位 ----------
    k0 = 0
    r = R["c2"][k0]
    m_orig, _ = perf_original(r)
    m_new, nav = mdd_with_initial_one(r)
    print("=" * 90)
    print("1) MDD 首月偏差定位")
    print("=" * 90)
    print(f"  路径 {k0} 首月收益 r[0] = {r[0]*100:+.6f}%（含 5 只建仓成本 5x0.0204% = 0.102%）")
    print(f"  原实现 MDD = {m_orig*100:.6f}%（eq 起点 = 1+r[0]，首月 dd 恒为 0）")
    print(f"  含起点 1.0  MDD = {m_new*100:.6f}%")
    print(f"  差 = {(m_new-m_orig)*100:+.6f}pp　=> 原实现**漏掉首月回撤**")
    n_neg_first = int((R["c2"][:, 0] < 0).sum())
    print(f"  c2 首月为负的路径数 {n_neg_first}/{n_paths}（因含建仓成本，几乎必然为负）")
    res["mdd_first_month"] = {
        "orig_median": float(np.median([perf_original(R["c2"][k])[0] for k in range(n_paths)])),
        "fixed_median": float(np.median([mdd_with_initial_one(R["c2"][k])[0] for k in range(n_paths)])),
        "n_paths_first_month_negative": n_neg_first,
        "explanation": "perf() 的 eq=cumprod(1+r) 无 1.0 前置，peak 从 eq[0] 起，使首月回撤恒为 0。"
                       "因建仓月必扣买入成本，c2 首月必然为负，故此项对 c2 影响最大。",
    }

    # ---------- 2) NAV-A vs NAV-B ----------
    print("\n" + "=" * 90)
    print("2) 月度再平衡（NAV-A）vs 买入持有不再平衡（NAV-B）")
    print("=" * 90)
    print(f"{'仓位':>6}{'A年化':>9}{'B年化':>9}{'差pp':>8}{'A MDD':>10}{'B MDD':>10}{'A波动':>9}{'B波动':>9}")
    res["rebalance_vs_buyhold"] = {}
    for w in (1.00, 0.50, 0.30, 0.25, 0.20):
        cw = 1 - w
        aA, aB, mA, mB, vA, vB = [], [], [], [], [], []
        for k in range(n_paths):
            rr = R["c2"][k]
            # A: 月度再平衡
            pr = w * rr + cw * CASH_M
            navA = np.concatenate([[1.0], np.cumprod(1 + pr)])
            # B: 买入持有
            navB = w * np.concatenate([[1.0], np.cumprod(1 + rr)]) + \
                   cw * np.concatenate([[1.0], np.cumprod(np.full(n_m, 1 + CASH_M))])
            for nav, aa, mm, vv in ((navA, aA, mA, vA), (navB, aB, mB, vB)):
                aa.append((nav[-1]) ** (12 / n_m) - 1)
                mm.append(float(np.min(nav / np.maximum.accumulate(nav) - 1)))
                rr2 = nav[1:] / nav[:-1] - 1
                vv.append(float(np.std(rr2, ddof=1) * np.sqrt(12)))
        res["rebalance_vs_buyhold"][f"{w:.2f}"] = {
            "A_ann": float(np.median(aA)), "B_ann": float(np.median(aB)),
            "A_mdd": float(np.median(mA)), "B_mdd": float(np.median(mB)),
            "A_vol": float(np.median(vA)), "B_vol": float(np.median(vB))}
        print(f"{w*100:>5.0f}%{np.median(aA)*100:>+8.2f}%{np.median(aB)*100:>+8.2f}%"
              f"{(np.median(aB)-np.median(aA))*100:>+7.2f}{np.median(mA)*100:>+9.2f}%"
              f"{np.median(mB)*100:>+9.2f}%{np.median(vA)*100:>8.2f}%{np.median(vB)*100:>8.2f}%")

    # ---------- 3) 凸性分解：为何 (c) > (a) ----------
    print("\n" + "=" * 90)
    print("3) 10.84% 与 9.84% 的差额分解（w=0.25）")
    print("=" * 90)
    w = 0.25; cw = 0.75
    g_stock = np.array([(np.prod(1 + R["c2"][k])) ** (12 / n_m) - 1 for k in range(n_paths)])
    g_mix = np.array([(np.prod(1 + w * R["c2"][k] + cw * CASH_M)) ** (12 / n_m) - 1
                      for k in range(n_paths)])
    # 逐路径的"线性基准"
    lin = w * g_stock + cw * 0.03
    print(f"  逐路径线性基准 0.25*g_stock + 0.75*3%：中位 {np.median(lin)*100:+.2f}%")
    print(f"  实际混合后年化：中位 {np.median(g_mix)*100:+.2f}%")
    print(f"  逐路径凸性差 (g_mix - lin)：中位 {(np.median(g_mix-lin))*100:+.2f}pp, "
          f"均值 {np.mean(g_mix-lin)*100:+.2f}pp")
    print(f"  股票年化的 分布：中位 {np.median(g_stock)*100:.2f}%，均值 {np.mean(g_stock)*100:.2f}%，"
          f"偏度 {float(((g_stock-g_stock.mean())**3).mean()/g_stock.std()**3):+.3f}")
    # 理论再平衡红利近似：0.5*w*(1-w)*sigma^2
    sig = float(np.std(R["c2"].mean(axis=0), ddof=1) * np.sqrt(12))
    print(f"  理论再平衡红利 ≈ 0.5*w*(1-w)*σ² = {0.5*w*(1-w)*sig**2*100:+.2f}pp（σ≈{sig*100:.1f}%）")
    res["convexity"] = {
        "lin_median": float(np.median(lin)), "mix_median": float(np.median(g_mix)),
        "convexity_pp": float(np.median(g_mix - lin)),
        "stock_ann_median": float(np.median(g_stock)), "stock_ann_mean": float(np.mean(g_stock)),
        "theoretical_rebal_premium_pp": float(0.5 * w * (1 - w) * sig ** 2),
        "note": "9.84% 用中位加权；逐路径线性基准中位更低。两者差主要来自："
                "(i) 中位 vs 逐路径，(ii) 月度再平衡的凸性红利。"
    }

    # ---------- 4) 有效持股数坍缩（c2 与 c0） ----------
    print("\n" + "=" * 90)
    print("4) 有效持股数（缺价被剔除后真正参与平均的只数）")
    print("=" * 90)
    print(f"  c2 路径中位有效数 {np.median(NV):.2f}｜<5 的月-路径占比 {(NV<5).mean()*100:.2f}%"
          f"｜=0 的占比 {(NV==0).mean()*100:.2f}%")
    dec = [(months[i][:4], float(NV[:, i].mean())) for i in range(0, n_m, 24)]
    print("  c2 按年代平均有效数：" + "  ".join(f"{y}:{v:.2f}" for y, v in dec))
    res["effective_holdings"] = {
        "c2_median": float(np.median(NV)), "share_below_k": float((NV < 5).mean()),
        "share_zero": float((NV == 0).mean()),
        "by_year": {y: v for y, v in dec},
        "note": "NV<5 说明该月至少 1 只持仓缺 fwd20/掉出 ok_buy 池，被直接剔除而非按真实收益计。"
                "这会造成收益平滑化（低估波动）与分散度下降（实际持仓 < 5 只）。",
    }

    with open(os.path.join(AOUT, "sp4_diag.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=1)
    print(f"\n[saved] {AOUT}/sp4_diag.json")


if __name__ == "__main__":
    main()
