#!/usr/bin/env python
"""阶段 N 审计（一）：核心数字对账 + 统计判据复核 + 成本敏感性。

## 纪律
- **只读**现有产物，不修改任何原始实验输出；
- 不改任何已冻结实验的参数、判据、窗口；
- 全部标记为「审计 / 敏感性分析」，不作确认性结论；
- 点估计越界 ≠ 统计否证（除非原判据本就只针对点估计）——本脚本会显式区分。

## 做什么
A. 对账 0.0424 / 0.0399 / 0.0507(≈0.0502) 三个成本后 alpha
B1. SURGE ratio：原判据是**点估计**区间 → 复核并给出 bootstrap 边界检验
B2. Holm 多重检验：独立重算 + **检验集合敏感性**（集合定义会改变 m）
D. 成本模型审计 + 费率敏感性（0.03% / 0.05% / 0.10% / 0.30%）+ A股不对称税费 + 盈亏平衡费率

用法：
    python scripts/audit_core_reconciliation.py
"""

from __future__ import annotations

import csv
import json
import math
import os
import statistics
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_c import NW_LAG  # noqa: E402

OUT_JSON = "reports/CORE_AUDIT_COMPUTED.json"

# 读取路径（只读）
P_I = "phasei_out/8f5c35121fdb/summary.json"
P_J = "phasej_out/f76de8a277f6/summary.json"
P_J_CSV = "phasej_out/f76de8a277f6/monthly.csv"
P_K = "phasek_out/3bc31a35903d/summary.json"
P_M = "phasem_out/2f5f61dd0b5f/monthly.csv"
P_M_SUM = "phasem_out/2f5f61dd0b5f/summary.json"

# 17 项实验的 |t|（来自产物）——用于 Holm
TESTS = [
    ("Gate 1 月频动量", 0.569, "预登记"), ("Gate 2 反转2016-2026", 1.642, "预登记"),
    ("Gate 3 反转2007-2015", 2.117, "预登记"), ("Gate 4 低波动", 0.868, "预登记"),
    ("Gate 5 活跃度", 0.155, "预登记"), ("Gate 6 反转全A", 4.386, "预登记"),
    ("阶段 C 池效应", 6.367, "预登记"), ("阶段 C-2 剔不可成交", 5.015, "预登记"),
    ("阶段 C-3 成交延迟", 0.597, "预登记"), ("阶段 E 反转单边", 1.901, "预登记"),
    ("阶段 F MAX5分层", 3.409, "预登记"), ("阶段 G MAX5单边", 1.764, "预登记"),
    ("阶段 H 排除Q5", 6.357, "**无预登记（评估）**"),
    ("阶段 I exQ5可成交", 4.528, "预登记"), ("阶段 J 冲击成本", 1.269, "预登记"),
    ("阶段 K 降换手", 1.433, "预登记"), ("阶段 L SURGE", 3.130, "预登记"),
]


def p_two_sided(t: float) -> float:
    return math.erfc(abs(t) / math.sqrt(2.0))


def holm(pairs):
    m = len(pairs)
    order = sorted(range(m), key=lambda i: pairs[i][1])
    out, still = {}, True
    for rank, i in enumerate(order):
        thr = 0.05 / (m - rank)
        rej = pairs[i][1] <= thr and still
        if not rej:
            still = False
        out[pairs[i][0]] = {"p": pairs[i][1], "holm_threshold": thr, "reject": bool(rej)}
    return out


def nw_stat(x, lag=NW_LAG):
    nw = C.newey_west(x, lag)
    return {"mean": statistics.mean(x), "t": nw["t_nw"], "se": nw["se_nw"]}


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print("阶段 N 审计（一）：核心对账 / 统计判据 / 成本敏感性")
    print("=" * 78)

    i_sum = json.load(open(P_I, encoding="utf-8"))
    j_sum = json.load(open(P_J, encoding="utf-8"))
    k_sum = json.load(open(P_K, encoding="utf-8"))
    m_sum = json.load(open(P_M_SUM, encoding="utf-8"))

    # ==================== A. 核心数字对账
    print()
    print("【A】三个成本后 alpha 的对账")
    a_i = i_sum["V1_executable"]["mean_pp"]
    comm_i = i_sum["cost"]["0.3pct"]["cost_pp_month"]
    net_i = i_sum["cost"]["0.3pct"]["net_pp_month"]
    t_i = i_sum["cost"]["0.3pct"]["t_net"]
    j3 = j_sum["by_capital"]["3000万"]
    k1 = k_sum["by_period"]["1"]

    print(f"   阶段 I（8f5c3512）：alpha {a_i:.6f} − 佣金 {comm_i:.6f} = 净 **{net_i:.6f}**  t(iid)={t_i:.3f}")
    print(f"     → 无现金残留、无冲击。报告中出现的 0.0507 是 0.1634−0.1127 的近似，")
    print(f"       而 0.1127 是**阶段 J** 的佣金；阶段 I 自身佣金为 {comm_i:.6f} → 净 {net_i:.6f}")
    print(f"   阶段 J（f76de8a2, W=3000万）：净 **{j3['net_A_pp']:.6f}**  t(NW)={j3['t_net_A']:.3f}")
    print(f"     折项：现金残留 {j3['cash_drag_pp']:.6f} + 佣金 {j3['comm_pp']:.6f} "
          f"+ 冲击 {j3['impact_A_pp']:.6f}")
    print(f"   阶段 K（3bc31a35, m=1）：净 **{k1['net_pp']:.6f}**  t(NW6)={k1['t_nw6']:.3f}")
    print(f"     佣金 {k1['comm_pp']:.6f}（换手 {k1['turnover_pct_month']:.3f}%）")

    d1 = net_i - j3["net_A_pp"]
    d2 = j3["net_A_pp"] - k1["net_pp"]
    print()
    print(f"   差额解释：")
    print(f"     阶段I − 阶段J = {d1:+.6f}  ≈ 现金残留 {j3['cash_drag_pp']:.6f} "
          f"+ 冲击 {j3['impact_A_pp']:.6f} − 佣金口径差 "
          f"{comm_i - j3['comm_pp']:+.6f}")
    print(f"     阶段J − 阶段K = {d2:+.6f}  ≈ 佣金口径差 "
          f"{k1['comm_pp'] - j3['comm_pp']:+.6f}"
          f"（K 把建仓首期 churn 记为 1.0）")
    verdict_a = "三个数字**来自不同实验与不同成本口径**，非同一量的三个值"
    print(f"   >>> {verdict_a}")

    # ==================== B1. SURGE ratio 判据
    print()
    print("【B1】SURGE ratio：原判据到底是哪种？")
    rows = list(csv.DictReader(open(P_M, encoding="utf-8")))
    sp = np.array([float(r["spread_pp"]) for r in rows])
    by = np.array([float(r["alpha_buy_pp"]) for r in rows])
    rb_point = by.mean() / sp.mean()
    rng = np.random.default_rng(20261021)
    m_ = len(sp)
    draws = []
    for _ in range(5000):
        k = rng.integers(0, m_, m_)
        s = sp[k].mean()
        if s != 0:
            draws.append(by[k].mean() / s)
    draws = np.array(draws)
    lo, hi = np.percentile(draws, [2.5, 97.5])
    p_gt = float((draws > 0.35).mean())     # 探索性：ratio 真值 > 0.35 的 bootstrap 证据
    print(f"   点估计 {rb_point:.4f}   bootstrap 95% [{lo:.3f}, {hi:.3f}]   n={m_}")
    print(f"   预登记原文：『H1 = ratio_buy ∈ [0.10, 0.35]』—— **只针对点估计**，")
    print(f"     未规定 CI 落入区间，也未规定对边界做检验。")
    print(f"   → 原判定 SHORT_SIDE_REJECTED **保留**（它按预登记只判点估计）。")
    print(f"   → 但按纪律 9：点估计越界**不等于统计否证**。")
    print(f"     探索性 bootstrap：P(ratio>0.35) = {p_gt:.3f}"
          f" → {'无' if p_gt < 0.95 else '有'}统计证据支持 ratio 超出上界")

    # ==================== B2. Holm
    print()
    print("【B2】Holm 多重检验：独立重算 + 检验集合敏感性")
    sets = {
        "A_全部17项": TESTS,
        "B_剔除无预登记的阶段H": [t for t in TESTS if t[0] != "阶段 H 排除Q5"],
        "C_仅独立新假设(去掉C-2/C-3/J/K等同一构造的稳健性)":
            [t for t in TESTS if t[0] not in
             ("阶段 C-2 剔不可成交", "阶段 C-3 成交延迟", "阶段 J 冲击成本",
              "阶段 K 降换手", "阶段 H 排除Q5")],
    }
    holm_out = {}
    for name, ts in sets.items():
        h = holm([(n, p_two_sided(t)) for n, t, _ in ts])
        surv = [n for n, v in h.items() if v["reject"]]
        holm_out[name] = {"m": len(ts), "survived": surv, "n_survived": len(surv),
                          "detail": {k: {"p": round(v["p"], 8),
                                         "holm_threshold": round(v["holm_threshold"], 8),
                                         "reject": v["reject"]} for k, v in h.items()}}
        print(f"   {name}: m={len(ts)} → 存活 {len(surv)}")

    print()
    print("   ⚠️ 口径检查：")
    print("     - p 值由 |t| 经**正态近似**得到（非 t 分布）；n=48 的 Gate 1 会有轻微反保守")
    print("     - 集合定义会改变结论：m 越小，阈值越松")

    # ==================== D. 成本模型与费率敏感性
    print()
    print("【D】成本模型审计 + 费率敏感性")
    mrows = list(csv.DictReader(open(P_J_CSV, encoding="utf-8")))
    to = np.array([float(r["to"]) for r in mrows])
    al = np.array([float(r["alpha_v1_pp"]) for r in mrows])
    fi = np.array([float(r["f_inv_1000w"]) for r in mrows])
    imp = np.array([float(r["impact_A_pp"]) for r in mrows])
    print(f"   月度序列 n={len(to)}  换手中位 {np.median(to)*100:.2f}%  "
          f"f_inv(1000万) 中位 {np.median(fi):.4f}")

    fees = (0.0003, 0.0005, 0.0010, 0.0030)
    sens = {}
    for f in fees:
        net = fi * al - 2.0 * to * f * 100.0 - imp
        st = nw_stat(list(net))
        sd = statistics.stdev(net)
        se_iid = sd / math.sqrt(len(net))
        sens[f"{f*100:.2f}pct"] = {
            "gross_alpha_pp": round(float(al.mean()), 6),
            "cost_pp": round(float((2.0 * to * f * 100.0).mean()), 6),
            "impact_pp": round(float(imp.mean()), 8),
            "net_pp": round(st["mean"], 6),
            "t_iid": round(st["mean"] / se_iid, 4),
            "t_nw": round(st["t"], 4),
            "ci95_iid": [round(st["mean"] - 1.96 * se_iid, 6),
                         round(st["mean"] + 1.96 * se_iid, 6)],
            "ci95_nw": [round(st["mean"] - 1.96 * st["se"], 6),
                        round(st["mean"] + 1.96 * st["se"], 6)],
        }
        s = sens[f"{f*100:.2f}pct"]
        print(f"   单边 {f*100:.2f}%: 成本 {s['cost_pp']:.4f}pp → 净 {s['net_pp']:+.4f}pp  "
              f"t(iid)={s['t_iid']:+.2f}  t(NW)={s['t_nw']:+.2f}  "
              f"CI_NW[{s['ci95_nw'][0]:+.4f},{s['ci95_nw'][1]:+.4f}]")

    base = float((fi * al - imp).mean())
    be = base / (2.0 * float(to.mean()) * 100.0)
    print(f"   盈亏平衡单边费率 = {be*100:.4f}%（净 alpha 归零）")

    # A 股真实税费结构（不对称）：佣金双边 + 印花税仅卖出 + 过户费双边
    comm, transfer, stamp = 0.00025, 0.00001, 0.0005
    cost_asy = 2.0 * to * (comm + transfer) * 100.0 + to * stamp * 100.0
    net_asy = fi * al - cost_asy - imp
    st_asy = nw_stat(list(net_asy))
    eq_sym = ((2 * (comm + transfer) + stamp) / 2.0)
    print()
    print(f"   A股不对称税费（佣金{comm*100:.3f}%双边 + 印花税{stamp*100:.3f}%仅卖出 "
          f"+ 过户费{transfer*100:.3f}%双边）：")
    print(f"     成本 {cost_asy.mean():.4f}pp → 净 {st_asy['mean']:+.4f}pp  t(NW)={st_asy['t']:+.2f}")
    print(f"     等价**对称**单边综合费率 ≈ {eq_sym*100:.3f}%")
    print(f"   ⚠️ 原报告的 0.3% 是这个等价值的 **{0.003/eq_sym:.1f} 倍** → 属**压力测试参数**，")
    print(f"     不是可实际获得的报价。")

    # 不同换手水平（阶段 K 的 m=1..6）
    print()
    print("   不同换手水平（阶段 K 各档，W=3000万，按其自身 alpha 与 f_inv）：")
    by_t = {}
    for m_, v in k_sum["by_period"].items():
        t_ = v["turnover_pct_month"] / 100.0
        a_ = v["alpha_pp"]
        f_ = v["f_inv_mean"]
        row = {"turnover_pct": v["turnover_pct_month"], "alpha_pp": a_, "f_inv": f_}
        for f in fees:
            row[f"net_{f*100:.2f}pct_pp"] = round(f_ * a_ - 2 * t_ * f * 100 - 0.000784, 6)
        by_t[f"m{m_}"] = row
        print(f"     m={m_}: 换手 {row['turnover_pct']:.2f}%  "
              + "  ".join(f"@{f*100:.2f}%→{row[f'net_{f*100:.2f}pct_pp']:+.4f}" for f in fees))

    out = {
        "script": "audit_core_reconciliation",
        "kind": "审计 / 敏感性分析（**非**确认性实验）",
        "A_reconciliation": {
            "phaseI": {"alpha_pp": a_i, "comm_pp": comm_i, "net_pp": net_i, "t_iid": t_i},
            "phaseJ_3000w": {k: j3[k] for k in
                             ("alpha_v1_pp", "f_inv_median", "cash_drag_pp", "comm_pp",
                              "impact_A_pp", "net_A_pp", "t_net_A", "t_net_A_iid")},
            "phaseK_m1": {k: k1[k] for k in
                          ("turnover_pct_month", "comm_pp", "alpha_pp", "f_inv_mean",
                           "net_pp", "t_nw6", "t_nw3", "t_iid")},
            "diff_I_minus_J": round(d1, 6), "diff_J_minus_K": round(d2, 6),
            "verdict": verdict_a,
        },
        "B1_surge_ratio": {
            "point_estimate": round(float(rb_point), 6),
            "bootstrap_ci95": [round(float(lo), 4), round(float(hi), 4)],
            "prereg_interval": [0.10, 0.35],
            "preregulated_criterion_type": "**点估计**落入区间（原文未规定 CI 或边界检验）",
            "original_verdict_retained": "SHORT_SIDE_REJECTED",
            "exploratory_P_ratio_gt_0.35": round(p_gt, 4),
            "caveat": "点估计越界 ≠ 统计否证；原判据只针对点估计，必须明确说明",
        },
        "B2_holm": holm_out,
        "D_cost_sensitivity": {
            "basis": "phasej monthly.csv（W=1000万 的 f_inv；逐月 to/alpha/impact）",
            "by_fee": sens,
            "breakeven_fee_one_side_pct": round(be * 100, 4),
            "a_share_asymmetric": {
                "commission_pct": comm * 100, "stamp_duty_sell_only_pct": stamp * 100,
                "transfer_fee_pct": transfer * 100,
                "cost_pp": round(float(cost_asy.mean()), 6),
                "net_pp": round(st_asy["mean"], 6), "t_nw": round(st_asy["t"], 4),
                "equivalent_symmetric_one_side_pct": round(eq_sym * 100, 4),
            },
            "by_turnover_level": by_t,
        },
        "runtime_sec": round(time.time() - t0, 1),
    }
    os.makedirs("reports", exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print()
    print(f"[产物] {OUT_JSON}   耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
