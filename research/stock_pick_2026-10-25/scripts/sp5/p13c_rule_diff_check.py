# -*- coding: utf-8 -*-
"""P0-1 复核：价格过滤 1-200 vs 1-50 对最终池与 top5 的真实影响。

⚠️ 复核缘起：P0-1 报告用 `panel_monthly.parquet` 复算得出「池差 1~6 只、top5 5/5 重合」，
但 `p13_forward.py` 在 20260930 产出的台账显示 signal_pool_n = 758（1-200）vs 686（1-50），
且两口径 top5 **完全不同**。二者必有一处方法不一致——本脚本用**权威实现**
（`p13_forward.build_snapshot` + `p13_forward.pick`，即前瞻实际运行的那套代码）
逐月复算，判定哪一方正确。

关键机制假设：`pick()` 在 `ok_buy` 子集内**重新计算** f3pct / liqpct 分位；价格上限收紧
→ 分母变小 → 同样的分位阈值对应不同个股集合。若 P0-1 复算沿用了面板里按 1-200 宇宙
预计算的分位列，则低估了差异（典型「没检出≠没问题」）。
"""
from __future__ import annotations

import json
import os
import sys

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import p13_forward as p13  # noqa: E402

MONTHS = ["20260430", "20260630", "20260831", "20260930"]


def main():
    out = []
    for me in MONTHS:
        raw = p13.build_snapshot(me)              # 只遍历一次全量文件
        base = raw.attrs["base_filter"]
        rec = {"month": me, "n_raw": int(len(raw)), "n_base": int(base.sum())}
        res = {}
        for tag, hi in (("v200", 200.0), ("v50", 50.0)):
            s = raw.copy()
            s["ok_buy"] = base & raw["close"].between(1.0, hi)
            pool, top5, rand5 = p13.pick(s)
            res[tag] = {"ok_buy": int(s["ok_buy"].sum()), "pool": int(len(pool)),
                        "top5": list(top5["code"]), "rand5": list(rand5["code"])}
        a, b = res["v200"], res["v50"]
        rec.update({
            "ok_buy_200": a["ok_buy"], "ok_buy_50": b["ok_buy"],
            "pool_200": a["pool"], "pool_50": b["pool"],
            "pool_diff": a["pool"] - b["pool"],
            "top5_200": a["top5"], "top5_50": b["top5"],
            "top5_overlap": len(set(a["top5"]) & set(b["top5"])),
            "rand5_overlap": len(set(a["rand5"]) & set(b["rand5"])),
        })
        out.append(rec)
        print(f"[{me}] ok_buy {a['ok_buy']} vs {b['ok_buy']} | pool {a['pool']} vs {b['pool']} "
              f"(差 {rec['pool_diff']}) | top5 重合 {rec['top5_overlap']}/5 | rand5 {rec['rand5_overlap']}/5")
        print(f"       1-200 top5 {a['top5']}")
        print(f"       1-50  top5 {b['top5']}")

    p = os.path.join(p13.BASE, "sp5_out", "p13c_rule_diff_check.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(f"[saved] {p}")


if __name__ == "__main__":
    main()
