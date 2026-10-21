#!/usr/bin/env python
"""阶段 N 审计：成本模型补充场景 + 信号增量信息检验（探索性）。

## 补充场景（任务 D）
- **最低佣金**：A 股多数券商有「单笔最低 5 元」。本组合单标的仓位仅数千元，
  从价佣金（0.025%×5000≈1.3 元）会低于下限 → **最低佣金才是真实主导项**。
  这是原成本模型**未建模**的项，按情景给出，不作确认性结论。
- **无现金残留上界**：`f_inv = 1`（对应资金足够大时的理想情况），仅作对照。

## 增量信息检验（任务 C，⚠️ 探索性，**无预登记**）
用**已冻结的信号定义**（与阶段 F/L 完全相同）：
1. Fama-MacBeth 横截面回归：
   `ret_{t+1} ~ z(MAX5) + z(SURGE) + z(波动率) + z(21日反转)`
   逐月回归，取斜率均值，用 NW(3) 判显著 → 直接读出**控制其他信号后的增量信息**
2. 5×5 双向分组：独立按 MAX5 与 SURGE 排序，看各自的**边际效应**

⚠️ 本检验**没有预登记**，结果只能标记为探索性分析，不得当作确认性结论。

用法：
    python scripts/audit_cost_addendum.py       # 只跑补充场景
    python scripts/audit_cost_addendum.py --signals   # 跑信号增量（约 2 分钟）
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
import math
import os
import statistics
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import run_phase_c as C  # noqa: E402
from run_phase_c import (  # noqa: E402
    END_HOLD, END_SIGNAL, MIN_LISTED_DAYS, START, build_mask, load_close,
)
from run_phase_i_exq5 import split_equal_indep  # noqa: E402
from run_phase_j_impact import load_dollar_volume  # noqa: E402
from run_phase_l_surge import N_GROUP, surge_of  # noqa: E402
from run_phase_f_max5 import MIN_VALID, TOP_N, WIN  # noqa: E402
from stage3_sigma_d import month_end_positions  # noqa: E402

P_J_CSV = "phasej_out/f76de8a277f6/monthly.csv"
K_HOLD = 1937          # 阶段 K m=1 的持仓中位
MIN_COMM_YUAN = 5.0


def cost_scenarios() -> dict:
    rows = list(csv.DictReader(open(P_J_CSV, encoding="utf-8")))
    to = np.array([float(r["to"]) for r in rows])
    al = np.array([float(r["alpha_v1_pp"]) for r in rows])
    fi = np.array([float(r["f_inv_1000w"]) for r in rows])
    imp = np.array([float(r["impact_A_pp"]) for r in rows])
    base = float((fi * al - imp).mean())          # 折掉现金残留与冲击后的毛收益
    to_m = float(to.mean())

    out = {"base_after_cash_and_impact_pp": round(base, 6),
           "turnover_mean": round(to_m, 6), "k_hold": K_HOLD}

    # 情景 1：纯从价（对称等价费率）
    scen = {}
    for fee in (0.0003, 0.0005, 0.0010, 0.0030):
        cost = 2 * to_m * fee * 100
        scen[f"advalorem_{fee*100:.2f}pct"] = {
            "cost_pp": round(cost, 6), "net_pp": round(base - cost, 6)}
    # 情景 2：A 股真实结构（佣金双边+印花税仅卖出+过户费双边），从价
    comm, transfer, stamp = 0.00025, 0.00001, 0.0005
    c_asy = 2 * to_m * (comm + transfer) * 100 + to_m * stamp * 100
    scen["a_share_advalorem"] = {"cost_pp": round(c_asy, 6),
                                 "net_pp": round(base - c_asy, 6),
                                 "note": "佣金双边+印花税仅卖出+过户费双边"}

    # 情景 3：叠加**最低佣金**（原模型未建模）
    minc = {}
    for w in (1.0e7, 3.0e7):
        n_trades = 2 * to_m * K_HOLD            # 每月买卖笔数
        per_trade = w / K_HOLD
        adval = per_trade * comm
        eff = max(adval, MIN_COMM_YUAN)          # 单笔取下限
        c_comm = n_trades * eff                  # 元/月
        total = c_comm / w * 100 + 2 * to_m * transfer * 100 + to_m * stamp * 100
        minc[f"W_{w/1e4:.0f}万"] = {
            "per_trade_yuan": round(per_trade, 1),
            "advalorem_per_trade_yuan": round(adval, 2),
            "effective_per_trade_yuan": round(eff, 2),
            "trades_per_month": round(n_trades, 0),
            "cost_pp": round(total, 6), "net_pp": round(base - total, 6),
            "note": "最低佣金 5 元/笔（情景，取决于券商）"}
    out["scenarios"] = scen
    out["min_commission_scenario"] = minc

    # 情景 4：无现金残留上界（f_inv=1）
    base_nocash = float((al - imp).mean())
    out["upper_bound_no_cash_residual"] = {
        "base_pp": round(base_nocash, 6),
        "net_at_0_05pct": round(base_nocash - 2 * to_m * 0.0005 * 100, 6),
        "net_at_0_30pct": round(base_nocash - 2 * to_m * 0.0030 * 100, 6),
        "note": "仅作对照，对应资金足够大、无整手取整残留的理想情况"}
    return out


# ==================== 任务 C：增量信息（探索性） ====================
def signal_panel() -> list[dict]:
    close = load_close()
    cal = close.index
    mask = build_mask(close, MIN_LISTED_DAYS)
    positions = month_end_positions(cal, START, END_HOLD)
    ret = close.pct_change(fill_method=None).to_numpy(dtype="float32", copy=True)
    dv = load_dollar_volume().reindex(index=close.index,
                                      columns=close.columns).to_numpy()
    ffill = close.ffill().to_numpy()
    del close

    out = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        sd = str(cal[pos].date())
        if sd > END_SIGNAL or pos - 2 * WIN + 1 < 0 or pos + 1 >= len(cal):
            continue
        ok = mask.iloc[pos].to_numpy()
        if ok.sum() < 100:
            continue
        idx = np.where(ok)[0]
        cur = dv[pos - WIN + 1: pos + 1][:, idx]
        pri = dv[pos - 2 * WIN + 1: pos - WIN + 1][:, idx]
        surge, elig = surge_of(cur, pri)
        e = np.where(elig)[0]
        if e.size < 200:
            continue
        idx, surge = idx[e], surge[e]
        w = ret[pos - WIN + 1: pos + 1][:, idx]
        vc = np.isfinite(w)
        good = vc.sum(axis=0) >= MIN_VALID
        if good.sum() < 200:
            continue
        idx, surge, w = idx[good], surge[good], w[:, good]
        m5 = np.sort(np.where(np.isfinite(w), w, -np.inf), axis=0)[-TOP_N:, :].mean(axis=0)
        vol = np.nanstd(np.where(np.isfinite(w), w, np.nan), axis=0, ddof=1)
        rev = ret[pos, idx]                       # 当月收益（作为反转/动量控制）
        fwd = ffill[nxt][idx] / ffill[pos][idx] - 1.0
        fin = np.isfinite(m5) & np.isfinite(vol) & np.isfinite(fwd) & np.isfinite(rev)
        if fin.sum() < 200:
            continue
        out.append({"signal_date": sd, "m5": m5[fin], "surge": surge[fin],
                    "vol": vol[fin], "rev": rev[fin], "fwd": fwd[fin]})
    return out


def zr(a):
    r = np.argsort(np.argsort(a)).astype(float)
    r = (r - r.mean()) / (r.std() + 1e-12)
    return r


def incremental(recs: list[dict]) -> dict:
    slopes = {"MAX5": [], "SURGE": [], "VOL": [], "REV": []}
    for r in recs:
        X = np.column_stack([np.ones(len(r["fwd"])), zr(r["m5"]), zr(r["surge"]),
                             zr(r["vol"]), zr(r["rev"])])
        y = r["fwd"]
        try:
            b = np.linalg.lstsq(X, y, rcond=None)[0]
        except np.linalg.LinAlgError:
            continue
        for k, name in zip(b[1:], ("MAX5", "SURGE", "VOL", "REV")):
            slopes[name].append(float(k))
    res = {}
    for name, s in slopes.items():
        m = statistics.mean(s)
        sd = statistics.stdev(s) / math.sqrt(len(s))
        nw = C.newey_west(s, 3)
        res[name] = {"mean_pp_per_sd": round(m * 100, 6), "t_iid": round(m / sd, 4),
                     "t_nw": round(nw["t_nw"], 4), "n_months": len(s)}
    return res


def double_sort(recs: list[dict]) -> dict:
    """5×5 双向分组：独立按 MAX5 与 SURGE 排序 → 各自边际效应。"""
    g_m5, g_sg = defaultdict(list), defaultdict(list)
    for r in recs:
        gm = split_equal_indep(r["m5"], N_GROUP)
        gs = split_equal_indep(r["surge"], N_GROUP)
        for a in range(N_GROUP):
            sel = gm == a
            if sel.sum() < 20:
                continue
            g_m5[a].append(float(r["fwd"][sel].mean()))
            sel2 = gs == a
            if sel2.sum() < 20:
                continue
            g_sg[a].append(float(r["fwd"][sel2].mean()))
    def marg(d):
        return {f"Q{k+1}": round(statistics.mean(d[k]) * 100, 6) for k in sorted(d)}
    mm, ss = marg(g_m5), marg(g_sg)
    return {"by_MAX5_quintile_pp": mm, "by_SURGE_quintile_pp": ss,
            "spread_Q1_minus_Q5_MAX5_pp": round(mm["Q1"] - mm["Q5"], 6),
            "spread_Q1_minus_Q5_SURGE_pp": round(ss["Q1"] - ss["Q5"], 6),
            "note": "单变量（未控制）边际，用于与回归的增量斜率对照"}


def main() -> int:
    t0 = time.time()
    print("=" * 78)
    print("阶段 N 审计：成本补充场景 + 增量信息（探索性）")
    print("=" * 78)
    out = {"script": "audit_cost_addendum", "kind": "审计 / 探索性分析"}

    print()
    print("【D 补充】最低佣金与无现金残留情景")
    cs = cost_scenarios()
    out["cost_scenarios"] = cs
    print(f"   折掉现金残留与冲击后的基数 {cs['base_after_cash_and_impact_pp']:.6f}pp  "
          f"（换手中位均值 {cs['turnover_mean']*100:.2f}%）")
    for k, v in cs["scenarios"].items():
        print(f"   {k:<24} 成本 {v['cost_pp']:.4f}pp → 净 {v['net_pp']:+.4f}pp")
    for k, v in cs["min_commission_scenario"].items():
        print(f"   最低佣金情景 {k}: 单笔 {v['per_trade_yuan']:.0f} 元 "
              f"(从价 {v['advalorem_per_trade_yuan']:.2f} 元 → 实收 {v['effective_per_trade_yuan']:.2f} 元) "
              f"成本 {v['cost_pp']:.4f}pp → 净 {v['net_pp']:+.4f}pp")
    u = cs["upper_bound_no_cash_residual"]
    print(f"   无现金残留上界：@0.05% 净 {u['net_at_0_05pct']:+.4f}pp  "
          f"@0.30% 净 {u['net_at_0_30pct']:+.4f}pp")

    if "--signals" in sys.argv:
        print()
        print("【C】增量信息检验（⚠️ 探索性，无预登记）")
        recs = signal_panel()
        print(f"   月度横截面 {len(recs)} 个")
        inc = incremental(recs)
        out["incremental_fama_macbeth"] = inc
        print("   Fama-MacBeth 斜率（每 +1 个横截面标准差的月收益，pp）：")
        for k, v in inc.items():
            print(f"     {k:<6} {v['mean_pp_per_sd']:+.4f}  t(iid)={v['t_iid']:+.2f}  "
                  f"t(NW)={v['t_nw']:+.2f}")
        ds = double_sort(recs)
        out["double_sort"] = ds
        print("   5×5 单变量边际（pp）：")
        print(f"     MAX5  {ds['by_MAX5_quintile_pp']}")
        print(f"     SURGE {ds['by_SURGE_quintile_pp']}")
        print(f"     spread(Q1−Q5): MAX5 {ds['spread_Q1_minus_Q5_MAX5_pp']:+.4f}  "
              f"SURGE {ds['spread_Q1_minus_Q5_SURGE_pp']:+.4f}")

    p = "reports/COST_ADDENDUM_AND_SIGNALS.json"
    os.makedirs("reports", exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=1) + "\n")
    print()
    print(f"[产物] {p}   耗时 {time.time()-t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
