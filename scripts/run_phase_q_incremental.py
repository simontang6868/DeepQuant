#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 Q：增量信息检验（确认性）

预登记 docs/PHASE_Q_INCREMENTAL_PREREG_2026-10-21.md
冻结哈希 ae7fc45acab06e991e997b7a1fac836ce6f17ebdd736935e612ccfb4e510fb1c

问题：在控制波动率与彼此之后，MAX5 / SURGE 是否仍有横截面预测力。
**低相关 ≠ 独立 ≠ 有增量信息** —— 本阶段只测最后一层。

方法：Fama-MacBeth 横截面回归（逐月 OLS，斜率序列用 NW(3) 判显著）。

⚠️ 阶段 N 的 REV 控制变量是**单日**收益（等于没控制）；本阶段改用
   21 日形成期收益 REV21。两种都跑：阶段 N 的定义作**复现闸门**。
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
    END_HOLD, END_SIGNAL, MIN_LISTED_DAYS, NW_LAG, START, build_mask, load_close,
    stream_bars,
)
from run_phase_i_exq5 import long_tradable  # noqa: E402
from run_phase_f_max5 import MIN_VALID, TOP_N, WIN  # noqa: E402
from run_phase_j_impact import load_dollar_volume  # noqa: E402
from run_phase_l_surge import N_GROUP, split_equal_indep, surge_of  # noqa: E402
from stage3_sigma_d import month_end_positions  # noqa: E402

PREREG_SHA256 = "ae7fc45acab06e991e997b7a1fac836ce6f17ebdd736935e612ccfb4e510fb1c"
PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phaseq_out", PREREG_SHA12)

T_CRIT = 1.96
VARS = ("MAX5", "SURGE", "VOL", "REV")

# 复现闸门目标（阶段 N 探索性结果，REV 定义 = 单日收益）
GATE_EXPECT = {
    "MAX5": (-0.209127, -1.2871),
    "SURGE": (-0.232875, -2.8327),
    "VOL": (-0.114457, -0.7012),
    "REV": (0.029855, 0.3485),
}
GATE_TOL_MEAN, GATE_TOL_T = 1e-4, 1e-3


def verify() -> None:
    p = "docs/PHASE_Q_INCREMENTAL_PREREG_2026-10-21.md"
    if not os.path.exists(p):
        raise SystemExit(f"预登记缺失：{p}")
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    if h != PREREG_SHA256:
        raise SystemExit(f"预登记哈希不符！\n  期望 {PREREG_SHA256}\n  实际 {h}")
    print(f"[Q] 预登记校验通过 sha256={h[:12]}")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ============================================================ 面板
def signal_panel() -> list[dict]:
    """逐月横截面：max5 / surge / vol / rev1 / rev21 / fwd / entry。

    与阶段 N 的 `signal_panel` 同构，只多算 rev21 与 entry。
    """
    close = load_close()
    cal = close.index
    mask = build_mask(close, MIN_LISTED_DAYS)
    positions = month_end_positions(cal, START, END_HOLD)
    ret = close.pct_change(fill_method=None).to_numpy(dtype="float32", copy=True)
    dv = load_dollar_volume().reindex(index=close.index,
                                      columns=close.columns).to_numpy()
    px = close.ffill().to_numpy()
    ffill = px
    cols = list(close.columns)             # S3 的可成交掩码需要代码名
    del close

    out = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sd = str(cal[pos].date())
        if sd > END_SIGNAL or pos - 2 * WIN + 1 < 0 or pos + 1 >= len(cal):
            continue
        ok = mask.iloc[pos].to_numpy()
        if ok.sum() < 100:
            continue
        idx = np.where(ok)[0]
        cur = dv[pos - WIN + 1: pos + 1][:, idx]
        pri = dv[pos - 2 * WIN + 1: pos - WIN + 1][:, idx]
        surge, elig = surge_of(cur, pri)
        e = np.where(elig)[0]
        if e.size < 200:
            continue
        idx, surge = idx[e], surge[e]
        w = ret[pos - WIN + 1: pos + 1][:, idx]
        vc = np.isfinite(w)
        good = vc.sum(axis=0) >= MIN_VALID
        if good.sum() < 200:
            continue
        idx, surge, w = idx[good], surge[good], w[:, good]
        m5 = np.sort(np.where(np.isfinite(w), w, -np.inf), axis=0)[-TOP_N:, :].mean(axis=0)
        vol = np.nanstd(np.where(np.isfinite(w), w, np.nan), axis=0, ddof=1)
        rev1 = ret[pos, idx]                                  # 阶段 N 的定义：**单日**
        with np.errstate(invalid="ignore", divide="ignore"):
            rev21 = (px[pos - 1, idx] / px[pos - WIN, idx] - 1.0)   # 21 日形成期收益
        fwd = ffill[nxt][idx] / ffill[pos][idx] - 1.0
        fin = (np.isfinite(m5) & np.isfinite(vol) & np.isfinite(fwd)
               & np.isfinite(rev1) & np.isfinite(rev21))
        if fin.sum() < 200:
            continue
        out.append({"signal_date": sd,
                    "entry": str(cal[pos + 1].date()),
                    "codes": [cols[i] for i in idx[fin]],
                    "m5": m5[fin], "surge": surge[fin], "vol": vol[fin],
                    "rev1": rev1[fin], "rev21": rev21[fin], "fwd": fwd[fin]})
    return out


# ============================================================ 统计
def zr(a: np.ndarray) -> np.ndarray:
    """横截面**秩** z-score（抗异常值）。"""
    r = np.argsort(np.argsort(a)).astype(float)
    return (r - r.mean()) / (r.std() + 1e-12)


def fm(slopes: list[float], lag: int = NW_LAG) -> dict:
    """斜率序列的均值检验：iid se 与 NW se 都报（本项目标准误口径不统一）。

    ⚠️ 必须**优雅**处理两种退化输入，不能抛异常后无声无息：
      - 非有限值（NaN/inf）：标 `has_nonfinite=True` 并以有效子集计算，
        但**保留 n_months 与 n_valid 的差额**让调用方看得见；
      - 有效样本 < 2：返回 NaN 并标 `insufficient=True`。
    单测 `test_mutation_nan_in_forward_return_is_visible` 与
    `test_mutation_tiny_pool_is_skipped_not_silently_used` 钉住此行为。
    """
    n = len(slopes)
    fin = [v for v in slopes if v == v and math.isfinite(v)]
    has_bad = len(fin) != n
    if len(fin) < 2:
        return {"n_months": n, "n_valid": len(fin), "has_nonfinite": has_bad,
                "insufficient": True, "coef_pp_per_sd": float("nan"),
                "t_iid": float("nan"), "t_nw": float("nan"),
                "p_nw": float("nan")}
    m = statistics.fmean(fin)
    se_iid = statistics.stdev(fin) / math.sqrt(len(fin))
    nw = C.newey_west(fin, lag)
    return {"n_months": n, "n_valid": len(fin), "has_nonfinite": has_bad,
            "insufficient": False,
            "coef_pp_per_sd": round(m * 100, 6),
            "t_iid": round(m / se_iid, 4), "t_nw": round(nw["t_nw"], 4),
            "p_nw": round(nw["p"], 6)}


def fm_multi(recs: list[dict], rev_key: str,
             keep_mask: list[np.ndarray] | None = None) -> dict:
    """多元 FM：fwd ~ z(MAX5)+z(SURGE)+z(VOL)+z(REV)。"""
    slopes = {k: [] for k in VARS}
    n_used = 0
    for j, r in enumerate(recs):
        m = (np.ones(len(r["fwd"]), dtype=bool) if keep_mask is None else keep_mask[j])
        if m.sum() < 100:
            continue
        X = np.column_stack([np.ones(int(m.sum())), zr(r["m5"][m]), zr(r["surge"][m]),
                             zr(r["vol"][m]), zr(r[rev_key][m])])
        y = r["fwd"][m]
        try:
            b = np.linalg.lstsq(X, y, rcond=None)[0]
        except np.linalg.LinAlgError:
            continue
        for k, name in zip(b[1:], VARS):
            slopes[name].append(float(k))
        n_used += 1
    return {k: fm(v) for k, v in slopes.items()}, n_used


def fm_uni(recs: list[dict], rev_key: str) -> dict:
    """单变量 FM：每个信号各自单独回归（保留率的分母）。"""
    out = {}
    key_map = {"MAX5": "m5", "SURGE": "surge", "VOL": "vol", "REV": rev_key}
    for name in VARS:
        sl = []
        for r in recs:
            X = np.column_stack([np.ones(len(r["fwd"])), zr(r[key_map[name]])])
            try:
                b = np.linalg.lstsq(X, r["fwd"], rcond=None)[0]
            except np.linalg.LinAlgError:
                continue
            sl.append(float(b[1]))
        out[name] = fm(sl)
    return out


def fm_within_vol_layers(recs: list[dict], rev_key: str) -> dict:
    """S2：波动率 5 层内各自回归，按标的数加权平均（非参数控制 VOL）。"""
    acc = {k: [] for k in VARS}
    wts = []
    for r in recs:
        g = split_equal_indep(r["vol"], N_GROUP)
        for L in range(N_GROUP):
            sel = g == L
            if sel.sum() < 100:
                continue
            X = np.column_stack([np.ones(int(sel.sum())), zr(r["m5"][sel]),
                                 zr(r["surge"][sel]), zr(r["rev21"][sel])])
            try:
                b = np.linalg.lstsq(X, r["fwd"][sel], rcond=None)[0]
            except np.linalg.LinAlgError:
                continue
            # 层内不控制 VOL（VOL 已被层吸收）→ 只有 3 个斜率
            for k, v in zip(("MAX5", "SURGE", "REV"), b[1:]):
                acc[k].append(float(v))
            wts.append(int(sel.sum()))
    out = {}
    for k in ("MAX5", "SURGE", "REV"):
        out[k] = fm(acc[k]) if acc[k] else {"n_months": 0}
    out["_note"] = ("层内不控制 VOL（VOL 由层吸收），故只有 3 个斜率；"
                    f"共 {len(wts)} 个层-月单元")
    return out


def judge(coef: float, t: float) -> str:
    if not (t == t) or abs(t) < T_CRIT:
        return "INCREMENTAL_ABSORBED"
    return ("INCREMENTAL_CONFIRMED" if coef < 0
            else "INCREMENTAL_DETECTED_WRONG_SIGN")


# ============================================================ 可成交口径
def tradable_masks(recs: list[dict]) -> list[np.ndarray]:
    """S3：用阶段 I 的 V1 建仓日约束给每个月打掩码。

    ⚠️ 第一版想拿阶段 I 的 `build_records` 按月对齐取 codes，结果 **0/237 月对齐成功**——
    两个面板的标的集不同（本面板额外要求 SURGE 可算）。
    正确做法：面板自己存 `codes`，不依赖外部对齐。
    """
    need: dict[str, set[str]] = defaultdict(set)
    for r in recs:
        for c in r["codes"]:
            need[c].add(r["entry"])
    print(f"   需扫 EXEC {len(need)} 只 · {sum(len(v) for v in need.values())} 点 …")
    bars, ex_al, span = stream_bars(need)
    masks = []
    for r in recs:
        keep = np.ones(len(r["fwd"]), dtype=bool)
        for i, c in enumerate(r["codes"]):
            f = C.flags_fast(bars.get(c, {}).get(r["entry"]), c, r["entry"],
                             ex_al.get(c, set()))
            reason = C._reason_of(f, c, r["entry"], span)
            ok, _why = long_tradable(f, reason)
            keep[i] = ok
        masks.append(keep)
    dropped = sum(int((~m).sum()) for m in masks)
    total = sum(len(m) for m in masks)
    print(f"   不可买入剔除 {dropped} 只次（占 {dropped/max(total,1)*100:.2f}%）")
    return masks


def main() -> int:
    t_start = time.time()
    verify()
    os.makedirs(OUT_DIR, exist_ok=True)

    print()
    print("=" * 78)
    print("① 面板构造")
    print("=" * 78)
    recs = signal_panel()
    print(f"   有效月份 {len(recs)}；池中位 "
          f"{int(statistics.median([len(r['fwd']) for r in recs]))} 只")

    # 复现闸门：用阶段 N 的定义（REV = 单日收益）
    print()
    print("② 复现闸门（用阶段 N 的 REV=单日收益 定义，对照其探索性结果）")
    gate, n_gate = fm_multi(recs, "rev1")
    gate_ok = True
    for k in VARS:
        em, et = GATE_EXPECT[k]
        got_m, got_t = gate[k]["coef_pp_per_sd"], gate[k]["t_nw"]
        ok = abs(got_m - em) <= GATE_TOL_MEAN and abs(got_t - et) <= GATE_TOL_T
        gate_ok &= ok
        print(f"   {k:<6} {got_m:+.6f} vs {em:+.6f}  t {got_t:+.4f} vs {et:+.4f}  "
              f"{'✓' if ok else '✗'}")
    if not gate_ok:
        raise SystemExit("复现闸门失败 —— 与阶段 N 不一致，拒绝继续。")
    print(f"   闸门通过 ✓（n_months={gate['MAX5']['n_months']}）")

    # 主检验：REV21
    print()
    print("③ 主检验：多元 FM（REV = 21 日形成期收益，修正后的定义）")
    print("=" * 78)
    prim, n_pri = fm_multi(recs, "rev21")
    print(f"   {'变量':<7} {'系数 pp/sd':>12} {'t(iid)':>9} {'t(NW)':>9} "
          f"{'p(NW)':>9}  {'判定'}")
    verdicts = {}
    for k in VARS:
        v = prim[k]
        vd = judge(v["coef_pp_per_sd"], v["t_nw"])
        verdicts[k] = vd
        mark = "  ← 主假设" if k in ("MAX5", "SURGE") else ""
        print(f"   {k:<7} {v['coef_pp_per_sd']:>+12.6f} {v['t_iid']:>+9.3f} "
              f"{v['t_nw']:>+9.3f} {v['p_nw']:>9.4f}  {vd}{mark}")

    # S1 单变量 + S4 保留率
    print()
    print("④ S1 单变量 FM + S4 保留率（多元/单变量）")
    uni = fm_uni(recs, "rev21")
    print(f"   {'变量':<7} {'单变量 pp/sd':>14} {'t(NW)':>9} {'多元 pp/sd':>13} "
          f"{'保留率':>8}")
    retention = {}
    for k in VARS:
        u, m = uni[k], prim[k]
        r = (m["coef_pp_per_sd"] / u["coef_pp_per_sd"]
             if abs(u["coef_pp_per_sd"]) > 1e-9 else float("nan"))
        retention[k] = round(r, 4) if r == r else None
        print(f"   {k:<7} {u['coef_pp_per_sd']:>+14.6f} {u['t_nw']:>+9.3f} "
              f"{m['coef_pp_per_sd']:>+13.6f} {retention[k] if retention[k] is not None else float('nan'):>8.3f}")

    # S2 波动率层内
    print()
    print("⑤ S2 波动率层内 FM（非参数控制 VOL）")
    lay = fm_within_vol_layers(recs, "rev21")
    for k in ("MAX5", "SURGE", "REV"):
        v = lay[k]
        print(f"   {k:<7} {v['coef_pp_per_sd']:>+12.6f} pp/sd  t(NW)={v['t_nw']:+.3f}  "
              f"n={v['n_months']}")
    print(f"   {lay['_note']}")

    # S3 可成交口径
    print()
    print("⑥ S3 可成交口径（加阶段 I 的 V1 建仓日约束）")
    masks = tradable_masks(recs)
    trad, n_trad = fm_multi(recs, "rev21", masks)
    for k in VARS:
        v = trad[k]
        print(f"   {k:<7} {v['coef_pp_per_sd']:>+12.6f} pp/sd  "
              f"t(NW)={v['t_nw']:+.3f}  p={v['p_nw']:.4f}")

    # 结论
    print()
    print("=" * 78)
    print("结论")
    print("=" * 78)
    for k in ("MAX5", "SURGE"):
        v = prim[k]
        print(f"   {k:<6} 多元偏斜率 {v['coef_pp_per_sd']:+.6f} pp/sd  "
              f"t(NW)={v['t_nw']:+.3f}  → {verdicts[k]}")
        print(f"          保留率（多元/单变量）= {retention[k]}")
    print()
    print("   ⚠️ 纪律提醒：本阶段测的是**信息含量**，不是可执行性。")
    print("      显著 ≠ 可交易；全部样本内。")

    # 产物
    monthly_path = os.path.join(OUT_DIR, "monthly.csv")
    with open(monthly_path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["signal_date", "n_pool", "coef_MAX5_pp", "coef_SURGE_pp",
                    "coef_VOL_pp", "coef_REV21_pp"])
        # 逐月斜率（重算一次以便落盘）
        for r in recs:
            X = np.column_stack([np.ones(len(r["fwd"])), zr(r["m5"]), zr(r["surge"]),
                                 zr(r["vol"]), zr(r["rev21"])])
            try:
                b = np.linalg.lstsq(X, r["fwd"], rcond=None)[0]
            except np.linalg.LinAlgError:
                continue
            w.writerow([r["signal_date"], len(r["fwd"])] +
                       [f"{x*100:.8f}" for x in b[1:]])

    summary = {
        "experiment": "phaseq-incremental-information-v1",
        "prereg_sha256": PREREG_SHA256,
        "kind": "confirmatory（主检验：MAX5 / SURGE 两个并列主假设）+ exploratory（副检验）",
        "frozen": {"win": WIN, "top_n": TOP_N, "nw_lag": NW_LAG, "t_crit": T_CRIT,
                   "cross_section_transform": "rank z-score",
                   "vol_layers": N_GROUP, "n_months": len(recs)},
        "rev_definitions": {
            "REV1（阶段N，单日）": "ret[pos] —— ⚠️ 单日收益基本是噪声，等于没控制",
            "REV21（本阶段，修正）": "P[pos-1]/P[pos-WIN] - 1，与项目反转定义一致",
        },
        "reproduction_gate": {
            "target": "阶段 N 探索性 FM（REV=单日）",
            "passed": bool(gate_ok), "tolerance": {"mean": GATE_TOL_MEAN, "t": GATE_TOL_T},
            "got": {k: {"coef_pp_per_sd": gate[k]["coef_pp_per_sd"],
                        "t_nw": gate[k]["t_nw"]} for k in VARS},
            "expect": {k: {"coef_pp_per_sd": GATE_EXPECT[k][0],
                           "t_nw": GATE_EXPECT[k][1]} for k in VARS},
        },
        "main_test_REV21": {"n_months": n_pri, "coefficients": prim,
                            "verdicts": verdicts},
        "S1_univariate": uni,
        "S4_retention_ratio": retention,
        "S2_within_vol_layers": {k: v for k, v in lay.items() if k != "_note"},
        "S2_note": lay["_note"],
        "S3_tradable": {"n_months": n_trad, "coefficients": trad},
        "honest_limits": [
            "两个并列主假设不做多重检验校正（不同变量的不同命题），两个 p 值都完整报告",
            "FM 施加线性假设；S2 的层内回归部分缓解",
            "⚠️ 预期方向被阶段 N 的探索性结果污染 —— 见预登记 §4 的声明",
            "本阶段测信息含量，不是可执行性；显著 ≠ 可交易",
            "全部样本内",
        ],
        "runtime_sec": round(time.time() - t_start, 1),
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w",
              encoding="utf-8", newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    files = ["monthly.csv", "summary.json"]
    manifest = {"experiment": "phaseq-incremental-information-v1",
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
