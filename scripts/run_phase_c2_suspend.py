#!/usr/bin/env python
"""阶段 C-2：不可成交缺口（建仓/平仓日停牌与退市）的敏感性。

预登记：`docs/PHASE_C2_SUSPEND_PREREG_2026-10-21.md`
冻结 sha256：`953c32fdc5565d3fa471553f7706815de981706af33cb83039f1c64bb2cf99d3`

## 问题

阶段 C 的 §5 只授权两类剔除（建仓日涨停剔多头、平仓日跌停剔空头）。
**建仓/平仓日停牌与退市没有授权剔除**，于是 5,322 个检查点（占 1.8%）被按"能成交"处理。
方向是偏乐观的。本实验量化这个乐观有多大。

## 两个格

- **V0** = 阶段 C 的 D 格，**直接复用 `run_phase_c.apply_constraints`**，必须逐位复现
  （d=+0.6582 / spread=1.3164 / t=+4.111）。复现不过就拒绝继续，不许解释成"数据变了"。
- **V1** = V0 再剔 `no_bar`（真停牌）与 `after_last_data`（序列终止）。
  方向不匹配的涨跌停**继续保留**——跌停能买入、涨停能卖出。

## 主检验

`Δ_t = spread(V1) − spread(V0)`，Newey-West 滞后 3，四档判据（见预登记 §3.1）。

## 为什么不做"顺延成交"

顺延要改收益起算点（`fwd[nxt]/fwd[e']`），与 Gate 6 / 阶段 C 不可比，
且"顺延几天算放弃"没有预登记依据。这个取舍写在预登记里，不是看到结果才决定的。

用法
----
    python scripts/run_phase_c2_suspend.py
"""

from __future__ import annotations

import hashlib
import glob
import json
import math
import os
import statistics
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_c import (  # noqa: E402
    ADJ_DIR, END_HOLD, END_SIGNAL, K_FIXED, K_FRAC, MIN_LISTED_DAYS, MIN_POOL, NW_LAG,
    Panels, START, _reason_of, apply_constraints, build_mask, collect_needed, cost_of,
    flags_fast, load_close, pair_diff, stream_bars, turnover_of,
)
from stage3_sigma_d import month_end_positions  # noqa: E402

# ==================== 冻结参数 ====================

PREREG_PATH = "docs/PHASE_C2_SUSPEND_PREREG_2026-10-21.md"
PREREG_SHA256 = "953c32fdc5565d3fa471553f7706815de981706af33cb83039f1c64bb2cf99d3"

PHASE_C_PREREG = "docs/PHASE_C_UNIVERSE_PREREG_2026-10-21.md"
PHASE_C_PREREG_SHA256 = "3a01759a37f03c57ec19a6090f52bbda11ed5a284271edfc4328504de64d1ab3"
PHASE_C_SUMMARY = "phasec_out/3a01759a37f0/summary.json"

ACCEPT_PATH = "data/exec_all/_acceptance.json"
MIN_QFQ_FILES = 5400

NW_LAG_C2 = NW_LAG
MIN_N_MAIN = 150
MIN_NW_EFF = 100
T_CRIT = 1.96
ECON_MIN_PP = 0.30
ALPHA = 0.05
COST_TIERS = (0.001, 0.003)

# 阶段 C 的 D 格必须逐位复现的期望值（预登记 §2）。**单位统一为 pp**，
# 从 phasec_out/3a01759a37f0/summary.json 的 cells.D.constrained 原样抄来。
# ⚠️ 第一版这里把 mean_d 写成小数 0.006582，而比较时 got 已 ×100 转 pp → 必然失败。
#    单位混用是这类校验最常见的坑，所以期望值直接从产物里抄、不手算。
EXPECT_V0 = {"n": 237, "mean_d": 0.65818965, "mean_spread": 1.31637930,
             "t_stat": 4.1110485798}
REPRO_TOL = {"n": 0, "mean_d": 1e-6, "mean_spread": 1e-6, "t_stat": 1e-6}

PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phasec2_out", PREREG_SHA12)

DROP_REASONS = ("no_bar", "after_last_data")


def _sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def verify() -> dict:
    """预登记 §8.2 五项校验。缺一即拒绝运行。"""
    info: dict = {}
    if not os.path.exists(PREREG_PATH):
        raise SystemExit(f"预登记不存在：{PREREG_PATH}")
    got = _sha(PREREG_PATH)
    if got != PREREG_SHA256:
        raise SystemExit(f"预登记 sha256 不符，拒绝运行。\n  期望 {PREREG_SHA256}\n  实际 {got}")
    info["prereg_sha256"] = got
    print(f"[C2] ① 本预登记校验 OK  {got[:16]}")

    c1 = _sha(PHASE_C_PREREG)
    if c1 != PHASE_C_PREREG_SHA256:
        raise SystemExit(f"阶段 C 预登记 sha256 不符：{c1}")
    if not os.path.exists(PHASE_C_SUMMARY):
        raise SystemExit(f"缺少阶段 C 产物 {PHASE_C_SUMMARY}")
    info["phase_c_prereg_sha256"] = c1
    info["phase_c_summary_sha256"] = _sha(PHASE_C_SUMMARY)
    print(f"[C2] ② 阶段 C 预登记与产物 OK  {c1[:16]}")

    acc = json.load(open(ACCEPT_PATH, encoding="utf-8"))
    if acc.get("n_missing", 1) != 0 or acc.get("v2_bad"):
        raise SystemExit(f"exec_all 验收未通过：{acc.get('n_missing')} / {acc.get('v2_bad')}")
    info["exec_acceptance"] = {"n_have": acc.get("n_have"), "n_target": acc.get("n_target"),
                               "n_missing": acc.get("n_missing"), "v2_bad": acc.get("v2_bad")}
    print(f"[C2] ③ exec_all 验收 OK  {acc.get('n_have')}/{acc.get('n_target')}")

    n = len(glob.glob(os.path.join(ADJ_DIR, "*.qfq.csv")))
    if n < MIN_QFQ_FILES:
        raise SystemExit(f"qfq_all 文件数 {n} < {MIN_QFQ_FILES}")
    info["n_qfq_files"] = n
    print(f"[C2] ④ qfq_all 覆盖 OK  {n} ≥ {MIN_QFQ_FILES}")

    info["params_hardcoded"] = True
    print("[C2] ⑤ 参数硬编码 OK（无 argparse，不可覆盖）")
    return info


def apply_v1(v0_rows: list[dict], bars: dict, ex_aligned: dict, span: dict) -> list[dict]:
    """在 V0 的持仓上**再剔**停牌与序列终止。只增加剔两类，不减少任何已有剔除。"""
    out = []
    for r in v0_rows:
        dl, ds = defaultdict(int), defaultdict(int)
        keep_l, keep_s = [], []
        for c in r["longs_c"]:
            f = flags_fast(bars.get(c, {}).get(r["entry"]), c, r["entry"],
                           ex_aligned.get(c, set()))
            reason = _reason_of(f, c, r["entry"], span)
            if reason in DROP_REASONS:
                dl[reason] += 1
            else:
                keep_l.append(c)
        for c in r["shorts_c"]:
            f = flags_fast(bars.get(c, {}).get(r["exit"]), c, r["exit"],
                           ex_aligned.get(c, set()))
            reason = _reason_of(f, c, r["exit"], span)
            if reason in DROP_REASONS:
                ds[reason] += 1
            else:
                keep_s.append(c)
        rec = dict(r)
        rec["n_drop2_long"] = sum(dl.values())
        rec["n_drop2_short"] = sum(ds.values())
        rec["drop2_long_reasons"] = dict(dl)
        rec["drop2_short_reasons"] = dict(ds)
        f = r["fwd"]
        if keep_l and keep_s:
            rl, rs = float(f.loc[keep_l].mean()), float(f.loc[keep_s].mean())
            rec.update({"longs_v1": keep_l, "shorts_v1": keep_s,
                        "r_long_v1": rl, "r_short_v1": rs, "d_v1": 0.5 * (rl - rs)})
        else:
            rec.update({"longs_v1": keep_l, "shorts_v1": keep_s,
                        "r_long_v1": float("nan"), "r_short_v1": float("nan"),
                        "d_v1": float("nan")})
        out.append(rec)
    return out


def judge(nw: dict) -> dict:
    """预登记 §3.1 四档。"""
    n, eff, t = nw["n"], nw["n_eff"], nw["t_nw"]
    mean_pp = abs(nw["mean_pp"]) if nw["mean_pp"] == nw["mean_pp"] else float("nan")
    if n < MIN_N_MAIN:
        return {"verdict": "INCONCLUSIVE", "rule_hits": [f"n={n} < {MIN_N_MAIN}"]}
    if eff < MIN_NW_EFF:
        return {"verdict": "INCONCLUSIVE", "rule_hits": [f"NW 有效样本 {eff} < {MIN_NW_EFF}"]}
    sig = abs(t) >= T_CRIT
    big = mean_pp >= ECON_MIN_PP
    if sig and big:
        return {"verdict": "SUSPENSION_EFFECT_MATERIAL",
                "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} >= {T_CRIT}",
                              f"|mean|={mean_pp:.4f}pp >= {ECON_MIN_PP}pp"],
                "direction": "剔除不可成交后效应变小" if nw["mean"] < 0 else "剔除后效应变大"}
    if not sig:
        return {"verdict": "SUSPENSION_EFFECT_NEGLIGIBLE",
                "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} < {T_CRIT}",
                              f"|mean|={mean_pp:.4f}pp"]}
    return {"verdict": "SUSPENSION_EFFECT_SMALL_BUT_SIGNIFICANT",
            "rule_hits": [f"n={n} >= {MIN_N_MAIN}", f"|NW t|={abs(t):.4f} >= {T_CRIT}",
                          f"|mean|={mean_pp:.4f}pp < {ECON_MIN_PP}pp"]}


def monthly_mean(xs: list[float]) -> float:
    return statistics.mean(xs) if xs else float("nan")


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print("阶段 C-2：不可成交缺口敏感性（停牌 / 序列终止）")
    print(f"预登记 {PREREG_SHA256[:16]}  窗口 {START} ~ {END_SIGNAL}  NW 滞后 {NW_LAG_C2}")
    print("=" * 78)
    vi = verify()
    print()

    close = load_close()
    cal = close.index
    panels = Panels(close)
    positions = month_end_positions(cal, START, END_HOLD)
    print(f"[C2] 调仓点 {len(positions)} 个")

    mask = build_mask(close, MIN_LISTED_DAYS)
    rows_d = panels.month_rows(mask, positions, "frac")     # 阶段 C D 格
    rows_b = panels.month_rows(mask, positions, "fixed")    # 阶段 C B 格
    print(f"[C2] 选股完成：D 格 {len(rows_d)} 月 / B 格 {len(rows_b)} 月")

    need = collect_needed({"D": rows_d, "B": rows_b})
    print(f"[C2] 需检查 {len(need)} 只 · {sum(len(v) for v in need.values())} 点")
    bars, ex_aligned, span = stream_bars(need)

    # ---- V0（复用阶段 C 的实现，保证同源）
    v0_d = apply_constraints(rows_d, bars, ex_aligned, span)
    v0_b = apply_constraints(rows_b, bars, ex_aligned, span)
    v1_d = apply_v1(v0_d, bars, ex_aligned, span)
    v1_b = apply_v1(v0_b, bars, ex_aligned, span)

    # ---- V0 复现校验（预登记 §9.2：不许把复现失败解释成"数据变了"）
    st0 = C.stats_of([r["d"] for r in v0_d])
    print()
    print("=== V0 复现校验（必须逐位等于阶段 C 的 D 格）===")
    repro = {}
    for k, exp in EXPECT_V0.items():
        got = st0[k] * (100 if k in ("mean_d", "mean_spread") else 1)
        tol = REPRO_TOL[k]
        ok = abs(got - exp) <= tol if tol else got == exp
        repro[k] = {"expected": exp, "actual": round(got, 8), "tol": tol, "ok": ok}
        print(f"  {k:<12} 实测 {got:+.6f}  期望 {exp:+.6f}  容差 {tol}  {'OK' if ok else 'FAIL'}")
    if not all(v["ok"] for v in repro.values()):
        raise SystemExit("V0 未能复现阶段 C 的 D 格，拒绝继续（先查实现，不许解释成数据变化）")
    print("  >>> V0 逐位复现通过，两个格同源可比")

    # ---- 主统计
    st1 = C.stats_of([r["d_v1"] for r in v1_d])
    sb0 = C.stats_of([r["d"] for r in v0_b])
    sb1 = C.stats_of([r["d_v1"] for r in v1_b])

    print()
    print("=" * 78)
    print("两格统计（含交易约束）")
    print("=" * 78)
    print(f"{'格':<8}{'n':>4}{'mean(d)':>11}{'spread':>10}{'t':>8}{'换手/月':>9}")
    for tag, s, rows, lk, sk in (("V0 (D格)", st0, v0_d, "longs_c", "shorts_c"),
                                 ("V1 (+剔停牌)", st1, v1_d, "longs_v1", "shorts_v1")):
        to = turnover_of(rows, lk, sk)
        print(f"{tag:<8}{s['n']:>4}{s['mean_d']*100:>+11.4f}{s['mean_spread']*100:>10.4f}"
              f"{s['t_stat']:>+8.3f}{to*100:>8.1f}%")

    # ---- 主检验
    print()
    print("=" * 78)
    print("主检验：Δ_t = spread(V1) − spread(V0)")
    print("=" * 78)
    common, delta = pair_diff(v0_d, [{**r, "d": r["d_v1"]} for r in v1_d], "d")
    nw = C.newey_west(delta, NW_LAG_C2)
    verdict = judge(nw)
    print(f"  配对月份 {len(common)}  NW 有效样本 {nw['n_eff']}")
    print(f"  mean(Δ) = {nw['mean_pp']:+.4f} pp/月    NW se = {nw['se_nw_pp']:.4f}")
    print(f"  NW t    = {nw['t_nw']:+.4f}   p = {nw['p']:.4f}")
    print(f"  >>> 主判定：{verdict['verdict']}")
    for h in verdict["rule_hits"]:
        print(f"      · {h}")
    if verdict.get("direction"):
        print(f"      · 方向：{verdict['direction']}")

    # ---- 交叉验证（B 格，固定 110）
    print()
    print("交叉验证（必报，不参与判定）：B 格（固定 k=110）做同一处理")
    common_b, delta_b = pair_diff(v0_b, [{**r, "d": r["d_v1"]} for r in v1_b], "d")
    nwb = C.newey_west(delta_b, NW_LAG_C2)
    print(f"  配对月份 {len(common_b)}  mean(Δ_B) = {nwb['mean_pp']:+.4f} pp  "
          f"NW t = {nwb['t_nw']:+.4f}  p = {nwb['p']:.4f}")
    same = (nw["mean"] * nwb["mean"] > 0) if (nw["mean"] == nw["mean"] and
                                              nwb["mean"] == nwb["mean"]) else None
    if same is True:
        cross = "两构造一致（同号）"
    elif same is False:
        cross = "两构造异号 → 主结论降级 EVIDENCE_CONFLICT"
        verdict = {"verdict": "EVIDENCE_CONFLICT",
                   "rule_hits": [f"主 {nw['mean_pp']:+.4f}pp vs 交叉 {nwb['mean_pp']:+.4f}pp 异号"],
                   "original": verdict["verdict"]}
    else:
        cross = "无法判断"
    print(f"  >>> {cross}")

    # ---- 机制诊断 D1~D5
    print()
    print("=" * 78)
    print("机制诊断（描述性，不参与判定）")
    print("=" * 78)
    by_year_l, by_year_s = defaultdict(int), defaultdict(int)
    mon_ratio, mon_delta = [], []
    dl_rets, kl_rets, ds_rets, ks_rets = [], [], [], []
    for r0, r in zip(v0_d, v1_d):
        y = r0["signal_date"][:4]
        by_year_l[y] += r["n_drop2_long"]
        by_year_s[y] += r["n_drop2_short"]
        f = r["fwd"]
        dL = [c for c in r["longs_c"] if c not in set(r["longs_v1"])]
        dS = [c for c in r["shorts_c"] if c not in set(r["shorts_v1"])]
        if dL:
            dl_rets.append(float(f.loc[dL].mean()))
        if r["longs_v1"]:
            kl_rets.append(float(f.loc[r["longs_v1"]].mean()))
        if dS:
            ds_rets.append(float(f.loc[dS].mean()))
        if r["shorts_v1"]:
            ks_rets.append(float(f.loc[r["shorts_v1"]].mean()))
        tot = len(r["longs_c"]) + len(r["shorts_c"])
        if tot:
            mon_ratio.append((len(dL) + len(dS)) / tot)
            mon_delta.append(2 * (r["d_v1"] - r0["d"]))

    print("D1 被剔只次按年（多 / 空）：")
    yrs = sorted(set(by_year_l) | set(by_year_s))
    line = []
    for y in yrs:
        if by_year_l[y] + by_year_s[y]:
            line.append(f"{y}:{by_year_l[y]}/{by_year_s[y]}")
    print("    " + "  ".join(line) if line else "    （无）")

    print(f"D2 被剔多头 fwd 均值 {monthly_mean(dl_rets)*100:+.4f}pp（{len(dl_rets)} 月）"
          f"  vs 保留多头 {monthly_mean(kl_rets)*100:+.4f}pp（{len(kl_rets)} 月）"
          f"  差 {((monthly_mean(dl_rets)-monthly_mean(kl_rets))*100):+.4f}pp")
    print(f"D3 被剔空头 fwd 均值 {monthly_mean(ds_rets)*100:+.4f}pp（{len(ds_rets)} 月）"
          f"  vs 保留空头 {monthly_mean(ks_rets)*100:+.4f}pp（{len(ks_rets)} 月）"
          f"  差 {((monthly_mean(ds_rets)-monthly_mean(ks_rets))*100):+.4f}pp")
    print("    （D2 若被剔多头收益更高 → 买不到它们确实让多头腿少赚 → 剔除削弱效应）")

    corr = float("nan")
    if len(mon_ratio) >= 10 and statistics.stdev(mon_ratio) > 0 and statistics.stdev(mon_delta) > 0:
        mr, md = statistics.mean(mon_ratio), statistics.mean(mon_delta)
        cov = sum((a - mr) * (b - md) for a, b in zip(mon_ratio, mon_delta)) / len(mon_ratio)
        corr = cov / (statistics.stdev(mon_ratio) * statistics.stdev(mon_delta))
    print(f"D4 每月被剔比例 vs 当月 Δ 的相关：r = {corr:+.4f}"
          f"（均值被剔比例 {monthly_mean(mon_ratio)*100:.2f}%）")

    nl0 = statistics.median(len(r["longs_c"]) for r in v0_d)
    nl1 = statistics.median(len(r["longs_v1"]) for r in v1_d)
    ns0 = statistics.median(len(r["shorts_c"]) for r in v0_d)
    ns1 = statistics.median(len(r["shorts_v1"]) for r in v1_d)
    print(f"D5 腿规模中位：多头 {nl0:.0f} → {nl1:.0f}，空头 {ns0:.0f} → {ns1:.0f}"
          f"（缩小 {(1 - nl1/nl0)*100:.2f}% / {(1 - ns1/ns0)*100:.2f}%）")

    # ---- 成本
    print()
    print("=" * 78)
    print("成本（月度成本 = 2 × 单边换手 × 费率；×12 是年化，不入月度扣减）")
    print("=" * 78)
    cost = {}
    for tag, s, rows, lk, sk in (("V0", st0, v0_d, "longs_c", "shorts_c"),
                                 ("V1", st1, v1_d, "longs_v1", "shorts_v1")):
        to = turnover_of(rows, lk, sk)
        tiers = {}
        for c in COST_TIERS:
            r_ = cost_of(to, s["mean_d"], s["sd_d"], s["n"], c)
            tiers[f"{c*100:.1f}pct"] = {k: round(v, 4) for k, v in r_.items()}
        cost[tag] = {"one_side_per_month": round(to, 6), "cost": tiers}
        print(f"  {tag} 单边 {to*100:5.1f}%/月  净@0.1% {tiers['0.1pct']['net_pp_month']:+.4f}pp"
              f"(t={tiers['0.1pct']['t_net']:+.2f})  净@0.3% {tiers['0.3pct']['net_pp_month']:+.4f}pp"
              f"(t={tiers['0.3pct']['t_net']:+.2f})")

    # ---- 写盘
    os.makedirs(OUT_DIR, exist_ok=True)

    def clean(s):
        return {k: (round(v, 10) if isinstance(v, float) else v) for k, v in s.items()}

    with open(os.path.join(OUT_DIR, "cells.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("cell,signal_date,month,n_pool,k_used,n_long,n_short,"
                "n_drop_v0_long,n_drop_v0_short,n_drop_v1_long,n_drop_v1_short,d,d_v1,spread,spread_v1\n")
        for r in v1_d:
            f.write(f"D,{r['signal_date']},{r['month']},{r['n_pool']},{r['k_used']},"
                    f"{len(r['longs_c'])},{len(r['shorts_c'])},"
                    f"{r['n_drop_long']},{r['n_drop_short']},"
                    f"{r['n_drop2_long']},{r['n_drop2_short']},"
                    f"{r['d']:.10f},{r['d_v1']:.10f},{2*r['d']:.10f},{2*r['d_v1']:.10f}\n")

    with open(os.path.join(OUT_DIR, "dropped_stats.csv"), "w", encoding="utf-8", newline="\n") as f:
        f.write("signal_date,leg,n_dropped,mean_fwd_pp,drop_reasons\n")
        for r in v1_d:
            fa = r["fwd"]
            for leg, kept_key, dk in (("long", "longs_v1", "drop2_long_reasons"),
                                      ("short", "shorts_v1", "drop2_short_reasons")):
                base = r["longs_c"] if leg == "long" else r["shorts_c"]
                kept = set(r[kept_key])
                dc = [c for c in base if c not in kept]
                if dc:
                    f.write(f"{r['signal_date']},{leg},{len(dc)},"
                            f"{float(fa.loc[dc].mean())*100:.6f},"
                            f"\"{json.dumps(r[dk], ensure_ascii=False)}\"\n")

    summary = {
        "experiment": "phasec2-suspension-gap-v1.0",
        "prereg_sha256": PREREG_SHA256,
        "phase_c_prereg_sha256": PHASE_C_PREREG_SHA256,
        "question": "建仓/平仓日停牌与序列终止若按不可成交剔除，效应变化多少？",
        "window": {"start": START, "end_signal": END_SIGNAL, "end_hold": END_HOLD},
        "params": {"k_frac": K_FRAC, "k_fixed_cross": K_FIXED, "min_listed_days": MIN_LISTED_DAYS,
                   "min_pool": MIN_POOL, "nw_lag": NW_LAG_C2, "min_n": MIN_N_MAIN,
                   "min_nw_eff": MIN_NW_EFF, "t_crit": T_CRIT, "econ_min_pp": ECON_MIN_PP},
        "verify": vi,
        "v0_reproduction": repro,
        "cells": {"V0_D": clean(st0), "V1_D": clean(st1),
                  "V0_B": clean(sb0), "V1_B": clean(sb1)},
        "turnover_cost": cost,
        "main_test": {"comparison": "spread(V1) − spread(V0)", "n_pairs": len(common),
                      **clean(nw), "judgement": verdict},
        "cross_validation_B": {"n_pairs": len(common_b), **clean(nwb), "reading": cross},
        "diagnostics": {
            "D1_dropped_by_year": {"long": dict(sorted(by_year_l.items())),
                                   "short": dict(sorted(by_year_s.items()))},
            "D2_dropped_long_fwd_pp": round(monthly_mean(dl_rets) * 100, 6),
            "D2_kept_long_fwd_pp": round(monthly_mean(kl_rets) * 100, 6),
            "D3_dropped_short_fwd_pp": round(monthly_mean(ds_rets) * 100, 6),
            "D3_kept_short_fwd_pp": round(monthly_mean(ks_rets) * 100, 6),
            "D4_corr_ratio_vs_delta": round(corr, 6) if corr == corr else None,
            "D4_mean_dropped_ratio": round(monthly_mean(mon_ratio), 6),
            "D5_leg_median": {"long": [nl0, nl1], "short": [ns0, ns1]},
        },
        "limitations": [
            "只做剔除，不做顺延成交；顺延会改收益起算点、与 Gate 6 不可比（预登记 §0.1 已声明）",
            "剔除不补位，腿规模略缩",
            "after_last_data 里'退市'与'抓取截断'不可区分（本数据集已知 249 只退市名单，另有 3 只 UNKNOWN）",
            "ST 无历史标记，5% 涨跌幅未区分",
            "做空腿不可得（M4 结构阻塞），d 不是可实现组合收益",
        ],
        "note": "本实验只影响 D 格的水平值；spread(B)−spread(A) 的配对差由两格共同承受，部分抵消。",
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")

    files = ["cells.csv", "dropped_stats.csv", "summary.json"]
    manifest = {
        "experiment": "phasec2-suspension-gap-v1.0",
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
    print(f"主判定：{verdict['verdict']}   交叉：{cross}")
    print(f"总耗时 {time.time()-t0:.0f}s")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
