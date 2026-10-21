#!/usr/bin/env python
"""阶段 G：MAX5 单边多头（可执行性检验）。

预登记：`docs/PHASE_G_MAX5_LONGONLY_PREREG_2026-10-21.md`
冻结 sha256：`e7da0f9ff3f1afae9af3785f3118e51732adb3cd0986c0ecf0679f32b2f64487`

## 问什么

阶段 F 证明控制波动率后 MAX5 的多空价差还有 +0.7405pp（t=3.41）。
但那个价差要做空 Q5，A 股个股做空不可得。本实验只买：

    alpha_t = R_long,t − R_bench,t

## 三个组合

- **V1（主）**：每层内最低 MAX5 的 20% 的并集，等权（与阶段 F 主检验同口径）
- **V0（对照）**：全池最低 MAX5 的 20%，等权（最自然可执行的定义）
- **V_L5（诊断）**：只在最高波动层内取最低 20%

## 事前概率（预登记 §0.1）

阶段 E 的 alpha 只有多空价差的 21%。若 MAX5 也如此：0.74×0.21 ≈ 0.16pp，
扣 0.17pp 成本（@0.1%）什么都不剩。**本实验很可能是否证。**

用法
----
    python scripts/run_phase_g_max5_longonly.py
"""

from __future__ import annotations

import glob
import hashlib
import json
import math
import os
import statistics
import sys
import time
from collections import defaultdict

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_c import (  # noqa: E402
    ADJ_DIR, END_HOLD, END_SIGNAL, MIN_LISTED_DAYS, NW_LAG, Panels, START,
    build_mask, cost_of, load_close, stream_bars,
)
from run_phase_f_max5 import N_GROUP, N_LAYER, TOP_N, WIN, MIN_VALID, split_equal  # noqa: E402
from stage3_sigma_d import month_end_positions  # noqa: E402

# ==================== 冻结参数 ====================

PREREG_PATH = "docs/PHASE_G_MAX5_LONGONLY_PREREG_2026-10-21.md"
PREREG_SHA256 = "e7da0f9ff3f1afae9af3785f3118e51732adb3cd0986c0ecf0679f32b2f64487"

PHASE_F_PREREG = "docs/PHASE_F_MAX5_PREREG_2026-10-21.md"
PHASE_F_PREREG_SHA256 = "aec46d88bbdaa207c14f183bd9275f1952f21b66a15fd762aea127aefd22071e"
PHASE_F_SUMMARY = "phasef_out/aec46d88bbda/summary.json"

ACCEPT_PATH = "data/exec_all/_acceptance.json"
MIN_QFQ_FILES = 5400

NW_LAG_G = NW_LAG
MIN_N_MAIN = 150
MIN_NW_EFF = 100
T_CRIT = 1.96
ECON_MIN_PP = 0.30
COST_TIERS = (0.001, 0.003)
LOT = 100

PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phaseg_out", PREREG_SHA12)


def _sha(p: str) -> str:
    return hashlib.sha256(open(p, "rb").read()).hexdigest()


def verify() -> tuple[dict, dict]:
    info = {}
    got = _sha(PREREG_PATH)
    if got != PREREG_SHA256:
        raise SystemExit(f"预登记 sha256 不符。\n  期望 {PREREG_SHA256}\n  实际 {got}")
    info["prereg_sha256"] = got
    print(f"[G] ① 本预登记校验 OK  {got[:16]}")

    f1 = _sha(PHASE_F_PREREG)
    if f1 != PHASE_F_PREREG_SHA256:
        raise SystemExit(f"阶段 F 预登记 sha256 不符：{f1}")
    if not os.path.exists(PHASE_F_SUMMARY):
        raise SystemExit(f"缺少阶段 F 产物 {PHASE_F_SUMMARY}")
    fsum = json.load(open(PHASE_F_SUMMARY, encoding="utf-8"))
    info["phase_f_prereg_sha256"] = f1
    info["phase_f_summary_sha256"] = _sha(PHASE_F_SUMMARY)
    print(f"[G] ② 阶段 F 预登记与产物 OK  {f1[:16]}")

    acc = json.load(open(ACCEPT_PATH, encoding="utf-8"))
    if acc.get("n_missing", 1) != 0 or acc.get("v2_bad"):
        raise SystemExit("exec_all 验收未通过")
    info["exec_acceptance"] = {"n_have": acc.get("n_have"), "n_target": acc.get("n_target")}
    print(f"[G] ②b exec_all 验收 OK  {acc.get('n_have')}/{acc.get('n_target')}")

    n = len(glob.glob(os.path.join(ADJ_DIR, "*.qfq.csv")))
    if n < MIN_QFQ_FILES:
        raise SystemExit(f"qfq_all 文件数 {n} < {MIN_QFQ_FILES}")
    info["n_qfq_files"] = n
    print(f"[G] ②c qfq_all 覆盖 OK  {n}")

    info["params_hardcoded"] = True
    print("[G] ③ 参数硬编码 OK")
    return info, fsum


def judge(nw: dict, spread_pp: float | None) -> dict:
    n, eff, t = nw["n"], nw["n_eff"], nw["t_nw"]
    a = nw["mean_pp"]
    aa = abs(a) if a == a else float("nan")
    if n < MIN_N_MAIN:
        return {"verdict": "INCONCLUSIVE", "rule_hits": [f"n={n} < {MIN_N_MAIN}"]}
    if eff < MIN_NW_EFF:
        return {"verdict": "INCONCLUSIVE", "rule_hits": [f"NW 有效样本 {eff} < {MIN_NW_EFF}"]}
    sig = abs(t) >= T_CRIT
    big = aa >= ECON_MIN_PP
    if sig and big:
        return {"verdict": "MAX5_LONGONLY_CONFIRMED",
                "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} >= {T_CRIT}",
                              f"|alpha|={aa:.4f}pp >= {ECON_MIN_PP}pp"],
                "direction": "多头跑赢基准" if a > 0 else "多头跑输基准"}
    if sig:
        return {"verdict": "MAX5_LONGONLY_SMALL_BUT_SIGNIFICANT",
                "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} >= {T_CRIT}",
                              f"|alpha|={aa:.4f}pp < {ECON_MIN_PP}pp"]}
    # 不显著：若同期多空价差显著，则判 NOT_EXECUTABLE（预登记 §3.1）
    if spread_pp is not None and spread_pp == spread_pp and abs(spread_pp) >= ECON_MIN_PP:
        return {"verdict": "MAX5_LONGONLY_NOT_EXECUTABLE",
                "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} < {T_CRIT}",
                              f"alpha={a:+.4f}pp 不显著，而阶段 F 多空价差 {spread_pp:+.4f}pp 显著",
                              "→ 多空价差是真的，但单边拿不到"],
                "note": "这是预登记 §3.1 预写的档位：效应在空头侧，非多头侧"}
    return {"verdict": "MAX5_LONGONLY_NOT_EXECUTABLE",
            "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} < {T_CRIT}"],
            "note": "单边不显著"}


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print("阶段 G：MAX5 单边多头（可执行性检验）")
    print(f"预登记 {PREREG_SHA256[:16]}  窗口 {START} ~ {END_SIGNAL}  NW 滞后 {NW_LAG_G}")
    print("=" * 78)
    vi, fsum = verify()
    spread_f = (fsum.get("main") or {}).get("mean_pp")
    print(f"    （阶段 F 多空价差 = {spread_f:+.4f}pp，作为 ratio 的分母）")
    print()

    close = load_close()
    cal = close.index
    panels = Panels(close)
    positions = month_end_positions(cal, START, END_HOLD)
    mask = build_mask(close, MIN_LISTED_DAYS)
    ret = close.pct_change(fill_method=None)
    cols = np.array(close.columns)
    arr_r = ret.to_numpy()
    ffill = panels.ffill

    recs, med_check = [], []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sdate = str(cal[pos].date())
        if sdate > END_SIGNAL or pos - WIN + 1 < 0 or pos + 1 >= len(cal):
            continue
        ok = mask.iloc[pos].to_numpy()
        if ok.sum() < 100:
            continue
        idx = np.where(ok)[0]
        sub = arr_r[pos - WIN + 1: pos + 1][:, idx]
        good = np.isfinite(sub).sum(axis=0) >= MIN_VALID
        if good.sum() < 100:
            continue
        w = sub[:, good]
        max5 = np.sort(np.where(np.isfinite(w), w, -np.inf), axis=0)[-TOP_N:, :].mean(axis=0)
        vol = np.nanstd(w, axis=0, ddof=1)
        codes = cols[idx][good]
        m = np.isfinite(max5) & np.isfinite(vol)
        if m.sum() < 100:
            continue
        max5, vol, codes = max5[m], vol[m], codes[m]

        p0 = ffill.iloc[pos][codes].to_numpy(dtype=float)
        p1 = ffill.iloc[nxt][codes].to_numpy(dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            fwd = p1 / p0 - 1.0
        fm = np.isfinite(fwd)
        if fm.sum() < 100:
            continue
        max5, vol, codes, fwd = max5[fm], vol[fm], codes[fm], fwd[fm]
        n_pool = len(fwd)
        med_check.append(float(np.median(max5)))

        r_bench = float(fwd.mean())

        # V1：分层后每层内 Q1 的并集
        gv = split_equal(vol, N_LAYER)
        v1_idx = []
        lay_of = {}
        for L in range(N_LAYER):
            sel = np.where(gv == L)[0]
            if len(sel) < N_GROUP * 5:
                continue
            gm = split_equal(max5[sel], N_GROUP)
            q1 = sel[gm == 0]
            v1_idx.append(q1)
            lay_of[L] = q1
        if not v1_idx:
            continue
        v1 = np.concatenate(v1_idx)

        # V0：全池 Q1
        gm_all = split_equal(max5, N_GROUP)
        v0 = np.where(gm_all == 0)[0]

        # V_L5：最高波动层内 Q1
        vl5 = lay_of.get(N_LAYER - 1, np.array([], dtype=int))

        def alpha_of(ii):
            return float(fwd[ii].mean()) - r_bench if len(ii) else float("nan")

        recs.append({
            "signal_date": sdate, "month": str(cal[nxt].date()), "n_pool": n_pool,
            "entry": str(cal[pos + 1].date()),
            "r_bench": r_bench,
            "r_v1": float(fwd[v1].mean()), "alpha_v1": alpha_of(v1),
            "r_v0": float(fwd[v0].mean()), "alpha_v0": alpha_of(v0),
            "r_l5": float(fwd[vl5].mean()) if len(vl5) else float("nan"),
            "alpha_l5": alpha_of(vl5),
            "n_v1": len(v1), "n_v0": len(v0), "n_l5": len(vl5),
            "v1_codes": list(codes[v1]), "v1_vol": float(np.median(vol[v1])),
            "pool_vol": float(np.median(vol)),
            "fwd": dict(zip(codes, fwd)),
        })

    if not recs:
        raise SystemExit("没有有效月份")

    # ---- 自检：MAX5 与阶段 F 一致
    med_got = statistics.mean(med_check) * 100
    med_exp = ((fsum.get("max5_selfcheck") or {}).get("got_pp"))
    rel = abs(med_got - med_exp) / abs(med_exp) if med_exp else None
    print("=== MAX5 实现自检（与阶段 F 比对）===")
    print(f"  实测 {med_got:.4f}pp  阶段 F {med_exp}pp  相对差 {rel*100:.4f}%")
    if rel is None or rel > 0.001:
        raise SystemExit("MAX5 实现与阶段 F 不一致（>0.1%），先查实现")
    print("  >>> 一致")

    # ---- 主检验
    print()
    print("=" * 78)
    print("主检验：alpha(V1 分层后 Q1 并集) = R_long − R_bench（双侧 NW 滞后 3）")
    print("=" * 78)
    a1 = [r["alpha_v1"] for r in recs]
    nw = C.newey_west(a1, NW_LAG_G)
    verdict = judge(nw, spread_f)
    print(f"  月份 {len(recs)}  NW 有效 {nw['n_eff']}")
    print(f"  mean(alpha) = {nw['mean_pp']:+.4f} pp/月   NW se = {nw['se_nw_pp']:.4f}")
    print(f"  NW t        = {nw['t_nw']:+.4f}   p = {nw['p']:.4g}")
    print(f"  >>> 主判定：{verdict['verdict']}")
    for h in verdict["rule_hits"]:
        print(f"      · {h}")

    ratio = nw["mean_pp"] / spread_f if spread_f else float("nan")
    print()
    print(f"★ alpha / 多空价差 = {nw['mean_pp']:+.4f} / {spread_f:+.4f} = {ratio:.4f}")
    print("   （阶段 E 的 21 日反转是 0.21；接近或低于它 ⇒ 效应同样在空头侧）")

    # ---- 对照 V0 / V_L5
    print()
    print("对照（不参与判定）：")
    for tag, key, lab in (("V0", "alpha_v0", "原始 Q1（全池最低 MAX5 20%）"),
                          ("V_L5", "alpha_l5", "仅最高波动层内 Q1")):
        v = [r[key] for r in recs if r[key] == r[key]]
        if not v:
            continue
        n2 = C.newey_west(v, NW_LAG_G)
        print(f"  {tag:<5} {lab:<32} alpha={n2['mean_pp']:+7.4f}pp  "
              f"NW t={n2['t_nw']:+6.3f}  n={n2['n']}")

    # ---- D3 分段
    print()
    print("D3 分段：")
    SEGS = (("2007-01-01", "2015-12-31", "2007-2015"), ("2016-01-01", "2026-09-30", "2016-2026"))
    seg = {}
    for lo, hi, lab in SEGS:
        sb = [r["alpha_v1"] for r in recs if lo <= r["signal_date"] <= hi]
        if len(sb) < 5:
            continue
        n3 = C.newey_west(sb, NW_LAG_G)
        seg[lab] = {"n": len(sb), "mean_pp": round(n3["mean_pp"], 6),
                    "t_nw": round(n3["t_nw"], 6)}
        print(f"    {lab}  n={len(sb):>3}  alpha={n3['mean_pp']:+8.4f}pp  NW t={n3['t_nw']:+.3f}")

    # ---- D4 分年度
    by = defaultdict(list)
    for r in recs:
        by[r["signal_date"][:4]].append(r["alpha_v1"])
    yearly = {y: {"n": len(v), "alpha_pp": round(statistics.mean(v) * 100, 6)}
              for y, v in sorted(by.items())}
    print()
    print("D4 分年度 alpha（pp/月）：")
    print("    " + "  ".join(f"{y}:{v['alpha_pp']:+.3f}" for y, v in yearly.items()))

    # ---- D5 beta
    y = [r["r_v1"] for r in recs]
    x = [r["r_bench"] for r in recs]
    my, mx = statistics.mean(y), statistics.mean(x)
    sxy = sum((a - my) * (b - mx) for a, b in zip(y, x)) / len(y)
    sxx = sum((b - mx) ** 2 for b in x) / len(y)
    syy = sum((a - my) ** 2 for a in y) / len(y)
    beta = sxy / sxx if sxx > 0 else float("nan")
    corr = sxy / math.sqrt(sxx * syy) if (sxx > 0 and syy > 0) else float("nan")
    print()
    print(f"D5 beta = {beta:+.4f}   相关 = {corr:+.4f}")

    # ---- D6 组合波动率 vs 池
    v1v = [r["v1_vol"] for r in recs]
    pv = [r["pool_vol"] for r in recs]
    print(f"D6 V1 组合波动率中位 {statistics.mean(v1v)*100:.4f}pp  vs 池 {statistics.mean(pv)*100:.4f}pp"
          f"  （比值 {statistics.mean(v1v)/statistics.mean(pv):.4f}）")
    if statistics.mean(v1v) / statistics.mean(pv) < 0.9:
        print("    ⚠ 组合波动率显著低于池 → alpha 可能只是低波动溢价，而 Gate 4 已证低波动不成立")

    # ---- 成本（V1 换手）
    tos, prev = [], None
    for r in recs:
        cur = r["v1_codes"]
        if prev:
            f = r["fwd"]
            wp = {c: 1.0 / len(prev) for c in prev}
            drift, tot = 0.0, 0.0
            drift = {}
            for c, w in wp.items():
                v = f.get(c, float("nan"))
                if v == v:
                    drift[c] = w * (1 + v)
                    tot += drift[c]
            if tot > 0:
                drift = {c: x / tot for c, x in drift.items()}
            wn = 1.0 / len(cur)
            tos.append(0.5 * sum(abs((wn if c in set(cur) else 0.0) - drift.get(c, 0.0))
                                 for c in set(drift) | set(cur)))
        prev = cur
    to = statistics.mean(tos) if tos else float("nan")
    sd = statistics.stdev(a1)
    print()
    print("=" * 78)
    print("成本（V1 等权组合实测换手；月度 = 2×换手×费率）")
    print("=" * 78)
    cost = {}
    for c in COST_TIERS:
        r_ = cost_of(to, nw["mean"], sd, len(a1), c)
        cost[f"{c*100:.1f}pct"] = {k: round(v, 4) for k, v in r_.items()}
        print(f"  单边 {c*100:.1f}%: 换手 {to*100:5.1f}%/月  成本 {r_['cost_pp_month']:.4f}pp → "
              f"净 {r_['net_pp_month']:+.4f}pp  t_net={r_['t_net']:+.3f}")

    # ---- 容量：需要 EXEC 收盘
    print()
    print("容量（需扫 EXEC，稍慢）…")
    need: dict[str, set[str]] = defaultdict(set)
    for r in recs:
        for c in r["v1_codes"]:
            need[c].add(r["entry"])
    bars, ex_al, span = stream_bars(need)
    w1, we, miss, tot_ = [], [], 0, 0
    for r in recs:
        lots = []
        for c in r["v1_codes"]:
            tot_ += 1
            rec = bars.get(c, {}).get(r["entry"])
            if rec is None or rec["bar"][3] <= 0:
                miss += 1
                continue
            lots.append(rec["bar"][3] * LOT)
        if lots:
            w1.append(sum(lots))
            we.append(len(lots) * max(lots))

    def q(v, p):
        if not v:
            return float("nan")
        s = sorted(v)
        return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]

    cap = {"w_1lot_median": q(w1, 0.5), "w_1lot_p90": q(w1, 0.9),
           "w_equal_median": q(we, 0.5), "w_equal_p90": q(we, 0.9),
           "lot_coverage": round(1 - miss / max(tot_, 1), 6)}
    print(f"  W_1lot    中位 {cap['w_1lot_median']/1e4:>9,.1f} 万   P90 {cap['w_1lot_p90']/1e4:>9,.1f} 万")
    print(f"  W_equal   中位 {cap['w_equal_median']/1e4:>9,.1f} 万   P90 {cap['w_equal_p90']/1e4:>9,.1f} 万")
    print(f"  价格覆盖 {cap['lot_coverage']*100:.2f}%")

    # ---- 写盘
    os.makedirs(OUT_DIR, exist_ok=True)

    def clean(s):
        return {k: (round(v, 10) if isinstance(v, float) else v) for k, v in s.items()}

    with open(os.path.join(OUT_DIR, "monthly.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("signal_date,month,n_pool,n_v1,r_v1,r_bench,alpha_v1,"
                "alpha_v0,alpha_l5,n_v0,n_l5,v1_vol,pool_vol\n")
        for r in recs:
            f.write(f"{r['signal_date']},{r['month']},{r['n_pool']},{r['n_v1']},"
                    f"{r['r_v1']:.10f},{r['r_bench']:.10f},{r['alpha_v1']:.10f},"
                    f"{r['alpha_v0']:.10f},{r['alpha_l5']:.10f},"
                    f"{r['n_v0']},{r['n_l5']},{r['v1_vol']:.8f},{r['pool_vol']:.8f}\n")

    av0 = [r["alpha_v0"] for r in recs if r["alpha_v0"] == r["alpha_v0"]]
    al5 = [r["alpha_l5"] for r in recs if r["alpha_l5"] == r["alpha_l5"]]
    nw_v0 = C.newey_west(av0, NW_LAG_G)
    nw_l5 = C.newey_west(al5, NW_LAG_G)

    summary = {
        "experiment": "phaseg-max5-longonly-v1.0",
        "prereg_sha256": PREREG_SHA256,
        "phase_f_prereg_sha256": PHASE_F_PREREG_SHA256,
        "phase_f_spread_pp": spread_f,
        "question": "只买低 MAX5 的股票，相对池内等权还剩多少 alpha？",
        "params": {"win": WIN, "top_n": TOP_N, "n_layer": N_LAYER, "n_group": N_GROUP,
                   "nw_lag": NW_LAG_G, "min_n": MIN_N_MAIN, "t_crit": T_CRIT,
                   "econ_min_pp": ECON_MIN_PP, "beta_neutralized": False, "lot": LOT},
        "window": {"start": START, "end_signal": END_SIGNAL, "end_hold": END_HOLD},
        "verify": vi,
        "max5_selfcheck": {"got_pp": round(med_got, 6), "phase_f_pp": med_exp,
                           "rel_diff": round(rel, 8) if rel is not None else None},
        "main": {"portfolio": "V1 = 分层后各层内最低 MAX5 20% 的并集，等权",
                 "n": len(recs), **clean(nw), "judgement": verdict},
        "ratio_alpha_to_spread": round(ratio, 6) if ratio == ratio else None,
        "controls": {
            "V0_raw_Q1": {**clean(nw_v0), "note": "全池最低 MAX5 20%，最自然可执行"},
            "V_L5_high_vol_Q1": {**clean(nw_l5), "note": "仅最高波动层内 Q1"},
        },
        "turnover_cost": {"one_side_per_month": round(to, 6), "cost": cost},
        "capacity": cap,
        "diagnostics": {
            "D3_segment": seg, "D4_yearly": yearly,
            "D5_beta": round(beta, 6), "D5_corr": round(corr, 6),
            "D6_v1_vol_pp": round(statistics.mean(v1v) * 100, 6),
            "D6_pool_vol_pp": round(statistics.mean(pv) * 100, 6),
            "D6_ratio": round(statistics.mean(v1v) / statistics.mean(pv), 6),
        },
        "limitations": [
            "冲击成本未建模；T+1 未建模；不做 beta 中性化（预登记 §7）",
            "L5 层流动性最差，其高 alpha 很可能被冲击成本吃掉（D2 只作诊断）",
            "容量按一手成本算，整手约束下等权需 W≥k×max(lot)",
            "ST 无历史标记；after_last_data 中退市与抓取截断不可区分；2005-2006 左截断",
        ],
        "note": "alpha 不是可实现收益；若判定 NOT_EXECUTABLE，MAX5 这条路与 21 日反转同命。",
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    files = ["monthly.csv", "summary.json"]
    manifest = {
        "experiment": "phaseg-max5-longonly-v1.0",
        "prereg_sha256": PREREG_SHA256,
        "script_sha256": _sha(os.path.abspath(__file__)),
        "files": {n: {"sha256": _sha(os.path.join(OUT_DIR, n)),
                      "bytes": os.path.getsize(os.path.join(OUT_DIR, n))} for n in files},
        "runtime_sec": round(time.time() - t0, 1),
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")

    print()
    print("=" * 78)
    print(f"产物 -> {OUT_DIR}/")
    print(f"主判定：{verdict['verdict']}   ratio={ratio:.4f}")
    print(f"总耗时 {time.time()-t0:.0f}s")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
