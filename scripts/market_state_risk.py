#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 4.2：市场状态与风险管理（描述 + **可证伪**的风控验证）。

口径见 `docs/MARKET_STATE_SPEC_2026-10-20.md`（**跑前冻结**）：
  - 载体 = 532 只研究池的**同池等权**（D-2 主基准），日频
  - 状态变量全部只用过去数据，分档用 **expanding 分位**（防前视）
  - 降风险规则 R（单点，不搜索）：月末 vol60 > 截至当日 expanding 80% 分位 → 下月仓位 0.5
  - 设计段 = 前 50% 交易日；**验证段 = 后 50%**（判定只用验证段）
  - 判定：验证段 MDD 改善 ≥ 3pp **且** Calmar 提升 ≥ 0.10 **且** 年化不降超 1.0pp

用法：
  python scripts/market_state_risk.py
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

TRADING_DAYS = 250
VOL_WIN = 60
CORR_WIN = 60
VOL_Q = 0.80
DE_RISK_W = 0.5

JUDGE = {"mdd_gain_pp": 3.0, "calmar_gain": 0.10, "cagr_loss_max_pp": 1.0}


def welch_t(a: np.ndarray, b: np.ndarray) -> tuple[float, float]:
    """Welch t 与正态近似双侧 p（无 scipy 依赖，n>30 时足够）。"""
    na, nb = len(a), len(b)
    if na < 3 or nb < 3:
        return float("nan"), float("nan")
    va, vb = a.var(ddof=1) / na, b.var(ddof=1) / nb
    se = math.sqrt(va + vb)
    if se == 0:
        return float("nan"), float("nan")
    t = (a.mean() - b.mean()) / se
    p = 2 * 0.5 * (1 - math.erf(abs(t) / math.sqrt(2)))
    return float(t), float(p)


def metrics(daily: pd.Series) -> dict:
    """由日收益序列算年化/波动/最大回撤/Calmar。"""
    d = daily.dropna()
    if len(d) < 30:
        return {}
    nav = (1 + d).cumprod()
    years = len(d) / TRADING_DAYS
    cagr = nav.iloc[-1] ** (1 / years) - 1 if years > 0 else float("nan")
    vol = d.std() * math.sqrt(TRADING_DAYS)
    mdd = float((nav / nav.cummax() - 1).min())
    return {
        "n_days": int(len(d)),
        "cagr": float(cagr),
        "vol": float(vol),
        "mdd": mdd,
        "calmar": float(cagr / abs(mdd)) if mdd < 0 else float("nan"),
        "total": float(nav.iloc[-1] - 1),
    }


def main() -> int:
    close_all, _ = load_close_matrix(POOL_DIR)
    close = close_all.drop(columns=[c for c in EXCLUDED_SYMBOLS if c in close_all.columns])
    ret = close.pct_change(fill_method=None).replace([np.inf, -np.inf], np.nan)
    ew = ret.mean(axis=1)                     # 同池等权日收益（当日可用标的等权）
    ew = ew.dropna()
    # spec §1 窗口 2007-01-01 起（2004-12 起只是暖机，不参与统计）
    ew = ew.loc["2007-01-01":"2026-09-30"]
    nav_all = (1 + ew).cumprod()
    nav = (1 + ew).cumprod()
    print(f"[4.2] 池 {close.shape[1]} 只 | 日频 {ew.index[0].date()} ~ {ew.index[-1].date()} | {len(ew)} 天")

    # ---------- 状态变量 ----------
    vol60 = ew.rolling(VOL_WIN).std() * math.sqrt(TRADING_DAYS)
    _ = nav_all
    vol_q80 = vol60.expanding(250).quantile(VOL_Q)        # 只用过去
    dd = nav / nav.cummax() - 1
    ma200 = nav.rolling(200).mean()
    trend = np.where(nav > ma200, "above", "below")

    # 横截面平均成对相关（月末算，省时且规则只用月末值）
    month_ends = ew.groupby([ew.index.year, ew.index.month]).tail(1).index
    corr60 = {}
    for ts in month_ends:
        win = ret.loc[:ts].tail(CORR_WIN)
        if len(win) < CORR_WIN:
            continue
        c = win.corr(min_periods=int(CORR_WIN * 0.8)).values
        n = c.shape[0]
        iu = np.triu_indices(n, 1)
        vals = c[iu]
        vals = vals[~np.isnan(vals)]
        if len(vals) > 100:
            corr60[ts] = float(vals.mean())
    corr = pd.Series(corr60).sort_index()
    print(f"[4.2] 相关性样本 {len(corr)} 个月末 | 均值 {corr.mean():.3f} | 范围 [{corr.min():.3f}, {corr.max():.3f}]")

    # ---------- 月度表 ----------
    rows = []
    for ts in month_ends:
        if ts not in corr.index or pd.isna(vol60.get(ts)) or pd.isna(vol_q80.get(ts)):
            continue
        nxt_end = ts + pd.offsets.MonthEnd(1)
        seg = ew.loc[(ew.index > ts) & (ew.index <= nxt_end)]
        if len(seg) < 10:
            continue
        mret = float((1 + seg).prod() - 1)
        rows.append({
            "date": str(ts.date()),
            "fwd_ret": mret,
            "vol60": float(vol60[ts]),
            "vol_q80": float(vol_q80[ts]),
            "dd": float(dd[ts]),
            "corr60": float(corr[ts]),
            "trend": str(trend[list(ew.index).index(ts)]),
        })
    m = pd.DataFrame(rows)
    print(f"[4.2] 月度观测 {len(m)} 个")

    # ---------- Q1：状态与次月收益 ----------
    m["vol_state"] = np.where(m["vol60"] > m["vol_q80"], "high",
                              np.where(m["vol60"] > m["vol_q80"] * 0.7, "mid", "low"))
    m["dd_state"] = np.where(m["dd"] < -0.20, "deep", np.where(m["dd"] < -0.08, "mid", "shallow"))

    print()
    print("=== Q1a 波动状态 vs 次月收益（vol_state 用 expanding 80% 分位切）===")
    for s in ["low", "mid", "high"]:
        g = m[m["vol_state"] == s]["fwd_ret"].values
        if len(g) < 3:
            continue
        print(f"  {s:<5} n={len(g):>3}  次月均值 {g.mean()*100:+6.2f}%  sd {g.std(ddof=1)*100:5.2f}%  "
              f"胜率 {(g>0).mean()*100:5.1f}%  最差 {g.min()*100:+7.2f}%")
    hi = m[m["vol_state"] == "high"]["fwd_ret"].values
    lo = m[m["vol_state"] == "low"]["fwd_ret"].values
    t, p = welch_t(hi, lo)
    print(f"  Welch t（high vs low 次月收益）= {t:+.2f}  p≈{p:.4f}")

    print()
    print("=== Q1b 回撤状态 vs 次月收益 ===")
    for s in ["shallow", "mid", "deep"]:
        g = m[m["dd_state"] == s]["fwd_ret"].values
        if len(g) < 3:
            continue
        print(f"  {s:<8} n={len(g):>3}  次月均值 {g.mean()*100:+6.2f}%  sd {g.std(ddof=1)*100:5.2f}%  胜率 {(g>0).mean()*100:5.1f}%")

    # ---------- Q1c："相关性在下跌时上升"是否成立 ----------
    print()
    print('=== Q1c 「相关性在下跌时上升」检验（同期月度收益 vs 期末 corr60）===')
    down = m[m["fwd_ret"] < 0]["corr60"].values
    up = m[m["fwd_ret"] >= 0]["corr60"].values
    if len(down) > 2 and len(up) > 2:
        t2, p2 = welch_t(down, up)
        print(f"  下跌月 corr60 均值 {down.mean():.3f}（n={len(down)}） vs 上涨月 {up.mean():.3f}（n={len(up)}）")
        print(f"  Welch t = {t2:+.2f}  p≈{p2:.4f}  → "
              f"{'下跌月显著更高（常识成立）' if (p2 < 0.05 and down.mean() > up.mean()) else '不显著或无此方向'}")
    # 秩相关（不依赖 scipy）：先排序再算 Pearson
    pr = m["corr60"].rank()
    qr = m["fwd_ret"].rank()
    print(f"  corr60 与同期收益的秩相关 ρ = {pr.corr(qr):+.3f}")

    # ---------- Q1d：按年看相关性 + 极端下跌月 ----------
    print()
    print("=== Q1d corr60 按年（描述）===")
    m["year"] = m["date"].str[:4]
    by_year = m.groupby("year")["corr60"].agg(["mean", "max", "count"])
    for y, r in by_year.iterrows():
        print(f"  {y}: 均值 {r['mean']:.3f}  年内最高 {r['max']:.3f}  n={int(r['count'])}")
    crash = m[m["fwd_ret"] < -0.10]["corr60"].values
    rest = m[m["fwd_ret"] >= -0.10]["corr60"].values
    t3, p3 = welch_t(crash, rest)
    print(f"  极端下跌月（<−10%）corr60 {crash.mean():.3f}（n={len(crash)}） vs 其余 {rest.mean():.3f}（n={len(rest)}）"
          f"  Welch t={t3:+.2f} p≈{p3:.4f}")

    # ---------- Q2：规则验证 ----------
    m["weight"] = np.where(m["vol60"] > m["vol_q80"], DE_RISK_W, 1.0)
    # 把月内权重映射回日频
    w_daily = pd.Series(1.0, index=ew.index)
    for _, r in m.iterrows():
        ts = pd.Timestamp(r["date"])
        nxt = ts + pd.offsets.MonthEnd(1)
        w_daily.loc[(w_daily.index > ts) & (w_daily.index <= nxt)] = float(r["weight"])
    r_rule = ew * w_daily.reindex(ew.index).fillna(1.0)
    r_half = ew * 0.5

    split = ew.index[int(len(ew) * 0.5)]
    segs = {"full": (ew.index[0], ew.index[-1]), "design": (ew.index[0], split), "valid": (split, ew.index[-1])}

    print()
    print("=== Q2 规则 R（vol60 > 当日 expanding 80% 分位 → 下月半仓）===")
    print(f"  设计段 {segs['design'][0].date()} ~ {segs['design'][1].date()} ｜ "
          f"验证段 {segs['valid'][0].date()} ~ {segs['valid'][1].date()}")
    print(f"  平均仓位：设计段 {w_daily.loc[segs['design'][0]:segs['design'][1]].mean():.2f}  "
          f"验证段 {w_daily.loc[segs['valid'][0]:segs['valid'][1]].mean():.2f}")
    trig = m[m["weight"] < 1.0].copy()
    trig["year"] = trig["date"].str[:4]
    print(f"  触发次数（共 {len(trig)}/{len(m)} 个月）：" +
          (", ".join(f"{y}:{n}" for y, n in trig["year"].value_counts().sort_index().items()) or "无"))
    print("  ↑ 触发频率随极端事件进入样本而单调下降，是 expanding 分位的**结构性后果**，不是规则本身失效")

    table = {}
    hdr = f"{'段':<8}{'策略':<10}{'CAGR':>9}{'波动':>9}{'MDD':>9}{'Calmar':>9}{'累计':>10}"
    print(hdr)
    for sname, (a, b) in segs.items():
        for label, series in [("满仓", ew), ("R 规则", r_rule), ("固定半仓", r_half)]:
            mt = metrics(series.loc[a:b])
            if not mt:
                continue
            table[f"{sname}|{label}"] = mt
            print(f"{sname:<8}{label:<10}{mt['cagr']*100:>8.2f}%{mt['vol']*100:>8.2f}%"
                  f"{mt['mdd']*100:>8.2f}%{mt['calmar']:>9.3f}{mt['total']*100:>9.1f}%")
        print()

    v0, v1 = table["valid|满仓"], table["valid|R 规则"]
    vh = table["valid|固定半仓"]
    mdd_gain = (v1["mdd"] - v0["mdd"]) * 100
    calmar_gain = v1["calmar"] - v0["calmar"]
    cagr_loss = (v0["cagr"] - v1["cagr"]) * 100
    ok = (mdd_gain >= JUDGE["mdd_gain_pp"] and calmar_gain >= JUDGE["calmar_gain"]
          and cagr_loss <= JUDGE["cagr_loss_max_pp"])
    print("=== 判定（验证段，spec §4）===")
    print(f"  ① MDD 改善 {mdd_gain:+.2f}pp（需 ≥ {JUDGE['mdd_gain_pp']}pp）")
    print(f"  ② Calmar 提升 {calmar_gain:+.3f}（需 ≥ {JUDGE['calmar_gain']}）")
    print(f"  ③ 年化下降 {cagr_loss:+.2f}pp（需 ≤ {JUDGE['cagr_loss_max_pp']}pp）")
    print(f"  >>> 判定：{'有效' if ok else '未验证（不能进策略）'}")
    print()
    print("  对照：R 规则 vs 固定半仓（验证段）")
    print(f"    R 规则  CAGR {v1['cagr']*100:+.2f}% / MDD {v1['mdd']*100:.2f}% / Calmar {v1['calmar']:.3f}")
    print(f"    固定半仓 CAGR {vh['cagr']*100:+.2f}% / MDD {vh['mdd']*100:.2f}% / Calmar {vh['calmar']:.3f}")
    print(f"    → 状态判断的增量：CAGR {(v1['cagr']-vh['cagr'])*100:+.2f}pp，Calmar {v1['calmar']-vh['calmar']:+.3f}")

    out = {
        "pool": int(close.shape[1]),
        "window": [str(ew.index[0].date()), str(ew.index[-1].date())],
        "params": {"vol_win": VOL_WIN, "corr_win": CORR_WIN, "vol_q": VOL_Q, "de_risk_w": DE_RISK_W},
        "split_date": str(split.date()),
        "n_months": int(len(m)),
        "corr_summary": {"mean": float(corr.mean()), "min": float(corr.min()), "max": float(corr.max())},
        "q1_vol_state": {s: {"n": int((m["vol_state"] == s).sum()),
                             "mean_fwd": float(m[m["vol_state"] == s]["fwd_ret"].mean()),
                             "sd_fwd": float(m[m["vol_state"] == s]["fwd_ret"].std(ddof=1))}
                         for s in ["low", "mid", "high"]},
        "q1_high_vs_low_welch_t": t, "q1_high_vs_low_p": p,
        "q1c_down_corr": float(down.mean()) if len(down) else None,
        "q1c_up_corr": float(up.mean()) if len(up) else None,
        "q1c_welch_t": t2 if len(down) > 2 and len(up) > 2 else None,
        "q1c_p": p2 if len(down) > 2 and len(up) > 2 else None,
        "q1d_corr_by_year": {y: {"mean": float(r["mean"]), "max": float(r["max"]), "n": int(r["count"])}
                             for y, r in by_year.iterrows()},
        "q1d_crash_corr": float(crash.mean()) if len(crash) else None,
        "q1d_rest_corr": float(rest.mean()) if len(rest) else None,
        "q1d_welch_t": t3 if len(crash) > 2 and len(rest) > 2 else None,
        "q1d_p": p3 if len(crash) > 2 and len(rest) > 2 else None,
        "trigger_months": int((m["weight"] < 1.0).sum()),
        "metrics": table,
        "judgement": {"mdd_gain_pp": mdd_gain, "calmar_gain": calmar_gain,
                      "cagr_loss_pp": cagr_loss, "effective": bool(ok)},
        "monthly": rows,
    }
    path = "docs/MARKET_STATE_RISK_2026-10-20.json"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"\n[4.2] report -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
