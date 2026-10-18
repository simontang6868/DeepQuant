"""历史结论一致性审计脚本（纯读，不跑任何回测）。

用途：B15/B16/B17/B20 写于 `oos75-v2` 装置与 48 只池之上。此后装置与数据侧发生了
5 处变更（见 `docs/HISTORICAL_CONCLUSION_AUDIT_2026-10-18.md`）。本脚本把
「结论赖以成立的那些事实还在不在」逐项机械核对，输出一份 JSON，
让审计文档里每个断言都能被重跑，而不是抄文档。

**这个脚本不产生任何收益数字**：不调用 `run_portfolio_backtest`，只读 CSV 与历史 JSON。

用法::

    python scripts/history_audit.py                # 打印 JSON
    python scripts/history_audit.py --out x.json   # 同时落盘
"""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "backend" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from deepquant.backtest.fee_presets import (               # noqa: E402
    FEE_PRESET_HIGH_COST,
    FEE_PRESET_REFERENCE,
    FEE_PRESET_SIMULATION,
)

V3_ROOT = Path(r"D:\DeepQuant power by txm\b50_v3")
HIST_SUMMARY = Path(r"D:\DeepQuant power by txm\b50_dist\step75_research_summary.json")
UNIVERSE = REPO_ROOT / "data" / "step75" / "universe" / "csi300_50.json"
PRICE_KEYS = ("open", "close", "high", "low")
OOS = ("2021-01-01", "2024-12-31")


# --------------------------------------------------------------------------- #
# 读取
# --------------------------------------------------------------------------- #


def read_prices(root: Path) -> dict[str, list[dict[str, str]]]:
    out: dict[str, list[dict[str, str]]] = {}
    for f in sorted(glob.glob(str(root / "*.qfq.csv"))):
        sym = os.path.basename(f).replace(".qfq.csv", "")
        with open(f, encoding="utf-8", newline="") as fh:
            out[sym] = list(csv.DictReader(fh))
    return out


def nonpositive_rows(rows: list[dict[str, str]]) -> list[str]:
    """返回任一价格（四价）<= 0 的日期。

    刻意用四价而不是只用 close：`000568.SZ` 2017-01-16 的 close=0.75（正），
    但 low=-0.01。只看 close 会把这一行判成正常——预登记缺陷 1 记的是「行」，
    本函数与它同口径。
    """
    bad = []
    for r in rows:
        for k in PRICE_KEYS:
            try:
                if float(r[k]) <= 0:
                    bad.append(r["date"])
                    break
            except (KeyError, TypeError, ValueError):
                continue
    return bad


def lot_cost(rows: list[dict[str, str]], start: str, end: str) -> tuple[float, float]:
    """区间内 1 手（100 股）成本的最小/最大值。"""
    vals = []
    for r in rows:
        if start <= r["date"] <= end:
            c = float(r["close"])
            if c > 0:
                vals.append(c * 100)
    return (min(vals), max(vals)) if vals else (float("nan"), float("nan"))


# --------------------------------------------------------------------------- #
# 六项核对
# --------------------------------------------------------------------------- #


def check_pool_split(prices: dict[str, list[dict[str, str]]], hist: dict) -> dict:
    """D5：v2 的 48 只池与 v3 的 50 只池，差集是哪两只。"""
    hist_syms = {r["symbol"] for r in hist["results"]}
    v3_syms = set(prices)
    skipped = [
        {"name": s.get("name"), "reason": s.get("reason"), "error_codes": s.get("error_codes"),
         "has_symbol_field": s.get("symbol") is not None}
        for s in hist.get("skipped", [])
    ]
    return {
        "v3_symbols": len(v3_syms),
        "v2_result_symbols": len(hist_syms),
        "in_v3_not_in_v2": sorted(v3_syms - hist_syms),
        "in_v2_not_in_v3": sorted(hist_syms - v3_syms),
        "v2_skipped_records": skipped,
        "v2_n_groups": len(hist["results"]),
        "v2_design_version": hist.get("design", {}).get("design_version"),
    }


def check_negative_prices(prices: dict[str, list[dict[str, str]]]) -> dict:
    """D3：非正价是否落在 8 年区间内，以及是否只命中被闸门拦下的那两只。"""
    detail = {}
    for sym, rows in prices.items():
        bad = nonpositive_rows(rows)
        if bad:
            detail[sym] = {
                "n_rows": len(bad),
                "first": bad[0],
                "last": bad[-1],
                "min_price": min(
                    min(float(r[k]) for k in PRICE_KEYS if _is_num(r[k]))
                    for r in rows if r["date"] in set(bad)
                ),
            }
    total_rows = sum(len(v) for v in prices.values())
    return {
        "n_symbols_with_nonpositive": len(detail),
        "n_nonpositive_rows": sum(d["n_rows"] for d in detail.values()),
        "share_of_all_rows": round(sum(d["n_rows"] for d in detail.values()) / total_rows, 6),
        "detail": detail,
        "any_inside_2017_2024": any(d["first"] >= "2017-01-01" for d in detail.values()),
    }


def check_price_limit_absent(hist: dict) -> dict:
    """D2：历史结果里是否出现过 price_limit —— 没出现即涨跌停未参与撮合。"""
    blob = json.dumps(hist, ensure_ascii=False)
    return {
        "mentions_price_limit": "price_limit" in blob,
        "mentions_market_rules": "market_rules" in blob,
        "interpretation": (
            "历史结果未记录任何涨跌停字段 → 与 B18 D2「涨跌停从未启用」一致"
            if "price_limit" not in blob
            else "历史结果里出现了涨跌停字段，需重新核对"
        ),
    }


def check_fee_ordering() -> dict:
    """D1：三档按「总成本」排序，验证 simulation 不是最低成本档。"""
    def round_trip(p):
        # 买 + 卖：佣金双边、印花税仅卖方、过户费双边、滑点双边
        return (2 * p.commission_rate + p.stamp_duty_rate + 2 * p.transfer_fee_rate
                + p.slippage_buy + p.slippage_sell)

    tiers = {
        p.fee_source: {"commission": p.commission_rate, "transfer": p.transfer_fee_rate,
                       "slippage": p.slippage_buy + p.slippage_sell,
                       "round_trip_cost": round(round_trip(p), 6)}
        for p in (FEE_PRESET_SIMULATION, FEE_PRESET_REFERENCE, FEE_PRESET_HIGH_COST)
    }
    ranked = sorted(tiers, key=lambda k: tiers[k]["round_trip_cost"])
    return {"tiers": tiers, "cheapest": ranked[0], "most_expensive": ranked[-1],
            "simulation_is_cheapest": ranked[0] == FEE_PRESET_SIMULATION.fee_source}


def check_affordability(prices: dict[str, list[dict[str, str]]]) -> dict:
    """D6：两种本金下，有多少只标的「1 手 > 单只预算」。"""
    out = {}
    for capital, label in ((100_000.0, "historical_100k"), (1_000_000.0, "gate1_1m")):
        budget = capital / 10  # top20% 取 10 只
        blocked = {}
        for sym, rows in prices.items():
            lo, _hi = lot_cost(rows, *OOS)
            if lo and lo > budget:
                blocked[sym] = round(lo, 0)
        out[label] = {"per_name_budget": budget, "n_never_affordable": len(blocked),
                      "symbols": blocked}
    return out


def check_hist_windows(hist: dict) -> dict:
    """F5 的前提：历史结果里单窗口成交笔数确实是 1~2 笔量级（读落盘值，不重算）。"""
    fills = [r.get("total_trades") for r in hist["results"]
             if r.get("total_trades") is not None]
    return {
        "n_groups_with_total_trades": len(fills),
        "total_trades_sum": sum(fills),
        "note": "total_trades 是全部窗口累计；单窗口密度见 B17 §5.5（1~2 笔/窗口）",
    }


def run() -> dict:
    prices = read_prices(V3_ROOT)
    hist = json.loads(HIST_SUMMARY.read_text(encoding="utf-8"))
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    report = {
        "generated_at": "2026-10-18 19:10 GMT+8",
        "inputs": {
            "v3_prices": str(V3_ROOT),
            "v3_n_symbols": len(prices),
            "hist_summary": str(HIST_SUMMARY),
            "hist_sha256_prefix": hashlib.sha256(
                HIST_SUMMARY.read_bytes()).hexdigest()[:16],
            "universe": str(UNIVERSE),
            "universe_n": len(universe.get("stocks", [])),
        },
        "D5_pool_split": check_pool_split(prices, hist),
        "D3_negative_prices": check_negative_prices(prices),
        "D2_price_limit": check_price_limit_absent(hist),
        "D1_fee_ordering": check_fee_ordering(),
        "D6_affordability": check_affordability(prices),
        "F5_window_trades": check_hist_windows(hist),
        "will_compute_returns": False,
    }
    return report


def _is_num(x: object) -> bool:
    try:
        float(x)  # type: ignore[arg-type]
        return True
    except (TypeError, ValueError):
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="历史结论一致性审计（只读）")
    ap.add_argument("--out", default=None, help="可选：把 JSON 写到该路径")
    args = ap.parse_args(argv)
    report = run()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
