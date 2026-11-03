"""组合级 L3（L3p）：多标的、共享现金池的 Walk-Forward。

为什么要有这一级
----------------
单标的 L3（:mod:`deepquant.gates.l3`）已暴露一个已登记限制：**27 折并不独立**，
因为只有一条价格路径。组合化不能消除时间序列上的共同因子（同一段历史的不同切片
永远是相关的），但它能把**横截面特异噪声**平均掉——这正是要测的东西：
单标的的"折间不一致"到底是策略本身的性质，还是单只股票的运气。

三条口径，每一条都排除了替代做法
--------------------------------
1. **共享现金池，不是"把各标的收益平均"**。各标的收益取平均等于假设资金可以无限
   分摊、且没有整手与现金约束，这是项目已经踩过的坑（`weight_mae` 抓不住现金残留）。
   所以直接用 ``run_portfolio_backtest``：单一现金账户、同一执行日先卖后买、连续净值。
2. **事件信号 → 状态重建**。``MaCrossStrategy`` 只在交叉当天给信号，月末调仓时
   几乎必然"当天没交叉"⇒ 选不出票。这里把事件序列按「持有到反向信号为止」重建为
   持仓状态——这与单标的引擎的行为**完全一致**（引擎同样持有到反向信号），
   不是换了一个策略，只是换了读法。
3. **每折在窗口首日补一次建仓信号**。月度调仓意味着折首最多有 29 天处于空仓，
   这段现金拖累会把折收益系统性压低，且与单标的 L3 不可比。补一个首日信号，
   让组合从折的第二天起就有持仓。

判据复用
--------
判定链**不重写**：:func:`deepquant.gates.l3.decide_from_summary` 只吃折级汇总，
L3 与 L3p 共用同一套阈值与顺序。各写一套必然漂移，最后变成"哪个宽松引用哪个"。
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.portfolio import (
    PortfolioBacktestConfig,
    PortfolioBacktestResult,
    TargetPortfolio,
    run_portfolio_backtest,
)
from deepquant.gates.l3 import (
    VERDICT_INCONCLUSIVE,
    VERDICT_SPEC,
    Fold,
    L3Result,
    WalkForwardSpec,
    build_folds,
    decide_from_summary,
    overlap_report,
    param_stats,
    parse_walk_forward,
    summarise_folds,
)
from deepquant.gates.spec import HypothesisSpec, SpecError, parse_spec
from deepquant.gates.sweep import default_strategy_factory

# --------------------------------------------------------------------------- #
# 终态：与 L3 一一对应，只换前缀（判据同源，编号可分）
# --------------------------------------------------------------------------- #
VERDICT_PREFIX = "L3P_"


def to_portfolio_verdict(verdict: str) -> str:
    """``L3_XXX`` → ``L3P_XXX``。前缀不同是为了让报告能一眼看出是哪一级的结论。"""
    if verdict.startswith("L3_"):
        return VERDICT_PREFIX + verdict[len("L3_") :]
    return verdict


ALL_VERDICTS: tuple[str, ...] = (
    "L3P_PASSED",
    "L3P_REJECTED_SYSTEMATIC_DIVERGENCE",
    "L3P_REJECTED_INCONSISTENT_FOLDS",
    "L3P_REJECTED_WINDOW",
    "L3P_REJECTED_DATA",
    "L3P_INCONCLUSIVE",
    "L3P_REJECTED_SPEC",
    "L3P_BLOCKED_PREREQUISITE",
)


class PortfolioGateError(ValueError):
    """组合闸门的输入不自洽。"""


# --------------------------------------------------------------------------- #
# 一、组合口径
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class PortfolioSpec:
    """组合层的执行口径。全部是模型参数，不是实测标定值。"""

    #: 初始资金。``allocation="weights"`` 时收益与它无关（不满足整手，只按权重记账），
    #: 保留它是为了让报告里的金额可读，并且这个"无关"本身有测试守着。
    initial_cash: float = 10_000_000.0
    #: weights（主口径，不受整手约束）| lots（整手约束，用于检验可执行性）。
    allocation: str = "weights"
    #: 单标的权重上限。等权下只有在持仓不足 5 只时才会触发。
    max_weight_per_asset: float = 0.20
    #: 调仓频率：按月最后一个交易日，外加每折窗口首个交易日。
    rebalance: str = "monthly"

    def __post_init__(self) -> None:
        if self.initial_cash <= 0 or not np.isfinite(self.initial_cash):
            raise PortfolioGateError(f"initial_cash 必须为有限正数，收到 {self.initial_cash}")
        if self.allocation not in ("weights", "lots"):
            raise PortfolioGateError(f"allocation 必须是 weights / lots，收到 {self.allocation!r}")
        if not 0 < self.max_weight_per_asset <= 1:
            raise PortfolioGateError(
                f"max_weight_per_asset 必须在 (0,1]，收到 {self.max_weight_per_asset}"
            )
        if self.rebalance != "monthly":
            raise PortfolioGateError(f"目前只支持 monthly 调仓，收到 {self.rebalance!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_cash": self.initial_cash,
            "allocation": self.allocation,
            "max_weight_per_asset": self.max_weight_per_asset,
            "rebalance": self.rebalance,
        }


def parse_portfolio_spec(payload: Mapping[str, Any] | None) -> PortfolioSpec:
    if payload is None:
        return PortfolioSpec()
    if not isinstance(payload, Mapping):
        raise PortfolioGateError("portfolio 段必须是对象")
    known = set(PortfolioSpec().to_dict())
    unknown = sorted(set(payload) - known)
    if unknown:
        # 拼错的键被静默忽略 ⇒ 阈值看起来生效其实没生效，直接拒绝
        raise PortfolioGateError(f"portfolio 段含未知键：{unknown}")
    kwargs = dict(payload)
    if "initial_cash" in kwargs:
        kwargs["initial_cash"] = float(kwargs["initial_cash"])
    if "max_weight_per_asset" in kwargs:
        kwargs["max_weight_per_asset"] = float(kwargs["max_weight_per_asset"])
    return PortfolioSpec(**kwargs)


# --------------------------------------------------------------------------- #
# 二、标的池
# --------------------------------------------------------------------------- #
@dataclass
class Universe:
    """一份冻结的标的池与其实际可用情况。"""

    universe_id: str
    frames: dict[str, pd.DataFrame]
    constituents: list[str]
    dropped: list[dict[str, Any]] = field(default_factory=list)
    source: str = ""
    rows_in_span: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "universe_id": self.universe_id,
            "source": self.source,
            "size": len(self.constituents),
            "constituents": list(self.constituents),
            "dropped": self.dropped,
            "rows_in_span": dict(sorted(self.rows_in_span.items())),
        }


def load_universe(
    spec_path: str | Path,
    *,
    data_dir: str | Path,
    span_start: str,
    span_end: str,
    min_rows_in_span: int = 250,
) -> Universe:
    """按冻结文件加载标的池。

    冻结文件里可能含本轮数据已不可用的标的（数据被清理、代码变更）：**如实剔除并记账**，
    不静默缩池——否则"30 只"这个数字会在无人察觉的情况下变成 27 只。
    """
    path = Path(spec_path)
    if not path.exists():
        raise PortfolioGateError(f"标的池文件不存在：{path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("constituents") or []
    if not entries:
        raise PortfolioGateError(f"标的池为空：{path}")

    qfq_dir = Path(data_dir)
    start = pd.Timestamp(span_start)
    end = pd.Timestamp(span_end)
    frames: dict[str, pd.DataFrame] = {}
    dropped: list[dict[str, Any]] = []
    rows: dict[str, int] = {}

    for entry in entries:
        code = str(entry["symbol"]) if isinstance(entry, Mapping) else str(entry)
        file = qfq_dir / f"{code}.qfq.csv"
        if not file.exists():
            dropped.append({"symbol": code, "reason": "缺少 qfq 文件"})
            continue
        frame = pd.read_csv(file, parse_dates=["date"]).set_index("date")
        frame = frame.loc[(frame.index >= start) & (frame.index <= end)]
        needed = {"open", "high", "low", "close"}
        missing = needed - set(frame.columns)
        if missing:
            dropped.append({"symbol": code, "reason": f"缺少列 {sorted(missing)}"})
            continue
        if len(frame) < min_rows_in_span:
            dropped.append({"symbol": code, "reason": f"跨度内仅 {len(frame)} 行"})
            continue
        frames[code] = frame
        rows[code] = len(frame)

    if not frames:
        raise PortfolioGateError("标的池在所有标的上都不可用")

    return Universe(
        universe_id=str(payload.get("universe_id") or path.stem),
        frames=frames,
        constituents=sorted(frames),
        dropped=dropped,
        source=str(path),
        rows_in_span=rows,
    )


# --------------------------------------------------------------------------- #
# 三、事件信号 → 持仓状态
# --------------------------------------------------------------------------- #
def state_from_events(signals: pd.Series) -> pd.Series:
    """把「只在交叉日出现」的事件序列重建为持仓状态（1=持有，0=空仓）。

    与单标的引擎的行为一致：引擎在收到 BUY 后持有，直到收到 SELL 才平仓。
    所以这不是换策略，是把同一串事件按同一个语义读成状态。
    """
    if not isinstance(signals, pd.Series):
        raise PortfolioGateError(f"信号必须是 Series，收到 {type(signals)!r}")
    nonzero = signals.where(signals != 0).ffill().fillna(0)
    return (nonzero > 0).astype("int8")


def state_at(states: pd.Series, day: pd.Timestamp) -> int:
    """取 ``day``（含）当日及之前最后一个状态值。无历史时视为空仓。"""
    position = int(states.index.searchsorted(day, side="right")) - 1
    if position < 0:
        return 0
    return int(states.iloc[position])


def month_end_dates(calendar: pd.DatetimeIndex) -> pd.DatetimeIndex:
    """每个自然月最后一个交易日。"""
    if len(calendar) == 0:
        return calendar
    by_month = pd.Series(calendar, index=calendar).groupby(calendar.to_period("M")).max()
    return pd.DatetimeIndex(sorted(by_month.values))


def schedule_from_states(
    states: Mapping[str, pd.Series],
    signal_dates: Sequence[pd.Timestamp],
    *,
    max_weight_per_asset: float | None = None,
) -> tuple[TargetPortfolio, ...]:
    """按信号日的持仓状态生成等权目标权重。

    「一个都不持有」时返回空权重（全现金）——这是合法状态，不是错误。
    """
    schedule: list[TargetPortfolio] = []
    for day in signal_dates:
        held = [symbol for symbol, series in states.items() if state_at(series, day) == 1]
        if not held:
            schedule.append(TargetPortfolio(signal_date=pd.Timestamp(day), weights={}))
            continue
        weight = 1.0 / len(held)
        if max_weight_per_asset is not None:
            weight = min(weight, max_weight_per_asset)
        schedule.append(
            TargetPortfolio(signal_date=pd.Timestamp(day), weights={symbol: weight for symbol in held})
        )
    return tuple(schedule)


def control_schedule(
    symbols: Sequence[str], signal_dates: Sequence[pd.Timestamp]
) -> tuple[TargetPortfolio, ...]:
    """对照臂：每期等权持有**全部**标的（不做任何选择）。

    这是同池同期的被动基准。没有它，组合收益的高低无法区分"信号有效"与"这批股票在涨"。
    """
    if not symbols:
        return ()
    weight = 1.0 / len(symbols)
    return tuple(
        TargetPortfolio(signal_date=pd.Timestamp(day), weights={s: weight for s in symbols})
        for day in signal_dates
    )


# --------------------------------------------------------------------------- #
# 四、折
# --------------------------------------------------------------------------- #
@dataclass
class PortfolioFold(Fold):
    """一折的组合执行结果。组合层不产生"平仓交易"，因此用成交笔数与持仓数代替。"""

    fills: int = 0
    avg_positions: float | None = None
    min_positions: int | None = None
    max_positions: int | None = None
    cash_only_periods: int = 0
    avg_cash_ratio: float | None = None
    #: 对照臂（每期等权持有全部标的）在同一测试窗的收益。
    control_return: float | None = None
    #: 对照臂的平均现金占比。必须与策略臂并列：两臂的现金拖累不同，
    #: 只比收益会把「稀释程度不同」误读成「选择有没有用」。
    control_cash_ratio: float | None = None
    #: 组合相对对照的超额。两条臂同池同期，差就是"选择"的贡献。
    excess_vs_control: float | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload.update(
            {
                "fills": self.fills,
                "avg_positions": self.avg_positions,
                "min_positions": self.min_positions,
                "max_positions": self.max_positions,
                "cash_only_periods": self.cash_only_periods,
                "avg_cash_ratio": self.avg_cash_ratio,
                "control_return": self.control_return,
                "control_cash_ratio": self.control_cash_ratio,
                "excess_vs_control": self.excess_vs_control,
            }
        )
        return payload


@dataclass
class PortfolioGateResult(L3Result):
    """L3p 结果：在 L3 结果之上补标的池与对照臂的汇总。"""

    universe: dict[str, Any] = field(default_factory=dict)
    control: dict[str, Any] = field(default_factory=dict)
    portfolio: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = super().to_dict()
        payload.update(
            {
                "universe": self.universe,
                "control": self.control,
                "portfolio": self.portfolio,
            }
        )
        return payload


# --------------------------------------------------------------------------- #
# 五、执行
# --------------------------------------------------------------------------- #
def _signal_dates_for(
    calendar: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp
) -> list[pd.Timestamp]:
    """窗口内的调仓信号日：窗口首日 + 每月最后交易日（且必须早于窗口末日）。

    末日不能作为信号日：执行在**次日**开盘，落在窗口外的信号永远不会被执行，
    只会让调度看起来比实际多一次调仓。
    """
    window = calendar[(calendar >= start) & (calendar <= end)]
    if len(window) == 0:
        return []
    dates = [window[0], *month_end_dates(window).tolist()]
    unique = sorted({pd.Timestamp(d) for d in dates})
    return [d for d in unique if d < window[-1]]


def _run_portfolio(
    frames: Mapping[str, pd.DataFrame],
    schedule: Sequence[TargetPortfolio],
    config: PortfolioBacktestConfig,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> PortfolioBacktestResult:
    return run_portfolio_backtest(
        frames, schedule, config, start_date=start, end_date=end
    )


def _cash_ratio_average(result: PortfolioBacktestResult) -> float | None:
    ratios = [
        state.cash / state.total_equity
        for state in result.states
        if state.total_equity is not None and state.total_equity > 0
    ]
    return statistics.fmean(ratios) if ratios else None


def run_l3_portfolio(
    payload: HypothesisSpec | Mapping[str, Any],
    *,
    universe: Universe,
    walk_forward: WalkForwardSpec | Mapping[str, Any] | None = None,
    portfolio: PortfolioSpec | Mapping[str, Any] | None = None,
    prior_verdicts: Mapping[str, str] | None = None,
    with_control: bool = True,
) -> PortfolioGateResult:
    """对一份契约做组合级 Walk-Forward。

    ``payload`` 与 L3 同构（L1a 契约 + ``walk_forward`` 段 + ``portfolio`` 段），
    差别只在"一个 fold 的收益"由组合账户而不是单标的账户产出。
    """
    result = PortfolioGateResult(hypothesis_id="", verdict=to_portfolio_verdict(VERDICT_SPEC))

    # ---- 0. 契约 ----
    try:
        if isinstance(payload, HypothesisSpec):
            spec = payload
            raw_wf = spec.walk_forward
            raw_pf = spec.portfolio
        elif isinstance(payload, Mapping):
            raw_wf = payload.get("walk_forward")
            raw_pf = payload.get("portfolio")
            spec = parse_spec(payload)
        else:
            raise SpecError(f"契约必须是 HypothesisSpec 或字典，收到 {type(payload)!r}")
        wf_spec = (
            walk_forward
            if isinstance(walk_forward, WalkForwardSpec)
            else parse_walk_forward(walk_forward if walk_forward is not None else raw_wf)
        )
        pf_spec = (
            portfolio
            if isinstance(portfolio, PortfolioSpec)
            else parse_portfolio_spec(portfolio if portfolio is not None else raw_pf)
        )
    except (SpecError, PortfolioGateError, ValueError) as exc:
        result.stages.append({"name": "spec", "status": "fail", "detail": str(exc)})
        result.limitations.append(f"契约不成立：{exc}")
        return result

    result.hypothesis_id = spec.hypothesis_id
    result.spec = {
        "hypothesis": spec.to_dict(),
        "walk_forward": wf_spec.to_dict(),
        "digest": wf_spec.digest,
        "portfolio": pf_spec.to_dict(),
        "universe": universe.to_dict(),
    }
    result.stages.append(
        {
            "name": "spec",
            "status": "pass",
            "detail": (
                f"切窗规则 {wf_spec.digest[:16]}…；口径 {pf_spec.allocation}；"
                f"标的池 {universe.universe_id}（{len(universe.constituents)} 只）"
            ),
        }
    )

    # ---- 1. 组合日历 ----
    all_days = sorted(set().union(*(set(frame.index) for frame in universe.frames.values())))
    calendar = pd.DatetimeIndex(all_days)
    span_start = pd.Timestamp(wf_spec.span_start or spec.in_sample.start)
    span_end = pd.Timestamp(wf_spec.span_end or spec.out_sample.end)
    calendar = calendar[(calendar >= span_start) & (calendar <= span_end)]
    if len(calendar) < wf_spec.train_days + wf_spec.test_days:
        result.stages.append(
            {
                "name": "data",
                "status": "fail",
                "detail": f"跨度内仅 {len(calendar)} 个交易日，不足以切出任何完整折",
            }
        )
        result.limitations.append("跨度内交易日不足，未做任何评估")
        result.verdict = to_portfolio_verdict("L3_REJECTED_WINDOW")
        return result

    result.window_meta = {
        "trading_days_total": len(calendar),
        "first_day": str(calendar[0].date()),
        "last_day": str(calendar[-1].date()),
        "declared_span": {"start": str(span_start.date()), "end": str(span_end.date())},
        "train_days": wf_spec.train_days,
        "test_days": wf_spec.test_days,
        "step_days": wf_spec.step_days,
        "mode": wf_spec.mode,
        "warmup_days": 0,
        "universe_size": len(universe.constituents),
    }
    result.universe = universe.to_dict()

    folds, meta = build_folds(calendar, wf_spec)
    result.window_meta.update(
        {
            "folds": meta["folds"],
            "unused_tail_days": meta["unused_tail_days"],
            "span_start": meta["span_start"],
            "span_end": meta["span_end"],
        }
    )
    result.overlap = overlap_report(folds)
    if not folds:
        result.stages.append(
            {"name": "windows", "status": "fail", "detail": f"按 train={wf_spec.train_days}/test={wf_spec.test_days} 切不出完整折"}
        )
        result.limitations.append("窗口不足：无法切出完整折，未做任何评估")
        result.verdict = to_portfolio_verdict("L3_REJECTED_WINDOW")
        return result
    result.stages.append(
        {
            "name": "windows",
            "status": "pass" if result.overlap["independent"] else "undetermined",
            "detail": (
                f"{len(folds)} 折，{meta['span_start']} → {meta['span_end']}；"
                f"测试窗{'互不重叠' if result.overlap['independent'] else '存在重叠'}"
            ),
        }
    )

    # ---- 2. 预计算状态（信号只依赖价格历史，可跨折复用）----
    grid = spec.grid_points()
    if not grid:
        result.stages.append({"name": "grid", "status": "fail", "detail": "参数网格为空"})
        result.verdict = to_portfolio_verdict(VERDICT_SPEC)
        return result
    states_by_params: dict[str, dict[str, pd.Series]] = {}
    for params in grid:
        key = json.dumps(params, sort_keys=True)
        per_symbol: dict[str, pd.Series] = {}
        for symbol, frame in universe.frames.items():
            try:
                strategy = default_strategy_factory(spec.strategy_id, params)
            except Exception as exc:  # noqa: BLE001 - 参数组合非法属预期情形
                result.stages.append(
                    {"name": "grid", "status": "fail", "detail": f"参数 {params} 构造策略失败：{exc}"}
                )
                result.verdict = to_portfolio_verdict(VERDICT_SPEC)
                return result
            per_symbol[symbol] = state_from_events(strategy.generate_signals(frame))
        states_by_params[key] = per_symbol
    result.stages.append(
        {"name": "signals", "status": "pass", "detail": f"{len(grid)} 组参数 × {len(universe.frames)} 只标的的状态已重建"}
    )

    base_config = BacktestConfig(
        symbol=universe.universe_id,
        initial_cash=pf_spec.initial_cash,
        slippage_buy=0.0,
        slippage_sell=0.0,
    )
    portfolio_config = PortfolioBacktestConfig(
        symbols=tuple(universe.constituents),
        initial_cash=pf_spec.initial_cash,
        fee_config=base_config,
        allocation=pf_spec.allocation,
        max_weight_per_asset=pf_spec.max_weight_per_asset,
    )

    # ---- 3. 逐折 ----
    for fold in folds:
        record = PortfolioFold(
            index=fold.index,
            train_start=fold.train_start,
            train_end=fold.train_end,
            test_start=fold.test_start,
            test_end=fold.test_end,
            train_days=fold.train_days,
            test_days=fold.test_days,
        )
        train_dates = _signal_dates_for(calendar, fold.train_start, fold.train_end)
        test_dates = _signal_dates_for(calendar, fold.test_start, fold.test_end)

        best: tuple[dict[str, Any], float] | None = None
        failures: list[str] = []
        for params in grid:
            key = json.dumps(params, sort_keys=True)
            schedule = schedule_from_states(
                states_by_params[key], train_dates, max_weight_per_asset=pf_spec.max_weight_per_asset
            )
            try:
                outcome = _run_portfolio(
                    universe.frames, schedule, portfolio_config, fold.train_start, fold.train_end
                )
            except (ValueError, RuntimeError) as exc:
                failures.append(f"{key}: {type(exc).__name__}: {exc}")
                continue
            value = outcome.cumulative_return
            if value is None:
                failures.append(f"{key}: 训练窗收益不可计算")
                continue
            if best is None or value > best[1]:
                best = (dict(params), float(value))

        if best is None:
            record.error = "训练窗内没有任何参数点产出可用收益；" + "；".join(failures[:3])
            result.folds.append(record)
            continue

        record.best_params = best[0]
        record.train_return = best[1]

        key = json.dumps(best[0], sort_keys=True)
        test_schedule = schedule_from_states(
            states_by_params[key], test_dates, max_weight_per_asset=pf_spec.max_weight_per_asset
        )
        record.cash_only_periods = sum(1 for item in test_schedule if item.is_cash_only)
        counts = [len(item.weights) for item in test_schedule if not item.is_cash_only]
        record.avg_positions = statistics.fmean(counts) if counts else 0.0
        record.min_positions = min(counts) if counts else 0
        record.max_positions = max(counts) if counts else 0

        try:
            test_outcome = _run_portfolio(
                universe.frames, test_schedule, portfolio_config, fold.test_start, fold.test_end
            )
        except (ValueError, RuntimeError) as exc:
            record.error = f"测试窗评估失败：{type(exc).__name__}: {exc}"
            result.folds.append(record)
            continue

        record.test_return = test_outcome.cumulative_return
        record.fills = len(test_outcome.fills)
        record.avg_cash_ratio = _cash_ratio_average(test_outcome)
        perf = test_outcome.performance
        record.test_max_drawdown = (
            getattr(getattr(perf, "stats", None), "max_drawdown_ratio", None)
            if perf is not None
            else None
        )

        if record.test_return is not None and record.train_return and record.train_return > 0:
            record.wfe = record.test_return / record.train_return
        elif record.train_return is not None and record.train_return <= 0:
            record.wfe = None
            record.wfe_reason = f"训练收益 {record.train_return:+.4f} 非正，测试/训练之比没有经济含义"
        else:
            record.wfe = None
            record.wfe_reason = "训练或测试收益不可计算"

        if with_control:
            control = control_schedule(universe.constituents, test_dates)
            try:
                control_outcome = _run_portfolio(
                    universe.frames, control, portfolio_config, fold.test_start, fold.test_end
                )
                record.control_return = control_outcome.cumulative_return
                record.control_cash_ratio = _cash_ratio_average(control_outcome)
            except (ValueError, RuntimeError) as exc:
                record.control_return = None
                record.error = (record.error + "；" if record.error else "") + (
                    f"对照臂失败：{type(exc).__name__}: {exc}"
                )
            if record.control_return is not None and record.test_return is not None:
                record.excess_vs_control = record.test_return - record.control_return

        result.folds.append(record)

    usable = [fold for fold in result.folds if fold.ok]
    failed = [fold for fold in result.folds if not fold.ok]
    result.summary = summarise_folds(result.folds, usable, wf_spec)
    result.param_selection = param_stats(usable)
    result.portfolio = _portfolio_summary(usable)
    result.control = _control_summary(usable)

    verdict, checks = decide_from_summary(
        result.summary,
        result.overlap,
        wf_spec,
        usable_folds=len(usable),
        failed_folds=len(failed),
    )
    result.verdict = to_portfolio_verdict(verdict)
    result.checks = checks
    result.stages.append(
        {
            "name": "decide",
            "status": "pass" if result.verdict == "L3P_PASSED" else "fail",
            "detail": "；".join(
                f"{check['name']}={'PASS' if check['passed'] else ('不可判定' if check['passed'] is None else 'FAIL')}"
                for check in checks
            ),
        }
    )

    blocked = {k: v for k, v in (prior_verdicts or {}).items() if v and not str(v).endswith("_PASSED")}
    if blocked:
        result.preliminary_verdict = result.verdict
        result.verdict = to_portfolio_verdict("L3_BLOCKED_PREREQUISITE")
        detail = "、".join(f"{k}={v}" for k, v in sorted(blocked.items()))
        result.stages.append({"name": "prerequisite", "status": "fail", "detail": detail})
        result.limitations.append(
            f"前置未满足：{detail}。本次组合级 Walk-Forward 仅用于量化折间一致性，**不构成放行结论**。"
        )
        result.limitations.append(
            f"若**不计**前置，本闸门判定链给出的终态是 `{result.preliminary_verdict}`"
            "（同样不构成放行依据，只用于说明本闸门自身的判断）。"
        )

    result.limitations.extend(_standard_limitations(wf_spec, pf_spec, result, len(usable)))
    if failed:
        result.notes.append(
            f"{len(failed)} 折未能完成：" + "；".join(f"#{fold.index} {fold.error}" for fold in failed)
        )
    if universe.dropped:
        result.notes.append(
            f"标的池剔除 {len(universe.dropped)} 只："
            + "；".join(f"{item['symbol']}（{item['reason']}）" for item in universe.dropped)
        )
    return result


def _portfolio_summary(usable: Sequence[PortfolioFold]) -> dict[str, Any]:
    positions = [fold.avg_positions for fold in usable if fold.avg_positions is not None]
    cash = [fold.avg_cash_ratio for fold in usable if fold.avg_cash_ratio is not None]
    cash_only = [fold.cash_only_periods for fold in usable]
    return {
        "folds_with_positions": sum(1 for fold in usable if (fold.avg_positions or 0) > 0),
        "avg_positions_median": statistics.median(positions) if positions else None,
        "avg_positions_min": min(positions) if positions else None,
        "avg_positions_max": max(positions) if positions else None,
        "avg_cash_ratio_median": statistics.median(cash) if cash else None,
        "cash_only_periods_total": sum(cash_only) if cash_only else 0,
        "allocation": "weights",
        "note": "avg_cash_ratio 是测试窗内逐日 cash/total_equity 的均值；"
        "它是「未投资比例」的代理，单看权重误差抓不到现金残留。",
    }


def _control_summary(usable: Sequence[PortfolioFold]) -> dict[str, Any]:
    pairs = [
        (fold.test_return, fold.control_return)
        for fold in usable
        if fold.test_return is not None and fold.control_return is not None
    ]
    if not pairs:
        return {"note": "对照臂没有可用数据", "beats_control_folds": None}
    excess = [a - b for a, b in pairs]
    strategy_cash = [f.avg_cash_ratio for f in usable if f.avg_cash_ratio is not None]
    control_cash = [f.control_cash_ratio for f in usable if f.control_cash_ratio is not None]
    # 每单位投入资金的收益：把两臂不同的现金拖累折算掉，否则"收益差"里
    # 混着"稀释程度差"。这是粗算（假设现金占比恒定、忽略逐日复利），
    # 只用于判断方向——但它能把符号翻过来，所以必须与原始口径并列。
    per_invested: list[float] = []
    for fold in usable:
        cash_a = fold.avg_cash_ratio
        cash_b = fold.control_cash_ratio
        if cash_a is None or cash_b is None:
            continue
        if cash_a >= 1 or cash_b >= 1 or fold.test_return is None or fold.control_return is None:
            continue
        strategy_adj = (1 + fold.test_return) / (1 - cash_a) - 1
        control_adj = (1 + fold.control_return) / (1 - cash_b) - 1
        per_invested.append(strategy_adj - control_adj)
    return {
        "folds_compared": len(pairs),
        "strategy_mean": statistics.fmean([a for a, _ in pairs]),
        "control_mean": statistics.fmean([b for _, b in pairs]),
        "excess_mean": statistics.fmean(excess),
        "excess_median": statistics.median(excess),
        "beats_control_folds": sum(1 for value in excess if value > 0),
        "beats_control_ratio": sum(1 for value in excess if value > 0) / len(excess),
        "strategy_cash_median": statistics.median(strategy_cash) if strategy_cash else None,
        "control_cash_median": statistics.median(control_cash) if control_cash else None,
        "cash_gap_median": (
            statistics.median(strategy_cash) - statistics.median(control_cash)
            if strategy_cash and control_cash
            else None
        ),
        "per_invested_excess_mean": statistics.fmean(per_invested) if per_invested else None,
        "per_invested_folds": len(per_invested),
        "note": "对照臂 = 同池同期每期等权持有全部标的（不做选择）。"
        "两条臂共享同一份费用与撮合口径，差就是「选择」的贡献；"
        "但两臂现金占比不同（策略臂在只选出少数标的时被权重上限逼成部分空仓），"
        "所以收益差是**稀释后**的差。`per_invested_excess_mean` 是折算掉现金差异后的粗算，"
        "只用于判断方向，不得当收益引用。",
    }


def _standard_limitations(
    wf_spec: WalkForwardSpec, pf_spec: PortfolioSpec, result: PortfolioGateResult, usable: int
) -> list[str]:
    lines = [
        "**组合化降低的是横截面特异噪声，不降低共同因子风险**：27 折仍是同一段历史的"
        "不同切片，折间仍共享市场体制。这条不能说成「折已独立」。",
        "**折收益仍不可拼成连续净值**：每折资金重置、从空仓起步；复利相乘不对应任何账户。",
        "**本闸门的「样本外」只针对参数选择**：整段历史在 L1a/L2 阶段已被看过，"
        "测的是参数随时间的稳健性，不构成前瞻样本外证据。",
        f"组合层用 `{pf_spec.allocation}` 记账："
        + (
            "不受整手约束、不产生现金残留，因此收益与初始资金无关；"
            "**可执行性需另用 lots 口径复核**。"
            if pf_spec.allocation == "weights"
            else "受整手约束，会产生现金残留（用 avg_cash_ratio 观察）。"
        ),
        "**标的池有生存偏差风险**：池取自项目既有 518 池，其构造口径见 "
        "`docs/DATASET_POOL_2026-10-18.md`；该池对退市股的完备性本轮未核对。",
        "**30 只≠全市场**：抽样的具体名单已冻结，换种子会得到不同结论；"
        "组合层结论只能限定在这份名单上。",
        "每折在窗口首日补一次建仓信号：否则月度调仓会让折首最多空仓 29 天，"
        "折收益被系统性压低且与单标的 L3 不可比。",
    ]
    if result.control.get("beats_control_ratio") is not None:
        lines.append(
            "对照臂（等权持有全部标的）与本策略共享费用与撮合口径，"
            "但**它不是可投资基准**（每期全换手、无成本优化），只用于隔离「选择」的贡献。"
        )
    if usable < 10:
        lines.append(f"仅 {usable} 个可用折：折级统计功效很低，一折之差就能改变结论。")
    return lines


# --------------------------------------------------------------------------- #
# 六、报告
# --------------------------------------------------------------------------- #
def render_l3p_markdown(result: PortfolioGateResult, *, title: str = "") -> str:
    summary = result.summary
    meta = result.window_meta
    universe = result.universe
    lines: list[str] = []
    lines.append(f"# L3p 组合级 Walk-Forward：{title or result.hypothesis_id}")
    lines.append("")
    lines.append(f"- 假设编号：`{result.hypothesis_id}`")
    lines.append(f"- **终态：`{result.verdict}`**")
    lines.append(f"- 标的池：`{universe.get('universe_id')}`（{universe.get('size')} 只）")
    if result.verdict == "L3P_BLOCKED_PREREQUISITE":
        lines.append("")
        lines.append(
            "> **前置未满足**：前序闸门未放行该假设。以下数字只用于量化折间一致性，"
            "**不构成放行结论**。"
        )
        if result.preliminary_verdict:
            lines.append(">")
            lines.append(f"> 若不计前置，本闸门判定链自身的结论是 `{result.preliminary_verdict}`。")
    lines.append("")

    lines.append("## 一、口径")
    lines.append("")
    lines.append("| 项目 | 取值 |")
    lines.append("|---|---|")
    for key in ("mode", "train_days", "test_days", "step_days"):
        lines.append(f"| {key} | {meta.get(key)} |")
    portfolio_cfg = result.spec.get("portfolio") or {}
    lines.append(f"| 记账方式 | {portfolio_cfg.get('allocation')} |")
    lines.append(f"| 单标的上限 | {portfolio_cfg.get('max_weight_per_asset')} |")
    lines.append(f"| 调仓频率 | {portfolio_cfg.get('rebalance')}（+ 每折窗口首日） |")
    lines.append(f"| 组合日历 | {meta.get('first_day')} → {meta.get('last_day')} |")
    lines.append(f"| 折覆盖区间 | {meta.get('span_start')} → {meta.get('span_end')} |")
    lines.append(f"| 未使用尾部 | {meta.get('unused_tail_days')} 个交易日 |")
    lines.append("")

    if result.stages:
        lines.append("## 二、阶段状态")
        lines.append("")
        lines.append("| 阶段 | 状态 | 说明 |")
        lines.append("|---|---|---|")
        for stage in result.stages:
            lines.append(f"| {stage.get('name')} | {stage.get('status')} | {stage.get('detail')} |")
        lines.append("")

    lines.append("## 三、逐折明细")
    lines.append("")
    if result.folds:
        lines.append(
            "| 折 | 训练窗 | 测试窗 | 最优参数 | 训练收益 | 测试收益 | 对照臂 | 超额 | WFE | 平均持仓 | 现金占比 | 成交 |"
        )
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
        for fold in result.folds:
            params = json.dumps(fold.best_params, ensure_ascii=False, sort_keys=True)
            lines.append(
                f"| {fold.index} | {fold.train_start.date()} → {fold.train_end.date()} "
                f"| {fold.test_start.date()} → {fold.test_end.date()} "
                f"| `{params}` | {_f(fold.train_return)} | {_f(fold.test_return)} "
                f"| {_f(fold.control_return)} | {_f(fold.excess_vs_control)} "
                f"| {_f(fold.wfe)} | {_p(fold.avg_positions)} "
                f"| {_p(fold.avg_cash_ratio)} | {fold.fills if isinstance(fold, PortfolioFold) else '—'} |"
            )
    else:
        lines.append("无折被执行。")
    lines.append("")

    if summary.get("folds_usable"):
        lines.append("## 四、折级汇总（注意：不是连续账户）")
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|---|---|")
        lines.append(f"| 可用折 / 总折 | {summary.get('folds_usable')} / {summary.get('folds_total')} |")
        for key, label in (
            ("test_return_mean", "测试收益均值"),
            ("test_return_median", "测试收益中位"),
        ):
            lines.append(f"| {label} | {_f(summary.get(key))} |")
        lines.append(
            f"| 测试收益范围 | {_f(summary.get('test_return_min'))} ~ {_f(summary.get('test_return_max'))} |"
        )
        lines.append(f"| 测试收益标准差 | {_p(summary.get('test_return_std'))} |")
        lines.append(
            f"| 正收益折占比 | {summary.get('test_positive_folds')}/{summary.get('folds_usable')}"
            f"（{_p(summary.get('test_positive_fold_ratio'))}） |"
        )
        lines.append(f"| 训练正收益折占比 | {_p(summary.get('train_positive_fold_ratio'))} |")
        lines.append(
            f"| 「训练正、测试负」折 | {summary.get('divergent_folds')} 折"
            f"（{_p(summary.get('divergent_fold_ratio'))}） |"
        )
        lines.append(f"| 中位 WFE | {_f(summary.get('median_wfe'))}（{summary.get('wfe_samples')} 个可算折） |")
        lines.append(
            f"| 测试回撤中位 / 最大 | {_p(summary.get('test_drawdown_median'))} / "
            f"{_p(summary.get('test_drawdown_max'))} |"
        )
        lines.append("")

    port = result.portfolio or {}
    if port.get("avg_positions_median") is not None:
        lines.append("## 五、组合形态")
        lines.append("")
        lines.append(f"- 平均持仓数（折中位）：**{port['avg_positions_median']:.1f}** 只"
                     f"（{port['avg_positions_min']:.1f} ~ {port['avg_positions_max']:.1f}）")
        lines.append(f"- 平均现金占比（折中位）：**{_p(port.get('avg_cash_ratio_median'))}**")
        lines.append(f"- 全现金调仓次数（全部折合计）：{port.get('cash_only_periods_total')}")
        lines.append(f"- {port.get('note')}")
        lines.append("")

    control = result.control or {}
    if control.get("beats_control_ratio") is not None:
        lines.append("## 六、与对照臂的对比（「选择」到底贡献了什么）")
        lines.append("")
        lines.append("| 指标 | 值 |")
        lines.append("|---|---|")
        lines.append(f"| 可比折数 | {control['folds_compared']} |")
        lines.append(f"| 策略折收益均值 | {_f(control['strategy_mean'])} |")
        lines.append(f"| 对照臂折收益均值 | {_f(control['control_mean'])} |")
        lines.append(f"| 超额（策略 − 对照）均值 | {_f(control['excess_mean'])} |")
        lines.append(f"| 超额中位 | {_f(control['excess_median'])} |")
        lines.append(
            f"| 跑赢对照的折 | {control['beats_control_folds']}/{control['folds_compared']}"
            f"（{_p(control['beats_control_ratio'])}） |"
        )
        lines.append(f"| 策略臂现金占比中位 | {_p(control.get('strategy_cash_median'))} |")
        lines.append(f"| 对照臂现金占比中位 | {_p(control.get('control_cash_median'))} |")
        lines.append(f"| 现金拖累差（策略 − 对照） | {_p(control.get('cash_gap_median'))} |")
        lines.append(
            f"| 每单位投入资金的超额（粗算，{control.get('per_invested_folds')} 折） | "
            f"{_f(control.get('per_invested_excess_mean'))} |"
        )
        lines.append("")
        lines.append(f"> {control['note']}")
        lines.append("")
        if control.get("per_invested_excess_mean") is not None and control["per_invested_excess_mean"] < 0:
            lines.append(
                "> **符号翻转**：原始口径超额为正，折算掉现金拖累后为负。"
                "这说明两臂的收益差主要由「投入比例」造成，而不是「选择」本身。"
            )
            lines.append("")

    if result.param_selection.get("distinct_param_sets") is not None:
        stats = result.param_selection
        lines.append("## 七、参数选择稳定性")
        lines.append("")
        lines.append(f"- 不同参数集：**{stats.get('distinct_param_sets')}** 个")
        lines.append(
            f"- 最常用参数：`{json.dumps(stats.get('most_common'), ensure_ascii=False, sort_keys=True)}`"
            f"，出现 {stats.get('most_common_count')} 次（稳定性 {_p(stats.get('param_stability'))}）"
        )
        lines.append(f"- 参数变动次数：{stats.get('changes')} / {stats.get('comparisons')} 次相邻比较")
        lines.append("")

    if result.checks:
        lines.append("## 八、判定明细")
        lines.append("")
        lines.append("| 检查项 | 结果 | 实际值 | 阈值 | 说明 |")
        lines.append("|---|---|---|---|---|")
        for check in result.checks:
            mark = {True: "PASS", False: "FAIL", None: "不可判定"}[check.get("passed")]
            lines.append(
                f"| {check['name']} | {mark} | {_cell(check.get('actual'))} | "
                f"{_cell(check.get('threshold'))} | {check.get('detail')} |"
            )
        lines.append("")

    if result.limitations:
        lines.append("## 九、口径与限制")
        lines.append("")
        for item in result.limitations:
            lines.append(f"- {item}")
        lines.append("")
    if result.notes:
        lines.append("## 十、备注")
        lines.append("")
        for item in result.notes:
            lines.append(f"- {item}")
        lines.append("")
    return "\n".join(lines)


def _f(value: float | None) -> str:
    return "—" if value is None else f"{value:+.4f}"


def _p(value: float | None) -> str:
    return "—" if value is None else f"{value:.2%}"


def _cell(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float):
        return f"{value:.4f}"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)
