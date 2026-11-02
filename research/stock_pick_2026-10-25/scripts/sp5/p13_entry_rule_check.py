# -*- coding: utf-8 -*-
"""SP-5 / P1-3 前置：前瞻 entry_rule 与回测口径的一致性检验。

问题
----
冻结配置 `forward/sp4_config_v1.json` 的 `entry_rule` 写的是
「池内按 amt20 **降序取前 5 只**（确定性；替代原随机买入）」，
并声称「审计已证买入规则对期望无贡献」。

但实测 2026-09-30 的目标 5 只 amt20 极差只有 **0.139%**——它们紧贴 `liqpct<20%`
的上边界并列。而降序取前 5 = 取「低流动性档里最活跃的 5 只」，
与「池内随机抽 5 只」的期望**未必相同**（随机抽的平均流动性更低，
而低流动性溢价与流动性单调相关）。

若不检验就直接前瞻，会产生一个**系统性口径偏差**：前瞻测的是"档内最活跃 5 只"，
却拿它去比回测的"随机 5 只"。

检验
----
2023-01 起 44 个月，每月：
  * `top5`：池内 amt20 降序前 5 的 `fwd20` 均值（毛）
  * `rand5`：池内随机抽 5 只，重复 500 次的均值（毛）
比较两者差异，并报配对 t。

判据（**三档，因为「没检出差异」≠「证明等价」**）
------------------------------------------------
* `ENTRY_RULE_BIASED`：差异显著（|t| ≥ 1.96）；
* `ENTRY_RULE_EQUIVALENT`：**等价性检验**通过——差异的 95% CI 全部落在 ±0.30pp/月 内；
* `ENTRY_RULE_CANNOT_VERIFY`：两者都不是。此时**不得**声称"买入规则无影响"，
  因为 MDE 可能远大于待检差异（记忆铁律 4：没检出 ≠ 没问题）。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import p03_tradability as p03  # noqa: E402
import p11_exec_window as p11  # noqa: E402

BASE = pathlib.Path(__file__).resolve().parents[2]
OUT = BASE / "sp5_out"
START = "20230131"
N_DRAW = 500
SEED = 20261026


def main() -> int:
    panel = pd.read_parquet(p03.PANEL)
    panel = panel[panel["date"] >= START]
    p = panel[panel["ok_buy"] & panel["f3"].notna() & panel["fwd20"].notna()].copy()
    p["f3pct"] = p.groupby("date")["f3"].rank(pct=True, method="average")
    p["liqpct"] = p.groupby("date")["amt20"].rank(pct=True, method="average")

    rows = []
    rng = np.random.default_rng(SEED)
    for d, g in p.groupby("date"):
        pool = g[(g["f3pct"] <= p03.THRESH) & (g["liqpct"] < p03.LIQ_MAX)]
        if len(pool) < 5:
            continue
        f = pool["fwd20"].to_numpy(float)
        amt = pool["amt20"].to_numpy(float)
        order = np.argsort(-amt)[:5]                 # amt20 降序前 5
        top5 = float(f[order].mean())
        draws = [float(f[rng.choice(len(f), 5, replace=False)].mean()) for _ in range(N_DRAW)]
        rand5 = float(np.mean(draws))
        rows.append({"month": d, "pool_n": len(pool), "top5": top5, "rand5": rand5,
                     "diff": top5 - rand5,
                     "amean_top5": float(amt[order].mean()),
                     "amean_pool": float(amt.mean())})

    df = pd.DataFrame(rows)
    st_t, st_r, st_d = p11.stats(df["top5"]), p11.stats(df["rand5"]), p11.stats(df["diff"])
    print(f"[n] {len(df)} 月｜{df['month'].min()} ~ {df['month'].max()}")
    print(f"[top5 ] 月均 {st_t['mean']*100:+.3f}pp  t={st_t['t_iid']:+.2f}")
    print(f"[rand5] 月均 {st_r['mean']*100:+.3f}pp  t={st_r['t_iid']:+.2f}")
    print(f"[diff ] 月均 {st_d['mean']*100:+.3f}pp  t={st_d['t_iid']:+.2f}  "
          f"MDE {st_d['mde']*100:.3f}pp")
    print(f"[amt20] top5 均值 {df['amean_top5'].mean()/1e6:.1f} 百万 vs "
          f"池均值 {df['amean_pool'].mean()/1e6:.1f} 百万 "
          f"（top5 是池均值的 {df['amean_top5'].mean()/df['amean_pool'].mean():.2f} 倍）")

    EQUIV_MARGIN = 0.003          # ±0.30pp/月 作为等价界（年化约 3.7pp）
    ci_lo = st_d["mean"] - 1.96 * st_d["se_iid"]
    ci_hi = st_d["mean"] + 1.96 * st_d["se_iid"]
    if abs(st_d["t_iid"]) >= 1.96:
        verdict = "ENTRY_RULE_BIASED"
    elif ci_lo > -EQUIV_MARGIN and ci_hi < EQUIV_MARGIN:
        verdict = "ENTRY_RULE_EQUIVALENT"
    else:
        verdict = "ENTRY_RULE_CANNOT_VERIFY"
    print(f"[ci] 差异 95% CI [{ci_lo*100:+.3f}, {ci_hi*100:+.3f}]pp"
          f"｜等价界 ±{EQUIV_MARGIN*100:.2f}pp ⇒ {'落在界内' if verdict=='ENTRY_RULE_EQUIVALENT' else '超出界外'}")
    res = {
        "window": {"start": str(df["month"].min()), "end": str(df["month"].max()),
                   "n_months": len(df)},
        "n_draw": N_DRAW, "seed": SEED,
        "top5": st_t, "rand5": st_r, "diff": st_d,
        "amt20_ratio": float(df["amean_top5"].mean() / df["amean_pool"].mean()),
        "diff_ci95": [float(ci_lo), float(ci_hi)],
        "equiv_margin": EQUIV_MARGIN,
        "monthly": df.to_dict("records"),
        "verdict": verdict,
        "note": ("毛口径（未扣成本），fwd20 是调仓后 20 日收益；top5=amt20 降序前 5，"
                 "rand5=池内随机 5 只×500 次均值。⚠️ diff 的 MDE 高达 2.2pp/月——"
                 "「未检出差异」绝不能读成「两者等价」"),
    }
    (OUT / "sp5_entry_rule_check.json").write_text(
        json.dumps(res, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n[verdict] {verdict}")
    print(f"[save] {OUT / 'sp5_entry_rule_check.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
