# -*- coding: utf-8 -*-
"""SP-5 / P0-3 诊断：三个必须查清的问题

1. **日频 MDD 对不上**：200 路径中位 −20.93%，审计（12 路径）是 −22.43%。
   是"路径样本不同"还是"引擎不同"？→ 用前 12 条路径（与审计同一批种子）重算，
   若落在 −22.4% 附近，则差异来自样本而非实现。
2. **NO_BAR 到底是什么**：真停牌、退市、还是数据缺口？三者对结论含义完全不同。
3. **沪市 5% 双界为何 0 额外失败**：一字事件本身有多少？是不是根本没有沪市一字？

输出：sp5_out/sp5_p03_diag.json
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


def main() -> int:
    panel = pd.read_parquet(p03.PANEL)
    by, months, paths, ns_list, monthly = p03.build_decisions(panel, 12)
    print(f"[12 路径] 月频年化中位 {np.median([p03.perf_monthly(r)[0] for r in monthly])*100:+.2f}%")

    cal = fe.market_calendar()
    cal_pos = {d: i for i, d in enumerate(cal)}
    cal_arr = np.asarray(cal)
    buy = [fe.month_buy_date(m, cal_pos, cal) for m in months]
    nxt = buy[1:] + [cal[min(cal_pos[buy[-1]] + 1, len(cal) - 1)]]

    needed = sorted({c for h in paths for hs in h for c in hs})
    # Q2/Q3 要用台账里全部代码（200 路径台账），否则会把「诊断没加载」误报成「无数据文件」
    led_pre = pd.read_csv(os.path.join(OUT, "sp5_fill_ledger.csv"), dtype={"code": str})
    needed = sorted(set(needed) | set(led_pre["code"].unique()))
    bars = {c: b for c in needed if (b := fe.load_bars(c)) is not None}
    px = {}
    for c, b in bars.items():
        arr = np.full(len(cal), np.nan)
        pos = np.searchsorted(cal_arr, b.d)
        ok = (pos < len(cal)) & (cal_arr[np.clip(pos, 0, len(cal) - 1)] == b.d)
        arr[pos[ok]] = b.qc[ok]
        px[c] = pd.Series(arr).ffill().to_numpy(float)

    # ---- 问题 1：无约束日频（与 audit_sp4_daily 同口径：月内每日等权、月间无缝） ----
    mdds, anns = [], []
    for holds in paths:
        nav = [1.0]
        for t, me in enumerate(months):
            b0, b1 = cal_pos[buy[t]], cal_pos[nxt[t]]
            L = b1 - b0 + 1
            seg = slice(b0, b1 + 1)
            m = np.zeros((p03.K, L))
            for j, c in enumerate(holds[t][:p03.K]):
                if c not in px:
                    continue
                p = px[c][seg]
                good = np.isfinite(p[1:]) & np.isfinite(p[:-1]) & (p[:-1] > 0)
                m[j, 1:] = np.where(good, p[1:] / p[:-1] - 1, 0.0)
            sleeve = m.sum(axis=0) / p03.K
            n_sw = 0 if t == 0 else len(set(holds[t - 1]) - set(holds[t]))
            for k in range(1, L):
                nav.append(nav[-1] * (1 + sleeve[k]))
            nav[-1] *= (1 - n_sw * 2 * p03.SIDE_COST)
        nv = np.asarray(nav)
        anns.append(p03.dmetrics(nv)["ann"])
        mdds.append(p03.dmetrics(p03.mix_nav(nv, 0.25))["mdd"])
    q1 = {"paths": len(paths), "ann100_median": float(np.median(anns)),
          "mdd25_median": float(np.median(mdds)),
          "mdd25_p10": float(np.percentile(mdds, 10)),
          "mdd25_p90": float(np.percentile(mdds, 90)),
          "audit_reference": -0.2243,
          "gap_pp": float(np.median(mdds) * 100 + 22.43)}
    print(f"[Q1] 12 路径（与审计同种子）100% 年化 {np.median(anns)*100:+.2f}%"
          f"｜25% MDD 中位 {np.median(mdds)*100:+.2f}%（审计 −22.43%）"
          f"｜p10 {np.percentile(mdds,10)*100:+.2f}% / p90 {np.percentile(mdds,90)*100:+.2f}%")

    # ---- 问题 2：NO_BAR 分类 ----
    led = pd.read_csv(os.path.join(OUT, "sp5_fill_ledger.csv"), dtype={"code": str})
    nb = led[led["status"] == "NO_BAR"].copy()
    kinds = []
    for _, r in nb.iterrows():
        b = bars.get(r["code"])
        if b is None:
            kinds.append("NO_BAR_NO_FILE")
            continue
        d = str(r["date"])
        if d < b.d[0]:
            kinds.append("BEFORE_LISTING")
        elif d > b.d[-1]:
            kinds.append("AFTER_LAST_TRADE")
        else:
            kinds.append("GAP_IN_RANGE")          # 区间内缺日 → 停牌
    nb["kind"] = kinds
    nb_by = (nb.groupby("kind")["n_orders"].sum().to_dict())
    q2 = {"no_bar_orders": int(nb["n_orders"].sum()),
          "by_kind": {k: int(v) for k, v in nb_by.items()},
          "note": ("AFTER_LAST_TRADE = 决策后已退市/长期停牌；GAP_IN_RANGE = 真停牌；"
                   "BEFORE_LISTING 不应出现（面板已按上市过滤）")}
    print(f"[Q2] NO_BAR {q2['no_bar_orders']} 笔 → {q2['by_kind']}")

    # ---- 问题 3：一字事件与沪市双界 ----
    one_word = {"buy": 0, "sell": 0, "sh": 0, "sz": 0}
    for _, r in led.iterrows():
        b = bars.get(r["code"])
        if b is None:
            continue
        raw, _, _ = fe.classify(b, str(r["date"]), bool(r["is_st"]))
        if raw in ("LOCK_UP", "LOCK_DOWN"):
            one_word[r["side"]] += int(r["n_orders"])
            one_word["sh" if fe.board_of(r["code"]).startswith("SH") else "sz"] += int(r["n_orders"])
    q3 = {"one_word_orders": one_word,
          "sh5_extra_fails": json.load(open(os.path.join(OUT, "sp5_tradability.json"),
                                            encoding="utf-8"))["sh5_extra_fails"],
          "note": "沪市 5% 双界只对『一字但涨幅 5%~10%』的事件新增失败；若沪市无一字，则为 0"}
    print(f"[Q3] 一字事件 {one_word}｜沪市双界额外失败 {q3['sh5_extra_fails']}")

    res = {"q1_daily_mdd_repro": q1, "q2_no_bar": q2, "q3_one_word": q3}
    with open(os.path.join(OUT, "sp5_p03_diag.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print("[saved] sp5_out/sp5_p03_diag.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
