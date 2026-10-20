#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""阶段 4.1：组合构建与资金约束模拟（整手 / 现金残留 / 换手成本 / 集中度）。

口径见 `docs/PORTFOLIO_CONSTRAINTS_SPEC_2026-10-20.md`（**跑前冻结**）：
  - 持仓集合 = 每月末按 `close × volume` 降序取前 k（中立规则，与 alpha 无关）
  - 建仓价 = 调仓日 EXEC 收盘价；最小单位 100 股；单只预算 W/k；现金收益 0
  - 对照 = 理论等权（无整手 / 无成本 / 永远满仓）
  - 网格 k ∈ {10,30,110} × W ∈ 10 档 × 成本 {0, 0.1%}，**全报不挑**

用法：
  python scripts/portfolio_lot_sim.py                        # 主窗口 2016-01~2026-09
  python scripts/portfolio_lot_sim.py --start 2007-01-01 --end 2015-12-31 --tag early
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import statistics
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from stage3_sigma_d import load_close_matrix, month_end_positions  # noqa: E402

EXEC_DIR = "data/exec_long"
LOT = 100

# 与 Gate 3 一致的研究池剔除清单（口径不同的标的不能混进来）
EXCLUDED = [
    "600515.SH", "000725.SZ", "000301.SZ", "000425.SZ", "000776.SZ", "600066.SH", "600115.SH", "600660.SH",
    "000760.SZ", "600077.SH", "600213.SH", "600225.SH", "600277.SH", "600401.SH", "600705.SH",
    "000038.SZ", "900956.SH",
]

K_GRID = [10, 30, 110]
W_GRID = [5e4, 1e5, 2e5, 3e5, 5e5, 1e6, 1.5e6, 3e6, 1e7, 3e7]
COST_GRID = [0.0, 0.001]


def load_exec_matrix(exec_dir: str, exclude: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """读 EXEC 的 close / volume 宽表（真不复权，交易模拟层唯一合法口径）。"""
    closes, vols = {}, {}
    for path in sorted(glob.glob(os.path.join(exec_dir, "*.exec.csv"))):
        code = os.path.basename(path).replace(".exec.csv", "")
        if code in exclude:
            continue
        df = pd.read_csv(path, usecols=["date", "close", "volume"])
        df["date"] = pd.to_datetime(df["date"])
        df = df.drop_duplicates(subset="date", keep="last").set_index("date").sort_index()
        closes[code] = df["close"].astype(float)
        vols[code] = df["volume"].astype(float)
    return pd.DataFrame(closes).sort_index(), pd.DataFrame(vols).sort_index()


def pick_targets(close: pd.DataFrame, vol: pd.DataFrame, pos: int, k: int) -> list[str]:
    """调仓日按 close×volume 取前 k（当日必须有正价）。"""
    px = close.iloc[pos]
    vv = vol.iloc[pos]
    amt = (px * vv).where(px > 0)
    amt = amt.dropna()
    return list(amt.sort_values(ascending=False).index[:k])


def simulate(close: pd.DataFrame, vol: pd.DataFrame, ffill: pd.DataFrame,
             positions: list[int], k: int, W: float, cost: float) -> dict:
    """逐月模拟一个 (k, W, cost) 组合。返回指标。

    记账顺序（与 spec §1.2 一致）：
      val_pre  = 现金 + 按调仓日价格估值的持仓（= 上月月末市值，停牌用最后可用价）
      val_post = 先卖后买、扣成本后的市值（整手约束在这步生效）
      月收益   = 期末市值 / val_post - 1        ← 成本必然体现为 val_post < val_pre
    """
    cash = W
    shares: dict[str, int] = {}
    per_month = []
    for i in range(len(positions) - 1):
        pos, nxt = positions[i], positions[i + 1]
        targets = pick_targets(close, vol, pos, k)
        if not targets:
            continue
        px_ffill = ffill.iloc[pos]
        px = close.iloc[pos]

        def price_of(c: str) -> float:
            p = px.get(c, float("nan"))
            if not (p and p > 0):
                p = float(px_ffill.get(c, float("nan")))
            return float(p) if (p and p > 0) else float("nan")

        # --- 调仓前市值 ---
        val_pre = cash + sum(sh * price_of(c) for c, sh in shares.items()
                            if sh > 0 and not math.isnan(price_of(c)))
        budget = val_pre / k
        traded = 0.0

        # --- 第 1 遍：卖出（不在目标集合的、以及所有需要减仓的）---
        for c in list(shares):
            if shares[c] == 0:
                continue
            p = price_of(c)
            if math.isnan(p):
                continue  # 无价，只能挂着（估值沿用最后价）
            if c in targets:
                tgt_sh = math.floor(budget / (p * LOT)) * LOT
                delta = min(0, tgt_sh - shares[c])
            else:
                delta = -shares[c]
            if delta == 0:
                continue
            cash -= delta * p          # delta<0 → 现金增加
            traded += abs(delta) * p
            shares[c] += delta

        # --- 第 2 遍：买入（只买目标集合里不足的，且受现金约束）---
        for c in targets:
            p = price_of(c)
            if math.isnan(p):
                continue
            tgt_sh = math.floor(budget / (p * LOT)) * LOT
            delta = max(0, tgt_sh - shares.get(c, 0))
            if delta == 0:
                continue
            need = delta * p
            if need > cash:
                continue
            cash -= need
            traded += need
            shares[c] = shares.get(c, 0) + delta

        fee = traded * cost
        cash -= fee

        # --- 估值 ---
        val_post = cash + sum(sh * price_of(c) for c, sh in shares.items()
                             if sh > 0 and not math.isnan(price_of(c)))
        end_val = cash + sum(shares[c] * float(ffill.iloc[nxt].get(c, float("nan")))
                             for c in shares if shares.get(c, 0) > 0)
        rets = [(float(ffill.iloc[nxt].get(c, float("nan"))) / float(px[c]) - 1.0)
                for c in targets if px.get(c, 0) and float(px[c]) > 0]
        rets = [r for r in rets if r == r]
        theory = sum(rets) / len(rets) if rets else float("nan")
        # 第二基准：**实际建仓的那几只**的等权（用来把"选股偏离"与"权重/现金偏离"分开）
        held = [c for c in targets if shares.get(c, 0) > 0 and px.get(c, 0) and float(px[c]) > 0]
        hr = [(float(ffill.iloc[nxt].get(c, float("nan"))) / float(px[c]) - 1.0) for c in held]
        hr = [r for r in hr if r == r]
        theory_held = sum(hr) / len(hr) if hr else float("nan")

        per_month.append({
            "date": str(close.index[pos].date()),
            "holdings_actual": sum(1 for c in targets if shares.get(c, 0) > 0),
            "k": len(targets),
            "buyable": sum(1 for c in targets
                           if px.get(c, 0) and float(px[c]) > 0
                           and math.floor(budget / (float(px[c]) * LOT)) >= 1),
            "weight_mae_pp": (sum(abs((shares.get(c, 0) * price_of(c)) / val_post - 1.0 / k)
                                  for c in targets if not math.isnan(price_of(c))) / k * 100)
            if val_post > 0 else float("nan"),
            "cash_pct": cash / end_val * 100 if end_val > 0 else float("nan"),
            "turnover_1side": traded / (2 * val_pre) if val_pre > 0 else float("nan"),
            "ret_port": end_val / val_post - 1.0 if val_post > 0 else float("nan"),
            "ret_theory": theory,
            "ret_theory_held": theory_held,
        })

    if not per_month:
        return {"k": k, "W": W, "cost": cost, "n": 0}

    def ann(rets):
        # 月度连乘 → 年化（12 个月）
        g = 1.0
        for r in rets:
            if r == r:
                g *= (1.0 + r)
        return g ** (12.0 / len(rets)) - 1.0

    rp = [m["ret_port"] for m in per_month]
    rt = [m["ret_theory"] for m in per_month]
    rh = [m["ret_theory_held"] for m in per_month]
    diff = [(a - b) * 100 for a, b in zip(rp, rt) if a == a and b == b]
    diff_h = [(a - b) * 100 for a, b in zip(rp, rh) if a == a and b == b]
    ann_rt = ann(rt)
    cash_avg = statistics.mean(m["cash_pct"] for m in per_month) / 100.0
    return {
        "k": k,
        "W": W,
        "cost": cost,
        "n": len(per_month),
        "buyable_ratio": statistics.mean(m["buyable"] / m["k"] for m in per_month),
        "holdings_actual": statistics.mean(m["holdings_actual"] for m in per_month),
        "weight_mae_pp": statistics.mean(m["weight_mae_pp"] for m in per_month),
        "cash_pct": statistics.mean(m["cash_pct"] for m in per_month),
        "turnover_1side": statistics.mean(m["turnover_1side"] for m in per_month),
        "ann_port": ann(rp),
        "ann_theory": ann(rt),
        "excess_pp_per_year": (ann(rp) - ann_rt) * 100,
        # 现金拖累：未投入比例 × **实际持仓等权**的年化收益（用 held 基准，符号才可解释：
        # 用"目标集合等权"当基准会把"选股偏离"混进来，而本节只研究执行机制）
        "ann_theory_held": ann(rh),
        "cash_drag_pp_per_year": -cash_avg * ann(rh) * 100,
        "excess_vs_held_pp_per_year": (ann(rp) - ann(rh)) * 100,
        # 成本拖累的**解析值**：2 × 单边换手 × 12 × 费率。
        # 为什么不用"两档成本实现差"来估计：整手取整是阶梯函数，成本改变 val_pre → budget 变化
        # → 取整结果跳变，噪声（月度 σ≈1pp）远大于成本本身（≈0.6pp/年），实测差值落在 ±0.3pp 内不可辨。
        "cost_ann_pp_analytic": -2 * statistics.mean(m["turnover_1side"] for m in per_month) * 12 * cost * 100,
        "monthly_excess_sd_pp": statistics.stdev(diff) if len(diff) > 1 else float("nan"),
        "monthly_excess_vs_held_sd_pp": statistics.stdev(diff_h) if len(diff_h) > 1 else float("nan"),
        "worst_month_pp": min(diff) if diff else float("nan"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="组合构建与资金约束模拟（4.1）")
    ap.add_argument("--exec-dir", default=EXEC_DIR)
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2026-09-30")
    ap.add_argument("--tag", default="recent")
    ap.add_argument("--report", default=None)
    args = ap.parse_args(argv)

    close, vol = load_exec_matrix(args.exec_dir, EXCLUDED)
    ffill = close.ffill()
    positions = month_end_positions(close.index, args.start, args.end)
    print(f"[4.1] 池 {close.shape[1]} 只 | 日历 {close.index[0].date()} ~ {close.index[-1].date()} "
          f"| 调仓点 {len(positions)}")

    # 调仓日的"一手成本"分布（纯价格信息，先看这个才知道为什么会有买不起）
    lot_costs = []
    for pos in positions:
        px = close.iloc[pos].dropna()
        px = px[px > 0]
        lot_costs.append(px * LOT)
    all_lot = pd.concat(lot_costs)
    q = all_lot.quantile([0.5, 0.75, 0.9, 0.99]).to_dict()
    print(f"[4.1] 一手成本（元）中位 {q[0.5]:,.0f} / P75 {q[0.75]:,.0f} / P90 {q[0.9]:,.0f} / P99 {q[0.99]:,.0f}"
          f" / 最大 {all_lot.max():,.0f}")

    ly = {}
    for pos in positions:
        px = close.iloc[pos].dropna()
        px = px[px > 0]
        if len(px) == 0:
            continue
        y = str(close.index[pos].year)
        ly.setdefault(y, []).extend((px * LOT).tolist())
    lot_by_year = {y: {"median": float(pd.Series(v).median()), "p90": float(pd.Series(v).quantile(0.9)),
                       "max": float(max(v)), "n": len(v)} for y, v in ly.items()}

    rows = []
    for k in K_GRID:
        for W in W_GRID:
            for cost in COST_GRID:
                r = simulate(close, vol, ffill, positions, k, W, cost)
                r["tag"] = args.tag
                rows.append(r)

    # W*：holdings ≥ 0.95k 且 weight_mae ≤ 0.5pp 的最小 W（每个 k + 每个成本档）
    wstar = {}
    for k in K_GRID:
        for cost in COST_GRID:
            ok = [r for r in rows if r["k"] == k and r["cost"] == cost
                  and r.get("n", 0) > 0
                  and r["holdings_actual"] >= 0.95 * k and r["weight_mae_pp"] <= 0.5]
            wstar[f"k{k}_{'cost' if cost else 'nocost'}"] = min((r["W"] for r in ok), default=None)

    # 补充档位（**事中发现的判据缺陷，见报告 §5**）：冻结的 W* 只看持仓数与权重 MAE，
    # 但现金残留 30% 的组合在 MAE 上仍然"很准"（因为所有仓位等比例缩小），所以 W* 会低估资金需求。
    # 这里额外给出加上"现金 ≤ 5%"后的档位，**只作补充，不替换冻结定义**。
    wstar_cash5 = {}
    for k in K_GRID:
        for cost in COST_GRID:
            ok = [r for r in rows if r["k"] == k and r["cost"] == cost
                  and r.get("n", 0) > 0
                  and r["holdings_actual"] >= 0.95 * k and r["cash_pct"] <= 5.0]
            wstar_cash5[f"k{k}_{'cost' if cost else 'nocost'}"] = min((r["W"] for r in ok), default=None)

    print()
    print("=== 整手约束（成本档 0，只看整手效应）===")
    hdr = (f"{'k':>4}{'W':>12}{'可买占比':>9}{'实际持仓':>9}{'权重MAE':>9}{'现金%':>7}"
           f"{'换手':>7}{'现金拖累':>9}{'实现差':>8}{'月差σ':>7}{'vs持仓等权':>10}")
    print(hdr)
    for k in K_GRID:
        for r in [x for x in rows if x["k"] == k and x["cost"] == 0.0 and x.get("n", 0) > 0]:
            print(f"{k:>4}{r['W']:>12,.0f}{r['buyable_ratio']*100:>8.1f}%{r['holdings_actual']:>9.1f}"
                  f"{r['weight_mae_pp']:>9.2f}{r['cash_pct']:>6.1f}%{r['turnover_1side']*100:>6.1f}%"
                  f"{r['cash_drag_pp_per_year']:>9.2f}{r['excess_pp_per_year']:>8.2f}"
                  f"{r['monthly_excess_sd_pp']:>7.2f}{r['excess_vs_held_pp_per_year']:>10.2f}")
        print()

    print("=== 加成本（单边 0.1%）年化差 ===")
    for k in K_GRID:
        line = []
        for r in [x for x in rows if x["k"] == k and x["cost"] == 0.001 and x.get("n", 0) > 0]:
            line.append(f"{r['W']/1e4:,.0f}万:{r['excess_pp_per_year']:+.1f}")
        print(f"  k={k:>3}: " + "  ".join(line))
    print()

    print("=== 可用档 W*（holdings ≥ 0.95k 且 权重MAE ≤ 0.5pp；spec §1.4 冻结定义）===")
    for kk, v in wstar.items():
        print(f"  {kk:<14} {'无解（>3000万）' if v is None else f'{v/1e4:,.0f} 万元'}")
    print()
    print("=== 补充档 W*'（holdings ≥ 0.95k 且 现金 ≤ 5%；只看现金，不看 MAE）===")
    for kk, v in wstar_cash5.items():
        print(f"  {kk:<14} {'无解（>3000万）' if v is None else f'{v/1e4:,.0f} 万元'}")

    out = {
        "tag": args.tag,
        "window": [args.start, args.end],
        "pool": int(close.shape[1]),
        "rebalances": len(positions),
        "lot_cost_quantiles": {str(k): float(v) for k, v in q.items()} | {"max": float(all_lot.max())},
        "lot_cost_by_year": lot_by_year,
        "grid": {"k": K_GRID, "W": W_GRID, "cost": COST_GRID},
        "wstar": wstar,
        "wstar_cash5_supplementary": wstar_cash5,
        "results": rows,
        "note": "持仓集合由成交额代理 close×volume 决定，与任何 alpha 无关；本节只测执行机制。",
    }
    path = args.report or f"docs/PORTFOLIO_CONSTRAINTS_{args.tag.upper()}_2026-10-20.json"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    print(f"\n[4.1] report -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
