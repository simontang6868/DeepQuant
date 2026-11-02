# -*- coding: utf-8 -*-
"""SP-4 Amendment A1：单只价格上限三口径（40 / 50 / 200）逐月对比。

对应预登记 `SP4_AMENDMENT_A1_价格上限_预登记.md` 的 Q1/Q2/Q4。

**不做的事**（预登记 §4 已冻结）：
  * 不重算净值 —— 价格过滤在 `scripts/build_panel.py:190`，改它要重建面板 + 重跑全部
    账本回测；本阶段 Q3 记为 `NOT_MEASURED`，**不得**用池大小的差异暗示收益影响。
  * 不做全样本 —— `build_snapshot` 每次遍历 5,444 个 CSV，257 个月末约 2~3 小时。
    本阶段取最近 34 个月末，报告里必须声明覆盖范围。

实现用 `p13_forward.build_snapshot` + `p13_forward.pick`，即**前瞻实际运行的那套代码**。
每个口径在**各自 ok_buy 子集内**重算 f3pct/liqpct（`pick()` 内部就如此），
不沿用 200 宇宙的预计算分位 —— 那正是 P0-1 当初算错的原因。

用法：
    python scripts/sp5/p13e_price_cap_amendment.py
    python scripts/sp5/p13e_price_cap_amendment.py --start 202401 --end 202610
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from itertools import combinations

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

import p13_forward as p13  # noqa: E402
import fill_engine as fe  # noqa: E402

CAPS = (40.0, 50.0, 200.0)
TAGS = {40.0: "cap40", 50.0: "cap50", 200.0: "cap200"}
PC_MIN = 100          # 预登记 Q1 的判据阈值


def month_ends(start: str, end: str) -> list[str]:
    """从真实交易日历取每月最后一个交易日（不自己造日期）。"""
    cal = fe.trading_calendar() if hasattr(fe, "trading_calendar") else None
    if cal is None:
        # 兜底：用最活跃的日历股读日期
        p = os.path.join(fe.QFQ_DIR, "000001.SZ.qfq.csv")
        with open(p, newline="", encoding="utf-8") as f:
            cal = [r["date"] for r in csv.DictReader(f)]
    s = pd.Series(pd.to_datetime(list(cal)))
    s = s[(s >= pd.Timestamp(start + "01")) & (s <= pd.Timestamp(end + "31"))]
    return [d.strftime("%Y%m%d") for d in s.groupby([s.dt.year, s.dt.month]).max()]


def pick_with_cap(raw: pd.DataFrame, base: pd.Series, hi: float):
    s = raw.copy()
    s["ok_buy"] = base & raw["close"].between(1.0, hi)
    n_ok = int(s["ok_buy"].sum())
    pool, target, rand = p13.pick(s)
    return n_ok, pool, target, rand


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="价格上限三口径对比（Amendment A1）")
    ap.add_argument("--start", default="202401")
    ap.add_argument("--end", default="202610")
    ap.add_argument("--out", default=os.path.join(HERE, "..", "..", "sp5_out", "p13e_price_cap.json"))
    a = ap.parse_args(argv)

    months = month_ends(a.start, a.end)
    print(f"[p13e] {len(months)} 个月末：{months[0]} ~ {months[-1]}")

    rows = []
    t0 = time.time()
    for i, me in enumerate(months, 1):
        raw = p13.build_snapshot(me)
        base = raw.attrs["base_filter"]
        rec = {"month": me, "n_raw": int(len(raw)), "n_base": int(base.sum())}
        # 每个口径只跑一次 pick（原写法跑了两遍，白花一倍时间）
        pool_sets: dict[str, set[str]] = {}
        for hi in CAPS:
            n_ok, pool, target, rand = pick_with_cap(raw, base, hi)
            t = TAGS[hi]
            rec[f"{t}_n_ok"] = n_ok
            rec[f"{t}_pool"] = int(len(pool))
            rec[f"{t}_top5"] = [str(x) for x in target["code"]]
            rec[f"{t}_rand5"] = [str(x) for x in rand["code"]]
            pool_sets[t] = set(pool["code"].astype(str))
        for x, y in combinations(CAPS, 2):
            kx, ky = set(rec[f"{TAGS[x]}_top5"]), set(rec[f"{TAGS[y]}_top5"])
            rec[f"overlap_{int(x)}_{int(y)}"] = len(kx & ky)
            sx, sy = pool_sets[TAGS[x]], pool_sets[TAGS[y]]
            rec[f"jaccard_{int(x)}_{int(y)}"] = round(len(sx & sy) / len(sx | sy), 6) if (sx | sy) else None
        rows.append(rec)
        print(f"  [{i}/{len(months)}] {me}  ok_buy 40={rec['cap40_n_ok']} 50={rec['cap50_n_ok']} "
              f"200={rec['cap200_n_ok']} | pool 40={rec['cap40_pool']} 50={rec['cap50_pool']} "
              f"200={rec['cap200_pool']} | top5 40vs50={rec['overlap_40_50']} 50vs200={rec['overlap_50_200']}")

    df = pd.DataFrame(rows)

    def agg(col):
        return {"min": float(df[col].min()), "median": float(df[col].median()),
                "max": float(df[col].max()), "mean": round(float(df[col].mean()), 3)}

    summary = {
        "n_months": len(df),
        "window": {"start": months[0], "end": months[-1]},
        "caps": [int(c) for c in CAPS],
        "n_ok": {TAGS[c]: agg(f"{TAGS[c]}_n_ok") for c in CAPS},
        "pool": {TAGS[c]: agg(f"{TAGS[c]}_pool") for c in CAPS},
        "overlap_top5": {
            "40_50": agg("overlap_40_50"),
            "40_200": agg("overlap_40_200"),
            "50_200": agg("overlap_50_200"),
        },
        "overlap_top5_counts": {
            "40_50": {int(k): int(v) for k, v in df["overlap_40_50"].value_counts().sort_index().items()},
            "40_200": {int(k): int(v) for k, v in df["overlap_40_200"].value_counts().sort_index().items()},
            "50_200": {int(k): int(v) for k, v in df["overlap_50_200"].value_counts().sort_index().items()},
        },
        "jaccard_pool": {k: agg(f"jaccard_{k}") for k in ("40_50", "40_200", "50_200")},
        "empty_pool_months": {TAGS[c]: int((df[f"{TAGS[c]}_pool"] == 0).sum()) for c in CAPS},
        "elapsed_sec": round(time.time() - t0, 1),
    }

    # ---- 预登记判据 ----
    q1 = summary["n_ok"]["cap40"]["min"] >= PC_MIN
    summary["Q1_ok_buy_ge_100"] = bool(q1)
    summary["Q1_min_ok_buy_cap40"] = summary["n_ok"]["cap40"]["min"]
    # P1/P2/P3/P4 事后核对
    p1 = summary["overlap_top5_counts"]["40_50"].get(5, 0) + summary["overlap_top5_counts"]["40_50"].get(4, 0)
    p2 = summary["overlap_top5_counts"]["50_200"].get(0, 0) + summary["overlap_top5_counts"]["50_200"].get(1, 0) \
        + summary["overlap_top5_counts"]["50_200"].get(2, 0)
    summary["predictions"] = {
        "P1_40vs50_overlap_ge4_majority": {"holds": p1 > len(df) / 2, "n_months": p1},
        "P2_50vs200_overlap_le2_majority": {"holds": p2 > len(df) / 2, "n_months": p2},
        "P3_ok_buy_cap40_never_below_100": {"holds": bool(q1), "min": summary["Q1_min_ok_buy_cap40"]},
        "P4_gap_40_50_smaller_than_50_200": {
            "holds": summary["overlap_top5"]["40_50"]["mean"] > summary["overlap_top5"]["50_200"]["mean"],
            "mean_overlap_40_50": summary["overlap_top5"]["40_50"]["mean"],
            "mean_overlap_50_200": summary["overlap_top5"]["50_200"]["mean"],
        },
    }
    summary["Q3_annual_return"] = "NOT_MEASURED"
    summary["Q3_reason"] = ("价格过滤在 scripts/build_panel.py:190，改它需重建面板并重跑全部账本回测；"
                            "预登记 §4 已冻结本阶段不做，且不得用池大小差异暗示收益影响")

    out = {"meta": {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "prereg": "SP4_AMENDMENT_A1_价格上限_预登记.md",
                    "impl": "p13_forward.build_snapshot + pick（权威实现）"},
           "summary": summary, "months": rows}
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
        f.write("\n")
    print(f"\n[p13e] -> {a.out}  ({summary['elapsed_sec']}s)")
    print(f"[p13e] Q1 可买数≥100：{q1}（40 口径最小 {summary['Q1_min_ok_buy_cap40']}）")
    print(f"[p13e] top5 平均重合：40vs50={summary['overlap_top5']['40_50']['mean']} "
          f"40vs200={summary['overlap_top5']['40_200']['mean']} "
          f"50vs200={summary['overlap_top5']['50_200']['mean']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
