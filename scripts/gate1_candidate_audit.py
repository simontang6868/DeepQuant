"""Gate 1 候选集实测（只统计「谁进池、谁被剔除、为什么」，不计算任何收益）。

这**不是** Gate 1 实验，不产出策略绩效数字。它回答三个实现层问题：

1. 按预登记 v1.1 的 P1~P3，96 个月末信号日各有几只候选？有没有少到
   只剩 1~2 只的月份（那会让「top20%」失去横截面意义）。
2. 剔除原因分布是否规则？如果「非正价」占比异常高，说明复权缺陷的影响
   比预登记第 2 节估计的更大。
3. top 20% 实际会持有几只？与 `ceil(n × 0.2)` 是否一致。

跑这个脚本不产生研究结论，可以在用户授权真正的 Gate 1 之前任意重跑。
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from deepquant.backtest.cross_section import CrossSectionPanel, CrossSectionConfig, REASON_OK
from deepquant.backtest.portfolio import month_end_signal_dates

SRC = Path(r"D:/DeepQuant power by txm/b50_v3")
UNIV = Path(r"D:/DeepQuant power by txm/DeepQuant-main/data/step75/universe/csi300_50.json")
OUT = Path(r"D:/DeepQuant power by txm/gate1_candidate_audit.json")


def load_prices(src: Path) -> dict[str, pd.DataFrame]:
    prices = {}
    for p in sorted(src.glob("*.qfq.csv")):
        code = p.name.replace(".qfq.csv", "")
        prices[code] = pd.read_csv(p, parse_dates=["date"]).sort_values("date")
    return prices


def main() -> None:
    prices = load_prices(SRC)
    univ = json.loads(UNIV.read_text(encoding="utf-8"))
    universe = {s["code"] for s in univ["stocks"]}
    print(f"标的：磁盘 {len(prices)} / 股票池 {len(universe)} / 交集 {len(set(prices) & universe)}")

    cfg = CrossSectionConfig()
    panel = CrossSectionPanel(prices, config=cfg)
    signal_days = month_end_signal_dates(panel.calendar)
    print(f"日历 {len(panel.calendar)} 天（{panel.calendar[0].date()} ~ {panel.calendar[-1].date()}）"
          f" / 月末信号日 {len(signal_days)} 个")

    rows = []
    for d in signal_days:
        reasons = panel.reasons(d)
        scores = panel.scores(d)
        ok = [s for s, r in reasons.items() if r == REASON_OK]
        n_ok = len(ok)
        buckets: dict[str, int] = {}
        for r in reasons.values():
            buckets[r.split(":", 1)[0]] = buckets.get(r.split(":", 1)[0], 0) + 1
        rows.append({
            "signal_date": str(d.date()),
            "n_eligible": n_ok,
            "n_top20": max(1, -(-n_ok * 2 // 10)) if n_ok else 0,   # ceil(n*0.2)
            "reasons": buckets,
            "excluded_600809": reasons.get("600809.SH"),
            "score_min": min((v for v in scores.values() if v is not None), default=None),
            "score_max": max((v for v in scores.values() if v is not None), default=None),
        })

    df = pd.DataFrame(rows)
    usable = df[df["n_eligible"] > 0]
    summary = {
        "n_months": len(df),
        "n_months_with_candidates": int(len(usable)),
        "n_months_empty": int((df["n_eligible"] == 0).sum()),
        "eligible_min": int(usable["n_eligible"].min()) if len(usable) else 0,
        "eligible_median": float(usable["n_eligible"].median()) if len(usable) else 0,
        "eligible_max": int(usable["n_eligible"].max()) if len(usable) else 0,
        "months_with_under_5": int((usable["n_eligible"] < 5).sum()),
        "months_with_under_10": int((usable["n_eligible"] < 10).sum()),
        "top20_min": int(usable["n_top20"].min()) if len(usable) else 0,
        "top20_median": float(usable["n_top20"].median()) if len(usable) else 0,
        "reason_totals": {},
        "first_usable": str(usable["signal_date"].iloc[0]) if len(usable) else None,
        "last_usable": str(usable["signal_date"].iloc[-1]) if len(usable) else None,
    }
    totals: dict[str, int] = {}
    for b in df["reasons"]:
        for k, v in b.items():
            totals[k] = totals.get(k, 0) + int(v)
    summary["reason_totals"] = dict(sorted(totals.items(), key=lambda kv: -kv[1]))

    OUT.write_text(json.dumps(
        {"summary": summary, "by_month": rows}, indent=2, ensure_ascii=False
    ), encoding="utf-8")

    print("\n=== 候选集规模 ===")
    print(f"有候选的月份 {summary['n_months_with_candidates']} / {summary['n_months']}"
          f"（空仓 {summary['n_months_empty']}）")
    print(f"候选数 min={summary['eligible_min']} median={summary['eligible_median']}"
          f" max={summary['eligible_max']}")
    print(f"候选 <5 只的月份 {summary['months_with_under_5']} 个；<10 只 {summary['months_with_under_10']} 个")
    print(f"top20% 实际持股 min={summary['top20_min']} median={summary['top20_median']}")
    print(f"可用区间 {summary['first_usable']} ~ {summary['last_usable']}")

    print("\n=== 剔除原因（累计标的·月）===")
    for k, v in summary["reason_totals"].items():
        print(f"  {k:34s} {v}")

    print("\n=== 逐年抽样（每年 1 月 / 7 月）===")
    for r in rows:
        if r["signal_date"][5:7] in ("01", "07"):
            print(f"  {r['signal_date']}  n={r['n_eligible']:2d}  top20={r['n_top20']:2d}"
                  f"  600809={r['excluded_600809']}")
    print(f"\n[SAVED] {OUT}")


if __name__ == "__main__":
    main()
