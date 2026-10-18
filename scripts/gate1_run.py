"""Gate 1 横截面动量实验的执行脚本（预登记 `gate1-prereg-v1.3`）。

设计原则（三条，不是为了好看）：

1. **默认不产生任何收益数字。** 不带参数运行时是 ``--dry-run``，只做输入校验、
   候选集规模统计与控制流演练。研究结论必须来自一次显式的、被授权的运行，
   而不是「跑着跑着就出了个数」。
2. **授权写在代码里。** 真跑需要 ``--execute`` **且** ``--authorized-by "<原话>"``
   两个条件同时满足。缺一个直接退出，并把「Gate 1 第二阶段需用户明确授权」写进
   错误信息。把闸门只写在文档里，等于没有闸门——文档不会阻止任何人敲回车。
3. **不重写撮合。** 策略与基准都走同一个 ``run_portfolio_backtest``，差别只在
   目标权重（``top_fraction`` / 加权方式），不另起一套成交逻辑。

用法::

    # 输入校验与控制流演练（默认，安全）
    python scripts/gate1_run.py

    # 真跑（需用户明确授权后，把原话填进去）
    python scripts/gate1_run.py --execute --authorized-by "<用户授权原话>"

    # 排除指定标的重跑（预登记 §10.2 第三条：数据缺陷影响能否隔离）
    # 真跑时额外执行 1 组对照，26 次回测 → 28 次
    python scripts/gate1_run.py --execute --authorized-by "<原话>" --exclude-symbols "600809.SH"

输出（真跑）：``gate1_out/<run_id>/`` 下的 ``monthly_paired.csv``、
``sensitivity.csv``、``summary.json``、``manifest.json``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

# 允许直接从仓库根目录运行（不要求先 pip install -e）
_SRC = Path(__file__).resolve().parents[1] / "backend" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from deepquant.backtest.config import BacktestConfig                       # noqa: E402
from deepquant.backtest.cross_section import (                             # noqa: E402
    CrossSectionConfig,
    CrossSectionPanel,
    trading_calendar,
)
from deepquant.backtest.fee_presets import get_preset                      # noqa: E402
from deepquant.backtest.portfolio import (                                 # noqa: E402
    PortfolioBacktestConfig,
    TargetPortfolio,
    build_rebalance_schedule,
    month_end_signal_dates,
    run_portfolio_backtest,
)
from deepquant.market.rules import a_share_rules  # noqa: E402
from deepquant.performance.gate1_judgement import (                        # noqa: E402
    N_SENSITIVITY,
    SensitivityOutcome,
    attach_bh,
    judge,
    paired_stats,
)


# --------------------------------------------------------------------------- #
# 预登记里写死的常量（改这里就是改协议，必须先改文档并重新冻结）
# --------------------------------------------------------------------------- #

PREREG_VERSION = "gate1-prereg-v1.3"
PREREG_SHA256 = "8b011551"          # 前缀，完整值见 docs/GATE1_PREREGISTRATION.sha256

BACKTEST_START = "2017-02-01"       # 首个信号日 2017-01-26 之后才开始持仓
BACKTEST_END = "2024-12-31"
OOS_START = "2021-01-01"
OOS_END = "2024-12-31"
S12_END = "2022-12-31"              # 敏感性 S12：OOS 前半段

INITIAL_CASH = 1_000_000.0
MIN_ASSETS = 5                      # 预登记 §5：候选 < 5 只当期空仓
MAIN_FEE = "broker_reference_common_2024"
HIGH_FEE = "research_scenario_high_cost"

# 组合层只能接受**单一**涨跌停比例（`MarketRules.price_limit` 是标量，
# 没有 per-symbol 通道）。预登记 §6 写的是 board 模式（创业板 20% / 其余 10%），
# 在本层无法直接实现，故退化为 flat 10%，理由见 docs/GATE1_STAGE2_IMPLEMENTATION_*.md：
#   - 池子 41/50 是主板，10% 对多数标的是**正确**值；
#   - 2020-08-24 之前创业板真实就是 10%，flat 10% 在那段同样**正确**；
#   - 只在 2020-08-24 之后对 9 只双创标的偏严（本该能成交的被挡）→ 悲观方向，
#     不会高估策略的成交能力。
# 这比用 20% 好：20% 会让 41 只主板永久偏松，那是乐观偏差，正是预登记想避免的方向。
PORTFOLIO_PRICE_LIMIT_RATE = 0.10
GEM_PREFIXES = ("300", "301", "688", "689")


# --------------------------------------------------------------------------- #
# 实验定义
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ExperimentSpec:
    """一次实验的完整定义。``name == "main"`` 是主实验，其余是敏感性。"""

    name: str
    variable: str
    value: str
    lookback: int = 60
    skip: int = 5
    top_fraction: float = 0.2
    fee_source: str = MAIN_FEE
    weighting: str = "equal"        # equal | momentum
    universe: str = "all"           # all | main_board
    eval_end: str = OOS_END         # S12 用 S12_END 截短判定窗口
    note: str | None = None


MAIN_SPEC = ExperimentSpec(name="main", variable="—", value="基准配置")

SENSITIVITY_SPECS: tuple[ExperimentSpec, ...] = (
    ExperimentSpec("S1", "lookback", "20日", lookback=20),
    ExperimentSpec("S2", "lookback", "120日", lookback=120),
    ExperimentSpec("S3", "lookback", "250日", lookback=250),
    ExperimentSpec("S4", "skip", "0日", skip=0),
    ExperimentSpec("S5", "skip", "20日", skip=20),
    ExperimentSpec("S6", "top_fraction", "top10%", top_fraction=0.10),
    ExperimentSpec("S7", "top_fraction", "top30%", top_fraction=0.30),
    ExperimentSpec("S8", "top_fraction", "top50%", top_fraction=0.50),
    ExperimentSpec("S9", "fee_source", HIGH_FEE, fee_source=HIGH_FEE),
    ExperimentSpec("S10", "weighting", "动量加权", weighting="momentum"),
    ExperimentSpec("S11", "universe", "仅主板", universe="main_board"),
    ExperimentSpec("S12", "eval_window", "OOS前半", eval_end=S12_END),
)


def all_specs() -> tuple[ExperimentSpec, ...]:
    return (MAIN_SPEC,) + SENSITIVITY_SPECS


# --------------------------------------------------------------------------- #
# 数据加载
# --------------------------------------------------------------------------- #


def load_prices(data_root: Path) -> dict[str, pd.DataFrame]:
    """读 50 只前复权日线。列缺失直接报错，不静默补列。"""
    files = sorted(data_root.glob("*.qfq.csv"))
    if not files:
        raise FileNotFoundError(f"{data_root} 下没有 *.qfq.csv")

    required = {"date", "open", "high", "low", "close", "amount"}
    prices: dict[str, pd.DataFrame] = {}
    for p in files:
        code = p.name.replace(".qfq.csv", "")
        df = pd.read_csv(p, parse_dates=["date"]).sort_values("date")
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"{code} 缺少列 {sorted(missing)}")
        prices[code] = df.reset_index(drop=True)
    return prices


def filter_universe(prices: Mapping[str, pd.DataFrame], universe: str) -> dict[str, pd.DataFrame]:
    if universe == "all":
        return dict(prices)
    if universe == "main_board":
        return {k: v for k, v in prices.items() if k[:3] not in GEM_PREFIXES}
    raise ValueError(f"未知 universe {universe!r}")


def parse_exclude_symbols(raw: str | None) -> tuple[str, ...]:
    """解析 ``--exclude-symbols``：支持逗号与空白混用、去重、保持首次出现顺序。

    刻意做得比「split(',')」严格：

    - 空串报错。``--exclude-symbols ""`` 若被当成「没有排除」，用户会以为跑了
      排除版对照，实际跑的是全池版——两者混淆会直接污染 §10.2 第三条判定。
    - 不存在的代码由 ``apply_exclusion`` 报错而不是静默跳过，理由同上。
    """
    if raw is None:
        return ()
    text = raw.strip()
    if not text:
        raise ValueError("--exclude-symbols 收到空串：要么不传该参数，要么给出至少一个代码")
    parts = [p.strip() for p in text.replace(",", " ").replace("，", " ").split()]
    codes = [p for p in parts if p]
    if not codes:
        raise ValueError(f"--exclude-symbols 解析后为空：{raw!r}")
    seen: dict[str, None] = {}
    for c in codes:
        seen.setdefault(c, None)
    return tuple(seen)


def apply_exclusion(
    prices: Mapping[str, pd.DataFrame], symbols: Sequence[str]
) -> dict[str, pd.DataFrame]:
    """从行情字典里剔除指定标的。

    不存在的代码直接报错：静默忽略会让「排除后重跑」实际没排除，而
    §10.2 第三条正是靠「包含/排除两版符号是否一致」来判定的。
    """
    if not symbols:
        return dict(prices)
    unknown = [s for s in symbols if s not in prices]
    if unknown:
        raise ValueError(
            f"--exclude-symbols 含行情里不存在的代码 {unknown}；"
            f"可用代码示例 {sorted(prices)[:5]}（共 {len(prices)} 只）"
        )
    if len(symbols) >= len(prices):
        raise ValueError(f"排除 {len(symbols)} 只等于清空池子（共 {len(prices)} 只），拒绝执行")
    return {k: v for k, v in prices.items() if k not in set(symbols)}


def symbol_flip_flag(main_mean_d: float, control_mean_d: float) -> str | None:
    """§10.2 第三条：包含/排除 600809 两版 ``mean(d)`` 符号是否不一致。

    符号按 ``> 0`` 判定，与脚本里 S11 / S9 两处既有判定同一口径；
    因此 ``control`` 恰为 0 时视为「非正」，与主实验的正号构成翻转。
    """
    if (main_mean_d > 0) == (control_mean_d > 0):
        return None
    return (
        f"排除指定标的后 mean(d) 符号翻转（含 {main_mean_d:+.6f} vs 排除后 "
        f"{control_mean_d:+.6f}）→ 该股数据缺陷的影响无法隔离"
    )


# --------------------------------------------------------------------------- #
# 目标组合构造（只产权重，不碰撮合）
# --------------------------------------------------------------------------- #


def momentum_weighted_targets(
    scores: Mapping[str, float | None],
    *,
    top_fraction: float,
    min_assets: int,
) -> dict[str, float]:
    """按动量分数加权（S10）。

    负数分数不能用来做空——A 股无融券假设下负权重不可执行。故权重取
    ``max(score, 0)`` 归一；若全部 ≤ 0（整段下跌行情）则退化为等权，
    这与「选出最强的一批」的原意一致，不引入新的择时规则。

    排序确定性沿用 ``equal_weight_targets``：分数降序、并列按代码升序。
    """
    usable = {
        str(s): float(v)
        for s, v in scores.items()
        if v is not None and isinstance(v, (int, float)) and math.isfinite(float(v))
    }
    if len(usable) < min_assets:
        return {}

    ranked = sorted(usable.items(), key=lambda kv: (-kv[1], kv[0]))
    k = max(1, math.ceil(len(ranked) * top_fraction))
    picked = ranked[:k]

    raw = {s: max(v, 0.0) for s, v in picked}
    total = sum(raw.values())
    if total <= 0:
        w = 1.0 / k
        return {s: w for s, _ in picked}
    return {s: raw[s] / total for s, _ in picked}


def build_targets(
    scores_by_date: Mapping[pd.Timestamp, Mapping[str, float | None]],
    *,
    top_fraction: float,
    weighting: str,
) -> tuple[TargetPortfolio, ...]:
    if weighting == "equal":
        return build_rebalance_schedule(
            scores_by_date, top_fraction=top_fraction, min_assets=MIN_ASSETS
        )
    if weighting == "momentum":
        return tuple(
            TargetPortfolio(
                signal_date=pd.Timestamp(d),
                weights=momentum_weighted_targets(
                    scores_by_date[d], top_fraction=top_fraction, min_assets=MIN_ASSETS
                ),
            )
            for d in sorted(pd.Timestamp(x) for x in scores_by_date)
        )
    raise ValueError(f"未知 weighting {weighting!r}")


# --------------------------------------------------------------------------- #
# 回测与统计
# --------------------------------------------------------------------------- #


def make_portfolio_config(
    symbols: Sequence[str], fee_source: str
) -> PortfolioBacktestConfig:
    preset = get_preset(fee_source)
    fee_config = BacktestConfig(
        symbol="PORTFOLIO",               # 组合层不使用该字段，仅作模板
        initial_cash=INITIAL_CASH,
        commission_rate=preset.commission_rate,
        min_commission=preset.min_commission,
        stamp_duty_rate=preset.stamp_duty_rate,
        transfer_fee_rate=preset.transfer_fee_rate,
        slippage_buy=preset.slippage_buy,
        slippage_sell=preset.slippage_sell,
        fee_source=preset.fee_source,
        market_rules=a_share_rules(price_limit_rate=PORTFOLIO_PRICE_LIMIT_RATE),
    )
    return PortfolioBacktestConfig(
        symbols=tuple(symbols),
        initial_cash=INITIAL_CASH,
        fee_config=fee_config,
        allocation="weights",
        max_weight_per_asset=None,
    )


def monthly_returns(
    states: Sequence, *, start: str, end: str
) -> pd.Series:
    """由**连续净值**算月度收益。

    这是 R12 的落点：不能拿窗口级独立收益冒充账户收益。这里只用每天的总权益，
    月末取最后一个交易日的值，月收益 = 本月末 / 上月末 - 1。
    """
    rows = [(pd.Timestamp(s.date), s.total_equity) for s in states if s.total_equity is not None]
    if not rows:
        raise ValueError("净值序列为空")
    series = pd.Series(
        [v for _, v in rows], index=pd.DatetimeIndex([d for d, _ in rows])
    ).sort_index()
    if series.index.has_duplicates:
        raise ValueError("净值序列含重复日期")

    month_end = series.resample("ME").last().dropna()
    rets = month_end.pct_change().dropna()
    # 判定窗口之外的一律不参与统计（S12 正是靠这个截短）
    return rets[(rets.index >= pd.Timestamp(start)) & (rets.index <= pd.Timestamp(end))]


@dataclass
class RunResult:
    spec: ExperimentSpec
    strategy: object
    benchmark: object
    paired: pd.DataFrame
    stats: object
    elapsed: float = 0.0
    q_value: float | None = None
    # 评估窗口内累计收益（按 paired 的月度收益复利），与「全程累计」严格区分：
    # 全程累计来自 strategy.cumulative_return（BACKTEST_START..BACKTEST_END），
    # 不随 spec.eval_end 截断，直接拿来当「窗口内累计」是错的（旧 D2 缺陷）。
    strategy_total_return_eval: float | None = None
    benchmark_total_return_eval: float | None = None


def run_one(
    spec: ExperimentSpec,
    prices: Mapping[str, pd.DataFrame],
    *,
    calendar: pd.DatetimeIndex,
) -> RunResult:
    """跑一组实验：策略 + 同池等权基准，返回逐月配对明细。"""
    t0 = time.time()
    pool = filter_universe(prices, spec.universe)
    panel = CrossSectionPanel(
        pool, config=CrossSectionConfig(lookback=spec.lookback, skip=spec.skip), calendar=calendar
    )
    signal_dates = month_end_signal_dates(calendar)
    scores = panel.scores_by_date(signal_dates)

    strategy_targets = build_targets(scores, top_fraction=spec.top_fraction, weighting=spec.weighting)
    # 基准 = 同池等权：同一批分数，top_fraction=1.0 取全部合格标的
    benchmark_targets = build_targets(scores, top_fraction=1.0, weighting="equal")

    cfg = make_portfolio_config(sorted(pool), spec.fee_source)
    common = dict(start_date=pd.Timestamp(BACKTEST_START), end_date=pd.Timestamp(BACKTEST_END))
    strategy = run_portfolio_backtest(pool, strategy_targets, cfg, **common)
    benchmark = run_portfolio_backtest(pool, benchmark_targets, cfg, **common)

    rs = monthly_returns(strategy.states, start=OOS_START, end=spec.eval_end)
    rb = monthly_returns(benchmark.states, start=OOS_START, end=spec.eval_end)
    if not rs.index.equals(rb.index):
        raise RuntimeError(
            "策略与基准的月度索引不一致——两者本应共用同一日历，"
            f"差异 {sorted(set(rs.index) ^ set(rb.index))[:5]}"
        )
    paired = pd.DataFrame({
        "month": [str(d.date()) for d in rs.index],
        "r_strategy": rs.to_numpy(),
        "r_benchmark": rb.to_numpy(),
    })
    paired["d"] = paired["r_strategy"] - paired["r_benchmark"]

    return RunResult(
        spec=spec,
        strategy=strategy,
        benchmark=benchmark,
        paired=paired,
        stats=paired_stats(paired["d"].tolist()),
        elapsed=time.time() - t0,
        strategy_total_return_eval=float((1.0 + rs).prod() - 1.0),
        benchmark_total_return_eval=float((1.0 + rb).prod() - 1.0),
    )


# --------------------------------------------------------------------------- #
# 输入校验 / 演练（dry-run）
# --------------------------------------------------------------------------- #


def data_quality_report(prices: Mapping[str, pd.DataFrame]) -> dict[str, object]:
    """报告中必须提到的数据缺陷，在此逐项量化（不涉及任何收益）。"""
    nonpositive: dict[str, int] = {}
    spans = {}
    for code, df in prices.items():
        bad = int((df[["open", "high", "low", "close"]] <= 0).any(axis=1).sum())
        if bad:
            nonpositive[code] = bad
        spans[code] = (str(df["date"].min().date()), str(df["date"].max().date()))
    gem = [c for c in prices if c[:3] in GEM_PREFIXES]
    return {
        "n_symbols": len(prices),
        "date_span": (min(v[0] for v in spans.values()), max(v[1] for v in spans.values())),
        "nonpositive_rows": nonpositive,
        "n_nonpositive_symbols": len(nonpositive),
        "gem_symbols": sorted(gem),
        "n_gem": len(gem),
    }


def dry_run(
    prices: Mapping[str, pd.DataFrame],
    *,
    data_root: Path,
    excluded_symbols: Sequence[str] = (),
) -> dict[str, object]:
    """只做校验与调度演练：不调用 run_portfolio_backtest，不产生任何收益数字。

    ``excluded_symbols`` 非空时额外报告排除项与对照跑的代价；为空时**不新增任何
    键**，保证不带参数运行时的输出与加入该功能之前逐字节一致。
    """
    calendar = trading_calendar(prices)
    signal_dates = month_end_signal_dates(calendar)
    main = MAIN_SPEC
    panel = CrossSectionPanel(
        prices, config=CrossSectionConfig(lookback=main.lookback, skip=main.skip), calendar=calendar
    )
    scores = panel.scores_by_date(signal_dates)
    targets = build_targets(scores, top_fraction=main.top_fraction, weighting=main.weighting)
    bench_targets = build_targets(scores, top_fraction=1.0, weighting="equal")

    per_month = []
    for t in targets:
        n_eligible = sum(1 for v in scores[t.signal_date].values() if v is not None)
        per_month.append({
            "signal_date": str(t.signal_date.date()),
            "n_scores": n_eligible,
            "n_holdings": len(t.weights),
            "is_cash": len(t.weights) == 0,
        })

    # 执行日映射是否唯一（信号日挤到同一执行日会抛错，这里先探一遍）
    exec_dates = {}
    collisions = []
    for t in targets:
        pos = calendar.searchsorted(pd.Timestamp(t.signal_date), side="right")
        if pos >= len(calendar):
            collisions.append(f"{t.signal_date.date()}: 信号日之后无交易日")
            continue
        d = calendar[pos]
        if d in exec_dates:
            collisions.append(f"{t.signal_date.date()} 与 {exec_dates[d]} 挤到 {d.date()}")
        exec_dates[d] = t.signal_date

    holdings = [m["n_holdings"] for m in per_month if not m["is_cash"]]
    report = {
        "mode": "dry-run",
        "data_root": str(data_root),
        "prereg": {"version": PREREG_VERSION, "sha256_prefix": PREREG_SHA256},
        "data_quality": data_quality_report(prices),
        "calendar": {
            "n_trading_days": int(len(calendar)),
            "first": str(calendar[0].date()),
            "last": str(calendar[-1].date()),
            "n_signal_dates": len(signal_dates),
        },
        "main_schedule": {
            "n_targets": len(targets),
            "n_cash_months": sum(1 for m in per_month if m["is_cash"]),
            "n_holdings_min": min(holdings) if holdings else None,
            "n_holdings_max": max(holdings) if holdings else None,
            "n_holdings_median": statistics.median(holdings) if holdings else None,
            "exit_collisions": collisions,
            "first_invested": next((m["signal_date"] for m in per_month if not m["is_cash"]), None),
        },
        "benchmark_schedule": {
            "n_targets": len(bench_targets),
            "n_cash_months": sum(1 for t in bench_targets if not t.weights),
        },
        # 每组都演练一遍调度：某组若因历史长度不足而几乎全程空仓，
        # 必须在开跑前发现——跑完才看到「S3 只有 12 个月有持仓」就晚了。
        "per_experiment_schedule": [
            schedule_probe(spec, prices, calendar) for spec in all_specs()
        ],
        "estimated_backtests": 2 * len(all_specs()),
        "price_limit_note": (
            f"组合层单一涨跌停比例 = {PORTFOLIO_PRICE_LIMIT_RATE:.0%}（预登记 §6 为 board 模式，"
            f"per-symbol 无法实现）；池内 {len([c for c in prices if c[:3] in GEM_PREFIXES])} 只"
            "双创标的在 2020-08-24 后被偏严判定（悲观方向）"
        ),
        "will_compute_returns": False,
    }
    if excluded_symbols:
        # 仅在真排除时才出现：不带该参数时输出保持原样（有回归测试比对字节）
        report["excluded_symbols"] = list(excluded_symbols)
        report["n_symbols_after_exclusion"] = len(prices)
        report["estimated_backtests"] = 2 * (len(all_specs()) + 1)
        report["extra_control_run"] = (
            "真跑时额外执行 1 组对照（主实验口径 × 排除后的池子，策略+基准共 2 次回测），"
            "用于预登记 §10.2「600809.SH 数据缺陷影响无法隔离」的符号判定"
        )
    return report


def schedule_probe(
    spec: ExperimentSpec, prices: Mapping[str, pd.DataFrame], calendar: pd.DatetimeIndex
) -> dict[str, object]:
    """单组实验的调度演练：只算「会持有几只、空仓几个月」，不算收益。"""
    pool = filter_universe(prices, spec.universe)
    panel = CrossSectionPanel(
        pool, config=CrossSectionConfig(lookback=spec.lookback, skip=spec.skip), calendar=calendar
    )
    signal_dates = month_end_signal_dates(calendar)
    scores = panel.scores_by_date(signal_dates)
    targets = build_targets(scores, top_fraction=spec.top_fraction, weighting=spec.weighting)
    holdings = [len(t.weights) for t in targets if t.weights]
    oos_months = [t for t in targets if OOS_START <= str(t.signal_date.date()) <= spec.eval_end]
    oos_invested = sum(1 for t in oos_months if t.weights)
    return {
        "name": spec.name,
        "n_targets": len(targets),
        "n_cash_months": sum(1 for t in targets if not t.weights),
        "n_holdings_min": min(holdings) if holdings else None,
        "n_holdings_max": max(holdings) if holdings else None,
        "n_holdings_median": statistics.median(holdings) if holdings else None,
        "oos_months": len(oos_months),
        "oos_invested_months": oos_invested,
        "first_invested": next((str(t.signal_date.date()) for t in targets if t.weights), None),
    }


# --------------------------------------------------------------------------- #
# 落盘
# --------------------------------------------------------------------------- #


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_outputs(
    out_dir: Path,
    results: Sequence[RunResult],
    judgement,
    *,
    elapsed: float,
    excluded_symbols: Sequence[str] = (),
    control: RunResult | None = None,
    inputs: Mapping[str, object] | None = None,
) -> dict[str, object]:
    out_dir.mkdir(parents=True, exist_ok=True)

    main = results[0]
    main.paired.to_csv(out_dir / "monthly_paired.csv", index=False)
    if control is not None:
        # 对照跑的逐月明细单独落盘：符号判定要能被复核，不能只留一个结论字符串
        control.paired.to_csv(out_dir / "monthly_paired_excluded.csv", index=False)

    rows = []
    for r in results:
        rows.append({
            "name": r.spec.name,
            "variable": r.spec.variable,
            "value": r.spec.value,
            "n": r.stats.n,
            "mean_d": r.stats.mean_d,
            "std_d": r.stats.std_d,
            "t_stat": r.stats.t_stat,
            "p_value": r.stats.p_value,
            "win_ratio": r.stats.win_ratio,
            # 全程累计（BACKTEST_START..BACKTEST_END）——**不是**评估窗口内累计
            "strategy_total_return_full": r.strategy.cumulative_return,
            "benchmark_total_return_full": r.benchmark.cumulative_return,
            # 评估窗口内累计（OOS_START..spec.eval_end，按月度收益复利）
            "strategy_total_return_eval": getattr(r, "strategy_total_return_eval", None),
            "benchmark_total_return_eval": getattr(r, "benchmark_total_return_eval", None),
            "q_value": getattr(r, "q_value", None),
        })
    pd.DataFrame(rows).to_csv(out_dir / "sensitivity.csv", index=False)

    summary = {
        "prereg_version": PREREG_VERSION,
        "prereg_sha256_prefix": PREREG_SHA256,
        "main": main.stats.as_dict(),
        "judgement": judgement.as_dict(),
        "oos_window": [OOS_START, OOS_END],
        "n_months": main.stats.n,
        "price_limit_rate": PORTFOLIO_PRICE_LIMIT_RATE,
        "elapsed_seconds": round(elapsed, 1),
    }
    if excluded_symbols:
        summary["excluded_symbols"] = list(excluded_symbols)
    if control is not None:
        summary["control_excluded"] = {
            "excluded_symbols": list(excluded_symbols),
            "stats": control.stats.as_dict(),
            "note": (
                "预登记 §10.2 第三条：与 main 的 mean(d) 符号不一致即判证据不足。"
                "本对照必须在全池上独立重跑，不能拿「已剔除数据上的同一配置」充数。"
            ),
        }
    if inputs:
        summary["inputs"] = dict(inputs)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )

    manifest = {
        "files": {
            p.name: {"sha256": _sha256(p), "bytes": p.stat().st_size}
            for p in sorted(out_dir.iterdir()) if p.is_file()
        },
        "prereg_version": PREREG_VERSION,
        "price_limit_rate": PORTFOLIO_PRICE_LIMIT_RATE,
        "excluded_symbols": list(excluded_symbols),
    }
    if inputs:
        manifest["inputs"] = dict(inputs)
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    return summary


def _frame_digest(df: pd.DataFrame) -> bytes:
    """单只标的的内容摘要：列名 + 形状 + **逐值哈希**。

    只哈希「行数 + 首末日期」是常见的假防线：换个价格但保持形状与日期，
    摘要不变 —— 那就不再是「内容哈希」，而是「形状哈希」。
    """
    h = hashlib.sha256()
    h.update("|".join(str(c) for c in df.columns).encode())
    h.update(str(df.shape).encode())
    values = pd.util.hash_pandas_object(df, index=True).to_numpy(dtype="uint64")
    h.update(values.tobytes())
    return h.digest()


def pool_fingerprint(prices: Mapping[str, pd.DataFrame]) -> str:
    """池子输入指纹：标的集合 + 每只的**内容摘要**（逐值哈希）。

    用途是**证明输入真的不同**：对照组若与主实验共用同一份数据，指纹必然相同，
    这正是 D1 缺陷（排除跑内部对照退化）的判据。指纹相同却在比较「含/排除」，
    说明装置退化了，应当直接报错而不是继续算。
    """
    h = hashlib.sha256()
    for code in sorted(prices):
        h.update(code.encode())
        h.update(_frame_digest(prices[code]))
    return h.hexdigest()[:16]


def make_run_id(prices: Mapping[str, pd.DataFrame]) -> str:
    """内容哈希：同一份数据 + 同一份协议 → 同一个 run_id（可复现的前提）。

    2026-10-19 起覆盖**逐值内容**（缺陷 #9）：此前的实现只哈希标的集合、
    行数与首末日期，形状相同但价格不同的两份数据会得到同一个 run_id，
    进而共用同一个输出目录，或让 `ensure_no_overwrite` 误判「已存在」。
    """
    h = hashlib.sha256()
    h.update(PREREG_VERSION.encode())
    h.update(str(len(prices)).encode())
    for code in sorted(prices):
        h.update(code.encode())
        h.update(_frame_digest(prices[code]))
    return h.hexdigest()[:16]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

MIN_AUTHORIZATION_CHARS = 10


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Gate 1 横截面动量实验（预登记 gate1-prereg-v1.3）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--execute", action="store_true",
                   help="真正运行回测并产出研究结论。必须同时给 --authorized-by")
    p.add_argument("--authorized-by", default=None,
                   help="用户授权原话（不是角色名或占位符）")
    p.add_argument("--data-root", default=r"D:\DeepQuant power by txm\b50_v3",
                   help="50 只 *.qfq.csv 所在目录")
    p.add_argument("--out-root", default=None,
                   help="结果目录（默认 <repo>/gate1_out）")
    p.add_argument("--exclude-symbols", default=None,
                   help="从池中剔除的标的，逗号或空格分隔（如 \"600809.SH\"）。"
                        "真跑时会额外执行 1 组对照用于预登记 §10.2 第三条符号判定")
    return p.parse_args(argv)


def _check_authorization(execute: bool, authorized_by: str | None) -> None:
    """授权闸门。两个条件缺一不可，缺哪个说哪个。"""
    if not execute:
        return
    if not authorized_by or not authorized_by.strip():
        raise SystemExit(
            "拒绝执行：缺少 --authorized-by。\n"
            "Gate 1 第二阶段（真跑主实验 + 12 组敏感性）需用户明确授权。\n"
            "用法：--execute --authorized-by \"<用户授权原话>\""
        )
    text = authorized_by.strip()
    placeholders = {"todo", "tbd", "xxx", "test", "placeholder", "待定"}
    if text.lower() in placeholders or len(text) < MIN_AUTHORIZATION_CHARS:
        raise SystemExit(
            f"拒绝执行：--authorized-by 至少 {MIN_AUTHORIZATION_CHARS} 个字符，且不能是占位符。\n"
            f"收到：{text!r}\n"
            "这一项要求记录用户授权原话，用于审计「协议先于结果」；"
            "填 role 名或 TODO 等于没有授权。"
        )


def ensure_no_overwrite(out_dir: Path) -> None:
    """拒绝覆盖已有结果。

    预登记 §12.5 把「同一配置两次运行必须一致」列为可复现性证据；覆盖会让
    第一次的结果消失，那条证据就永远无法复核。
    """
    if (out_dir / "summary.json").exists():
        raise SystemExit(
            f"拒绝覆盖：{out_dir} 已有结果。\n"
            "预登记 §12.5 要求同一配置两次运行必须一致——覆盖会毁掉这个证据。"
            "如确需重跑，请换 --out-root 或先人工归档。"
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    # 授权校验放在最前面：拒绝时连数据都不读，也不会碰磁盘
    _check_authorization(args.execute, args.authorized_by)

    data_root = Path(args.data_root)
    prices = load_prices(data_root)
    prices_full = prices  # 全池原件：排除跑的对照组必须回到这份数据上独立重跑
    print(f"[LOAD] {len(prices)} 只标的 from {data_root}")

    excluded = parse_exclude_symbols(args.exclude_symbols)
    if excluded:
        prices = apply_exclusion(prices, excluded)
        print(f"[EXCLUDE] 剔除 {list(excluded)} → 剩 {len(prices)} 只")

    if not args.execute:
        report = dry_run(prices, data_root=data_root, excluded_symbols=excluded)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        print("\n[DRY-RUN] 未计算任何收益。真跑需 --execute 且 --authorized-by \"<用户授权原话>\"。")
        return 0

    out_root = Path(args.out_root) if args.out_root else Path(__file__).resolve().parents[1] / "gate1_out"
    run_id = make_run_id(prices)
    out_dir = out_root / run_id
    ensure_no_overwrite(out_dir)

    print(f"[EXECUTE] authorized_by={args.authorized_by!r} run_id={run_id}")
    calendar = trading_calendar(prices)

    t0 = time.time()
    results: list[RunResult] = []
    for spec in all_specs():
        r = run_one(spec, prices, calendar=calendar)
        results.append(r)
        print(
            f"  [{spec.name:4s}] n={r.stats.n:3d} mean_d={r.stats.mean_d:+.6f} "
            f"t={r.stats.t_stat:+.3f} win={r.stats.win_ratio:.1%} ({r.elapsed:.1f}s)"
        )

    # 敏感性的 q 值挂回去（顺序与输入一致）
    sens = [
        SensitivityOutcome(r.spec.name, r.spec.variable, r.spec.value, r.stats)
        for r in results[1:]
    ]
    with_q = attach_bh(sens)
    for r, s in zip(results[1:], with_q):
        r.q_value = s.q_value

    n_positive = sum(1 for r in results[1:] if r.stats.mean_d > 0)
    if len(results) != N_SENSITIVITY + 1:
        raise RuntimeError(f"实验组数应为 {N_SENSITIVITY}+1，实际 {len(results)}")

    # 预登记 10.2 末三条的自动判定（前两条在此，第三条需 --exclude-symbols 的对照跑）
    flags: list[str] = []
    s11 = next(r for r in results if r.spec.name == "S11")
    if (results[0].stats.mean_d > 0) != (s11.stats.mean_d > 0):
        flags.append(
            f"排除创业板/科创板后 mean(d) 符号翻转（主 {results[0].stats.mean_d:+.6f} "
            f"vs S11 {s11.stats.mean_d:+.6f}）→ 无法实现的涨跌停约束可能有实质影响"
        )
    s9 = next(r for r in results if r.spec.name == "S9")
    if results[0].stats.mean_d > 0 >= s9.stats.mean_d:
        flags.append(
            f"成本提到 high_cost 后 mean(d) 由正转负（{results[0].stats.mean_d:+.6f} → "
            f"{s9.stats.mean_d:+.6f}）"
        )

    # §10.2 第三条：包含/排除该股两版结果符号不一致 → 数据缺陷影响无法隔离。
    # 只有传了 --exclude-symbols 才有对照；没传就不产生这条 flag（也不能凭空判「无法隔离」）。
    #
    # 关键：对照组必须回到**全池**上独立重跑。此前实现直接复用已被剔除的 prices，
    # 等于在同一份数据上重跑同一配置 —— 两个 CSV 的 sha256 完全相同（D1 缺陷）。
    control: RunResult | None = None
    inputs: dict[str, object] = {
        "main_pool": {
            "n_symbols": len(prices),
            "fingerprint": pool_fingerprint(prices),
        },
    }
    if excluded:
        print(f"\n[CONTROL] 在全池（{len(prices_full)} 只）上独立重跑主实验口径……")
        control = run_one(MAIN_SPEC, prices_full, calendar=calendar)
        inputs["control_pool"] = {
            "n_symbols": len(prices_full),
            "fingerprint": pool_fingerprint(prices_full),
        }
        same_input = inputs["main_pool"]["fingerprint"] == inputs["control_pool"]["fingerprint"]
        if same_input:
            raise RuntimeError(
                "对照组与主实验的输入指纹相同：对照已退化为「同一份数据重跑」，"
                "不能用于 §10.2 第三条的符号比较。请检查 apply_exclusion 是否真的生效。"
            )
        print(
            f"  [ctrl] n={control.stats.n:3d} mean_d={control.stats.mean_d:+.6f} "
            f"t={control.stats.t_stat:+.3f} ({control.elapsed:.1f}s)"
        )
        flip = symbol_flip_flag(results[0].stats.mean_d, control.stats.mean_d)
        if flip:
            flags.append(flip)

    verdict = judge(results[0].stats, n_positive_sensitivity=n_positive, insufficient_flags=flags)
    elapsed = time.time() - t0
    summary = write_outputs(
        out_dir, results, verdict, elapsed=elapsed,
        excluded_symbols=excluded, control=control, inputs=inputs,
    )

    print(f"\n[JUDGE] {verdict.label}  rule_hits={list(verdict.rule_hits)}")
    if verdict.insufficient_flags:
        print(f"        insufficient_flags={list(verdict.insufficient_flags)}")
    print(f"[SAVED] {out_dir}")
    print(json.dumps(summary, indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
