"""诊断「OOS 窗口收益中位数全 0」的成因（B 线第 3 项）。

用法：
    python scripts/diag_oos_zero_median.py

做什么：
1. 用 deepquant 自己的 akshare 适配器拉**真实** A 股日线（东财被拦时自动 fallback 腾讯）；
2. 用 **Step 7.5 原始配置**跑 walk-forward（一个参数都不改）；
3. 逐窗口输出收益 / 订单数 / 成交笔数，按成因分类，并给出口径修正对比。

纪律（红线）：
- 只做**归因诊断**，不调参（R2 / R11）；
- 拉不到真实行情就跳过该标的并记录，**绝不用合成数据顶替**（R3）；
- 结论只成立于**窗口级统计**语义，不代表连续账户收益（R12）。
"""

from __future__ import annotations

import statistics
import sys
import time
from pathlib import Path

import pandas as pd

# 让脚本可以直接从仓库任意位置运行
_SRC = Path(__file__).resolve().parent.parent / "backend" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from deepquant.backtest.config import BacktestConfig  # noqa: E402
from deepquant.backtest.fee_presets import FEE_PRESET_SIMULATION  # noqa: E402
from deepquant.data.loaders.akshare_loader import fetch_daily_a_share  # noqa: E402
from deepquant.strategies.ma_cross import MaCrossStrategy  # noqa: E402
from deepquant.walkforward.parameter_selection import (  # noqa: E402
    GridSearchConfig,
    run_walkforward_v2,
)

# —— 与 data/step75/research.py 完全一致，不做任何调整 ——
MA_GRID = GridSearchConfig(
    fast_candidates=(3, 5, 10, 20),
    slow_candidates=(15, 25, 40, 60),
    min_train_periods=150,
    min_oos_periods=50,
    min_trades=None,
    max_combinations=50,
)
INITIAL_CASH = 100000.0
START, END = "20180101", "20241231"
LOT_SIZE = 100

TARGETS = [
    ("000001", "000001.SZ", "平安银行"),
    ("600519", "600519.SH", "贵州茅台"),
]


def ma_factory(p):
    return MaCrossStrategy(
        fast_window=int(p["fast_window"]), slow_window=int(p["slow_window"])
    )


def classify(r) -> str:
    """把一个 OOS 窗口按「到底参没参与」分类。"""
    orders = r.order_count or 0
    trades = r.trade_count or 0
    ret = r.cumulative_return
    if orders == 0:
        return "no_signal"                              # 窗口内根本没产生信号
    if trades == 0 and (ret is None or ret == 0):
        return "order_unfilled"                         # 有信号/订单，但一笔都没成交
    if trades == 0:
        return "open_only"                              # 只建仓未平仓，收益来自期末浮盈
    return "traded"                                      # 至少完成一次往返


def diag(symbol_code: str, full_symbol: str, name: str, verbose: bool) -> None:
    print("=" * 92)
    print(f"{full_symbol} {name}  ma_cross  simulation_placeholder（Step 7.5 原始配置）")
    print("=" * 92)

    df, meta = fetch_daily_a_share(symbol_code, START, END)
    m = meta.to_metadata()
    if len(df) == 0:
        print(f"[SKIP] {symbol_code} 未取到真实行情 —— 按 R3 停止，不用合成数据顶替")
        return
    print(f"真实行情：{len(df)} 行  {df['date'].min().date()} ~ {df['date'].max().date()}")
    print(f"数据溯源：{m['adapter']} / 源={m['used_source']} / 复权={m['adjust_status']}")

    price_mean = float(df["close"].mean())
    lot_cash = price_mean * LOT_SIZE
    affordable = lot_cash <= INITIAL_CASH
    print(
        f"均价约 {price_mean:.2f} 元 → 买 1 手（{LOT_SIZE} 股）约需 {lot_cash:,.0f} 元；"
        f"初始资金 {INITIAL_CASH:,.0f} 元 "
        + ("✅ 资金可覆盖 1 手" if affordable else "⚠️ 一手都买不起")
    )

    df_wf = df.set_index("date")[["open", "high", "low", "close", "volume"]]
    df_wf.index.name = None

    cfg = FEE_PRESET_SIMULATION.apply(
        BacktestConfig(symbol=full_symbol, initial_cash=INITIAL_CASH)
    )
    t0 = time.time()
    specs, results, summary = run_walkforward_v2(
        df_wf, cfg, strategy_factory=ma_factory, grid_config=MA_GRID
    )
    print(f"walkforward 完成：{len(specs)} 个窗口，耗时 {time.time() - t0:.1f}s")

    rows = []
    for sp, r in zip(specs, results):
        d = vars(sp) if hasattr(sp, "__dict__") else {}
        s, e = d.get("oos_start"), d.get("oos_end")
        bars = len(df_wf.loc[s:e]) if s is not None and e is not None else None
        rows.append(
            {
                "win": r.window_index,
                "start": s,
                "end": e,
                "bars": bars,
                "ret": r.cumulative_return,
                "orders": r.order_count or 0,
                "trades": r.trade_count or 0,
                "kind": classify(r),
            }
        )

    if verbose:
        print(f"\n{'win':>4} {'oos_start':>11} {'oos_end':>11} {'bars':>5} "
              f"{'ret':>10} {'trades':>7} {'orders':>7}  kind")
        print("-" * 92)
        for r in rows:
            ret_s = "None" if r["ret"] is None else f"{r['ret']:+.6f}"
            print(
                f"{r['win']:>4} {str(r['start'])[:10]:>11} {str(r['end'])[:10]:>11} "
                f"{(r['bars'] if r['bars'] is not None else -1):>5} {ret_s:>10} "
                f"{r['trades']:>7} {r['orders']:>7}  {r['kind']}"
            )

    kinds: dict[str, int] = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1

    zero = [r for r in rows if r["ret"] == 0]
    engaged = [r for r in rows if r["kind"] in ("traded", "open_only")]
    engaged_rets = [r["ret"] for r in engaged if r["ret"] is not None]
    rets = [r["ret"] for r in rows if r["ret"] is not None]
    bars_all = [r["bars"] for r in rows if r["bars"] is not None]

    print("\n【窗口形态】")
    if bars_all:
        print(f"  OOS 窗口交易日长度：min={min(bars_all)} "
              f"中位={statistics.median(bars_all)} max={max(bars_all)}"
              f"（网格里最慢的均线候选 = 60 日）")
    print(f"  成因分布：{kinds}")
    print(f"  零收益窗口                : {len(zero)} / {len(rows)}")
    print(f"    其中「完全无信号」      : {kinds.get('no_signal', 0)}")
    print(f"    其中「有订单但零成交」  : {kinds.get('order_unfilled', 0)}")
    print(f"  实际参与交易的窗口        : {len(engaged)} / {len(rows)}")
    print(f"  每窗口订单数：min={min(r['orders'] for r in rows)} "
          f"max={max(r['orders'] for r in rows)} "
          f"合计={sum(r['orders'] for r in rows)}")
    print(f"  每窗口往返成交：min={min(r['trades'] for r in rows)} "
          f"max={max(r['trades'] for r in rows)} "
          f"合计={sum(r['trades'] for r in rows)}")

    print("\n【口径对比】")
    pos_all = sum(1 for v in rets if v > 0)
    print(f"  全窗口（现口径）：n={len(rets)} 正收益={pos_all}"
          f"（{pos_all / len(rets):.1%}） 中位数={statistics.median(rets):+.6f} "
          f"均值={sum(rets) / len(rets):+.6f}")
    if engaged_rets:
        pos_eng = sum(1 for v in engaged_rets if v > 0)
        print(f"  仅参与窗口      ：n={len(engaged_rets)} 正收益={pos_eng}"
              f"（{pos_eng / len(engaged_rets):.1%}） "
              f"中位数={statistics.median(engaged_rets):+.6f} "
              f"均值={sum(engaged_rets) / len(engaged_rets):+.6f}")

    print(f"\n  （汇总口径核对）median_ret={summary.median_cumulative_return} "
          f"positive_ratio={summary.positive_ratio}")


def main() -> None:
    verbose = "--quiet" not in sys.argv
    print("诊断：OOS 窗口收益中位数全 0 的成因")
    print("性质：归因诊断，不调参（R2 / R11）；窗口级统计，不代表连续账户收益（R12）\n")
    for code, full, name in TARGETS:
        diag(code, full, name, verbose)
        print()


if __name__ == "__main__":
    main()
