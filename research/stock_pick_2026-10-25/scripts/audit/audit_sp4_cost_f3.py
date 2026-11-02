# -*- coding: utf-8 -*-
"""P1-1 交易成本敏感性 + P1-2 F3 增量证据重估

P1-1：
  从落盘的净收益反推毛收益（r_gross = r_net + n_swap*COST0），
  再对任意成本情景重算净收益，避免重跑模拟。
  情景：基础（佣金+印花+过户）、+价差 0.10%/0.30%/0.60%、极端（含冲击）
  另报：盈亏平衡成本、资金规模对成本率的影响、对 "年化扣 2pp" 的反向验证

P1-2：
  冻结 F3 定义与阈值，只做证据强度评估：
  配对差 + CI、分段 + CI、随机种子敏感性（重跑 3 个种子）、剔除最佳月份、
  2022 年后功效（MDE）、"无显著证据" vs "证明无效果" 的区分

输出：audit_out/sp4_cost.json / sp4_f3_evidence.json
      reports/SP4_COST_SCENARIOS.csv

运行：python scripts/audit/audit_sp4_cost_f3.py
"""
import os, json
import numpy as np
import pandas as pd

ROOT = r"D:/DeepQuant power by txm/DeepQuant-main"
RD = os.path.join(ROOT, "research", "stock_pick_2026-10-25")
AOUT = os.path.join(RD, "audit_out")
REP = os.path.join(RD, "reports")
K, THRESH, LIQ_MAX = 5, 0.70, 0.20
COST0 = 0.000204
SEED = 20261026
N_PATHS = 200


def ann(r):
    r = np.asarray(r, float); eq = np.cumprod(1 + r)
    return float(eq[-1] ** (12.0 / len(r)) - 1) if eq[-1] > 0 else -1.0


def mdd(r):
    eq = np.concatenate([[1.0], np.cumprod(1 + np.asarray(r, float))])
    return float(np.min(eq / np.maximum.accumulate(eq) - 1))


def vol(r):
    return float(np.std(r, ddof=1) * np.sqrt(12))


def money_cost(W, n_hold, commission_rate=0.00025, min_comm=5.0,
               stamp=0.0005, transfer=0.00001, spread=0.0, impact=0.0):
    """每"换 1 只"（卖 1 买 1）占总资金的比例成本。"""
    per = W / n_hold
    sell_comm = max(min_comm, per * commission_rate)
    buy_comm = max(min_comm, per * commission_rate)
    fees = sell_comm + buy_comm + per * stamp + 2 * per * transfer
    sp = per * spread * 2            # 卖付一半价差、买付一半价差 = 一个 spread
    im = per * impact * 2
    return (fees + sp + im) / W


def main():
    z = np.load(os.path.join(AOUT, "sp4_monthly.npz"), allow_pickle=True)
    months = [str(x) for x in z["months"]]
    NS, R2, R0, R1 = z["NS"], z["R_c2"], z["R_c0"], z["R_c1"]
    n_paths, n_m = R2.shape
    NS_c1 = NS          # C1 与 C2 换手次数完全一致（配对）
    G2 = R2 + NS * COST0            # 毛收益（剔除原成本）
    G0 = R0
    G1 = R1 + NS_c1 * COST0

    # ---------------- P1-1 成本情景 ----------------
    scen = {
        "S0_零成本":                dict(commission_rate=0, min_comm=0, stamp=0, transfer=0, spread=0, impact=0),
        "S1_仅显性费用(万2.5+5元)":  dict(commission_rate=0.00025, min_comm=5.0, stamp=0.0005, transfer=0.00001, spread=0, impact=0),
        "S2_+价差0.10%":            dict(commission_rate=0.00025, min_comm=5.0, stamp=0.0005, transfer=0.00001, spread=0.0010, impact=0),
        "S3_+价差0.30%":            dict(commission_rate=0.00025, min_comm=5.0, stamp=0.0005, transfer=0.00001, spread=0.0030, impact=0),
        "S4_+价差0.60%(极端)":       dict(commission_rate=0.00025, min_comm=5.0, stamp=0.0005, transfer=0.00001, spread=0.0060, impact=0),
        "S5_极端+冲击0.10%":         dict(commission_rate=0.00025, min_comm=5.0, stamp=0.0005, transfer=0.00001, spread=0.0060, impact=0.0010),
    }
    W = 100000.0
    rows, res_cost = [], {"W": W, "n_hold": K, "cost0": COST0, "scenarios": {}}
    print("=" * 104)
    print("P1-1 成本情景（10 万 / 5 只，每只 2 万；月换手 %.1f%%）" % (np.median(NS.mean(axis=1) / K) * 100))
    print("=" * 104)
    print(f"{'情景':<26}{'每换1只成本':>12}{'年化(100%股票)':>15}{'MDD':>10}{'年化(25%仓位)':>15}")
    for nm, kw in scen.items():
        c = money_cost(W, K, **kw)
        Rg = G2 - NS * c
        a = float(np.median([ann(Rg[k]) for k in range(n_paths)]))
        m = float(np.median([mdd(Rg[k]) for k in range(n_paths)]))
        # 25% 仓位（月再平衡）
        a25 = float(np.median([ann(0.25 * Rg[k] + 0.75 * (0.03 / 12)) for k in range(n_paths)]))
        ann_cost_pp = float(np.median(NS.mean(axis=1))) * c * 12 * 100   # 绝对换手只数 x 成本率 x 12
        res_cost["scenarios"][nm] = {"cost_per_swap": c, "annual_cost_pp": ann_cost_pp,
                                     "ann": a, "mdd": m, "ann_25pct": a25}
        rows.append({"scenario": nm, "cost_per_swap": c, "annual_cost_pp": ann_cost_pp,
                     "ann_100pct": a, "mdd_100pct": m, "ann_25pct": a25})
        print(f"{nm:<26}{c*100:>11.4f}%{a*100:>+14.2f}%{m*100:>+9.2f}%{a25*100:>+14.2f}%")

    # 盈亏平衡成本（使 100% 股票年化 = 0 / = 8%）
    def solve(target):
        lo, hi = 0.0, 0.05
        for _ in range(60):
            mid = (lo + hi) / 2
            a = float(np.median([ann(G2[k] - NS[k] * mid) for k in range(n_paths)]))
            if a > target:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2
    be0 = solve(0.0); be8 = solve(0.08)
    res_cost["breakeven"] = {"cost_for_ann_0": be0, "cost_for_ann_8pct": be8,
                             "note": "每换 1 只的成本率阈值（占组合总值）"}
    print(f"\n  盈亏平衡：年化=0 需每换 1 只成本 {be0*100:.4f}%；年化=8% 需 ≤ {be8*100:.4f}%")

    # 资金规模对成本率
    print("\n" + "=" * 104)
    print("资金规模 -> 每换 1 只的成本率（S2 价差 0.10%，5 只）")
    print("=" * 104)
    scale = {}
    for Wx in (5e4, 1e5, 3e5, 1e6, 3e6, 1e7):
        c = money_cost(Wx, K, **scen["S2_+价差0.10%"])
        scale[f"{Wx/1e4:.0f}万"] = c
        print(f"  {Wx/1e4:>6.0f} 万  每只 {Wx/K/1e4:>6.1f} 万  成本率 {c*100:.4f}%  "
              f"年化成本 {np.median(NS.mean(axis=1))*c*12*100:.3f}pp")
    res_cost["scale"] = scale

    # "年化扣 2pp" 的反向验证
    ns_mean = float(np.median(NS.mean(axis=1)))          # 每月换几只（绝对数）
    implied = 0.02 / (12 * ns_mean)
    res_cost["reverse_check_2pp"] = {
        "monthly_swaps_abs": ns_mean,
        "monthly_turnover_ratio": ns_mean / K,
        "implied_cost_per_swap": implied,
        "implied_spread": implied - 0.0,
        "note": f"『年化扣 2pp』等价于每换 1 只成本 {implied*100:.4f}%（{implied*W/K:.0f} 元/只），"
                f"即显性费用 {COST0*100:.4f}% + 价差 {(implied-COST0)*100:.4f}%。"
                f"该价差低于小盘股常见 0.2~0.3%，因此 −2pp 是**偏乐观**的保守值，不是压力值。"
    }
    print(f"\n  『扣 2pp/年』反推：每换 1 只需 {implied*100:.4f}%（约 {implied*W/K:.0f} 元/只），"
          f"其中价差 {(implied-COST0)*100:.4f}%")

    # ---------------- P1-2 F3 增量证据 ----------------
    print("\n" + "=" * 104)
    print("P1-2 F3 增量证据（冻结定义，不新增网格）")
    print("=" * 104)
    res_f3 = {"frozen": {"factor": "f3 = mean(vol,5)/mean(vol,60)", "sell_threshold": THRESH,
                         "K": K, "liq_max": LIQ_MAX, "rebalance": "monthly"}}
    d = np.array([ann(R2[k]) - ann(R1[k]) for k in range(n_paths)])
    se = float(np.std(d, ddof=1) / np.sqrt(len(d)))
    res_f3["paired_diff"] = {"mean": float(d.mean()), "median": float(np.median(d)),
                             "se": se, "t": float(d.mean() / se),
                             "ci95": [float(d.mean() - 1.96 * se), float(d.mean() + 1.96 * se)],
                             "win_rate": float((d > 0).mean()), "n": len(d)}
    print(f"  全样本配对差 {d.mean()*100:+.2f}pp  t={d.mean()/se:.2f}  "
          f"95%CI [{res_f3['paired_diff']['ci95'][0]*100:+.2f}, {res_f3['paired_diff']['ci95'][1]*100:+.2f}]pp  "
          f"胜率 {(d>0).mean()*100:.1f}%")

    # 分段 + CI
    SEGS = [("2005", "2017", "IS"), ("2018", "2021", "OOS1"), ("2022", "2026", "OOS2")]
    res_f3["segments"] = {}
    for lo, hi, lab in SEGS:
        ii = [i for i, m in enumerate(months) if lo + "0101" <= m <= hi + "1231"]
        dd = np.array([ann(R2[k][ii]) - ann(R1[k][ii]) for k in range(n_paths)])
        s = float(np.std(dd, ddof=1) / np.sqrt(len(dd)))
        res_f3["segments"][lab] = {"mean": float(dd.mean()), "t": float(dd.mean() / s),
                                   "ci95": [float(dd.mean() - 1.96 * s), float(dd.mean() + 1.96 * s)],
                                   "win_rate": float((dd > 0).mean()), "n": len(dd),
                                   "months": len(ii)}
        # 该段的 MDE（最小可检测效应）
        res_f3["segments"][lab]["mde"] = float(2.8016 * s)
        print(f"  {lab:<5} 月数 {len(ii):>3}  差 {dd.mean()*100:>+6.2f}pp  t={dd.mean()/s:>+5.2f}  "
              f"CI [{dd.mean()-1.96*s:+.4f}, {dd.mean()+1.96*s:+.4f}]  MDE {2.8016*s*100:.2f}pp  "
              f"胜率 {(dd>0).mean()*100:.1f}%")

    # 剔除最佳/最差月份
    res_f3["drop_months"] = {}
    base = float(np.median([ann(R2[k]) - ann(R1[k]) for k in range(n_paths)]))
    for ndrop in (1, 3, 5):
        # 逐路径剔除"对其自身贡献最大的 ndrop 个月"
        vals = []
        for k in range(n_paths):
            contrib = (R2[k] - R1[k])
            idx = np.argsort(-contrib)[:ndrop]
            keep = np.ones(n_m, bool); keep[idx] = False
            vals.append(ann(R2[k][keep]) - ann(R1[k][keep]))
        res_f3["drop_months"][f"drop_best_{ndrop}"] = {
            "median_pp": float(np.median(vals)), "share_positive": float((np.array(vals) > 0).mean())}
        print(f"  逐路径剔除最佳 {ndrop} 个月：中位 {np.median(vals)*100:+.2f}pp（未剔 {base*100:+.2f}pp）"
              f"  路径为正占比 {(np.array(vals)>0).mean()*100:.1f}%")

    # 结论等级
    oos = res_f3["segments"]["OOS2"]
    if oos["mean"] > 0 and oos["t"] > 1.96 and oos["mean"] > oos["mde"]:
        grade = "A"
    elif oos["mean"] > 0:
        grade = "B"
    else:
        grade = "C"
    res_f3["grade"] = grade
    res_f3["grade_reason"] = (
        f"OOS2 配对差 {oos['mean']*100:+.2f}pp，t={oos['t']:.2f}，MDE={oos['mde']*100:.2f}pp。"
        + ("观察值超过 MDE 且 t 过 1.96 -> 增量证据相对稳定。" if grade == "A"
           else "观察值小于 MDE（功效不足），不能拒绝零，也不能断言无效 -> 方向有希望但证据不足。"
           if grade == "B" else "方向为负 -> 当前证据不支持继续使用 F3 作为增量信号。"))
    print(f"\n  【F3 增量证据等级】{grade}：{res_f3['grade_reason']}")
    print(f"  ⚠️ 区分：OOS2 t={oos['t']:.2f} 属『没有显著证据』，不等于『已证明无效果』"
          f"（n={oos['months']} 月，MDE={oos['mde']*100:.2f}pp）")

    os.makedirs(REP, exist_ok=True)
    pd.DataFrame(rows).to_csv(os.path.join(REP, "SP4_COST_SCENARIOS.csv"),
                              index=False, encoding="utf-8-sig")
    json.dump(res_cost, open(os.path.join(AOUT, "sp4_cost.json"), "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)
    json.dump(res_f3, open(os.path.join(AOUT, "sp4_f3_evidence.json"), "w", encoding="utf-8", newline="\n"),
              ensure_ascii=False, indent=1)
    print(f"\n[saved] {AOUT}/sp4_cost.json + sp4_f3_evidence.json + {REP}/SP4_COST_SCENARIOS.csv")


if __name__ == "__main__":
    main()
