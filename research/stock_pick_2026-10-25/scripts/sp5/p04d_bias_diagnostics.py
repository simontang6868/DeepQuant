# -*- coding: utf-8 -*-
"""P0-2 附件 2：异常偏置量化（只读诊断，不改变任何规则/参数）。

三项诊断：
  D1 退市/长期停牌股残留：目标池中出现"最后 bar 早于数据末段"的代码，账本按最后价无限期 carry
  D2 买入端成交约束敏感度：施加 buy_ok（涨停/停牌不可买）后 7 起点的 ann/mdd 变化
  D3 无静默填充敏感度：缺失日按"不可成交"处理（不 carry 价格）后的 ann/mdd 变化
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
STARTS = ["2005", "2008", "2010", "2013", "2015", "2018", "2021"]


def simulate2(holds, t0, w, px, tabs, months, buy, nxt, cal_pos, cal,
              enforce_buy=False, strict_missing=False, px_raw=None):
    """在 p04b.simulate 基础上加两个开关（默认关闭 = 与 p04 完全一致）。"""
    cash = p04b.CAPITAL
    shares = {}
    cost_cum = 0.0
    rows = []
    buyable = {}

    def tradable(c, d):
        return p04b._ok(tabs, c, d, "buy")

    for t in range(t0, len(months)):
        d = buy[t]
        i0 = cal_pos[d]
        V = cash + sum(n * px[c][i0] for c, n in shares.items() if c in px)
        cur = holds[t]
        for c in list(shares):
            if c in cur:
                continue
            p = px[c][i0]
            if not np.isfinite(p) or not p04b._ok(tabs, c, d, "sell"):
                continue
            notional = shares[c] * p
            cm, tr, stp = p04b.fee_parts(notional, "sell")
            f = cm + tr + stp + notional * p04b.SPREAD
            cash += notional - f
            cost_cum += f
            del shares[c]
        for c in cur:
            p = px[c][i0]
            if strict_missing and px_raw is not None and not np.isfinite(px_raw[c][i0]):
                continue                      # 缺失日不可交易，也不 carry 价格
            if not np.isfinite(p):
                continue
            if enforce_buy and not tradable(c, d):
                continue                      # 涨停/停牌不可买（买入端约束）
            target_val = w * V / len(cur)
            have = shares.get(c, 0)
            tgt_sh = int(target_val // (p * p04b.LOT)) * p04b.LOT
            delta = tgt_sh - have
            if delta > 0:
                cost = delta * p
                cm, tr, stp = p04b.fee_parts(cost, "buy")
                f = cm + tr + stp + cost * p04b.SPREAD
                if cash >= cost + f:
                    cash -= cost + f
                    cost_cum += f
                    shares[c] = have + delta
                else:
                    lots = int((cash / (1 + p04b.COMM_RATE)) // (p * p04b.LOT))
                    if lots > 0:
                        cost = lots * p04b.LOT * p
                        cm, tr, stp = p04b.fee_parts(cost, "buy")
                        f = cm + tr + stp + cost * p04b.SPREAD
                        cash -= cost + f
                        cost_cum += f
                        shares[c] = have + lots * p04b.LOT
            elif delta < 0 and p04b._ok(tabs, c, d, "sell"):
                sell_sh = min(-delta, have)
                notional = sell_sh * p
                cm, tr, stp = p04b.fee_parts(notional, "sell")
                f = cm + tr + stp + notional * p04b.SPREAD
                cash += notional - f
                cost_cum += f
                shares[c] = have - sell_sh
        b0, b1 = cal_pos[buy[t]], cal_pos[nxt[t]]
        L = b1 - b0 + 1
        names = list(shares)
        sv = np.zeros(L)
        if names:
            Pm = np.vstack([px[c][b0:b1 + 1] for c in names]).astype(np.float64)
            sv = np.asarray([shares[c] for c in names], float) @ Pm
        cf = cash * (1 + p04b.CASH_ANN / p04b.TRADAYS) ** np.arange(1, L + 1)
        for k in range(L):
            rows.append((cal[b0 + k], float(sv[k]), float(cf[k]), cost_cum))
        cash = float(cf[-1])
    df = pd.DataFrame(rows, columns=["date", "stock_value", "cash", "cost_cum"])
    df["nav"] = df["stock_value"] + df["cash"]
    df["dd"] = df["nav"] / df["nav"].cummax() - 1
    return df


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
    print(f"[base] {len(months)} 月 {months[0]}..{months[-1]} / 路径 {len(paths)}")

    # ---------- D1 退市/长期停牌残留 ----------
    all_codes = sorted({c for h in paths for hs in h for c in hs})
    last_bar, first_bar = {}, {}
    for c in all_codes:
        b = fe.load_bars(c)
        if b is None:
            continue
        first_bar[c], last_bar[c] = b.d[0], b.d[-1]
    cutoff = cal[-90]                      # 末段 90 个交易日
    dead = {c for c, d in last_bar.items() if d < cutoff}
    # 统计这些死码在目标池中出现的月数、是否进入 path0/path26
    occ = {}
    for pi, holds in enumerate(paths):
        for t, hs in enumerate(holds):
            for c in hs:
                if c in dead:
                    occ.setdefault(pi, set()).add(c)
    n_slot = sum(1 for holds in paths for hs in holds for c in hs if c in dead)
    n_total = sum(len(hs) for holds in paths for hs in holds)
    rows = []
    for c in sorted(dead):
        n_months = sum(1 for holds in paths[:1] for hs in holds if c in hs)
        rows.append({"code": c, "first_bar": first_bar.get(c), "last_bar": last_bar.get(c),
                     "months_in_path0": n_months,
                     "in_path0": bool(c in occ.get(0, set())),
                     "in_path26": bool(c in occ.get(26, set()))})
    d1 = pd.DataFrame(rows).sort_values("last_bar")
    d1.to_csv(os.path.join(OUT, "p04d_dead_codes.csv"), index=False, encoding="utf-8-sig")
    print(f"[D1] 目标池 {len(all_codes)} 只 / 末段 90 日无行情 {len(dead)} 只 / "
          f"占槽位 {n_slot}/{n_total} = {n_slot/n_total*100:.3f}%")
    if len(d1):
        print("     最早停更 5 只:", d1.head(5)[["code", "last_bar", "months_in_path0"]].to_dict("records"))

    # ---------- D2/D3 path0 敏感度 ----------
    holds0 = paths[0]
    codes0 = sorted({c for hs in holds0 for c in hs})
    px_raw, px, tabs, cal, cal_pos, miss, fillc = p04b.load_px_bars(codes0, st_months)
    res = {}
    for tag, kw in (("BASE", {}),
                    ("BUY_OK", {"enforce_buy": True}),
                    ("STRICT_MISSING", {"strict_missing": True, "px_raw": px_raw}),
                    ("BOTH", {"enforce_buy": True, "strict_missing": True, "px_raw": px_raw})):
        per = {}
        for yr in STARTS:
            t0 = next(t for t, m in enumerate(months) if m[:4] >= yr)
            df = simulate2(holds0, t0, 0.20, px, tabs, months, buy, nxt, cal_pos, cal, **kw)
            m = p04b.metrics(df)
            per[yr] = {"ann": float(m["ann"]), "mdd": float(m["mdd"]),
                       "final": float(m["final"]),
                       "uninvested_end": float(df["cash"].iloc[-1] / df["nav"].iloc[-1])}
        res[tag] = per
        print(f"[{tag}] " + " | ".join(f"{y}: {per[y]['ann']*100:+.2f}%/{per[y]['mdd']*100:+.2f}%"
                                       for y in STARTS))
    d2 = {}
    for yr in STARTS:
        b = res["BASE"][yr]
        d2[yr] = {k: {"ann_delta_pp": (res[k][yr]["ann"] - b["ann"]) * 100,
                      "mdd_delta_pp": (res[k][yr]["mdd"] - b["mdd"]) * 100,
                      "ann": res[k][yr]["ann"], "mdd": res[k][yr]["mdd"],
                      "uninvested_end": res[k][yr]["uninvested_end"]}
                  for k in ("BUY_OK", "STRICT_MISSING", "BOTH")}
        d2[yr]["BASE"] = {"ann": b["ann"], "mdd": b["mdd"],
                          "uninvested_end": b["uninvested_end"]}
    with open(os.path.join(OUT, "p04d_bias_sensitivity.json"), "w", encoding="utf-8") as f:
        json.dump({"path": 0, "weight": 0.20, "silent_fill_days": int(fillc),
                   "by_start": d2}, f, ensure_ascii=False, indent=2)
    print("[saved] p04d_bias_sensitivity.json")

    # ---------- 修正版最差路径归因（谷底所在交易月） ----------
    holds26 = paths[26]
    codes26 = sorted({c for hs in holds26 for c in hs})
    _, px26, tabs26, cal, cal_pos, miss26, fill26 = p04b.load_px_bars(codes26, st_months)
    df26 = p04b.simulate(holds26, 0, 0.20, px26, tabs26, months, buy, nxt, cal_pos, cal)
    ti = int(df26["dd"].idxmin())
    pi = int(df26["nav"].iloc[:ti + 1].idxmax())
    trough_d = str(df26["date"].iloc[ti])
    t_idx = max(t for t, bd in enumerate(buy) if bd <= trough_d)
    worst = []
    i0, i1 = cal_pos[buy[t_idx]], cal_pos[nxt[t_idx]]
    for c in holds26[t_idx]:
        p0, p1 = px26[c][i0], px26[c][i1]
        if np.isfinite(p0) and np.isfinite(p1) and p0 > 0:
            worst.append({"code": c, "ret_pct": float((p1 / p0 - 1) * 100)})
    worst = sorted(worst, key=lambda x: x["ret_pct"])[:5]
    out = json.load(open(os.path.join(OUT, "p04c_exc_worstdd.json"), encoding="utf-8"))
    out.update({"peak_date": str(df26["date"].iloc[pi]), "trough_date": trough_d,
                "peak_nav": float(df26["nav"].iloc[pi]), "trough_nav": float(df26["nav"].iloc[ti]),
                "trading_days": int(ti - pi),
                "trough_month": months[t_idx], "trough_buy_date": buy[t_idx],
                "trough_month_n_holdings": len(holds26[t_idx]),
                "trough_month_worst_holdings": worst})
    with open(os.path.join(OUT, "p04c_exc_worstdd.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[fix] 最差路径 峰 {out['peak_date']} 谷 {trough_d} 月={months[t_idx]} "
          f"持仓 {len(holds26[t_idx])} 只 / 最差个股 {worst[:2]}")


if __name__ == "__main__":
    main()
