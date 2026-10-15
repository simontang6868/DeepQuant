"""BacktestResult 的稳定 JSON 序列化 / 反序列化。

设计要点：
- 输出为纯 **标准 JSON**（``json.dumps(..., allow_nan=False)``），
  禁止 NaN / Infinity / -Infinity；
- 日期统一为无时区 ISO-8601 字符串（保留本项目无时区约定）；
- 枚举序列化为稳定的字符串值（``.value``）；
- 缺失值统一用 ``null``；
- 保留 ``schema_version`` 与 ``engine_version``，反序列化时校验 schema 兼容性；
- 反序列化恢复核心数据结构及其类型（BacktestConfig / MarketRules / Order / Fill /
  Fees / Position / AccountState / PerformanceMetrics），而非不可恢复的字符串；
- 失败时在异常消息中给出具体字段路径。

本模块不重新实现任何交易执行逻辑，只做结构与类型的读写。
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from deepquant.backtest.config import (
    BacktestConfig,
    ExecutionPrice,
    require_naive_timestamp,
)
from deepquant.backtest.models import (
    AccountState,
    BacktestResult,
    Fees,
    Fill,
    Order,
    OrderStatus,
    Position,
    ValuationStatus,
)
from deepquant.market.rules import MarketRules, PriceLimit, Side
from deepquant.performance.json_encoding import SerializationError, to_jsonable
from deepquant.performance.metrics import (
    DailyReturn,
    EquityPoint,
    PerformanceMetrics,
    TradeStats,
)
from deepquant.performance.reproducibility import reproducibility_report
from deepquant.version import RESULT_SCHEMA_VERSION

SUPPORTED_SCHEMA_VERSIONS: frozenset[str] = frozenset({RESULT_SCHEMA_VERSION})


# --------------------------------------------------------------------------- #
# 基础辅助
# --------------------------------------------------------------------------- #


def _require_key(data: dict, key: str, where: str) -> Any:
    if key not in data:
        raise SerializationError(f"{where}: 缺少必需字段 {key!r}")
    return data[key]


def _iso(value: pd.Timestamp | None) -> str | None:
    if value is None:
        return None
    return pd.Timestamp(value).isoformat()


def _parse_ts(value: Any, where: str) -> pd.Timestamp | None:
    if value is None:
        return None
    try:
        return require_naive_timestamp(value, where)
    except ValueError as exc:
        raise SerializationError(f"{where}: 无法解析时间戳：{value!r}（{exc}）") from exc


def _expect_mapping(value: Any, where: str) -> dict:
    if not isinstance(value, dict):
        raise SerializationError(f"{where}: 期望对象（dict），收到 {type(value)!r}")
    return value


def _expect_list(value: Any, where: str) -> list:
    if not isinstance(value, list):
        raise SerializationError(f"{where}: 期望数组（list），收到 {type(value)!r}")
    return value


def _expect_enum(enum_cls: type, value: Any, where: str):
    try:
        return enum_cls(value)
    except (ValueError, KeyError) as exc:
        raise SerializationError(f"{where}: 非法枚举值 {value!r}（{enum_cls.__name__}）") from exc


# --------------------------------------------------------------------------- #
# 市场规则 / 配置
# --------------------------------------------------------------------------- #


def market_rules_to_dict(rules: MarketRules) -> dict:
    limit = rules.price_limit
    return {
        "name": rules.name,
        "lot_size": rules.lot_size,
        "t_plus_one": bool(rules.t_plus_one),
        "price_limit": (
            None if limit is None else {"rate": limit.rate, "tick_size": limit.tick_size}
        ),
    }


def market_rules_from_dict(data: dict) -> MarketRules:
    data = _expect_mapping(data, "$.config.market_rules")
    limit_data = data.get("price_limit")
    price_limit = None
    if limit_data is not None:
        limit_data = _expect_mapping(limit_data, "$.config.market_rules.price_limit")
        price_limit = PriceLimit(
            rate=_require_key(limit_data, "rate", "$.config.market_rules.price_limit"),
            tick_size=_require_key(limit_data, "tick_size", "$.config.market_rules.price_limit"),
        )
    return MarketRules(
        name=_require_key(data, "name", "$.config.market_rules"),
        lot_size=_require_key(data, "lot_size", "$.config.market_rules"),
        t_plus_one=_require_key(data, "t_plus_one", "$.config.market_rules"),
        price_limit=price_limit,
    )


def config_to_dict(config: BacktestConfig) -> dict:
    return {
        "symbol": config.symbol,
        "initial_cash": config.initial_cash,
        "start_date": _iso(config.start_date),
        "end_date": _iso(config.end_date),
        "exec_price_rule": config.exec_price_rule.value,
        "signal_lag": config.signal_lag,
        "market_rules": market_rules_to_dict(config.market_rules),
        "commission_rate": config.commission_rate,
        "min_commission": config.min_commission,
        "stamp_duty_rate": config.stamp_duty_rate,
        "transfer_fee_rate": config.transfer_fee_rate,
        "slippage_buy": config.slippage_buy,
        "slippage_sell": config.slippage_sell,
        "fee_source": config.fee_source,
        "assumptions": list(config.assumptions),
    }


def config_from_dict(data: dict) -> BacktestConfig:
    data = _expect_mapping(data, "$.config")
    return BacktestConfig(
        symbol=_require_key(data, "symbol", "$.config"),
        initial_cash=_require_key(data, "initial_cash", "$.config"),
        start_date=_parse_ts(data.get("start_date"), "$.config.start_date"),
        end_date=_parse_ts(data.get("end_date"), "$.config.end_date"),
        exec_price_rule=_expect_enum(
            ExecutionPrice,
            _require_key(data, "exec_price_rule", "$.config"),
            "$.config.exec_price_rule",
        ),
        signal_lag=_require_key(data, "signal_lag", "$.config"),
        market_rules=market_rules_from_dict(_require_key(data, "market_rules", "$.config")),
        commission_rate=_require_key(data, "commission_rate", "$.config"),
        min_commission=_require_key(data, "min_commission", "$.config"),
        stamp_duty_rate=_require_key(data, "stamp_duty_rate", "$.config"),
        transfer_fee_rate=_require_key(data, "transfer_fee_rate", "$.config"),
        slippage_buy=_require_key(data, "slippage_buy", "$.config"),
        slippage_sell=_require_key(data, "slippage_sell", "$.config"),
        fee_source=_require_key(data, "fee_source", "$.config"),
        assumptions=tuple(_require_key(data, "assumptions", "$.config")),
    )


def config_summary(config: BacktestConfig) -> dict:
    """配置的紧凑摘要（供可复现性判定使用）。"""
    return {
        "symbol": config.symbol,
        "initial_cash": config.initial_cash,
        "fee_source": config.fee_source,
        "exec_price_rule": config.exec_price_rule.value,
        "signal_lag": config.signal_lag,
        "market_rules": market_rules_to_dict(config.market_rules),
        "period": {"start": _iso(config.start_date), "end": _iso(config.end_date)},
    }


# --------------------------------------------------------------------------- #
# 订单 / 成交 / 账户状态
# --------------------------------------------------------------------------- #


def order_to_dict(order: Order) -> dict:
    return {
        "order_id": order.order_id,
        "symbol": order.symbol,
        "side": order.side.value,
        "signal_date": _iso(order.signal_date),
        "order_date": _iso(order.order_date),
        "planned_exec_date": _iso(order.planned_exec_date),
        "quantity": order.quantity,
        "status": order.status.value,
        "reason": order.reason,
        "fill_id": order.fill_id,
    }


def order_from_dict(data: dict, where: str = "$.orders[i]") -> Order:
    data = _expect_mapping(data, where)
    return Order(
        order_id=_require_key(data, "order_id", where),
        symbol=_require_key(data, "symbol", where),
        side=_expect_enum(Side, _require_key(data, "side", where), f"{where}.side"),
        signal_date=_parse_ts(_require_key(data, "signal_date", where), f"{where}.signal_date"),
        order_date=_parse_ts(_require_key(data, "order_date", where), f"{where}.order_date"),
        planned_exec_date=_parse_ts(
            _require_key(data, "planned_exec_date", where), f"{where}.planned_exec_date"
        ),
        quantity=_require_key(data, "quantity", where),
        status=_expect_enum(
            OrderStatus, _require_key(data, "status", where), f"{where}.status"
        ),
        reason=data.get("reason"),
        fill_id=data.get("fill_id"),
    )


def fees_to_dict(fees: Fees) -> dict:
    return {
        "commission": fees.commission,
        "stamp_duty": fees.stamp_duty,
        "transfer_fee": fees.transfer_fee,
    }


def fees_from_dict(data: dict, where: str) -> Fees:
    data = _expect_mapping(data, where)
    return Fees(
        commission=_require_key(data, "commission", where),
        stamp_duty=_require_key(data, "stamp_duty", where),
        transfer_fee=_require_key(data, "transfer_fee", where),
    )


def fill_to_dict(fill: Fill) -> dict:
    return {
        "fill_id": fill.fill_id,
        "order_id": fill.order_id,
        "symbol": fill.symbol,
        "side": fill.side.value,
        "fill_date": _iso(fill.fill_date),
        "price": fill.price,
        "quantity": fill.quantity,
        "fees": fees_to_dict(fill.fees),
    }


def fill_from_dict(data: dict, where: str = "$.fills[i]") -> Fill:
    data = _expect_mapping(data, where)
    return Fill(
        fill_id=_require_key(data, "fill_id", where),
        order_id=_require_key(data, "order_id", where),
        symbol=_require_key(data, "symbol", where),
        side=_expect_enum(Side, _require_key(data, "side", where), f"{where}.side"),
        fill_date=_parse_ts(_require_key(data, "fill_date", where), f"{where}.fill_date"),
        price=_require_key(data, "price", where),
        quantity=_require_key(data, "quantity", where),
        fees=fees_from_dict(_require_key(data, "fees", where), f"{where}.fees"),
    )


def position_to_dict(position: Position) -> dict:
    return {
        "symbol": position.symbol,
        "quantity": position.quantity,
        "bought_today": position.bought_today,
        "avg_cost": position.avg_cost,
    }


def position_from_dict(data: dict, where: str) -> Position:
    data = _expect_mapping(data, where)
    return Position(
        symbol=_require_key(data, "symbol", where),
        quantity=_require_key(data, "quantity", where),
        bought_today=_require_key(data, "bought_today", where),
        avg_cost=data.get("avg_cost"),
    )


def account_state_to_dict(state: AccountState) -> dict:
    return {
        "date": _iso(state.date),
        "cash": state.cash,
        "position": position_to_dict(state.position),
        "close": state.close,
        "market_value": state.market_value,
        "total_equity": state.total_equity,
        "valuation_status": state.valuation_status.value,
    }


def account_state_from_dict(data: dict, where: str = "$.account_states[i]") -> AccountState:
    data = _expect_mapping(data, where)
    return AccountState(
        date=_parse_ts(_require_key(data, "date", where), f"{where}.date"),
        cash=_require_key(data, "cash", where),
        position=position_from_dict(_require_key(data, "position", where), f"{where}.position"),
        close=data.get("close"),
        market_value=data.get("market_value"),
        total_equity=data.get("total_equity"),
        valuation_status=_expect_enum(
            ValuationStatus,
            _require_key(data, "valuation_status", where),
            f"{where}.valuation_status",
        ),
    )


# --------------------------------------------------------------------------- #
# 绩效指标
# --------------------------------------------------------------------------- #


def trade_stats_to_dict(stats: TradeStats) -> dict:
    return {
        "n_orders": stats.n_orders,
        "n_filled_orders": stats.n_filled_orders,
        "n_unfilled_orders": stats.n_unfilled_orders,
        "n_fills": stats.n_fills,
        "buy_gross_amount": stats.buy_gross_amount,
        "sell_gross_amount": stats.sell_gross_amount,
        "total_fees": stats.total_fees,
        "n_closed_trades": stats.n_closed_trades,
        "n_winning_trades": stats.n_winning_trades,
        "n_losing_trades": stats.n_losing_trades,
        "n_breakeven_trades": stats.n_breakeven_trades,
        "win_rate": stats.win_rate,
        "open_position_quantity": stats.open_position_quantity,
    }


def trade_stats_from_dict(data: dict, where: str = "$.performance.trade_stats") -> TradeStats:
    data = _expect_mapping(data, where)
    return TradeStats(
        n_orders=_require_key(data, "n_orders", where),
        n_filled_orders=_require_key(data, "n_filled_orders", where),
        n_unfilled_orders=_require_key(data, "n_unfilled_orders", where),
        n_fills=_require_key(data, "n_fills", where),
        buy_gross_amount=_require_key(data, "buy_gross_amount", where),
        sell_gross_amount=_require_key(data, "sell_gross_amount", where),
        total_fees=_require_key(data, "total_fees", where),
        n_closed_trades=_require_key(data, "n_closed_trades", where),
        n_winning_trades=_require_key(data, "n_winning_trades", where),
        n_losing_trades=_require_key(data, "n_losing_trades", where),
        n_breakeven_trades=_require_key(data, "n_breakeven_trades", where),
        win_rate=data.get("win_rate"),
        open_position_quantity=_require_key(data, "open_position_quantity", where),
    )


def performance_to_dict(metrics: PerformanceMetrics) -> dict:
    return {
        "initial_equity": metrics.initial_equity,
        "final_equity": metrics.final_equity,
        "total_pnl": metrics.total_pnl,
        "cumulative_return": metrics.cumulative_return,
        "daily_returns": [
            {
                "date": _iso(r.date),
                "equity": r.equity,
                "prev_equity": r.prev_equity,
                "value": r.value,
            }
            for r in metrics.daily_returns
        ],
        "equity_curve": [
            {"date": _iso(p.date), "total_equity": p.total_equity} for p in metrics.equity_curve
        ],
        "max_drawdown_amount": metrics.max_drawdown_amount,
        "max_drawdown_ratio": metrics.max_drawdown_ratio,
        "max_drawdown_peak_date": _iso(metrics.max_drawdown_peak_date),
        "max_drawdown_trough_date": _iso(metrics.max_drawdown_trough_date),
        "drawdown_observation_count": metrics.drawdown_observation_count,
        "annualized_return": metrics.annualized_return,
        "annualized_volatility": metrics.annualized_volatility,
        "trade_stats": trade_stats_to_dict(metrics.trade_stats),
        "periods_per_year": metrics.periods_per_year,
        "is_complete": metrics.is_complete,
        "incomplete_reasons": list(metrics.incomplete_reasons),
        "unavailable_metrics": list(metrics.unavailable_metrics),
    }


def performance_from_dict(data: dict, where: str = "$.performance") -> PerformanceMetrics:
    data = _expect_mapping(data, where)
    daily_returns = []
    for i, item in enumerate(_expect_list(_require_key(data, "daily_returns", where), where)):
        item = _expect_mapping(item, f"{where}.daily_returns[{i}]")
        daily_returns.append(
            DailyReturn(
                date=_parse_ts(
                    _require_key(item, "date", f"{where}.daily_returns[{i}]"),
                    f"{where}.daily_returns[{i}].date",
                ),
                equity=item.get("equity"),
                prev_equity=item.get("prev_equity"),
                value=item.get("value"),
            )
        )
    equity_curve = []
    for i, item in enumerate(
        _expect_list(_require_key(data, "equity_curve", where), where)
    ):
        item = _expect_mapping(item, f"{where}.equity_curve[{i}]")
        equity_curve.append(
            EquityPoint(
                date=_parse_ts(
                    _require_key(item, "date", f"{where}.equity_curve[{i}]"),
                    f"{where}.equity_curve[{i}].date",
                ),
                total_equity=item.get("total_equity"),
            )
        )
    return PerformanceMetrics(
        initial_equity=_require_key(data, "initial_equity", where),
        final_equity=data.get("final_equity"),
        total_pnl=data.get("total_pnl"),
        cumulative_return=data.get("cumulative_return"),
        daily_returns=tuple(daily_returns),
        equity_curve=tuple(equity_curve),
        max_drawdown_amount=data.get("max_drawdown_amount"),
        max_drawdown_ratio=data.get("max_drawdown_ratio"),
        max_drawdown_peak_date=_parse_ts(
            data.get("max_drawdown_peak_date"), f"{where}.max_drawdown_peak_date"
        ),
        max_drawdown_trough_date=_parse_ts(
            data.get("max_drawdown_trough_date"), f"{where}.max_drawdown_trough_date"
        ),
        drawdown_observation_count=_require_key(data, "drawdown_observation_count", where),
        annualized_return=data.get("annualized_return"),
        annualized_volatility=data.get("annualized_volatility"),
        trade_stats=trade_stats_from_dict(
            _require_key(data, "trade_stats", where), f"{where}.trade_stats"
        ),
        periods_per_year=_require_key(data, "periods_per_year", where),
        is_complete=_require_key(data, "is_complete", where),
        incomplete_reasons=tuple(data.get("incomplete_reasons", [])),
        unavailable_metrics=tuple(data.get("unavailable_metrics", [])),
    )


# --------------------------------------------------------------------------- #
# 回测结果
# --------------------------------------------------------------------------- #


def result_to_dict(result: BacktestResult) -> dict:
    """把 BacktestResult 编码为 JSON 安全字典（含绩效数据，若已计算）。"""
    document = {
        "schema_version": result.schema_version,
        "engine_version": result.engine_version,
        "config": config_to_dict(result.config),
        "period": {"start": _iso(result.period_start), "end": _iso(result.period_end)},
        "strategy": result.strategy,
        "dataset": result.dataset,
        "orders": [order_to_dict(o) for o in result.orders],
        "fills": [fill_to_dict(f) for f in result.fills],
        "account_states": [account_state_to_dict(s) for s in result.account_states],
        "data_quality_issues": list(result.data_quality_issues),
        "limitations": list(result.limitations),
        "is_complete": result.is_complete,
        "incomplete_reasons": list(result.incomplete_reasons),
        "performance": (
            None if result.performance is None else performance_to_dict(result.performance)
        ),
        "reproducibility": reproducibility_report(
            strategy_meta=result.strategy,
            dataset_meta=result.dataset,
            config_summary=config_summary(result.config),
        ),
    }
    # 统一经过 JSON 安全编码，确保不含 NaN / Inf（否则抛出 SerializationError）
    return to_jsonable(document)


def result_to_json(result: BacktestResult, *, indent: int = 2) -> str:
    """序列化为标准 JSON 文本（不允许 NaN / Infinity）。"""
    return json.dumps(
        result_to_dict(result), ensure_ascii=False, indent=indent, allow_nan=False
    )


def result_from_dict(data: dict) -> BacktestResult:
    """由字典恢复 BacktestResult（恢复核心结构与类型）。"""
    data = _expect_mapping(data, "$")

    schema_version = _require_key(data, "schema_version", "$")
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise SerializationError(
            f"$: 不支持的 schema_version {schema_version!r}，"
            f"当前支持 {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )

    config = config_from_dict(_require_key(data, "config", "$"))

    orders = [
        order_from_dict(item, f"$.orders[{i}]")
        for i, item in enumerate(_expect_list(data.get("orders", []), "$.orders"))
    ]
    fills = [
        fill_from_dict(item, f"$.fills[{i}]")
        for i, item in enumerate(_expect_list(data.get("fills", []), "$.fills"))
    ]
    account_states = [
        account_state_from_dict(item, f"$.account_states[{i}]")
        for i, item in enumerate(
            _expect_list(data.get("account_states", []), "$.account_states")
        )
    ]

    period = _expect_mapping(data.get("period", {}), "$.period")
    result = BacktestResult(
        config=config,
        period_start=_parse_ts(period.get("start"), "$.period.start"),
        period_end=_parse_ts(period.get("end"), "$.period.end"),
        orders=orders,
        fills=fills,
        account_states=account_states,
        data_quality_issues=list(data.get("data_quality_issues", [])),
        limitations=list(data.get("limitations", [])),
        is_complete=bool(data.get("is_complete", False)),
        incomplete_reasons=list(data.get("incomplete_reasons", [])),
        schema_version=schema_version,
        engine_version=_require_key(data, "engine_version", "$"),
        strategy=data.get("strategy"),
        dataset=data.get("dataset"),
    )

    performance_data = data.get("performance")
    if performance_data is not None:
        result.performance = performance_from_dict(performance_data)

    issues = result.consistency_issues()
    if issues:
        raise SerializationError(f"$: 反序列化后订单与成交不一致：{issues}")

    return result


def result_from_json(text: str) -> BacktestResult:
    """由 JSON 文本恢复 BacktestResult。"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SerializationError(f"$: 无法解析 JSON：{exc}") from exc
    return result_from_dict(data)
