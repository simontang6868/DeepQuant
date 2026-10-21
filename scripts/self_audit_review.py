#!/usr/bin/env python
"""评审包 §10 三条质疑的自查。

评审包 §10 给了评审者三个建议提问点。本脚本在**评审者提问之前**先自查：

1. **口径**（§10.1）：阶段 F 的 Δ 是「层内价差按层大小加权」。
   "最低波动层是负的"被这个加权抹平了吗？换几种聚合规则，结论还成立吗？
   → 预声明四种规则，全报，不挑。
2. **外推**（§10.2）：§5.1「空头侧现象」只有两个样本 → 已由**阶段 M** 补测（SURGE）。
   本脚本只做交叉汇总。
3. **样本内**（§10.3）：多重检验校正 + "不衰减"是否只是"没被拒绝"。

用法：
    python scripts/self_audit_review.py
"""

from __future__ import annotations

import csv
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
    END_HOLD, END_SIGNAL, MIN_LISTED_DAYS, NW_LAG, START, build_mask, load_close,
)
from run_phase_f_max5 import MIN_VALID, N_GROUP, TOP_N, WIN, split_equal  # noqa: E402
from stage3_sigma_d import month_end_positions  # noqa: E402

ALPHA = 0.05
SEGMENTS = (("2007-01-01", "2015-12-31", "2007-2015"),
            ("2016-01-01", "2026-09-30", "2016-2026"))

# 17 个实验的 |t|（全部来自产物；评审包 §3 总表）
# 来源：reports/ROBUSTNESS_TABLE.json + 各阶段 summary.json
TESTS = [
    ("Gate 1 月频动量", 0.569), ("Gate 2 反转2016-2026", 1.642),
    ("Gate 3 反转2007-2015", 2.117), ("Gate 4 低波动", 0.868),
    ("Gate 5 活跃度", 0.155), ("Gate 6 反转全A", 4.386),
    ("阶段 C 池效应", 6.367), ("阶段 C-2 剔不可成交", 5.015),
    ("阶段 C-3 成交延迟", 0.597), ("阶段 E 反转单边", 1.901),
    ("阶段 F MAX5分层", 3.409), ("阶段 G MAX5单边", 1.764),
    ("阶段 H 排除Q5(评估)", 6.357), ("阶段 I exQ5可成交", 4.528),
    ("阶段 J 冲击成本", 1.269), ("阶段 K 降换手(最好档)", 1.433),
    ("阶段 L SURGE", 3.130),
]


def p_two_sided(t: float) -> float:
    # 双侧 p，正态近似。p = erfc(|t|/√2) = 2·(1−Φ(|t|))
    return math.erfc(abs(t) / math.sqrt(2.0))


def t_for_p(p: float) -> float:
    """给定双侧 p，反解 |t|。标准库没有 erfcinv，用 NormalDist 的反 CDF。

    p = erfc(z) 且 |t| = √2·z，Φ(z√2) = 1 − p/2
    → |t| = inv_cdf(1 − p/2)
    """
    return statistics.NormalDist().inv_cdf(1.0 - p / 2.0)


def holm(pairs: list[tuple[str, float]]) -> dict:
    """Holm-Bonferroni 逐步校正。返回逐项的 (阈值, 是否拒绝)。"""
    m = len(pairs)
    order = sorted(range(m), key=lambda i: pairs[i][1])
    out, still = {}, True
    for rank, i in enumerate(order):
        thr = ALPHA / (m - rank)
        rej = pairs[i][1] <= thr and still
        if not rej:
            still = False
        out[pairs[i][0]] = {"p": pairs[i][1], "threshold": thr, "reject": bool(rej)}
    return out


# ==================== §10.1 阶段 F 的聚合方式稳健性 ====================
def max5_records() -> list[dict]:
    close = load_close()
    cal = close.index
    mask = build_mask(close, MIN_LISTED_DAYS)
    positions = month_end_positions(cal, START, END_HOLD)
    ret = close.pct_change(fill_method=None).to_numpy()
    cols = np.array(close.columns)
    ffill = close.ffill().to_numpy()

    recs = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sdate = str(cal[pos].date())
        if sdate > END_SIGNAL or pos - WIN + 1 < 0 or pos + 1 >= len(cal):
            continue
        ok = mask.iloc[pos].to_numpy()
        if ok.sum() < 100:
            continue
        idx = np.where(ok)[0]
        w = ret[pos - WIN + 1: pos + 1][:, idx]
        good = np.isfinite(w).sum(axis=0) >= MIN_VALID
        if good.sum() < 100:
            continue
        sub = w[:, good]
        m5 = np.sort(np.where(np.isfinite(sub), sub, -np.inf), axis=0)[-TOP_N:, :].mean(axis=0)
        vol = np.nanstd(np.where(np.isfinite(sub), sub, np.nan), axis=0, ddof=1)
        codes = cols[idx][good]
        fin = np.isfinite(m5) & np.isfinite(vol)
        codes, m5, vol = codes[fin], m5[fin], vol[fin]
        p0 = ffill[pos][idx[good][fin]]
        p1 = ffill[nxt][idx[good][fin]]
        with np.errstate(invalid="ignore", divide="ignore"):
            fwd = p1 / p0 - 1.0
        fm = np.isfinite(fwd)
        if fm.sum() < 100:
            continue
        recs.append({"signal_date": sdate, "m5": m5[fm], "vol": vol[fm],
                     "fwd": fwd[fm]})
    return recs


def aggregations(recs: list[dict]) -> dict:
    """四种聚合规则，**全报，不挑**。规则在跑之前写死。"""
    out = defaultdict(list)
    for r in recs:
        n = len(r["fwd"])
        vg = split_equal(r["vol"], N_GROUP)
        gg = split_equal(r["m5"], N_GROUP)
        per = {}
        for L in range(N_GROUP):
            sub = np.where(vg == L)[0]
            if len(sub) < 25:
                continue
            # ⚠️ 必须在**层内**重排 MAX5。第一版用了全池分位（gg），
            #    由于 MAX5 与波动率相关 0.88，最低波动层里几乎没有 Q5 → 层内比较塌陷。
            g2 = split_equal(r["m5"][sub], N_GROUP)
            q1 = sub[g2 == 0]
            q5 = sub[g2 == N_GROUP - 1]
            if len(q1) < 5 or len(q5) < 5:
                continue
            per[L] = {"d": float(r["fwd"][q1].mean() - r["fwd"][q5].mean()),
                      "n": len(sub)}
        if not per:
            continue
        # (i) 按层大小加权（阶段 F 原口径）
        wsum = sum(v["n"] for v in per.values())
        out["i_weighted_by_layer"].append(
            sum(v["d"] * v["n"] for v in per.values()) / wsum * 100)
        # (ii) 各层等权（不按大小加权）
        out["ii_equal_layer"].append(statistics.mean([v["d"] for v in per.values()]) * 100)
        # (iii) 各层取中位数
        out["iii_median_layer"].append(
            statistics.median([v["d"] for v in per.values()]) * 100)
        # (iv) 只用 L2~L4（去掉最低与最高波动层）
        mid = [v["d"] for L, v in per.items() if L in (1, 2, 3)]
        if mid:
            out["iv_mid_layers_only"].append(statistics.mean(mid) * 100)
        # (v) 全池不分层（对照）
        out["v_pool_unstratified"].append(
            float(r["fwd"][gg == 0].mean() - r["fwd"][gg == N_GROUP - 1].mean()) * 100)
    return out


# ==================== §10.3 多重检验 + 不衰减的正式检验 ====================
def segment_difference(path: str, col: str) -> dict:
    """两段之差的 Welch 检验：'不衰减'到底是"真的不衰减"还是"没被拒绝"。"""
    if not os.path.exists(path):
        return {"error": f"缺失 {path}"}
    a, b = [], []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            v = row.get(col)
            if not v:
                continue
            d = row["signal_date"]
            if "2007-01-01" <= d <= "2015-12-31":
                a.append(float(v))
            elif "2016-01-01" <= d <= "2026-09-30":
                b.append(float(v))
    if len(a) < 5 or len(b) < 5:
        return {"error": "样本不足"}
    ma, mb = statistics.mean(a), statistics.mean(b)
    va, vb = statistics.variance(a), statistics.variance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    t = (mb - ma) / se if se else float("nan")
    return {"n_early": len(a), "n_late": len(b),
            "mean_early_pp": round(ma, 6), "mean_late_pp": round(mb, 6),
            "diff_late_minus_early_pp": round(mb - ma, 6),
            "welch_t": round(t, 4), "p": p_two_sided(t),
            "reading": ("两段之差显著（真有变化）" if abs(t) >= 1.96
                        else "两段之差**不显著** → '不衰减'目前只是'没被拒绝'，"
                             "不能反过来宣称'证明不衰减'")}


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print("评审包 §10 三条质疑 · 自查")
    print("=" * 78)

    # ---- §10.3a 多重检验
    print()
    print("【§10.3a】多重检验校正（17 个实验，双侧，正态近似）")
    pairs = [(n, p_two_sided(t)) for n, t in TESTS]
    h = holm(pairs)
    bonf = ALPHA / len(pairs)
    print(f"   Bonferroni 阈值 α/m = {bonf:.5f} → 对应 |t| ≥ "
          f"{t_for_p(bonf):.3f}")
    survived = [n for n, r in h.items() if r["reject"]]
    print(f"   Holm 后仍拒绝原假设的 {len(survived)}/{len(pairs)} 个：")
    for n in survived:
        print(f"     ✓ {n:<24} p={h[n]['p']:.2e}")
    dropped = [n for n, r in h.items() if not r["reject"]]
    print(f"   未通过：{len(dropped)} 个 —— " + "、".join(dropped[:6]) +
          ("…" if len(dropped) > 6 else ""))

    # ---- §10.3b 不衰减
    print()
    print("【§10.3b】『不衰减』是不是只是『没被拒绝』")
    for path, col, tag in (
        ("phasei_out/8f5c35121fdb/monthly.csv", "alpha_v1_pp", "阶段 I exQ5 alpha"),
        ("phasej_out/f76de8a277f6/monthly.csv", "m1_net", "阶段 J 净值(m=1)"),
        ("phasek_out/3bc31a35903d/monthly.csv", "m2_net", "阶段 K 净值(m=2)"),
        ("phasel_out/9ffc3710cf6f/monthly.csv", "alpha_v1_pp", "阶段 L SURGE alpha"),
    ):
        r = segment_difference(path, col)
        if "error" in r:
            print(f"   {tag}: {r['error']}")
            continue
        print(f"   {tag}: 前段 {r['mean_early_pp']:+.4f} / 后段 {r['mean_late_pp']:+.4f}  "
              f"差 {r['diff_late_minus_early_pp']:+.4f}pp  Welch t={r['welch_t']:+.2f}")
        print(f"      → {r['reading']}")

    # ---- §10.1 聚合方式
    print()
    print("【§10.1】阶段 F 的聚合方式稳健性（四种规则，全报不挑）")
    recs = max5_records()
    agg = aggregations(recs)
    agg_out = {}
    for k, v in agg.items():
        nw = C.newey_west(v, NW_LAG)
        agg_out[k] = {"n": len(v), "mean_pp": round(statistics.mean(v), 6),
                      "t_nw": round(nw["t_nw"], 6)}
        print(f"   {k:<24} Δ={statistics.mean(v):+.4f}pp  t={nw['t_nw']:+.3f}  "
              f"{'显著' if abs(nw['t_nw']) >= 1.96 else '不显著'}")

    out = {
        "script": "self_audit_review",
        "purpose": "评审包 §10 三条质疑的自查（评审者提问之前先自查）",
        "multiple_testing": {
            "n_tests": len(pairs), "bonferroni_alpha": bonf,
            "bonferroni_t": round(t_for_p(bonf), 4),
            "holm": {k: {"p": round(v["p"], 8), "threshold": round(v["threshold"], 8),
                         "reject": v["reject"]} for k, v in h.items()},
            "survived": survived,
        },
        "stability_tests": {
            "阶段 I exQ5 alpha": segment_difference(
                "phasei_out/8f5c35121fdb/monthly.csv", "alpha_v1_pp"),
            "阶段 J 净值(m=1)": segment_difference(
                "phasej_out/f76de8a277f6/monthly.csv", "m1_net"),
            "阶段 K 净值(m=2)": segment_difference(
                "phasek_out/3bc31a35903d/monthly.csv", "m2_net"),
            "阶段 L SURGE alpha": segment_difference(
                "phasel_out/9ffc3710cf6f/monthly.csv", "alpha_v1_pp"),
        },
        "phase_f_aggregation": agg_out,
        "runtime_sec": round(time.time() - t0, 1),
    }
    os.makedirs("reports", exist_ok=True)
    p = "reports/REVIEW_SELF_AUDIT.json"
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print()
    print(f"[产物] {p}   耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
