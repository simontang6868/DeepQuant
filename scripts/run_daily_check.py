"""股债利差研究 · 日频执行核验。

对月频口径下已确定的信号事件，做一次**独立于月频折算**的日频复核：

1. 执行价改为「信号日之后第一个交易日」收盘价（真实可执行时点）。
2. 持有期改为精确交易日数（20/60/120/250），不再按月折算。
3. 基准组改用**同一日频收益口径**重算，使信号组与基准组的唯一差异是入场时点集合。

本脚本不修改任何信号规则、阈值或默认参数；它的产出是「月频近似误差有多大」的定量证据。

用法::

    PYTHONPATH=backend/src python scripts/run_daily_check.py
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from deepquant.research.equity_bond_spread.config import (
    IndicatorType,
    ResearchConfig,
)
from deepquant.research.equity_bond_spread.daily_exec import (
    DAILY_WINDOWS,
    build_baseline_daily,
    buy_and_hold_daily,
    check_events_daily,
    compare_daily,
    load_daily_close,
    load_trading_calendar,
)
from deepquant.research.equity_bond_spread.data import build_panel
from deepquant.research.equity_bond_spread.experiment import compute_event_outcomes
from deepquant.research.equity_bond_spread.indicators import build_indicator_frame
from deepquant.research.equity_bond_spread.signals import extract_events

DEFAULT_DIR = Path("data/equity_bond_spread")


def _round(obj, nd: int = 6):
    if isinstance(obj, float):
        return None if pd.isna(obj) else round(obj, nd)
    if isinstance(obj, dict):
        return {k: _round(v, nd) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round(v, nd) for v in obj]
    return obj


def run(data_dir: Path, indicator: IndicatorType, results_dir: Path) -> dict:
    cfg = ResearchConfig(indicator=indicator)
    panel = build_panel(data_dir / "csi300_monthly.csv", data_dir / "cn10y.csv")
    frame = panel.frame

    daily = load_daily_close(data_dir / "csi300_daily.csv")
    calendar = load_trading_calendar(data_dir / "trade_cal.csv")

    ind = build_indicator_frame(frame, cfg)
    events = extract_events(ind, cfg)
    ev_monthly = compute_event_outcomes(frame, events, cfg)

    # 补上月频执行价（来自月频价格序列），用于量化执行偏移
    if len(ev_monthly):
        px = frame["price"].astype("float64")
        ev_monthly["exec_price"] = [
            float(px.loc[d]) if pd.notna(d) and d in px.index else None
            for d in ev_monthly["exec_date"]
        ]

    ev_daily = check_events_daily(
        ev_monthly, daily, calendar, windows=DAILY_WINDOWS, cost_rate=cfg.cost_rate
    )

    comparisons = {}
    for td in DAILY_WINDOWS:
        base = build_baseline_daily(frame, daily, calendar, td, cost_rate=cfg.cost_rate)
        comparisons[f"{td}td"] = compare_daily(
            ev_daily, base, td, is_end_date=cfg.is_end_date
        )

    bh = buy_and_hold_daily(daily, daily.index[0], daily.index[-1])

    out = {
        "meta": {
            "indicator": indicator.value,
            "threshold_pct": cfg.threshold_pct,
            "percentile_window": cfg.percentile_window.value,
            "min_history_obs": cfg.min_history_obs,
            "lookback_years": cfg.lookback_years,
            "min_gap_obs": cfg.min_gap_obs,
            "exec_lag_obs": cfg.exec_lag_obs,
            "is_end_date": cfg.is_end_date,
            "cost_rate": cfg.cost_rate,
            "daily_coverage": [str(daily.index[0].date()), str(daily.index[-1].date())],
            "daily_rows": int(len(daily)),
            "calendar_coverage": [str(calendar.min().date()), str(calendar.max().date())],
            "note": "信号规则与月频完全一致；仅执行时点与持有期改为日频精确口径",
        },
        "n_events_monthly": int(len(ev_monthly)),
        "comparison_daily": comparisons,
        "buy_and_hold_daily": bh,
        "monthly_vs_daily": [],
    }

    # 月频 vs 日频 逐事件对照（这是本脚本的核心产出）
    if len(ev_daily):
        for _, r in ev_daily.iterrows():
            mc = ev_monthly[ev_monthly["event_id"] == r["event_id"]]
            mrow = mc.iloc[0] if len(mc) else None
            item = {
                "event_id": int(r["event_id"]),
                "signal_date": r["signal_date"],
                "exec_date_daily": r["exec_date_daily"],
                "exec_price_daily": r["exec_price_daily"],
                "exec_date_monthly": r["exec_date_monthly"],
                "exec_price_monthly": r["exec_price_monthly"],
                "exec_gap_pct": r["exec_gap_pct"],
                "daily_available": bool(r["daily_available"]),
            }
            for td in DAILY_WINDOWS:
                item[f"daily_ret_{td}td"] = r.get(f"ret_{td}td")
                item[f"daily_dd_{td}td"] = r.get(f"dd_{td}td")
                item[f"monthly_ret_{td}td"] = (
                    float(mrow[f"ret_{td}td"]) if mrow is not None else None
                )
                diff = None
                if mrow is not None and pd.notna(mrow[f"ret_{td}td"]) and pd.notna(r.get(f"ret_{td}td")):
                    diff = float(mrow[f"ret_{td}td"]) - float(r[f"ret_{td}td"])
                item[f"diff_{td}td"] = diff
            out["monthly_vs_daily"].append(item)

    results_dir.mkdir(parents=True, exist_ok=True)
    ev_daily.to_csv(results_dir / f"event_daily_{indicator.value}.csv", index=False)
    (results_dir / f"daily_check_{indicator.value}.json").write_text(
        json.dumps(_round(out), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default=str(DEFAULT_DIR))
    ap.add_argument("--results-dir", default=str(DEFAULT_DIR / "daily_check"))
    ap.add_argument("--indicator", default="B", choices=["A", "B"])
    args = ap.parse_args()

    res = run(Path(args.data_dir), IndicatorType(args.indicator), Path(args.results_dir))

    print(f"=== 假设 {res['meta']['indicator']} · 日频执行核验 ===")
    print(f"日频覆盖 {res['meta']['daily_coverage']}，{res['meta']['daily_rows']} 个交易日")
    print(f"月频事件数 {res['n_events_monthly']}")
    print()
    for item in res["monthly_vs_daily"]:
        print(
            f"事件{item['event_id']} 信号 {item['signal_date']} | "
            f"日频执行 {item['exec_date_daily']} @ {item['exec_price_daily']} | "
            f"月频执行 {item['exec_date_monthly']} @ {item['exec_price_monthly']} | "
            f"偏移 {('%.2f%%' % (item['exec_gap_pct'] * 100)) if item['exec_gap_pct'] is not None else '—'}"
        )
        if not item["daily_available"]:
            print("   ⚠ 日频数据未覆盖该事件，无法核验")
            continue
        for td in DAILY_WINDOWS:
            d, m, df_ = item[f"daily_ret_{td}td"], item[f"monthly_ret_{td}td"], item[f"diff_{td}td"]
            fmt = lambda x: "—" if x is None or pd.isna(x) else f"{x*100:+.2f}%"
            print(f"   {td:>3}td  日频 {fmt(d):>8}  月频 {fmt(m):>8}  差 {fmt(df_):>8}")
        print()

    print("=== 日频统一口径下 信号组 vs 基准组 ===")
    for key, c in res["comparison_daily"].items():
        f = c.get("full", {})
        if not f.get("n"):
            print(f"{key}: 无可用事件")
            continue
        line = (
            f"{key}: 信号 n={f['n']} 均值 {f['mean']*100:+.2f}% | "
            f"基准 n={c['baseline_full']['n']} 均值 {c['baseline_full']['mean']*100:+.2f}%"
        )
        for seg in ("is", "oos"):
            s = c.get(seg)
            if s and s.get("n"):
                bn = s.get("baseline_n", 0)
                warn = "  ⚠ 基准样本过小" if bn < 10 else ""
                line += (
                    f" | {seg.upper()}: n={s['n']} 均值 {s['mean']*100:+.2f}% "
                    f"基准 n={bn} 均值 {s['baseline_mean']*100:+.2f}% "
                    f"差 {s['mean_difference']*100:+.2f}%{warn}"
                )
            elif s and s.get("n") == 0:
                line += f" | {seg.upper()}: 无事件"
        print(line)

    bh = res["buy_and_hold_daily"]
    if "cagr" in bh:
        print(
            f"\n买入持有（日频 {bh['start']}~{bh['end']}）: "
            f"累计 {bh['total_return']*100:+.2f}% / 年化 {bh['cagr']*100:+.2f}% / "
            f"年化波动 {bh['ann_vol']*100:.2f}% / 最大回撤 {bh['max_drawdown']*100:.2f}%"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
