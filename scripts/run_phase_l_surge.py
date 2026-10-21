#!/usr/bin/env python
"""阶段 L：排除异常放量最高的 20%（SURGE）。

预登记 `docs/PHASE_L_SURGE_PREREG_2026-10-21.md`，冻结 `9ffc3710cf6f…`。

## 与阶段 I（exQ5）的关系

结构完全同构（`alpha_avoid ≡ 0.25 × (R_bench − R_Q5)`），只是分组变量换成 SURGE。
**新增的是判据 D：`net @0.3% ≥ 0.30pp`。**
阶段 I/K 已证明 0.16pp 量级的单边 alpha 在成本后只剩 0.05pp，统计再显著也没用，
所以本轮把"量级够不够"写成判据，而不是事后讨论。

## SURGE 定义（写死）

    SURGE = mean(近 21 个交易日成交额) / mean(前 21 个交易日成交额)

⚠️ 两个窗口都要 ≥19 个有效交易日：可行性检查发现右尾极大（月最大中位 34 倍、
个别月 2990 倍），来源是停牌期成交量为 0。那是口径产物，不是投机性放量。
⚠️ **不缩尾**：只用 SURGE 的秩做等频分组，秩对单调变换不变 → 缩尾无影响、只多一个参数。

用法：
    python scripts/run_phase_l_surge.py
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
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_c import (  # noqa: E402
    END_HOLD, END_SIGNAL, MIN_LISTED_DAYS, NW_LAG, START, Panels,
    build_mask, load_close, stream_bars,
)
from run_phase_i_exq5 import (  # noqa: E402
    LOT, SEGMENTS, WIN, capacity_of, filter_v1, split_equal_indep,
)
from run_phase_j_impact import load_dollar_volume  # noqa: E402
from stage3_sigma_d import month_end_positions  # noqa: E402

# ==================== 预登记常量 ====================
PREREG_SHA256 = "9ffc3710cf6f2c43390d0ef0f91f663909eca44967c00214b4958b578b8c995c"
PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phasel_out", PREREG_SHA12)

N_GROUP = 5
Q_HIGH = N_GROUP - 1
MIN_VALID = 19
MIN_VALID_PRIOR = 19
TOP_N_MAX5 = 5                 # 仅用于共线性诊断
COST_TIERS = (0.001, 0.003)
PRIMARY_COST = 0.003
T_CRIT = 1.96
ECON_MIN_PP = 0.30             # ⭐ 判据 D
CAPITAL = 3.0e7                # 与阶段 J/K 同档，用于容量描述
ABSORB_BY_VOL_RATIO = 0.5      # 分层后掉到 50% 以下 → 打 ABSORBED_BY_VOL 标记


def verify() -> None:
    p = "docs/PHASE_L_SURGE_PREREG_2026-10-21.md"
    if not os.path.exists(p):
        raise SystemExit(f"预登记缺失：{p}")
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    if h != PREREG_SHA256:
        raise SystemExit(f"预登记哈希不符！\n  期望 {PREREG_SHA256}\n  实际 {h}")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def surge_of(cur: np.ndarray, pri: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """`(21, n)` 的近窗与前窗成交额 → (SURGE, 合格掩码)。

    ⚠️ **两个窗口都要 ≥19 个有效交易日**（`MIN_VALID` / `MIN_VALID_PRIOR`）。
    这不是可选的清洗，而是可行性检查逼出来的：右尾极大（月最大中位 34 倍、
    个别月 2990 倍），来源是**停牌期成交量为 0 → 复牌后比值爆炸**。
    那是口径产物，不是"投机性放量"，必须挡在门外。

    ⚠️ **不缩尾**：本构造只用 SURGE 的秩做等频分组，秩对任何单调变换不变
    → 缩尾对结果没有影响，只是白白多一个自由参数。
    """
    vc = np.isfinite(cur) & (cur > 0)
    vp = np.isfinite(pri) & (pri > 0)
    elig = (vc.sum(axis=0) >= MIN_VALID) & (vp.sum(axis=0) >= MIN_VALID_PRIOR)
    # ⚠️ 必须用 nansum：第一版写成 `np.where(vc, cur, np.nan).sum(axis=0)`，
    #    NaN 会传播 → **只要有一个无效日，整个 SURGE 就变成 NaN 被静默丢弃**，
    #    实际口径退化成"两个窗口都必须 21 天全有效"，与预登记的"≥19 有效日"不符。
    #    是单测 test_surge_uses_mean_of_valid_days_only 抓出来的。
    with np.errstate(invalid="ignore", divide="ignore"):
        mc = np.nansum(np.where(vc, cur, np.nan), axis=0) / np.maximum(vc.sum(axis=0), 1)
        mp = np.nansum(np.where(vp, pri, np.nan), axis=0) / np.maximum(vp.sum(axis=0), 1)
        surge = mc / mp
    return surge, elig


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    if a.size < 10:
        return float("nan")
    ra = np.argsort(np.argsort(a)).astype(float)
    rb = np.argsort(np.argsort(b)).astype(float)
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def judge(nw: dict, segs: dict, net_primary: dict) -> dict:
    """五态判据（预登记 §2.1）。四条并列必要条件，按顺序判定。"""
    cond_a = abs(nw["t_nw"]) >= T_CRIT
    sign = 1.0 if nw["mean_pp"] > 0 else -1.0
    cond_b = all(s.get("enough", True) and (s["mean_pp"] > 0) == (sign > 0)
                 and abs(s["t_nw"]) >= T_CRIT for s in segs.values())
    cond_c = abs(net_primary["t_net"]) >= T_CRIT
    cond_d = net_primary["net_pp_month"] >= ECON_MIN_PP

    if cond_a and cond_b and cond_c and cond_d:
        v = "SURGE_AVOID_CONFIRMED"
    elif cond_a and cond_b and cond_c:
        v = "SURGE_AVOID_SIGNIFICANT_BUT_SMALL"
    elif cond_a and cond_c:
        v = "SURGE_AVOID_UNSTABLE"
    elif cond_a:
        v = "SURGE_AVOID_ABSORBED"
    else:
        v = "SURGE_AVOID_NOT_DETECTED"
    return {"A_stat": bool(cond_a), "B_stable": bool(cond_b), "C_cost": bool(cond_c),
            "D_magnitude": bool(cond_d), "verdict": v}


def build_records() -> tuple[list[dict], dict]:
    close = load_close()
    cal = close.index
    panels = Panels(close)
    dv_all = load_dollar_volume().reindex(index=close.index, columns=close.columns)
    mask = build_mask(close, MIN_LISTED_DAYS)
    positions = month_end_positions(cal, START, END_HOLD)
    ret = close.pct_change(fill_method=None).to_numpy(dtype="float32", copy=True)
    arr_dv = dv_all.to_numpy()
    del dv_all
    cols = np.array(close.columns)
    ffill = panels.ffill

    recs, skipped = [], defaultdict(int)
    corr = defaultdict(list)
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sdate = str(cal[pos].date())
        if sdate > END_SIGNAL or pos - 2 * WIN + 1 < 0 or pos + 1 >= len(cal):
            skipped["window"] += 1
            continue
        ok = mask.iloc[pos].to_numpy()
        if ok.sum() < 100:
            skipped["pool"] += 1
            continue
        idx = np.where(ok)[0]

        cur = arr_dv[pos - WIN + 1: pos + 1][:, idx]
        pri = arr_dv[pos - 2 * WIN + 1: pos - WIN + 1][:, idx]
        surge, elig = surge_of(cur, pri)
        if elig.sum() < 100:
            skipped["elig"] += 1
            continue
        e = np.where(elig)[0]
        surge_e = surge[e]
        fin = np.isfinite(surge_e) & (surge_e > 0)
        if fin.sum() < 100:
            skipped["surge"] += 1
            continue
        idx = idx[e][fin]
        surge_e = surge_e[fin]

        grp = split_equal_indep(surge_e, N_GROUP)
        is_q5 = grp == Q_HIGH
        codes = cols[idx]

        p0 = ffill.iloc[pos][codes].to_numpy(dtype=float)
        p1 = ffill.iloc[nxt][codes].to_numpy(dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            fwd = p1 / p0 - 1.0
        fm = np.isfinite(fwd)
        if fm.sum() < 100:
            skipped["fwd"] += 1
            continue
        codes, fwd, grp, surge_e = codes[fm], fwd[fm], grp[fm], surge_e[fm]

        # 共线性诊断变量（不涉及未来收益）
        w = ret[pos - WIN + 1: pos + 1][:, idx][:, fm]
        vol = np.nanstd(np.where(np.isfinite(w), w, np.nan), axis=0, ddof=1)
        m5 = np.sort(np.where(np.isfinite(w), w, -np.inf), axis=0)[-TOP_N_MAX5:, :].mean(axis=0)
        msk = np.isfinite(vol) & np.isfinite(m5)
        if msk.sum() > 50:
            corr["vol"].append(spearman(surge_e[msk], vol[msk]))
            corr["max5"].append(spearman(surge_e[msk], m5[msk]))

        recs.append({
            "signal_date": sdate, "entry": str(cal[pos + 1].date()),
            "n_pool": int(len(fwd)), "n_keep": int((~is_q5).sum()),
            "codes": [str(c) for c in codes],
            "keep_codes": [str(c) for c in codes[~is_q5]],
            "q5_codes": [str(c) for c in codes[is_q5]],
            "surge": surge_e.tolist(), "grp": grp.tolist(),
            "vol": vol.tolist(), "fwd": fwd.tolist(),
            "r_bench": float(fwd.mean()),
            "r_q5": float(fwd[is_q5].mean()),
        })
    if not recs:
        raise SystemExit("没有有效月份")
    meta = {"skipped": dict(skipped),
            "surge_median_pp": statistics.median(
                [statistics.median(r["surge"]) for r in recs]),
            "corr_median": {k: (round(statistics.median(v), 6) if v else None)
                            for k, v in corr.items()}}
    return recs, meta


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print(f"阶段 L：排除异常放量最高的 20%（SURGE）   预登记 {PREREG_SHA12}")
    print("=" * 78)
    verify()
    print("  预登记哈希校验 ✓")

    recs, meta = build_records()
    print(f"  月份 {len(recs)}   跳过 {meta['skipped']}")
    print(f"  SURGE 横截面中位（逐月中位）{meta['surge_median_pp']:.4f}")
    print(f"  共线性复核 SURGE vs 波动率 {meta['corr_median']['vol']:+.4f}  "
          f"vs MAX5 {meta['corr_median']['max5']:+.4f}"
          f"（可行性检查 0.3513 / 0.4607）")

    # V0
    for r in recs:
        grp = np.array(r["grp"])
        r["alpha"] = float(np.mean([r["fwd"][i] for i in range(len(r["fwd"]))
                                    if grp[i] != Q_HIGH])) - r["r_bench"]
    dates = [r["signal_date"] for r in recs]
    alphas = [r["alpha"] for r in recs]
    nw0 = C.newey_west(alphas, NW_LAG)

    # ---- V1：可成交约束
    need: dict[str, set[str]] = defaultdict(set)
    for i, r in enumerate(recs):
        for c in r["keep_codes"]:
            need[c].add(r["entry"])
            if i + 1 < len(recs):
                need[c].add(recs[i + 1]["entry"])
    print(f"  需扫 EXEC {len(need)} 只 · {sum(len(v) for v in need.values())} 点 …")
    bars, ex_al, span = stream_bars(need)

    drop_agg = defaultdict(int)
    n_drop = 0
    for r in recs:
        pm = {c: i for i, c in enumerate(r["codes"])}
        f1 = filter_v1(r, bars, ex_al, span, pm)
        r["keep_v1"] = f1["keep_codes"]
        n_drop += f1["n_drop"]
        for k, v in f1["drop"].items():
            drop_agg[k] += v
        r["alpha_v1"] = (float(np.mean([r["fwd"][pm[c]] for c in f1["keep_codes"]]))
                         - r["r_bench"]) if f1["keep_codes"] else float("nan")
        lots = []
        for c in r["keep_v1"]:
            rb = bars.get(c, {}).get(r["entry"])
            if rb is not None and rb["bar"][3] > 0:
                lots.append(rb["bar"][3] * LOT)
        r["lots"] = lots

    a1 = [r["alpha_v1"] for r in recs if np.isfinite(r["alpha_v1"])]
    d1 = [r["signal_date"] for r in recs if np.isfinite(r["alpha_v1"])]
    nw1 = C.newey_west(a1, NW_LAG)
    print()
    print("① 主检验（V1 = 可成交口径）")
    print(f"   V0（无约束）alpha {nw0['mean_pp']:+.4f}pp  t={nw0['t_nw']:+.4f}")
    print(f"   V1 alpha {nw1['mean_pp']:+.4f}pp  NW t={nw1['t_nw']:+.4f}  n={len(a1)}")
    print(f"   与 V0 差 {nw1['mean_pp']-nw0['mean_pp']:+.4f}pp"
          f"（阶段 I 经验：可成交折损约 −29%）")
    print(f"   剔除 {n_drop} 只次  {dict(drop_agg)}")

    segs = {}
    for lo, hi, lab in SEGMENTS:
        sub = [x for x, d in zip(a1, d1) if lo <= d <= hi]
        nw = C.newey_west(sub, NW_LAG)
        segs[lab] = {"n": len(sub), "mean_pp": round(nw["mean_pp"], 6),
                     "t_nw": round(nw["t_nw"], 6), "enough": len(sub) >= NW_LAG + 3}
        print(f"   {lab}  n={len(sub):>3}  alpha={nw['mean_pp']:+.4f}pp  t={nw['t_nw']:+.3f}")

    # 换手与成本
    prev, tos = None, []
    for r in recs:
        cur = set(r["keep_v1"])
        if prev:
            tos.append(0.5 * (len(cur - prev) + len(prev - cur)) / max(len(cur), 1))
        prev = cur
    to = statistics.mean(tos)
    sd = statistics.stdev(a1)
    net = {f"{c*100:.1f}pct": C.cost_of(to, nw1["mean"], sd, len(a1), c) for c in COST_TIERS}
    print()
    print("② 换手与成本（走 cost_of）")
    print(f"   单边换手 {to*100:.2f}%/月")
    for k, v in net.items():
        print(f"   @{k}: 成本 {v['cost_pp_month']:.4f}pp → 净 {v['net_pp_month']:+.4f}pp  "
              f"t_net={v['t_net']:+.3f}")

    jd = judge(nw1, segs, net["0.3pct"])
    print()
    print("③ 判定（四条并列必要条件）")
    print(f"   A 统计  |t|≥1.96        : {nw1['t_nw']:+.4f}  → {'过' if jd['A_stat'] else '不过'}")
    print(f"   B 稳定  两段显著同号     : → {'过' if jd['B_stable'] else '不过'}")
    print(f"   C 成本  @0.3% |t|≥1.96  : {net['0.3pct']['t_net']:+.4f}  "
          f"→ {'过' if jd['C_cost'] else '不过'}")
    print(f"   D 量级  net@0.3% ≥0.30pp: {net['0.3pct']['net_pp_month']:+.4f}pp  "
          f"→ {'过' if jd['D_magnitude'] else '不过'}")
    print(f"   >>> {jd['verdict']}")

    # ---- 诊断：波动率分层复核
    print()
    print("④ 诊断：波动率分层复核（防'又是波动率的影子'）")
    lay = []
    for r in recs:
        grp = np.array(r["grp"])
        vol = np.array(r["vol"])
        keep = np.where(grp != Q_HIGH)[0]
        if len(keep) < 50:
            continue
        # 分层版本：先按波动率分 5 层（在 keep 集内），
        # 每层内再按 SURGE 重新等频 5 分组、排除层内最高 20%，
        # 按层大小加权 → 问"SURGE 的效应是否独立于波动率"
        vg = split_equal_indep(vol[keep], N_GROUP)
        surge_k = np.array(r["surge"])[keep]
        tot, wsum = 0.0, 0.0
        for L in range(N_GROUP):
            selk = keep[vg == L]
            if len(selk) < 20:
                continue
            g2 = split_equal_indep(surge_k[vg == L], N_GROUP)
            ex = selk[g2 != Q_HIGH]
            if len(ex) < 10:
                continue
            r_ex = float(np.mean([r["fwd"][i] for i in ex]))
            r_lay = float(np.mean([r["fwd"][i] for i in selk]))
            tot += (r_ex - r_lay) * len(selk)
            wsum += len(selk)
        if wsum:
            lay.append((r["signal_date"], tot / wsum))
    lay_a = [x[1] for x in lay]
    if len(lay_a) > 10:
        nwl = C.newey_west(lay_a, NW_LAG)
        ratio = (nwl["mean_pp"] / nw1["mean_pp"]) if nw1["mean_pp"] else float("nan")
        flag = abs(ratio) < ABSORB_BY_VOL_RATIO
        print(f"   波动率分层后 alpha {nwl['mean_pp']:+.4f}pp  t={nwl['t_nw']:+.3f}  "
              f"保留率 {ratio:+.3f}")
        print(f"   {'⚠️ 打上 ABSORBED_BY_VOL 标记' if flag else '未触发（保留率 ≥0.5）'}")
    else:
        nwl, ratio, flag = None, float("nan"), False
        print("   分层样本不足，跳过")

    # 被排除组的构成
    q5_all = [r["r_q5"] for r in recs]
    print()
    print("⑤ 诊断：被排除组（Q5 = 异常放量最高 20%）")
    print(f"   R_Q5 均值 {statistics.mean(q5_all)*100:+.4f}pp  "
          f"<0 月份占比 {sum(1 for x in q5_all if x < 0)/len(q5_all)*100:.1f}%")
    print("   ⚠️ 措辞：效应是「Q5 跑输池平均」，不是「Q5 下跌」（阶段 I 的教训）")
    for lo, hi, lab in SEGMENTS:
        sub = [r["r_q5"] for r in recs if lo <= r["signal_date"] <= hi]
        print(f"   {lab} R_Q5 {statistics.mean(sub)*100:+.4f}pp")
    by_year = defaultdict(list)
    for d, a in zip(d1, a1):
        by_year[d[:4]].append(a)
    print("   分年度：" + "  ".join(f"{y}:{statistics.mean(v)*100:+.2f}"
                                    for y, v in sorted(by_year.items())))

    # 容量（描述性）
    caps = [capacity_of(r["lots"], CAPITAL) for r in recs if r["lots"]]
    uninv = statistics.median([c["uninvested"] for c in caps])
    print()
    print(f"⑥ 容量（描述性，W={CAPITAL/1e4:.0f}万）：未投资中位 {uninv*100:.2f}%  "
          f"覆盖中位 {statistics.median([c['covered'] for c in caps])*100:.1f}%")

    # ---- 产物
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "monthly.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["signal_date", "n_pool", "n_keep", "r_bench_pp", "r_q5_pp",
                    "alpha_v0_pp", "alpha_v1_pp"])
        for r in recs:
            w.writerow([r["signal_date"], r["n_pool"], r["n_keep"],
                        round(r["r_bench"] * 100, 6), round(r["r_q5"] * 100, 6),
                        round(r["alpha"] * 100, 6),
                        round(r["alpha_v1"] * 100, 6) if np.isfinite(r["alpha_v1"]) else ""])

    summary = {
        "experiment": "phasel-surge-avoid-v1",
        "prereg_sha256": PREREG_SHA256,
        "definition": "SURGE = mean(近21日成交额) / mean(前21日成交额)；两窗口均需 ≥19 有效日",
        "V0_unconstrained": {"n": len(recs), "mean_pp": round(nw0["mean_pp"], 6),
                             "t_nw": round(nw0["t_nw"], 6)},
        "V1_executable": {"n": len(a1), "mean_pp": round(nw1["mean_pp"], 6),
                          "t_nw": round(nw1["t_nw"], 6),
                          "delta_vs_v0_pp": round(nw1["mean_pp"] - nw0["mean_pp"], 6),
                          "drop_total": n_drop, "drop_reasons": dict(drop_agg)},
        "segments": segs,
        "turnover_one_side": round(to, 6),
        "cost": {k: {kk: round(vv, 6) for kk, vv in v.items()} for k, v in net.items()},
        "criteria": jd,
        "verdict": jd["verdict"],
        "econ_min_pp": ECON_MIN_PP,
        "vol_stratified": ({"mean_pp": round(nwl["mean_pp"], 6),
                            "t_nw": round(nwl["t_nw"], 6),
                            "retention": round(ratio, 6),
                            "ABSORBED_BY_VOL": bool(flag)} if nwl else None),
        "collinearity_recheck": meta["corr_median"],
        "surge_median": round(meta["surge_median_pp"], 6),
        "diagnostics": {
            "r_q5_mean_pp": round(statistics.mean(q5_all) * 100, 6),
            "r_q5_negative_share": round(sum(1 for x in q5_all if x < 0) / len(q5_all), 6),
            "yearly": {y: round(statistics.mean(v) * 100, 6) for y, v in sorted(by_year.items())},
        },
        "capacity_desc": {"capital": CAPITAL,
                          "uninvested_median": round(uninv, 6),
                          "covered_median": round(
                              statistics.median([c["covered"] for c in caps]), 6)},
        "honest_limits": [
            "同一批数据上的检验，不提供样本外证据。",
            "冲击成本未建模（阶段 J 已证明在 3000 万量级可忽略，沿用其上界）。",
        ],
    }
    sp = os.path.join(OUT_DIR, "summary.json")
    with open(sp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")

    files = ["monthly.csv", "summary.json"]
    man = {"experiment": "phasel-surge-avoid-v1", "prereg_sha256": PREREG_SHA256,
           "script_sha256": _sha256_file(os.path.abspath(__file__)),
           "files": {n: {"sha256": _sha256_file(os.path.join(OUT_DIR, n)),
                         "bytes": os.path.getsize(os.path.join(OUT_DIR, n))} for n in files},
           "runtime_sec": round(time.time() - t0, 1)}
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(man, ensure_ascii=False, indent=1) + "\n")
    print()
    print(f"[产物] {OUT_DIR}/  脚本 {man['script_sha256'][:12]}  总耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
