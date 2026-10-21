#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 P：分段相等性检验（"衰减 / 不衰减"能不能被检验出来）

预登记 docs/PHASE_P_SEGMENT_EQUALITY_PREREG_2026-10-21.md
冻结哈希 3d19f7508e109d17420f2d13cfb811b893e68c555ed6e6130ceadcce465e15d9

**本阶段只读各阶段已冻结的逐月产物，不重算任何选股/组合。**
它检验的是「已有序列的性质」，不是新实验。

主检验：exQ5 净 alpha（BASE@3000万）的两段之差是否为 0。
正对照：SURGE 可成交 alpha（阶段 L 报过 −0.1292pp 的衰减）——用来校准检验效力。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402

PREREG_SHA256 = ("3d19f7508e109d17420f2d13cfb811b893e68c555ed6e6130ceadcce465e15d9")
PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phasep_out", PREREG_SHA12)

# ── 冻结项（预登记 §4） ───────────────────────────────────────────
SPLIT_DATE = "2016-01-01"
SEGMENTS = (("2007-01-01", "2015-12-31", "2007-2015"),
            ("2016-01-01", "2026-10-31", "2016-2026"))
NW_LAG = 3
T_CRIT = 1.96
Z_POWER80 = 2.8016          # 双侧 5% + 功效 80% 的 MDE 系数

# 主序列与其期望 sha256（来自阶段 O 的 manifest）
MAIN_CSV = os.path.join("phaseo_out", "ac7aa2830ab0", "monthly.csv")
MAIN_COL = "net_BASE_3000w"
MAIN_CSV_SHA256 = "768e49a9462cd7c00f3aacfcdb1e87bbe37bb75b348cd6fd85809e12493a4152"

# 主序列所属阶段 O 的预登记哈希（溯源用）
PHASE_O_PREREG = "ac7aa2830ab0c80bb08a4443d348ce32040ba306eb97de2bb32ba6539eda435f"


def verify() -> None:
    p = "docs/PHASE_P_SEGMENT_EQUALITY_PREREG_2026-10-21.md"
    if not os.path.exists(p):
        raise SystemExit(f"预登记缺失：{p}")
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    if h != PREREG_SHA256:
        raise SystemExit(f"预登记哈希不符！\n  期望 {PREREG_SHA256}\n  实际 {h}")
    print(f"[P] 预登记校验通过 sha256={h[:12]}")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================================================ 核心统计量
def hac_dummy_regression(y: list[float], d: list[int], lag: int = NW_LAG,
                         finite_sample: bool = True) -> dict:
    """哑变量回归 y = β0 + β1·d + ε，返回 β1 及其 HAC(Bartlett) 标准误。

    经 Frisch–Waugh–Lovell：先对截距去均值，再单变量回归。
    残差 û 与全回归的 OLS 残差**逐位相同**（这是 FWL 定理的结论，
    单测 `test_fwl_residual_equals_full_ols_residual` 钉住）。

    Var(β1) = c · Ω̂_z / (Σ D̃²)²
      Ω̂_z = Σ z_t² + 2 Σ_{j=1..lag} (1 − j/(lag+1)) Σ_{t>j} z_t z_{t−j}
      z_t  = D̃_t · û_t
      c    = n/(n−2)（有限样本修正；finite_sample=False 时 c=1）

    ⚠️ 单位：se 与 y **同单位**。传入 pp 就返回 pp。
    （本项目已两次把 `newey_west` 的 `*_pp` 字段当同单位用而放大 100 倍。）
    """
    n = len(y)
    if n != len(d) or n == 0:
        raise ValueError("y 与 d 长度必须一致且非空")
    if not (0 <= lag <= n - 3):
        lag = max(0, min(lag, n - 3))

    ybar = statistics.fmean(y)
    dbar = statistics.fmean(d)
    dy = [v - ybar for v in y]
    dd = [v - dbar for v in d]

    sxx = sum(v * v for v in dd)
    if sxx <= 0:
        return {"n": n, "beta1": float("nan"), "se": float("nan"), "t": float("nan"),
                "p": float("nan"), "sxx": 0.0, "lag": lag, "degenerate": True}
    beta1 = sum(a * b for a, b in zip(dd, dy)) / sxx

    u = [a - beta1 * b for a, b in zip(dy, dd)]      # FWL 残差 = OLS 残差
    z = [a * b for a, b in zip(dd, u)]

    omega = sum(v * v for v in z)
    for j in range(1, lag + 1):
        gj = sum(z[t] * z[t - j] for t in range(j, n))
        omega += 2.0 * (1.0 - j / (lag + 1)) * gj
    if omega <= 0:                                    # 小样本可能为负
        omega = sum(v * v for v in z)
        fell_back = True
    else:
        fell_back = False

    var = omega / (sxx * sxx)
    if finite_sample and n > 2:
        var *= n / (n - 2)
    se = math.sqrt(var) if var > 0 else float("nan")
    t = beta1 / se if se == se and se > 0 else float("nan")
    return {"n": n, "beta1": beta1, "se": se, "t": t, "p": C._p2(t),
            "sxx": sxx, "lag": lag, "omega": omega, "degenerate": False,
            "variance_fell_back_to_g0": fell_back,
            "beta0": ybar - beta1 * dbar,
            "mde": (Z_POWER80 * se) if se == se else float("nan")}


def power_at_observed(abs_t: float, crit: float = T_CRIT) -> float:
    """若真实效应恰等于观测到的 |β1|/se，则双侧检验能检出它的概率。

    power = Φ(|t| − c) + Φ(−|t| − c)

    ⚠️ 这是**事后功效**（把估计值当真值），只用于判读「未检出」有多少信息量，
    **不是**新判据。典型值：|t|=1.96 → 50%（抛硬币）；|t|=0.22 → ≈5%（≈检验水平）。
    """
    if abs_t != abs_t:
        return float("nan")
    z = abs_t - crit
    return 0.5 * math.erfc(-z / math.sqrt(2.0)) + 0.5 * math.erfc(-(-abs_t - crit)
                                                                  / math.sqrt(2.0))


def segment_means(vals: list[float], dates: list[str]) -> dict:
    """两段的均值与 NW t（用于描述，不用于判定）。"""
    out = {}
    for lo, hi, lab in SEGMENTS:
        sub = [v for v, dt in zip(vals, dates) if lo <= dt <= hi and v == v]
        nw = C.newey_west(sub, NW_LAG)
        out[lab] = {"n": len(sub),
                    "mean_pp": (statistics.fmean(sub) if sub else float("nan")),
                    "t_nw": nw["t_nw"], "enough": len(sub) >= NW_LAG + 3}
    return out


# ============================================================ 判据
def judge(beta1: float, t: float, early_mean: float) -> dict:
    """三档判据（预登记 §1.4）。"""
    detected = (t == t) and abs(t) >= T_CRIT
    if not detected:
        verdict = "SEGMENT_DIFFERENCE_NOT_DETECTED"
    else:
        same_sign = (beta1 > 0) == (early_mean > 0)
        verdict = ("SEGMENT_DIFFERENCE_DETECTED_SAME_SIGN" if same_sign
                   else "SEGMENT_DIFFERENCE_DETECTED_OPPOSITE")
    return {"verdict": verdict, "detected": bool(detected)}


# ============================================================ 序列读取
def read_series(path: str, date_col: str, val_col: str) -> tuple[list[str], list[float]]:
    if not os.path.exists(path):
        raise SystemExit(f"产物缺失：{path}")
    dates, vals = [], []
    with open(path, encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        if date_col not in (rd.fieldnames or []) or val_col not in (rd.fieldnames or []):
            raise SystemExit(f"{path} 缺少列 {date_col} / {val_col}；实际 {rd.fieldnames}")
        for row in rd:
            dt = (row[date_col] or "")[:10]
            raw = row[val_col]
            try:
                v = float(raw)
            except (TypeError, ValueError):
                v = float("nan")
            dates.append(dt)
            vals.append(v)
    return dates, vals


def main() -> int:
    t_start = time.time()
    verify()
    os.makedirs(OUT_DIR, exist_ok=True)

    # ---- 主序列：先校验文件指纹（预登记 §1.1）
    print()
    print("=" * 78)
    print("① 主序列读取与指纹校验")
    print("=" * 78)
    got_sha = _sha256_file(MAIN_CSV)
    if got_sha != MAIN_CSV_SHA256:
        raise SystemExit(f"主序列文件指纹不符！\n  期望 {MAIN_CSV_SHA256}\n  实际 {got_sha}")
    print(f"   {MAIN_CSV}")
    print(f"   sha256 校验通过（{got_sha[:16]}…）")

    dates, main_vals = read_series(MAIN_CSV, "signal_date", MAIN_COL)
    good = [(d, v) for d, v in zip(dates, main_vals) if v == v and d >= SEGMENTS[0][0]]
    dates_main = [d for d, _ in good]
    vals_main = [v for _, v in good]
    print(f"   {MAIN_COL}: n={len(vals_main)}（有效月）")

    # 复现闸门：分段均值必须复现阶段 O 的 segments_BASE_3000w
    gate_ok = True
    expect_seg = {"2007-2015": (108, 0.143784, 3.3173), "2016-2026": (129, 0.129408, 2.5813)}
    segs_main = segment_means(vals_main, dates_main)
    print()
    print("② 复现闸门（对照阶段 O §segments_BASE_3000w）")
    for lab, (en, em, et) in expect_seg.items():
        s = segs_main[lab]
        ok_n = s["n"] == en
        ok_m = abs(s["mean_pp"] - em) <= 5e-5
        ok_t = abs(s["t_nw"] - et) <= 5e-3
        gate_ok &= (ok_n and ok_m and ok_t)
        print(f"   {lab}  n {s['n']} vs {en} {'✓' if ok_n else '✗'}   "
              f"mean {s['mean_pp']:+.6f} vs {em:+.6f} {'✓' if ok_m else '✗'}   "
              f"t {s['t_nw']:+.4f} vs {et:+.4f} {'✓' if ok_t else '✗'}")
    if not gate_ok:
        raise SystemExit("复现闸门失败 —— 主序列与阶段 O 不一致，拒绝继续。")
    print("   闸门通过 ✓")

    # 交叉校验：两段之差必须等于回归的 β1（两条独立路径）
    diff_direct = segs_main["2016-2026"]["mean_pp"] - segs_main["2007-2015"]["mean_pp"]

    d_main = [1 if dt >= SPLIT_DATE else 0 for dt in dates_main]
    reg_main = hac_dummy_regression(vals_main, d_main, NW_LAG)
    print()
    print("③ 主检验：exQ5 净 alpha（BASE @ 3,000 万）两段相等性")
    print("=" * 78)
    print(f"   n = {reg_main['n']}（早 {segs_main['2007-2015']['n']} / "
          f"晚 {segs_main['2016-2026']['n']}）  L={NW_LAG}")
    print(f"   α_early (β0) = {segs_main['2007-2015']['mean_pp']:+.6f} pp")
    print(f"   α_late       = {segs_main['2016-2026']['mean_pp']:+.6f} pp")
    print(f"   β1 (差)      = {reg_main['beta1']:+.6f} pp")
    print(f"   独立路径核对：两段均值直接相减 = {diff_direct:+.6f} pp  "
          f"（差 {abs(diff_direct - reg_main['beta1']):.2e}）")
    print(f"   se(NW)       = {reg_main['se']:+.6f} pp")
    print(f"   t            = {reg_main['t']:+.4f}   p = {reg_main['p']:.4f}")
    print(f"   95% CI       = [{reg_main['beta1'] - T_CRIT*reg_main['se']:+.6f}, "
          f"{reg_main['beta1'] + T_CRIT*reg_main['se']:+.6f}] pp")
    print(f"   ⭐ MDE（80% 功效）= {reg_main['mde']:.6f} pp")
    print(f"   |实测差| / MDE  = {abs(reg_main['beta1'])/reg_main['mde']:.4f}")
    pw_main = power_at_observed(abs(reg_main["t"]))
    print(f"   ⭐ 事后功效（若真值=观测值）= {pw_main*100:.1f}%  "
          f"（≈检验水平 5% 时，说明该效应量下基本检不出）")
    # 「若后段完全归零」的检出功效：β1 将等于 −α_early
    vanish_main = power_at_observed(abs(segs_main["2007-2015"]["mean_pp"]) / reg_main["se"])
    print(f"   ⭐ 对「后段完全归零」的检出功效 = {vanish_main*100:.1f}%"
          f"（β1 将为 {-(segs_main['2007-2015']['mean_pp']):+.4f}pp）")

    jd = judge(reg_main["beta1"], reg_main["t"], segs_main["2007-2015"]["mean_pp"])
    print(f"   >>> 判定 {jd['verdict']}")

    # ---- 正对照
    print()
    print("④ 正对照：SURGE 可成交 alpha（阶段 L 报过衰减 −0.1292pp）")
    print("=" * 78)
    ctrl_path = os.path.join("phasel_out", "9ffc3710cf6f", "monthly.csv")
    d_l, v_l = read_series(ctrl_path, "signal_date", "alpha_v1_pp")
    good_l = [(d, v) for d, v in zip(d_l, v_l) if v == v and d >= SEGMENTS[0][0]]
    dates_l = [d for d, _ in good_l]
    vals_l = [v for _, v in good_l]
    segs_l = segment_means(vals_l, dates_l)
    d_late = [1 if dt >= SPLIT_DATE else 0 for dt in dates_l]
    reg_l = hac_dummy_regression(vals_l, d_late, NW_LAG)
    print(f"   n = {reg_l['n']}  α_early {segs_l['2007-2015']['mean_pp']:+.6f} → "
          f"α_late {segs_l['2016-2026']['mean_pp']:+.6f}")
    print(f"   β1 = {reg_l['beta1']:+.6f} pp   se = {reg_l['se']:.6f}   "
          f"t = {reg_l['t']:+.4f}   p = {reg_l['p']:.4f}")
    print(f"   95% CI = [{reg_l['beta1'] - T_CRIT*reg_l['se']:+.6f}, "
          f"{reg_l['beta1'] + T_CRIT*reg_l['se']:+.6f}]   MDE = {reg_l['mde']:.6f} pp")
    pw_ctrl = power_at_observed(abs(reg_l["t"]))
    print(f"   ⭐ 事后功效（若真值=观测值）= {pw_ctrl*100:.1f}%")

    ctrl_detected = (reg_l["t"] == reg_l["t"]) and abs(reg_l["t"]) >= T_CRIT
    margin = abs(reg_l["t"]) - T_CRIT
    print(f"   正对照是否检出差异：{'是' if ctrl_detected else '否'}"
          f"（|t| − 1.96 = {margin:+.4f}）")
    if ctrl_detected and margin >= 0.10:
        power_note = (f"检验有足够效力（检出 −0.1292pp，余量 {margin:+.3f}）→ "
                      "主检验的「未检出」有信息量")
    elif ctrl_detected:
        power_note = (f"⚠️ 正对照**擦边通过**（|t| − 1.96 = {margin:+.4f}，"
                      f"事后功效仅 {pw_ctrl*100:.0f}%，等于抛硬币）→ "
                      "检验对 0.13pp 量级只有约五成把握，主检验的「未检出」"
                      "**仅有弱信息量**")
    else:
        power_note = (f"⚠️ 检验**效力不足**：连阶段 L 报告的 −0.1292pp（衰减 73%）都检不出"
                      " → 主检验的「未检出」**无信息量**")
    print(f"   >>> {power_note}")

    # ---- 副检验（描述性）
    print()
    print("⑤ 副检验（描述性，不参与判定）")
    print("=" * 78)
    # ⚠️ scale：各阶段产物的单位**不统一**。
    #    phasef 的 `delta` 是**小数**（均值 0.007405），其余列是 pp。
    #    混在一张表里会让整行读错 100 倍 —— 这正是变异测试里
    #    「百分数与小数混用」那一类故障。必须显式缩放并加门控。
    sub_defs = [
        ("S1", "exQ5 毛 alpha（V1，未扣费）",
         os.path.join("phasei_out", "8f5c35121fdb", "monthly.csv"), "alpha_v1_pp", 1.0),
        ("S2", "exQ5 净（STRESS @3000万）", MAIN_CSV, "net_STRESS_3000w", 1.0),
        ("S3", "MAX5 层内价差 Δ（阶段 F）",
         os.path.join("phasef_out", "aec46d88bbda", "monthly.csv"), "delta", 100.0),
        ("S4", "SURGE 可成交 alpha（=正对照）", ctrl_path, "alpha_v1_pp", 1.0),
    ]
    # 门控：缩放后的全样本均值必须复现各阶段报告值
    sub_gates = {"S3": ("phasef summary main.mean_pp", 0.7404925843, 5e-6)}
    subs = {}
    print(f"   {'#':<3} {'序列':<34} {'α_early':>9} {'α_late':>9} {'β1':>9} "
          f"{'t':>7} {'MDE':>8} {'|β1|/MDE':>9} {'功效':>7}")
    for sid, lab, path, col, scale in sub_defs:
        ds, vs = read_series(path, "signal_date", col)
        good_s = [(d, v * scale) for d, v in zip(ds, vs) if v == v and d >= SEGMENTS[0][0]]
        dates_s = [d for d, _ in good_s]
        vals_s = [v for _, v in good_s]
        if sid in sub_gates:
            gname, gexp, gtol = sub_gates[sid]
            got_m = statistics.fmean(vals_s)
            if abs(got_m - gexp) > gtol:
                raise SystemExit(f"副检验 {sid} 单位门控失败：缩放后全样本均值 "
                                 f"{got_m:.8f} 与 {gname}={gexp} 不符（容差 {gtol}）")
            print(f"   {sid} 单位门控 ✓（×{scale:g} 后全样本均值 {got_m:.6f} "
                  f"复现 {gname}）")
        segs_s = segment_means(vals_s, dates_s)
        dvec = [1 if dt >= SPLIT_DATE else 0 for dt in dates_s]
        r = hac_dummy_regression(vals_s, dvec, NW_LAG)
        subs[sid] = {"label": lab, "source": f"{path}::{col}",
                     "n": r["n"],
                     "alpha_early_pp": round(segs_s["2007-2015"]["mean_pp"], 6),
                     "alpha_late_pp": round(segs_s["2016-2026"]["mean_pp"], 6),
                     "beta1_pp": round(r["beta1"], 6),
                     "se_pp": round(r["se"], 6),
                     "t_nw": round(r["t"], 4), "p": round(r["p"], 4),
                     "ci95_pp": [round(r["beta1"] - T_CRIT * r["se"], 6),
                                 round(r["beta1"] + T_CRIT * r["se"], 6)],
                     "mde_pp": round(r["mde"], 6),
                     "abs_ratio_over_mde": round(abs(r["beta1"]) / r["mde"], 4),
                     "scale_applied": scale,
                     "power_at_observed": round(power_at_observed(abs(r["t"])), 4)}
        s = subs[sid]
        print(f"   {sid:<3} {lab:<34} {s['alpha_early_pp']:>+9.4f} "
              f"{s['alpha_late_pp']:>+9.4f} {s['beta1_pp']:>+9.4f} "
              f"{s['t_nw']:>+7.3f} {s['mde_pp']:>8.4f} {s['abs_ratio_over_mde']:>9.4f} "
              f"{s['power_at_observed']*100:>6.1f}%")

    # ---- 结论
    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)
    ratio_main = abs(reg_main["beta1"]) / reg_main["mde"]
    print(f"  主检验 {jd['verdict']}")
    print(f"  实测差 {reg_main['beta1']:+.6f}pp，MDE {reg_main['mde']:.6f}pp，"
          f"比值 {ratio_main:.4f}")
    print(f"  {power_note}")
    print()
    if jd["verdict"] == "SEGMENT_DIFFERENCE_NOT_DETECTED":
        print("  ⚠️ 按纪律第 8 条：以下表述均**不允许**：")
        print("     · “exQ5 不衰减” / “证明了跨期稳定” / “2016 后仍然有效”")
        print("     唯一允许的表述：")
        print(f"     “在 MDE={reg_main['mde']:.4f}pp 的检验能力下未能拒绝两段相等；"
              f"实测差 {reg_main['beta1']:+.4f}pp，")
        print(f"      无法区分‘真的相等’与‘存在小于 {reg_main['mde']:.4f}pp 的变化’。”")
    if not ctrl_detected:
        print()
        print("  ⚠️ 更强的结论（因正对照失败）：")
        print("     **本项目无法用 237 个月分辨任何信号是否衰减。**")
        print("     这同时适用于阶段 L「SURGE 衰减 73%」这类表述 ——")
        print("     它们是描述性观测，**不是**被统计检验支持的结论。")

    # ---- 产物
    monthly_path = os.path.join(OUT_DIR, "monthly.csv")
    with open(monthly_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["signal_date", "segment", "net_BASE_3000w_pp"])
        for dt, v in zip(dates_main, vals_main):
            w.writerow([dt, "late" if dt >= SPLIT_DATE else "early", f"{v:.6f}"])

    summary = {
        "experiment": "phasep-segment-equality-v1",
        "prereg_sha256": PREREG_SHA256,
        "kind": "confirmatory（主检验）+ exploratory（副检验）",
        "frozen_items": {"split_date": SPLIT_DATE, "nw_lag": NW_LAG,
                         "t_crit": T_CRIT, "finite_sample_c": "n/(n-2)",
                         "mde_coefficient": Z_POWER80},
        "main_series": {"path": MAIN_CSV, "column": MAIN_COL,
                        "sha256": got_sha, "phase_o_prereg": PHASE_O_PREREG,
                        "n": len(vals_main)},
        "reproduction_gate": {
            "target": "phaseo_out/ac7aa2830ab0/summary.json::segments_BASE_3000w",
            "passed": bool(gate_ok),
            "segments": {k: {"n": v["n"], "mean_pp": round(v["mean_pp"], 6),
                             "t_nw": round(v["t_nw"], 4)} for k, v in segs_main.items()},
            "expect": {k: {"n": e[0], "mean_pp": e[1], "t_nw": e[2]}
                       for k, e in expect_seg.items()},
            "cross_path_diff": {"two_mean_subtraction": round(diff_direct, 8),
                                "regression_beta1": round(reg_main["beta1"], 8),
                                "abs_gap": round(abs(diff_direct - reg_main["beta1"]), 12)},
        },
        "main_test": {
            "n": reg_main["n"], "lag": reg_main["lag"],
            "alpha_early_pp": round(segs_main["2007-2015"]["mean_pp"], 6),
            "alpha_late_pp": round(segs_main["2016-2026"]["mean_pp"], 6),
            "beta1_pp": round(reg_main["beta1"], 6),
            "se_nw_pp": round(reg_main["se"], 6),
            "t_nw": round(reg_main["t"], 4), "p": round(reg_main["p"], 6),
            "ci95_pp": [round(reg_main["beta1"] - T_CRIT * reg_main["se"], 6),
                        round(reg_main["beta1"] + T_CRIT * reg_main["se"], 6)],
            "mde_pp": round(reg_main["mde"], 6),
            "abs_ratio_over_mde": round(ratio_main, 4),
            "power_at_observed": round(pw_main, 4),
            "power_against_complete_vanishing": round(vanish_main, 4),
            "verdict": jd["verdict"], "detected": jd["detected"],
        },
        "positive_control": {
            "series": "phasel_out/9ffc3710cf6f/monthly.csv::alpha_v1_pp",
            "reported_decay_pp": -0.129203,
            "beta1_pp": round(reg_l["beta1"], 6), "se_nw_pp": round(reg_l["se"], 6),
            "t_nw": round(reg_l["t"], 4), "p": round(reg_l["p"], 6),
            "ci95_pp": [round(reg_l["beta1"] - T_CRIT * reg_l["se"], 6),
                        round(reg_l["beta1"] + T_CRIT * reg_l["se"], 6)],
            "mde_pp": round(reg_l["mde"], 6),
            "power_at_observed": round(pw_ctrl, 4),
            "margin_over_crit": round(margin, 4),
            "detected": bool(ctrl_detected),
            "power_note": power_note,
        },
        "secondary_exploratory": subs,
        "unit_and_definition_notes": [
            "副检验 S3（阶段F delta）产物单位为**小数**，本阶段 ×100 转为 pp；"
            "已用门控核对缩放后全样本均值 = 阶段F main.mean_pp 0.7404926",
            "副检验 S2 与阶段 J segments_at_best 差约 0.0002pp：阶段 J 用逐月 Amihud 冲击，"
            "阶段 O 用 3,000 万档的常量上界 0.000784pp，故略有差异（可解释）",
            "所有报告的 se / MDE / CI 与所在列**同单位**（pp）",
        ],
        "honest_limits": [
            "主检验的「未检出」不等于「证明相等」（纪律 8）",
            "副检验全部为描述性，不参与判定、不参与多重检验校正",
            "本阶段只读已冻结产物，不重算选股/组合",
            "所有序列均为样本内；前瞻样本外验证仍然缺失",
        ],
        "runtime_sec": round(time.time() - t_start, 1),
    }
    sp = os.path.join(OUT_DIR, "summary.json")
    with open(sp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    files = ["monthly.csv", "summary.json"]
    manifest = {"experiment": "phasep-segment-equality-v1",
                "prereg_sha256": PREREG_SHA256,
                "files": {n: {"sha256": _sha256_file(os.path.join(OUT_DIR, n)),
                              "bytes": os.path.getsize(os.path.join(OUT_DIR, n))}
                          for n in files},
                "runtime_sec": summary["runtime_sec"]}
    with open(os.path.join(OUT_DIR, "manifest.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)

    print()
    print(f"产物：{OUT_DIR}/  （{summary['runtime_sec']}s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
