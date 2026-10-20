#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 4.2-v2：绝对阈值（R2）与波动率目标（R3）的验证。

口径见 `docs/MARKET_STATE_V2_SPEC_2026-10-20.md`（**跑前冻结**）：
  R2: vol60 > 35%（年化） → 下月仓位 0.5，否则 1.0
  R3: 下月仓位 = min(1, 20% / vol60)
  判据 4 条，第 4 条是新增的「相对同平均仓位固定仓位的 Calmar 提升 ≥ 0.05」

用法：
  python scripts/market_state_risk_v2.py
"""

from __future__ import annotations

import json
import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage3_sigma_d import load_close_matrix  # noqa: E402
from run_gate3_longsample import EXCLUDED_SYMBOLS, POOL_DIR  # noqa: E402
from market_state_risk import metrics, TRADING_DAYS, VOL_WIN  # noqa: E402

ABS_THRESHOLD = 0.35     # R2 的绝对波动阈值（年化）
TARGET_VOL = 0.20        # R3 的目标波动（年化）
DE_RISK_W = 0.5

JUDGE = {"mdd_gain_pp": 3.0, "calmar_gain": 0.10, "cagr_loss_max_pp": 1.0, "calmar_gain_vs_fixed": 0.05}


def main() -> int:
    close_all, _ = load_close_matrix(POOL_DIR)
    close = close_all.drop(columns=[c for c in EXCLUDED_SYMBOLS if c in close_all.columns])
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    ew = ret.mean(axis=1).dropna().loc["2007-01-01":"2026-09-30"]
    print(f"[v2] 池 {close.shape[1]} 只 | {ew.index[0].date()} ~ {ew.index[-1].date()} | {len(ew)} 天")

    vol60 = ew.rolling(VOL_WIN).std() * math.sqrt(TRADING_DAYS)
    month_ends = ew.groupby([ew.index.year, ew.index.month]).tail(1).index

    rows = []
    for ts in month_ends:
        v = vol60.get(ts)
        if pd.isna(v):
            continue
        rows.append({
            "date": str(ts.date()),
            "vol60": float(v),
            "w_r2": DE_RISK_W if v > ABS_THRESHOLD else 1.0,
            "w_r3": float(min(1.0, TARGET_VOL / v)) if v > 0 else 1.0,
        })
    m = pd.DataFrame(rows)
    print(f"[v2] 月度决策 {len(m)} 个 | vol60 中位 {m['vol60'].median():.1%} | "
          f"最高 {m['vol60'].max():.1%} | 最低 {m['vol60'].min():.1%}")

    def to_daily(w_col: str) -> pd.Series:
        w = pd.Series(1.0, index=ew.index)
        for _, r in m.iterrows():
            ts = pd.Timestamp(r["date"])
            nxt = ts + pd.offsets.MonthEnd(1)
            w.loc[(w.index > ts) & (w.index <= nxt)] = float(r[w_col])
        return w.reindex(ew.index).fillna(1.0)

    w_r2, w_r3 = to_daily("w_r2"), to_daily("w_r3")
    split = ew.index[int(len(ew) * 0.5)]
    a, b = split, ew.index[-1]
    seg = slice(a, b)

    print(f"[v2] 设计段 {ew.index[0].date()} ~ {split.date()} | 验证段 {a.date()} ~ {b.date()}")

    rules = {"R2 绝对阈值35%": w_r2, "R3 波动目标20%": w_r3}
    out_tbl = {}
    base_valid = metrics(ew.loc[seg])
    print()
    print("=== 验证段结果 ===")
    print(f"{'策略':<20}{'CAGR':>9}{'波动':>9}{'MDD':>9}{'Calmar':>9}{'平均仓位':>10}")
    print(f"{'满仓':<20}{base_valid['cagr']*100:>8.2f}%{base_valid['vol']*100:>8.2f}%"
          f"{base_valid['mdd']*100:>8.2f}%{base_valid['calmar']:>9.3f}{1.0:>10.2f}")
    out_tbl["满仓"] = {**base_valid, "avg_w": 1.0}

    judge = {}
    for name, w in rules.items():
        r = ew * w.loc[ew.index]
        mt = metrics(r.loc[seg])
        avg_w = float(w.loc[seg].mean())
        # 同平均仓位的固定仓位对照（第 4 条判据的对手）
        r_fix = ew * avg_w
        mt_fix = metrics(r_fix.loc[seg])
        out_tbl[name] = {**mt, "avg_w": avg_w, "fixed_same_w": mt_fix}
        print(f"{name:<20}{mt['cagr']*100:>8.2f}%{mt['vol']*100:>8.2f}%"
              f"{mt['mdd']*100:>8.2f}%{mt['calmar']:>9.3f}{avg_w:>10.2f}")
        print(f"{'  └ 同仓位固定'+f'{avg_w:.2f}':<20}{mt_fix['cagr']*100:>8.2f}%{mt_fix['vol']*100:>8.2f}%"
              f"{mt_fix['mdd']*100:>8.2f}%{mt_fix['calmar']:>9.3f}{avg_w:>10.2f}")

        mdd_gain = (mt["mdd"] - base_valid["mdd"]) * 100
        calmar_gain = mt["calmar"] - base_valid["calmar"]
        cagr_loss = (base_valid["cagr"] - mt["cagr"]) * 100
        calmar_vs_fixed = mt["calmar"] - mt_fix["calmar"]
        ok = (mdd_gain >= JUDGE["mdd_gain_pp"] and calmar_gain >= JUDGE["calmar_gain"]
              and cagr_loss <= JUDGE["cagr_loss_max_pp"] and calmar_vs_fixed >= JUDGE["calmar_gain_vs_fixed"])
        judge[name] = {"mdd_gain_pp": mdd_gain, "calmar_gain": calmar_gain, "cagr_loss_pp": cagr_loss,
                       "calmar_gain_vs_fixed": calmar_vs_fixed, "effective": bool(ok), "avg_w": avg_w}
        print(f"      ① MDD {mdd_gain:+.2f}pp  ② Calmar {calmar_gain:+.3f}  ③ 年化降 {cagr_loss:+.2f}pp  "
              f"④ vs 同仓位 {calmar_vs_fixed:+.3f}  → {'有效' if ok else '未验证'}")
        print()

    # 全样本与设计段（描述性）
    print("=== 全样本 / 设计段（描述性）===")
    for segname, sl in [("full", slice(ew.index[0], ew.index[-1])), ("design", slice(ew.index[0], split))]:
        base = metrics(ew.loc[sl])
        line = f"  {segname:<7} 满仓 CAGR {base['cagr']*100:+6.2f}% MDD {base['mdd']*100:7.2f}% | "
        for name, w in rules.items():
            mt = metrics((ew * w.loc[ew.index]).loc[sl])
            line += f"{name} CAGR {mt['cagr']*100:+6.2f}% MDD {mt['mdd']*100:7.2f}% | "
        print(line)

    # 触发结构
    print()
    print("=== 决策结构 ===")
    m["year"] = m["date"].str[:4]
    r2_trig = m[m["w_r2"] < 1.0]
    print(f"  R2 触发 {len(r2_trig)}/{len(m)} 个月：" +
          (", ".join(f"{y}:{n}" for y, n in r2_trig["year"].value_counts().sort_index().items()) or "无"))
    print(f"  R3 平均仓位 {m['w_r3'].mean():.2f}，仓位分布："
          f"半仓以下 {int((m['w_r3']<0.5).sum())} 月 / 0.5~0.8 {int(((m['w_r3']>=0.5)&(m['w_r3']<0.8)).sum())} 月 / "
          f">=0.8 {int((m['w_r3']>=0.8).sum())} 月")
    print(f"  两规则相关系数 ρ(仓位) = {m['w_r2'].corr(m['w_r3']):+.3f}")

    out = {
        "pool": int(close.shape[1]),
        "window": [str(ew.index[0].date()), str(ew.index[-1].date())],
        "split_date": str(split.date()),
        "params": {"vol_win": VOL_WIN, "abs_threshold": ABS_THRESHOLD, "target_vol": TARGET_VOL,
                   "de_risk_w": DE_RISK_W},
        "n_months": int(len(m)),
        "valid": out_tbl,
        "judgement": judge,
        "trigger": {"r2_months": int(len(r2_trig)),
                    "r2_by_year": r2_trig["year"].value_counts().sort_index().to_dict(),
                    "r3_avg_w": float(m["w_r3"].mean())},
        "monthly": rows,
    }
    path = "docs/MARKET_STATE_RISK_V2_2026-10-20.json"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"\n[v2] report -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
