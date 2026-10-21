#!/usr/bin/env python
"""阶段 I：exQ5（排除最高 MAX5 的 20% 后的全池）正式检验。

预登记 `docs/PHASE_I_EXQ5_PREREG_2026-10-21.md`，冻结 `8f5c35121fdb…`。

## 与阶段 H 的区别（这是本脚本存在的理由）

阶段 H 是可行性检查，它 import 了阶段 F 的 `split_equal`，两者同源；
"不衰减"是看到结果才注意到的；也没考虑建仓日买不到。

本脚本做三件事：

1. **独立实现**：分组用 `searchsorted` 在边界值上定位（阶段 F 用 `argsort` + 逐段切片），
   MAX5 用 `np.partition`（阶段 F/H 用 `np.sort`）。两者必须逐元素一致 —— 这本身是交叉验证。
2. **判据前置**：A 统计 / B 分段稳定 / C 成本后显著，三条**并列必要条件**，跑前写死在预登记里。
3. **可成交口径**：V1 剔除建仓日涨停、一字板向上、停牌、序列已终止。

**V0 是复现闸门**：必须逐位复现阶段 H（+0.230703 / t=+6.357163 / n=237，容差 1e-4），
不通过则整个实验作废。

用法：
    python scripts/run_phase_i_exq5.py
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
from stage3_sigma_d import month_end_positions  # noqa: E402

# ==================== 预登记常量（不许改，改了必须新预登记） ====================
PREREG_SHA256 = "8f5c35121fdb81398aa3a75cb780e4273dee88c2f5684b0ba8e3028d8fdd78e1"
PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phasei_out", PREREG_SHA12)

WIN = 21                  # 21 个交易日窗口
TOP_N = 5                 # 最大的 5 个日收益
MIN_VALID = 19            # 窗口内有效日收益下限
N_GROUP = 5               # 等频分 5 组
Q_HIGH = N_GROUP - 1      # Q5 = 组号 4
EXCLUDE_FRAC = 0.20       # 写死：排除 20%

SEGMENTS = (
    ("2007-01-01", "2015-12-31", "2007-2015"),
    ("2016-01-01", "2026-09-30", "2016-2026"),
)
COST_TIERS = (0.001, 0.003)
PRIMARY_COST = 0.003      # 判据 C 用 0.3%
T_CRIT = 1.96

CAPITAL_TIERS = (1.5e6, 3.0e6, 5.0e6, 1.0e7, 3.0e7)
M1_CAPITAL = 1.0e7        # 成熟度判定用 W
MAX_UNINVESTED = 0.05     # 未投资比例上限
MAX_WEIGHT_MAE_PP = 0.5   # 权重平均绝对偏差上限（pp）

LOT = 100                 # A 股整手

# 复现闸门：阶段 H 的实测值（reports/EXQ5_FEASIBILITY.json）
EXPECT = {
    "n_months": 237,
    "alpha_mean_pp": 0.230703,
    "t_nw": 6.357163,
    "seg_2007_2015_pp": 0.237627,
    "seg_2016_2026_pp": 0.224905,
}
REPRO_TOL = {
    "n_months": 0,
    "alpha_mean_pp": 1e-4,
    "t_nw": 1e-3,
    "seg_2007_2015_pp": 1e-4,
    "seg_2016_2026_pp": 1e-4,
}


# ==================== 独立实现：分组与 MAX5 ====================
def split_equal_indep(x: np.ndarray, k: int) -> np.ndarray:
    """等频分组，返回 0..k-1（按值升序，0 最小）。

    **与阶段 F 的 `split_equal` 算法不同**：这里先算右开边界数组，
    再用 `searchsorted` 一次性定位每个名次属于哪一组；
    阶段 F 是逐段切片写回。两者的**定义相同**（前 `n % k` 组各多 1 只），
    所以结果必须逐元素一致 —— 单元测试会验这一点。

    ties 的处理依赖 `argsort(kind="stable")`：并列值按原下标顺序占位。
    这与阶段 F 一致；若并列跨越组边界，两者会给同一个解。
    """
    n = x.size
    if n == 0:
        return np.zeros(0, dtype=int)
    base, rem = divmod(n, k)
    sizes = np.full(k, base, dtype=int)
    sizes[:rem] += 1
    bounds = np.cumsum(sizes)                 # 右开边界：[b0, b1, …, n)
    order = np.argsort(x, kind="stable")
    pos = np.arange(n, dtype=int)             # 排序后的名次
    grp_sorted = np.searchsorted(bounds, pos, side="right")
    grp = np.empty(n, dtype=int)
    grp[order] = grp_sorted
    return grp


def max5_partition(w: np.ndarray) -> np.ndarray:
    """窗口内最大的 `TOP_N` 个日收益的均值。

    入参 `w` 形状 `(WIN, n)`，非有限值已替换为 `-inf`。
    **用 `np.partition`**（阶段 F/H 用 `np.sort` 后取尾部），结果必须一致：
    取均值时前 5 名内部顺序无关，所以 partition 是等价的。
    """
    if w.shape[0] < TOP_N:
        return np.full(w.shape[1], np.nan)
    top = np.partition(w, w.shape[0] - TOP_N, axis=0)[w.shape[0] - TOP_N:, :]
    return top.mean(axis=0)


# ==================== 可成交性 ====================
# 严格按预登记 §2 的 V1 定义：**只**挡这四类（涨停 / 一字板向上 / 停牌 / 序列已终止）。
# 跌停**不挡**——跌停可以买入。
BLOCK_LONG = {"limit_up", "no_bar", "after_last_data"}
# 无法判定（缺前收、前收非正）：不改动，只计数。预登记没授权剔它们。
UNDETERMINED = {"no_prev", "bad_prev_close"}


def long_tradable(flags: dict, reason: str) -> tuple[bool, str]:
    """建仓日能否买入。**跌停可以买**（只挡涨停方向），这条写死。"""
    if reason in BLOCK_LONG:
        return False, reason
    if reason == "one_word_board":
        up = flags.get("dir") == "up"
        return (not up), ("one_word_board_up" if up else "kept_one_word_down")
    return True, reason


def filter_v1(rec: dict, bars: dict, ex_aligned: dict, span: dict,
              pos_map: dict[str, int]) -> dict:
    """V1：在建仓日剔除买不到的标的。`pos_map` 用于 O(1) 定位 fwd 下标。"""
    keep, drop = [], defaultdict(int)
    undet = 0
    for c in rec["keep_codes"]:
        rec_b = bars.get(c, {}).get(rec["entry"])
        f = C.flags_fast(rec_b, c, rec["entry"], ex_aligned.get(c, set()))
        reason = C._reason_of(f, c, rec["entry"], span)
        ok, why = long_tradable(f, reason)
        if ok:
            keep.append(c)
            if why in UNDETERMINED:
                undet += 1
        else:
            drop[why] += 1
    return {"keep_codes": keep, "drop": dict(drop), "n_drop": sum(drop.values()),
            "n_undetermined_kept": undet, "pos_map": pos_map}


# ==================== 统计 ====================
def seg_stats(alphas: list[float], dates: list[str], lo: str, hi: str) -> dict:
    """分段统计。⚠️ `newey_west` 在 `n < lag+3` 时只返回 `n/mean/t_nw`（无 `mean_pp`），
    直接取键会 KeyError。这里显式处理，并把 `enough` 报出来 —— 样本不足的分段
    在 B 条件里必须**判为不通过**，不能因为 nan 比较恒为 False 就悄悄混过去。
    """
    sub = [a for a, d in zip(alphas, dates) if lo <= d <= hi]
    nw = C.newey_west(sub, NW_LAG)
    return {"n": len(sub),
            "mean_pp": nw.get("mean_pp", float("nan")),
            "t_nw": nw.get("t_nw", float("nan")),
            "enough": len(sub) >= NW_LAG + 3}


def turnover_of_members(codes_per_month: list[set[str]]) -> float:
    """单边换手 = 成员变动比例的一半（买入+卖出各计一边）。与阶段 H 同口径。"""
    ts = []
    for prev, cur in zip(codes_per_month, codes_per_month[1:]):
        ts.append(0.5 * (len(cur - prev) + len(prev - cur)) / max(len(cur), 1))
    return statistics.mean(ts) if ts else float("nan")


def judge(nw: dict, segs: dict[str, dict], net_primary: dict) -> dict:
    """三条并列必要条件 + 四态判定（预登记 §3）。顺序：先命中先返回。"""
    cond_a = abs(nw["t_nw"]) >= T_CRIT

    sign_full = 1.0 if nw["mean_pp"] > 0 else -1.0
    cond_b = all(
        s.get("enough", True)
        and (s["mean_pp"] > 0) == (sign_full > 0)
        and abs(s["t_nw"]) >= T_CRIT
        for s in segs.values()
    )
    cond_c = abs(net_primary["t_net"]) >= T_CRIT

    if cond_a and cond_b and cond_c:
        v = "EXQ5_CONFIRMED"
    elif cond_a and cond_c and not cond_b:
        v = "EXQ5_SIGNIFICANT_BUT_UNSTABLE"
    elif cond_a and not cond_c:
        v = "EXQ5_COST_ABSORBED"
    else:
        v = "EXQ5_NOT_SIGNIFICANT"

    return {"A_stat": bool(cond_a), "B_stable": bool(cond_b), "C_cost": bool(cond_c),
            "verdict": v}


# ==================== 容量 ====================
def affordable_subset(lots: list[float], budget: float) -> list[int]:
    """按一手金额**从高到低剔除**，直到 `k × max(lot) ≤ budget`。

    ⚠️ 规则写死在预登记 §5：**只按一手金额剔，不许按信号值剔**（那会偷偷引入选择）。
    `k × lots_sorted[k-1]` 对 k 单调不减，所以可以提前 break。
    """
    n = len(lots)
    if n == 0 or budget <= 0:
        return []
    order = sorted(range(n), key=lambda i: lots[i])
    best = 0
    for k in range(1, n + 1):
        if k * lots[order[k - 1]] <= budget:
            best = k
        else:
            break
    return order[:best]


def capacity_of(lots: list[float], budget: float) -> dict:
    """给定资金，能买多少只、剩多少现金、权重偏离多大。

    ⚠️ 必须**同时**报 `未投资比例`：`weight_mae` 在仓位等比例缩小时看不出偏差
    （MEMORY §10 的教训），单看它会得出"权重很准"的错误结论。
    """
    k_total = len(lots)
    if k_total == 0:
        return {"k_total": 0, "k": 0, "covered": 0.0, "uninvested": 1.0,
                "weight_mae_pp": float("nan"), "invested": 0.0}
    idx = affordable_subset(lots, budget)
    k = len(idx)
    if k == 0:
        return {"k_total": k_total, "k": 0, "covered": 0.0, "uninvested": 1.0,
                "weight_mae_pp": float("nan"), "invested": 0.0}
    per = budget / k
    invested = 0.0
    ws = []
    for i in idx:
        need = lots[i]
        n_lot = int(per // need) if need > 0 else 0
        amt = n_lot * need
        invested += amt
        ws.append(amt / budget)
    target = 1.0 / k
    mae_pp = statistics.mean([abs(w - target) for w in ws]) * 100
    return {"k_total": k_total, "k": k, "covered": k / k_total,
            "uninvested": 1.0 - invested / budget,
            "weight_mae_pp": mae_pp, "invested": invested}


def q(v: list[float], p: float) -> float:
    s = sorted(v)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


# ==================== 启动校验 ====================
def verify() -> dict:
    p = "docs/PHASE_I_EXQ5_PREREG_2026-10-21.md"
    if not os.path.exists(p):
        raise SystemExit(f"预登记缺失：{p}")
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    if h != PREREG_SHA256:
        raise SystemExit(f"预登记哈希不符！\n  期望 {PREREG_SHA256}\n  实际 {h}\n"
                         f"  预登记被改过 → 本实验无效，必须新预登记")
    n_adj = len([f for f in os.listdir(C.ADJ_DIR) if f.endswith(".qfq.csv")])
    n_exec = len([f for f in os.listdir(C.EXEC_DIR) if f.endswith(".exec.csv")])
    if n_adj < 5400 or n_exec < 5400:
        raise SystemExit(f"数据不足：qfq={n_adj} exec={n_exec}")
    return {"prereg_sha256": h, "n_adj": n_adj, "n_exec": n_exec}


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_csv(path: str, header: list[str], rows: list[list]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


# ==================== 主流程 ====================
def build_records() -> tuple[list[dict], dict]:
    """构造每月持有集（V0 口径：不做交易约束）。"""
    close = load_close()
    cal = close.index
    panels = Panels(close)
    positions = month_end_positions(cal, START, END_HOLD)
    mask = build_mask(close, MIN_LISTED_DAYS)
    ret = close.pct_change(fill_method=None)
    cols = np.array(close.columns)
    arr_r = ret.to_numpy()
    ffill = panels.ffill

    recs: list[dict] = []
    n_skip = defaultdict(int)
    diff_split = 0     # 与阶段 F 分组函数不一致的月份数（应为 0）
    # ⚠️ 预登记 §2.2 自身有矛盾：它既要求「不许 import `run_phase_f_max5.split_equal`」，
    #    又要求「两者结果必须逐元素一致」。两句在字面上无法同时满足。
    #    按**更强**的那一条执行：import 仅用于交叉校验，**不作生产实现**
    #    （生产用的是本文件的 `split_equal_indep`）。已登记为 prereg_defects #2。
    from run_phase_f_max5 import split_equal as split_equal_f

    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sdate = str(cal[pos].date())
        if sdate > END_SIGNAL or pos - WIN + 1 < 0 or pos + 1 >= len(cal):
            n_skip["outside_window"] += 1
            continue
        ok = mask.iloc[pos].to_numpy()
        if ok.sum() < 100:
            n_skip["pool_too_small"] += 1
            continue
        idx = np.where(ok)[0]
        w = arr_r[pos - WIN + 1: pos + 1][:, idx]
        good = np.isfinite(w).sum(axis=0) >= MIN_VALID
        if good.sum() < 100:
            n_skip["window_too_sparse"] += 1
            continue
        sub = w[:, good]
        max5 = max5_partition(np.where(np.isfinite(sub), sub, -np.inf))
        codes = cols[idx][good]
        m = np.isfinite(max5)
        if m.sum() < 100:
            n_skip["max5_all_nan"] += 1
            continue
        max5, codes = max5[m], codes[m]

        # —— 独立分组 vs 阶段 F 分组：必须逐元素一致
        grp = split_equal_indep(max5, N_GROUP)
        if not np.array_equal(grp, split_equal_f(max5, N_GROUP)):
            diff_split += 1

        p0 = ffill.iloc[pos][codes].to_numpy(dtype=float)
        p1 = ffill.iloc[nxt][codes].to_numpy(dtype=float)
        with np.errstate(invalid="ignore", divide="ignore"):
            fwd = p1 / p0 - 1.0
        fm = np.isfinite(fwd)
        if fm.sum() < 100:
            n_skip["fwd_all_nan"] += 1
            continue
        max5, codes, fwd, grp = max5[fm], codes[fm], fwd[fm], grp[fm]

        is_q5 = grp == Q_HIGH
        q5_codes = codes[is_q5]
        keep_codes = codes[~is_q5]
        r_bench = float(fwd.mean())
        r_q5 = float(fwd[is_q5].mean())
        r_ex = float(fwd[~is_q5].mean())

        recs.append({
            "signal_date": sdate,
            "entry": str(cal[pos + 1].date()),
            "n_pool": int(len(fwd)),
            "n_keep": int((~is_q5).sum()),
            "alpha": r_ex - r_bench,
            "r_bench": r_bench,
            "r_q5": r_q5,
            "keep_codes": [str(c) for c in keep_codes],
            "q5_codes": [str(c) for c in q5_codes],
            "codes": [str(c) for c in codes],
            "fwd": fwd.tolist(),
            "max5_median": float(np.median(max5)),
        })
    if not recs:
        raise SystemExit("没有有效月份")
    return recs, {"n_skip": dict(n_skip), "split_mismatch_months": diff_split,
                  "max5_median_mean_pp": statistics.mean(r["max5_median"] for r in recs) * 100}


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print(f"阶段 I：exQ5 正式检验   预登记 {PREREG_SHA12}")
    print("=" * 78)

    vi = verify()
    print(f"  预登记哈希校验 ✓   qfq={vi['n_adj']}  exec={vi['n_exec']}")

    recs, meta = build_records()
    print(f"  月份 {len(recs)}   数据跳过 {meta['n_skip']}")
    print(f"  独立分组 vs 阶段F分组：不一致月份 {meta['split_mismatch_months']} "
          f"{'✓' if meta['split_mismatch_months'] == 0 else '✗ 实现有分歧'}")
    print(f"  MAX5 中位逐月均值 {meta['max5_median_mean_pp']:.4f}pp "
          f"（可行性报告 3.23234pp）")

    dates = [r["signal_date"] for r in recs]
    alphas = [r["alpha"] for r in recs]
    nw0 = C.newey_west(alphas, NW_LAG)
    segs0 = {lab: seg_stats(alphas, dates, lo, hi) for lo, hi, lab in SEGMENTS}
    print()
    print("① V0（无约束，复现闸门）")
    print(f"   alpha = {nw0['mean_pp']:+.6f}pp  NW t = {nw0['t_nw']:+.6f}  n = {len(recs)}")
    for lab, s in segs0.items():
        print(f"   {lab}  n={s['n']:>3}  alpha={s['mean_pp']:+8.6f}pp  t={s['t_nw']:+7.3f}")

    # ---- 复现闸门
    got = {
        "n_months": len(recs),
        "alpha_mean_pp": nw0["mean_pp"],
        "t_nw": nw0["t_nw"],
        "seg_2007_2015_pp": segs0["2007-2015"]["mean_pp"],
        "seg_2016_2026_pp": segs0["2016-2026"]["mean_pp"],
    }
    repro, repro_ok = {}, True
    for k, exp in EXPECT.items():
        d = abs(got[k] - exp)
        ok = d <= REPRO_TOL[k]
        repro[k] = {"expected": exp, "actual": round(got[k], 6), "diff": round(d, 10), "ok": ok}
        repro_ok &= ok
    print(f"   复现闸门：{'通过 ✓' if repro_ok else '✗ 不通过 —— 实验作废'}")
    if not repro_ok:
        for k, v in repro.items():
            if not v["ok"]:
                print(f"     {k}: 实测 {v['actual']} vs 期望 {v['expected']}  差 {v['diff']:.2e}")
        return 2

    # ---- V1：建仓日可成交
    print()
    print("② V1（主检验：建仓日剔除买不到的）")
    need: dict[str, set[str]] = defaultdict(set)
    for i, r in enumerate(recs):
        for c in r["keep_codes"]:
            need[c].add(r["entry"])
            # 下一期调仓日 = 本期的卖出日，也要取 bar（给 V2 计数器用）
            if i + 1 < len(recs):
                need[c].add(recs[i + 1]["entry"])
    print(f"   需扫 EXEC {len(need)} 只 · {sum(len(v) for v in need.values())} 点 …")
    bars, ex_al, span = stream_bars(need)

    drop_agg = defaultdict(int)
    n_drop_l, n_keep_l, n_undet = 0, 0, 0
    # 预登记 §4.2 要求：若 V1 低于 V0，必须单独解释机制。
    # 这里把"被剔标的 vs 保留标的"的同期收益差直接算出来，而不是事后讲故事。
    d_fwd, k_fwd, d_share = [], [], []
    for r in recs:
        pos_map = {c: i for i, c in enumerate(r["codes"])}   # O(1) 定位，别用 list.index
        f1 = filter_v1(r, bars, ex_al, span, pos_map)
        r["keep_v1"] = f1["keep_codes"]
        r["n_drop"] = f1["n_drop"]
        for k, v in f1["drop"].items():
            drop_agg[k] += v
        n_drop_l += f1["n_drop"]
        n_keep_l += len(r["keep_codes"])
        n_undet += f1["n_undetermined_kept"]
        dropped = [c for c in r["keep_codes"] if c not in set(f1["keep_codes"])]
        if dropped:
            d_fwd.append(float(np.mean([r["fwd"][pos_map[c]] for c in dropped])))
            d_share.append(len(dropped) / max(len(r["keep_codes"]), 1))
        if f1["keep_codes"]:
            kept = float(np.mean([r["fwd"][pos_map[c]] for c in f1["keep_codes"]]))
            k_fwd.append(kept)
            r["alpha_v1"] = kept - r["r_bench"]
        else:
            r["alpha_v1"] = float("nan")

    print(f"   剔除 {n_drop_l} 只次（占 {n_drop_l/max(n_keep_l,1)*100:.2f}%）")
    for k, v in sorted(drop_agg.items(), key=lambda kv: -kv[1]):
        print(f"     {k:<22} {v:>7}")
    if n_undet:
        print(f"   无法判定但保留 {n_undet} 只次（预登记未授权剔除，仅计数）")

    valid = [(r["signal_date"], r["alpha_v1"]) for r in recs if np.isfinite(r["alpha_v1"])]
    d1 = [x[0] for x in valid]
    a1 = [x[1] for x in valid]
    nw1 = C.newey_west(a1, NW_LAG)
    segs1 = {lab: seg_stats(a1, d1, lo, hi) for lo, hi, lab in SEGMENTS}
    print(f"   alpha = {nw1['mean_pp']:+.6f}pp  NW t = {nw1['t_nw']:+.6f}  n = {len(a1)}")
    print(f"   与 V0 差 {nw1['mean_pp'] - nw0['mean_pp']:+.6f}pp（预登记预期 +0.02~+0.10）")
    # 分解：为什么 V1 与 V0 差这么多（预登记 §4.2 强制要求解释）
    d_mean = statistics.mean(d_fwd) * 100 if d_fwd else float("nan")
    k_mean = statistics.mean(k_fwd) * 100 if k_fwd else float("nan")
    print(f"   分解：被剔标的下期收益 {d_mean:+.4f}pp  vs  保留 {k_mean:+.4f}pp"
          f"  → 差 {d_mean - k_mean:+.4f}pp（被剔的更{'+' if d_mean > k_mean else '-'}好）")
    if d_share:
        share = statistics.mean(d_share)
        print(f"   每月被剔比例均值 {share*100:.2f}%"
              f"  → 对 alpha 的贡献约 {share*(k_mean - d_mean):+.4f}pp"
              f"（剔掉更{('好' if d_mean > k_mean else '差')}的标的，alpha 相应降低）")
    for lab, s in segs1.items():
        print(f"   {lab}  n={s['n']:>3}  alpha={s['mean_pp']:+8.6f}pp  t={s['t_nw']:+7.3f}")

    # ---- 换手与成本
    print()
    print("③ 换手与成本（成本走 cost_of，不许手写）")
    to_v0 = turnover_of_members([set(r["keep_codes"]) for r in recs])
    to_v1 = turnover_of_members([set(r["keep_v1"]) for r in recs])
    sd1 = statistics.stdev(a1)
    net = {f"{c*100:.1f}pct": C.cost_of(to_v1, nw1["mean"], sd1, len(a1), c) for c in COST_TIERS}
    for tag, r_ in net.items():
        print(f"   @{tag}: 成本 {r_['cost_pp_month']:.4f}pp/月 → 净 {r_['net_pp_month']:+.4f}pp  "
              f"t_net={r_['t_net']:+.3f}")
    print(f"   单边换手 V0 {to_v0*100:.2f}%/月")

    # ---- V2 计数器（预登记 §2 被降级的那项：只计数，不重算 alpha）
    sell_blocked, sell_total, sell_by = 0, 0, defaultdict(int)
    for i, r in enumerate(recs[:-1]):
        ex_date = recs[i + 1]["entry"]          # 下一期建仓日 = 本期卖出日
        for c in r["keep_v1"]:
            rec_b = bars.get(c, {}).get(ex_date)
            f = C.flags_fast(rec_b, c, ex_date, ex_al.get(c, set()))
            reason = C._reason_of(f, c, ex_date, span)
            sell_total += 1
            blocked = (reason in {"limit_down", "no_bar", "after_last_data"} or
                       (reason == "one_word_board" and f.get("dir") == "down"))
            if blocked:
                sell_blocked += 1
                sell_by[reason] += 1
    print(f"   调仓日不可卖出 {sell_blocked}/{sell_total} 只次"
          f"（{sell_blocked/max(sell_total,1)*100:.2f}%）← V2 计数器，不参与判定")

    # ---- 判定
    j = judge(nw1, segs1, net["0.3pct"])
    print()
    print("④ 判定（预登记 §3）")
    print(f"   A 统计  |t|≥1.96      : t={nw1['t_nw']:+.4f}  → {'过' if j['A_stat'] else '不过'}")
    print(f"   B 稳定  两段均显著同号 : "
          + "  ".join(f"{k} {v['mean_pp']:+.4f}(t={v['t_nw']:+.2f})" for k, v in segs1.items())
          + f"  → {'过' if j['B_stable'] else '不过'}")
    print(f"   C 成本  @0.3% |t|≥1.96: t_net={net['0.3pct']['t_net']:+.4f}  "
          f"→ {'过' if j['C_cost'] else '不过'}")
    print(f"   >>> {j['verdict']}")

    # ---- 容量
    print()
    print("⑤ 容量（描述性，不参与统计判定；成熟度用它）")
    # 一手金额只算一次，容量与可负担子集共用（避免重复扫 bars）
    for r in recs:
        lots, lc = [], []
        for c in r["keep_v1"]:
            rb = bars.get(c, {}).get(r["entry"])
            if rb is not None and rb["bar"][3] > 0:
                lots.append(rb["bar"][3] * LOT)
                lc.append(c)
        r["lots"], r["lot_codes"] = lots, lc

    cap_rows, cap_sum = [], {}
    for budget in CAPITAL_TIERS:
        cov, unin, mae, ks = [], [], [], []
        for r in recs:
            if not r["lots"]:
                continue
            cs = capacity_of(r["lots"], budget)
            cov.append(cs["covered"])
            unin.append(cs["uninvested"])
            mae.append(cs["weight_mae_pp"])
            ks.append(cs["k"])
        row = {"budget": budget,
               "covered_median": statistics.median(cov),
               "uninvested_median": statistics.median(unin),
               "weight_mae_pp_median": statistics.median(mae),
               "k_median": statistics.median(ks)}
        cap_sum[f"{budget/1e4:.0f}万"] = row
        cap_rows.append([f"{budget/1e4:.0f}", f"{row['k_median']:.0f}",
                         f"{row['covered_median']:.4f}", f"{row['uninvested_median']:.4f}",
                         f"{row['weight_mae_pp_median']:.4f}"])
        print(f"   W={budget/1e4:>7.0f}万  可买 {row['k_median']:>6.0f} 只"
              f"（{row['covered_median']*100:5.1f}%）  未投资 {row['uninvested_median']*100:5.2f}%"
              f"   weight_mae {row['weight_mae_pp_median']:.4f}pp")

    m1_row = cap_sum[f"{M1_CAPITAL/1e4:.0f}万"]
    cap_ok = (m1_row["uninvested_median"] <= MAX_UNINVESTED
              and m1_row["weight_mae_pp_median"] <= MAX_WEIGHT_MAE_PP)
    maturity = "M1" if (j["verdict"] == "EXQ5_CONFIRMED" and cap_ok) else "M0"
    print(f"   容量可行（未投资≤{MAX_UNINVESTED*100:.0f}% 且 mae≤{MAX_WEIGHT_MAE_PP}pp @W={M1_CAPITAL/1e4:.0f}万）"
          f"：{'是' if cap_ok else '否'}")
    print(f"   >>> 成熟度 {maturity}")

    # ---- 可负担子集的 alpha（描述性）
    sub_alpha = []
    for r in recs:
        if not r["lots"]:
            continue
        idx = affordable_subset(r["lots"], M1_CAPITAL)
        if len(idx) < 50:
            continue
        pos = {c: i for i, c in enumerate(r["codes"])}
        picked = [r["lot_codes"][i] for i in idx]
        r_sub = float(np.mean([r["fwd"][pos[c]] for c in picked]))
        sub_alpha.append((r["signal_date"], r_sub - r["r_bench"]))
    nw_sub = C.newey_west([x[1] for x in sub_alpha], NW_LAG) if len(sub_alpha) > 5 else None
    if nw_sub:
        print(f"   可负担子集（W={M1_CAPITAL/1e4:.0f}万）alpha = {nw_sub['mean_pp']:+.4f}pp  "
              f"t={nw_sub['t_nw']:+.3f}  n={len(sub_alpha)}  ← 描述性，不参与判定")

    # ---- 诊断
    print()
    print("⑥ 诊断")
    # V0 在 0.3% 成本下会怎样（描述性：用来区分"是成本吃掉的"还是"是可成交约束吃掉的"）
    sd0 = statistics.stdev(alphas)
    net_v0_03 = C.cost_of(to_v0, nw0["mean"], sd0, len(alphas), 0.003)
    print(f"   对照：V0 的 @0.3% 净 {net_v0_03['net_pp_month']:+.4f}pp "
          f"t_net={net_v0_03['t_net']:+.3f}"
          f"  → V0 口径下 C 条件{'过' if abs(net_v0_03['t_net']) >= T_CRIT else '不过'}")
    rr = [r["r_q5"] for r in recs]
    print(f"   R_Q5 均值 {statistics.mean(rr)*100:+.4f}pp/月  "
          f"<0 月份占比 {sum(1 for x in rr if x < 0)/len(rr)*100:.1f}%")
    for lab, (lo, hi, _) in zip(segs0, SEGMENTS):
        sub = [r["r_q5"] for r in recs if lo <= r["signal_date"] <= hi]
        print(f"   {lab} R_Q5 均值 {statistics.mean(sub)*100:+.4f}pp")
    persist = []
    prev = None
    for r in recs:
        cur = set(r["q5_codes"])
        if prev:
            persist.append(len(cur & prev) / max(len(cur), 1))
        prev = cur
    print(f"   Q5 成员持续性（与上期重叠）中位 {statistics.median(persist)*100:.1f}%")
    by_year = defaultdict(list)
    for d, a in zip(d1, a1):
        by_year[d[:4]].append(a)
    yr = {y: {"n": len(v), "mean_pp": statistics.mean(v) * 100} for y, v in sorted(by_year.items())}
    print("   分年度 alpha：" + "  ".join(f"{y}:{v['mean_pp']:+.2f}" for y, v in yr.items()))

    # ---- 产物
    os.makedirs(OUT_DIR, exist_ok=True)
    _write_csv(os.path.join(OUT_DIR, "monthly.csv"),
               ["signal_date", "entry", "n_pool", "n_keep", "n_drop",
                "r_bench_pp", "r_q5_pp", "alpha_v0_pp", "alpha_v1_pp"],
               [[r["signal_date"], r["entry"], r["n_pool"], r["n_keep"], r["n_drop"],
                 round(r["r_bench"] * 100, 6), round(r["r_q5"] * 100, 6),
                 round(r["alpha"] * 100, 6),
                 (round(r["alpha_v1"] * 100, 6) if np.isfinite(r["alpha_v1"]) else "")]
                for r in recs])
    _write_csv(os.path.join(OUT_DIR, "capacity.csv"),
               ["budget_wan", "k_median", "covered_median", "uninvested_median",
                "weight_mae_pp_median"], cap_rows)

    summary = {
        "experiment": "phasei-exq5-formal-v1",
        "prereg_sha256": PREREG_SHA256,
        "prereg_version": "1.0",
        "design": {
            "win": WIN, "top_n": TOP_N, "min_valid": MIN_VALID, "n_group": N_GROUP,
            "exclude_frac": EXCLUDE_FRAC, "pool": "as-of 全 A + 252 日",
            "window": f"{START} ~ {END_SIGNAL}", "nw_lag": NW_LAG,
            "t_crit": T_CRIT, "primary_cost": PRIMARY_COST,
        },
        "independence": {
            "split_mismatch_months": meta["split_mismatch_months"],
            "max5_median_mean_pp": round(meta["max5_median_mean_pp"], 6),
            "note": "分组用 searchsorted 边界定位、MAX5 用 np.partition，均与阶段 F 算法不同",
        },
        "reproduction_gate": {"passed": bool(repro_ok), "checks": repro},
        "V0_unconstrained": {"n": len(recs), **{k: round(v, 6) for k, v in nw0.items()
                                                if isinstance(v, (int, float))},
                             "segments": {k: {kk: round(vv, 6) for kk, vv in v.items()}
                                          for k, v in segs0.items()}},
        "V1_executable": {"n": len(a1), **{k: round(v, 6) for k, v in nw1.items()
                                           if isinstance(v, (int, float))},
                          "segments": {k: {kk: round(vv, 6) for kk, vv in v.items()}
                                       for k, v in segs1.items()},
                          "delta_vs_v0_pp": round(nw1["mean_pp"] - nw0["mean_pp"], 6),
                          "drop_total": n_drop_l,
                          "drop_share": round(n_drop_l / max(n_keep_l, 1), 6),
                          "drop_reasons": dict(drop_agg),
                          "mechanism": {
                              "dropped_fwd_pp": round(statistics.mean(d_fwd) * 100, 6),
                              "kept_fwd_pp": round(statistics.mean(k_fwd) * 100, 6),
                              "drop_share_mean": round(statistics.mean(d_share), 6),
                              "note": "被剔标的下期收益若高于保留标的，V1 就会低于 V0",
                          }},
        "turnover": {"v0_one_side": round(to_v0, 6), "v1_one_side": round(to_v1, 6)},
        "V2_counter": {"sell_blocked": sell_blocked, "sell_total": sell_total,
                       "share": round(sell_blocked / max(sell_total, 1), 6),
                       "by_reason": dict(sell_by),
                       "note": "只计数，不重算 alpha（预登记 §2 的 V2 因前视问题降级，见 prereg_defects）"},
        "cost": {k: {kk: round(vv, 6) for kk, vv in v.items()} for k, v in net.items()},
        "criteria": j,
        "verdict": j["verdict"],
        "capacity": {"tiers": cap_sum, "m1_capital": M1_CAPITAL,
                     "max_uninvested": MAX_UNINVESTED,
                     "max_weight_mae_pp": MAX_WEIGHT_MAE_PP, "feasible": bool(cap_ok)},
        "maturity": maturity,
        "affordable_subset": ({"budget": M1_CAPITAL, "n": len(sub_alpha),
                               "mean_pp": round(nw_sub["mean_pp"], 6),
                               "t_nw": round(nw_sub["t_nw"], 6),
                               "note": "描述性，不参与判定；剔除规则按一手金额、不按信号值"}
                              if nw_sub else None),
        "diagnostics": {
            "v0_net_at_0_3pct": {k: round(v, 6) for k, v in net_v0_03.items()},
            "v0_would_pass_C": bool(abs(net_v0_03["t_net"]) >= T_CRIT),
            "r_q5_mean_pp": round(statistics.mean(rr) * 100, 6),
            "r_q5_negative_share": round(sum(1 for x in rr if x < 0) / len(rr), 6),
            "q5_persistence_median": round(statistics.median(persist), 6),
            "yearly": {y: {"n": v["n"], "mean_pp": round(v["mean_pp"], 6)}
                       for y, v in yr.items()},
        },
        "prereg_defects": [{
            "item": "§2 的 V2（平仓日剔除）",
            "problem": "持有型构造没有明确平仓日；若要按 nxt 日能否卖出决定是否持有，"
                       "则需要用到 nxt 日信息才能定当期持仓 = 前视。",
            "handling": "降级为计数器（只报调仓日不可卖出比例），不重算 alpha。"
                        "V2 本来就是描述性、不参与判定，故不影响结论。",
        }, {
            "item": "§2.2 的独立性要求自相矛盾",
            "problem": "同一段既写「不许 import `run_phase_f_max5.split_equal`」，"
                       "又写「两者结果必须逐元素一致」——不 import 就无从比较。",
            "handling": "按更强的那条执行：import 仅用于交叉校验，生产实现用本文件的 "
                        "`split_equal_indep`（searchsorted 边界定位）。"
                        "交叉校验结果见 independence.split_mismatch_months（应为 0）。",
        }],
        "honest_limits": [
            "exQ5 是在同一批数据上由阶段 F 的构造代数推出的，本阶段只提供"
            "「实现独立 + 判据前置 + 可成交口径」，**不提供样本外证据**。",
            "alpha_exQ5 ≡ 0.25×(R_bench−R_Q5) 是恒等式，不是新因子。",
        ],
    }
    sp = os.path.join(OUT_DIR, "summary.json")
    with open(sp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(summary, ensure_ascii=False, indent=1) + "\n")

    files = ["monthly.csv", "capacity.csv", "summary.json"]
    man = {
        "experiment": "phasei-exq5-formal-v1",
        "prereg_sha256": PREREG_SHA256,
        "script_sha256": _sha256_file(os.path.abspath(__file__)),
        "data": {"adj_dir": C.ADJ_DIR, "exec_dir": C.EXEC_DIR,
                 "n_adj": vi["n_adj"], "n_exec": vi["n_exec"]},
        "files": {n: {"sha256": _sha256_file(os.path.join(OUT_DIR, n)),
                      "bytes": os.path.getsize(os.path.join(OUT_DIR, n))} for n in files},
        "runtime_sec": round(time.time() - t0, 1),
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(man, ensure_ascii=False, indent=1) + "\n")

    print()
    print(f"[产物] {OUT_DIR}/  脚本 {man['script_sha256'][:12]}  总耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
