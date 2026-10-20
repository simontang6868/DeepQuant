#!/usr/bin/env python
"""Gate 6 敏感性：长停牌"复牌首日"对反转效应的影响。

为什么必须做
------------
Gate 6（及其前身）在计算 fwd 收益时，只要求"当日有价"，**没有处理复牌首日的跳变**。
实测 `data/exec_all` 有 **1,122 只**标的存在 ≥120 自然日的长停牌，探针确认全为真实停牌。
A 股长期停牌（重组/股改/破产重整）复牌首日常有巨幅涨跌，且**无涨跌幅限制**。

反转策略做多"过去 21 日跌最多的"，这类标的里长期停牌股占比不低。
若复牌首日的巨大反弹被计入 fwd，会让**输家腿收益虚高**（d 偏大）。
本脚本量化这个偏差有多大。

两种中性化口径（都跑，互为对照）
--------------------------------
- **S1 剔除样本**：持有期内有复牌（间隔 > 5 自然日）的标的，**从当月横截面剔除**。
  最保守，但会损失样本（且剔除本身有选择偏差）。
- **S2 中和跳变**：保留标的，但把复牌首日那一天的收益**置为 0**
  （fwd ×= 复牌前收 / 复牌日收，逐个复牌日）。保留样本，只去掉跳变。

判定：**不改 Gate 6 主判定**（预登记已冻结）。本脚本只产出描述性对照，
用于在报告中声明"复牌首日到底贡献了多少"。

用法
----
    python scripts/gate6_sens_resume.py
    python scripts/gate6_sens_resume.py --report docs/GATE6_SENS_RESUME.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import statistics
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

ADJ_DIR = "data/qfq_all"
LOOKBACK, SKIP = 21, 1
MIN_VALID_RATIO = 0.90
K_FRAC = 0.20
MIN_LISTED_DAYS = 252
START, END_SIGNAL, END_HOLD = "2007-01-01", "2026-09-30", "2026-10-31"
RESUME_GAP = 5          # 与 TradabilityIndex 一致：>5 自然日视为复牌


def load_adj(adj_dir: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """返回 (close, resume_flag)：resume_flag[i,c]=True 表示第 i 天是该标的复牌首日。"""
    import glob
    cols, flags = {}, {}
    for path in sorted(glob.glob(os.path.join(adj_dir, "*.qfq.csv"))):
        code = os.path.basename(path).replace(".qfq.csv", "")
        df = pd.read_csv(path, usecols=["date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates("date", keep="last").sort_values("date")
        idx = df["date"]
        cols[code] = pd.Series(df["close"].astype(float).values, index=idx)
        gap = idx.diff().dt.days
        flags[code] = pd.Series((gap > RESUME_GAP).values, index=idx)
    close = pd.DataFrame(cols).sort_index()
    resume = pd.DataFrame(flags).reindex(close.index).fillna(False).astype(bool)
    return close, resume


def month_ends(calendar: pd.DatetimeIndex) -> list[int]:
    pos = []
    for i in range(len(calendar)):
        if i == len(calendar) - 1 or calendar[i].month != calendar[i + 1].month:
            pos.append(i)
    return pos


def build(close: pd.DataFrame, resume: pd.DataFrame, positions, k_frac: float,
          mode: str) -> list[dict]:
    """mode: base | s1(剔除) | s2(中和跳变)"""
    valid = close.notna() & (close > 0)
    cum = valid.cumsum(axis=0)
    mask = valid & (cum >= MIN_LISTED_DAYS)
    ffill = close.ffill()
    cal = close.index
    out = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        if str(cal[pos].date()) > END_SIGNAL:
            continue
        ok = mask.iloc[pos]
        pool0 = ok[ok].index
        if len(pool0) < 10:
            continue
        s0, s1 = pos - (SKIP + LOOKBACK - 1), pos - SKIP
        win = close.iloc[s0:s1 + 1][pool0]
        nd = win.notna().sum(axis=0)
        p_start, p_end = win.iloc[0], win.iloc[-1]
        good = (nd >= math.ceil(LOOKBACK * MIN_VALID_RATIO)) & p_start.notna() & p_end.notna()
        sc = (p_end / p_start - 1.0).where(good).dropna()

        p0 = close.iloc[pos][pool0]
        p1 = ffill.iloc[nxt][pool0]
        fwd = (p1 / p0 - 1.0).dropna()
        pool = sc.index.intersection(fwd.index)

        # 持有期内 (pos, nxt] 的复牌标记
        seg = resume.iloc[pos + 1:nxt + 1][pool]
        if mode == "s1":
            hit = seg.any(axis=0)
            pool = pool[~hit.reindex(pool).fillna(False).values]

        if mode == "s2" and len(pool):
            # 对每个复牌首日 r：fwd ×= close[r-1] / close[r] （去掉这一天的收益）
            adj = pd.Series(1.0, index=pool)
            for code in pool:
                idxs = np.flatnonzero(seg[code].to_numpy())
                if idxs.size == 0:
                    continue
                factor = 1.0
                for j in idxs:
                    r_pos = pos + 1 + int(j)
                    if r_pos <= pos:
                        continue
                    c_now = close.iloc[r_pos].get(code, np.nan)
                    c_prev = close.iloc[r_pos - 1].get(code, np.nan)
                    if c_now and c_prev and c_now > 0 and c_prev > 0:
                        factor *= float(c_prev) / float(c_now)
                adj[code] = factor
            fwd = fwd * adj

        n_pool = len(pool)
        if n_pool < 10:
            continue
        k = max(1, min(int(round(k_frac * n_pool)), n_pool // 2))
        ranked = sc.loc[pool].sort_values(ascending=True)
        longs, shorts = ranked.index[:k], ranked.index[-k:]
        rl = float(fwd.loc[longs].mean())
        rs = float(fwd.loc[shorts].mean())
        out.append({"signal_date": str(cal[pos].date()), "n_pool": n_pool, "k": k,
                    "r_long": rl, "r_short": rs, "d": 0.5 * (rl - rs)})
    return out


def stats(d: list[float]) -> dict:
    n = len(d)
    if n < 2:
        return {"n": n}
    m = statistics.mean(d)
    sd = statistics.stdev(d)
    se = sd / math.sqrt(n)
    t = m / se
    return {"n": n, "mean_pp": m * 100, "sd_pp": sd * 100, "t": t,
            "mde_pp": 2.8016 * sd / math.sqrt(n) * 100,
            "win": sum(1 for x in d if x > 0) / n}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--adj-dir", default=ADJ_DIR)
    ap.add_argument("--report", default="docs/GATE6_SENS_RESUME.json")
    args = ap.parse_args(argv)

    print(f"[load] {args.adj_dir} ……")
    close, resume = load_adj(args.adj_dir)
    print(f"[load] {close.shape[0]} 天 × {close.shape[1]} 只 | 复牌标记 {int(resume.to_numpy().sum())} 处")
    cal = close.index
    positions = month_ends(cal)
    positions = [p for p in positions if str(cal[p].date()) >= START]
    print(f"[load] 调仓点 {len(positions)}")

    res = {}
    for mode, label in (("base", "基准（复牌首日计入）"),
                        ("s1", "S1 剔除含复牌的标的当月"),
                        ("s2", "S2 把复牌首日收益置 0")):
        rows = build(close, resume, positions, K_FRAC, mode)
        d = [r["d"] for r in rows]
        st = stats(d)
        res[mode] = {"label": label, "stats": st,
                     "mean_k": statistics.mean(r["k"] for r in rows) if rows else 0}
        print(f"  {label:<26} n={st.get('n')}  mean={st.get('mean_pp'):+.4f}pp  "
              f"t={st.get('t'):+.4f}  MDE={st.get('mde_pp'):.4f}pp  胜率={st.get('win'):.1%}")

    base, s1, s2 = res["base"]["stats"], res["s1"]["stats"], res["s2"]["stats"]
    delta1 = s1["mean_pp"] - base["mean_pp"]
    delta2 = s2["mean_pp"] - base["mean_pp"]
    print()
    print(f"[结论] S1 变化 {delta1:+.4f}pp（{delta1/base['mean_pp']*100:+.1f}%）  "
          f"S2 变化 {delta2:+.4f}pp（{delta2/base['mean_pp']*100:+.1f}%）")
    verdict = ("复牌首日贡献可忽略" if abs(delta2) < 0.05 * abs(base["mean_pp"])
               else "复牌首日有实质贡献，必须在报告中声明")
    print(f"[判定] {verdict}（门槛：|Δ| < 基准的 5%）")

    out = {"adj_dir": args.adj_dir, "k_frac": K_FRAC, "resume_gap_days": RESUME_GAP,
           "window": [START, END_SIGNAL], "results": res,
           "delta_s1_pp": delta1, "delta_s2_pp": delta2,
           "delta_s2_pct": delta2 / base["mean_pp"] * 100 if base["mean_pp"] else None,
           "verdict": verdict,
           "note": "本脚本不改 Gate 6 主判定，只量化复牌首日的影响。"}
    if os.path.dirname(args.report):
        os.makedirs(os.path.dirname(args.report), exist_ok=True)
    json.dump(out, open(args.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
