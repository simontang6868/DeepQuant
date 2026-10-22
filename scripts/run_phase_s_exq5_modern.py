#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 S：exQ5 现代段（2016-2026）可执行性结项检验

预登记 docs/PHASE_S_EXQ5_MODERN_PREREG_2026-10-22.md
冻结哈希 f098a59fb1b584eda3ce69a8ecfc890b1be93cae9091bf2ba776469f949f2371

**本阶段只读阶段 O / 阶段 J 已冻结的逐月产物，不重算任何选股/组合。**

主检验：exQ5 在现代段（2016-2026，n=129）的成本后净 alpha 是否可检出。
主序列 = net_BASE_3000w + 0.00078376 − 0.01899561
        （BASE 真实费率 + 现金残留 + √ 冲击 η=1；把阶段 O 的 Amihud 常数上界
          换成更保守的 √ 模型常数）

设计动机：阶段 R 用"后段单独是否显著"这把尺子杀死了 SURGE（后段 t=0.26）。
exQ5 是项目唯一存活信号，必须被同一把尺子量过才能结项。
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import random
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402

PREREG_SHA256 = ("f098a59fb1b584eda3ce69a8ecfc890b1be93cae9091bf2ba776469f949f2371")
PREREG_SHA12 = PREREG_SHA256[:12]
OUT_DIR = os.path.join("phases_out", PREREG_SHA12)

# ── 冻结项（预登记 §4） ───────────────────────────────────────────
SPLIT_DATE = "2016-01-01"
NW_LAG = 3
T_CRIT = 1.96
Z_POWER80 = 2.8016            # 双侧 5% + 功效 80% 的 MDE 系数
BOOT_N = 2000
BOOT_SEED = 20261022

O_CSV = os.path.join("phaseo_out", "ac7aa2830ab0", "monthly.csv")
J_CSV = os.path.join("phasej_out", "f76de8a277f6", "monthly.csv")
O_SHA256 = "768e49a9462cd7c00f3aacfcdb1e87bbe37bb75b348cd6fd85809e12493a4152"
J_SHA256 = "6f1961bdc2709c98b637ef545a8a90e2f76b3e2f1983974c827e35dffb859235"

# 阶段 J by_capital 的冲击常数（pp/月）
IMPACT_UPPER = 0.00078376     # Amihud @3000万（阶段 O 收取的上界）
SQRT_3000W = 0.01899561       # √ η=1 @3000万
SQRT_1000W = 0.01131169       # √ η=1 @1000万
SCALE_3M_OVER_1M = SQRT_3000W / SQRT_1000W   # 1.6792901856（预登记 §4 冻结）

# 主序列（pp/月）
PRIMARY = "net_S_3000w"


# ── 基础工具 ────────────────────────────────────────────────────
def verify() -> None:
    p = "docs/PHASE_S_EXQ5_MODERN_PREREG_2026-10-22.md"
    if not os.path.exists(p):
        raise SystemExit(f"预登记缺失：{p}")
    h = hashlib.sha256(open(p, "rb").read()).hexdigest()
    if h != PREREG_SHA256:
        raise SystemExit(f"预登记哈希不符！\n  期望 {PREREG_SHA256}\n  实际 {h}")
    print(f"[S] 预登记校验通过 sha256={h[:12]}")


def sha256_file(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def nw_stats_pp(vals_pp: list[float]) -> dict:
    """对 pp 序列求 NW 均值检验。

    ⚠️ 单位铁律：`newey_west` 入参必须是小数（pp/100），读 mean_pp/se_nw_pp。
    直接传 pp 会放大 100 倍——本项目踩过两次，单测钉死。
    """
    r = C.newey_west([v / 100.0 for v in vals_pp], lag=NW_LAG)
    n = r["n"]
    sd = statistics.pstdev(vals_pp) * math.sqrt(n / (n - 1)) if n > 1 else float("nan")
    se_iid_pp = sd / math.sqrt(n) if n > 1 else float("nan")
    t_iid = r["mean_pp"] / se_iid_pp if se_iid_pp and se_iid_pp > 0 else float("nan")
    return {
        "n": n,
        "mean_pp": r["mean_pp"],
        "se_nw_pp": r["se_nw_pp"],
        "se_iid_pp": se_iid_pp,
        "t_nw": r["t_nw"],
        "t_iid": t_iid,
        "p_nw": r["p"],
        "mde_pp": Z_POWER80 * r["se_nw_pp"] if r["se_nw_pp"] == r["se_nw_pp"] else float("nan"),
        "power_observed": power_at_observed(abs(r["t_nw"])) if r["t_nw"] == r["t_nw"] else float("nan"),
        "variance_fell_back_to_g0": r["variance_fell_back_to_g0"],
    }


def power_at_observed(abs_t: float, crit: float = T_CRIT) -> float:
    """事后功效：Φ(|t|−crit) + Φ(−|t|−crit)；|t|=crit 时恰为 50%。"""
    def phi(z: float) -> float:
        return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))
    if abs_t != abs_t:
        return float("nan")
    return phi(abs_t - crit) + phi(-abs_t - crit)


def boot_ci_pp(vals_pp: list[float], n_draws: int = BOOT_N,
               seed: int = BOOT_SEED) -> list[float]:
    """月份整段重抽（iid）的均值 95% 分位区间。稳健性参考，非判定依据。"""
    rng = random.Random(seed)
    n = len(vals_pp)
    means = []
    for _ in range(n_draws):
        s = 0.0
        for _ in range(n):
            s += vals_pp[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    lo = means[int(0.025 * n_draws)]
    hi = means[min(n_draws - 1, int(0.975 * n_draws))]
    return [lo, hi]


def judge(mean_pp: float, t_nw: float) -> dict:
    if t_nw != t_nw:
        return {"verdict": "INDETERMINATE", "reason": "t 为 NaN"}
    if abs(t_nw) >= T_CRIT and mean_pp > 0:
        return {"verdict": "MODERN_ERA_ALPHA_DETECTED"}
    if abs(t_nw) >= T_CRIT and mean_pp < 0:
        return {"verdict": "MODERN_ERA_ALPHA_NEGATIVE"}
    return {"verdict": "MODERN_ERA_ALPHA_NOT_DETECTED"}


# ── 数据装载 ────────────────────────────────────────────────────
def load_merged() -> list[dict]:
    for path, want in ((O_CSV, O_SHA256), (J_CSV, J_SHA256)):
        got = sha256_file(path)
        if got != want:
            raise SystemExit(f"输入 sha256 不符，拒绝运行：{path}\n  期望 {want}\n  实际 {got}")
    print("[S] 输入 sha256 校验通过（阶段 O / 阶段 J 逐月产物）")

    def read(path: str) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                d = row["signal_date"]
                out[d] = {k: float(v) for k, v in row.items() if k != "signal_date"}
        return out

    o, j = read(O_CSV), read(J_CSV)
    if set(o) != set(j):
        raise SystemExit("两个产物的 signal_date 集合不一致")
    rows = []
    for d in sorted(o):
        r = {"signal_date": d}
        r.update(o[d])
        r.update({k: v for k, v in j[d].items() if k not in r})
        rows.append(r)
    if len(rows) != 237:
        raise SystemExit(f"样本量异常：{len(rows)} != 237")
    print(f"[S] 合并 237 个月（{rows[0]['signal_date']} → {rows[-1]['signal_date']}）")
    return rows


def build_series(rows: list[dict]) -> dict[str, list[float]]:
    """按预登记公式构造各口径逐月序列（pp/月）。"""
    s: dict[str, list[float]] = {k: [] for k in (
        PRIMARY, "net_S_1000w_const", "net_S_1000w_pm", "net_S_3000w_pm",
        "net_BASE_3000w", "net_BASE_1000w", "net_STRESS_3000w",
        "net_CONS_1000w", "net_CONS_3000w", "alpha_pp",
        "finv_1000w", "finv_3000w", "n_keep", "to",
        "impact_A_pp", "impact_B_eta1_pp")}
    for r in rows:
        s[PRIMARY].append(r["net_BASE_3000w"] + IMPACT_UPPER - SQRT_3000W)
        s["net_S_1000w_const"].append(r["net_BASE_1000w"] + IMPACT_UPPER - SQRT_1000W)
        s["net_S_1000w_pm"].append(r["net_BASE_1000w"] + IMPACT_UPPER - r["impact_B_eta1_pp"])
        s["net_S_3000w_pm"].append(r["net_BASE_3000w"] + IMPACT_UPPER
                                   - r["impact_B_eta1_pp"] * SCALE_3M_OVER_1M)
        for k in ("net_BASE_3000w", "net_BASE_1000w", "net_STRESS_3000w",
                  "net_CONS_1000w", "net_CONS_3000w", "alpha_pp",
                  "finv_1000w", "finv_3000w", "n_keep", "to",
                  "impact_A_pp", "impact_B_eta1_pp"):
            s[k].append(r[k])
    return s


def segment(dates: list[str]) -> list[str]:
    return ["2016-2026" if d >= SPLIT_DATE else "2007-2015" for d in dates]


# ── 主流程 ──────────────────────────────────────────────────────
def main() -> int:
    t0 = time.time()
    verify()
    rows = load_merged()
    dates = [r["signal_date"] for r in rows]
    segs = segment(dates)
    n_late = sum(1 for x in segs if x == "2016-2026")
    n_early = len(segs) - n_late
    print(f"[S] 分段：前段 {n_early} 月 / 现代段 {n_late} 月（分割点 {SPLIT_DATE}）")
    if (n_early, n_late) != (108, 129):
        raise SystemExit(f"分段月数异常：{n_early}/{n_late} != 108/129")

    S = build_series(rows)

    # ── 复现闸门 ──
    gates: dict[str, dict] = {}
    full = nw_stats_pp(S["net_BASE_3000w"])
    late = [v for v, g in zip(S["net_BASE_3000w"], segs) if g == "2016-2026"]
    late_stats = nw_stats_pp(late)
    gates["G3_full_net_BASE_3000w_t_nw"] = {"got": round(full["t_nw"], 4),
                                            "expect": 4.0386,
                                            "ok": abs(full["t_nw"] - 4.0386) < 5e-4}
    gates["G4_late_net_BASE_3000w_t_nw"] = {"got": round(late_stats["t_nw"], 4),
                                            "expect": 2.5813,
                                            "ok": abs(late_stats["t_nw"] - 2.5813) < 5e-4}
    m_impactB = statistics.fmean(S["impact_B_eta1_pp"])
    gates["G5_impactB_full_mean"] = {"got": round(m_impactB, 8), "expect": SQRT_1000W,
                                     "ok": abs(m_impactB - SQRT_1000W) < 1e-6}
    for k, g in gates.items():
        print(f"[S] 闸门 {k}: got={g['got']} expect={g['expect']} -> {'PASS' if g['ok'] else 'FAIL'}")
    if not all(g["ok"] for g in gates.values()):
        raise SystemExit("复现闸门未过，拒绝出结论")

    # ── 各序列 × 各分段统计 ──
    table: dict[str, dict] = {}
    for name, vals in S.items():
        if name in ("finv_1000w", "finv_3000w", "n_keep", "to",
                    "impact_A_pp", "impact_B_eta1_pp"):
            continue                      # 描述量另行处理
        for seg_name in ("2007-2015", "2016-2026"):
            sub = [v for v, g in zip(vals, segs) if g == seg_name]
            st = nw_stats_pp(sub)
            st["boot_ci95_pp"] = boot_ci_pp(sub)
            table[f"{name}|{seg_name}"] = st

    # 描述量：分段均值/中位数
    desc: dict[str, dict] = {}
    for name in ("finv_1000w", "finv_3000w", "n_keep", "to",
                 "impact_A_pp", "impact_B_eta1_pp"):
        for seg_name in ("2007-2015", "2016-2026"):
            sub = [v for v, g in zip(S[name], segs) if g == seg_name]
            desc[f"{name}|{seg_name}"] = {"mean": statistics.fmean(sub),
                                          "median": statistics.median(sub),
                                          "n": len(sub)}

    # ── 主检验判定 ──
    primary_stats = table[f"{PRIMARY}|2016-2026"]
    verdict = judge(primary_stats["mean_pp"], primary_stats["t_nw"])
    print()
    print("=" * 66)
    print(f"主检验：{PRIMARY} | 2016-2026（n={primary_stats['n']}）")
    print(f"  均值 = {primary_stats['mean_pp']:+.6f} pp/月")
    print(f"  se_nw = {primary_stats['se_nw_pp']:.6f} | se_iid = {primary_stats['se_iid_pp']:.6f}")
    print(f"  t_nw = {primary_stats['t_nw']:+.4f} | p = {primary_stats['p_nw']:.4f}")
    print(f"  MDE = {primary_stats['mde_pp']:.6f} pp | 事后功效 = {primary_stats['power_observed']:.3f}")
    print(f"  bootstrap 95% = [{primary_stats['boot_ci95_pp'][0]:+.6f}, "
          f"{primary_stats['boot_ci95_pp'][1]:+.6f}]")
    print(f"  判定 = {verdict['verdict']}")
    print("=" * 66)

    # ── 落盘（一律 LF：Windows 文本模式会把 \n 写成 \r\n，
    #    与 git autocrlf 归一后的 blob 不一致 → API 推送会树不一致） ──
    os.makedirs(OUT_DIR, exist_ok=True)
    out_csv = os.path.join(OUT_DIR, "monthly.csv")
    cols = (["signal_date", "segment"] + list(S.keys()))
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for i, d in enumerate(dates):
            w.writerow([d, segs[i]] + [round(S[c][i], 10) for c in S])

    summary = {
        "experiment": "phaseS-exq5-modern-era-v1",
        "prereg_sha256": PREREG_SHA256,
        "kind": "确认性检验（exQ5 结项：现代段成本后 alpha 可检出性）",
        "inputs": {"phase_o_csv": {"path": O_CSV, "sha256": O_SHA256},
                   "phase_j_csv": {"path": J_CSV, "sha256": J_SHA256}},
        "frozen": {"split_date": SPLIT_DATE, "nw_lag": NW_LAG, "t_crit": T_CRIT,
                   "z_power80": Z_POWER80, "boot_n": BOOT_N, "boot_seed": BOOT_SEED,
                   "impact_upper": IMPACT_UPPER, "sqrt_3000w": SQRT_3000W,
                   "sqrt_1000w": SQRT_1000W, "scale_3m_over_1m": SCALE_3M_OVER_1M,
                   "primary_formula": "net_S_3000w = net_BASE_3000w + 0.00078376 - 0.01899561"},
        "reproduction_gates": gates,
        "primary": {"series": PRIMARY, "segment": "2016-2026", **primary_stats,
                    "verdict": verdict["verdict"]},
        "table": table,
        "descriptives": desc,
        "honest_limits": [
            "主序列的 √ 冲击用常数（3,000 万档无逐月序列）；已核实该常数高估后段成本"
            "（后段逐月 √ 均值仅为前段 35%），故主检验偏保守",
            "缩放系数 1.6792901856 是两档常数之比，副检验 4 的逐月 3,000 万序列是近似",
            "显著 ≠ 可交易（纪律 7）：样本内、无盘中数据、单一冲击模型",
            "现代段现金残留 7.2%@3,000 万 / 14.5%@1,000 万（见 descriptives）",
            "本阶段不检验'是否衰减'（那是阶段 P 的差值问题），只检验现代段单独可检出性",
        ],
        "runtime_sec": round(time.time() - t0, 1),
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", newline="\n",
              encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)

    man = {"experiment": "phaseS-exq5-modern-era-v1",
           "prereg_sha256": PREREG_SHA256,
           "script_sha256": sha256_file(os.path.abspath(__file__)),
           "files": {}, "runtime_sec": round(time.time() - t0, 1)}
    for fn in ("monthly.csv", "summary.json"):
        p = os.path.join(OUT_DIR, fn)
        man["files"][fn] = {"sha256": sha256_file(p), "bytes": os.path.getsize(p)}
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", newline="\n",
              encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=1)

    print(f"\n[S] 产物已写入 {OUT_DIR}/（runtime {man['runtime_sec']}s）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
