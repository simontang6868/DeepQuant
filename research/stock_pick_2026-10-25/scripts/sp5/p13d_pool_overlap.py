# -*- coding: utf-8 -*-
"""P0-1 更正配套：单月（20260930）两口径的池交集与机制拆解。

回答三个问题：
  Q1 1-50 的池是否是 1-200 池的子集？（若否，说明分位重算改变了入选集合）
  Q2 差异来自哪一层：价格过滤本身 / f3pct 重算 / liqpct 重算？
  Q3 top5 完全不同的直接原因？
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import p13_forward as p13  # noqa: E402

ME = "20260930"


def main():
    raw = p13.build_snapshot(ME)
    base = raw.attrs["base_filter"]
    raw.attrs = {}
    out = {"month": ME}

    def build(hi, rank_on="own"):
        s = raw.copy()
        s["ok_buy"] = base & raw["close"].between(1.0, hi)
        g = s[s["ok_buy"]].copy()
        if rank_on == "own":
            g["f3pct"] = g["f3"].rank(pct=True, method="average")
            g["liqpct"] = g["amt20"].rank(pct=True, method="average")
        else:                                   # 用 1-200 宇宙的预计算分位（P0-1 旧方法）
            u = s[base & raw["close"].between(1.0, 200.0)].copy()
            u["f3pct"] = u["f3"].rank(pct=True, method="average")
            u["liqpct"] = u["amt20"].rank(pct=True, method="average")
            g = g.drop(columns=[c for c in ("f3pct", "liqpct") if c in g]).merge(
                u[["code", "f3pct", "liqpct"]].copy(), on="code", how="left")
        pool = g[(g["f3pct"] <= p13.THRESH) & (g["liqpct"] < p13.LIQ_MAX)]
        return s[s["ok_buy"]]["code"].to_numpy(), pool

    u200, pool200 = build(200.0)
    u50, pool50 = build(50.0)
    u50_old, pool50_old = build(50.0, rank_on="universe")

    out["universe"] = {"n200": int(len(u200)), "n50": int(len(u50)),
                       "n50_not_in_200": int(len(set(u50) - set(u200))),
                       "n200_not_in_50": int(len(set(u200) - set(u50)))}
    s200, s50 = set(pool200["code"]), set(pool50["code"])
    s50o = set(pool50_old["code"])
    out["pool"] = {
        "n200": len(s200), "n50": len(s50), "inter": len(s200 & s50),
        "jaccard": len(s200 & s50) / len(s200 | s50),
        "only_50": len(s50 - s200), "only_200": len(s200 - s50),
        "old_method_n50": len(s50o), "old_method_inter": len(s200 & s50o),
        "old_method_top5": list(pool50_old.sort_values("amt20", ascending=False)["code"].head(5)),
    }
    # 机制：单条件拆解（都基于 1-200 宇宙的 rank，只换价格上限 → 看门槛效应）
    g = raw[base].copy()
    g["f3pct"] = g["f3"].rank(pct=True, method="average")
    g["liqpct"] = g["amt20"].rank(pct=True, method="average")
    for hi in (200.0, 50.0):
        sub = g[g["close"].between(1.0, hi)]
        out[f"thr_{int(hi)}"] = {
            "n": int(len(sub)),
            "f3_cut_value": float(sub["f3pct"].quantile(p13.THRESH)) if len(sub) else None,
            "pass_f3": int((sub["f3pct"] <= p13.THRESH).sum()),
            "pass_liq": int((sub["liqpct"] < p13.LIQ_MAX).sum()),
        }
    # 两口径 top5 的价格与 amt20，判断是否被价格门槛直接排除
    t200 = pool200.sort_values("amt20", ascending=False).head(5)
    out["top5_200_detail"] = t200[["code", "close", "amt20", "f3", "f3pct", "liqpct"]].round(4)\
        .to_dict("records")
    t50 = pool50.sort_values("amt20", ascending=False).head(5)
    out["top5_50_detail"] = t50[["code", "close", "amt20", "f3", "f3pct", "liqpct"]].round(4)\
        .to_dict("records")
    # 1-200 的 top5 是否"价格都 ≤50"（若是，则不是被价格直接排除，而是分位重算所致）
    out["top5_200_all_below_50"] = bool((t200["close"] <= 50).all())

    p = os.path.join(p13.BASE, "sp5_out", "p13d_pool_overlap.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
