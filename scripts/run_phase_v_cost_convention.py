"""阶段 V：成本口径一致性（等权再平衡）。

唯一变量：把每月成本从「仅成分变动」（阶段 O）换成「等权再平衡」（A5 台账口径）。
其余全部沿用阶段 O / S 的冻结值。

预登记：docs/PHASE_V_COST_CONVENTION_PREREG_2026-10-23.md（启动校验 sha256）
产物：phasev_out/<预登记哈希前12位>/

用法：python scripts/run_phase_v_cost_convention.py
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import statistics

# ── 冻结项（预登记 §3、§4） ──────────────────────────────────────
PREREG = "docs/PHASE_V_COST_CONVENTION_PREREG_2026-10-23.md"
PREREG_SHA256 = "3614e12a0546249f7857d2e1146069d13bc14e5acec417b1cb80089c916546fe"

O_CSV = os.path.join("phaseo_out", "ac7aa2830ab0", "monthly.csv")
O_SHA256 = "768e49a9462cd7c00f3aacfcdb1e87bbe37bb75b348cd6fd85809e12493a4152"

REB_BASE_CSV = os.path.join("research", "exq5_audit_2026-10-22", "out",
                            "a5_ledger", "monthly_BASE_rebalance.csv")
REB_BASE_SHA256 = "565295c6ac6d6e0205f23d72d154678b0457646837f582ae5724e0efb849b998"

REB_CONS_CSV = os.path.join("research", "exq5_audit_2026-10-22", "out",
                            "a5_ledger", "monthly_CONSERVATIVE_rebalance.csv")
REB_CONS_SHA256 = "d40f15b2f53e9fcd587278d8aca4bc57f4327dfe8459eb65458242d0649e75d6"

A5_SUMMARY = os.path.join("research", "exq5_audit_2026-10-22", "out",
                          "a5_ledger", "summary.json")
A5_SUMMARY_SHA256 = "7f3fa14a4a242303ff1f7c366aa2107a523a77beaad6764dde0c8abf1b95cbf6"

IMPACT_UPPER = 0.00078376     # Amihud @3000万，pp/月
SQRT_3000W = 0.01899561       # √η=1 @3000万，pp/月
NW_LAG = 3
T_CRIT = 1.96
Z_POWER80 = 2.8016
MODERN_LO = "2016-01-01"
DROP6 = ("2016-01", "2016-02", "2020-02", "2020-03", "2024-01", "2024-02")
FIRST_MONTH = "2007-01-31"     # 首月成本置 0（沿用阶段 O 冻结约定，见预登记 §2）

# 阶段 S 已记录值（G4 复现闸门）
S_RECORD_MEAN = 0.111196
S_RECORD_T = 2.2180

OUT_DIR = os.path.join("phasev_out", PREREG_SHA256[:12])
# 阶段 O 的 monthly.csv 一律保留 6 位小数，每列舍入误差 ≤ 5e-7。
# 复算差的上界（忽略二阶项）：
#   |Δ(finv×alpha)| ≤ 5e-7×|alpha| + 5e-7×|finv| + 5e-7     （|alpha| 最大约 3.5）
#   + 5e-7（cost） + 5e-7（net）= 约 3.2e-6 pp
# 实测最大值 3.19e-6 与该推导一致 → 容差取 5e-6，而不是 1e-9。
ROUND_TOL = 5e-6


# ── 基础工具 ────────────────────────────────────────────────────
def sha256_file(path: str) -> str:
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def verify() -> None:
    if not os.path.exists(PREREG):
        raise SystemExit(f"预登记缺失：{PREREG}")
    h = sha256_file(PREREG)
    if h != PREREG_SHA256:
        raise SystemExit(f"预登记哈希不符！\n  期望 {PREREG_SHA256}\n  实际 {h}")
    print(f"[V] 预登记校验通过 sha256={h[:12]}")


def read_csv_rows(path: str) -> list[dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def power_two_sided(t: float) -> float:
    return norm_cdf(abs(t) - T_CRIT) + norm_cdf(-abs(t) - T_CRIT)


# ── NW 估计（独立实现，不调用项目代码） ─────────────────────────
def nw_profile(x: list[float], lags=range(0, 7)) -> dict[int, dict]:
    n = len(x)
    if n < 2:
        raise SystemExit("样本不足")
    m = statistics.fmean(x)
    e = [v - m for v in x]
    g0 = sum(v * v for v in e) / n
    out: dict[int, dict] = {}
    sd = statistics.pstdev(x) * math.sqrt(n / (n - 1))
    se_iid = sd / math.sqrt(n)
    for L in lags:
        s = g0
        for l in range(1, L + 1):
            w = 1.0 - l / (L + 1.0)
            gl = sum(e[i] * e[i - l] for i in range(l, n)) / n
            s += 2.0 * w * gl
        fell_back = False
        if s <= 0.0:
            s = g0
            fell_back = True
        se = math.sqrt(s / n)
        out[L] = {"mean": m, "se_nw": se, "t_nw": m / se,
                  "se_iid": se_iid, "t_iid": m / se_iid,
                  "fell_back": fell_back, "n": n}
    return out


def stats_of(x: list[float]) -> dict:
    g = nw_profile(x)
    p = g[NW_LAG]
    return {"n": p["n"], "mean_pp": p["mean"], "se_nw": p["se_nw"],
            "t_nw": p["t_nw"], "t_iid": p["t_iid"],
            "mde_pp": Z_POWER80 * p["se_nw"],
            "power": power_two_sided(p["t_nw"]),
            "grid_t": {L: g[L]["t_nw"] for L in g}}


def grid_significant(x: list[float]) -> list[int]:
    g = nw_profile(x)
    return [L for L in sorted(g) if g[L]["t_nw"] > T_CRIT and g[L]["mean"] > 0]


def month_key(d: str) -> str:
    return d[:7]


def main() -> None:
    verify()
    for path, want in ((O_CSV, O_SHA256), (REB_BASE_CSV, REB_BASE_SHA256),
                       (REB_CONS_CSV, REB_CONS_SHA256),
                       (A5_SUMMARY, A5_SUMMARY_SHA256)):
        got = sha256_file(path)
        if got != want:
            raise SystemExit(f"输入哈希不符：{path}\n  期望 {want}\n  实际 {got}")
        print(f"[V] 冻结输入 OK {os.path.basename(path):42s} {got[:12]}")

    o = read_csv_rows(O_CSV)
    reb_b = {r["signal_date"]: float(r["fee_pp"]) for r in read_csv_rows(REB_BASE_CSV)}
    reb_c = {r["signal_date"]: float(r["fee_pp"]) for r in read_csv_rows(REB_CONS_CSV)}
    a5 = json.load(open(A5_SUMMARY, encoding="utf-8"))

    # ── 构造序列 ────────────────────────────────────────────────
    rec: list[dict] = []
    for r in o:
        d = r["signal_date"]
        a = float(r["alpha_pp"])
        f3 = float(r["finv_3000w"])
        c_o_b = float(r["cost_BASE_pp"])
        c_o_c = float(r["cost_CONS_pp"])
        net_o_b = float(r["net_BASE_3000w"])
        net_o_c = float(r["net_CONS_3000w"])

        if d == FIRST_MONTH:
            c_v_b = c_v_c = 0.0
        else:
            c_v_b, c_v_c = reb_b[d], reb_c[d]

        net_v_b = f3 * a - c_v_b - IMPACT_UPPER
        net_v_c = f3 * a - c_v_c - IMPACT_UPPER
        net_v_s = net_v_b + IMPACT_UPPER - SQRT_3000W

        rec.append({"d": d, "alpha_pp": a, "finv": f3,
                    "c_o_b": c_o_b, "c_v_b": c_v_b,
                    "c_o_c": c_o_c, "c_v_c": c_v_c,
                    "net_o_b": net_o_b, "net_o_c": net_o_c,
                    "net_v_b": net_v_b, "net_v_c": net_v_c,
                    "net_v_s": net_v_s,
                    "net_o_s": net_o_b + IMPACT_UPPER - SQRT_3000W,
                    "net_v_s_cons": net_v_c + IMPACT_UPPER - SQRT_3000W,
                    "net_o_s_cons": net_o_c + IMPACT_UPPER - SQRT_3000W})

    n = len(rec)
    print(f"[V] 样本 {n} 月  {rec[0]['d']} … {rec[-1]['d']}")

    gates: list[dict] = []

    # ── G1：用 O 的成本列复现 O 的净值 ─────────────────────────
    g1b = max(abs(r["net_o_b"] - (r["finv"] * r["alpha_pp"] - r["c_o_b"] - IMPACT_UPPER))
              for r in rec)
    g1c = max(abs(r["net_o_c"] - (r["finv"] * r["alpha_pp"] - r["c_o_c"] - IMPACT_UPPER))
              for r in rec)
    gates.append({"id": "G1", "name": "复现 O 净值（成本列 + finv + 冲击常数）",
                  "base_max_abs_diff": g1b, "cons_max_abs_diff": g1c,
                  "tol": ROUND_TOL,
                  "ok": g1b < ROUND_TOL and g1c < ROUND_TOL})

    # ── G2：成本序列对账 ───────────────────────────────────────
    c_v_b_series = [r["c_v_b"] for r in rec]
    c_v_c_series = [r["c_v_c"] for r in rec]
    m_ex_b = statistics.fmean(c_v_b_series[1:])          # 剔首月
    m_ex_c = statistics.fmean(c_v_c_series[1:])
    want_b = a5["modes"]["rebalance"]["BASE"]["mean_fee_ex_first_pp"]
    want_c = a5["modes"]["rebalance"]["CONSERVATIVE"]["mean_fee_ex_first_pp"]
    g2 = {
        "id": "G2", "name": "成本序列与 A5 对账汇总一致",
        "first_month_zero": c_v_b_series[0] == 0.0 and c_v_c_series[0] == 0.0,
        "n_months": n, "any_negative": any(v < 0 for v in c_v_b_series + c_v_c_series),
        "base_mean_ex_first": m_ex_b, "base_expected": want_b,
        "base_rel_diff": abs(m_ex_b - want_b) / want_b,
        "cons_mean_ex_first": m_ex_c, "cons_expected": want_c,
        "cons_rel_diff": abs(m_ex_c - want_c) / want_c,
    }
    g2["ok"] = (g2["first_month_zero"] and not g2["any_negative"]
                and n == a5["n_months"]
                and g2["base_rel_diff"] < 1e-9 and g2["cons_rel_diff"] < 1e-9)
    gates.append(g2)

    # ── G3：恒等 + 量级（两条一起校） ─────────────────────────
    d_id_b = max(abs((r["net_v_b"] - r["net_o_b"]) + (r["c_v_b"] - r["c_o_b"]))
                 for r in rec)
    d_id_c = max(abs((r["net_v_c"] - r["net_o_c"]) + (r["c_v_c"] - r["c_o_c"]))
                 for r in rec)
    mean_delta_b = statistics.fmean(r["c_v_b"] - r["c_o_b"] for r in rec)
    mean_delta_c = statistics.fmean(r["c_v_c"] - r["c_o_c"] for r in rec)
    mean_net_diff_b = statistics.fmean(r["net_v_b"] - r["net_o_b"] for r in rec)
    mean_net_diff_c = statistics.fmean(r["net_v_c"] - r["net_o_c"] for r in rec)
    g3 = {"id": "G3", "name": "净值差恒等于成本差（逐月）+ 均值量级一致",
          "identity_max_abs_diff_base": d_id_b,
          "identity_max_abs_diff_cons": d_id_c,
          "mean_cost_delta_pp_base": mean_delta_b,
          "mean_net_delta_pp_base": mean_net_diff_b,
          "mean_scale_diff_base": abs(mean_net_diff_b + mean_delta_b),
          "mean_cost_delta_pp_cons": mean_delta_c,
          "mean_net_delta_pp_cons": mean_net_diff_c,
          "mean_scale_diff_cons": abs(mean_net_diff_c + mean_delta_c),
          "tol": ROUND_TOL}
    g3["ok"] = (d_id_b < ROUND_TOL and d_id_c < ROUND_TOL
                and g3["mean_scale_diff_base"] < ROUND_TOL
                and g3["mean_scale_diff_cons"] < ROUND_TOL)
    gates.append(g3)

    # ── G4：复现阶段 S 头条 ────────────────────────────────────
    s_o = [r["net_o_s"] for r in rec if r["d"] >= MODERN_LO]
    st_s_o = stats_of(s_o)
    g4 = {"id": "G4", "name": "用冻结成本列复现阶段 S 头条",
          "mean_pp": st_s_o["mean_pp"], "mean_expected": S_RECORD_MEAN,
          "abs_diff_mean": abs(st_s_o["mean_pp"] - S_RECORD_MEAN),
          "t_nw": st_s_o["t_nw"], "t_expected": S_RECORD_T,
          "abs_diff_t": abs(st_s_o["t_nw"] - S_RECORD_T)}
    g4["ok"] = g4["abs_diff_mean"] < 1e-5 and g4["abs_diff_t"] < 1e-3
    gates.append(g4)

    # ── G5：单位自检（t 尺度无关、均值差 100 倍） ─────────────
    g5 = {"id": "G5", "name": "pp / 小数 单位自检"}
    # 同一序列整体除以 100（pp → 小数）：t 必须逐位不变，均值必须正好差 100 倍。
    s_v = [r["net_v_s"] for r in rec if r["d"] >= MODERN_LO]
    st_v = stats_of(s_v)
    s_dec = [v / 100.0 for v in s_v]
    st_dec = stats_of(s_dec)
    g5["t_pp"] = st_v["t_nw"]
    g5["t_dec"] = st_dec["t_nw"]
    g5["mean_pp"] = st_v["mean_pp"]
    g5["mean_dec"] = st_dec["mean_pp"]
    g5["t_abs_diff"] = abs(g5["t_pp"] - g5["t_dec"])
    g5["mean_ratio"] = g5["mean_pp"] / g5["mean_dec"]
    g5["ok"] = g5["t_abs_diff"] < 1e-9 and abs(g5["mean_ratio"] - 100.0) < 1e-6
    gates.append(g5)

    # ── 四个承重读数 ───────────────────────────────────────────
    def window(rows, key, drop6=False):
        out = [r[key] for r in rows if r["d"] >= MODERN_LO]
        if drop6:
            out = [r[key] for r in rows
                   if r["d"] >= MODERN_LO and month_key(r["d"]) not in DROP6]
        return out

    full_b = [r["net_v_b"] for r in rec]
    full_c = [r["net_v_c"] for r in rec]
    full_ob = [r["net_o_b"] for r in rec]
    full_oc = [r["net_o_c"] for r in rec]
    mod_s = window(rec, "net_v_s")
    mod_s_o = window(rec, "net_o_s")
    t1a = window(rec, "net_v_s", drop6=True)
    t1a_o = window(rec, "net_o_s", drop6=True)

    mod_s_cons = [r["net_v_s_cons"] for r in rec if r["d"] >= MODERN_LO]
    mod_s_cons_o = [r["net_o_s_cons"] for r in rec if r["d"] >= MODERN_LO]
    R = {
        "R1": {"label": "O 口径 BASE@3000万 全样本", "new": stats_of(full_b),
               "old": stats_of(full_ob)},
        "R2": {"label": "O 口径 CONS@3000万 全样本", "new": stats_of(full_c),
               "old": stats_of(full_oc)},
        "R3": {"label": "S 口径 net_S 后段", "new": stats_of(mod_s),
               "old": stats_of(mod_s_o)},
        "R4": {"label": "T1a 后段剔 DROP6", "new": stats_of(t1a),
               "old": stats_of(t1a_o)},
        "R5": {"label": "诊断：S 口径 CONS 成本 后段（不参与判定）",
               "new": stats_of(mod_s_cons), "old": stats_of(mod_s_cons_o)},
    }

    # ── 判定 ───────────────────────────────────────────────────
    A = R["R1"]["new"]["mean_pp"] > 0 and R["R1"]["new"]["t_nw"] > T_CRIT
    B = R["R3"]["new"]["mean_pp"] > 0 and R["R3"]["new"]["t_nw"] > T_CRIT
    C = R["R4"]["new"]["t_nw"] < T_CRIT
    U_EXQ5_GRID = {  # U 已记录的 exQ5 承重序列在 L=0..6 中显著的滞后集合
        "R1": list(range(0, 7)),
        "R3": [L for L in range(0, 7) if L >= 2],
        "R4": [L for L in range(0, 7) if L >= 4],
    }
    grids = {"R1": grid_significant(full_b),
             "R3": grid_significant(mod_s),
             "R4": grid_significant(t1a)}
    D = all(grids[k] == U_EXQ5_GRID[k] for k in U_EXQ5_GRID)

    verdict = "COST_CONVENTION_ROBUST" if (A and B and C and D) else "COST_CONVENTION_SENSITIVE"

    # ── 翻转额度（成本还能涨多少才翻转） ──────────────────────
    s_new = R["R3"]["new"]
    se = s_new["se_nw"]
    delta_star = s_new["mean_pp"] - T_CRIT * se            # 允许的额外成本 pp/月
    cost_now = statistics.fmean([r["c_v_b"] for r in rec if r["d"] >= MODERN_LO])
    headroom = {
        "delta_star_pp_per_month": delta_star,
        "cost_now_pp_per_month": cost_now,
        "cost_could_rise_pct": delta_star / cost_now * 100.0 if cost_now > 0 else None,
        "note": "在 se 不变的近似下，额外月度成本超过 delta_star 时阶段 S 的判定翻转",
    }

    # ── 输出 ───────────────────────────────────────────────────
    os.makedirs(OUT_DIR, exist_ok=True)
    cols = ["signal_date", "alpha_pp", "cost_O_BASE_pp", "cost_V_BASE_pp",
            "cost_O_CONS_pp", "cost_V_CONS_pp", "net_O_BASE_3000w",
            "net_V_BASE_3000w", "net_O_CONS_3000w", "net_V_CONS_3000w",
            "net_O_S_3000w", "net_V_S_3000w",
            "net_O_S_cons_3000w", "net_V_S_cons_3000w"]
    with open(os.path.join(OUT_DIR, "monthly.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(cols)
        for r in rec:
            w.writerow([r["d"], f"{r['alpha_pp']:.6f}",
                        f"{r['c_o_b']:.6f}", f"{r['c_v_b']:.6f}",
                        f"{r['c_o_c']:.6f}", f"{r['c_v_c']:.6f}",
                        f"{r['net_o_b']:.6f}", f"{r['net_v_b']:.6f}",
                        f"{r['net_o_c']:.6f}", f"{r['net_v_c']:.6f}",
                        f"{r['net_o_s']:.6f}", f"{r['net_v_s']:.6f}",
                        f"{r['net_o_s_cons']:.6f}", f"{r['net_v_s_cons']:.6f}"])

    summary = {
        "experiment": "阶段 V：成本口径一致性（等权再平衡）",
        "prereg_sha256": PREREG_SHA256,
        "kind": "cost_convention_consistency",
        "single_variable": "成本口径：仅成分变动 → 等权再平衡（首月成本仍置 0）",
        "inputs": {O_CSV: O_SHA256, REB_BASE_CSV: REB_BASE_SHA256,
                   REB_CONS_CSV: REB_CONS_SHA256, A5_SUMMARY: A5_SUMMARY_SHA256},
        "constants": {"IMPACT_UPPER": IMPACT_UPPER, "SQRT_3000W": SQRT_3000W,
                      "NW_LAG": NW_LAG, "T_CRIT": T_CRIT, "Z_POWER80": Z_POWER80,
                      "MODERN_LO": MODERN_LO, "DROP6": list(DROP6),
                      "W": 3.0e7},
        "n_months": n,
        "cost_means": {"cost_O_BASE_pp": statistics.fmean(r["c_o_b"] for r in rec),
                       "cost_V_BASE_pp": statistics.fmean(c_v_b_series),
                       "cost_O_CONS_pp": statistics.fmean(r["c_o_c"] for r in rec),
                       "cost_V_CONS_pp": statistics.fmean(c_v_c_series),
                       "delta_BASE_pp": mean_delta_b, "delta_CONS_pp": mean_delta_c,
                       "base_ratio": statistics.fmean(c_v_b_series) /
                       statistics.fmean(r["c_o_b"] for r in rec)},
        "gates": gates,
        "readings": {k: {"label": v["label"],
                         "new": v["new"], "old": v["old"],
                         "mean_change_pp": v["new"]["mean_pp"] - v["old"]["mean_pp"],
                         "t_change": v["new"]["t_nw"] - v["old"]["t_nw"]}
                     for k, v in R.items()},
        "criteria": {
            "A_O_base_significant": bool(A),
            "B_S_modern_significant": bool(B),
            "C_T_still_fragile": bool(C),
            "D_U_grid_unchanged": bool(D),
            "grids_new": grids,
            "grids_expected": U_EXQ5_GRID,
            "verdict": verdict,
        },
        "headroom": headroom,
        "limits": [
            "再平衡成本是下界：A5 台账中 101,784 次增量买入因不足一手被跳过 → 真实等权再平衡成本 ≥ 所用值，故本实验净值为上界。",
            "未建模项与阶段 O 一致（买卖价差、T+1 缺口、持有期退市、ST 5% 漏判），不构成本实验的差异。",
            "不尝试介于 drift 与 rebalance 之间的部分再平衡比例（那会是新的自由度）。",
            "不改写历史报告：阶段 O/S/T 的判定与数字原样保留，本实验只出更正说明。",
            "固定 3,000 万，不能外推其他资金规模。",
        ],
    }
    with open(os.path.join(OUT_DIR, "summary.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # ── manifest（产物指纹，供证据索引引用） ──────────────────
    files = {}
    for name in ("monthly.csv", "summary.json"):
        fp = os.path.join(OUT_DIR, name)
        files[name] = {"sha256": sha256_file(fp),
                       "bytes": os.path.getsize(fp)}
    manifest = {
        "experiment": "阶段 V：成本口径一致性（等权再平衡）",
        "prereg": PREREG,
        "prereg_sha256": PREREG_SHA256,
        "run_dir": OUT_DIR.replace(os.sep, "/"),
        "n_months": n,
        "window": [rec[0]["d"], rec[-1]["d"]],
        "inputs": {k.replace(os.sep, "/"): v
                   for k, v in ((O_CSV, O_SHA256), (REB_BASE_CSV, REB_BASE_SHA256),
                                (REB_CONS_CSV, REB_CONS_SHA256),
                                (A5_SUMMARY, A5_SUMMARY_SHA256))},
        "gates_all_pass": all(g["ok"] for g in gates),
        "verdict": verdict,
        "files": files,
    }
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # ── 打印 ───────────────────────────────────────────────────
    print("\n" + "=" * 74)
    print("闸门")
    for g in gates:
        print(f"  {g['id']} {'PASS' if g['ok'] else 'FAIL'}  {g['name']}")
    print("\n成本（pp/月）")
    print(f"  BASE  O {summary['cost_means']['cost_O_BASE_pp']:.6f}"
          f"  V {summary['cost_means']['cost_V_BASE_pp']:.6f}"
          f"  Δ +{mean_delta_b:.6f}  ({summary['cost_means']['base_ratio']:.4f}×)")
    print(f"  CONS  O {summary['cost_means']['cost_O_CONS_pp']:.6f}"
          f"  V {summary['cost_means']['cost_V_CONS_pp']:.6f}"
          f"  Δ +{mean_delta_c:.6f}")
    print("\n四个承重读数")
    for k, v in R.items():
        o, nn = v["old"], v["new"]
        print(f"  {k} {v['label']}")
        print(f"      O 口径 {o['mean_pp']:+.6f}pp  t={o['t_nw']:+.4f}  n={o['n']}")
        print(f"      V 口径 {nn['mean_pp']:+.6f}pp  t={nn['t_nw']:+.4f}"
              f"  ({nn['mean_pp']-o['mean_pp']:+.6f}pp, t{nn['t_nw']-o['t_nw']:+.4f})"
              f"  MDE {nn['mde_pp']:.4f} 功效 {nn['power']:.3f}")
        print(f"      旧 L 网格 " + " ".join(f"L{L}:{t:+.2f}"
                                           for L, t in o["grid_t"].items()))
        print(f"      新 L 网格 " + " ".join(f"L{L}:{t:+.2f}"
                                           for L, t in nn["grid_t"].items()))
    print("\n判定")
    print(f"  A O BASE 显著        : {A}")
    print(f"  B S 现代段显著       : {B}")
    print(f"  C T 仍 FRAGILE       : {C}")
    print(f"  D U 网格不变         : {D}  新 {grids} / 期望 {U_EXQ5_GRID}")
    print(f"  → {verdict}")
    print(f"\n翻转额度：Δ* = {delta_star:.6f} pp/月"
          f"（现行成本 {cost_now:.6f}，可再涨 {headroom['cost_could_rise_pct']:.1f}%）")
    print(f"\n产物 {OUT_DIR}/")
    print("=" * 74)

    if not all(g["ok"] for g in gates):
        raise SystemExit("闸门未全部通过，结论不可引用")


if __name__ == "__main__":
    main()
