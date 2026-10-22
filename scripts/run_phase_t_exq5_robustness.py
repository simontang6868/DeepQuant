#!/usr/bin/env python
"""阶段 T：exQ5 现代段（2016-2026）成本后 alpha 的稳健性解剖。

预登记 `docs/PHASE_T_EXQ5_ROBUSTNESS_PREREG_2026-10-22.md`，冻结 `ef29a743997b…`。

三个子检验（判据全部跑前写死在预登记里）：

  T1a 主检验  剔除 6 个**先验已知**的市场极端月后，现代段 net_S_3000w 是否仍显著
  T1b 副检验  按 |alpha| 事后剔除 top-k（k=1/3/6）的压力读数 —— 只报数不判定
  T1c 副检验  NW 滞后 L=0/1/3/6 与 iid 的 t 敏感性
  T2  副检验  换 6 个替代分界点（2013~2019），≥4/6 后段显著 → 通过
  T3  副检验  alpha 按 ADV 五分位的精确可加分解；contribution_1/contribution_5 ≤ 3 → 通过

工程约定（踩过的坑）：
- 所有落盘文件一律显式 LF 行尾（Windows 文本模式默认 CRLF，会让 git 提交
  归一后的树与 API 上传的工作区字节不一致 —— 2026-10-22 阶段 S 实测过）；
- `newey_west()` 入参必须是小数（pp/100），读 `mean_pp`/`se_nw_pp`；
- 面板重建必须通过 G3 复现闸门（复现阶段 I 的 V0：+0.230703 / t=+6.357163）。

用法：
    python scripts/run_phase_t_exq5_robustness.py
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

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_c import (  # noqa: E402
    END_HOLD, END_SIGNAL, MIN_LISTED_DAYS, NW_LAG, START, Panels,
    build_mask, load_close,
)
from run_phase_i_exq5 import (  # noqa: E402
    EXPECT as I_EXPECT, MIN_VALID, N_GROUP, Q_HIGH, REPRO_TOL as I_TOL,
    TOP_N, WIN, max5_partition, split_equal_indep,
)
from run_phase_j_impact import load_dollar_volume  # noqa: E402
from run_phase_s_exq5_modern import nw_stats_pp, power_at_observed  # noqa: E402
from stage3_sigma_d import month_end_positions  # noqa: E402

# ==================== 预登记常量 ====================
PREREG_SHA256 = "ef29a743997b5371bf7f1bad5aa5880563867591859e48dea787407122139f4e"
PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phaset_out", PREREG_SHA12)
PREREG_MD = "docs/PHASE_T_EXQ5_ROBUSTNESS_PREREG_2026-10-22.md"

S_CSV = os.path.join("phases_out", "f098a59fb1b5", "monthly.csv")
O_CSV = os.path.join("phaseo_out", "ac7aa2830ab0", "monthly.csv")
J_CSV = os.path.join("phasej_out", "f76de8a277f6", "monthly.csv")
INPUT_SHA = {
    S_CSV: "3333efe96ac0db29d1568057678eda5187d0c03b42c1e508f5eb8736bd24da91",
    O_CSV: "768e49a9462cd7c00f3aacfcdb1e87bbe37bb75b348cd6fd85809e12493a4152",
    J_CSV: "6f1961bdc2709c98b637ef545a8a90e2f76b3e2f1983974c827e35dffb859235",
}

# —— T1a：先验已知的市场极端月（不看结果，写死于预登记 §2）——
APRIORI_EXTREME = ("2016-01", "2016-02", "2020-02", "2020-03", "2024-01", "2024-02")
EXTREME_EVENTS = {
    "2016-01": "熔断机制首次触发、四次熔断两次休市",
    "2016-02": "熔断机制废止前后极端波动",
    "2020-02": "新冠疫情暴跌",
    "2020-03": "全球市场恐慌与反弹",
    "2024-01": "微盘股流动性危机（量化产品集中赎回）",
    "2024-02": "微盘股流动性危机延续",
}
# —— T1b ——
TOPK = (1, 3, 6)
# —— T1c ——
LAGS = (0, 1, 3, 6)
# —— T2 ——
ALT_SPLITS = ("2013-01-01", "2014-01-01", "2015-01-01",
              "2017-01-01", "2018-01-01", "2019-01-01")
SPLIT_PASS_MIN = 4
# —— T3 ——
ADV_WIN = WIN                  # 与 MAX5 同窗（21 个交易日）
ADV_MIN_VALID = MIN_VALID      # 同样 ≥19 个有效日
N_ADV_GROUP = 5
ADV_RATIO_MAX = 3.0
IDENTITY_TOL = 1e-12
T_CRIT = 1.96
Z_POWER80 = 2.8016             # 双侧 5%、功效 80%
MODERN_LO = "2016-01-01"


# ==================== 工具 ====================
def sha256_file(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def verify() -> None:
    if not os.path.exists(PREREG_MD):
        raise SystemExit(f"预登记缺失：{PREREG_MD}")
    h = sha256_file(PREREG_MD)
    if h != PREREG_SHA256:
        raise SystemExit(f"预登记哈希不符！\n  期望 {PREREG_SHA256}\n  实际 {h}\n"
                         f"预登记被改过 → 本实验无效，必须新预登记")
    for path, want in INPUT_SHA.items():
        if not os.path.exists(path):
            raise SystemExit(f"输入缺失：{path}")
        got = sha256_file(path)
        if got != want:
            raise SystemExit(f"输入哈希不符：{path}\n  期望 {want}\n  实际 {got}")
    n_adj = len([f for f in os.listdir(C.ADJ_DIR) if f.endswith(".qfq.csv")])
    n_exec = len([f for f in os.listdir(C.EXEC_DIR) if f.endswith(".exec.csv")])
    print(f"  预登记哈希校验 ✓   qfq={n_adj}  exec={n_exec}")


def verdict_of(t1a_ok: bool, t2_ok: bool, t3_ok: bool) -> str:
    """组合判定（预登记 §5 写死）。

    变异易发点：把两个副检验的失败"与"成主检验失败、或反之。这里逐条对应
    预登记原文，改判据必须先改预登记并重新冻结哈希。
    """
    if not t1a_ok or (not t2_ok and not t3_ok):
        return "T_FRAGILE"
    if t2_ok and t3_ok:
        return "T_ROBUST_ALL"
    return "T_ROBUST_WITH_CAVEAT"


def nw_with_lag(vals_pp: list[float], lag: int) -> dict:
    """指定 NW 滞后的均值检验（单位铁律：入参小数）。"""
    r = C.newey_west([v / 100.0 for v in vals_pp], lag=lag)
    return {"n": r["n"], "mean_pp": r["mean_pp"], "se_nw_pp": r["se_nw_pp"],
            "t_nw": r["t_nw"], "p_nw": r["p"]}


def seg_subset(dates: list[str], vals: list[float], lo: str,
               drop: tuple[str, ...] = ()) -> tuple[list[str], list[float]]:
    """取 `d >= lo` 的子序列，并可额外剔除指定月份（T1a 用）。"""
    ds, vs = [], []
    for d, v in zip(dates, vals):
        if d < lo:
            continue
        if d[:7] in drop:
            continue
        ds.append(d)
        vs.append(v)
    return ds, vs


def load_net_s() -> tuple[list[str], list[float]]:
    df = pd.read_csv(S_CSV)
    if len(df) != 237:
        raise SystemExit(f"阶段 S 序列长度应为 237，实测 {len(df)}")
    # ⚠️ signal_date 是**月末交易日**（2007-01-31 ~ 2026-09-30），不是月初
    if df["signal_date"].min() != "2007-01-31" or df["signal_date"].max() != "2026-09-30":
        raise SystemExit(f"阶段 S 序列区间异常：{df['signal_date'].min()} ~ "
                         f"{df['signal_date'].max()}")
    return df["signal_date"].astype(str).tolist(), df["net_S_3000w"].astype(float).tolist()


# ==================== T3：面板重建 + ADV 分解 ====================
def rebuild_with_adv() -> tuple[list[dict], dict]:
    """独立重跑阶段 I 的 V0 构造，并额外带上每月的 ADV。

    ⚠️ 与 `run_phase_i_exq5.build_records()` 逐行同构；正确性由 G3 复现闸门
    （复现 +0.230703 / t=+6.357163）证明，不靠"看起来一样"。
    """
    close = load_close()
    cal = close.index
    panels = Panels(close)
    positions = month_end_positions(cal, START, END_HOLD)
    mask = build_mask(close, MIN_LISTED_DAYS)
    ret = close.pct_change(fill_method=None)
    cols = np.array(close.columns)
    arr_r = ret.to_numpy()
    ffill = panels.ffill

    dv = load_dollar_volume().reindex(index=close.index, columns=close.columns)
    arr_dv = dv.to_numpy(dtype=float)

    recs: list[dict] = []
    n_skip: dict[str, int] = {}
    split_mismatch = 0
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sdate = str(cal[pos].date())
        if sdate > END_SIGNAL or pos - WIN + 1 < 0 or pos + 1 >= len(cal):
            n_skip["outside_window"] = n_skip.get("outside_window", 0) + 1
            continue
        ok = mask.iloc[pos].to_numpy()
        if ok.sum() < 100:
            n_skip["pool_too_small"] = n_skip.get("pool_too_small", 0) + 1
            continue
        idx = np.where(ok)[0]
        w = arr_r[pos - WIN + 1: pos + 1][:, idx]
        good = np.isfinite(w).sum(axis=0) >= MIN_VALID
        if good.sum() < 100:
            n_skip["window_too_sparse"] = n_skip.get("window_too_sparse", 0) + 1
            continue
        sub = w[:, good]
        max5 = max5_partition(np.where(np.isfinite(sub), sub, -np.inf))
        codes = cols[idx][good]
        m = np.isfinite(max5)
        if m.sum() < 100:
            n_skip["max5_all_nan"] = n_skip.get("max5_all_nan", 0) + 1
            continue
        max5, codes = max5[m], codes[m]

        grp = split_equal_indep(max5, N_GROUP)
        # 结构自检：按 MAX5 排序后组号必须非递减（分组必须是有序分块）
        order = np.argsort(max5, kind="stable")
        if np.any(np.diff(grp[order]) < 0):
            split_mismatch += 1

        # —— ADV：与 MAX5 同窗、同有效日门槛，取自 EXEC 真实成交额 ——
        dvw = arr_dv[pos - WIN + 1: pos + 1][:, idx][:, good][:, m]
        adv_ok = np.isfinite(dvw).sum(axis=0) >= ADV_MIN_VALID
        with np.errstate(invalid="ignore"):
            adv = np.nanmean(np.where(np.isfinite(dvw), dvw, np.nan), axis=0)

        p0 = ffill.iloc[pos][codes].to_numpy(dtype=float)
        p1 = ffill.iloc[nxt][codes].to_numpy(dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            fwd = p1 / p0 - 1.0
        fm = np.isfinite(fwd)
        if fm.sum() < 100:
            n_skip["fwd_all_nan"] = n_skip.get("fwd_all_nan", 0) + 1
            continue
        max5, codes, fwd, grp = max5[fm], codes[fm], fwd[fm], grp[fm]
        adv, adv_ok = adv[fm], adv_ok[fm]

        is_q5 = grp == Q_HIGH
        recs.append({
            "signal_date": sdate,
            "codes": [str(c) for c in codes],
            "fwd": fwd.tolist(),
            "adv": adv.tolist(),
            "adv_ok": adv_ok.tolist(),
            "is_q5": is_q5.tolist(),
            "n_pool": int(len(fwd)),
            "alpha": float(fwd[~is_q5].mean() - fwd.mean()),
        })
    if not recs:
        raise SystemExit("没有有效月份")
    return recs, {"n_skip": n_skip, "split_mismatch_months": split_mismatch}


def adv_quintiles(adv: np.ndarray) -> np.ndarray:
    """按 ADV 等频分 5 组（0=最低 … 4=最高）。输入必须全为有限值。"""
    return split_equal_indep(adv, N_ADV_GROUP)


def decompose(recs: list[dict]) -> tuple[list[dict], dict]:
    """逐月 ADV 五分位可加分解 + 独立实现交叉校验。"""
    rows = []
    disagree_total = 0
    coverage = []
    for r in recs:
        fwd = np.asarray(r["fwd"], dtype=float)
        adv = np.asarray(r["adv"], dtype=float)
        adv_ok = np.asarray(r["adv_ok"], dtype=bool)
        is_q5 = np.asarray(r["is_q5"], dtype=bool)
        keep = ~is_q5

        v = adv_ok                      # T3 的有效池（有 ADV 的股票）
        coverage.append(float(v.sum()) / float(len(v)))
        if v.sum() < N_ADV_GROUP * 20:
            rows.append({"signal_date": r["signal_date"], "skip": "pool_too_small"})
            continue

        pv = np.where(v)[0]
        kv = pv[keep[pv]]
        n_k, n_p = len(kv), len(pv)
        if n_k < 20:
            rows.append({"signal_date": r["signal_date"], "skip": "keep_too_small"})
            continue

        a = adv[pv]
        g = adv_quintiles(a)
        # 独立交叉校验：用分位数阈值法再分一次，比较不一致的股票数
        qs = np.quantile(a, [0.2, 0.4, 0.6, 0.8])
        g2 = np.searchsorted(qs, a, side="right")
        disagree_total += int((g != g2).sum())

        contrib = np.zeros(N_ADV_GROUP)
        within = np.zeros(N_ADV_GROUP)   # 组内避免效应
        for gi in range(N_ADV_GROUP):
            in_p = g == gi
            in_k = in_p & keep[pv]
            n_pg = int(in_p.sum())
            contrib[gi] = (fwd[pv][in_k].sum() / n_k) - (fwd[pv][in_p].sum() / n_p)
            within[gi] = (fwd[pv][in_k].mean() if in_k.any() else float("nan")) \
                - (fwd[pv][in_p].mean() if n_pg else float("nan"))

        alpha_t3 = float(fwd[kv].mean() - fwd[pv].mean())
        rows.append({
            "signal_date": r["signal_date"],
            "n_pool_v": n_p, "n_keep_v": n_k,
            "alpha_t3": alpha_t3,
            "alpha_full": r["alpha"],
            "contrib": contrib.tolist(),
            "within": within.tolist(),
            "adv_median": float(np.median(a)),
        })
    return rows, {"independent_split_disagreements": disagree_total,
                  "adv_coverage_mean": statistics.mean(coverage)}


# ==================== 主流程 ====================
def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print(f"阶段 T：exQ5 现代段稳健性解剖   预登记 {PREREG_SHA12}")
    print("=" * 78)
    verify()

    dates, net_s = load_net_s()
    n_all = len(net_s)
    n_mod = sum(1 for d in dates if d >= MODERN_LO)
    print(f"\n主序列 net_S_3000w：n={n_all}（现代段 {n_mod}）  "
          f"{dates[0]} → {dates[-1]}")

    # ---------- G5：统计实现复现 ----------
    o = pd.read_csv(O_CSV)
    o_dates = o["signal_date"].astype(str).tolist()
    o_base = o["net_BASE_3000w"].astype(float).tolist()
    if o_dates != dates:
        raise SystemExit("阶段 O 与阶段 S 的 signal_date 不对齐")
    g5 = nw_with_lag(o_base, NW_LAG)
    g5_ok = abs(g5["t_nw"] - 4.0386) <= 1e-3
    print(f"G5 统计实现（net_BASE_3000w 全样本 NW t）= {g5['t_nw']:+.6f} "
          f"（期望 4.0386）{'✓' if g5_ok else '✗'}")

    # ---------- 基准：现代段主检验（复述阶段 S） ----------
    md, mv = seg_subset(dates, net_s, MODERN_LO)
    base = nw_stats_pp(mv)
    print(f"\n① 基准（阶段 S 主检验复述）：n={base['n']}  "
          f"{base['mean_pp']:+.6f}pp  t={base['t_nw']:+.4f}  "
          f"MDE={base['mde_pp']:.6f}  功效={base['power_observed']:.3f}")

    # ---------- T1a：先验极端期剔除（主检验） ----------
    print(f"\n② T1a 主检验：剔除先验极端月 {list(APRIORI_EXTREME)}")
    ad, av = seg_subset(dates, net_s, MODERN_LO, drop=APRIORI_EXTREME)
    t1a = nw_stats_pp(av)
    t1a_ok = t1a["t_nw"] >= T_CRIT and t1a["mean_pp"] > 0
    print(f"   n={t1a['n']}  {t1a['mean_pp']:+.6f}pp  se_nw={t1a['se_nw_pp']:.6f}  "
          f"t={t1a['t_nw']:+.4f}  MDE={t1a['mde_pp']:.6f}  "
          f"功效={t1a['power_observed']:.3f}")
    print(f"   → {'通过 ✓' if t1a_ok else '不通过 ✗'}")

    # ---------- T1b：事后影响力压力读数 ----------
    print("\n③ T1b 副检验（事后选择，只报数不判定）：按 |alpha| 剔除 top-k")
    t1b = {}
    order_idx = sorted(range(len(mv)), key=lambda i: -abs(mv[i]))
    for k in TOPK:
        drop_idx = set(order_idx[:k])
        sub = [v for i, v in enumerate(mv) if i not in drop_idx]
        st = nw_stats_pp(sub)
        worst = [md[i] for i in order_idx[:k]]
        t1b[str(k)] = {"n": st["n"], "mean_pp": st["mean_pp"], "t_nw": st["t_nw"],
                       "mde_pp": st["mde_pp"], "dropped_months": worst}
        print(f"   k={k}（剔除 {' '.join(worst)}）: n={st['n']}  "
              f"{st['mean_pp']:+.6f}pp  t={st['t_nw']:+.4f}  MDE={st['mde_pp']:.6f}")

    # ---------- T1c：NW 滞后敏感性 ----------
    print("\n④ T1c 副检验：NW 滞后与 iid 的 t 敏感性（现代段）")
    t1c = {}
    for lag in LAGS:
        st = nw_with_lag(mv, lag)
        t1c[f"L{lag}"] = {"t_nw": st["t_nw"], "se_nw_pp": st["se_nw_pp"]}
        print(f"   L={lag}: t={st['t_nw']:+.4f}  se={st['se_nw_pp']:.6f}")
    print(f"   iid : t={base['t_iid']:+.4f}  se={base['se_iid_pp']:.6f}")
    t1c["iid"] = {"t_nw": base["t_iid"], "se_nw_pp": base["se_iid_pp"]}

    # ---------- T2：分界点敏感性 ----------
    print(f"\n⑤ T2 副检验：替代分界点（判据 ≥{SPLIT_PASS_MIN}/6 后段显著）")
    t2 = {}
    n_pass = 0
    for b in ALT_SPLITS:
        bd, bv = seg_subset(dates, net_s, b)
        st = nw_stats_pp(bv)
        ok = st["t_nw"] >= T_CRIT and st["mean_pp"] > 0
        n_pass += int(ok)
        t2[b] = {"n": st["n"], "mean_pp": st["mean_pp"], "t_nw": st["t_nw"],
                 "mde_pp": st["mde_pp"], "power_observed": st["power_observed"],
                 "pass": ok}
        print(f"   {b} → 2026-09: n={st['n']:>3}  {st['mean_pp']:+.6f}pp  "
              f"t={st['t_nw']:+.4f}  MDE={st['mde_pp']:.6f}  "
              f"{'✓' if ok else '✗'}")
    t2_ok = n_pass >= SPLIT_PASS_MIN
    print(f"   → {n_pass}/6 通过 {'✓' if t2_ok else '✗'}")

    # ---------- T3：ADV 分解（含 G3 复现闸门） ----------
    print("\n⑥ T3 副检验：面板重建 + ADV 五分位分解")
    recs, meta = rebuild_with_adv()
    print(f"   月份 {len(recs)}  跳过 {meta['n_skip'] or '无'}  "
          f"分组结构异常 {meta['split_mismatch_months']}")

    alphas_pp = [r["alpha"] * 100.0 for r in recs]
    # ⚠️ 单位铁律（又踩一次）：`recs[*]["alpha"]` 是**小数**（日收益口径），
    #    而 `nw_with_lag()` 的契约是 pp。直接传会把均值压小 100 倍，
    #    而 t 尺度无关所以仍然逐位正确 —— 首跑正是这么栽的（mean 差 100×、
    #    t=+6.357163 却完全吻合）。单测 test_g3_units_conversion 钉住。
    g3 = nw_with_lag(alphas_pp, NW_LAG)
    g3_ok = (len(recs) == I_EXPECT["n_months"]
             and abs(g3["mean_pp"] - I_EXPECT["alpha_mean_pp"]) <= I_TOL["alpha_mean_pp"]
             and abs(g3["t_nw"] - I_EXPECT["t_nw"]) <= I_TOL["t_nw"])
    print(f"   G3 复现阶段 I V0：n={g3['n']}（期望 {I_EXPECT['n_months']}）  "
          f"均值={g3['mean_pp']:+.6f}pp（期望 {I_EXPECT['alpha_mean_pp']:+.6f}）  "
          f"t={g3['t_nw']:+.6f}（期望 {I_EXPECT['t_nw']:+.6f}）  "
          f"{'✓' if g3_ok else '✗'}")
    if not g3_ok:
        print("   ✗ G3 不过 —— T3 作废，不出分解结论")
        return 2

    rows, dmeta = decompose(recs)
    ok_rows = [r for r in rows if "skip" not in r]
    print(f"   ADV 覆盖率（逐月均值）{dmeta['adv_coverage_mean']:.4f}  "
          f"独立分组实现不一致股票数 {dmeta['independent_split_disagreements']}")
    print(f"   分解月份 {len(ok_rows)}/{len(rows)}")

    # G4：恒等式校验
    worst_id = 0.0
    for r in ok_rows:
        worst_id = max(worst_id, abs(sum(r["contrib"]) - r["alpha_t3"]))
    g4_ok = worst_id < IDENTITY_TOL
    print(f"   G4 分解恒等式：最大偏差 {worst_id:.3e}（容差 {IDENTITY_TOL:.0e}）"
          f"{'✓' if g4_ok else '✗'}")

    t3 = {}
    for gi in range(N_ADV_GROUP):
        ser = [r["contrib"][gi] for r in ok_rows]
        st = nw_stats_pp(ser)
        t3[f"adv_g{gi+1}"] = {"n": st["n"], "mean_pp": st["mean_pp"],
                              "se_nw_pp": st["se_nw_pp"], "t_nw": st["t_nw"],
                              "mde_pp": st["mde_pp"]}
    within = {}
    for gi in range(N_ADV_GROUP):
        ser = [r["within"][gi] for r in ok_rows]
        within[f"adv_g{gi+1}"] = nw_stats_pp(ser)["mean_pp"]
    c1 = t3["adv_g1"]["mean_pp"]
    c5 = t3["adv_g5"]["mean_pp"]
    ratio = (c1 / c5) if c5 != 0 else float("nan")
    ratio_defined = c5 > 0
    t3_ok = bool(ratio_defined and ratio <= ADV_RATIO_MAX)
    print(f"\n   月均贡献（g1=最低 ADV → g5=最高）：")
    for gi in range(N_ADV_GROUP):
        d = t3[f"adv_g{gi+1}"]
        print(f"     g{gi+1}: {d['mean_pp']:+.6f}pp  t={d['t_nw']:+.4f}  "
              f"组内避免效应 {within[f'adv_g{gi+1}']:+.6f}pp")
    print(f"   contribution_1/contribution_5 = {ratio:.3f}"
          f"{'' if ratio_defined else '（分母非正 → 比值不可比）'}")
    print(f"   → {'通过 ✓' if t3_ok else '不通过 / 不可判定 ✗'}（阈值 {ADV_RATIO_MAX}）")

    # ---------- 描述性：分年 alpha 与滚动窗口 ----------
    yearly = {}
    for d, v in zip(md, mv):
        yearly.setdefault(d[:4], []).append(v)
    year_tbl = {y: {"n": len(v), "mean_pp": statistics.fmean(v)}
                for y, v in sorted(yearly.items())}
    roll = []
    W = 60
    for i in range(W - 1, len(mv)):
        sub = mv[i - W + 1: i + 1]
        st = nw_stats_pp(sub)
        roll.append({"end": md[i], "t_nw": st["t_nw"], "mean_pp": st["mean_pp"]})
    roll_sig = sum(1 for r in roll if r["t_nw"] >= T_CRIT)
    print(f"\n   描述性：现代段分年 alpha（n / 月均 pp）")
    for y, d in year_tbl.items():
        print(f"     {y}: {d['n']:>2} 月  {d['mean_pp']:+.4f}pp")
    print(f"   描述性：滚动 {W} 月窗口 {len(roll)} 个，其中 t≥1.96 的 "
          f"{roll_sig} 个（{roll_sig/len(roll)*100:.0f}%）")

    # ---------- 组合判定 ----------
    verdict = verdict_of(t1a_ok, t2_ok, t3_ok)
    print(f"\n⑦ 组合判定：{verdict}"
          f"（T1a {'✓' if t1a_ok else '✗'}  T2 {'✓' if t2_ok else '✗'}  "
          f"T3 {'✓' if t3_ok else '✗'}）")

    # ---------- 落盘（显式 LF） ----------
    os.makedirs(OUT_DIR, exist_ok=True)
    cols = (["signal_date", "segment"] + ["net_S_3000w"])
    with open(os.path.join(OUT_DIR, "monthly.csv"), "w", newline="\n",
              encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for d, v in zip(dates, net_s):
            w.writerow([d, "modern" if d >= MODERN_LO else "early", round(v, 10)])

    summary = {
        "prereg_sha256": PREREG_SHA256,
        "verdict": verdict,
        "baseline_modern": base,
        "T1a": {"drop_months": list(APRIORI_EXTREME), "events": EXTREME_EVENTS,
                "result": t1a, "pass": t1a_ok},
        "T1b_topk": t1b,
        "T1c_lag": t1c,
        "T2_splits": t2, "T2_pass_count": n_pass, "T2_pass": t2_ok,
        "T3": {"contrib": t3, "within_avoid_pp": within,
               "ratio_c1_over_c5": ratio, "ratio_defined": ratio_defined,
               "pass": t3_ok, "adv_coverage_mean": dmeta["adv_coverage_mean"],
               "independent_split_disagreements": dmeta["independent_split_disagreements"],
               "identity_max_abs_err": worst_id, "identity_ok": g4_ok},
        "gates": {"G3_reproduce_phaseI_V0": {"ok": g3_ok, "got": g3,
                                             "expected": I_EXPECT},
                  "G4_identity": {"ok": g4_ok, "max_abs_err": worst_id},
                  "G5_stats_reproduce_phaseO": {"ok": g5_ok, "got_t": g5["t_nw"],
                                                "expected_t": 4.0386}},
        "descriptive": {"per_year": year_tbl, "rolling60": {"n": len(roll),
                                                            "n_sig": roll_sig,
                                                            "detail": roll}},
        "honest_limits": [
            "样本内：全部 237 个月既用于构造也用于检验，无真正样本外",
            "T1b 是事后选择，只作压力读数，不构成证据",
            "T3 分解的是 V0（无交易约束）口径；可成交性与资金约束由阶段 I/J/O 处理",
            "冲击成本仍是模型估计（Amihud 与 √ 模型相差 25 倍），无盘中数据",
            "最低佣金 5 元只在 CONS 口径体现，主序列 BASE 未含",
        ],
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", newline="\n",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    man = {"prereg_sha256": PREREG_SHA256, "inputs": INPUT_SHA,
           "outputs": {"monthly_csv_sha256": sha256_file(os.path.join(OUT_DIR, "monthly.csv")),
                       "summary_json_sha256": sha256_file(os.path.join(OUT_DIR, "summary.json"))},
           "constants": {"APRIORI_EXTREME": list(APRIORI_EXTREME), "TOPK": list(TOPK),
                         "LAGS": list(LAGS), "ALT_SPLITS": list(ALT_SPLITS),
                         "SPLIT_PASS_MIN": SPLIT_PASS_MIN, "N_ADV_GROUP": N_ADV_GROUP,
                         "ADV_RATIO_MAX": ADV_RATIO_MAX, "IDENTITY_TOL": IDENTITY_TOL,
                         "NW_LAG": NW_LAG, "T_CRIT": T_CRIT, "Z_POWER80": Z_POWER80,
                         "ADV_WIN": ADV_WIN, "ADV_MIN_VALID": ADV_MIN_VALID},
           "panel": {"months": len(recs), "n_skip": meta["n_skip"],
                     "split_mismatch_months": meta["split_mismatch_months"]}}
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", newline="\n",
              encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)

    print(f"\n完成 {time.time()-t0:.0f}s → {OUT_DIR}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
