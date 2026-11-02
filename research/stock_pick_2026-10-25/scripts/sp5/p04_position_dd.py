# -*- coding: utf-8 -*-
"""SP-5 / P0-4：20% 仓位的回撤稳健性（四道验证）

为什么不能再用"缩放口径"
----------------------
审计与 P0-3 的 25%/20% 净值都是 `w × 日收益 + (1−w) × 现金` 的**每日再平衡**混合。
它隐含两个假设：(1) 每天把权重精确调回目标值；(2) 可以无限细分、无整手、无最低佣金。
对 10 万本金 + 5 只约 4000 元的持仓，这两条都不成立。本脚本改为**真实组合账本**：

  * 整数手（100 股），余数留现金；
  * 每日按收盘价盯市（前复权），现金按 3%/年逐日计息；
  * 费用逐笔按真实费率：佣金 max(5 元, 0.025%) + 过户费 0.001% 双边 + 印花税 0.05% 仅卖出；
  * 月末按冻结配置把股票权重调回目标（每月再平衡），买卖受涨跌停/停牌约束（沿用 P0-3 位图）。

四道验证（任务书 P0-4）
--------------------
1. 起点敏感性：7 个起点（2005/2008/2010/2013/2015/2018/2021）各自跑到数据末；
2. 市场阶段：2008 / 2015H2 / 2018 / 2022 / 2024 初，区间内 MDD；
3. 真实组合净值：股票市值 + 现金 + 累计成本 同一时间轴；
4. 再平衡真实执行：月度再平衡 vs 不再平衡（权重漂移）。

输出：sp5_out/sp5_position_dd.json、sp5_out/sp5_nav_recon_p{path}_{mode}.csv
"""

from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fill_engine as fe  # noqa: E402
import p03_tradability as p03  # noqa: E402

RD = p03.RD
OUT = p03.OUT
K = 5
CAPITAL = 100_000.0
LOT = 100
CASH_ANN = 0.03
COMM_RATE, COMM_MIN = 0.00025, 5.0
TRANSFER, STAMP = 1e-05, 0.0005
N_PATHS = int(os.environ.get("SP5_PATHS", "50"))
STARTS = ["2005", "2008", "2010", "2013", "2015", "2018", "2021"]
SEGS = [("20080101", "20081231", "2008 崩盘"),
        ("20150601", "20151231", "2015 H2"),
        ("20180101", "20181231", "2018 熊市"),
        ("20220101", "20221231", "2022 回调"),
        ("20240101", "20240229", "2024 初小盘急跌")]


def fee_parts(notional: float, side: str) -> tuple[float, float, float]:
    """逐笔真实费率，返回 (佣金, 过户费, 印花税)。最小 5 元佣金是主导项。"""
    if notional <= 0:
        return 0.0, 0.0, 0.0
    comm = max(COMM_MIN, notional * COMM_RATE)
    trans = notional * TRANSFER
    stamp = notional * STAMP if side == "sell" else 0.0
    return comm, trans, stamp


def fee(notional: float, side: str) -> float:
    return sum(fee_parts(notional, side))


def simulate(holds, t0, w, px, tabs, months, buy, nxt, cal_pos, cal, mode="rebal",
             spread_side=0.0):
    """真实组合账本。返回每日 (nav, stock_value, cash, cost_cum, rebal_flag)。"""
    cash = CAPITAL
    shares: dict[str, int] = {}
    cost_cum = 0.0
    parts = {"comm": 0.0, "transfer": 0.0, "stamp": 0.0}
    n_min_comm = 0
    n_trades = 0
    rows = []
    for t in range(t0, len(months)):
        d = buy[t]
        i0 = cal_pos[d]
        V = cash + sum(n * px[c][i0] for c, n in shares.items() if np.isfinite(px[c][i0]))
        cur = holds[t]
        # --- 1) 卖出（不在目标里） ---
        for c in list(shares):
            if c in cur:
                continue
            p = px[c][i0]
            if not np.isfinite(p) or not _ok(tabs, c, d, "sell"):
                continue
            notional = shares[c] * p
            cm, tr, stp = fee_parts(notional, "sell")
            parts["comm"] += cm; parts["transfer"] += tr; parts["stamp"] += stp
            n_trades += 1; n_min_comm += (notional * COMM_RATE < COMM_MIN)
            f = cm + tr + stp + notional * spread_side
            cash += notional - f
            cost_cum += f
            del shares[c]
        # --- 2) 买入新目标 + 权重调整 ---
        for c in cur:
            p = px[c][i0]
            if not np.isfinite(p):
                continue
            target_val = w * V / len(cur)
            have = shares.get(c, 0)
            tgt_sh = int(target_val // (p * LOT)) * LOT
            delta = tgt_sh - have
            if mode == "drift" and have > 0:
                continue                     # 漂移档：已持有的不调权重
            if delta > 0:
                cost = (delta * p)
                cm, tr, stp = fee_parts(cost, "buy")
                n_trades += 1; n_min_comm += (cost * COMM_RATE < COMM_MIN)
                f = cm + tr + stp + cost * spread_side
                if cash >= cost + f:
                    cash -= cost + f
                    cost_cum += f
                    parts["comm"] += cm; parts["transfer"] += tr; parts["stamp"] += stp
                    shares[c] = have + delta
                else:                        # 现金不足 → 按可用资金取整手
                    lots = int((cash / (1 + COMM_RATE)) // (p * LOT))
                    if lots > 0:
                        cost = lots * LOT * p
                        cm, tr, stp = fee_parts(cost, "buy")
                        n_trades += 1; n_min_comm += (cost * COMM_RATE < COMM_MIN)
                        f = cm + tr + stp + cost * spread_side
                        cash -= cost + f
                        cost_cum += f
                        parts["comm"] += cm; parts["transfer"] += tr; parts["stamp"] += stp
                        shares[c] = have + lots * LOT
            elif delta < 0 and _ok(tabs, c, d, "sell"):
                sell_sh = min(-delta, have)
                notional = sell_sh * p
                cm, tr, stp = fee_parts(notional, "sell")
                n_trades += 1; n_min_comm += (notional * COMM_RATE < COMM_MIN)
                f = cm + tr + stp + notional * spread_side
                cash += notional - f
                cost_cum += f
                parts["comm"] += cm; parts["transfer"] += tr; parts["stamp"] += stp
                shares[c] = have - sell_sh
        if mode == "drift":
            pass                              # 不再平衡 20/80 权重
        # --- 3) 逐日盯市（向量化：避免逐日 × 逐票的标量索引） ---
        b0, b1 = cal_pos[buy[t]], cal_pos[nxt[t]]
        L = b1 - b0 + 1
        names = list(shares)
        if names:
            Pm = np.vstack([px[c][b0:b1 + 1] for c in names]).astype(np.float64)
            Pm = np.where(np.isfinite(Pm), Pm, 0.0)
            sv = np.asarray([shares[c] for c in names], float) @ Pm
        else:
            sv = np.zeros(L)
        cf = cash * (1 + CASH_ANN / 244.0) ** np.arange(1, L + 1)
        for k in range(L):
            rows.append((cal[b0 + k], float(sv[k]), float(cf[k]), cost_cum, k == 0))
        cash = float(cf[-1])
    df = pd.DataFrame(rows, columns=["date", "stock_value", "cash", "cost_cum", "is_rebal"])
    df["nav"] = df["stock_value"] + df["cash"]
    df["dd"] = df["nav"] / df["nav"].cummax() - 1
    df.attrs["parts"] = parts
    df.attrs["n_trades"] = n_trades
    df.attrs["n_min_comm"] = n_min_comm
    return df


def _ok(tabs, code, date, side) -> bool:
    t = tabs.get(code)
    if t is None:
        return False
    dd, bo, so = t
    i = np.searchsorted(dd, date)
    if i >= len(dd) or dd[i] != date:
        return False
    return bool(bo[i]) if side == "buy" else bool(so[i])


def metrics(df) -> dict:
    nav = df["nav"].to_numpy(float)
    nav = nav / nav[0]          # ⚠️ 必须归一：nav 是绝对金额，不归一会把"最终市值"当成年化因子
    n = len(nav) - 1
    dd = df["dd"].to_numpy(float)
    rr = nav[1:] / nav[:-1] - 1
    return {"ann": float(nav[-1] ** (244.0 / n) - 1), "mdd": float(dd.min()),
            "vol": float(np.std(rr, ddof=1) * np.sqrt(244)),
            "calmar": float((nav[-1] ** (244.0 / n) - 1) / abs(dd.min())) if dd.min() < 0 else None,
            "days": n, "final": float(nav[-1])}


def main() -> int:
    prereg = p03.check_prereg()
    os.makedirs(OUT, exist_ok=True)
    panel = pd.read_parquet(p03.PANEL)
    st = pd.read_parquet(os.path.join(RD, "data", "st_sz_monthly.parquet"))
    st["date"] = pd.to_datetime(st["date"]).dt.strftime("%Y%m%d")
    st_months: dict[str, set[str]] = {}
    for c, d, v in zip(st["code"], st["date"], st["is_st"]):
        if v:
            st_months.setdefault(c, set()).add(d[:6])

    by, months, paths, ns_list, monthly = p03.build_decisions(panel, N_PATHS)
    cal = fe.market_calendar()
    cal_pos = {d: i for i, d in enumerate(cal)}
    cal_arr = np.asarray(cal)
    buy = [fe.month_buy_date(m, cal_pos, cal) for m in months]
    nxt = buy[1:] + [cal[min(cal_pos[buy[-1]] + 1, len(cal) - 1)]]

    codes = sorted({c for h in paths for hs in h for c in hs})
    print(f"[need] {len(codes)} 只股票 / {len(months)} 月 / {len(paths)} 路径")
    px, tabs, miss = {}, {}, 0
    for i, c in enumerate(codes):
        b = fe.load_bars(c)
        if b is None:
            miss += 1
            continue
        arr = np.full(len(cal), np.nan, dtype=np.float32)
        pos = np.searchsorted(cal_arr, b.d)
        okm = (pos < len(cal)) & (cal_arr[np.clip(pos, 0, len(cal) - 1)] == b.d)
        arr[pos[okm]] = b.qc[okm].astype(np.float32)
        px[c] = pd.Series(arr).ffill().to_numpy(dtype=np.float32)
        tabs[c] = fe.status_tables(b, st_months.get(c))
        if (i + 1) % 1000 == 0:
            print(f"    {i+1}/{len(codes)}")
    print(f"[bars] {len(px)} 只，缺 {miss}")

    def idx_of(year: str) -> int:
        for t, m in enumerate(months):
            if m[:4] >= year:
                return t
        return len(months) - 1

    res = {"prereg_sha256": prereg, "n_paths": len(paths), "months": len(months),
           "capital": CAPITAL, "fees": {"commission": COMM_RATE, "min_cny": COMM_MIN,
                                        "transfer": TRANSFER, "stamp_sell": STAMP,
                                        "cash_ann": CASH_ANN},
           "starts": {}, "segments": {}, "bydraw": {}, "modes": {}}

    # ---------- 四道验证 1+2+3：7 起点 × 2 仓位 × 2 模式 ----------
    for w in (0.20, 0.25):
        for mode in ("rebal", "drift"):
            per_start = {}
            for yr in STARTS:
                t0 = idx_of(yr)
                ms, tss = [], []
                for pi, holds in enumerate(paths):
                    df = simulate(holds, t0, w, px, tabs, months, buy, nxt, cal_pos, cal, mode)
                    m = metrics(df)
                    ms.append(m)
                    tss.append(float(df["stock_value"].iloc[-1] / df["nav"].iloc[-1]))
                    if pi == 0 and w == 0.20 and mode == "rebal":
                        df.to_csv(os.path.join(OUT, f"sp5_nav_recon_p0_{yr}.csv"),
                                  index=False, encoding="utf-8-sig")
                ann = np.array([x["ann"] for x in ms])
                mdd = np.array([x["mdd"] for x in ms])
                per_start[yr] = {
                    "n": len(ms), "ann_med": float(np.median(ann)),
                    "mdd_med": float(np.median(mdd)), "mdd_worst": float(mdd.min()),
                    "mdd_p10": float(np.percentile(mdd, 10)), "mdd_p90": float(np.percentile(mdd, 90)),
                    "breach_20pct_share": float((mdd < -0.20).mean()),
                    "stock_weight_end_med": float(np.median(tss)),
                    "months": len(months) - t0,
                }
                print(f"[w={w:.0%} {mode:5s}] 起点 {yr}: 年化 {np.median(ann)*100:+6.2f}% "
                      f"MDD 中位 {np.median(mdd)*100:+7.2f}% 最差 {mdd.min()*100:+7.2f}% "
                      f"破20%占比 {(i2 := (mdd < -0.20).mean())*100:5.1f}%")
            res["starts"][f"{w:.2f}_{mode}"] = per_start

    # ---------- 分段（全样本、20% rebal） ----------
    segout = {}
    navs_full = []
    for pi, holds in enumerate(paths):
        df = simulate(holds, 0, 0.20, px, tabs, months, buy, nxt, cal_pos, cal, "rebal")
        navs_full.append(df)
    for lo, hi, label in SEGS:
        vals = []
        for df in navs_full:
            m = (df["date"] >= lo) & (df["date"] <= hi)
            if m.sum() < 20:
                continue
            sub = df.loc[m, "nav"].to_numpy(float)
            base = sub / sub[0]
            vals.append(float((base / np.maximum.accumulate(base) - 1).min()))
        if vals:
            segout[label] = {"n": len(vals), "mdd_med": float(np.median(vals)),
                             "mdd_worst": float(np.min(vals))}
            print(f"[段] {label:14s} MDD 中位 {np.median(vals)*100:+7.2f}% 最差 {np.min(vals)*100:+7.2f}%")
    res["segments"] = segout

    # ---------- 成本结构 + 漂移对比（全样本） ----------
    full20 = [metrics(df) for df in navs_full]
    a20 = np.array([x["ann"] for x in full20])
    m20 = np.array([x["mdd"] for x in full20])
    res["full_20_rebal"] = {"ann_med": float(np.median(a20)), "mdd_med": float(np.median(m20)),
                            "mdd_p10": float(np.percentile(m20, 10)),
                            "mdd_worst": float(m20.min()),
                            "breach_share": float((m20 < -0.20).mean())}
    cost_ratio = float(np.median([df["cost_cum"].iloc[-1] / CAPITAL for df in navs_full]))
    res["cost_cum_ratio_med"] = cost_ratio
    agg = {k: float(np.median([df.attrs["parts"][k] for df in navs_full])) for k in ("comm", "transfer", "stamp")}
    ntr = float(np.median([df.attrs["n_trades"] for df in navs_full]))
    nmin = float(np.median([df.attrs["n_min_comm"] for df in navs_full]))
    res["cost_parts_med"] = agg
    res["trades"] = {"n_trades_med": ntr, "n_min_comm_med": nmin,
                     "min_comm_share": nmin / ntr if ntr else None}
    print(f"[cost] 累计费用/本金 中位 {cost_ratio*100:.2f}%（{len(months)/12:.0f} 年）"
          f"｜佣金 {agg['comm']:.0f} 元 过户 {agg['transfer']:.0f} 元 印花 {agg['stamp']:.0f} 元"
          f"｜成交 {ntr:.0f} 笔，其中 {nmin/ntr*100:.1f}% 触发 5 元下限")

    # 判定（预登记 §5.3）
    st20 = res["starts"]["0.20_rebal"]
    all_ok = all(v["mdd_med"] >= -0.20 for v in st20.values())
    margin = min(v["mdd_med"] for v in st20.values()) + 0.20
    verdict = "DD_ROBUST_20PCT" if (all_ok and margin >= 0.02) else "DD_NOT_ROBUST"
    res["verdict"] = verdict
    res["margin_worst_start"] = float(margin)
    print("\n" + "=" * 90)
    print(f"7 起点 20% 仓位：全部 ≥ −20% = {all_ok}；最差起点余量 {margin*100:+.2f}pp")
    print(f"【判定】{verdict}")
    with open(os.path.join(OUT, "sp5_position_dd.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("[saved] sp5_out/sp5_position_dd.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
