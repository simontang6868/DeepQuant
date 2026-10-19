"""POST /portfolio-backtests：多标的组合回测（薄层）。

为什么单独一个模块
------------------
``backtest/portfolio.py`` 已经实现了「多标的、共享现金池、连续净值」的记账循环，
``backtest/cross_section.py`` 已经实现了截面动量取分。本模块**只做接线**：

    读数据集 → 走同一套数据闸门 → 取分 → 建调仓表 → 调用 run_portfolio_backtest
    → 序列化

本层不撮合、不重算绩效、不改写账户状态。撮合口径的唯一来源是
``docs/A3_ACCOUNTING_SPEC.md`` 与 ``portfolio.py``，这里一旦「顺手修一下」
就会出现「界面与研究报告不是同一个引擎」的问题，那种偏差无法靠测试发现。

与单标的接口的关系
------------------
``POST /backtests`` 是「单标的、单持仓、单现金」模型。把单标的引擎套在循环里
跑 N 只，得到的是 N 个独立账户，不是组合（现金不能互相调济、无法表达「卖出 A
买入 B」）；因此组合必须走 ``run_portfolio_backtest``，不能复用 ``run_backtest``。
两个接口并存，各自回答不同问题，不存在「哪个更准」。

如实标注（本层刻意写进响应里的三件事）
--------------------------------------
1. 涨跌停：组合层 ``PriceLimit`` 是**单一标量**，没有 per-symbol 通道，
   因此只能按 flat 10% 执行。创业板/科创板 2020-08-24 起的 20% 无法在组合层
   区分——这与 ``docs/GATE1_PREREGISTRATION.md`` 附录 C 记录的偏差是同一条。
2. 标的池：界面用的是「2026-10 的成分股快照」，回看 2017 年存在幸存者偏差。
   本层无法修正（没有历史成分股数据），只能把这条写进 ``limitations``。
3. 区间上限：由数据集覆盖终点决定，不由接口决定。请求的 end_date 超出数据
   覆盖时会被真实数据截断，响应里的 ``period`` 是**实际**区间。
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from deepquant.api.datasets import DatasetInfo, find_dataset
from deepquant.api.errors import BacktestRejectedError, DatasetUnavailableError
from deepquant.api.schemas import PortfolioBacktestRequest
from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.cross_section import CrossSectionConfig, CrossSectionPanel
from deepquant.backtest.portfolio import (
    PortfolioBacktestConfig,
    PortfolioBacktestResult,
    build_rebalance_schedule,
    month_end_signal_dates,
    run_portfolio_backtest,
)
from deepquant.data.storage.io import read_daily_parquet
from deepquant.data.validation.checks import ERROR, validate_daily
from deepquant.market.rules import a_share_rules

#: 组合层响应的 schema 版本（与单标的 ``result_to_dict`` 的 schema_version 分开演进）
PORTFOLIO_SCHEMA_VERSION = "portfolio-v1"

#: 组合层单一涨跌停比例。与 ``scripts/gate1_run.py`` 的
#: ``PORTFOLIO_PRICE_LIMIT_RATE`` 保持同一数值；改这里等于改研究口径。
PORTFOLIO_PRICE_LIMIT_RATE = 0.10

#: 预登记 §5 的 min_assets 默认值（候选 < 5 只当期空仓）
DEFAULT_MIN_ASSETS = 5

#: 预登记 §3~§4 的截面口径默认值
DEFAULT_LOOKBACK = 60
DEFAULT_SKIP = 5

#: 选股口径
SELECTION_MOMENTUM_TOP = "momentum_top"
SELECTION_EQUAL_WEIGHT_ALL = "equal_weight_all"


@dataclass(frozen=True)
class LoadedPool:
    """已通过数据闸门的标的池。"""

    symbols: tuple[str, ...]
    prices: dict[str, pd.DataFrame]
    datasets: tuple[DatasetInfo, ...]


def _load_one(data_root: Path, symbol: str) -> tuple[DatasetInfo, pd.DataFrame]:
    """载入单只并复用单标的接口的同一套数据校验。

    这里**不跳过**校验：组合层与单标的层必须对「什么算可用的数据」看法一致，
    否则同一份数据在两边一个能跑一个不能跑，报错指向也无从对照。
    """
    info, processed_path = find_dataset(data_root, symbol)
    try:
        frame = read_daily_parquet(processed_path)
    except Exception as exc:  # noqa: BLE001 - 对外统一为「数据集不可用」
        raise DatasetUnavailableError(
            f"数据集 {symbol!r} 已登记但不可用：数据文件无法读取"
        ) from exc

    report = validate_daily(frame)
    if not report.is_valid:
        detail = "; ".join(f"{i.code}({i.count})" for i in report.issues if i.level == ERROR)
        raise BacktestRejectedError(
            f"数据集 {symbol!r} 存在严重数据质量问题：{detail}"
        )
    return info, frame


def load_pool(data_root: Path, symbols: Sequence[str]) -> LoadedPool:
    """按请求顺序载入标的池（顺序不影响结果，只为报告可读）。"""
    prices: dict[str, pd.DataFrame] = {}
    infos: list[DatasetInfo] = []
    for symbol in symbols:
        info, frame = _load_one(data_root, symbol)
        prices[symbol] = frame
        infos.append(info)
    return LoadedPool(tuple(symbols), prices, tuple(infos))


def _build_fee_config(request: PortfolioBacktestRequest) -> BacktestConfig:
    """组合层只用 ``BacktestConfig`` 作费率/市场规则**模板**（symbol/cash 不参与）。"""
    return BacktestConfig(
        symbol="PORTFOLIO",
        initial_cash=request.initial_cash,
        commission_rate=request.commission_rate,
        min_commission=request.min_commission,
        stamp_duty_rate=request.stamp_duty_rate,
        transfer_fee_rate=request.transfer_fee_rate,
        slippage_buy=request.slippage_buy,
        slippage_sell=request.slippage_sell,
        fee_source=request.fee_source,
        # flat 10%：组合层没有 per-symbol 涨跌停通道，这是能力边界而非选择
        market_rules=a_share_rules(price_limit_rate=PORTFOLIO_PRICE_LIMIT_RATE),
    )


def _selection_meta(
    *,
    selection: str,
    top_fraction: float,
    min_assets: int,
    lookback: int,
    skip: int,
    signal_dates: Sequence[pd.Timestamp],
    schedule: Sequence,
) -> dict:
    """把「选股口径」与「实际选出了什么」一起记下来。

    只记参数不记结果，会让「12 个月全部空仓」看起来像正常的低换手组合。
    """
    sizes = [len(t.weights) for t in schedule]
    empty = sum(1 for n in sizes if n == 0)
    return {
        "selection": selection,
        "top_fraction": top_fraction,
        "min_assets": min_assets,
        "lookback": lookback,
        "skip": skip,
        "rebalance": "month_end",
        "n_signal_dates": len(signal_dates),
        "n_empty_periods": empty,
        "n_non_empty_periods": len(sizes) - empty,
        "avg_holdings": (sum(sizes) / len(sizes)) if sizes else None,
        "min_holdings": min(sizes) if sizes else None,
        "max_holdings": max(sizes) if sizes else None,
        "weighting": "equal",
    }


def _limit_notes(request: PortfolioBacktestRequest, pool: LoadedPool) -> list[str]:
    """把无法在本层修正的偏差如实写进响应（不是免责声明，是可核对的已知限制）。"""
    notes = [
        f"涨跌停按组合层单一标量 {PORTFOLIO_PRICE_LIMIT_RATE:.0%} 执行："
        "组合层 PriceLimit 无 per-symbol 通道，创业板/科创板 2020-08-24 起的 20% "
        "无法区分（与 docs/GATE1_PREREGISTRATION.md 附录 C 记录的是同一条偏差）",
        "标的池来自已导入的数据快照，若为「当前成分股」回看历史则存在幸存者偏差，"
        "本层无法修正（没有历史成分股数据）",
        "组合按目标**权重**记账（allocation=weights），不受整手约束；"
        "「能不能真的买进这些股数」需用 allocation=lots 另行检验",
    ]
    if request.min_assets < DEFAULT_MIN_ASSETS:
        notes.append(
            f"min_assets={request.min_assets} 低于预登记 §5 的 {DEFAULT_MIN_ASSETS}："
            "候选不足时不再空仓，本期结论与研究报告口径不同"
        )
    if len(pool.symbols) < DEFAULT_MIN_ASSETS:
        notes.append(
            f"标的池只有 {len(pool.symbols)} 只，少于预登记 §5 的 {DEFAULT_MIN_ASSETS} 只"
        )
    return notes


def _coverage_note(pool: LoadedPool, period: dict) -> str:
    """区间上限是数据覆盖的终点，不是接口偏好——必须说清楚。"""
    ends = sorted({d.end_date for d in pool.datasets})
    earliest_end = ends[0]
    if len(ends) == 1:
        return f"全部标的的数据覆盖到 {earliest_end}，回测区间无法超过该日期"
    return (
        f"各标的数据终点不一致（{earliest_end} ~ {ends[-1]}），"
        f"回测区间被最早的终点 {earliest_end} 截断前请注意尾部只有部分标的有数据"
    )


def _serialize_states(result: PortfolioBacktestResult) -> list[dict]:
    return [
        {
            "date": s.date.strftime("%Y-%m-%d"),
            "cash": float(s.cash),
            "market_value": float(s.market_value),
            "total_equity": None if s.total_equity is None else float(s.total_equity),
            "n_positions": int(s.n_positions),
            "n_stale_positions": int(s.n_stale_positions),
            "stale_weight_ratio": float(s.stale_weight_ratio),
            "low_confidence_valuation": bool(s.low_confidence_valuation),
        }
        for s in result.states
    ]


def _serialize_fills(result: PortfolioBacktestResult) -> list[dict]:
    return [
        {
            "fill_id": f.fill_id,
            "date": f.date.strftime("%Y-%m-%d"),
            "symbol": f.symbol,
            "side": f.side.value,
            "price": float(f.price),
            "quantity": int(f.quantity),
            "gross": float(f.gross),
            "fees_total": float(f.fees_total),
            "reason": f.reason,
        }
        for f in result.fills
    ]


def _serialize_blocked(result: PortfolioBacktestResult) -> list[dict]:
    return [
        {
            "date": b.date.strftime("%Y-%m-%d"),
            "symbol": b.symbol,
            "side": b.side.value,
            "reason": b.reason,
            "requested_quantity": int(b.requested_quantity),
            "filled_quantity": int(b.filled_quantity),
        }
        for b in result.blocked
    ]


def _serialize_performance(result: PortfolioBacktestResult) -> dict | None:
    perf = result.performance
    if perf is None:
        return None
    stats = perf.stats
    return {
        "n_periods": int(stats.n_periods),
        "n_returns": int(stats.n_returns),
        "initial_value": float(stats.initial_value),
        "final_value": None if stats.final_value is None else float(stats.final_value),
        "cumulative_return": (
            None if stats.cumulative_return is None else float(stats.cumulative_return)
        ),
        "annualized_return": (
            None if stats.annualized_return is None else float(stats.annualized_return)
        ),
        "annualized_volatility": (
            None if stats.annualized_volatility is None else float(stats.annualized_volatility)
        ),
        "max_drawdown_ratio": (
            None if stats.max_drawdown_ratio is None else float(stats.max_drawdown_ratio)
        ),
        "max_drawdown_amount": (
            None if stats.max_drawdown_amount is None else float(stats.max_drawdown_amount)
        ),
        "is_complete": bool(stats.is_complete),
        "total_pnl": None if perf.total_pnl is None else float(perf.total_pnl),
        "sharpe": {
            "value": None if perf.sharpe.value is None else float(perf.sharpe.value),
            "risk_free_rate_annual": float(perf.sharpe.risk_free_rate_annual),
            "periods_per_year": int(perf.sharpe.periods_per_year),
            "n_returns": int(perf.sharpe.n_returns),
            "unavailable_reason": perf.sharpe.unavailable_reason,
            "definition": perf.sharpe.definition,
        },
        "turnover": {
            "total_traded_amount": float(perf.turnover.total_traded_amount),
            "average_equity": (
                None
                if perf.turnover.average_equity is None
                else float(perf.turnover.average_equity)
            ),
            "years": float(perf.turnover.years),
            "one_way_annualized": (
                None
                if perf.turnover.one_way_annualized is None
                else float(perf.turnover.one_way_annualized)
            ),
            "unavailable_reason": perf.turnover.unavailable_reason,
            "definition": perf.turnover.definition,
        },
        "cost": {
            "total_fees": float(perf.cost.total_fees),
            "net_profit": None if perf.cost.net_profit is None else float(perf.cost.net_profit),
            "gross_profit": (
                None if perf.cost.gross_profit is None else float(perf.cost.gross_profit)
            ),
            "cost_to_gross_ratio": (
                None
                if perf.cost.cost_to_gross_ratio is None
                else float(perf.cost.cost_to_gross_ratio)
            ),
            "unavailable_reason": perf.cost.unavailable_reason,
            "definition": perf.cost.definition,
        },
        "evidence": {
            "level": perf.evidence.level,
            "n_rebalance_periods": int(perf.evidence.n_rebalance_periods),
            "n_tradable_assets": int(perf.evidence.n_tradable_assets),
            "reasons": list(perf.evidence.reasons),
        },
    }


def run_portfolio(request: PortfolioBacktestRequest, data_root: Path) -> dict:
    """执行一次组合回测并返回可 JSON 化的结果文档。

    Raises:
        DatasetNotFoundError / DatasetUnavailableError: 数据集缺失或不可用（404）。
        BacktestRejectedError: 数据未通过质量闸门，或组合配置与行情不匹配（400）。
        ConfigInvalidError: 请求参数组合不成立（422，由路由层先行拦截）。
    """
    pool = load_pool(data_root, request.dataset_symbols)

    # 日历 = 各标的日期并集（缺失日视为停牌，不当作「没有这一天」）。
    # 显式传入以固定区间：若由数据反推，删一行会让整条日历平移，信号日随之漂移。
    all_dates = sorted(set().union(*(set(pd.to_datetime(df["date"])) for df in pool.prices.values())))
    if not all_dates:
        raise BacktestRejectedError("标的池内没有任何可用行情")
    calendar = pd.DatetimeIndex(all_dates)
    if request.start_date is not None:
        calendar = calendar[calendar >= pd.Timestamp(request.start_date)]
    if request.end_date is not None:
        calendar = calendar[calendar <= pd.Timestamp(request.end_date)]
    if len(calendar) == 0:
        raise BacktestRejectedError("回测区间内没有任何可用行情，请检查起止日期")

    selection = request.selection
    top_fraction = 1.0 if selection == SELECTION_EQUAL_WEIGHT_ALL else request.top_fraction
    if selection == SELECTION_EQUAL_WEIGHT_ALL and request.top_fraction != 1.0:
        # 全池等权时 top_fraction 无意义；不静默忽略，直接在 limitations 里说清
        pass

    cross_config = CrossSectionConfig(lookback=request.lookback, skip=request.skip)
    panel = CrossSectionPanel(pool.prices, config=cross_config, calendar=calendar)
    signal_dates = month_end_signal_dates(calendar)
    scores = panel.scores_by_date(signal_dates)
    schedule = build_rebalance_schedule(
        scores, top_fraction=top_fraction, min_assets=request.min_assets
    )

    fee_config = _build_fee_config(request)
    config = PortfolioBacktestConfig(
        symbols=pool.symbols,
        initial_cash=request.initial_cash,
        fee_config=fee_config,
        allocation=request.allocation,
        max_weight_per_asset=request.max_weight_per_asset,
    )

    exec_start = time.perf_counter()
    try:
        result = run_portfolio_backtest(
            pool.prices,
            schedule,
            config,
            start_date=None if request.start_date is None else pd.Timestamp(request.start_date),
            end_date=None if request.end_date is None else pd.Timestamp(request.end_date),
        )
    except ValueError as exc:
        # 引擎的 ValueError 都是「配置与行情不匹配」类，属于请求问题而非服务故障
        raise BacktestRejectedError(f"组合回测无法执行：{exc}") from exc
    elapsed = time.perf_counter() - exec_start

    period = {
        "start_date": calendar[0].strftime("%Y-%m-%d"),
        "end_date": calendar[-1].strftime("%Y-%m-%d"),
        "n_trading_days": int(len(calendar)),
    }

    limitations = list(result.limitations) + _limit_notes(request, pool)
    if selection == SELECTION_EQUAL_WEIGHT_ALL:
        limitations.append(
            "选股口径为「全池等权」（top_fraction=1.0）：不做动量筛选，"
            "对标的池的构成变化完全暴露"
        )

    consistency = result.consistency_issues()

    return {
        "schema_version": PORTFOLIO_SCHEMA_VERSION,
        "engine": {
            "module": "deepquant.backtest.portfolio.run_portfolio_backtest",
            "price_limit_rate": PORTFOLIO_PRICE_LIMIT_RATE,
            "elapsed_seconds": round(elapsed, 4),
        },
        "config": {
            "symbols": list(pool.symbols),
            "n_symbols": len(pool.symbols),
            "initial_cash": float(config.initial_cash),
            "allocation": config.allocation,
            "lot_size": int(config.lot_size),
            "max_weight_per_asset": config.max_weight_per_asset,
            "selection": selection,
            "top_fraction": top_fraction,
            "lookback": int(request.lookback),
            "skip": int(request.skip),
            "min_assets": int(request.min_assets),
            "fee_source": request.fee_source,
            "commission_rate": float(request.commission_rate),
            "min_commission": float(request.min_commission),
            "stamp_duty_rate": float(request.stamp_duty_rate),
            "transfer_fee_rate": float(request.transfer_fee_rate),
            "slippage_buy": float(request.slippage_buy),
            "slippage_sell": float(request.slippage_sell),
        },
        "period": period,
        "coverage": {
            "note": _coverage_note(pool, period),
            "symbol_end_dates": {d.symbol: d.end_date for d in pool.datasets},
        },
        "datasets": [
            {
                "symbol": d.symbol,
                "display_name": d.display_name,
                "rows": int(d.rows),
                "start_date": d.start_date,
                "end_date": d.end_date,
                "source_name": d.source_name,
                "source_sha256": d.source_sha256,
            }
            for d in pool.datasets
        ],
        "selection": _selection_meta(
            selection=selection,
            top_fraction=top_fraction,
            min_assets=request.min_assets,
            lookback=request.lookback,
            skip=request.skip,
            signal_dates=signal_dates,
            schedule=schedule,
        ),
        "targets": [
            {
                "signal_date": t.signal_date.strftime("%Y-%m-%d"),
                "weights": {k: float(v) for k, v in sorted(t.weights.items())},
            }
            for t in result.schedule
        ],
        "states": _serialize_states(result),
        "fills": _serialize_fills(result),
        "blocked": _serialize_blocked(result),
        "performance": _serialize_performance(result),
        "data_quality_issues": list(result.data_quality_issues),
        "limitations": limitations,
        "is_complete": bool(result.is_complete),
        "incomplete_reasons": list(result.incomplete_reasons),
        "consistency_issues": consistency,
    }
