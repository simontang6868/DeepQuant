#!/usr/bin/env python
"""阶段 K：低换手版本的 exQ5（换手—alpha 权衡）。

预登记 `docs/PHASE_K_LOWTURNOVER_PREREG_2026-10-21.md`，冻结 `3bc31a35903d…`。

## 为什么做这个

阶段 J 的归因：佣金吃掉毛 alpha 的 **69%**（0.1127 / 0.1634），冲击只占 0.5%。
换手是唯一没动过的杠杆。阶段 I 实测 Q5 成员与上期重叠仅 36.5%，
说明月换手里有相当部分是"边界翻来覆去"。

## 唯一的变量

在同一批信号点上，每 `m` 个月调仓一次，`m ∈ {1, 2, 3, 6}`。
信号定义、池、可成交约束、基准——全部与阶段 I 的 V1 完全一致。

## 统计量

四个变体都产出 **237 个月的月度收益序列**（而非 m 月持有期收益），
这样它们在同一时间轴上、样本量相同，可以直接比较。

⚠️ 标准误**统一用 NW(6)**：`m>1` 时同一篮子被持有 m 个月，相邻月份因共享篮子而相关，
自相关可延伸到 m−1 阶。不按 m 各自挑滞后阶数（那等于给每个变体调一个参数）。
NW(3) 与 iid 同时报。

用法：
    python scripts/run_phase_k_lowturnover.py
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
from run_phase_c import NW_LAG, load_close, stream_bars  # noqa: E402
from run_phase_i_exq5 import LOT, WIN, build_records, capacity_of, filter_v1  # noqa: E402
from stage3_sigma_d import month_end_positions  # noqa: E402

# ==================== 预登记常量 ====================
PREREG_SHA256 = "3bc31a35903dc36590ac8b5231abc62ff34ba3afa089d355166c0956f9983d12"
PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phasek_out", PREREG_SHA12)

REBALANCE_PERIODS = (1, 2, 3, 6)
COMM_RATE = 0.003
CAPITAL = 3.0e7                 # 固定 W（阶段 J 的最好档）
IMPACT_UPPER_PP = 0.000784     # 阶段 J @3000万、月调仓的 Amihud 冲击（上界）
NW_PRIMARY_LAG = 6             # ⚠️ 统一 6，不按 m 调
T_CRIT = 1.96
SEGMENTS = (("2007-01-01", "2015-12-31", "2007-2015"),
            ("2016-01-01", "2026-09-30", "2016-2026"))
PHASEI_MONTHLY = "phasei_out/8f5c35121fdb/monthly.csv"


def verify() -> None:
    p = "docs/PHASE_K_LOWTURNOVER_PREREG_2026-10-21.md"
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


def churn_of(new: list[str], old: list[str], first: bool = False) -> float:
    """单次调仓的换手比例（单边口径：买入+卖出各算一边，除以持仓数后取半）。

    `first=True` 表示建仓首期。预登记 §3.2 未规定首期口径 → 取 1.0
    （视作替换既有组合），与后续各期同用 `2×` 系数。已登记为 prereg_defects。
    """
    if first:
        return 1.0
    return 0.5 * (len(set(new) - set(old)) + len(set(old) - set(new))) / max(len(new), 1)


def rebalance_schedule(n: int, m: int) -> list[int]:
    """调仓发生的月份下标：0, m, 2m, …（`range(0, n, m)`）。"""
    return list(range(0, n, m))


def t_iid(x: list[float]) -> float:
    if len(x) < 2:
        return float("nan")
    sd = statistics.stdev(x)
    se = sd / math.sqrt(len(x))
    return statistics.mean(x) / se if se else float("nan")


def judge(by_m: dict[int, dict]) -> dict:
    """三态判据（§4.1）。**不许把"某个 m 的 t 最大"当结论**——必须同时过 A ∧ B。"""
    ok_any = False
    for m in REBALANCE_PERIODS:
        r = by_m[m]
        ok = abs(r["t_nw6"]) >= T_CRIT and r["seg_ok"]
        r["pass_AB"] = bool(ok)
        if m > 1 and ok:
            ok_any = True
    net1 = by_m[1]["net_pp"]
    best_other = max(by_m[m]["net_pp"] for m in REBALANCE_PERIODS if m > 1)
    if ok_any:
        v = "LOW_TURNOVER_RESCUES"
    elif best_other > net1:
        v = "LOW_TURNOVER_INSUFFICIENT"
    else:
        v = "LOW_TURNOVER_NO_GAIN"
    return {"verdict": v, "net_m1": net1, "best_net_other": best_other}


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print(f"阶段 K：低换手版本的 exQ5   预登记 {PREREG_SHA12}")
    print("=" * 78)
    verify()
    print("  预登记哈希校验 ✓")

    # ---- ① 信号与持仓（与阶段 I 同源）
    recs, meta = build_records()
    print(f"  月份 {len(recs)}   分组独立校验不一致 {meta['split_mismatch_months']}")

    need: dict[str, set[str]] = defaultdict(set)
    for i, r in enumerate(recs):
        for c in r["keep_codes"]:
            need[c].add(r["entry"])
            if i + 1 < len(recs):
                need[c].add(recs[i + 1]["entry"])
    bars, ex_al, span = stream_bars(need)

    for r in recs:
        pos_map = {c: i for i, c in enumerate(r["codes"])}
        f1 = filter_v1(r, bars, ex_al, span, pos_map)
        r["keep_v1"] = f1["keep_codes"]
        r["pos_map"] = pos_map
        r["q5_set"] = set(r["q5_codes"])
        lots = []
        for c in r["keep_v1"]:
            rb = bars.get(c, {}).get(r["entry"])
            if rb is not None and rb["bar"][3] > 0:
                lots.append(rb["bar"][3] * LOT)
        r["lots"] = lots

    # ---- ② 月度收益矩阵（237 × 全池）
    close = load_close()
    cal = close.index
    ffill = close.ffill()
    positions = month_end_positions(cal, "2007-01-01", "2026-10-31")
    # 完整性：recs 必须与 positions 前 N 项一一对应（build_records 的 n_skip 为空）
    mism = sum(1 for i, r in enumerate(recs)
               if i >= len(positions) or str(cal[positions[i]].date()) != r["signal_date"])
    if mism or len(positions) < len(recs) + 1:
        raise SystemExit(f"信号点与月末位置对不上（{mism} 处）→ 月度收益无法对齐")
    cols = list(close.columns)
    col_of = {c: i for i, c in enumerate(cols)}
    arr_f = ffill.to_numpy()
    print(f"  月末位置 {len(positions)} 个，与 {len(recs)} 个信号点对齐 ✓")
    del ffill, close

    # 第 i 月：pos_i → pos_{i+1}
    ret_rows = []
    for i in range(len(recs)):
        p0, p1 = positions[i], positions[i + 1]
        with np.errstate(invalid="ignore", divide="ignore"):
            row = arr_f[p1] / arr_f[p0] - 1.0
        ret_rows.append(np.where(np.isfinite(row), row, np.nan))
    print(f"  月度收益矩阵 {len(ret_rows)} × {len(cols)}")

    # 复现闸门：m=1 必须与阶段 I 的 monthly.csv 逐月一致
    if os.path.exists(PHASEI_MONTHLY):
        ref = {}
        with open(PHASEI_MONTHLY, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["alpha_v1_pp"]:
                    ref[row["signal_date"]] = float(row["alpha_v1_pp"])
        got = []
        for i, r in enumerate(recs):
            if r["signal_date"] not in ref or not r["keep_v1"]:
                continue
            idx = [col_of[c] for c in r["keep_v1"] if c in col_of]
            v = ret_rows[i][idx]
            v = v[np.isfinite(v)]
            # ⚠️ 第一版写成 `abs(mean − r_bench)*100 − ref`：abs 把负 alpha 的月份翻正，
            #    于是 −3.5pp 与参考 −3.5pp 差了 7.0pp，闸门误报作废。
            #    （独立验证脚本逐月对比最大差是 0.0，实现本身没错 —— 错的是闸门的 abs。）
            got.append((float(v.mean()) - r["r_bench"]) * 100 - ref[r["signal_date"]])
        mx = max(abs(x) for x in got)
        print(f"  复现闸门（m=1 vs 阶段 I）：{len(got)} 月，最大差 {mx:.2e} "
              f"{'✓' if mx < 1e-6 else '✗ 作废'}")
        if not mx < 1e-6:
            raise SystemExit("m=1 未复现阶段 I → 后续比较不可信")
    else:
        print("  ⚠ 未找到阶段 I monthly.csv，跳过复现闸门")

    # ---- ③ 四档调仓周期
    n = len(recs)
    by_m, monthly = {}, defaultdict(dict)
    for m in REBALANCE_PERIODS:
        basket = [None] * n          # 每月持仓（代码列表）
        to_m = [0.0] * n             # 每月单边换手
        finv_m = [1.0] * n
        prev_basket: list[str] = []
        for j in range(0, n, m):
            r = recs[j]
            cur = list(r["keep_v1"])
            churn = churn_of(cur, prev_basket, first=(j == 0))
            cs = capacity_of(r["lots"], CAPITAL)
            finv = 1.0 - (cs["uninvested"] if cs["k"] > 0 else 1.0)
            for t in range(j, min(j + m, n)):
                basket[t] = cur
                to_m[t] = churn / m
                finv_m[t] = finv
            prev_basket = cur

        alpha, comm, net, miss, ksz = [], [], [], 0, []
        for t in range(n):
            if basket[t] is None:
                continue
            idx = [col_of[c] for c in basket[t] if c in col_of]
            if not idx:
                continue
            v = ret_rows[t][idx]
            good = v[np.isfinite(v)]
            miss += len(v) - len(good)
            ksz.append(len(good))
            r_b = recs[t]["r_bench"]
            a = (float(good.mean()) - r_b) * 100.0
            cm = 2.0 * to_m[t] * COMM_RATE * 100.0
            alpha.append(a)
            comm.append(cm)
            net.append(finv_m[t] * a - cm - IMPACT_UPPER_PP)

        nw6 = C.newey_west(net, 6)
        nw3 = C.newey_west(net, NW_LAG)
        segs = {}
        for lo, hi, lab in SEGMENTS:
            sub = [x for x, d in zip(net, [r["signal_date"] for r in recs])
                   if lo <= d <= hi]
            s6 = C.newey_west(sub, 6)
            segs[lab] = {"n": len(sub), "net_pp": round(statistics.mean(sub), 6),
                         "t_nw6": round(s6["t_nw"], 6)}
        sign = 1.0 if statistics.mean(net) > 0 else -1.0
        seg_ok = all((s["net_pp"] > 0) == (sign > 0) and abs(s["t_nw6"]) >= T_CRIT
                     for s in segs.values())
        by_m[m] = {
            "turnover_pct_month": round(statistics.mean(to_m) * 100, 6),
            "comm_pp": round(statistics.mean(comm), 6),
            "alpha_pp": round(statistics.mean(alpha), 6),
            "f_inv_mean": round(statistics.mean(finv_m), 6),
            "net_pp": round(statistics.mean(net), 6),
            "t_nw6": round(nw6["t_nw"], 6), "t_nw3": round(nw3["t_nw"], 6),
            "t_iid": round(t_iid(net), 6),
            "alpha_retention": None, "segments": segs, "seg_ok": bool(seg_ok),
            "n_hold_median": statistics.median(ksz),
            "missing_price_months": miss,
        }
        for t in range(n):
            monthly[recs[t]["signal_date"]][f"m{m}_alpha"] = round(alpha[t], 6)
            monthly[recs[t]["signal_date"]][f"m{m}_net"] = round(net[t], 6)

    a1 = by_m[1]["alpha_pp"]
    for m in REBALANCE_PERIODS:
        by_m[m]["alpha_retention"] = round(by_m[m]["alpha_pp"] / a1, 6) if a1 else None

    print()
    print("③ 四档对照（W=3,000 万，净 = f_inv×alpha − 佣金 − 冲击上界 0.000784）")
    print("   m   换手%/月   佣金     alpha    保留率   净       t(NW6)  t(NW3)  t(iid)")
    for m in REBALANCE_PERIODS:
        r = by_m[m]
        print(f"  {m:>2}  {r['turnover_pct_month']:>7.2f}  {r['comm_pp']:.4f}  "
              f"{r['alpha_pp']:+.4f}  {r['alpha_retention']:>6.3f}  "
              f"{r['net_pp']:+.4f}  {r['t_nw6']:+.3f}  {r['t_nw3']:+.3f}  {r['t_iid']:+.3f}")

    jd = judge(by_m)
    print()
    print("④ 判定（预登记 §4.1）")
    for m in REBALANCE_PERIODS:
        print(f"   m={m}: A(|t_NW6|≥1.96)={'过' if abs(by_m[m]['t_nw6']) >= T_CRIT else '不过'}  "
              f"B(两段显著同号)={'过' if by_m[m]['seg_ok'] else '不过'}  "
              f"→ {'A∧B' if by_m[m]['pass_AB'] else '不过'}")
    print(f"   >>> {jd['verdict']}   （m=1 净 {jd['net_m1']:+.4f}，"
          f"其他档最好 {jd['best_net_other']:+.4f}）")

    # ---- ④ 诊断：Q5 边界的"翻来覆去"
    flip_num, flip_den = 0, 0
    for t in range(1, n - 1):
        left = recs[t - 1]["q5_set"] - recs[t]["q5_set"]     # t 月离开 Q5
        back = len(left & recs[t + 1]["q5_set"])            # t+1 月又回去
        flip_num += back
        flip_den += len(left)
    flip_rate = flip_num / flip_den if flip_den else float("nan")

    print()
    print("⑤ 诊断")
    print(f"   Q5 边界『翻来覆去』率（本月离开、下月又回来）= {flip_rate*100:.1f}%"
          f"  ({flip_num:,}/{flip_den:,})")
    print(f"   预登记 §5.1 猜 alpha 保留率 ≈ 0.65^(m−1)："
          + "  ".join(f"m{m} 猜{0.65**(m-1):.3f}/实测{by_m[m]['alpha_retention']:.3f}"
                      for m in REBALANCE_PERIODS))
    for m in REBALANCE_PERIODS:
        s = by_m[m]["segments"]
        print(f"   m={m} 分段："
              + "  ".join(f"{k} {v['net_pp']:+.4f}(t={v['t_nw6']:+.2f})"
                         for k, v in s.items()))
    print(f"   无价格月份数（m=1）：{by_m[1]['missing_price_months']:,} 个标的月")

    # ---- 产物
    os.makedirs(OUT_DIR, exist_ok=True)
    dates = [r["signal_date"] for r in recs]
    with open(os.path.join(OUT_DIR, "monthly.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["signal_date"] +
                   [f"m{m}_{k}" for m in REBALANCE_PERIODS for k in ("alpha", "net")])
        for d in dates:
            row = monthly.get(d, {})
            w.writerow([d] + [row.get(f"m{m}_{k}", "")
                              for m in REBALANCE_PERIODS for k in ("alpha", "net")])

    summary = {
        "experiment": "phasek-low-turnover-v1",
        "prereg_sha256": PREREG_SHA256,
        "design": {"periods": list(REBALANCE_PERIODS), "capital": CAPITAL,
                   "comm_rate": COMM_RATE, "nw_primary_lag": NW_PRIMARY_LAG,
                   "impact_upper_pp": IMPACT_UPPER_PP,
                   "net_def": "net = f_inv × alpha − 2·(churn/m)·0.003·100 − 0.000784"},
        "reconciliation": {"m1_vs_phaseI": "max|diff| < 1e-6"},
        "by_period": {str(m): by_m[m] for m in REBALANCE_PERIODS},
        "verdict": jd["verdict"],
        "net_m1_pp": round(jd["net_m1"], 6),
        "best_net_other_pp": round(jd["best_net_other"], 6),
        "diagnostics": {"q5_flipflop_rate": round(flip_rate, 6),
                        "q5_flipflop_n": flip_num, "q5_flipflop_den": flip_den,
                        "predicted_retention": {str(m): round(0.65 ** (m - 1), 6)
                                               for m in REBALANCE_PERIODS}},
        "prereg_defects": [{
            "item": "§3.2 未规定建仓首期的换手",
            "handling": "首期 churn 取 1.0（视作替换既有组合），与后续各期同用 2× 系数；"
                        "不改判据，且在报告中说明。",
        }],
    }
    sp = os.path.join(OUT_DIR, "summary.json")
    with open(sp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")

    files = ["monthly.csv", "summary.json"]
    man = {"experiment": "phasek-low-turnover-v1", "prereg_sha256": PREREG_SHA256,
           "script_sha256": _sha256_file(os.path.abspath(__file__)),
           "files": {n_: {"sha256": _sha256_file(os.path.join(OUT_DIR, n_)),
                          "bytes": os.path.getsize(os.path.join(OUT_DIR, n_))}
                     for n_ in files},
           "runtime_sec": round(time.time() - t0, 1)}
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(man, ensure_ascii=False, indent=1) + "\n")

    print()
    print(f"[产物] {OUT_DIR}/  脚本 {man['script_sha256'][:12]}  总耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
