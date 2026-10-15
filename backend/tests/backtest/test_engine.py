"""回测引擎测试：完整流程、交易规则、账务一致性、估值与输入校验。"""

import math

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.costs import round_money
from deepquant.backtest.engine import (
    REASON_ALREADY_HOLDING,
    REASON_INSUFFICIENT_CASH,
    REASON_INSUFFICIENT_POSITION,
    normalize_daily_data,
    run_backtest,
    validate_signals,
)
from deepquant.backtest.models import OrderStatus, ValuationStatus
from deepquant.market.ashare import BLOCK_LIMIT_DOWN, BLOCK_LIMIT_UP, BLOCK_NO_PRICE
from deepquant.market.rules import Side, a_share_rules
from deepquant.strategies.base import Signal, Strategy
from deepquant.strategies.ma_cross import MaCrossStrategy

DATES = pd.to_datetime(
    ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08", "2024-01-09"]
)


class FakeStrategy(Strategy):
    """确定性假策略：按预置映射逐日给出信号，其余为 HOLD。"""

    name = "fake"
    version = "0.0.0"

    def __init__(self, signal_map: dict):
        self._map = {pd.Timestamp(k): int(v) for k, v in signal_map.items()}

    @property
    def params(self) -> dict:
        return {"signals": {str(k): int(v) for k, v in self._map.items()}}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        s = pd.Series(int(Signal.HOLD), index=data.index, dtype="int64")
        for ts, value in self._map.items():
            # 只设置行情索引中存在的日期，避免 .loc 扩充导致与行情索引不一致
            if ts in s.index:
                s.loc[ts] = value
        return s


class BadIndexStrategy(Strategy):
    name = "bad_index"
    version = "0.0.0"

    @property
    def params(self) -> dict:
        return {}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        # 故意返回错位索引，验证不得依赖 pandas 自动对齐
        return pd.Series(int(Signal.HOLD), index=data.index + pd.Timedelta(days=1))


class BadValueStrategy(Strategy):
    name = "bad_value"
    version = "0.0.0"

    @property
    def params(self) -> dict:
        return {}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        s = pd.Series(int(Signal.HOLD), index=data.index)
        s.iloc[0] = 2  # 非法信号值
        return s


def make_frame(dates, opens, closes, volume=1000, as_date_column=False) -> pd.DataFrame:
    high = [max(o, c) for o, c in zip(opens, closes)]
    low = [min(o, c) for o, c in zip(opens, closes)]
    frame = pd.DataFrame(
        {
            "open": opens,
            "high": high,
            "low": low,
            "close": closes,
            "volume": [volume] * len(opens),
        },
        index=pd.to_datetime(dates),
    )
    if as_date_column:
        frame = frame.reset_index().rename(columns={"index": "date"})
    return frame


def make_config(**overrides) -> BacktestConfig:
    kwargs = dict(symbol="600000", initial_cash=100000.0)
    kwargs.update(overrides)
    return BacktestConfig(**kwargs)


# ===================================================================== #
# A. 完整交易流程（买入 → 持仓 → 卖出）
# ===================================================================== #


def test_full_buy_hold_sell_flow():
    frame = make_frame(
        DATES,
        opens=[10.00, 10.00, 11.00, 12.00, 13.00, 14.00],
        closes=[10.00, 11.00, 12.00, 13.00, 14.00, 15.00],
    )
    strategy = FakeStrategy({DATES[0]: Signal.BUY, DATES[2]: Signal.SELL})
    result = run_backtest(frame, strategy, make_config(initial_cash=100000.0))

    # 订单与成交
    assert len(result.orders) == 2
    assert len(result.fills) == 2

    buy_order = result.orders[0]
    assert buy_order.side is Side.BUY
    assert buy_order.signal_date == DATES[0]
    assert buy_order.order_date == DATES[0]
    assert buy_order.planned_exec_date == DATES[1]  # T+1 执行
    assert buy_order.quantity == 10000  # 信号日按收盘价 10 估算的目标
    assert buy_order.status is OrderStatus.FILLED

    buy_fill = result.fills[0]
    assert buy_fill.fill_date == DATES[1]
    assert buy_fill.price == pytest.approx(10.01)  # 10.00 × 1.001，按 tick 取整
    assert buy_fill.quantity == 9900  # 因费用逐手回退（10000 → 9900）
    assert buy_fill.fees.commission == pytest.approx(29.73)
    assert buy_fill.fees.stamp_duty == pytest.approx(0.0)

    # 账务：现金 = 100000 − 99099.00 − 29.73
    assert result.account_states[1].cash == pytest.approx(871.27)
    assert result.account_states[1].position.quantity == 9900
    assert result.account_states[1].market_value == pytest.approx(108900.0)
    assert result.account_states[1].total_equity == pytest.approx(109771.27)

    # 持有日权益
    assert result.account_states[2].total_equity == pytest.approx(119671.27)

    # 卖出
    sell_order = result.orders[1]
    assert sell_order.side is Side.SELL
    assert sell_order.signal_date == DATES[2]
    assert sell_order.planned_exec_date == DATES[3]
    assert sell_order.quantity == 9900
    assert sell_order.status is OrderStatus.FILLED

    sell_fill = result.fills[1]
    assert sell_fill.fill_date == DATES[3]
    assert sell_fill.price == pytest.approx(11.99)  # 12.00 × 0.999
    assert sell_fill.quantity == 9900
    assert sell_fill.fees.commission == pytest.approx(35.61)
    assert sell_fill.fees.stamp_duty == pytest.approx(59.35)  # 仅卖出方

    # 最终现金与权益
    assert result.account_states[-1].cash == pytest.approx(119477.31)
    assert result.account_states[-1].position.quantity == 0
    assert result.account_states[-1].total_equity == pytest.approx(119477.31)

    # 完成度
    assert result.is_complete is True
    assert result.incomplete_reasons == []
    assert result.data_quality_issues == []
    assert len(result.account_states) == len(DATES)


def test_date_column_input_matches_index_input():
    frame_idx = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    frame_col = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0], as_date_column=True)
    strategy = FakeStrategy({DATES[0]: Signal.BUY})
    config = make_config()
    r1 = run_backtest(frame_idx, strategy, config)
    r2 = run_backtest(frame_col, strategy, config)
    assert r1.fills[0].quantity == r2.fills[0].quantity
    assert r1.account_states[-1].cash == r2.account_states[-1].cash


# ===================================================================== #
# B. 交易规则
# ===================================================================== #


def test_buy_quantity_is_lot_multiple():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    result = run_backtest(frame, FakeStrategy({DATES[0]: Signal.BUY}), make_config())
    assert result.fills[0].quantity % 100 == 0


def test_insufficient_cash_steps_down_and_rejects():
    # 现金不足以买 1 手 → 拒绝，账户不变
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    result = run_backtest(
        frame, FakeStrategy({DATES[0]: Signal.BUY}), make_config(initial_cash=1000.0)
    )
    order = result.orders[0]
    assert order.status is OrderStatus.REJECTED
    assert order.reason == REASON_INSUFFICIENT_CASH
    assert result.fills == []
    assert result.account_states[-1].cash == pytest.approx(1000.0)
    assert result.account_states[-1].position.quantity == 0


def test_t1_buy_then_sell_signal_fills_next_day():
    # 买入于 D2 成交；D2 收盘给出 SELL 信号 → 最早 D3 成交，买入数量在 D3 已可卖（T+1 满足）
    frame = make_frame(DATES[:3], opens=[10.0, 10.0, 10.0], closes=[10.0, 10.0, 10.0])
    strategy = FakeStrategy({DATES[0]: Signal.BUY, DATES[1]: Signal.SELL})
    result = run_backtest(frame, strategy, make_config())

    buy_fill = result.fills[0]
    sell_fill = result.fills[1]
    assert buy_fill.fill_date == DATES[1]
    assert sell_fill.fill_date == DATES[2]  # 不早于买入日的下一交易日
    assert sell_fill.quantity == buy_fill.quantity == 9900
    assert result.account_states[1].position.quantity == 9900  # 买入当日仍持仓
    assert result.account_states[2].position.quantity == 0


def test_sell_when_flat_is_rejected():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    result = run_backtest(frame, FakeStrategy({DATES[0]: Signal.SELL}), make_config())
    order = result.orders[0]
    assert order.status is OrderStatus.REJECTED
    assert order.reason == REASON_INSUFFICIENT_POSITION
    assert result.fills == []
    assert result.account_states[-1].cash == pytest.approx(100000.0)


def test_second_buy_signal_while_holding_is_rejected():
    # D1 BUY 成交后持仓；D3 再次 BUY 信号 → already_holding，不重复建立持仓
    frame = make_frame(
        DATES[:4],
        opens=[10.0, 10.0, 10.0, 10.0],
        closes=[10.0, 10.0, 10.0, 10.0],
    )
    strategy = FakeStrategy({DATES[0]: Signal.BUY, DATES[2]: Signal.BUY})
    result = run_backtest(frame, strategy, make_config())
    assert len(result.orders) == 2
    assert result.orders[0].status is OrderStatus.FILLED
    assert result.orders[1].status is OrderStatus.REJECTED
    assert result.orders[1].reason == REASON_ALREADY_HOLDING
    assert len(result.fills) == 1
    assert result.account_states[-1].position.quantity == 9900


def test_configured_limit_up_blocks_buy():
    rules = a_share_rules(price_limit_rate=0.10)
    frame = make_frame(DATES[:2], opens=[10.0, 11.0], closes=[10.0, 11.0])
    result = run_backtest(
        frame,
        FakeStrategy({DATES[0]: Signal.BUY}),
        make_config(market_rules=rules),
    )
    order = result.orders[0]
    assert order.status is OrderStatus.REJECTED
    assert order.reason == BLOCK_LIMIT_UP
    assert result.fills == []
    assert result.account_states[-1].cash == pytest.approx(100000.0)


def test_configured_limit_down_blocks_sell():
    rules = a_share_rules(price_limit_rate=0.10)
    frame = make_frame(
        DATES[:3],
        opens=[10.0, 10.0, 9.0],
        closes=[10.0, 10.0, 9.0],
    )
    strategy = FakeStrategy({DATES[0]: Signal.BUY, DATES[1]: Signal.SELL})
    result = run_backtest(frame, strategy, make_config(market_rules=rules))
    sell_order = result.orders[1]
    assert sell_order.status is OrderStatus.REJECTED
    assert sell_order.reason == BLOCK_LIMIT_DOWN
    assert len(result.fills) == 1  # 只有买入成交
    assert result.account_states[-1].position.quantity == 9900


def test_unconfigured_price_limit_does_not_block():
    frame = make_frame(DATES[:2], opens=[10.0, 11.0], closes=[10.0, 11.0])
    result = run_backtest(
        frame, FakeStrategy({DATES[0]: Signal.BUY}), make_config()  # 默认未配置涨跌停
    )
    assert result.orders[0].status is OrderStatus.FILLED
    assert result.account_states[-1].position.quantity == 9000


def test_missing_open_price_blocks_buy():
    frame = make_frame(DATES[:2], opens=[10.0, math.nan], closes=[10.0, 10.0])
    result = run_backtest(frame, FakeStrategy({DATES[0]: Signal.BUY}), make_config())
    order = result.orders[0]
    assert order.status is OrderStatus.REJECTED
    assert order.reason == BLOCK_NO_PRICE
    assert result.fills == []
    assert any("开盘价缺失" in issue for issue in result.data_quality_issues)


def test_invalid_open_price_blocks_buy():
    frame = make_frame(DATES[:2], opens=[10.0, -1.0], closes=[10.0, 10.0])
    result = run_backtest(frame, FakeStrategy({DATES[0]: Signal.BUY}), make_config())
    assert result.orders[0].reason == BLOCK_NO_PRICE


def test_last_day_signal_has_no_order():
    frame = make_frame(DATES[:3], opens=[10.0, 10.0, 10.0], closes=[10.0, 10.0, 10.0])
    result = run_backtest(frame, FakeStrategy({DATES[2]: Signal.BUY}), make_config())
    assert result.orders == []
    assert result.fills == []
    assert len(result.account_states) == 3


# ===================================================================== #
# C. 账务一致性
# ===================================================================== #


def test_accounting_is_consistent_and_non_negative():
    frame = make_frame(
        DATES,
        opens=[10.00, 10.00, 11.00, 12.00, 13.00, 14.00],
        closes=[10.00, 11.00, 12.00, 13.00, 14.00, 15.00],
    )
    strategy = FakeStrategy({DATES[0]: Signal.BUY, DATES[2]: Signal.SELL})
    result = run_backtest(frame, strategy, make_config(initial_cash=100000.0))

    # 由成交记录反推现金
    expected_cash = 100000.0
    for fill in result.fills:
        gross = round_money(fill.price * fill.quantity)
        if fill.side is Side.BUY:
            expected_cash = round_money(expected_cash - gross - fill.fees.total)
        else:
            expected_cash = round_money(expected_cash + gross - fill.fees.total)
    assert expected_cash == pytest.approx(result.account_states[-1].cash)

    # 由成交记录反推持仓
    net = sum(f.quantity if f.side is Side.BUY else -f.quantity for f in result.fills)
    assert net == result.account_states[-1].position.quantity

    # 不产生负现金 / 负持仓
    for state in result.account_states:
        assert state.cash >= 0.0
        assert state.position.quantity >= 0


def test_rejected_order_does_not_change_account():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    result = run_backtest(
        frame, FakeStrategy({DATES[0]: Signal.BUY}), make_config(initial_cash=500.0)
    )
    assert result.fills == []
    for state in result.account_states:
        assert state.cash == pytest.approx(500.0)
        assert state.position.quantity == 0


def test_repeated_runs_are_deterministic():
    frame = make_frame(
        DATES,
        opens=[10.00, 10.00, 11.00, 12.00, 13.00, 14.00],
        closes=[10.00, 11.00, 12.00, 13.00, 14.00, 15.00],
    )
    strategy = FakeStrategy({DATES[0]: Signal.BUY, DATES[2]: Signal.SELL})
    config = make_config(initial_cash=100000.0)
    r1 = run_backtest(frame, strategy, config)
    r2 = run_backtest(frame, strategy, config)
    assert r1.fills == r2.fills
    assert r1.account_states == r2.account_states
    assert r1.orders == r2.orders
    assert r1.is_complete == r2.is_complete


# ===================================================================== #
# D. 估值
# ===================================================================== #


def test_empty_position_equity_is_cash():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    result = run_backtest(frame, FakeStrategy({}), make_config(initial_cash=1000.0))
    for state in result.account_states:
        assert state.market_value == 0.0
        assert state.total_equity == pytest.approx(1000.0)


def test_missing_close_marks_result_incomplete():
    frame = make_frame(DATES[:3], opens=[10.0, 10.0, 10.0], closes=[10.0, 10.0, math.nan])
    result = run_backtest(frame, FakeStrategy({DATES[0]: Signal.BUY}), make_config())

    last = result.account_states[-1]
    assert last.valuation_status is ValuationStatus.MISSING_CLOSE
    assert last.market_value is None  # 不得把持仓市值记为 0
    assert last.total_equity is None  # 不得伪造权益
    assert result.is_complete is False
    assert result.incomplete_reasons
    assert any("收盘价缺失" in issue for issue in result.data_quality_issues)


def test_multiple_consecutive_account_states():
    frame = make_frame(DATES, opens=[10.0] * 6, closes=[10.0] * 6)
    result = run_backtest(frame, FakeStrategy({}), make_config())
    assert len(result.account_states) == 6
    assert [s.date for s in result.account_states] == list(DATES)


# ===================================================================== #
# 与现有真实策略接口兼容（冒烟）
# ===================================================================== #


def test_engine_runs_with_real_ma_cross_strategy():
    dates = pd.bdate_range("2024-01-02", periods=12)
    closes = [10.0, 10.0, 10.0, 8.0, 8.0, 8.0, 12.0, 12.0, 12.0, 8.0, 8.0, 8.0]
    frame = make_frame(dates, opens=closes, closes=closes)
    result = run_backtest(frame, MaCrossStrategy(fast_window=2, slow_window=3), make_config())

    assert result.consistency_issues() == []
    assert len(result.account_states) == len(dates)
    for fill in result.fills:
        assert fill.quantity > 0
        assert fill.quantity % 100 == 0  # 遵守手数规则
    for state in result.account_states:
        assert state.cash >= 0.0
        assert state.position.quantity >= 0


# ===================================================================== #
# E. 输入校验
# ===================================================================== #


def test_normalize_rejects_duplicate_dates():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    frame.index = pd.DatetimeIndex([DATES[0], DATES[0]])
    with pytest.raises(ValueError, match="重复"):
        normalize_daily_data(frame)


def test_normalize_rejects_unsorted_dates():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    frame = frame.iloc[::-1]
    with pytest.raises(ValueError, match="升序"):
        normalize_daily_data(frame)


def test_normalize_rejects_missing_columns():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    frame = frame.drop(columns=["volume"])
    with pytest.raises(ValueError, match="缺少必需字段"):
        normalize_daily_data(frame)


def test_normalize_rejects_unparsable_date():
    frame = pd.DataFrame(
        {
            "date": ["2024-01-02", "not-a-date"],
            "open": [10.0, 10.0],
            "high": [10.0, 10.0],
            "low": [10.0, 10.0],
            "close": [10.0, 10.0],
            "volume": [1000, 1000],
        }
    )
    with pytest.raises(ValueError, match="无法解析"):
        normalize_daily_data(frame)


def test_normalize_rejects_tz_aware_dates():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    frame.index = frame.index.tz_localize("Asia/Shanghai")
    with pytest.raises(ValueError, match="无时区"):
        normalize_daily_data(frame)


def test_validate_signals_rejects_index_mismatch():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    with pytest.raises(ValueError, match="索引不一致"):
        run_backtest(frame, BadIndexStrategy(), make_config())


def test_validate_signals_rejects_illegal_value():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    with pytest.raises(ValueError, match="非法取值"):
        run_backtest(frame, BadValueStrategy(), make_config())


def test_validate_signals_accepts_signal_series():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    series = pd.Series([int(Signal.HOLD), int(Signal.HOLD)], index=frame.index)
    validated = validate_signals(series, frame.index, "s")
    assert list(validated) == [0, 0]


def test_empty_range_raises():
    frame = make_frame(DATES[:2], opens=[10.0, 10.0], closes=[10.0, 10.0])
    config = make_config(start_date="2025-01-01")
    with pytest.raises(ValueError, match="没有可用的行情数据"):
        run_backtest(frame, FakeStrategy({}), config)


def test_date_range_filter_limits_states():
    frame = make_frame(DATES, opens=[10.0] * 6, closes=[10.0] * 6)
    config = make_config(start_date="2024-01-04", end_date="2024-01-08")
    result = run_backtest(frame, FakeStrategy({}), config)
    assert len(result.account_states) == 3
    assert result.period_start == pd.Timestamp("2024-01-04")
    assert result.period_end == pd.Timestamp("2024-01-08")
