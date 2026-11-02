# -*- coding: utf-8 -*-
"""SP-5 / P1-3：判定协议的功效核算（只读）。

问题：配置写 `success_criteria.annual_net_gt = 0.08`、`min_observation_months = 36`。
在 36 个月的观察期上，"年化 > 8%" 这个门槛到底能不能判定？

方法：用独立账本 path-0 的月度收益估计月波动率，按
  se(年化) = 12·s/√T,  MDE = 2.8016·se,  功效 = Φ(|t|−1.96)+Φ(−|t|−1.96)
给出不同 T 的分辨率与功效。**不重算收益、不改任何参数。**
"""
from __future__ import annotations

import csv
import math
import os

import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(BASE, "reports")
SP5 = os.path.join(BASE, "sp5_out")


def Phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def main():
    rows = []
    for yr in ("2005", "2013", "2018", "2021"):
        p = os.path.join(SP5, f"p04b_independent_nav_p0_{yr}.csv")
        if not os.path.exists(p):
            continue
        d = pd.read_csv(p)
        d["dt"] = pd.to_datetime(d["date"].astype(str), format="%Y%m%d")
        ser = d.set_index("dt")["nav"]
        m = ser.groupby(ser.index.to_period("M")).last().pct_change().dropna()
        s, mu, n = m.std(ddof=1), m.mean(), len(m)
        for T in (36, 60, 120):
            se = 12.0 * s / math.sqrt(T)
            mde = 2.8016 * se
            t8 = 0.08 / se
            rows.append({
                "start": yr, "n_months_hist": n,
                "monthly_mean_pct": round(mu * 100, 4), "monthly_sd_pct": round(s * 100, 4),
                "geom_ann_pct": round(((ser.iloc[-1] / ser.iloc[0]) ** (12.0 / (n + 1)) - 1) * 100, 3),
                "T_months": T, "se_ann_pp": round(se * 100, 3),
                "ci95_half_pp": round(1.96 * se * 100, 3), "MDE_ann_pp": round(mde * 100, 3),
                "power_true8pct": round((Phi(t8 - 1.96) + Phi(-t8 - 1.96)) * 100, 1),
                "power_true_geom": round((Phi(abs((((ser.iloc[-1] / ser.iloc[0]) ** (12.0 / (n + 1)) - 1)) / se) - 1.96)
                                           + Phi(-abs((((ser.iloc[-1] / ser.iloc[0]) ** (12.0 / (n + 1)) - 1)) / se) - 1.96)) * 100, 1),
            })
    p = os.path.join(OUT, "SP5_DECISION_POWER.csv")
    with open(p, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        if r["start"] == "2021":
            print(f"  2021 起点 T={r['T_months']}m: se={r['se_ann_pp']}pp ±{r['ci95_half_pp']}pp "
                  f"MDE={r['MDE_ann_pp']}pp 功效(真8%)={r['power_true8pct']}%")
    print(f"[saved] {p}")


if __name__ == "__main__":
    main()
