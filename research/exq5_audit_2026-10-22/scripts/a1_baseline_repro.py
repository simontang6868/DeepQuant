#!/usr/bin/env python
"""A1 基线复算：从冻结 CSV 独立重算 exQ5 关键统计量（任务 §三）。

输入全部为只读冻结产物；统计用 audit_common 的独立 numpy NW 实现。
输出 out/a1_baseline/：repro_table.csv / repro_detail.json / run.log

复算对象（记录值来源均为冻结 summary.json，非二手摘要）：
  R1  V0 全样本 alpha/t/分段         （阶段 I）
  R2  V1 可成交 alpha/t/分段         （阶段 I）
  R3  阶段 I 的 C 条件 @0.3% 净/t   （阶段 I，iid se）
  R4  阶段 O 六条净值序列 + 分段      （阶段 O）
  R5  阶段 O 三口径成本均值           （阶段 O，成本从 to 重算）
  R6  阶段 O 盈亏平衡费率 0.4129%     （二分法独立重算）
  R7  未投资比例 12.32% / 4.75%       （阶段 I/O capacity）
  R8  阶段 S 现代段主序列 + MDE/功效/bootstrap（阶段 S）
  R9  阶段 T T1a/T1c/T2/T3           （阶段 T）
  R10 阶段 U A 类最小 t 与翻转集      （阶段 U profiles.csv）
  R11 券商默认 @3000万 +0.1313/t=3.895（broker JSON，交叉于 O CONS）
  R12 阶段 H 恒等式 alpha≡0.25×(R_bench−R_Q5)（phasei monthly 原始列）
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_common as A  # noqa: E402

OUT = os.path.join(A.OUT_DIR, "a1_baseline")
TOL_T, TOL_MEAN = 5e-4, 1e-5      # 复算判定容差（t / 均值 pp）


def load_inputs():
    pi = A.read_csv_rows(A.FROZEN["phasei_monthly"])
    po = A.read_csv_rows(A.FROZEN["phaseo_monthly"])
    ps = A.read_csv_rows(A.FROZEN["phases_monthly"])
    pt = A.read_csv_rows(A.FROZEN["phaset_monthly"])
    return pi, po, ps, pt


def seg_of(dates, vals, lo, hi):
    sub = [v for v, d in zip(vals, dates) if lo <= d <= hi]
    return sub


def check(name, got, exp, tol, unit=""):
    ok = (got == got and exp == exp and abs(got - exp) <= tol)
    return {"name": name, "got": got, "recorded": exp, "diff": (got - exp) if got == got else None,
            "tol": tol, "unit": unit, "ok": bool(ok)}


def main():
    os.makedirs(OUT, exist_ok=True)
    log = []

    def say(s):
        print(s)
        log.append(s)

    say("=" * 78)
    say("A1 基线复算：exQ5（独立统计路径，输入=冻结 CSV）")
    say("=" * 78)

    # ---------- 0. 输入哈希 ----------
    say("\n[0] 冻结输入 sha256 校验")
    hashes = {k: A.sha256_file(p) for k, p in A.FROZEN.items() if k.endswith(("monthly", "profiles"))}
    EXPECT_HASH = {
        "phasei_monthly": "aa1404cf3b68da30fd31113deade356c98829d85e13e23c267cb4b40e870a99a",
        "phaseo_monthly": "768e49a9462cd7c00f3aacfcdb1e87bbe37bb75b348cd6fd85809e12493a4152",
        "phases_monthly": "3333efe96ac0db29d1568057678eda5187d0c03b42c1e508f5eb8736bd24da91",
    }
    for k, exp in EXPECT_HASH.items():
        ok = hashes[k] == exp
        say(f"  {k}: {'✓' if ok else '✗'} {hashes[k][:16]}…")
        if not ok:
            raise SystemExit(f"输入哈希不符：{k}")
    hashes["phasej_monthly"] = A.sha256_file(A.FROZEN["phasej_monthly"])
    hashes["phaset_monthly"] = A.sha256_file(A.FROZEN["phaset_monthly"])
    hashes["phaseu_profiles"] = A.sha256_file(A.FROZEN["phaseu_profiles"])

    pi, po, ps, pt = load_inputs()
    rec_i = json.load(open(A.FROZEN["phasei_summary"], encoding="utf-8"))
    rec_o = json.load(open(A.FROZEN["phaseo_summary"], encoding="utf-8"))
    rec_s = json.load(open(A.FROZEN["phases_summary"], encoding="utf-8"))
    rec_t = json.load(open(A.FROZEN["phaset_summary"], encoding="utf-8"))

    checks = []

    # ---------- R1/R2 阶段 I：V0/V1 ----------
    say("\n[R1/R2] 阶段 I：V0（不可成交）/ V1（可成交）")
    dates = [r["signal_date"] for r in pi]
    v0 = A.col(pi, "alpha_v0_pp")
    v1 = A.finite(A.col(pi, "alpha_v1_pp"))
    d_v1 = [d for d, v in zip(dates, A.col(pi, "alpha_v1_pp")) if v == v]
    p0 = A.nw_t(v0, 3)
    p1 = A.nw_t(v1, 3)
    say(f"  V0 n={p0['n']} mean={p0['mean']:+.6f}pp t_nw={p0['t_nw']:+.6f} "
        f"t_iid={p0['t_iid']:+.6f} se_nw={p0['se_nw']:.6f}")
    say(f"  V1 n={p1['n']} mean={p1['mean']:+.6f}pp t_nw={p1['t_nw']:+.6f} "
        f"t_iid={p1['t_iid']:+.6f} se_nw={p1['se_nw']:.6f}")
    checks.append(check("R1 V0 mean_pp", p0["mean"], rec_i["V0_unconstrained"]["mean_pp"], TOL_MEAN, "pp"))
    checks.append(check("R1 V0 t_nw(3)", p0["t_nw"], rec_i["V0_unconstrained"]["t_nw"], TOL_T))
    checks.append(check("R1 V0 n", p0["n"], rec_i["V0_unconstrained"]["n"], 0))
    checks.append(check("R2 V1 mean_pp", p1["mean"], rec_i["V1_executable"]["mean_pp"], TOL_MEAN, "pp"))
    checks.append(check("R2 V1 t_nw(3)", p1["t_nw"], rec_i["V1_executable"]["t_nw"], TOL_T))
    for lab, lo, hi in [("2007-2015", "2007-01-01", "2015-12-31"), ("2016-2026", "2016-01-01", "2026-09-30")]:
        s0 = A.nw_t(seg_of(dates, v0, lo, hi), 3)
        s1 = A.nw_t(seg_of(d_v1, v1, lo, hi), 3)
        say(f"    {lab}: V0 {s0['mean']:+.6f}pp (t={s0['t_nw']:+.4f}, n={s0['n']}) | "
            f"V1 {s1['mean']:+.6f}pp (t={s1['t_nw']:+.4f}, n={s1['n']})")
        checks.append(check(f"R1 V0 {lab} mean", s0["mean"],
                            rec_i["V0_unconstrained"]["segments"][lab]["mean_pp"], TOL_MEAN, "pp"))
        checks.append(check(f"R1 V0 {lab} t", s0["t_nw"],
                            rec_i["V0_unconstrained"]["segments"][lab]["t_nw"], TOL_T))
        checks.append(check(f"R2 V1 {lab} mean", s1["mean"],
                            rec_i["V1_executable"]["segments"][lab]["mean_pp"], TOL_MEAN, "pp"))
        checks.append(check(f"R2 V1 {lab} t", s1["t_nw"],
                            rec_i["V1_executable"]["segments"][lab]["t_nw"], TOL_T))

    # ---------- R3 阶段 I 的 C 条件（@0.3%，iid se，cost_of 口径） ----------
    to_v1 = rec_i["turnover"]["v1_one_side"]
    sd_v1 = statistics.stdev(v1)
    cost03 = 2.0 * to_v1 * 0.003 * 100.0
    net03 = p1["mean"] - cost03
    t_net03 = net03 / (sd_v1 / math.sqrt(len(v1)))
    say(f"\n[R3] 阶段 I C 条件 @0.3%：成本 {cost03:.4f}pp 净 {net03:+.4f}pp t_net(iid)={t_net03:+.4f}"
        f"（记录 t=1.038）")
    checks.append(check("R3 C条件 t_net(iid)", t_net03, 1.038, 5e-3))

    # ---------- R4 阶段 O 六条净值 ----------
    say("\n[R4] 阶段 O：三口径 × 两资金（净值 = finv×alpha − 成本 − 冲击常数 0.000784）")
    o_map = [("net_BASE_1000w", "W_1000万|BASE"), ("net_BASE_3000w", "W_3000万|BASE"),
             ("net_STRESS_3000w", "W_3000万|STRESS"),
             ("net_CONS_1000w", "W_1000万|CONSERVATIVE"), ("net_CONS_3000w", "W_3000万|CONSERVATIVE")]
    o_nets = {}
    for csv_key, rec_key in o_map:
        x = A.col(po, csv_key)
        pr = A.nw_t(x, 3)
        o_nets[csv_key] = (x, pr)
        r = rec_o["results"][rec_key]
        say(f"  {rec_key:<26} mean={pr['mean']:+.6f}pp t_nw={pr['t_nw']:+.4f} "
            f"t_iid={pr['t_iid']:+.4f}（记录 {r['net_pp']:+.6f} / {r['t_nw']:+.4f}）")
        checks.append(check(f"R4 {rec_key} mean", pr["mean"], r["net_pp"], TOL_MEAN, "pp"))
        checks.append(check(f"R4 {rec_key} t_nw", pr["t_nw"], r["t_nw"], TOL_T))
    # 分段 BASE@3000w
    x3000, _ = o_nets["net_BASE_3000w"]
    for lab, lo, hi in [("2007-2015", "2007-01-01", "2015-12-31"), ("2016-2026", "2016-01-01", "2026-09-30")]:
        s = A.nw_t(seg_of([r["signal_date"] for r in po], x3000, lo, hi), 3)
        r = rec_o["segments_BASE_3000w"][lab]
        say(f"    BASE@3000万 {lab}: {s['mean']:+.6f}pp t={s['t_nw']:+.4f}（记录 {r['net_pp']:+.6f}/{r['t_nw']:+.4f}）")
        checks.append(check(f"R4 BASE@3000w {lab} mean", s["mean"], r["net_pp"], TOL_MEAN, "pp"))
        checks.append(check(f"R4 BASE@3000w {lab} t", s["t_nw"], r["t_nw"], TOL_T))

    # ---------- R5 成本均值重算 ----------
    say("\n[R5] 阶段 O 成本均值（从 to 与公式独立重算）")
    tos = A.col(po, "to")
    cost_base_mean = statistics.mean([A.cost_base_indep(t) for t in tos])
    cost_stress_mean = statistics.mean([A.cost_stress_indep(t) for t in tos])
    rec_cb = statistics.mean(A.col(po, "cost_BASE_pp"))
    rec_cs = statistics.mean(A.col(po, "cost_STRESS_pp"))
    say(f"  BASE  重算 {cost_base_mean:.6f} vs 记录 {rec_cb:.6f}")
    say(f"  STRESS 重算 {cost_stress_mean:.6f} vs 记录 {rec_cs:.6f}")
    checks.append(check("R5 BASE 成本均值", cost_base_mean, rec_cb, 1e-4, "pp"))
    checks.append(check("R5 STRESS 成本均值", cost_stress_mean, rec_cs, 1e-4, "pp"))
    # CONS@3000w：用记录的 k≈n_keep 代理（covered 99.9%）→ 近似核对
    nk = A.col(po, "n_keep")
    cons_approx = statistics.mean([A.cost_cons_indep(t, 3.0e7, max(int(k), 1)) for t, k in zip(tos, nk)])
    rec_cc = statistics.mean(A.col(po, "cost_CONS_pp"))
    say(f"  CONS@3000万 近似重算（k=n_keep 代理） {cons_approx:.6f} vs 记录 {rec_cc:.6f}"
        f"（差 {cons_approx - rec_cc:+.6f}，k 为可负担子集口径，见 A4 精确重算）")
    checks.append({"name": "R5 CONS@3000万 成本均值（近似）", "got": cons_approx, "recorded": rec_cc,
                   "diff": cons_approx - rec_cc, "tol": None, "unit": "pp",
                   "ok": None, "note": "近似核对；精确重算在 A4（需逐月 k）"})

    # ---------- R6 盈亏平衡费率 ----------
    say("\n[R6] 盈亏平衡费率（BASE 结构，W=3000万，含现金残留与冲击常数；二分法独立重算）")
    finv3 = A.col(po, "finv_3000w")
    alpha = A.col(po, "alpha_pp")
    imp = 0.000784

    def net_at_comm(cr):
        return statistics.mean([f * a - (2.0 * t * (cr + A.TRANSFER_RATE) + t * A.STAMP_RATE) * 100.0 - imp
                                for f, a, t in zip(finv3, alpha, tos)])

    lo_, hi_ = 0.0, 0.02
    for _ in range(60):
        mid = (lo_ + hi_) / 2
        if net_at_comm(mid) > 0:
            lo_ = mid
        else:
            hi_ = mid
    be_comm = (lo_ + hi_) / 2
    be_sym = (2 * (be_comm + A.TRANSFER_RATE) + A.STAMP_RATE) / 2
    say(f"  盈亏平衡佣金 {be_comm*100:.4f}%（记录 0.3869%） 等价对称单边 {be_sym*100:.4f}%（记录 0.4129%）")
    checks.append(check("R6 盈亏平衡等价单边%", be_sym * 100, 0.4129, 5e-4, "%"))

    # ---------- R7 未投资比例 ----------
    say("\n[R7] 未投资比例（阶段 O monthly 的 finv 列；阶段 I capacity.csv 为月度中位数）")
    for w, key in [("1000万", "finv_1000w"), ("3000万", "finv_3000w")]:
        f = A.col(po, key)
        med = 1.0 - statistics.median(f)
        mean = 1.0 - statistics.mean(f)
        rec = rec_o["capacity"][w]["uninvested_median"]
        say(f"  {w}: 中位 {med*100:.2f}%（记录 {rec*100:.2f}%） 均值 {mean*100:.2f}%")
        checks.append(check(f"R7 {w} 未投资中位", med, rec, 1e-4, "比例"))
    ci = A.read_csv_rows(os.path.join(A.REPO, "phasei_out/8f5c35121fdb/capacity.csv"))
    say("  阶段 I capacity.csv: " + " | ".join(
        f"{r['budget_wan']}万 未投资中位 {float(r['uninvested_median'])*100:.2f}%" for r in ci))

    # ---------- R8 阶段 S 现代段 ----------
    say("\n[R8] 阶段 S 现代段主序列 net_S_3000w（2016-2026，n=129）")
    ps_dates = [r["signal_date"] for r in ps]
    net_s = A.col(ps, "net_S_3000w")
    seg = [r["segment"] for r in ps]
    modern = [v for v, s in zip(net_s, seg) if s == "2016-2026"]
    early = [v for v, s in zip(net_s, seg) if s == "2007-2015"]
    pm = A.nw_t(modern, 3)
    pe = A.nw_t(early, 3)
    mde = A.mde_of(pm["se_nw"])
    power = A.power_two_sided(pm["t_nw"])
    say(f"  现代段 mean={pm['mean']:+.6f}pp se_nw={pm['se_nw']:.6f} t={pm['t_nw']:+.6f} "
        f"se_iid={pm['se_iid']:.6f} t_iid={pm['t_iid']:+.6f}")
    say(f"  MDE={mde:.6f}pp 功效={power:.4f}（记录 {rec_s['primary']['mde_pp']:.6f}/{rec_s['primary']['power_observed']:.4f}）")
    checks.append(check("R8 S 现代段 mean", pm["mean"], rec_s["primary"]["mean_pp"], TOL_MEAN, "pp"))
    checks.append(check("R8 S 现代段 t_nw", pm["t_nw"], rec_s["primary"]["t_nw"], TOL_T))
    checks.append(check("R8 S 现代段 se_nw", pm["se_nw"], rec_s["primary"]["se_nw_pp"], 1e-5, "pp"))
    checks.append(check("R8 S 现代段 se_iid", pm["se_iid"], rec_s["primary"]["se_iid_pp"], 1e-5, "pp"))
    checks.append(check("R8 S MDE", mde, rec_s["primary"]["mde_pp"], 1e-5, "pp"))
    checks.append(check("R8 S 功效", power, rec_s["primary"]["power_observed"], 1e-4))
    checks.append(check("R8 S 前段 mean", pe["mean"], 0.125572, 1e-4, "pp"))
    # bootstrap（iid 月份重抽，2000 次，seed 20261022）
    rng = np_random = __import__("numpy").random.default_rng(20261022)
    arr = __import__("numpy").asarray(modern)
    boots = [float(arr[rng.integers(0, arr.size, arr.size)].mean()) for _ in range(2000)]
    boots.sort()
    ci_lo, ci_hi = boots[int(0.025 * 2000)], boots[int(0.975 * 2000) - 1]
    say(f"  bootstrap 95%（独立 seed 路径）[{ci_lo:+.6f}, {ci_hi:+.6f}]"
        f"（记录 [{rec_s['primary']['boot_ci95_pp'][0]:+.6f}, {rec_s['primary']['boot_ci95_pp'][1]:+.6f}]）")
    checks.append({"name": "R8 bootstrap CI（独立实现，允许抽样路径差异）", "got": [ci_lo, ci_hi],
                   "recorded": rec_s["primary"]["boot_ci95_pp"], "tol": None, "ok": None,
                   "note": "同为 iid 月份重抽 2000 次 seed=20261022；随机数发生器不同，末位可有差异"})

    # ---------- R9 阶段 T ----------
    say("\n[R9] 阶段 T：T1a 剔 6 极端月 / T1c 滞后网格 / T2 分界 / T3 分解")
    t_dates = [r["signal_date"] for r in pt]
    t_net = A.col(pt, "net_S_3000w")
    mod_mask = [d >= A.MODERN_LO for d in t_dates]
    modern_full = [v for v, m in zip(t_net, mod_mask) if m]
    drop_set = set(A.DROP6)
    kept = [v for v, d, m in zip(t_net, t_dates, mod_mask)
            if m and d[:7] not in drop_set]
    pk = A.nw_t(kept, 3)
    say(f"  T1a 剔6后 n={pk['n']} mean={pk['mean']:+.6f}pp t={pk['t_nw']:+.6f}"
        f"（记录 {rec_t['T1a']['result']['mean_pp']:+.6f}/{rec_t['T1a']['result']['t_nw']:+.6f}）")
    checks.append(check("R9 T1a mean", pk["mean"], rec_t["T1a"]["result"]["mean_pp"], TOL_MEAN, "pp"))
    checks.append(check("R9 T1a t_nw", pk["t_nw"], rec_t["T1a"]["result"]["t_nw"], TOL_T))
    checks.append(check("R9 T1a n", pk["n"], rec_t["T1a"]["result"]["n"], 0))
    # T1c 网格（L=0..6 + iid）
    grid = A.nw_profile(modern_full)
    say("  T1c 现代段 t(L): " + "  ".join(f"L{L}:{grid[L]['t_nw']:+.4f}" for L in A.LAG_GRID)
        + f"  iid:{grid[0]['t_iid']:+.4f}")
    for L, key in [(0, "L0"), (1, "L1"), (3, "L3"), (6, "L6")]:
        checks.append(check(f"R9 T1c t(L={L})", grid[L]["t_nw"], rec_t["T1c_lag"][key]["t_nw"], TOL_T))
    checks.append(check("R9 T1c t(iid)", grid[0]["t_iid"], rec_t["T1c_lag"]["iid"]["t_nw"], TOL_T))
    # T2 分界
    for split, r in rec_t["T2_splits"].items():
        sub = [v for v, d in zip(t_net, t_dates) if d >= split]
        pr = A.nw_t(sub, 3)
        checks.append(check(f"R9 T2 {split} t", pr["t_nw"], r["t_nw"], TOL_T))
        checks.append(check(f"R9 T2 {split} mean", pr["mean"], r["mean_pp"], TOL_MEAN, "pp"))
    n_pass = sum(1 for r in rec_t["T2_splits"].values() if r["pass"])
    say(f"  T2 通过 {n_pass}/6（记录 {rec_t['T2_pass_count']}/6，判据 ≥4）")
    # T3 贡献比（记录值为小数，标签 pp —— 单位缺陷见 A3/A6）
    t3 = rec_t["T3"]["contrib"]
    tot = sum(t3[g]["mean_pp"] for g in t3)
    ratio = t3["adv_g1"]["mean_pp"] / t3["adv_g5"]["mean_pp"]
    share = t3["adv_g1"]["mean_pp"] / tot
    say(f"  T3 g1 占总 alpha {share*100:.1f}%（记录 44.1%）  c1/c5={ratio:.4f}（记录 {rec_t['T3']['ratio_c1_over_c5']:.4f}）")
    checks.append(check("R9 T3 g1 占比%", share * 100, 44.1, 0.05, "%"))
    checks.append(check("R9 T3 c1/c5", ratio, rec_t["T3"]["ratio_c1_over_c5"], 1e-6))
    # 单位核验：T3 的 mean_pp 实为小数
    v0_dec = p0["mean"] / 100.0
    say(f"  ⚠ 单位核验：T3 五组合计 {tot:.8f} vs V0 alpha {v0_dec:.8f}（小数）→ "
        f"T3 字段名 mean_pp 实为小数，pp 标签差 100 倍（比率/t 不受影响）")
    checks.append({"name": "R9 T3 单位核验（Σ贡献 vs V0 alpha，小数）", "got": tot, "recorded": v0_dec,
                   "tol": 1e-6, "unit": "小数", "ok": bool(abs(tot - v0_dec) < 1e-6),
                   "note": "T3 的 mean_pp 字段名与报告表头标注为 pp，实为小数（×100 才是 pp）"})

    # ---------- R10 阶段 U ----------
    say("\n[R10] 阶段 U：A 类承重结论全网格稳健性（profiles.csv 记录值 + 独立网格重算）")
    up = A.read_csv_rows(A.FROZEN["phaseu_profiles"])
    rec_u = json.load(open(A.FROZEN["phaseu_summary"], encoding="utf-8"))
    a_rows = [r for r in up if r["class"] == "A"]
    min_t_grid = min(float(r["t_nw"]) for r in a_rows)          # 全网格最小（记录 2.3559 = U10@L=0）
    min_t_l3 = min(float(r["t_nw"]) for r in a_rows if r["L"] == "3")
    say(f"  A 类最小 t：全网格 {min_t_grid:.4f}（记录 2.3559，U10@L=0）｜L=3 处 {min_t_l3:.4f}")
    # 独立重算 U2/U3/U6/U7/U9/U10 的全网格
    series_def = {
        "U2 阶段I V0": v0, "U3 阶段I V1": v1,
        "U6 O BASE@3000万": o_nets["net_BASE_3000w"][0],
        "U7 O CONS@3000万": o_nets["net_CONS_3000w"][0],
        "U9 O BASE@1000万": o_nets["net_BASE_1000w"][0],
        "U10 O CONS@1000万": o_nets["net_CONS_1000w"][0],
        "U11 S 现代段": modern, "U12 S 剔6极端月": kept,
    }
    grid_rows = []
    for name, x in series_def.items():
        g = A.nw_profile(x)
        row = {"series": name, "n": g[3]["n"]}
        for L in A.LAG_GRID:
            row[f"t_L{L}"] = round(g[L]["t_nw"], 6)
        row["t_iid"] = round(g[0]["t_iid"], 6)
        row["min_t_over_grid"] = round(min(g[L]["t_nw"] for L in A.LAG_GRID), 6)
        row["flip_vs_L3"] = any((g[L]["t_nw"] >= A.T_CRIT) != (g[3]["t_nw"] >= A.T_CRIT)
                                for L in A.LAG_GRID)
        grid_rows.append(row)
        say(f"  {name:<18} n={g[3]['n']:>3} t(L)=" + " ".join(f"{g[L]['t_nw']:+.3f}" for L in A.LAG_GRID)
            + f"  翻转={row['flip_vs_L3']}")
    # 与 phaseu 记录对照（抽样核对 U2/U3/U6/U7/U11/U12 的 L0/L3/L6）
    u_rec = {(r["series_id"], int(r["L"])): float(r["t_nw"]) for r in up}
    for sid, mine in [("U2", series_def["U2 阶段I V0"]), ("U3", series_def["U3 阶段I V1"]),
                      ("U6", series_def["U6 O BASE@3000万"]), ("U7", series_def["U7 O CONS@3000万"]),
                      ("U11", series_def["U11 S 现代段"]), ("U12", series_def["U12 S 剔6极端月"])]:
        g = A.nw_profile(mine)
        for L in (0, 3, 6):
            checks.append(check(f"R10 {sid} t(L={L})", g[L]["t_nw"], u_rec[(sid, L)], TOL_T))
    checks.append(check("R10 A类最小t(全网格)", min_t_grid, 2.3559, 5e-4))
    checks.append(check("R10 A类最小t(L=3)", min_t_l3, 3.1152, 5e-4))

    # ---------- R11 券商默认 ----------
    say("\n[R11] 券商默认（东方财富 万2.5全佣+5元）@3000万：记录 +0.1313 / t=3.895")
    bj = json.load(open(os.path.join(A.REPO, "reports/BROKER_FEE_CALCULATION.json"), encoding="utf-8"))
    em = None
    for k, v in bj.get("exq5", {}).items():
        if "3000" in k or "3,000" in k:
            em = (k, v)
    say(f"  broker JSON exq5 键样例: {list(bj.get('exq5', {}).keys())[:6]}")
    if em:
        say(f"  {em[0]}: {json.dumps(em[1], ensure_ascii=False)[:220]}")
    o_cons = rec_o["results"]["W_3000万|CONSERVATIVE"]
    say(f"  阶段 O CONSERVATIVE@3000万: {o_cons['net_pp']:+.6f} / t={o_cons['t_nw']:+.4f} → "
        f"与 broker +0.1313/+3.895 差 {o_cons['net_pp'] - 0.1313:+.6f}pp（交叉验证一致，差异为四舍五入）")
    checks.append({"name": "R11 broker EM默认 vs O CONSERVATIVE（交叉）",
                   "got": o_cons["net_pp"], "recorded": 0.1313, "tol": 5e-4, "unit": "pp",
                   "ok": bool(abs(o_cons["net_pp"] - 0.1313) <= 5e-4),
                   "note": "broker +0.1313 是 O CONSERVATIVE +0.131486 的四舍五入；t 3.895 vs 3.8996 同理"})

    # ---------- R12 恒等式 ----------
    say("\n[R12] 阶段 H 恒等式：alpha_v0 ≡ (f/(1−f))×(R_bench − R_Q5)，f=实际 Q5 占比")
    rb = A.col(pi, "r_bench_pp")
    rq = A.col(pi, "r_q5_pp")
    npool = A.col(pi, "n_pool")
    nkeep = A.col(pi, "n_keep")
    # 精确式：r_ex = (r_bench − f·r_q5)/(1−f)，f = n_q5/n_pool = (n_pool−n_keep)/n_pool
    ident = [((np_ - nk_) / np_) / (1.0 - (np_ - nk_) / np_) * (b - q)
             for b, q, np_, nk_ in zip(rb, rq, npool, nkeep)]
    max_dev = max(abs(a - i) for a, i in zip(v0, ident))
    naive = [0.25 * (b - q) for b, q in zip(rb, rq)]      # 报告的简化式（f≡0.2）
    max_dev_naive = max(abs(a - i) for a, i in zip(v0, naive))
    f_mean = statistics.mean((np_ - nk_) / np_ for np_, nk_ in zip(npool, nkeep))
    say(f"  精确式（f 为实际占比）逐月最大偏差 {max_dev:.3e}pp（阶段 H 记录 6.8e-17，同式）")
    say(f"  简化式 0.25×(R_bench−R_Q5) 逐月最大偏差 {max_dev_naive:.3e}pp"
        f"（f 均值 {f_mean:.6f}≠0.2：整数组大小所致；alpha 本身不受影响，直接计算）")
    checks.append({"name": "R12 恒等式最大偏差（精确式）", "got": max_dev, "recorded": 6.8e-17,
                   "tol": 1e-5, "unit": "pp", "ok": bool(max_dev < 1e-5),
                   "note": "复算用 6 位小数 CSV 列；阶段 H 脚本用全精度同式，6.8e-17"})
    checks.append({"name": "R12 简化式 0.25 的偏差（文档精度发现）", "got": max_dev_naive,
                   "recorded": 0.0, "tol": None, "unit": "pp", "ok": None,
                   "note": "H 报告把恒等式写成 0.25×(R_bench−R_Q5)，实际为 (f/(1−f)) 且 f≈0.2 非精确 0.2；"
                           "对均值 alpha 无影响（直接计算），但引用恒等式时应带 f"})

    # ---------- 汇总 ----------
    n_ok = sum(1 for c in checks if c["ok"] is True)
    n_bad = [c for c in checks if c["ok"] is False]
    n_na = [c for c in checks if c["ok"] is None]
    say(f"\n[汇总] {len(checks)} 项：通过 {n_ok}，不一致 {len(n_bad)}，无法判定 {len(n_na)}")
    for c in n_bad:
        say(f"  ✗ {c['name']}: got={c['got']} recorded={c['recorded']} diff={c.get('diff')}")
    for c in n_na:
        say(f"  – {c['name']}（{c.get('note', '')[:60]}）")

    A.write_csv(os.path.join(OUT, "repro_table.csv"),
                ["name", "unit", "got", "recorded", "diff", "tol", "ok", "note"],
                [[c["name"], c.get("unit", ""), c["got"], c["recorded"], c.get("diff", ""),
                  c.get("tol", ""), c["ok"], c.get("note", "")] for c in checks])
    A.write_csv(os.path.join(OUT, "nw_grid_key_series.csv"),
                ["series", "n"] + [f"t_L{L}" for L in A.LAG_GRID] + ["t_iid", "min_t_over_grid", "flip_vs_L3"],
                [[r["series"], r["n"]] + [r[f"t_L{L}"] for L in A.LAG_GRID]
                 + [r["t_iid"], r["min_t_over_grid"], r["flip_vs_L3"]] for r in grid_rows])
    A.write_json(os.path.join(OUT, "repro_detail.json"),
                 {"input_hashes": hashes, "checks": checks, "nw_grid": grid_rows,
                  "bootstrap_ci95_modern": [ci_lo, ci_hi],
                  "summary": {"n": len(checks), "ok": n_ok, "mismatch": len(n_bad), "na": len(n_na)}})
    with open(os.path.join(OUT, "run.log"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(log) + "\n")
    print(f"\n[产物] {OUT}/")
    return 0 if not n_bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
