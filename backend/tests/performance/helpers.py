"""performance 测试的共享构造器与假策略（非测试模块，不会被收集）。"""

from __future__ import annotations

import pandas as pd

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.engine import run_backtest
from deepquant.backtest.models import (
    AccountState,
    BacktestResult,
    Fees,
    Fill,
    Order,
    OrderStatus,
    Position,
    ValuationStatus,
    assess_completeness,
)
from deepquant.market.rules import Side
from deepquant.strategies.base import Signal, Strategy

DATES = pd.to_datetime(
    ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]
)


class FakeStrategy(Strategy):
    """确定性假策略：按预置映射逐日给信号，其余为 HOLD。"""

    name = "fake"
    version = "0.0.0"

    def __init__(self, signal_map: dict):
        self._map = {pd.Timestamp(k): int(v) for k, v in signal_map.items()}

    @property
    def params(self) -> dict:
        return {"signals": {str(k): int(v) for k, v in self._map.items()}}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        series = pd.Series(int(Signal.HOLD), index=data.index, dtype="int64")
        for ts, value in self._map.items():
            # 只设置行情索引中存在的日期，避免 .loc 扩充导致索引与行情不一致
            if ts in series.index:
                series.loc[ts] = value
        return series


def make_frame(dates, opens, closes, volume=1000) -> pd.DataFrame:
    high = [max(o, c) for o, c in zip(opens, closes)]
    low = [min(o, c) for o, c in zip(opens, closes)]
    return pd.DataFrame(
        {
            "open": opens,
            "high": high,
            "low": low,
            "close": closes,
            "volume": [volume] * len(opens),
        },
        index=pd.to_datetime(dates),
    )


def make_config(**overrides) -> BacktestConfig:
    kwargs = dict(symbol="600000", initial_cash=100000.0)
    kwargs.update(overrides)
    return BacktestConfig(**kwargs)


def make_state(date, cash, quantity=0, close=None, bought_today=0, avg_cost=None) -> AccountState:
    """构造合法 AccountState：空仓 → 权益=cash；持仓且缺收盘价 → 权益=None。"""
    position = Position(symbol="600000", quantity=quantity, bought_today=bought_today, avg_cost=avg_cost)
    if quantity == 0:
        return AccountState(
            date=pd.Timestamp(date),
            cash=cash,
            position=position,
            close=None,
            market_value=0.0,
            total_equity=cash,
            valuation_status=ValuationStatus.OK,
        )
    if close is None:
        return AccountState(
            date=pd.Timestamp(date),
            cash=cash,
            position=position,
            close=None,
            market_value=None,
            total_equity=None,
            valuation_status=ValuationStatus.MISSING_CLOSE,
        )
    return AccountState(
        date=pd.Timestamp(date),
        cash=cash,
        position=position,
        close=close,
        market_value=quantity * close,
        total_equity=cash + quantity * close,
        valuation_status=ValuationStatus.OK,
    )


def flat_states(equities, dates=None) -> list[AccountState]:
    """构造空仓每日状态，使每日权益 == 给定值（便于精确测试权益曲线）。

    未显式给日期时，按权益点数取前 N 个默认日期（DATES[:len(equities)]）。
    """
    if dates is None:
        dates = DATES[: len(equities)]
    assert len(equities) == len(dates), "equities 与 dates 长度必须一致"
    return [make_state(d, cash=v) for d, v in zip(dates, equities)]


def missing_state(date, cash=500.0, quantity=100) -> AccountState:
    """持仓但收盘价缺失的每日状态（权益不可用）。"""
    return make_state(date, cash, quantity=quantity, close=None)


def build_result(
    *, states=(), orders=(), fills=(), initial=1000.0, **config_overrides
) -> BacktestResult:
    config = make_config(initial_cash=initial, **config_overrides)
    state_list = list(states)
    result = BacktestResult(
        config=config, orders=list(orders), fills=list(fills), account_states=state_list
    )
    result.is_complete, result.incomplete_reasons = assess_completeness(state_list)
    return result


def make_order(
    order_id="O1",
    side=Side.BUY,
    signal_date="2024-01-02",
    planned="2024-01-03",
    quantity=100,
    status=OrderStatus.FILLED,
    reason=None,
    fill_id=None,
) -> Order:
    signal_ts = pd.Timestamp(signal_date)
    return Order(
        order_id=order_id,
        symbol="600000",
        side=side,
        signal_date=signal_ts,
        order_date=signal_ts,
        planned_exec_date=pd.Timestamp(planned),
        quantity=quantity,
        status=status,
        reason=reason,
        fill_id=fill_id,
    )


def make_fill(
    fill_id="F1",
    order_id="O1",
    side=Side.BUY,
    fill_date="2024-01-03",
    price=10.0,
    quantity=100,
    commission=0.0,
    stamp_duty=0.0,
    transfer_fee=0.0,
) -> Fill:
    return Fill(
        fill_id=fill_id,
        order_id=order_id,
        symbol="600000",
        side=side,
        fill_date=pd.Timestamp(fill_date),
        price=price,
        quantity=quantity,
        fees=Fees(commission=commission, stamp_duty=stamp_duty, transfer_fee=transfer_fee),
    )


def run_engine(strategy=None, frame=None, config=None) -> BacktestResult:
    strategy = strategy if strategy is not None else FakeStrategy({DATES[0]: 1, DATES[2]: -1})
    frame = (
        frame
        if frame is not None
        else make_frame(DATES, opens=[10.0, 10.0, 11.0, 12.0, 13.0, 14.0], closes=[10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    )
    config = config if config is not None else make_config(initial_cash=100000.0)
    return run_backtest(frame, strategy, config)


def engine_result() -> BacktestResult:
    return run_engine()
