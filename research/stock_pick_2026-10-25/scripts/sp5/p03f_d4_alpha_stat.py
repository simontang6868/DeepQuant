# -*- coding: utf-8 -*-
"""统计口径审计（回答专家第 5 轮 P0/P1）：t 在哪一级算的、σ 到底是哪个。

回答四个问题：
  1. t=5.43 / MDE=0.4516 隐含的 σ 是多少？（反推 + 实测对照）
  2. t 在"月"上还是"路径-槽位"上算的？（直接打印 len）
  3. NW t 随 lag 的跳升来自哪一阶自相关？（lag=0/1/2/3/6/12 全打印）
  4. 四列口径表：绝对/超额 × 路径平均/单路径，每列 μ σ ρ t_iid t_NW n(t=2) n(MDE)

⚠️ 只读、不改既有产物；输出 `d4_alpha_out/a1801ece1ad7/stat.json`。
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RD = HERE.parents[1]
REPO = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

import p03d_d4_strict as p03d  # noqa: E402
import run_phase_c as C  # noqa: E402

OUT = RD / "d4_alpha_out" / "a1801ece1ad7"
MODERN = "20160101"
LAGS = (0, 1, 2, 3, 6, 12)


def stats_of(x: np.ndarray) -> dict:
    """x: 一维月度序列。返回 μ、σ、自相关、各 lag 的 t、样本量需求。"""
    x = np.asarray(x, float)
    x = x[np.isfinite(x)]
    n = len(x)
    mu = float(np.mean(x))
    sd = float(np.std(x, ddof=1))
    se_iid = sd / math.sqrt(n)
    t_iid = mu / se_iid if se_iid > 0 else float("nan")

    out = {
        "n": n, "mu_pp": mu * 100, "sigma_pp": sd * 100,
        "se_iid_pp": se_iid * 100, "t_iid": t_iid,
        "rho": {str(k): float(np.corrcoef(x[:-k], x[k:])[0, 1]) for k in LAGS if k > 0},
        "t_by_lag": {}, "se_by_lag_pp": {},
    }
    for lag in LAGS:
        if lag == 0:
            se, t = se_iid, t_iid
        else:
            r = C.newey_west(list(map(float, x)), lag)
            se, t = r["se_nw"], r["t_nw"]
        out["se_by_lag_pp"][str(lag)] = se * 100
        out["t_by_lag"][str(lag)] = t

    # 样本量需求：μ 与 σ 同比例按仓位缩放 ⇒ n 与仓位无关（只在 100% 口径报一次）
    out["n_for_t2"] = (2.0 * sd / mu) ** 2 if mu > 0 else float("nan")
    out["n_for_mde_eq_mu"] = (2.8016 * sd / mu) ** 2 if mu > 0 else float("nan")
    return out


def main() -> int:
    panel = pd.read_parquet(p03d.PANEL)
    by, months, paths, ns_list, monthly, _ = p03d.build_decisions(panel, 200, "d4")
    bench = np.asarray([float(np.mean(by[d]["fwd"])) for d in months], float)
    sel = np.asarray([m >= MODERN for m in months])

    M = np.vstack([np.asarray(r, float) for r in monthly])   # (200,257) 绝对净收益
    A = M - bench                                             # 超额

    # ---- 层级证据 ----
    levels = {
        "len_monthly_excess_series": int(sel.sum()),          # 128（路径平均后）
        "len_monthly_excess_full": int(len(months)),          # 257
        "len_path_level_returns": int(sum(len(x) for x in monthly)),  # 51,400
        "n_paths": int(len(monthly)),
        "unique_hold_slots": 89619,       # 来自 audit.json，非本脚本重算
        "path_slots": 257000,
        "t_computed_on": "月度序列（路径先平均）",
        "t_not_computed_on": "路径-槽位 / 唯一槽位",
        "implication": "t 不是按 51,400 或 257,000 个观测算的；√2.87 膨胀不适用",
    }

    # ---- 四列口径 ----
    series = {
        "绝对_路径平均": M.mean(axis=0)[sel],
        "超额_路径平均": A.mean(axis=0)[sel],
        "绝对_单路径": np.median(M[:, sel], axis=0),
        "超额_单路径": np.median(A[:, sel], axis=0),
    }
    # 单账户的典型 σ：每条路径各自的时间 σ，再取中位（≠"中位数路径序列"的 σ）
    single_path_sigma = {
        "绝对": float(np.median([np.std(row[sel], ddof=1) for row in M])),
        "超额": float(np.median([np.std(row[sel], ddof=1) for row in A])),
    }
    table = {k: stats_of(v) for k, v in series.items()}
    # ⚠️ "逐月中位序列"是每月从不同路径取中位拼成的**虚构组合**，不是任何一条真实路径。
    #    它的 μ 不能用于单账户推断；单账户应配"路径 μ 的均值"(见 path_heterogeneity)。
    for k in ("绝对_单路径", "超额_单路径"):
        table[k]["warning"] = ("逐月中位拼成的虚构组合，非真实路径；"
                               "μ 不可用于单账户样本量推断，只用其 σ 作为单账户典型波动的近似来源")
    for tag, sd in single_path_sigma.items():
        s = table[f"{tag}_单路径"]
        s["sigma_note"] = ("上面 sigma_pp 是'跨路径取中位后序列'的波动；"
                           "实盘单账户应使用本字段 sigma_single_account_pp")
        s["sigma_single_account_pp"] = sd * 100
        mu = s["mu_pp"] / 100
        s["n_for_t2_single_account"] = (2.0 * sd / mu) ** 2 if mu > 0 else float("nan")
        s["n_for_mde_single_account"] = (2.8016 * sd / mu) ** 2 if mu > 0 else float("nan")

    # ---- 路径间异质性：单账户可能落在哪条路径上 ----
    path_mu = A[:, sel].mean(axis=1)             # 200 条路径各自的现代段月均超额
    path_sigma = np.median([np.std(row[sel], ddof=1) for row in A])
    path_hetero = {
        "n_paths": int(len(path_mu)),
        "mean_pp": float(np.mean(path_mu)) * 100,
        "percentiles_pp": {str(q): float(np.percentile(path_mu, q)) * 100
                           for q in (5, 25, 50, 75, 95)},
        "share_negative": float(np.mean(path_mu < 0)),
        "note": "期望 μ=0.946pp 是 200 条路径的均值；单账户只走一条，中位数路径只有 0.3pp 量级",
    }

    # ---- 实盘可观测性：**期望 μ**（不是中位数路径 μ）配**单账户 σ** ----
    mu_expect_c1 = float(np.mean(A[:, sel])) * 0.20 * 0.2   # C1 成本加成近似
    # 更精确：从 summary 的 C1 modern 取 0.876pp；此处用 0.876*0.2
    mu_c1_100 = 0.00876
    mu_c1_20 = mu_c1_100 * 0.20
    sd20 = single_path_sigma["超额"] * 0.20
    live = {
        "mu_100pct_pp": mu_c1_100 * 100,
        "mu_20pct_pp": mu_c1_20 * 100,
        "sigma_20pct_pp": sd20 * 100,
        "n_t2": (2.0 * sd20 / mu_c1_20) ** 2,
        "n_mde": (2.8016 * sd20 / mu_c1_20) ** 2,
        "note": "μ 用期望值（200 路径均值，C1 口径），σ 用单账户典型波动。"
                "这是'单账户多久能测出期望 alpha'的正确配对，"
                "不是 1066 月（那用了中位数路径的 μ，是条件量、非无偏估计）",
    }

    # ---- t=5.43 反推 ----
    mde_req = 0.4516 / 100       # 0.4516pp -> 小数
    se_req = mde_req / 2.8016
    official = table["超额_路径平均"]
    implied = {
        "reported_t": 5.43, "reported_mde_pp": 0.4516,
        "implied_se_nw_pp": se_req * 100,
        "implied_sigma_if_n128_noNW_pp": se_req * math.sqrt(128) * 100,
        "implied_sigma_if_n128_withNW_pp": se_req * math.sqrt(128)
                                            / (official["se_by_lag_pp"]["3"]
                                               / (official["sigma_pp"] / math.sqrt(128))) * 100,
        "implied_sigma_if_n237_pp": se_req * math.sqrt(237) * 100,
        "measured_sigma_excess_pathavg_pp": official["sigma_pp"],
        "measured_t_excess_pathavg_NW3": official["t_by_lag"]["3"],
        "conclusion": ("t=5.43/MDE=0.4516 对应的是'超额·路径平均'序列（n=128, σ≈2.02%）"
                       "；σ=4.88% 是单账户典型波动，属另一个口径，不得互推"),
    }

    res = {
        "schema": "p03f_d4_alpha_stat/v1",
        "prereg_sha256": "a1801ece1ad7",
        "note": "统计口径审计；不改既有产物，不参与预登记判定",
        "level_evidence": levels,
        "table": table,
        "single_account_sigma": single_path_sigma,
        "t543_reverse": implied,
        "official_alpha_test": {
            "series": "超额_路径平均",
            "n": official["n"], "sigma_pp": official["sigma_pp"],
            "t_iid": official["t_iid"], "t_nw3": official["t_by_lag"]["3"],
            "use": "判定'信号是否存在'（研究结论）",
            "not_for": "不得用它的 n(t=2)=18 推断实盘观察期——实盘只有一条路径",
        },
        "path_heterogeneity": path_hetero,
        "official_live_observability": live,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stat.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    print("=== 层级证据 ===")
    for k, v in levels.items():
        print(f"  {k} = {v}")
    print("\n=== 四列口径（现代段 n=128；20% 仓的 μ/σ 同乘 0.2，t 与 n 不变）===")
    for name, r in table.items():
        print(f"  {name:<14} μ={r['mu_pp']:+.4f}pp σ={r['sigma_pp']:.4f}% "
              f"ρ1={r['rho']['1']:+.4f} t_iid={r['t_iid']:+.3f} t_NW3={r['t_by_lag']['3']:+.3f} "
              f"n(t2)={r['n_for_t2']:.0f} n(MDE)={r['n_for_mde_eq_mu']:.0f}")
        if "sigma_single_account_pp" in r:
            print(f"    单账户 σ={r['sigma_single_account_pp']:.4f}% ⇒ "
                  f"n(t2)={r['n_for_t2_single_account']:.0f} "
                  f"n(MDE)={r['n_for_mde_single_account']:.0f}")
    print("\n=== NW lag 敏感性（超额·路径平均）===")
    print("  lag  : " + "  ".join(f"{l:>6}" for l in LAGS))
    print("  t    : " + "  ".join(f"{official['t_by_lag'][str(l)]:+6.3f}" for l in LAGS))
    print("  rho  : " + "  ".join(f"{official['rho'].get(str(l), float('nan')):+6.3f}" for l in LAGS))
    print("\n=== 路径间异质性（200 条路径各自的月均超额）===")
    print(f"  均值 {path_hetero['mean_pp']:+.4f}pp｜分位 "
          + " ".join(f"p{q}={v:+.3f}" for q, v in path_hetero["percentiles_pp"].items())
          + f"｜负值占 {path_hetero['share_negative']*100:.1f}%")
    print(f"\n=== 实盘可观测性（期望 μ + 单账户 σ）===")
    print(f"  μ20={live['mu_20pct_pp']:.4f}pp σ20={live['sigma_20pct_pp']:.4f}% "
          f"⇒ n(t2)={live['n_t2']:.0f} 月 n(MDE)={live['n_mde']:.0f} 月")

    print("\n=== t=5.43 反推 ===")
    for k, v in implied.items():
        print(f"  {k} = {v}")
    print(f"\n→ {OUT / 'stat.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
