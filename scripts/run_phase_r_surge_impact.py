#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 R：SURGE 的冲击成本 + 真实费率下的可执行性复核

预登记 docs/PHASE_R_SURGE_IMPACT_PREREG_2026-10-22.md
冻结哈希 7b5e7643e8ca2c48c077a206cf8e84d0b006e2d1f1d4a1ef7c54225247483e11

补上 SURGE 唯一未建模的摩擦：冲击成本。
信号/构造/可成交约束/窗口全部沿用阶段 L（不改动），只补这一项 + 换成已查证的费率。

复用：
  - `run_phase_l_surge.build_records()`（自带 codes / entry）
  - `run_phase_i_exq5.filter_v1 / capacity_of / LOT`
  - `run_phase_j_impact.load_dollar_volume / liquidity_at / impact_amihud /
     impact_sqrt / breakeven_eta`
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
from run_phase_c import NW_LAG, stream_bars  # noqa: E402
from run_phase_i_exq5 import LOT, SEGMENTS, capacity_of, filter_v1  # noqa: E402
from run_phase_j_impact import (  # noqa: E402
    breakeven_eta, impact_amihud, impact_sqrt, liquidity_at, load_dollar_volume,
)
from run_phase_l_surge import WIN, build_records as build_l  # noqa: E402

PREREG_SHA256 = "7b5e7643e8ca2c48c077a206cf8e84d0b006e2d1f1d4a1ef7c54225247483e11"
PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phaser_out", PREREG_SHA12)

T_CRIT = 1.96

# 规费（查证：多家来源一致）
STAMP_RATE = 0.0005       # 印花税，仅卖出
TRANSFER_RATE = 0.00001   # 过户费，沪深双边

# 费率情景（来源与可信度见 docs/BROKER_FEE_CALCULATION_2026-10-21.md）
SCEN = [
    {"id": "EM_DEFAULT", "name": "东方财富 官方默认（万2.5全佣+5元）",
     "comm": 0.00025, "min_comm": 5.0, "all_in": False, "primary": True},
    {"id": "ZY_DEFAULT", "name": "中原证券 默认（万3+5元）",
     "comm": 0.0003, "min_comm": 5.0, "all_in": False},
    {"id": "QMT_WAN1", "name": "量化协商 万1（含5元）",
     "comm": 0.0001, "min_comm": 5.0, "all_in": False},
    {"id": "QMT_WAN1_NO5", "name": "量化协商 万1 + 免五 ⚠️合规存疑",
     "comm": 0.0001, "min_comm": 0.0, "all_in": False},
    {"id": "STRESS_03", "name": "阶段 L 压力口径 0.3%（对照，阶段L原式）",
     "comm": 0.003, "min_comm": 0.0, "all_in": True},
]

CAPITALS = (1.0e7, 3.0e7, 5.0e7, 1.0e8)
PHASEL_MONTHLY = "phasel_out/9ffc3710cf6f/monthly.csv"


def verify() -> None:
    p = "docs/PHASE_R_SURGE_IMPACT_PREREG_2026-10-22.md"
    if not os.path.exists(p):
        raise SystemExit(f"预登记缺失：{p}")
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    if h != PREREG_SHA256:
        raise SystemExit(f"预登记哈希不符！\n  期望 {PREREG_SHA256}\n  实际 {h}")
    print(f"[R] 预登记校验通过 sha256={h[:12]}")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def commission_pp(to: float, w: float, k: int, comm: float,
                  min_comm: float, all_in: bool) -> float:
    """月度佣金（pp）。

    all_in=True  → 阶段 L 原式：2×to×rate×100（rate 已含一切，不再另加规费）
    all_in=False → 拆分口径：佣金(双边,含每笔最低) + 印花税(仅卖出) + 过户费(双边)
    """
    if k <= 0 or to <= 0:
        return 0.0
    if all_in:
        return 2.0 * to * comm * 100.0
    per_trade = w / k
    n_trades = 2.0 * to * k
    per_comm = max(per_trade * comm, min_comm) if min_comm else per_trade * comm
    return (n_trades * per_comm / w * 100.0
            + to * STAMP_RATE * 100.0
            + 2.0 * to * TRANSFER_RATE * 100.0)


def local_of(idx_sorted: np.ndarray, kidx: np.ndarray) -> np.ndarray:
    """把**全局列号**映射回流动性数组的**局部**下标。

    ⚠️ 第一版在这里踩坑：`codes` 的顺序不是列号顺序，直接对未排序数组做
    `searchsorted` 会给出**错误下标**（不报错，只是取错股票的 ILLIQ）。
    因此构造时必须 `argsort`，这里才能安全二分。
    单测 `test_local_of_handles_unsorted_code_order` 钉住。
    """
    if kidx.size == 0:
        return kidx
    if not np.all(np.diff(idx_sorted) > 0):
        raise ValueError("idx_sorted 必须严格递增（构造时先 argsort）")
    loc = np.searchsorted(idx_sorted, kidx)
    ok = loc < idx_sorted.size
    loc = loc[ok]
    hit = idx_sorted[loc] == kidx[ok]
    return loc[hit]


def net_stats(net: list[float]) -> dict:
    """⚠️ net 已是 pp → 用 `se_nw`（不是 `se_nw_pp`，那会放大 100 倍）。

    ⚠️ 必须**优雅**处理退化输入（与阶段 Q 的 `fm()` 同一条规矩）：
    非有限值标 `has_nonfinite=True` 并以有效子集计算；有效样本 < 2 返回 NaN 并标
    `insufficient=True`。**不许抛异常后无声无息**。
    """
    n = len(net)
    fin = [v for v in net if v == v and math.isfinite(v)]
    has_bad = len(fin) != n
    if len(fin) < 2:
        return {"n": n, "n_valid": len(fin), "has_nonfinite": has_bad,
                "insufficient": True, "net_pp": float("nan"),
                "t_iid": float("nan"), "t_nw": float("nan"),
                "p_nw": float("nan"),
                "ci95_pp": [float("nan"), float("nan")]}
    m = statistics.fmean(fin)
    se_iid = statistics.stdev(fin) / math.sqrt(len(fin))
    nw = C.newey_west(fin, NW_LAG)
    return {"n": n, "n_valid": len(fin), "has_nonfinite": has_bad,
            "insufficient": False, "net_pp": round(m, 6),
            "t_iid": round(m / se_iid, 4), "t_nw": round(nw["t_nw"], 4),
            "p_nw": round(nw["p"], 6),
            "ci95_pp": [round(m - T_CRIT * nw["se_nw"], 6),
                        round(m + T_CRIT * nw["se_nw"], 6)]}


def judge_impact(pass_a: list[bool], pass_b: list[bool]) -> str:
    if all(pass_a) and all(pass_b):
        return "SURGE_EXECUTABLE_UNDER_REAL_FEES"
    if all(pass_a) and not all(pass_b):
        return "SURGE_IMPACT_MODEL_DEPENDENT"
    if any(pass_a):
        return "SURGE_SCALE_DEPENDENT"
    return "SURGE_NOT_EXECUTABLE"


def main() -> int:
    t_start = time.time()
    verify()
    os.makedirs(OUT_DIR, exist_ok=True)

    print()
    print("=" * 78)
    print("① 重建 SURGE V1（与阶段 L 同一套 build_records + filter_v1）")
    print("=" * 78)
    recs, meta = build_l()
    print(f"   月份 {len(recs)}   跳过 {meta['skipped']}")
    print(f"   共线性复核 SURGE vs 波动率 {meta['corr_median']['vol']:+.4f}  "
          f"vs MAX5 {meta['corr_median']['max5']:+.4f}"
          f"（可行性 0.3513 / 0.4607）")

    need: dict[str, set[str]] = defaultdict(set)
    for r in recs:
        for c in r["keep_codes"]:
            need[c].add(r["entry"])
    bars, ex_al, span = stream_bars(need)

    for r in recs:
        pm = {c: i for i, c in enumerate(r["codes"])}
        f1 = filter_v1(r, bars, ex_al, span, pm)
        r["keep_v1"] = f1["keep_codes"]
        r["pos_map"] = pm
        r["alpha_v1"] = (float(np.mean([r["fwd"][pm[c]] for c in f1["keep_codes"]]))
                         - r["r_bench"]) if f1["keep_codes"] else float("nan")
        lots = []
        for c in r["keep_v1"]:
            rb = bars.get(c, {}).get(r["entry"])
            if rb is not None and rb["bar"][3] > 0:
                lots.append(rb["bar"][3] * LOT)
        r["lots"] = lots

    # ---- 复现闸门
    print()
    print("② 复现闸门（对照阶段 L monthly.csv::alpha_v1_pp）")
    ref = {}
    with open(PHASEL_MONTHLY, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row["alpha_v1_pp"]:
                ref[row["signal_date"]] = float(row["alpha_v1_pp"])
    diffs = [abs(r["alpha_v1"] * 100 - ref[r["signal_date"]])
             for r in recs
             if r["signal_date"] in ref and np.isfinite(r["alpha_v1"])]
    mx = max(diffs) if diffs else float("nan")
    ok_n = len(diffs) == 237
    print(f"   {len(diffs)} 月（需 237 {'✓' if ok_n else '✗'}）  最大差 {mx:.2e} "
          f"{'✓' if mx < 1e-6 else '✗'}")
    if not (ok_n and mx < 1e-6):
        raise SystemExit("复现闸门失败 → 后续净值不可信")

    # ---- 流动性面板
    print()
    print("③ 流动性面板（ILLIQ / ADV / 日波动）")
    from run_phase_c import load_close
    from stage3_sigma_d import month_end_positions
    close = load_close()
    cal = close.index
    dv = load_dollar_volume().reindex(index=close.index,
                                      columns=close.columns).to_numpy()
    ret = close.pct_change(fill_method=None).to_numpy(dtype="float32", copy=True)
    cols = list(close.columns)
    col_of = {c: i for i, c in enumerate(cols)}
    positions = month_end_positions(cal, SEGMENTS[0][0], "2026-10-31")
    pos_of = {str(cal[p].date()): p for p in positions}

    liq = {}
    for r in recs:
        p = pos_of.get(r["signal_date"])
        if p is None or p - WIN + 1 < 0:
            continue
        raw = np.array([col_of[c] for c in r["codes"] if c in col_of], dtype=int)
        if raw.size == 0:
            continue
        # ⚠️ `codes` 的顺序不是列号顺序 → 必须先排序，否则 searchsorted 给出错下标
        order = np.argsort(raw)
        idx_sorted = raw[order]
        il, ad, sd = liquidity_at(ret[p - WIN + 1: p + 1][:, idx_sorted],
                                  dv[p - WIN + 1: p + 1][:, idx_sorted])
        keep_raw = np.array([col_of[c] for c in r["keep_v1"] if c in col_of],
                            dtype=int)
        liq[r["signal_date"]] = {"illiq": il, "adv": ad, "sd": sd,
                                 "idx": idx_sorted,
                                 "keep_global": keep_raw}
    print(f"   已算 {len(liq)} 个信号点")

    # ---- 换手（月度单边）：与阶段 L 一致的定义
    prev_basket: list[str] | None = None
    tos = []
    for r in recs:
        cur = set(r["keep_v1"])
        if prev_basket is None:
            tos.append(1.0 if cur else 0.0)
        else:
            inter = len(cur & prev_basket)
            union_side = 0.5 * (len(cur - prev_basket) + len(prev_basket - cur))
            tos.append(union_side / max(len(cur), 1) if cur else 0.0)
        prev_basket = set(cur)
    to_mean = statistics.fmean(tos)
    print(f"   月度单边换手均值 {to_mean*100:.2f}%（阶段 L 报告 24.09%）")

    # ---- 净值
    print()
    print("④ 净值（net = f_inv × alpha − 佣金 − 冲击）")
    print("=" * 78)

    out_all = {}
    participation_rows = []
    for w in CAPITALS:
        print(f"\n   ── W = {w/1e4:,.0f} 万")
        print(f"      {'情景':<18} {'佣金':>7} {'冲击A':>7} {'净A':>9} {'tA':>7} "
              f"{'CI下界':>8} | {'冲击B':>7} {'净B':>9} {'tB':>7}")
        part_m, finv_s, k_s = [], [], []
        cap_res = {}
        for sc in SCEN:
            net_a, net_b, comm_s, imp_a, imp_b = [], [], [], [], []
            for i, r in enumerate(recs):
                sd_key = r["signal_date"]
                if sd_key not in liq or not np.isfinite(r["alpha_v1"]):
                    continue
                cs = capacity_of(r["lots"], w)
                k = cs["k"]
                if k <= 0:
                    continue
                # ⚠️ f_inv 必须是 **已投资资金比例** = 1 − uninvested。
                #    `covered` 是"能买到的标的比例"（99.9%），两者差约 5pp，
                #    用错会高估净值。阶段 J 用的是 1 − uninvested，这里必须一致。
                f_inv_t = 1.0 - cs["uninvested"]
                p = w / k
                L = liq[sd_key]
                loc = local_of(L["idx"], L["keep_global"])
                if loc.size == 0:
                    continue
                c = commission_pp(tos[i], w, k, sc["comm"], sc["min_comm"],
                                  sc["all_in"])
                ia = impact_amihud(L["illiq"], loc, p, loc.size)
                ib = impact_sqrt(L["sd"], L["adv"], loc, p, loc.size, 1.0)
                comm_s.append(c)
                imp_a.append(ia)
                imp_b.append(ib)
                net_a.append(f_inv_t * r["alpha_v1"] * 100 - c - ia)
                net_b.append(f_inv_t * r["alpha_v1"] * 100 - c - ib)
                if sc.get("primary"):
                    finv_s.append(f_inv_t)
                    k_s.append(k)
                    with np.errstate(invalid="ignore", divide="ignore"):
                        pr = np.where(L["adv"][loc] > 0, p / L["adv"][loc], np.nan)
                    part_m.append(float(np.nanmedian(pr)))
            if not net_a:
                continue
            sa, sb = net_stats(net_a), net_stats(net_b)
            sa["cost_commission_pp"] = round(statistics.fmean(comm_s), 6)
            sa["impact_A_pp"] = round(statistics.fmean(imp_a), 6)
            sb["impact_B_eta1_pp"] = round(statistics.fmean(imp_b), 6)
            sb["cost_commission_pp"] = sa["cost_commission_pp"]
            cap_res[sc["id"]] = {"A_amihud": sa, "B_sqrt_eta1": sb}
            pa = abs(sa["t_nw"]) >= T_CRIT
            pb = abs(sb["t_nw"]) >= T_CRIT
            print(f"      {sc['id']:<18} {sa['cost_commission_pp']:>7.4f} "
                  f"{sa['impact_A_pp']:>7.4f} {sa['net_pp']:>+9.4f} {sa['t_nw']:>+7.3f} "
                  f"{sa['ci95_pp'][0]:>+8.4f} | {sb['impact_B_eta1_pp']:>7.4f} "
                  f"{sb['net_pp']:>+9.4f} {sb['t_nw']:>+7.3f}"
                  f"{'  ← 主口径' if sc.get('primary') else ''}")
        # 参与率与打平 η*
        pa_sorted = sorted(part_m)
        q = lambda a, x: float(np.percentile(a, x)) if a else float("nan")
        participation_rows.append({
            "capital": w, "k_median": (statistics.median(k_s) if k_s else None),
            "f_inv_median": (round(statistics.median(finv_s), 6) if finv_s else None),
            "participation_median": q(pa_sorted, 50),
            "participation_p90": q(pa_sorted, 90),
            "participation_p99": q(pa_sorted, 99),
            "participation_max": (max(part_m) if part_m else None),
        })
        out_all[f"{w/1e4:.0f}万"] = cap_res

    # 打平 η*：单独重算（需要序列而非均值）
    be_rows = []
    for w in CAPITALS:
        sc = [s for s in SCEN if s.get("primary")][0]
        base_s, unit_s = [], []
        for i, r in enumerate(recs):
            if r["signal_date"] not in liq or not np.isfinite(r["alpha_v1"]):
                continue
            cs = capacity_of(r["lots"], w)
            k = cs["k"]
            if k <= 0:
                continue
            f_inv_t = 1.0 - cs["uninvested"]
            L = liq[r["signal_date"]]
            loc = local_of(L["idx"], L["keep_global"])
            if loc.size == 0:
                continue
            p = w / k
            c = commission_pp(tos[i], w, k, sc["comm"], sc["min_comm"], sc["all_in"])
            ia = impact_amihud(L["illiq"], loc, p, loc.size)
            ib = impact_sqrt(L["sd"], L["adv"], loc, p, loc.size, 1.0)
            base_s.append(f_inv_t * r["alpha_v1"] * 100 - c - ia)
            unit_s.append(ib)
        if base_s and unit_s:
            be_rows.append({"capital": w, "breakeven_eta": round(
                breakeven_eta(base_s, unit_s), 4)})

    # ---- 判据
    print()
    print("⑤ 判据（主口径 = EM_DEFAULT + 冲击 A）")
    print("=" * 78)
    pass_a, pass_b = [], []
    for w in CAPITALS:
        key = f"{w/1e4:.0f}万"
        a = out_all[key]["EM_DEFAULT"]["A_amihud"]["t_nw"]
        b = out_all[key]["EM_DEFAULT"]["B_sqrt_eta1"]["t_nw"]
        pass_a.append(abs(a) >= T_CRIT)
        pass_b.append(abs(b) >= T_CRIT)
        print(f"   {key:>6}  A t={a:+.3f} {'✓' if abs(a) >= T_CRIT else '✗'}   "
              f"B(η=1) t={b:+.3f} {'✓' if abs(b) >= T_CRIT else '✗'}")
    verdict = judge_impact(pass_a, pass_b)
    print(f"   >>> 判定 {verdict}")

    # ---- 分段（描述性）
    print()
    print("⑥ 分段（**描述性**，不参与判定；阶段 P 已确立：两段各自显著 ≠ 稳定）")
    segs = {}
    for lo, hi, lab in SEGMENTS:
        row = {}
        for w in CAPITALS:
            key = f"{w/1e4:.0f}万"
            sc = [s for s in SCEN if s.get("primary")][0]
            vals = []
            for i, r in enumerate(recs):
                if not (lo <= r["signal_date"] <= hi):
                    continue
                if r["signal_date"] not in liq or not np.isfinite(r["alpha_v1"]):
                    continue
                cs = capacity_of(r["lots"], w)
                k = cs["k"]
                if k <= 0:
                    continue
                f_inv_t = 1.0 - cs["uninvested"]
                L = liq[r["signal_date"]]
                loc = local_of(L["idx"], L["keep_global"])
                if loc.size == 0:
                    continue
                c = commission_pp(tos[i], w, k, sc["comm"], sc["min_comm"],
                                  sc["all_in"])
                ia = impact_amihud(L["illiq"], loc, w / k, loc.size)
                vals.append(f_inv_t * r["alpha_v1"] * 100 - c - ia)
            row[key] = net_stats(vals) if len(vals) >= 6 else {"n": len(vals)}
        segs[lab] = row
        for w in CAPITALS:
            v = row[f"{w/1e4:.0f}万"]
            if "t_nw" in v:
                print(f"   {lab}  W={w/1e4:>6,.0f}万  n={v['n']:>3}  "
                      f"净 {v['net_pp']:+.4f}pp  t={v['t_nw']:+.3f}")

    # ---- 产物
    mp = os.path.join(OUT_DIR, "monthly.csv")
    with open(mp, "w", encoding="utf-8", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["signal_date", "n_keep_v1", "to_one_side", "alpha_v1_pp"])
        for i, r in enumerate(recs):
            wtr.writerow([r["signal_date"], len(r["keep_v1"]), f"{tos[i]:.6f}",
                          f"{r['alpha_v1']*100:.6f}"])

    summary = {
        "experiment": "phaser-surge-impact-v1",
        "prereg_sha256": PREREG_SHA256,
        "kind": "confirmatory（补 SURGE 唯一未建模的摩擦：冲击成本）",
        "frozen_from": "阶段 L（信号/构造/V1约束/窗口）+ 阶段 J（冲击模型）+ 阶段I（容量）",
        "reproduction_gate": {"months": len(diffs), "expect": 237,
                              "max_abs_diff": mx, "passed": bool(ok_n and mx < 1e-6)},
        "turnover_one_side_mean": round(to_mean, 6),
        "fee_scenarios": [{k: v for k, v in s.items() if k != "primary"}
                          for s in SCEN],
        "results_by_capital": out_all,
        "participation": participation_rows,
        "breakeven_eta": be_rows,
        "criteria": {"pass_A_by_capital": pass_a, "pass_B_by_capital": pass_b,
                     "verdict": verdict},
        "segments_descriptive": segs,
        "honest_limits": [
            "冲击仍是**模型**，无盘中数据；两模型在 exQ5 上相差 25 倍",
            "显著 ≠ 可交易（纪律 7）；全部样本内",
            "分段只作描述性：阶段 P 已确立两段各自显著 ≠ 稳定",
            "⚠️ 预期方向部分建立在上一轮券商费率测算（情景分析、无预登记）之上",
            "最低佣金按『每只一个委托』计，实盘合并委托会更低 → 偏保守",
        ],
        "runtime_sec": round(time.time() - t_start, 1),
    }
    sp = os.path.join(OUT_DIR, "summary.json")
    with open(sp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    files = ["monthly.csv", "summary.json"]
    with open(os.path.join(OUT_DIR, "manifest.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump({"experiment": "phaser-surge-impact-v1",
                   "prereg_sha256": PREREG_SHA256,
                   "files": {n: {"sha256": _sha256_file(os.path.join(OUT_DIR, n)),
                                 "bytes": os.path.getsize(os.path.join(OUT_DIR, n))}
                             for n in files},
                   "runtime_sec": summary["runtime_sec"]},
                  f, ensure_ascii=False, indent=1)

    print()
    print(f"产物：{OUT_DIR}/  （{summary['runtime_sec']}s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
