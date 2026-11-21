# -*- coding: utf-8 -*-
"""p03f 的诊断与阈值（答复第 3 轮：口径表 / 阈值 / worked example）。

回答四件事：
  1. 缺失的**真实规模**：槽位数 vs 路径-月数（上一轮把后者当成了前者，此处纠正）；
  2. 缺失按类别拆开的真实平均收益（可读 K 线 / 停牌恢复 / **无 K 线**）；
  3. 只对"无 K 线"那一类施加统一亏损 x 的单轴扫描 ⇒ 求吃掉全部 alpha 的临界 x*；
  4. `sell < len(dates)` 的语义：用真实退市股做 worked example（局部排除 vs 全样本排除）。

只读；只写本目录 diag.json。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
RD = HERE.parents[1]
REPO = HERE.parents[3]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(REPO / "scripts"))

import p03d_d4_strict as p03d  # noqa: E402
import run_phase_c as C  # noqa: E402
import fill_engine as fe  # noqa: E402

OUT = RD / "d4_alpha_out" / "a1801ece1ad7"
MODERN = "20160101"
K = p03d.K


def nw(x, months, lo=None):
    if lo:
        x = [v for v, m in zip(x, months) if m >= lo]
    r = C.newey_west(list(map(float, x)), C.NW_LAG)
    return float(r["mean"]) * 100, float(r["t_nw"]), 2.8016 * float(r["se_nw"]) * 100


def bars_of(code, cache):
    if code not in cache:
        try:
            cache[code] = fe.load_bars(code)
        except Exception:  # noqa: BLE001
            cache[code] = None
    return cache[code]


def fwd_own(code, me, cache):
    b = bars_of(code, cache)
    if b is None or len(b.d) == 0:
        return float("nan"), "nobar"
    pos = int(np.searchsorted(b.d, me, side="right")) - 1
    if pos < 0:
        return float("nan"), "nobar"
    buy, sell = pos + 1, pos + 21
    if buy >= len(b.d):
        return float("nan"), "nobar"
    if sell < len(b.d):
        return float(b.qc[sell] / b.qc[buy] - 1.0), "readable"
    if str(b.d[-1]) > me:
        return float(b.qc[-1] / b.qc[buy] - 1.0), "suspend_resume"
    return float(b.qc[-1] / b.qc[buy] - 1.0), "ended"


def main() -> int:
    panel = pd.read_parquet(p03d.PANEL)
    by, months, paths, ns_list, monthly, _ = p03d.build_decisions(panel, 200, "d4")
    bench = np.asarray([float(np.mean(by[d]["fwd"])) for d in months], float)
    cache: dict = {}

    slots = miss_slots = 0
    months_with_miss = 0
    cat_n = {"readable": 0, "suspend_resume": 0, "ended": 0, "nobar": 0}
    cat_r = {"readable": [], "suspend_resume": [], "ended": []}
    val: dict = {}          # (p,t,c) -> r 或 None（nobar 留待扫描）
    kind_of: dict = {}
    for p, holds in enumerate(paths):
        for t, d in enumerate(months):
            cm = by[d]["cmap"]
            slots += len(holds[t])
            n_miss = 0
            for c in holds[t]:
                if c in cm:
                    continue
                n_miss += 1
                miss_slots += 1
                r, kind = fwd_own(c, d, cache)
                kind_of[(p, t, c)] = kind
                cat_n[kind] += 1
                if kind in cat_r:
                    cat_r[kind].append(r)
                    val[(p, t, c)] = r
                else:
                    val[(p, t, c)] = None      # nobar ⇒ 待扫描
            months_with_miss += int(n_miss > 0)

    def build_series(x_nobar: float):
        rows = []
        for p, holds in enumerate(paths):
            seq = []
            for t, d in enumerate(months):
                cm = by[d]["cmap"]
                pv = [by[d]["fwd"][cm[c]] for c in holds[t] if c in cm]
                gone = [c for c in holds[t] if c not in cm]
                extra = 0.0
                for c in gone:
                    r = val.get((p, t, c))
                    if r is None:
                        r = x_nobar
                    if r != r:
                        r = bench[t]
                    extra += r
                cost = ns_list[p][t] * 2 * p03d.SIDE_COST
                seq.append(((sum(pv) + extra) / K if pv or gone else 0.0) - cost - bench[t])
            rows.append(seq)
        return np.vstack(rows).mean(axis=0)

    sweep = []
    for x in [0.0, -0.05, -0.10, -0.20, -0.30, -0.40, -0.50, -0.60, -0.80, -1.0]:
        s = build_series(x)
        m, tt, mde = nw(s, months, MODERN)
        sweep.append({"x_nobar": x, "mean_pp": m, "t_nw": tt, "mde_pp": mde})
    xs = np.array([v["x_nobar"] for v in sweep])
    ys = np.array([v["mean_pp"] for v in sweep])
    x_star = float(np.interp(0.0, ys[::-1], xs[::-1]))

    # worked example A：nobar（数据缺失）
    nob = [k for k, v in kind_of.items() if v == "nobar"]
    ex_nobar = None
    if nob:
        p, t, c = sorted(nob, key=lambda k: k[2])[0]
        b = fe.load_bars(c)
        me = months[t]
        prev = months[max(0, t - 1)]
        pos = int(np.searchsorted(b.d, me, side="right")) - 1
        ppos = int(np.searchsorted(b.d, prev, side="right")) - 1
        ex_nobar = {"code": c, "me": me, "path": p, "bars_found": b is not None,
                    "n_bars": int(len(b.d)), "first_bar": str(b.d[0]), "last_bar": str(b.d[-1]),
                    "pos": pos, "buy": pos + 1, "sell": pos + 21, "len_dates": int(len(b.d)),
                    "sell_lt_len": bool(pos + 21 < len(b.d)),
                    "row_in_panel_this_month": bool(((panel["code"] == c) & (panel["date"] == me)).any()),
                    "prev_month": prev, "prev_pos": ppos, "prev_sell": ppos + 21,
                    "prev_sell_lt_len": bool(ppos + 21 < len(b.d)),
                    "prev_row_in_panel": bool(((panel["code"] == c) & (panel["date"] == prev)).any())}

    # worked example B：ended（有 K 线、但该月后 20 个交易日内数据终止）
    ended = [k for k, v in kind_of.items() if v == "ended"]
    ex_ended = None
    if ended:
        p, t, c = sorted(ended, key=lambda k: k[2])[0]
        b = fe.load_bars(c)
        me = months[t]
        pos = int(np.searchsorted(b.d, me, side="right")) - 1
        prev_me = months[max(0, t - 1)]
        ppos = int(np.searchsorted(b.d, prev_me, side="right")) - 1
        ex_ended = {"code": c, "me": me, "path": p, "n_bars": int(len(b.d)),
                    "first_bar": str(b.d[0]), "last_bar": str(b.d[-1]),
                    "pos": pos, "buy": pos + 1, "sell": pos + 21,
                    "len_dates": int(len(b.d)),
                    "sell_lt_len": bool(pos + 21 < len(b.d)),
                    "row_in_panel_this_month": bool(((panel["code"] == c) & (panel["date"] == me)).any()),
                    "prev_month": prev_me, "prev_pos": ppos, "prev_sell": ppos + 21,
                    "prev_sell_lt_len": bool(ppos + 21 < len(b.d)),
                    "prev_row_in_panel": bool(((panel["code"] == c) & (panel["date"] == prev_me)).any())}

    gross = []
    for p, rets in enumerate(monthly):
        ns = np.asarray(ns_list[p], float)
        gross.append(np.asarray(rets, float) + ns * 2 * p03d.SIDE_COST)
    G = np.vstack(gross)
    vol100_path = float(np.median([np.std(row, ddof=1) for row in G]))   # 单账户口径
    vol100_avg = float(np.std(G.mean(axis=0), ddof=1))                    # 期望组合口径（去掉特质噪声）
    mean100 = 0.008760                                                    # 100% 组合的月超额
    mean20 = mean100 * 0.20

    res = {
        "slots": slots, "missing_slots": miss_slots,
        "missing_slot_rate": miss_slots / slots,
        "path_months": len(paths) * len(months),
        "path_months_with_missing": months_with_miss,
        "path_month_miss_rate": months_with_miss / (len(paths) * len(months)),
        "category_counts": cat_n,
        "category_mean_fwd_pp": {k: (float(np.mean(v)) * 100 if v else None) for k, v in cat_r.items()},
        "category_median_fwd_pp": {k: (float(np.median(v)) * 100 if v else None) for k, v in cat_r.items()},
        "category_share_of_slots": {k: v / slots for k, v in cat_n.items()},
        "sweep_nobar_only": sweep,
        "zero_crossing_x_nobar": x_star,
        "worked_example_nobar": ex_nobar,
        "worked_example_ended": ex_ended,
        "vol": {"month_100pct_single_path_median": vol100_path,
                "month_100pct_expected_path": vol100_avg,
                "month_20pct_single_path": vol100_path * 0.20,
                "mean20_pp": mean20 * 100,
                "months_to_t2_single_path": (2 * vol100_path * 0.20 / mean20) ** 2,
                "months_to_t2_expected_path": (2 * vol100_avg * 0.20 / mean20) ** 2},
    }
    (OUT / "diag.json").write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                                   encoding="utf-8", newline="\n")

    print(f"[规模] 槽位 {slots}｜缺失槽位 {miss_slots} ({miss_slots/slots*100:.2f}%)｜"
          f"路径-月 {len(paths)*len(months)}｜其中有缺失 {months_with_miss} "
          f"({months_with_miss/(len(paths)*len(months))*100:.1f}%)")
    print(f"[分类] {cat_n}")
    for k in ("readable", "suspend_resume", "ended"):
        if cat_r[k]:
            print(f"  {k:<16} 平均 {res['category_mean_fwd_pp'][k]:+.2f}pp  "
                  f"中位 {res['category_median_fwd_pp'][k]:+.2f}pp  "
                  f"占槽位 {cat_n[k]/slots*100:.2f}%")
    print("\n[单轴扫描：只改 nobar 的取值，其余用真实价]")
    for v in sweep:
        print(f"  x={v['x_nobar']:+.2f}  {v['mean_pp']:+.4f}pp  t={v['t_nw']:+.2f}  MDE={v['mde_pp']:.4f}")
    print(f"\n[临界] 零交叉 x* = {x_star*100:.1f}%")
    v = res["vol"]
    print(f"[波动] 单路径 100% {v['month_100pct_single_path_median']*100:.2f}%/月"
          f"｜20% {v['month_20pct_single_path']*100:.2f}%/月"
          f"｜t=2 需 {v['months_to_t2_single_path']:.0f} 月"
          f"（期望组合口径 {v['months_to_t2_expected_path']:.0f} 月）")
    print(f"[ex nobar] {json.dumps(ex_nobar, ensure_ascii=False)}")
    print(f"[ex ended] {json.dumps(ex_ended, ensure_ascii=False)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
