#!/usr/bin/env python
"""阶段 M：SURGE 的单边/多空比（ratio 补测）。

预登记 `docs/PHASE_M_SURGE_RATIO_PREREG_2026-10-21.md`，冻结 `2f5f61dd0b5f…`。

## 动因

评审包 §10 第 2 条质疑：§5.1「横截面异象天然是空头侧现象」目前只有两个样本
（反转 ratio 0.21、MAX5 ratio 0.24）。SURGE 是**避免型**构造，ratio 未单独测。
本阶段补第三个独立支撑。不需要新数据。

## 测什么

    spread_M    = Σ_L w_L × [R(Q1_L) − R(Q5_L)]        （波动率分层后，层内价差）
    alpha_buy   = R(∪_L Q1_L) − R_bench                 买型   （与阶段 G 可比）
    alpha_avoid = R(∪_L 层内除去 Q5_L) − R_bench         避免型 （与阶段 L 可比）
    ratio_buy   = alpha_buy / spread_M
    ratio_avoid = alpha_avoid / spread_M

⚠️ 两个 ratio 都要报。`alpha_avoid ≡ 0.25×(R_bench−R_Q5)` 是恒等式，
   但 `ratio_avoid` 不是常数（它取决于 Q1 相对池的位置），必须实测。

用法：
    python scripts/run_phase_m_surge_ratio.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import statistics
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_c import NW_LAG, stream_bars  # noqa: E402
from run_phase_i_exq5 import LOT, filter_v1, split_equal_indep  # noqa: E402
from run_phase_l_surge import N_GROUP, Q_HIGH, build_records, judge  # noqa: E402

PREREG_SHA256 = "2f5f61dd0b5f510b9b54a17739f2cb7675c35aa1b21e052b1da67bda4f4a9cd2"
PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phasem_out", PREREG_SHA12)

# 复现闸门：阶段 L 的两个数（无约束 / 可成交）
GATE = {"v0_avoid": 0.1858, "v1_avoid": 0.1061}
GATE_TOL = 1e-4                    # 阶段 L 只印了 4 位小数，容差按此给
RATIO_LOW, RATIO_HIGH = 0.10, 0.35  # H1 的区间


def verify() -> None:
    p = "docs/PHASE_M_SURGE_RATIO_PREREG_2026-10-21.md"
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


def judge_ratio(rb: float, ra: float) -> dict:
    h1 = RATIO_LOW <= rb <= RATIO_HIGH
    h2 = ra > rb
    v = ("SHORT_SIDE_HYPOTHESIS_HOLDS" if (h1 and h2)
         else "SHORT_SIDE_PARTIAL" if h1 else "SHORT_SIDE_REJECTED")
    return {"H1_ratio_buy_in_range": bool(h1), "H2_avoid_gt_buy": bool(h2),
            "verdict": v}


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print(f"阶段 M：SURGE 的单边/多空比   预登记 {PREREG_SHA12}")
    print("=" * 78)
    verify()
    print("  预登记哈希校验 ✓")

    recs, meta = build_records()
    print(f"  月份 {len(recs)}   共线性 SURGE vs vol {meta['corr_median']['vol']:+.4f}")

    # ---- 可成交约束（得到 V1 持仓）
    need: dict[str, set[str]] = defaultdict(set)
    for i, r in enumerate(recs):
        for c in r["keep_codes"]:
            need[c].add(r["entry"])
            if i + 1 < len(recs):
                need[c].add(recs[i + 1]["entry"])
    print(f"  需扫 EXEC {len(need)} 只 · {sum(len(v) for v in need.values())} 点 …")
    bars, ex_al, span = stream_bars(need)
    for r in recs:
        pm = {c: i for i, c in enumerate(r["codes"])}
        f1 = filter_v1(r, bars, ex_al, span, pm)
        r["keep_v1"] = f1["keep_codes"]
        r["pos_map"] = pm

    # ---- 逐月三个量（无约束 V0 / 可成交 V1）
    def per_month(use_v1: bool) -> dict[str, list[float]]:
        out = defaultdict(list)
        for r in recs:
            fwd = np.array(r["fwd"])
            sur = np.array(r["surge"])
            vol = np.array(r["vol"])
            bench = r["r_bench"]
            if use_v1:
                keep_set = set(r["keep_v1"])
                mask = np.array([c in keep_set for c in r["codes"]])
                if mask.sum() < 100:
                    continue
                sel = np.where(mask)[0]
            else:
                sel = np.arange(len(fwd))
            # 分层：在 sel 内按波动率分 5 层
            vg = split_equal_indep(vol[sel], N_GROUP)
            num, wsum = 0.0, 0.0
            q1u, avoidu = [], []
            for L in range(N_GROUP):
                sub = sel[vg == L]
                if len(sub) < 20:
                    continue
                g2 = split_equal_indep(sur[sub], N_GROUP)
                q1 = sub[g2 == 0]
                q5 = sub[g2 == Q_HIGH]
                if len(q1) < 5 or len(q5) < 5:
                    continue
                r1 = float(fwd[q1].mean())
                r5 = float(fwd[q5].mean())
                num += (r1 - r5) * len(sub)
                wsum += len(sub)
                q1u.extend(q1.tolist())
                av = sub[g2 != Q_HIGH]
                if len(av) >= 5:
                    avoidu.extend(av.tolist())
            if wsum == 0 or not q1u:
                continue
            sp = num / wsum
            a_buy = float(fwd[q1u].mean()) - bench
            a_av = float(fwd[avoidu].mean()) - bench if avoidu else float("nan")
            out["spread"].append(sp * 100)
            out["alpha_buy"].append(a_buy * 100)
            out["alpha_avoid"].append(a_av * 100)
        return out

    res = {"V0": per_month(False), "V1": per_month(True)}

    print()
    print("① 三个量（pp/月，波动率分层口径）")
    stats = {}
    for tag in ("V0", "V1"):
        d = res[tag]
        nw_sp = C.newey_west(d["spread"], NW_LAG)
        nw_b = C.newey_west(d["alpha_buy"], NW_LAG)
        nw_a = C.newey_west(d["alpha_avoid"], NW_LAG)
        m_sp = statistics.mean(d["spread"])
        rb = statistics.mean(d["alpha_buy"]) / m_sp if m_sp else float("nan")
        ra = statistics.mean(d["alpha_avoid"]) / m_sp if m_sp else float("nan")
        stats[tag] = {
            "n": len(d["spread"]),
            "spread_pp": m_sp, "spread_t": nw_sp["t_nw"],
            "alpha_buy_pp": statistics.mean(d["alpha_buy"]), "alpha_buy_t": nw_b["t_nw"],
            "alpha_avoid_pp": statistics.mean(d["alpha_avoid"]), "alpha_avoid_t": nw_a["t_nw"],
            "ratio_buy": rb, "ratio_avoid": ra,
            "ratio_buy_ci95": None,
        }
        s = stats[tag]
        print(f"   {tag}: spread {s['spread_pp']:+.4f}(t={s['spread_t']:+.2f})   "
              f"buy {s['alpha_buy_pp']:+.4f}(t={s['alpha_buy_t']:+.2f})   "
              f"avoid {s['alpha_avoid_pp']:+.4f}(t={s['alpha_avoid_t']:+.2f})")
        print(f"       ratio_buy={rb:.4f}   ratio_avoid={ra:.4f}")

    # ---- 复现闸门
    # ⚠️ 只用**全池口径**校验。分层并集的 avoid 与阶段 L 的全池 avoid
    #    **不是同一个量**（阶段 L 印的是全池 0.1858/0.1061）。
    #    第一版把分层值也 AND 进 gate_ok → 分类错误，导致闸门误判作废。
    print()
    print("② 复现闸门（对照阶段 L 的**全池**口径）")

    def pool_avoid(use_v1: bool) -> list[float]:
        out = []
        for r in recs:
            fwd = np.array(r["fwd"])
            grp = np.array(r["grp"])
            if use_v1:
                ks = set(r["keep_v1"])
                sel = [i for i, c in enumerate(r["codes"]) if c in ks]
            else:
                sel = np.where(grp != Q_HIGH)[0]
            if len(sel) < 100:
                continue
            out.append(float(fwd[sel].mean()) - r["r_bench"])
        return out

    pool = {"V0": pool_avoid(False), "V1": pool_avoid(True)}
    pool_pp = {}
    gate_ok = True
    for tag in ("V0", "V1"):
        nw = C.newey_west(pool[tag], NW_LAG)
        pool_pp[tag] = nw["mean_pp"]
        ok = abs(nw["mean_pp"] - GATE["v0_avoid" if tag == "V0" else "v1_avoid"]) <= GATE_TOL
        gate_ok &= ok
        print(f"   {tag} 全池 avoid alpha {nw['mean_pp']:+.6f}pp (t={nw['t_nw']:+.3f})  "
              f"vs 阶段 L {GATE['v0_avoid' if tag=='V0' else 'v1_avoid']:+.4f}  "
              f"{'✓' if ok else '✗ 作废'}")
    if not gate_ok:
        raise SystemExit("复现闸门不通过 → 后续 ratio 不可信")

    # ---- ratio 的抽样不确定性（描述性，不改判据）
    # ratio 是**两个噪声量之比**，点估计会骗人。给一个月份级 bootstrap 区间。
    def boot_ci(d: dict[str, list[float]], n: int = 2000, seed: int = 20261021):
        rng = np.random.default_rng(seed)
        m = len(d["spread"])
        sp = np.array(d["spread"])
        by = np.array(d["alpha_buy"])
        rs = []
        for _ in range(n):
            k = rng.integers(0, m, m)
            s = sp[k].mean()
            if s == 0:
                continue
            rs.append(by[k].mean() / s)
        return float(np.percentile(rs, 2.5)), float(np.percentile(rs, 97.5))

    ci = {tag: boot_ci(res[tag]) for tag in ("V0", "V1")}
    print()
    print("   ratio_buy 的 bootstrap 95% 区间（月份重抽，描述性）：")
    for tag in ("V0", "V1"):
        print(f"     {tag}: [{ci[tag][0]:.3f}, {ci[tag][1]:.3f}]  "
              f"点估计 {stats[tag]['ratio_buy']:.3f}")

    # ---- 判据
    jd = judge_ratio(stats["V0"]["ratio_buy"], stats["V0"]["ratio_avoid"])
    print()
    print("③ 判据（H1 用 V0 无约束口径，与阶段 E/G 可比）")
    print(f"   H1  ratio_buy ∈ [{RATIO_LOW}, {RATIO_HIGH}] : "
          f"{stats['V0']['ratio_buy']:.4f}  → {'成立' if jd['H1_ratio_buy_in_range'] else '不成立'}")
    print(f"   H2  ratio_avoid > ratio_buy : "
          f"{stats['V0']['ratio_avoid']:.4f} > {stats['V0']['ratio_buy']:.4f}  → "
          f"{'成立' if jd['H2_avoid_gt_buy'] else '不成立'}")
    print(f"   >>> {jd['verdict']}")

    print()
    print("④ 三个信号对照（§5.1 的支撑数）")
    print("   信号        多空价差   单边alpha   ratio")
    print(f"   21日反转     1.3164    +0.2767    0.210   （阶段 E）")
    print(f"   MAX5         0.7405    +0.1813    0.245   （阶段 F/G）")
    print(f"   SURGE      {stats['V0']['spread_pp']:8.4f}  {stats['V0']['alpha_buy_pp']:+8.4f}   "
          f"{stats['V0']['ratio_buy']:.3f}   （本阶段）")

    # ---- 产物
    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join(OUT_DIR, "monthly.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["i", "spread_pp", "alpha_buy_pp", "alpha_avoid_pp"])
        d = res["V0"]
        for i in range(len(d["spread"])):
            w.writerow([i, round(d["spread"][i], 6), round(d["alpha_buy"][i], 6),
                        round(d["alpha_avoid"][i], 6)])

    summary = {
        "experiment": "phasem-surge-ratio-v1",
        "prereg_sha256": PREREG_SHA256,
        "question": "SURGE 的单边/多空比是多少？为 §5.1 补第三个独立支撑",
        "reproduction_gate": {"v0_pool_avoid_pp": round(pool_pp["V0"], 6),
                              "v1_pool_avoid_pp": round(pool_pp["V1"], 6),
                              "expected": GATE, "tol": GATE_TOL, "passed": bool(gate_ok)},
        "by_version": {k: {kk: (round(vv, 6) if isinstance(vv, float) else vv)
                           for kk, vv in v.items()} for k, v in stats.items()},
        "ratio_buy_bootstrap95": {k: [round(v[0], 4), round(v[1], 4)] for k, v in ci.items()},
        "criteria": jd,
        "verdict": jd["verdict"],
        "cross_signal": {
            "reversal": {"spread_pp": 1.3164, "alpha_pp": 0.2767, "ratio": 0.210,
                         "source": "阶段 E / 阶段 C D格"},
            "max5": {"spread_pp": 0.7405, "alpha_pp": 0.1813, "ratio": 0.245,
                     "source": "阶段 G / 阶段 F"},
            "surge": {"spread_pp": round(stats["V0"]["spread_pp"], 6),
                      "alpha_pp": round(stats["V0"]["alpha_buy_pp"], 6),
                      "ratio": round(stats["V0"]["ratio_buy"], 6), "source": "本阶段"},
        },
        "note": "ratio_buy 用买型单边（与阶段 G 可比）；ratio_avoid 另报。"
                "alpha_avoid ≡ 0.25×(R_bench−R_Q5) 是恒等式，但 ratio_avoid 不是常数。",
        "honest_limits": ["同一批数据，无样本外证据",
                          "分层口径的 spread 与阶段 L 报的全池 avoid 不是同一个量，不可混用"],
    }
    sp = os.path.join(OUT_DIR, "summary.json")
    with open(sp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")

    files = ["monthly.csv", "summary.json"]
    man = {"experiment": "phasem-surge-ratio-v1", "prereg_sha256": PREREG_SHA256,
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
