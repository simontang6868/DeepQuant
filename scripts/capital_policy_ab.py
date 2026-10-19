"""资金口径 AB 对照实验（只跑样本内区间，不触碰已消耗的样本外数据）。

为什么只在 IS 区间跑
--------------------
`docs/OOS_RESULTS_2026-10-19.json` 的 2025-01-02~2026-10-16 已经在 `1ec9c579` 被消耗过
（判 R4 INCONCLUSIVE，但"看过数字"就等于用完）。任何在这些日期上的重跑都只能算
"已知结果的检验"，不能再当证据。因此本实验把区间**退回 2017-01-03~2024-12-31**：
这段本来就被反复使用过（所有历史研究都在它上面），不新增任何污染。

四个口径各自回答一个问题
------------------------
| 口径 | 资金 | 标的 | 回答 |
|---|---|---|---|
| `as_is` | 10 万 | 6 只，不剔除 | 历史口径到底算出了什么（对照基线） |
| `raise_cash` | 提高到能买最贵一手 | 6 只，不剔除 | 只是"钱不够"造成的吗？补钱会变成什么样 |
| `drop_symbols` | 10 万 | 逐窗口剔除买不起的 | 不补钱、只承认缺席，结论会怎么变 |
| `both` | 按池内中位数锚定 | 逐窗口剔除超标的 | 不让一只茅台绑架整个组合的折中 |

为什么要"资金固定、剔除逐窗口"
------------------------------
资金一旦逐窗口浮动，每个窗口的组合规模都不同，跨窗口汇总就没有共同分母。
所以资金按**全区间**一次算定（`raise_cash` / `both`），
而"买不起"是**窗口级**事实（茅台 2017-2020 买得起、2021 后买不起），
所以剔除必须逐窗口判断，否则会连带删掉它可交易的那段历史。

输出
----
``docs/CAPITAL_POLICY_AB_2026-10-19.json`` —— 全部数字，机器可读。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepquant.backtest.config import BacktestConfig  # noqa: E402
from deepquant.backtest.fee_presets import FEE_PRESET_SIMULATION  # noqa: E402
from deepquant.market.rules import a_share_price_limit_rate, a_share_rules  # noqa: E402
from deepquant.research.capital_adequacy import (  # noqa: E402
    plan_as_is,
    plan_both,
    plan_drop_symbols,
    plan_raise_cash,
    symbol_cost,
)
from deepquant.strategies.ma_cross import MaCrossStrategy  # noqa: E402
from deepquant.walkforward import WalkForwardConfig, generate_windows  # noqa: E402
from deepquant.walkforward.parameter_selection import (  # noqa: E402
    GridSearchConfig,
    generate_ma_cross_candidates,
    run_walkforward_window,
)

DATA_DIR = ROOT / "data" / "step75_ext"
POOL = ["600519.SH", "000001.SZ", "601318.SH", "300750.SZ", "000858.SZ", "600900.SH"]
BASE_CASH = 100_000.0
LOT_SIZE = 100
IS_START = pd.Timestamp("2017-01-01")
IS_END = pd.Timestamp("2024-12-31")
OUT = ROOT / "docs" / "CAPITAL_POLICY_AB_2026-10-19.json"

TRAIN, OOS, STEP = 150, 75, 75
MIN_TRADES = 2


def load(code: str) -> pd.DataFrame:
    df = pd.read_csv(DATA_DIR / f"{code}.qfq.csv")
    df["date"] = pd.to_datetime(df["date"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.sort_values("date").reset_index(drop=True)


def to_wf(df: pd.DataFrame) -> pd.DataFrame:
    out = df.set_index("date")[["open", "high", "low", "close", "volume"]].copy()
    out.index.name = None
    return out


def dist(vals: list[float]) -> dict | None:
    if not vals:
        return None
    a = np.array(vals, dtype=float)
    a = a[np.isfinite(a)]
    if a.size == 0:
        return None
    return {
        "n": int(a.size),
        "median": round(float(np.median(a)), 6),
        "mean": round(float(a.mean()), 6),
        "p10": round(float(np.percentile(a, 10)), 6),
        "p90": round(float(np.percentile(a, 90)), 6),
        "pos_ratio": round(float((a > 0).sum() / a.size), 4),
    }


def main() -> None:
    frames: dict[str, pd.DataFrame] = {}
    for code in POOL:
        df = load(code)
        df = df[(df["date"] >= IS_START) & (df["date"] <= IS_END)]
        if df.empty:
            print(f"[SKIP] {code} 在 IS 区间内无数据")
            continue
        frames[code] = to_wf(df)
        print(f"[LOAD] {code} {len(df)} 行 {df['date'].min().date()}~{df['date'].max().date()}")

    # --- 全区间成本（决定"资金"这一维）---
    cost_full = {
        code: symbol_cost(code, wf[["close"]], lot_size=LOT_SIZE)
        for code, wf in frames.items()
    }
    print("\n== 全区间一手成本（qfq，strict 判据=窗口内最高价）==")
    for code, sc in sorted(cost_full.items(), key=lambda kv: -kv[1].one_lot_max):
        print(f"  {code}  最低 {sc.one_lot_min:>10,.0f}  最高 {sc.one_lot_max:>10,.0f}")

    plans = {
        "as_is": plan_as_is(cost_full, BASE_CASH, price_basis="qfq"),
        "raise_cash": plan_raise_cash(cost_full, BASE_CASH, price_basis="qfq"),
        "drop_symbols": plan_drop_symbols(cost_full, BASE_CASH, price_basis="qfq"),
        "both": plan_both(cost_full, BASE_CASH, price_basis="qfq"),
    }
    print("\n== 口径解析 ==")
    for name, p in plans.items():
        print(f"  {name:14s} cash={p.initial_cash:>10,.0f}  有效 {p.n_effective}/6  {p.note}")

    # --- 窗口（以行数最多的标的为基准；逐标的独立生成，允许长度差异）---
    grid = GridSearchConfig(
        fast_candidates=(3, 5, 10, 20),
        slow_candidates=(15, 25, 40, 60),
        min_train_periods=TRAIN,
        min_oos_periods=OOS,
        min_trades=MIN_TRADES,
        max_combinations=50,
    )
    cands = generate_ma_cross_candidates(grid)
    wcfg = WalkForwardConfig(train_window=TRAIN, oos_window=OOS, step=STEP)

    results: dict[str, dict] = {}

    for mode in ("as_is", "raise_cash", "drop_symbols", "both"):
        cash_fixed = plans[mode].initial_cash
        rows: list[dict] = []
        print(f"\n== 跑口径 {mode}（cash={cash_fixed:,.0f}）==", flush=True)

        for code, wf in frames.items():
            specs = generate_windows(wf.index, wcfg)
            rules = a_share_rules(
                price_limit_rate=a_share_price_limit_rate(code, mode="board")
            )
            cfg = FEE_PRESET_SIMULATION.apply(
                BacktestConfig(symbol=code, initial_cash=cash_fixed, market_rules=rules)
            )
            fac = lambda p: MaCrossStrategy(  # noqa: E731
                fast_window=int(p["fast_window"]), slow_window=int(p["slow_window"])
            )

            for spec in specs:
                # 窗口内成本：既覆盖训练也覆盖 OOS（两段都要能下单）
                seg = wf.loc[(wf.index >= spec.train_start) & (wf.index <= spec.oos_end)]
                wcost = symbol_cost(code, seg[["close"]], lot_size=LOT_SIZE)

                excluded_reason = None
                if mode in ("drop_symbols", "both"):
                    # 逐窗口判定：同一标的在不同年份可买性不同
                    # （茅台 2017 年一手约 4 千买得起，2021 年 13 万买不起）
                    if not wcost.affordable(cash_fixed, "strict"):
                        excluded_reason = (
                            f"窗口内一手 {wcost.one_lot_max:,.0f} > 资金 {cash_fixed:,.0f}"
                        )

                if excluded_reason:
                    rows.append({
                        "code": code,
                        "window_index": spec.window_index,
                        "train_start": str(spec.train_start.date()),
                        "oos_start": str(spec.oos_start.date()),
                        "status": "excluded_by_capital",
                        "reason": excluded_reason,
                        "ret": None,
                    })
                    continue

                r = run_walkforward_window(
                    wf,
                    cfg,
                    window_index=spec.window_index,
                    train_start=spec.train_start,
                    train_end=spec.train_end,
                    oos_start=spec.oos_start,
                    oos_end=spec.oos_end,
                    strategy_factory=fac,
                    candidates=cands,
                    min_trades=MIN_TRADES,
                    warmup_days=TRAIN,
                )
                status = (
                    "selection_failed" if not r.selection_success
                    else ("oos_ok" if r.oos_success else "oos_failed")
                )
                rows.append({
                    "code": code,
                    "window_index": r.window_index,
                    "train_start": str(spec.train_start.date()),
                    "oos_start": str(spec.oos_start.date()),
                    "status": status,
                    "one_lot_max_in_window": wcost.one_lot_max,
                    "selected": r.selected_params,
                    "ret": r.cumulative_return,
                    "mdd": r.max_drawdown_ratio,
                    "trades": r.trade_count,
                    # order_count 与 trade_count 必须分开看：
                    # trade_count 只数**已平仓**交易，持仓未平也是 0；
                    # 所以「trades=0」不能当空壳判据，否则会把「持有到期未平仓」误判成「一笔没成交」。
                    "orders": r.order_count,
                })

        valid = [x["ret"] for x in rows if x["status"] == "oos_ok"]
        by_status: dict[str, int] = {}
        for x in rows:
            by_status[x["status"]] = by_status.get(x["status"], 0) + 1

        # --- 执行形态分类（这比"收益多少"更能说明窗口到底发生了什么）---
        exec_shape: dict[str, int] = {}
        for x in rows:
            if x["status"] != "oos_ok":
                continue
            orders = x.get("orders") or 0
            trades = x.get("trades") or 0
            ret = x.get("ret")
            if orders == 0:
                shape = "no_signal"          # 策略没发信号
            elif trades == 0 and (ret is None or abs(ret) < 1e-12):
                shape = "all_rejected"       # 发了单，一笔没成交（资金不足的签名）
            elif trades == 0:
                shape = "holding_at_end"     # 买了但持有到窗口结束未平仓
            else:
                shape = "executed"
            exec_shape[shape] = exec_shape.get(shape, 0) + 1

        # 干净样本：真的下过单（orders>0）。用 trades>0 会漏掉「持有未平仓」。
        clean = [x["ret"] for x in rows
                 if x["status"] == "oos_ok" and (x.get("orders") or 0) > 0
                 and x["ret"] is not None]

        per_sym: dict[str, dict] = {}
        for x in rows:
            d = per_sym.setdefault(x["code"], {"oos_ok": 0, "selection_failed": 0,
                                              "excluded": 0, "total": 0})
            d["total"] += 1
            if x["status"] == "oos_ok":
                d["oos_ok"] += 1
            elif x["status"] == "selection_failed":
                d["selection_failed"] += 1
            elif x["status"] == "excluded_by_capital":
                d["excluded"] += 1

        results[mode] = {
            "initial_cash": cash_fixed,
            "plan": plans[mode].as_dict(),
            "n_rows": len(rows),
            "by_status": by_status,
            "exec_shape": exec_shape,
            "per_symbol": per_sym,
            "ret_dist": dist(valid),
            "ret_dist_clean": dist(clean),
            "rows": rows,
        }
        print(f"  状态分布 {by_status}")
        print(f"  执行形态 {exec_shape}")
        print(f"  有效样本 {len(valid)}  中位 {dist(valid)}")
        print(f"  干净样本 {len(clean)}（orders>0）  中位 {dist(clean)}")
        for c in sorted(per_sym):
            d = per_sym[c]
            print(f"    {c} ok={d['oos_ok']:>2} sel_fail={d['selection_failed']:>2} "
                  f"excluded={d['excluded']:>2} / {d['total']}")

    # --- 汇总对照表 ---
    print("\n" + "=" * 96)
    print(f"{'口径':13s} {'资金':>9s} {'有效':>4s} {'选择失败':>7s} {'剔除':>5s} "
          f"{'中位(全)':>9s} {'中位(干净)':>10s} {'干净n':>6s}")
    print("-" * 96)
    summary = {}
    for mode, r in results.items():
        d = r["ret_dist"] or {}
        dc = r["ret_dist_clean"] or {}
        print(f"{mode:13s} {r['initial_cash']:>9,.0f} {d.get('n', 0):>4d} "
              f"{r['by_status'].get('selection_failed', 0):>7d} "
              f"{r['by_status'].get('excluded_by_capital', 0):>5d} "
              f"{d.get('median', 0):>+9.4f} {dc.get('median', 0):>+10.4f} "
              f"{dc.get('n', 0):>6d}")
        summary[mode] = {
            "initial_cash": r["initial_cash"],
            "n_valid": d.get("n", 0),
            "median": d.get("median"),
            "mean": d.get("mean"),
            "pos_ratio": d.get("pos_ratio"),
            "n_clean": dc.get("n", 0),
            "median_clean": dc.get("median"),
            "mean_clean": dc.get("mean"),
            "pos_ratio_clean": dc.get("pos_ratio"),
            "selection_failed": r["by_status"].get("selection_failed", 0),
            "excluded_by_capital": r["by_status"].get("excluded_by_capital", 0),
            "exec_shape": r["exec_shape"],
        }

    payload = {
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "label": (
            "样本内对照实验（IS 2017-01-03~2024-12-31）。"
            "不触碰已消耗的 2025+ 样本外数据，不构成任何样本外证据。"
        ),
        "data_dir": "data/step75_ext",
        "price_basis": "qfq（前复权，一手成本为真实成本的下界）",
        "design": {
            "train": TRAIN, "oos": OOS, "step": STEP,
            "min_trades": MIN_TRADES, "base_cash": BASE_CASH, "lot_size": LOT_SIZE,
            "cell": "ma_cross / simulation_placeholder",
        },
        "summaries": summary,
        "details": results,
    }
    OUT.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str),
                   encoding="utf-8")
    print(f"\n[OUT] {OUT}")


if __name__ == "__main__":
    main()
