# -*- coding: utf-8 -*-
"""SP-5 / P0-2：独立重建组合账本（不调用 p04_position_dd.simulate / metrics）

独立性声明
----------
本脚本**不 import p04_position_dd**，账本的每一步（费用、整手、逐日盯市、现金计息、
回撤/年化）全部从零实现。仅复用：
  * p03.build_decisions  —— 策略信号（目标持仓序列），属"输入"非"净值函数"；
  * fill_engine.load_bars / status_tables —— 取数 + 成交可行性原语（非净值函数）。
目的：在不调用现有净值函数的前提下，独立重算 NAV，与 p04 的已存产物逐日/逐分布对账。

与 p04 的关键差异（审计重点）
---------------------------
p04 对缺失行情做 `pd.Series(arr).ffill()`（前向填充）→ 静默填补停牌/数据缺口。
本脚本**不做前向填充**：缺失日价格 = 最近已知价（显式 carry），并**计数**这些天数，
既复现 p04 的 NAV 水平（对持有股 carry≈ffill），又把"静默填补"这一事实暴露出来量化。
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
SPREAD = 0.0  # p04 默认 spread_side=0（不含 0.10% 价差），与已存 CSV 对齐
TRADAYS = 244.0


def fee_parts(notional, side):
    if notional <= 0:
        return 0.0, 0.0, 0.0
    comm = max(COMM_MIN, notional * COMM_RATE)
    trans = notional * TRANSFER
    stamp = notional * STAMP if side == "sell" else 0.0
    return comm, trans, stamp


def _ok(tabs, code, date, side):
    t = tabs.get(code)
    if t is None:
        return False
    dd, bo, so = t
    i = np.searchsorted(dd, date)
    if i >= len(dd) or dd[i] != date:
        return False
    return bool(bo[i]) if side == "buy" else bool(so[i])


def load_px_bars(codes, st_months):
    """返回 (px_raw, px_filled, tabs, cal, cal_pos, miss, fill_count)。

    px_raw  : 日历对齐、缺失=NaN，不做任何填充（用于量化静默填充）
    px_filled: 复现 p04 的**全局前向填充**（跨月继承最近已知价）+ 头部 NaN→0，
               用于与 p04 已存产物逐日对账。
    fill_count: 被静默前向填充的日历天数（跨月缺口 + 停牌段），即 p04 隐藏的填补量。
    """
    cal = fe.market_calendar()
    cal_pos = {d: i for i, d in enumerate(cal)}
    cal_arr = np.asarray(cal)
    px_raw, px_filled, tabs, miss = {}, {}, {}, 0
    fill_count = 0
    for c in codes:
        b = fe.load_bars(c)
        if b is None:
            miss += 1
            continue
        arr = np.full(len(cal), np.nan, dtype=np.float64)
        pos = np.searchsorted(cal_arr, b.d)
        okm = (pos < len(cal)) & (cal_arr[np.clip(pos, 0, len(cal) - 1)] == b.d)
        arr[pos[okm]] = b.qc[okm].astype(np.float64)
        px_raw[c] = arr
        # 全局前向填充（复现 p04 ffill）
        filled = arr.copy()
        last = np.nan
        for i in range(len(filled)):
            if np.isfinite(filled[i]):
                last = filled[i]
            elif np.isfinite(last):
                filled[i] = last
        n_nan_raw = int(np.isnan(arr).sum())
        n_nan_after = int(np.isnan(filled).sum())
        fill_count += (n_nan_raw - n_nan_after)
        filled = np.where(np.isfinite(filled), filled, 0.0)   # 头部 NaN→0（同 p04）
        px_filled[c] = filled
        tabs[c] = fe.status_tables(b, st_months.get(c))
    return px_raw, px_filled, tabs, cal, cal_pos, miss, fill_count


def simulate(holds, t0, w, px, tabs, months, buy, nxt, cal_pos, cal):
    """独立账本。px 为已全局前向填充的数组（复现 p04 ffill 行为）。返回 df。"""
    cash = CAPITAL
    shares = {}
    cost_cum = 0.0
    n_min_comm = 0
    n_trades = 0
    rows = []

    for t in range(t0, len(months)):
        d = buy[t]
        i0 = cal_pos[d]
        V = cash + sum(n * px[c][i0] for c, n in shares.items() if c in px)
        cur = holds[t]
        # 卖出不在目标里的
        for c in list(shares):
            if c in cur:
                continue
            p = px[c][i0]
            if not np.isfinite(p) or not _ok(tabs, c, d, "sell"):
                continue
            notional = shares[c] * p
            cm, tr, stp = fee_parts(notional, "sell")
            n_trades += 1
            n_min_comm += (notional * COMM_RATE < COMM_MIN)
            f = cm + tr + stp + notional * SPREAD
            cash += notional - f
            cost_cum += f
            del shares[c]
        # 买入新目标 + 权重调整
        for c in cur:
            p = px[c][i0]
            if not np.isfinite(p):
                continue
            target_val = w * V / len(cur)
            have = shares.get(c, 0)
            tgt_sh = int(target_val // (p * LOT)) * LOT
            delta = tgt_sh - have
            if delta > 0:
                cost = delta * p
                cm, tr, stp = fee_parts(cost, "buy")
                n_trades += 1
                n_min_comm += (cost * COMM_RATE < COMM_MIN)
                f = cm + tr + stp + cost * SPREAD
                if cash >= cost + f:
                    cash -= cost + f
                    cost_cum += f
                    shares[c] = have + delta
                else:
                    lots = int((cash / (1 + COMM_RATE)) // (p * LOT))
                    if lots > 0:
                        cost = lots * LOT * p
                        cm, tr, stp = fee_parts(cost, "buy")
                        n_trades += 1
                        n_min_comm += (cost * COMM_RATE < COMM_MIN)
                        f = cm + tr + stp + cost * SPREAD
                        cash -= cost + f
                        cost_cum += f
                        shares[c] = have + lots * LOT
            elif delta < 0 and _ok(tabs, c, d, "sell"):
                sell_sh = min(-delta, have)
                notional = sell_sh * p
                cm, tr, stp = fee_parts(notional, "sell")
                n_trades += 1
                n_min_comm += (notional * COMM_RATE < COMM_MIN)
                f = cm + tr + stp + notional * SPREAD
                cash += notional - f
                cost_cum += f
                shares[c] = have - sell_sh
        # 逐日盯市（用已填充价格数组）
        b0, b1 = cal_pos[buy[t]], cal_pos[nxt[t]]
        L = b1 - b0 + 1
        names = list(shares)
        sv = np.zeros(L)
        if names:
            Pm = np.vstack([px[c][b0:b1 + 1] for c in names]).astype(np.float64)
            sv = np.asarray([shares[c] for c in names], float) @ Pm
        cf = cash * (1 + CASH_ANN / TRADAYS) ** np.arange(1, L + 1)
        for k in range(L):
            rows.append((cal[b0 + k], float(sv[k]), float(cf[k]), cost_cum, k == 0))
        cash = float(cf[-1])
    df = pd.DataFrame(rows, columns=["date", "stock_value", "cash", "cost_cum", "is_rebal"])
    df["nav"] = df["stock_value"] + df["cash"]
    df["dd"] = df["nav"] / df["nav"].cummax() - 1
    return df


def _carry(row):
    """对单只股票的日价格序列，NaN 用最近已知价前向 carry（复现 p04 ffill）。"""
    out = row.copy()
    last = np.nan
    for i in range(len(out)):
        if np.isfinite(out[i]):
            last = out[i]
        elif np.isfinite(last):
            out[i] = last
    return out


def metrics(df):
    nav = df["nav"].to_numpy(float)
    nav = nav / nav[0]
    n = len(nav) - 1
    dd = df["dd"].to_numpy(float)
    rr = nav[1:] / nav[:-1] - 1
    return {"ann": float(nav[-1] ** (TRADAYS / n) - 1), "mdd": float(dd.min()),
            "final": float(nav[-1])}


def main():
    os.makedirs(OUT, exist_ok=True)
    n_paths = int(os.environ.get("SP5_PATHS", "50"))
    panel = pd.read_parquet(p03.PANEL)
    st = pd.read_parquet(os.path.join(RD, "data", "st_sz_monthly.parquet"))
    st["date"] = pd.to_datetime(st["date"]).dt.strftime("%Y%m%d")
    st_months = {}
    for c, d, v in zip(st["code"], st["date"], st["is_st"]):
        if v:
            st_months.setdefault(c, set()).add(d[:6])

    by, months, paths, ns_list, monthly = p03.build_decisions(panel, n_paths)
    cal = fe.market_calendar()
    cal_pos = {d: i for i, d in enumerate(cal)}
    cal_arr = np.asarray(cal)
    buy = [fe.month_buy_date(m, cal_pos, cal) for m in months]
    nxt = buy[1:] + [cal[min(cal_pos[buy[-1]] + 1, len(cal) - 1)]]

    STARTS = ["2005", "2008", "2010", "2013", "2015", "2018", "2021"]

    def idx_of(year):
        for t, m in enumerate(months):
            if m[:4] >= year:
                return t
        return len(months) - 1

    # ---------- 1) path 0 精确逐日对账（与已存 sp5_nav_recon_p0_<yr>.csv） ----------
    codes0 = sorted({c for hs in paths[0] for c in hs})
    _, px0, tabs0, cal, cal_pos, miss0, fill0 = load_px_bars(codes0, st_months)
    print(f"[path0] {len(codes0)} 只 / 缺 {miss0} / 静默填充天数={fill0}")
    recon_rows = []
    for yr in STARTS:
        t0 = idx_of(yr)
        df = simulate(paths[0], t0, 0.20, px0, tabs0, months, buy, nxt, cal_pos, cal)
        df.to_csv(os.path.join(OUT, f"p04b_independent_nav_p0_{yr}.csv"),
                  index=False, encoding="utf-8-sig")
        # 对账
        ref = pd.read_csv(os.path.join(OUT, f"sp5_nav_recon_p0_{yr}.csv"))
        ref["date"] = ref["date"].astype(str)
        m = df.merge(ref, on="date", suffixes=("", "_ref"), how="inner")
        nav_diff = (m["nav"] - m["nav_ref"]).abs()
        mdd_mine = df["dd"].min()
        mdd_ref = ref["dd"].min()
        ann_mine = metrics(df)["ann"]
        # ref 的 ann 无法直接得（无日收益前提），用 nav 首末年化
        navr = ref["nav"].to_numpy(float); navr = navr / navr[0]
        ann_ref = navr[-1] ** (TRADAYS / (len(navr) - 1)) - 1
        recon_rows.append({
            "start": yr, "days": len(m),
            "nav_max_abs_diff": float(nav_diff.max()),
            "nav_max_abs_diff_pct": float(nav_diff.max() / m["nav"].iloc[-1] * 100),
            "nav_final_diff_pct": float((df["nav"].iloc[-1] - ref["nav"].iloc[-1]) / ref["nav"].iloc[-1] * 100),
            "mdd_mine": float(mdd_mine), "mdd_ref": float(mdd_ref),
            "mdd_diff": float(mdd_mine - mdd_ref),
            "ann_mine": float(ann_mine), "ann_ref": float(ann_ref),
            "ann_diff": float(ann_mine - ann_ref),
            "silent_fill_days_path0": int(fill0),
        })
        print(f"[recon {yr}] nav_max_diff={nav_diff.max():.4f} "
              f"mdd {mdd_mine*100:+.2f}% vs {mdd_ref*100:+.2f}% | "
              f"ann {ann_mine*100:+.2f}% vs {ann_ref*100:+.2f}%")

    pd.DataFrame(recon_rows).to_csv(
        os.path.join(OUT, "p04b_independent_path0_recon.csv"), index=False, encoding="utf-8-sig")
    print(f"[saved] p04b_independent_path0_recon.csv ({len(recon_rows)} 起点)")

    # ---------- 2) 全分布独立复现（与 sp5_position_dd.json starts['0.20_rebal'] 对账） ----------
    if n_paths >= 50:
        all_codes = sorted({c for h in paths for hs in h for c in hs})
        px_raw, px, tabs, cal, cal_pos, miss, fill_all = load_px_bars(all_codes, st_months)
        print(f"[full] {len(all_codes)} 只 / 缺 {miss} / 静默填充天数={fill_all}")

        # 异常案例扫描：成交失败 / 停牌缺口 / 完整路径 / 大幅回撤
        exc = {"paths_with_fill_fail": 0, "fill_fail_months": 0,
               "paths_with_gap": 0, "gap_day_total": 0,
               "worst_drawdown_path": None, "worst_drawdown": 0.0,
               "examples": []}
        for pi, holds in enumerate(paths):
            ff = 0
            gp = 0
            for t in range(len(months)):
                d = buy[t]
                i0 = cal_pos[d]
                cur = holds[t]
                for c in cur:
                    if c in tabs and not _ok(tabs, c, d, "buy"):
                        ff += 1
                    if c in px_raw:
                        seg = px_raw[c][i0: cal_pos[nxt[t]] + 1]
                        g = int(np.isnan(seg).sum())
                        gp += g
            if ff > 0:
                exc["paths_with_fill_fail"] += 1
                exc["fill_fail_months"] += ff
            if gp > 0:
                exc["paths_with_gap"] += 1
                exc["gap_day_total"] += gp
                if len(exc["examples"]) < 5:
                    exc["examples"].append({"path": pi, "gap_days": gp, "fill_fail_months": ff})

        per_start = {}
        for yr in STARTS:
            t0 = idx_of(yr)
            anns, mdds = [], []
            for pi, holds in enumerate(paths):
                df = simulate(holds, t0, 0.20, px, tabs, months, buy, nxt, cal_pos, cal)
                m = metrics(df)
                anns.append(m["ann"]); mdds.append(m["mdd"])
                if yr == STARTS[0]:
                    if m["mdd"] < exc["worst_drawdown"]:
                        exc["worst_drawdown"] = float(m["mdd"])
                        exc["worst_drawdown_path"] = pi
            per_start[yr] = {
                "ann_med": float(np.median(anns)), "mdd_med": float(np.median(mdds)),
                "mdd_worst": float(np.min(mdds)),
                "breach_20pct_share": float((np.array(mdds) < -0.20).mean()),
                "months": len(months) - t0,
            }
        exc["worst_drawdown"] = float(exc["worst_drawdown"])
        # 全样本 20% rebal
        full_anns, full_mdds = [], []
        for holds in paths:
            df = simulate(holds, 0, 0.20, px, tabs, months, buy, nxt, cal_pos, cal)
            m = metrics(df); full_anns.append(m["ann"]); full_mdds.append(m["mdd"])
        summary = {
            "n_paths": n_paths, "starts_0p20_rebal": per_start,
            "full_20_rebal": {
                "ann_med": float(np.median(full_anns)), "mdd_med": float(np.median(full_mdds)),
                "mdd_worst": float(np.min(full_mdds)),
                "breach_share": float((np.array(full_mdds) < -0.20).mean())},
            "verdict": ("DD_ROBUST_20PCT"
                        if (all(v["mdd_med"] >= -0.20 for v in per_start.values())
                            and min(v["mdd_med"] for v in per_start.values()) + 0.20 >= 0.02)
                        else "DD_NOT_ROBUST"),
        }
        with open(os.path.join(OUT, "p04b_independent_summary.json"), "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        # 异常案例写入独立文件
        with open(os.path.join(OUT, "p04b_exception_cases.json"), "w", encoding="utf-8") as f:
            json.dump(exc, f, ensure_ascii=False, indent=2)
        print(f"[saved] p04b_independent_summary.json 判定={summary['verdict']}")
        print(f"[exc] 含成交失败路径 {exc['paths_with_fill_fail']}/{n_paths} | "
              f"含停牌缺口路径 {exc['paths_with_gap']}/{n_paths} | 缺口总天数 {exc['gap_day_total']} | "
              f"最差路径 #{exc['worst_drawdown_path']} MDD {exc['worst_drawdown']*100:.2f}%")
        # 与已存 json 对账
        ref = json.load(open(os.path.join(OUT, "sp5_position_dd.json"), encoding="utf-8"))
        print("=== 对账 sp5_position_dd.json (0.20_rebal) ===")
        for yr in STARTS:
            a = summary["starts_0p20_rebal"][yr]
            b = ref["starts"]["0.20_rebal"][yr]
            print(f"  {yr}: ann_med {a['ann_med']*100:+.2f}% vs {b['ann_med']*100:+.2f}% | "
                  f"mdd_med {a['mdd_med']*100:+.2f}% vs {b['mdd_med']*100:+.2f}% | "
                  f"breach {a['breach_20pct_share']*100:.1f}% vs {b['breach_20pct_share']*100:.1f}%")
        rf = ref["full_20_rebal"]
        print(f"  full: ann_med {summary['full_20_rebal']['ann_med']*100:+.2f}% vs {rf['ann_med']*100:+.2f}% | "
              f"mdd_med {summary['full_20_rebal']['mdd_med']*100:+.2f}% vs {rf['mdd_med']*100:+.2f}% | "
              f"verdict {summary['verdict']} vs {ref['verdict']}")
    else:
        print(f"[skip] n_paths={n_paths}<50，不跑全分布复现；仅 path0 对账已输出")
    return 0


if __name__ == "__main__":
    sys.exit(main())
