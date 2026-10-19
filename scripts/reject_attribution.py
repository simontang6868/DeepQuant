"""拒单归因：把"下了单但零成交"拆成具体原因。

为什么必须拆
------------
`docs/CAPITAL_POLICY_AB_2026-10-19.md` 发现「orders>0 且 trades=0 且 ret=0」的窗口有 4 个，
但**把资金从 10 万提到 26 万只消掉了其中 1 个**（600519 w10，一手 17.6 万）。
剩下 3 个在 26 万下依然零成交 —— 说明"下单全被拒"至少有两种原因，资金只是其中一种。

如果不拆就把它们记为"资金不足"，会得到一个错误结论：
「补钱就能让所有标的正常交易」，而事实是补钱只能解决一部分。

做法
----
`run_walkforward_window(capture_trades=True)` 会保留 OOS 段的逐笔订单（含 `reason` 字段，
引擎在 `OrderStatus.REJECTED` 时会写入）。按 reason 聚合即可得到归因表。
**不重跑整段研究，只重跑这 3 个窗口**。

输出：``probe_out/reject_attribution.json`` + stdout
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))

from deepquant.backtest.config import BacktestConfig  # noqa: E402
from deepquant.backtest.fee_presets import FEE_PRESET_SIMULATION  # noqa: E402
from deepquant.market.rules import a_share_price_limit_rate, a_share_rules  # noqa: E402
from deepquant.strategies.ma_cross import MaCrossStrategy  # noqa: E402
from deepquant.walkforward.parameter_selection import (  # noqa: E402
    GridSearchConfig,
    generate_ma_cross_candidates,
    run_walkforward_window,
)

AB = ROOT / "docs" / "CAPITAL_POLICY_AB_2026-10-19.json"
DATA = ROOT / "data" / "step75_ext"
TARGETS = [("300750.SZ", 3), ("300750.SZ", 6), ("000858.SZ", 14)]
CASH = 260_000.0  # 用补过资金的档位，确保「资金不足」不会被误当成原因


def load(code: str) -> pd.DataFrame:
    df = pd.read_csv(DATA / f"{code}.qfq.csv")
    df["date"] = pd.to_datetime(df["date"])
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    out = df.set_index("date")[["open", "high", "low", "close", "volume"]].copy()
    out.index.name = None
    return out, df


def main() -> None:
    ab = json.loads(AB.read_text(encoding="utf-8"))
    rows = {
        (r["code"], r["window_index"]): r
        for r in ab["details"]["raise_cash"]["rows"]
        if r["status"] == "oos_ok"
    }
    grid = GridSearchConfig(
        fast_candidates=(3, 5, 10, 20), slow_candidates=(15, 25, 40, 60),
        min_train_periods=150, min_oos_periods=75, min_trades=2, max_combinations=50,
    )
    cands = generate_ma_cross_candidates(grid)

    out = []
    for code, wi in TARGETS:
        wf, raw = load(code)
        r0 = rows[(code, wi)]
        train_start = pd.Timestamp(r0["train_start"])
        # 训练期长度固定 150 交易日的窗口：用索引定位
        idx = wf.index
        ts_pos = int(idx.get_loc(train_start))
        train_start_pos = ts_pos
        train_end = idx[train_start_pos + 149]
        oos_start = pd.Timestamp(r0["oos_start"])
        oos_pos = int(idx.get_loc(oos_start))
        oos_end = idx[min(oos_pos + 74, len(idx) - 1)]

        rules = a_share_rules(price_limit_rate=a_share_price_limit_rate(code, mode="board"))
        cfg = FEE_PRESET_SIMULATION.apply(
            BacktestConfig(symbol=code, initial_cash=CASH, market_rules=rules)
        )
        r = run_walkforward_window(
            wf, cfg, window_index=wi,
            train_start=train_start, train_end=train_end,
            oos_start=oos_start, oos_end=oos_end,
            strategy_factory=lambda p: MaCrossStrategy(
                fast_window=int(p["fast_window"]), slow_window=int(p["slow_window"])
            ),
            candidates=cands, min_trades=2, warmup_days=150, capture_trades=True,
        )
        reasons = Counter(o.get("reason") or "(无 reason)" for o in r.orders)
        status = Counter(o.get("status") for o in r.orders)
        print(f"\n== {code} w{wi}  {train_start.date()}~{train_end.date()} | "
              f"OOS {oos_start.date()}~{oos_end.date()}")
        print(f"   orders={r.order_count} trades={r.trade_count} ret={r.cumulative_return}")
        print(f"   状态 {dict(status)}")
        for k, v in reasons.most_common():
            print(f"   reason[{k}] x{v}")
        out.append({
            "code": code, "window_index": wi,
            "train": f"{train_start.date()}~{train_end.date()}",
            "oos": f"{oos_start.date()}~{oos_end.date()}",
            "cash": CASH, "orders": r.order_count, "trades": r.trade_count,
            "ret": r.cumulative_return,
            "status_counts": dict(status),
            "reason_counts": dict(reasons),
            "orders_detail": list(r.orders),
        })

    p = ROOT / "probe_out" / "reject_attribution.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print(f"\n[OUT] {p}")


if __name__ == "__main__":
    main()
