# -*- coding: utf-8 -*-
"""P0-2 附件：异常案例明细提取（只读，不改任何既有产物）。

产出：
  sp5_out/p04c_exc_fillfail.csv   成交失败逐笔（path0 + 全路径按原因汇总）
  sp5_out/p04c_exc_gaps.csv       最长停牌/缺失缺口（含被 ffill 掩盖的天数）
  sp5_out/p04c_exc_worstdd.json   最差回撤路径（#26）的峰谷定位与月度拆解
"""
import os
import sys
import json

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import p03_tradability as p03          # noqa: E402
import fill_engine as fe                   # noqa: E402
import p04b_ledger_independent as p04b     # noqa: E402

RD = p04b.RD
OUT = p04b.OUT
ST_FILLED, ST_LOCK_UP, ST_LOCK_DOWN = fe.ST_FILLED, fe.ST_LOCK_UP, fe.ST_LOCK_DOWN
ST_HALT, ST_NO_BAR = fe.ST_HALT, fe.ST_NO_BAR
KIND_NAME = {ST_FILLED: "FILLED", ST_LOCK_UP: "LIMIT_UP", ST_LOCK_DOWN: "LIMIT_DOWN",
             ST_HALT: "HALT", ST_NO_BAR: "NO_BAR"}


def kind_series(bar, st_months):
    """复刻 fill_engine.status_tables 的 kind 判定（逐日原因），用于异常归因。"""
    n = len(bar.d)
    lim = np.full(n, 0.10)
    b = fe.board_of(bar.code)
    if b == "STAR":
        lim[:] = 0.20
    elif b == "GEM":
        lim[:] = np.where(bar.d >= "20200824", 0.20, 0.10)
    elif b == "BSE":
        lim[:] = 0.30
    elif b == "SH_MAIN":
        lim[:] = 0.10
    elif b == "SZ_MAIN" and st_months:
        is_st = np.isin(np.array([d[:6] for d in bar.d]), np.array(sorted(st_months)))
        lim = np.where(is_st, 0.05, 0.10)
    r = np.full(n, np.nan)
    prev = bar.qc[:-1]
    with np.errstate(invalid="ignore", divide="ignore"):
        r[1:] = np.where(prev > 0, bar.qc[1:] / prev - 1.0, np.nan)
    lot = bar.hi == bar.lo
    vol_ok = np.isfinite(bar.vol) & (bar.vol > 0) & np.isfinite(r)
    k = np.full(n, ST_FILLED)
    k[lot & (r >= lim - fe.TOL)] = ST_LOCK_UP
    k[lot & (r <= -(lim - fe.TOL))] = ST_LOCK_DOWN
    k[lot & (r < lim - fe.TOL) & (r > -(lim - fe.TOL))] = ST_HALT
    k[~vol_ok] = ST_HALT
    k[~np.isfinite(r)] = ST_NO_BAR
    return bar.d, k


def main():
    panel = pd.read_parquet(p03.PANEL)
    st = pd.read_parquet(os.path.join(RD, "data", "st_sz_monthly.parquet"))
    st["date"] = pd.to_datetime(st["date"]).dt.strftime("%Y%m%d")
    st_months = {}
    for c, d, v in zip(st["code"], st["date"], st["is_st"]):
        if v:
            st_months.setdefault(c, set()).add(d[:6])

    by, months, paths, ns_list, monthly = p03.build_decisions(panel, 50)
    cal = fe.market_calendar()
    cal_pos = {d: i for i, d in enumerate(cal)}
    cal_arr = np.asarray(cal)
    buy = [fe.month_buy_date(m, cal_pos, cal) for m in months]

    # ---------- A. 成交失败逐笔（path0 全量 + 全 50 路径原因汇总） ----------
    rows = []
    agg = {}
    all_codes = sorted({c for h in paths for hs in h for c in hs})
    cache = {}
    for pi, holds in enumerate(paths):
        for t, m in enumerate(months):
            d = buy[t]
            for c in holds[t]:
                if c not in cache:
                    bar = fe.load_bars(c)
                    cache[c] = kind_series(bar, st_months) if bar is not None else None
                kv = cache[c]
                if kv is None:
                    kind = "NO_DATA"
                else:
                    dd, kk = kv
                    i = np.searchsorted(dd, d)
                    kind = KIND_NAME.get(int(kk[i]), "?") if (i < len(dd) and dd[i] == d) else "NO_BAR"
                if kind in ("FILLED", "LIMIT_DOWN"):
                    continue
                agg[kind] = agg.get(kind, 0) + 1
                if pi == 0:
                    bar = fe.load_bars(c)
                    rows.append({"month": m, "buy_date": d, "code": c, "reason": kind,
                                 "close": float(bar.qc[np.searchsorted(bar.d, d)])
                                 if bar is not None and np.searchsorted(bar.d, d) < len(bar.d) else np.nan})
    fdf = pd.DataFrame(rows)
    fdf.to_csv(os.path.join(OUT, "p04c_exc_fillfail.csv"), index=False, encoding="utf-8-sig")
    print(f"[A] path0 成交失败 {len(fdf)} 笔 / 覆盖月份 {fdf['month'].nunique() if len(fdf) else 0}；"
          f"全路径原因汇总 {agg}")

    # ---------- B. 最长停牌/缺失缺口 ----------
    gaps = []
    for c in all_codes:
        bar = fe.load_bars(c)
        if bar is None:
            continue
        arr = np.full(len(cal), np.nan)
        pos = np.searchsorted(cal_arr, bar.d)
        okm = (pos < len(cal)) & (cal_arr[np.clip(pos, 0, len(cal) - 1)] == bar.d)
        arr[pos[okm]] = bar.qc[okm].astype(float)
        # 只统计该股最早/最晚有效价之间的缺口（首尾 NaN 不计）
        valid = np.where(np.isfinite(arr))[0]
        if len(valid) < 2:
            continue
        lo, hi = valid[0], valid[-1]
        seg = arr[lo:hi + 1]
        isn = np.isnan(seg)
        if not isn.any():
            continue
        # 连续缺口段
        idx = np.where(isn)[0]
        brk = np.where(np.diff(idx) > 1)[0]
        starts = np.r_[idx[0], idx[brk + 1]]
        ends = np.r_[idx[brk], idx[-1]]
        for s, e in zip(starts, ends):
            n = e - s + 1
            if n >= 5:      # 只留 ≥5 个交易日的中长缺口
                gaps.append({"code": c, "start": cal[lo + s], "end": cal[lo + e],
                             "days": int(n), "last_px": float(seg[s - 1]) if s > 0 else np.nan})
    gdf = pd.DataFrame(gaps).sort_values("days", ascending=False)
    gdf.head(300).to_csv(os.path.join(OUT, "p04c_exc_gaps.csv"), index=False, encoding="utf-8-sig")
    print(f"[B] ≥5 日缺口 {len(gdf)} 段 / 涉及 {gdf['code'].nunique() if len(gdf) else 0} 只 / "
          f"最长 {int(gdf['days'].max()) if len(gdf) else 0} 交易日")

    # ---------- C. 最差回撤路径 #26 ----------
    holds = paths[26]
    codes = sorted({c for hs in holds for c in hs})
    _, px, tabs, cal, cal_pos, miss, fillc = p04b.load_px_bars(codes, st_months)
    buyn = buy[1:] + [cal[min(cal_pos[buy[-1]] + 1, len(cal) - 1)]]
    df = p04b.simulate(holds, 0, 0.20, px, tabs, months, buy, buyn, cal_pos, cal)
    df["nav"] = df["nav"].astype(float)
    trough_i = int(df["dd"].idxmin())
    peak_i = int(df["nav"].iloc[:trough_i + 1].idxmax())
    peak_d, trough_d = str(df["date"].iloc[peak_i]), str(df["date"].iloc[trough_i])
    m = p04b.metrics(df)
    # 峰→谷区间逐月（自然月）收益
    seg = df.iloc[peak_i:trough_i + 1].copy()
    seg["ym"] = seg["date"].str[:6]
    month_ret = seg.groupby("ym")["nav"].agg(lambda s: s.iloc[-1] / s.iloc[0] - 1)
    # 谷底所在月：持仓个股当月跌幅前 5（基于原始收盘价，非账本损益）
    ym_t = trough_d[:6]
    t_idx = [t for t, mm in enumerate(months) if mm == ym_t]
    worst_stocks = []
    if t_idx:
        t = t_idx[0]
        i0, i1 = cal_pos[buy[t]], cal_pos[buyn[t]]
        for c in holds[t]:
            p0, p1 = px[c][i0], px[c][i1]
            if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
                worst_stocks.append({"code": c, "ret": float(p1 / p0 - 1)})
        worst_stocks = sorted(worst_stocks, key=lambda x: x["ret"])[:5]
    peak_val, trough_val = float(df["nav"].iloc[peak_i]), float(df["nav"].iloc[trough_i])
    detail = {
        "path": 26, "start": "2005-01 (全样本)", "weight": 0.20,
        "ann": m["ann"], "mdd": m["mdd"], "final_nav": m["final"],
        "peak_date": peak_d, "trough_date": trough_d,
        "peak_nav": peak_val, "trough_nav": trough_val,
        "dd_from_peak": trough_val / peak_val - 1,
        "trading_days": int(trough_i - peak_i),
        "monthly_returns_in_dd": {k: float(v) for k, v in month_ret.items()},
        "trough_month": ym_t,
        "trough_month_worst_holdings": worst_stocks,
        "silent_fill_days_path26": int(fillc),
        "miss_codes_path26": int(miss),
        "n_holdings_trough_month": len(holds[t_idx[0]]) if t_idx else None,
    }
    with open(os.path.join(OUT, "p04c_exc_worstdd.json"), "w", encoding="utf-8") as f:
        json.dump(detail, f, ensure_ascii=False, indent=2)
    print(f"[C] path26 MDD {m['mdd']*100:+.2f}% | 峰 {peak_d} -> 谷 {trough_d} "
          f"({detail['trading_days']} 交易日, {detail['dd_from_peak']*100:+.2f}%)")


if __name__ == "__main__":
    main()
