# -*- coding: utf-8 -*-
"""P0-2 附件 3：两处口径修正（只读诊断）。

R1 死码槽位重算：只统计「个股最后 bar 之后仍被列为目标持仓」的槽位（真·幽灵仓位），
   而不是「该股在整个历史中被持有的槽位」。
R2 最差路径归因修正：按**调仓窗口**（buy[t] → buy[t+1]）计算收益，定位最大亏损窗口，
   再拆该窗口内个股涨跌；原归因误用了谷底之后的窗口。
"""
import os
import sys
import json

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import fill_engine as fe                    # noqa: E402
import p03_tradability as p03               # noqa: E402
import p04b_ledger_independent as p04b      # noqa: E402

OUT = p04b.OUT
RD = p04b.RD


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
    buy = [fe.month_buy_date(m, cal_pos, cal) for m in months]
    nxt = buy[1:] + [cal[min(cal_pos[buy[-1]] + 1, len(cal) - 1)]]

    # ---------- R1 幽灵仓位槽位 ----------
    all_codes = sorted({c for h in paths for hs in h for c in hs})
    last_bar = {}
    for c in all_codes:
        b = fe.load_bars(c)
        if b is not None:
            last_bar[c] = b.d[-1]
    ghost = {}          # code -> 停更后仍被持有的 (path, month, buy_date) 计数
    n_ghost_slot = 0
    n_slot = 0
    rows = []
    for pi, holds in enumerate(paths):
        for t, hs in enumerate(holds):
            d = buy[t]
            for c in hs:
                n_slot += 1
                lb = last_bar.get(c)
                if lb is None or d > lb:        # 停更后（或无任何行情）仍被列为目标
                    n_ghost_slot += 1
                    g = ghost.setdefault(c, {"code": c, "last_bar": lb, "months": 0,
                                             "first_ghost_month": months[t], "paths": set()})
                    g["months"] += 1
                    g["paths"].add(pi)
    for c, g in ghost.items():
        rows.append({"code": c, "last_bar": g["last_bar"], "ghost_months_total": g["months"],
                     "first_ghost_month": g["first_ghost_month"],
                     "n_paths_affected": len(g["paths"])})
    gdf = pd.DataFrame(rows).sort_values("ghost_months_total", ascending=False)
    gdf.to_csv(os.path.join(OUT, "p04d_dead_codes.csv"), index=False, encoding="utf-8-sig")
    print(f"[R1] 幽灵槽位 {n_ghost_slot}/{n_slot} = {n_ghost_slot/n_slot*100:.3f}% | "
          f"涉及 {len(gdf)} 只")
    if len(gdf):
        print(gdf.head(6).to_string(index=False))

    # ---------- R2 最差路径按调仓窗口归因 ----------
    holds26 = paths[26]
    codes26 = sorted({c for hs in holds26 for c in hs})
    _, px, tabs, cal, cal_pos, miss26, fill26 = p04b.load_px_bars(codes26, st_months)
    df = p04b.simulate(holds26, 0, 0.20, px, tabs, months, buy, nxt, cal_pos, cal)
    ti = int(df["dd"].idxmin())
    pi = int(df["nav"].iloc[:ti + 1].idxmax())
    # 逐窗口收益
    dfi = df.set_index("date")
    win = []
    for t in range(len(months)):
        d0, d1 = buy[t], nxt[t]
        seg = dfi.loc[d0:d1]
        if len(seg) < 2:
            continue
        win.append({"t": t, "month": months[t], "buy": d0, "end": d1,
                    "ret": float(seg["nav"].iloc[-1] / seg["nav"].iloc[0] - 1)})
    wdf = pd.DataFrame(win)
    wdf.to_csv(os.path.join(OUT, "p04c_exc_worst_windows.csv"), index=False, encoding="utf-8-sig")
    ww = wdf.loc[wdf["ret"].idxmin()]
    # 峰谷所在窗口
    peak_d, trough_d = str(df["date"].iloc[pi]), str(df["date"].iloc[ti])
    # 最大亏损窗口的个股拆解
    t = int(ww["t"])
    i0, i1 = cal_pos[buy[t]], cal_pos[nxt[t]]
    stocks = []
    for c in holds26[t]:
        p0, p1 = px[c][i0], px[c][i1]
        if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
            stocks.append({"code": c, "ret_pct": float((p1 / p0 - 1) * 100)})
    stocks = sorted(stocks, key=lambda x: x["ret_pct"])
    # 峰谷区间逐窗口汇总
    span = wdf[(wdf["buy"] >= peak_d) & (wdf["buy"] < trough_d)]
    detail = {
        "path": 26, "weight": 0.20, "ann": p04b.metrics(df)["ann"],
        "mdd": p04b.metrics(df)["mdd"],
        "peak_date": peak_d, "trough_date": trough_d,
        "peak_nav": float(df["nav"].iloc[pi]), "trough_nav": float(df["nav"].iloc[ti]),
        "trading_days_peak_to_trough": int(ti - pi),
        "windows_in_drawdown": span[["month", "ret"]].assign(
            ret=lambda x: (x["ret"] * 100).round(2)).to_dict("records"),
        "worst_window": {"month": str(ww["month"]), "ret_pct": float(ww["ret"] * 100),
                         "buy": str(ww["buy"]), "end": str(ww["end"])},
        "worst_window_holdings": stocks,
        "worst_window_n_holdings": len(holds26[t]),
        "worst_window_negative_share": float(np.mean([s["ret_pct"] < 0 for s in stocks]) * 100)
        if stocks else None,
        "silent_fill_days_path26": int(fill26), "miss_codes_path26": int(miss26),
        "ghost_slot_share_pct": round(n_ghost_slot / n_slot * 100, 3),
    }
    with open(os.path.join(OUT, "p04c_exc_worstdd.json"), "w", encoding="utf-8") as f:
        json.dump(detail, f, ensure_ascii=False, indent=2)
    print(f"[R2] MDD {detail['mdd']*100:+.2f}% 峰 {peak_d} 谷 {trough_d} "
          f"({detail['trading_days_peak_to_trough']} 交易日)")
    print(f"     最大亏损窗口 {ww['month']} ({ww['buy']}→{ww['end']}) {ww['ret']*100:+.2f}% "
          f"持仓 {len(holds26[t])} 只 下跌占比 {detail['worst_window_negative_share']:.0f}%")
    print("     个股:", [(s['code'], round(s['ret_pct'], 1)) for s in stocks])


if __name__ == "__main__":
    main()
