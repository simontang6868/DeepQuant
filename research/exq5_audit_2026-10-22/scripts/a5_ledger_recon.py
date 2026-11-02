#!/usr/bin/env python
"""A5 台账引擎 × 阶段 O 端到端对账（真实 keep 列表，双模式）。

模式：
- drift     ：买入持有，仅交易成分变动（与阶段 O 的成本定义一致）
- rebalance ：每月重置到等权（与 alpha 的「每月等权」口径一致）

输出：out/a5_ledger/{summary.json, monthly_<model>_<mode>.csv}

⚠️ 对账 + 口径诊断，不是新实验；不改变既有判定。
"""
from __future__ import annotations

import json
import os
import statistics
import sys
from collections import defaultdict
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import audit_common as A  # noqa: E402
import fwd_ledger as L  # noqa: E402

OUT = os.path.join(A.OUT_DIR, "a5_ledger")
KEEP = os.path.join(A.OUT_DIR, "a3_panel", "keep_lists.csv")
W = Decimal("30000000")
COST_COL = {"BASE": "cost_BASE_pp", "CONSERVATIVE": "cost_CONS_pp"}


def load():
    rows = A.read_csv_rows(KEEP)
    months, px = {}, {}
    for r in rows:
        d = r["signal_date"]
        m = months.setdefault(d, {"entry": r["entry"], "nxt": r["nxt"], "items": []})
        m["items"].append((r["code"], float(r["lot_yuan"])))
        px[(r["code"], r["entry"])] = Decimal(r["px_entry"])
        if r["px_exit"]:
            px[(r["code"], r["nxt"])] = Decimal(r["px_exit"])
    return months, px


def simulate(model, months, px, mode: str):
    holdings: dict[str, Decimal] = {}
    last_px: dict[str, Decimal] = {}
    cash = W
    stats = defaultdict(int)
    series = []
    prev_nxt = None
    for d in sorted(months):
        m = months[d]
        entry = m["entry"]
        cur = {c for c, _ in m["items"]}
        fee_m = Decimal(0)
        buy_nm = Decimal(0)
        sell_nm = Decimal(0)

        # 1) 离场者按上月月末价卖出；月末无价（停牌）则按**最后已知价**卖出
        for code in list(holdings):
            if code in cur:
                continue
            p = px.get((code, prev_nxt)) if prev_nxt else None
            if p is None:
                p = last_px.get(code)
                if p is None:
                    stats["no_price_dropped"] += 1
                    holdings.pop(code, None)
                    continue
                stats["unsellable_at_last_px"] += 1
            qty = holdings.pop(code)
            f = L.fee(model, "sell", qty, p)
            cash += qty * p - f
            fee_m += f
            sell_nm += qty * p
            stats["sells"] += 1
            last_px.pop(code, None)

        # 2) 目标规模
        if mode == "drift":
            # 买入持有：剩余现金平分给新进者
            entrants = [c for c in cur if c not in holdings]
            if entrants:
                per = cash / Decimal(len(entrants))
                for code in entrants:
                    p = px.get((code, entry))
                    if p is None or p <= 0:
                        stats["no_price"] += 1
                        continue
                    qty = (per / p / L.LOT).to_integral_value(rounding="ROUND_FLOOR") * L.LOT
                    if qty < L.LOT:
                        stats["lot_reject"] += 1
                        continue
                    f = L.fee(model, "buy", qty, p)
                    if qty * p + f > cash:
                        stats["cash_short"] += 1
                        continue
                    cash -= qty * p + f
                    fee_m += f
                    buy_nm += qty * p
                    holdings[code] = qty
                    last_px[code] = p
                    stats["buys"] += 1
        else:
            # 月度等权再平衡：目标 = 总资产 / k
            total = cash
            for code, qty in holdings.items():
                total += qty * last_px.get(code, Decimal(0))
            k = len(cur)
            if k == 0:
                continue
            target = total / Decimal(k)
            for code in sorted(cur):
                p = px.get((code, entry))
                if p is None or p <= 0:
                    stats["no_price"] += 1
                    continue
                qty = holdings.get(code, Decimal(0))
                cur_val = qty * last_px.get(code, p)
                delta_val = target - cur_val
                if delta_val > 0:
                    q = (delta_val / p / L.LOT).to_integral_value(rounding="ROUND_FLOOR") * L.LOT
                    if q < L.LOT:
                        stats["lot_skip_buy"] += 1
                        last_px[code] = p
                        continue
                    f = L.fee(model, "buy", q, p)
                    if q * p + f > cash:
                        stats["cash_short"] += 1
                        last_px[code] = p
                        continue
                    cash -= q * p + f
                    fee_m += f
                    buy_nm += q * p
                    holdings[code] = qty + q
                    stats["buys"] += 1
                elif delta_val < 0 and qty > 0:
                    q = min(qty, (-delta_val / p / L.LOT).to_integral_value(rounding="ROUND_FLOOR") * L.LOT)
                    if q <= 0:
                        last_px[code] = p
                        continue
                    f = L.fee(model, "sell", q, p)
                    cash += q * p - f
                    fee_m += f
                    sell_nm += q * p
                    holdings[code] = qty - q
                    stats["sells"] += 1
                last_px[code] = p

        # 当期组合市值（现金 + 持仓按最新价）—— 阶段 O 的成本是「按资本计」，
        # 买入持有下市值会增长，必须用当期市值做分母，否则费用被系统性放大
        pv = cash + sum(q * last_px.get(c, Decimal(0)) for c, q in holdings.items())
        pv = pv if pv > 0 else W
        series.append({"signal_date": d, "n_keep": len(cur), "n_held": len(holdings),
                       "fee_pp": float(fee_m / pv * 100), "cash": float(cash),
                       "port_value": float(pv),
                       "buy_nm_pp": float(buy_nm / pv * 100), "sell_nm_pp": float(sell_nm / pv * 100)})
        prev_nxt = m["nxt"]
    return series, dict(stats)


def main():
    os.makedirs(OUT, exist_ok=True)
    months, px = load()
    if not months:
        raise SystemExit("keep_lists.csv 为空，先跑 a3")
    o_rows = {r["signal_date"]: r for r in A.read_csv_rows(A.FROZEN["phaseo_monthly"])}
    summary = {"n_months": len(months), "window": [min(months), max(months)],
               "W": float(W), "modes": {}, "trade_stats": {}}

    for mode in ("drift", "rebalance"):
        for name in ("BASE", "CONSERVATIVE"):
            series, stats = simulate(L.MODELS[name], months, px, mode)
            for x in series:
                o = o_rows.get(x["signal_date"])
                x["recorded_cost_pp"] = float(o[COST_COL[name]]) if o else float("nan")
                x["diff_pp"] = x["fee_pp"] - x["recorded_cost_pp"]
            # 首月无历史持仓，O 记 0 成本，不参与比较
            cmp_rows = series[1:]
            diffs = [x["diff_pp"] for x in cmp_rows]
            summary["modes"].setdefault(mode, {})[name] = {
                "mean_fee_pp": statistics.mean([x["fee_pp"] for x in series]),
                "mean_fee_ex_first_pp": statistics.mean([x["fee_pp"] for x in cmp_rows]),
                "mean_recorded_pp": statistics.mean([x["recorded_cost_pp"] for x in cmp_rows]),
                "mean_diff_pp": statistics.mean(diffs),
                "max_abs_diff_pp": max(abs(v) for v in diffs),
                "cum_fee_pp": sum(x["fee_pp"] for x in series),
                "cum_recorded_pp": sum(x["recorded_cost_pp"] for x in cmp_rows),
                "ratio_fee_to_recorded": (statistics.mean([x["fee_pp"] for x in cmp_rows])
                                          / statistics.mean([x["recorded_cost_pp"] for x in cmp_rows])
                                          if statistics.mean([x["recorded_cost_pp"] for x in cmp_rows]) else None),
            }
            summary["trade_stats"][f"{mode}|{name}"] = stats
            A.write_csv(os.path.join(OUT, f"monthly_{name}_{mode}.csv"),
                        ["signal_date", "n_keep", "n_held", "fee_pp", "recorded_cost_pp",
                         "diff_pp", "cash", "port_value", "buy_nm_pp", "sell_nm_pp"],
                        [[x["signal_date"], x["n_keep"], x["n_held"], x["fee_pp"],
                          x["recorded_cost_pp"], x["diff_pp"], x["cash"], x["buy_nm_pp"], x["sell_nm_pp"]] for x in series])

    A.write_json(os.path.join(OUT, "summary.json"), summary)
    print(f"窗口 {summary['window'][0]} ~ {summary['window'][1]}，{summary['n_months']} 月，W={float(W):,.0f}")
    for mode, d in summary["modes"].items():
        for name, s in d.items():
            print(f"[{mode:9s} {name:12s}] 均值 {s['mean_fee_ex_first_pp']:.6f}pp "
                  f"vs 记录 {s['mean_recorded_pp']:.6f}pp  比 {s['ratio_fee_to_recorded']:.2f}x  "
                  f"累计 {s['cum_fee_pp']:.3f} vs {s['cum_recorded_pp']:.3f}pp")
    for k, v in summary["trade_stats"].items():
        print(f"  {k}: {json.dumps(v, ensure_ascii=False, sort_keys=True)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
