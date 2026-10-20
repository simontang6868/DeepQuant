#!/usr/bin/env python
"""Gate 3 敏感性：532 池里混进了 14 只 B 股，影响多大？

发现经过
--------
Phase C 在实现"显式排除 B 股"规则时，用 `is_a_share()` 扫了一遍 Gate 3 的 532 只池，发现：

    200018.SZ 200053.SZ 200054.SZ 200152.SZ 200160.SZ 200168.SZ 200413.SZ
    200418.SZ 200613.SZ 200706.SZ 200771.SZ 200986.SZ 900953.SH 900957.SH

**14 只 B 股**。而 Gate 3 的排除清单（`data/_gate3_excluded.txt`）只排了 1 只（900956.SH）。

B 股特征：沪市以**美元**计价、深市以**港币**计价，独立清算体系，流动性与 A 股差异极大，
且长期存在相对 A 股的大幅折价。把它们和被外资无法直接参与的 A 股放在同一个横截面里排序，
等于把两种定价机制混在一起。

本脚本量化这个污染：**同一构造、同一窗口、同一 k=110，只切换"是否含 B 股"**。

⚠️ 不改 Gate 3 判定（预登记已冻结）。这是**池污染的描述性量化**，
   结果写进 `docs/research/UNIVERSE_EFFECT_EXPERIMENT_DESIGN.md` 与阶段 C 预登记。

用法
----
    python scripts/gate3_sens_bshare.py
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

POOL_DIR = "data/qfq_rebuilt_broad_long"
POOL_FILE = "data/_gate3_pool_532.json"
LOOKBACK, SKIP = 21, 1
MIN_VALID_RATIO = 0.90
K_FIXED = 110
MIN_LISTED_DAYS = 252
START, END_SIGNAL = "2007-01-01", "2015-12-31"


def is_b_share(code: str) -> bool:
    return code.split(".")[0].startswith(("200", "900"))


def load(pool_dir: str, codes: list[str]) -> pd.DataFrame:
    cols = {}
    for c in codes:
        p = os.path.join(pool_dir, f"{c}.qfq.csv")
        if not os.path.exists(p):
            continue
        df = pd.read_csv(p, usecols=["date", "close"])
        df["date"] = pd.to_datetime(df["date"])
        cols[c] = df.drop_duplicates("date", keep="last").set_index("date")["close"].astype(float)
    return pd.DataFrame(cols).sort_index()


def month_ends(cal: pd.DatetimeIndex) -> list[int]:
    return [i for i in range(len(cal)) if i == len(cal) - 1 or cal[i].month != cal[i + 1].month]


def run(close: pd.DataFrame, positions, k: int, use_mask: bool = True) -> list[dict]:
    """use_mask=False 时**不要求**上市满 252 交易日 —— 与 Gate 3 的 run_combo 一致。

    ⚠️ 2026-10-21 发现：`run_gate3_longsample.run_combo` **没有** min_listed_days 规则，
       只要 21 日历史足够就进池。而 Gate 6 有 252 日要求。
       这不是"池不同"，是**入池门槛不同**，会直接改变每月候选集与 k_frac。
       本开关用于对齐两种口径，验证差异来源。
    """
    valid = close.notna() & (close > 0)
    if use_mask:
        mask = valid & (valid.cumsum(axis=0) >= MIN_LISTED_DAYS)
    else:
        mask = valid
    ffill = close.ffill()
    cal = close.index
    out = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        if str(cal[pos].date()) > END_SIGNAL:
            continue
        pool0 = mask.iloc[pos][mask.iloc[pos]].index
        if len(pool0) < 10:
            continue
        s0, s1 = pos - (SKIP + LOOKBACK - 1), pos - SKIP
        win = close.iloc[s0:s1 + 1][pool0]
        nd = win.notna().sum(axis=0)
        good = (nd >= math.ceil(LOOKBACK * MIN_VALID_RATIO)) & win.iloc[0].notna() & win.iloc[-1].notna()
        sc = (win.iloc[-1] / win.iloc[0] - 1.0).where(good).dropna()
        fwd = (ffill.iloc[nxt][pool0] / ffill.iloc[pos][pool0] - 1.0).dropna()
        pool = sc.index.intersection(fwd.index)
        if len(pool) < 10:
            continue
        kk = max(1, min(k, len(pool) // 2))
        ranked = sc.loc[pool].sort_values(ascending=True)
        rl = float(fwd.loc[ranked.index[:kk]].mean())
        rs = float(fwd.loc[ranked.index[-kk:]].mean())
        out.append({"signal_date": str(cal[pos].date()), "n_pool": len(pool),
                    "r_long": rl, "r_short": rs, "d": 0.5 * (rl - rs)})
    return out


def stats(d: list[float]) -> dict:
    n = len(d)
    m = statistics.mean(d)
    sd = statistics.stdev(d)
    return {"n": n, "mean_pp": m * 100, "sd_pp": sd * 100, "t": m / (sd / math.sqrt(n)),
            "mde_pp": 2.8016 * sd / math.sqrt(n) * 100,
            "win": sum(1 for x in d if x > 0) / n}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pool-file", default=POOL_FILE)
    ap.add_argument("--pool-dir", default=POOL_DIR)
    ap.add_argument("--report", default="docs/GATE3_SENS_BSHARE.json")
    args = ap.parse_args(argv)

    pool = json.load(open(args.pool_file, encoding="utf-8"))["codes"]
    b = [c for c in pool if is_b_share(c)]
    a = [c for c in pool if not is_b_share(c)]
    print(f"[池] 原 {len(pool)} 只 | B 股 {len(b)} 只 | 纯 A 股 {len(a)} 只")
    print(f"[B股] {b}")

    res = {}
    for tag, codes, label, um in (
            ("with_b", pool, f"原池（含 {len(b)} 只 B 股，n={len(pool)}）· Gate3 口径(无252日)", False),
            ("a_only", a, f"纯 A 股池（剔 B 股，n={len(a)}）· Gate3 口径(无252日)", False),
            ("with_b_252", pool, f"原池 · Gate6 口径(需252日)", True),
            ("a_only_252", a, f"纯 A 股池 · Gate6 口径(需252日)", True)):
        close = load(args.pool_dir, codes)
        pos = [p for p in month_ends(close.index) if str(close.index[p].date()) >= START]
        rows = run(close, pos, K_FIXED, use_mask=um)
        d = [r["d"] for r in rows]
        st = stats(d)
        res[tag] = {"label": label, "n_symbols": close.shape[1], "stats": st}
        print(f"  {label:<34} n={st['n']} mean={st['mean_pp']:+.4f}pp t={st['t']:+.4f} "
              f"MDE={st['mde_pp']:.4f} 胜率={st['win']:.1%}")

    w, o = res["with_b"]["stats"], res["a_only"]["stats"]
    delta = o["mean_pp"] - w["mean_pp"]
    print()
    print(f"[结论] 剔 B 股后 mean 变化 {delta:+.4f}pp（{delta/w['mean_pp']*100:+.1f}%），"
          f"t 从 {w['t']:+.3f} 变 {o['t']:+.3f}")
    verdict = ("B 股污染可忽略" if abs(delta) < 0.05 * abs(w["mean_pp"])
               else "B 股有实质影响，新旧池不可混用")
    print(f"[判定] {verdict}（门槛 |Δ| < 原池 5%）")

    out = {"window": [START, END_SIGNAL], "k_fixed": K_FIXED, "pool_file": args.pool_file,
           "n_b_share": len(b), "b_share_codes": b,
           "results": res, "delta_pp": delta,
           "delta_pct": delta / w["mean_pp"] * 100 if w["mean_pp"] else None,
           "verdict": verdict,
           "note": "不改 Gate 3 判定（预登记冻结）。这是池污染的描述性量化。"}
    if os.path.dirname(args.report):
        os.makedirs(os.path.dirname(args.report), exist_ok=True)
    json.dump(out, open(args.report, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"report -> {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
