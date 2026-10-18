"""B20：对已有 288 组历史结果做窗口级 B0/B1 对照（只诊断，不跑新回测）。

背景
----
B15/B16/B17 的结论建立在「窗口级收益中位数 ≈ 0」上。中位数只说绝对收益，
不说「相对放着不动是赢是输」。P5（基准缺失）因此一直是个缺口。

本脚本用已交付的 A7 基准模块（``performance.benchmarks``）补这个对照：

- **B0 现金**：逐窗收益恒为 0 → 配对精确（配对差 = 策略逐窗收益本身）。
- **B1 买入持有**：同区间、同复权、同成交时点、同初始资金、同费率档。

关键约束（全部硬性）
--------------------
1. **不跑新回测**：策略侧不重算任何信号、不重跑引擎，只读已落盘的分位数。
2. **不改参数**：窗口生成只用 ``design`` 里落盘的 ``min_train_periods`` /
   ``min_oos_periods``，不引入任何新取值。
3. **不删结果**：288 组全部保留；不可比的分组单独计数并说明原因，不剔除。
4. 措辞是「诊断 / 补全对照」，不是「解决」。

为什么是「分布级对照」而不是「逐窗配对」
----------------------------------------
历史结果只落盘了 ``window_stats``（分位数），没有逐窗收益明细。B1 逐窗收益
随行情变化，要算配对差必须有**同一批窗口上的策略逐窗收益**，这在现有数据里
不可重建。因此：

- B0：`paired=True`（基准恒 0，数学上精确）；
- B1：`paired=False`（只比同窗口集合上的中位数/分位数/亏损占比）。

并且明确一条算术事实：**差值的中位数 ≠ 中位数的差**。B1 那一列报的是后者，
不得写成「配对差值中位数」。Gate 1 起新实验必须落盘逐窗明细，届时才能做真配对。

用法
----
    python scripts/b20_window_benchmark.py \
        --summary "D:/DeepQuant power by txm/b50_dist/step75_research_summary.json" \
        --data-root "D:/DeepQuant power by txm/b50_dist" \
        --out "D:/DeepQuant power by txm/b20_out"
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.fee_presets import (
    FEE_PRESET_HIGH_COST,
    FEE_PRESET_REFERENCE,
    FEE_PRESET_SIMULATION,
)
from deepquant.market.rules import a_share_rules
from deepquant.performance.benchmarks import buy_and_hold, cash_benchmark
from deepquant.performance.window_distribution import (
    WindowComparison,
    WindowDistribution,
    aggregate_comparisons,
    compare_windows,
)
from deepquant.walkforward import WalkForwardConfig, generate_windows

PRESETS_BY_SOURCE = {
    p.fee_source: p
    for p in (FEE_PRESET_SIMULATION, FEE_PRESET_REFERENCE, FEE_PRESET_HIGH_COST)
}

# 历史口径：oos75-v2 的装置里**没有启用涨跌停**（D2 缺陷）。
# 为了让 B1 与当时的策略处在同一装置下，主口径必须是「不启用」；
# 启用后的结果只作对照计数（有多少个窗口的期初买入会被涨停挡住）。
PRIMARY_PRICE_LIMIT_RATE = None
DIAGNOSTIC_PRICE_LIMIT_RATE = 0.10


def load_prices(csv_path: Path) -> pd.DataFrame:
    """读入缓存行情，返回以日期为索引、按日期升序的 OHLCV。"""
    df = pd.read_csv(csv_path)
    df["date"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close", "volume"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.sort_values("date").reset_index(drop=True)
    return df.set_index("date")[["open", "high", "low", "close", "volume"]]


def build_windows(df: pd.DataFrame, design: dict) -> list:
    """按落盘的 design 复现窗口（train/oos/step 全部取自 design，不引入新值）。"""
    cfg = WalkForwardConfig(
        train_window=int(design["min_train_periods"]),
        oos_window=int(design["min_oos_periods"]),
        step=int(design["min_oos_periods"]),
        min_train=int(design["min_train_periods"]),
        min_oos=int(design["min_oos_periods"]),
    )
    return [w for w in generate_windows(df.index, cfg) if w.valid]


def window_slice(df: pd.DataFrame, spec) -> tuple[list, list, list]:
    """取样本外区间（含首尾）的日期 / 开盘 / 收盘。"""
    sub = df.loc[spec.oos_start : spec.oos_end]
    dates = list(sub.index)
    opens = [None if pd.isna(v) else float(v) for v in sub["open"]]
    closes = [None if pd.isna(v) else float(v) for v in sub["close"]]
    return dates, opens, closes


def window_returns(
    df: pd.DataFrame,
    specs: list,
    config: BacktestConfig,
    *,
    liquidate_at_end: bool = False,
) -> tuple[list[float | None], int, int]:
    """逐窗买入持有收益。

    返回 (逐窗收益, 不可执行窗口数, 期初被涨停挡住的窗口数)。
    不可执行 → None（**绝不记 0%**）：期初资金买不起 1 手时，
    「买入持有」这个基准根本不存在，记 0% 会污染整列中位数。
    """
    returns: list[float | None] = []
    n_not_executable = 0
    n_blocked = 0
    for spec in specs:
        dates, opens, closes = window_slice(df, spec)
        if len(dates) < 2:
            returns.append(None)
            n_not_executable += 1
            continue
        result = buy_and_hold(dates, opens, closes, config, liquidate_at_end=liquidate_at_end)
        if not result.is_executable:
            returns.append(None)
            n_not_executable += 1
            if result.blocked_reason:
                n_blocked += 1
            continue
        is_complete = result.summary.is_complete
        value = result.summary.cumulative_return
        returns.append(value if (is_complete and value is not None) else None)
        if value is None:
            n_not_executable += 1
    return returns, n_not_executable, n_blocked


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="B20 窗口级 B0/B1 对照（只诊断）")
    parser.add_argument("--summary", required=True, help="已落盘的 step75_research_summary.json")
    parser.add_argument("--data-root", required=True, help="缓存行情目录（含 *.qfq.csv）")
    parser.add_argument("--out", required=True, help="输出目录")
    parser.add_argument(
        "--liquidate",
        action="store_true",
        help="B1 期末平仓（含卖出成本）。默认不平仓，与 A7 默认口径一致。",
    )
    args = parser.parse_args(argv)

    summary_path = Path(args.summary)
    data_root = Path(args.data_root)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    design = payload["design"]
    initial_cash = float(design.get("initial_cash", 100000.0))
    results = payload["results"]

    symbols = sorted({r["symbol"] for r in results})
    print(f"[LOAD ] {summary_path.name}: {len(results)} 组 / {len(symbols)} 只", flush=True)
    print(
        f"[DESIGN] {design.get('design_version')} | "
        f"train={design['min_train_periods']} oos={design['min_oos_periods']} "
        f"cash={initial_cash:.0f}",
        flush=True,
    )

    # --- 1. 逐标的复现窗口，并与落盘的 n_windows 对齐校验 ---
    specs_by_symbol: dict[str, list] = {}
    n_windows_expected: dict[str, int] = {}
    for r in results:
        n_windows_expected.setdefault(r["symbol"], r["n_windows"])
    mismatched: list[dict] = []
    for symbol in symbols:
        csv_path = data_root / f"{symbol}.qfq.csv"
        if not csv_path.exists():
            raise SystemExit(f"缺少行情缓存：{csv_path}（R3：不编造数据，直接停下）")
        df = load_prices(csv_path)
        specs = build_windows(df, design)
        specs_by_symbol[symbol] = specs
        if len(specs) != n_windows_expected[symbol]:
            # 不一致说明窗口口径已漂移，继续算出来的对照不可信 → 明确记录
            mismatched.append(
                {
                    "symbol": symbol,
                    "reproduced": len(specs),
                    "recorded": n_windows_expected[symbol],
                    "n_rows": len(df),
                }
            )
    print(
        f"[WINDOW] 复现窗口：{len(symbols)} 只，口径不一致 {len(mismatched)} 只",
        flush=True,
    )
    if mismatched:
        for m in mismatched[:10]:
            print(f"  ! {m['symbol']} 复现 {m['reproduced']} vs 落盘 {m['recorded']}", flush=True)

    # --- 2. 逐标的缓存 B1（按期初资金口径分档，不随策略变化）---
    # 三档费率的买入费用略有差异（佣金率不同），因此 B1 也按费率档分别计算。
    # 同时跑一遍「启用涨跌停」的诊断口径，只为统计有多少窗口的期初买入会被
    # 涨停挡住 —— 分布本身仍用主口径（不启用），与 oos75-v2 装置保持一致。
    b1_cache: dict[tuple[str, str, bool], list[float | None]] = {}
    b1_exec_diag: dict[tuple[str, str], dict[str, int]] = {}
    for symbol in symbols:
        df = load_prices(data_root / f"{symbol}.qfq.csv")
        specs = specs_by_symbol[symbol]
        for fee_source, preset in PRESETS_BY_SOURCE.items():
            for liquidate in (False, True):
                if liquidate and not args.liquidate:
                    continue
                base = BacktestConfig(
                    symbol=symbol,
                    initial_cash=initial_cash,
                    market_rules=a_share_rules(price_limit_rate=PRIMARY_PRICE_LIMIT_RATE),
                )
                cfg = preset.apply(base)
                returns, n_bad, n_blocked = window_returns(
                    df, specs, cfg, liquidate_at_end=liquidate
                )
                b1_cache[(symbol, fee_source, liquidate)] = returns
                if not liquidate:
                    b1_exec_diag[(symbol, fee_source)] = {
                        "not_executable": n_bad,
                        "blocked_by_limit": n_blocked,
                    }
            # 诊断口径：同样的窗口，启用 board 涨跌停，只看「期初买不进」的窗口数
            board_cfg = BacktestConfig(
                symbol=symbol,
                initial_cash=initial_cash,
                market_rules=a_share_rules(price_limit_rate=DIAGNOSTIC_PRICE_LIMIT_RATE),
            )
            _, _, n_blocked_board = window_returns(
                df, specs, preset.apply(board_cfg), liquidate_at_end=False
            )
            b1_exec_diag[(symbol, fee_source)]["blocked_by_limit_if_enabled"] = n_blocked_board

    # --- 3. 逐组配对 / 对照 ---
    groups: list[dict] = []
    comparisons_b0: list[WindowComparison] = []
    comparisons_b1: list[WindowComparison] = []
    skipped_groups: list[dict] = []

    for r in results:
        symbol = r["symbol"]
        strategy_stats = WindowDistribution.from_mapping(r.get("window_stats"))
        if strategy_stats is None:
            skipped_groups.append({"symbol": symbol, "strategy": r["strategy"],
                                   "fee_source": r["fee_source"], "reason": "无 window_stats"})
            continue

        specs = specs_by_symbol[symbol]
        n_specs = len(specs)
        diag = b1_exec_diag[(symbol, r["fee_source"])]

        # B0：现金逐窗恒 0 → 与窗口数无关，永远对齐；用 A7 的 cash_benchmark 取口径字段
        cash = cash_benchmark(
            initial_cash,
            start_date=specs[0].oos_start if specs else None,
            end_date=specs[-1].oos_end if specs else None,
            n_periods=n_specs,
        )
        assert cash.cumulative_return == 0.0, "B0 现金基准收益必须恒为 0"
        b0_dist = WindowDistribution.from_returns([0.0] * strategy_stats.n_windows)
        cmp_b0 = compare_windows(
            strategy_stats, b0_dist, benchmark_kind="B0_cash", paired=True
        )
        comparisons_b0.append(cmp_b0)

        # B1：分布级对照（策略逐窗明细未落盘 → 无法真配对）
        b1_returns = b1_cache[(symbol, r["fee_source"], False)]
        b1_dist = WindowDistribution.from_returns(b1_returns)
        cmp_b1 = None
        if b1_dist is not None:
            cmp_b1 = compare_windows(
                strategy_stats, b1_dist, benchmark_kind="B1_buy_and_hold", paired=False
            )
            comparisons_b1.append(cmp_b1)

        groups.append(
            {
                "symbol": symbol,
                "name": r.get("name"),
                "strategy": r["strategy"],
                "fee_source": r["fee_source"],
                "n_windows_spec": n_specs,
                "n_windows_strategy": strategy_stats.n_windows,
                "b1_windows_not_executable": diag["not_executable"],
                "b1_windows_blocked_by_limit": diag["blocked_by_limit_if_enabled"],
                "strategy_stats": strategy_stats.to_dict(),
                "b1_stats": None if b1_dist is None else b1_dist.to_dict(),
                "b0_comparison": cmp_b0.to_dict(),
                "b1_comparison": None if cmp_b1 is None else cmp_b1.to_dict(),
                "b1_assumptions": (
                    [
                        "期初首个可执行日按开盘价买入（含买入滑点）",
                        "数量受 100 股整手与「成交额+费用<=现金」约束",
                        "期末按收盘价估值，不平仓（不虚构卖出）",
                        f"初始资金 {initial_cash:.0f}，与策略窗口一致",
                        "涨跌停：主口径不启用（与 oos75-v2 装置一致）",
                    ]
                    if b1_dist is not None
                    else []
                ),
            }
        )

    # --- 4. 汇总（按 策略 × 费率 分组；窗口数不一致的组不并入）---
    def summarize(items: list[WindowComparison]) -> dict[str, object]:
        return aggregate_comparisons(items)

    def comparison_of(group: dict) -> WindowComparison | None:
        """从已落盘字段重建对照对象（避免在汇总阶段重算基准）。"""
        if group["b1_stats"] is None:
            return None
        return WindowComparison(
            strategy=WindowDistribution.from_mapping(group["strategy_stats"]),
            benchmark=WindowDistribution.from_mapping(group["b1_stats"]),
            benchmark_kind="B1_buy_and_hold",
            paired=False,
            n_aligned=group["b1_comparison"]["n_aligned"],
        )

    by_strategy_fee: dict[str, dict] = {}
    for strategy in sorted({g["strategy"] for g in groups}):
        for fee_source in sorted({g["fee_source"] for g in groups}):
            idx = [
                i
                for i, g in enumerate(groups)
                if g["strategy"] == strategy and g["fee_source"] == fee_source
            ]
            if not idx:
                continue
            b0_items = [comparisons_b0[i] for i in idx]
            b1_items = [c for c in (comparison_of(groups[i]) for i in idx) if c is not None]
            by_strategy_fee[f"{strategy}|{fee_source}"] = {
                "strategy": strategy,
                "fee_source": fee_source,
                "b0": summarize(b0_items),
                "b1": summarize(b1_items),
                "n_groups": len(idx),
                "n_groups_with_b1": len(b1_items),
                "n_groups_b1_not_executable": len(idx) - len(b1_items),
            }

    by_strategy: dict[str, dict] = {}
    for strategy in sorted({g["strategy"] for g in groups}):
        idx = [i for i, g in enumerate(groups) if g["strategy"] == strategy]
        b0_items = [comparisons_b0[i] for i in idx]
        b1_items = [c for c in (comparison_of(groups[i]) for i in idx) if c is not None]
        by_strategy[strategy] = {
            "strategy": strategy,
            "b0": summarize(b0_items),
            "b1": summarize(b1_items),
            "n_groups_with_b1": len(b1_items),
            "n_groups_b1_not_executable": len(idx) - len(b1_items),
        }

    out = {
        "run_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "kind": "B20_window_benchmark_diagnostic",
        "note": (
            "只诊断/补全对照：不跑新回测、不改参数、不删结果。"
            "B0 配对精确（基准恒 0）；B1 为分布级对照（策略逐窗明细未落盘）。"
        ),
        "source": {
            "summary_path": str(summary_path),
            "summary_sha256": sha256_of(summary_path),
            "summary_run_at": payload.get("run_at"),
            "summary_data_source": payload.get("data_source"),
            "design_version": design.get("design_version"),
            "n_groups": len(results),
            "n_symbols": len(symbols),
            "n_skipped_by_data_gate": len(payload.get("skipped", [])),
        },
        "window_generation": {
            "train_periods": design["min_train_periods"],
            "oos_periods": design["min_oos_periods"],
            "step": design["min_oos_periods"],
            "price_limit_primary": PRIMARY_PRICE_LIMIT_RATE,
            "price_limit_diagnostic": DIAGNOSTIC_PRICE_LIMIT_RATE,
            "liquidate_at_end": bool(args.liquidate),
            "mismatched_symbols": mismatched,
        },
        "aggregates": {
            "by_strategy_fee": by_strategy_fee,
            "by_strategy": by_strategy,
        },
        "groups": groups,
        "skipped_groups": skipped_groups,
    }

    out_path = out_dir / "b20_window_benchmark.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"[WRITE] {out_path}", flush=True)

    # --- 5. 控制台摘要（人读）---
    print("\n=== B1 买入持有对照（分布级；差值 = 策略 − B&H，正数表示跑赢）===")
    header = f"{'strategy':<16}{'fee':<30}{'组数':>4}{'可比':>4}{'B1不可执行':>10}{'中位Δ':>12}{'正/负/零':>12}"
    print(header)
    for key, agg in by_strategy_fee.items():
        b1 = agg["b1"]
        print(
            f"{agg['strategy']:<16}{agg['fee_source'][:28]:<30}"
            f"{agg['n_groups']:>4}{b1['n_aligned']:>4}{agg['n_groups_b1_not_executable']:>10}"
            f"{(b1['median_delta_p50'] if b1['median_delta_p50'] is not None else float('nan')):>12.6f}"
            f"{b1['n_positive']:>5}/{b1['n_negative']:>3}/{b1['n_zero']:>3}"
        )
    print("\n=== 按策略合并（三档费率全部计入）===")
    for strategy, agg in by_strategy.items():
        b1 = agg["b1"]
        print(
            f"{strategy:<16} 可比 {b1['n_aligned']:>3} 组 | "
            f"Δ中位数 {b1['median_delta_p50']:+.6f} | "
            f"正 {b1['n_positive']} / 负 {b1['n_negative']} / 零 {b1['n_zero']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
