"""Step 7.0 可信度专项测试：未来函数、执行时序、市场规则、绩效边界。

所有测试使用小规模、确定性人工行情，不依赖真实市场数据；
失败必须定位到具体逻辑（不能通过改预期来迎合实现）。

与既有 test_engine.py 的区别：
- 此处用「前后对照 / 扰动 / 区间裁剪 / 同价不同日」等手段直接验证
  「引擎没有偷看未来数据」；
- 覆盖既有测试未触及的边界：signal_lag>1、同日收盘价不可同日成交、
  费用计算与成交记录一致、权益缺失时收益口径。
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig, ExecutionPrice
from deepquant.backtest.engine import run_backtest
from deepquant.backtest.models import OrderStatus, Side
from deepquant.performance.metrics import compute_performance
from deepquant.strategies.base import Signal, Strategy


# --------------------------------------------------------------------------- #
# 公共辅助
# --------------------------------------------------------------------------- #


@dataclass
class _DailyMarket:
    dates: list
    opens: list
    closes: list
    volumes: list | None = None

    def frame(self) -> pd.DataFrame:
        dates = pd.to_datetime(self.dates)
        opens = list(self.opens)
        closes = list(self.closes)
        highs = [max(o, c) for o, c in zip(opens, closes)]
        lows = [min(o, c) for o, c in zip(opens, closes)]
        vols = self.volumes or [1000] * len(opens)
        return pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": vols},
            index=dates,
        )

    def copy_with_close(self, idx: int, value: float) -> "_DailyMarket":
        new = copy.deepcopy(self)
        new.closes[idx] = value
        return new

    def copy_with_open(self, idx: int, value: float) -> "_DailyMarket":
        new = copy.deepcopy(self)
        new.opens[idx] = value
        return new


def _config(**overrides) -> BacktestConfig:
    kwargs = dict(symbol="TEST", initial_cash=100000.0)
    kwargs.update(overrides)
    return BacktestConfig(**kwargs)


class _EventStrategy(Strategy):
    """在指定日期产出事件型信号，其余日期为 HOLD。"""

    name = "event"
    version = "1.0.0"

    def __init__(self, buy_dates=None, sell_dates=None):
        self._buy = set(pd.to_datetime(buy_dates or []))
        self._sell = set(pd.to_datetime(sell_dates or []))

    @property
    def params(self) -> dict:
        return {"buy": list(self._buy), "sell": list(self._sell)}

    def generate_signals(self, data: pd.DataFrame) -> pd.Series:
        s = pd.Series(int(Signal.HOLD), index=data.index, dtype="int64")
        for d in self._buy:
            if d in s.index:
                s.loc[d] = int(Signal.BUY)
        for d in self._sell:
            if d in s.index:
                s.loc[d] = int(Signal.SELL)
        return s


# ===========================================================================
# F1. 未来数据扰动测试（引擎层面）
#     理论：T 日收盘后引擎才生成订单，且最早 T+1 成交；若修改 T+k(k>0) 的行情，
#     那么 T 及之前创建的订单 / 成交 / 状态都不得改变。
# ===========================================================================


def test_future_close_does_not_change_past_orders() -> None:
    """未来收盘价改变 → 已创建的订单（order_date <= T）完全不变。"""
    market = _DailyMarket(
        dates=pd.date_range("2024-01-02", periods=6, freq="B"),
        opens=[10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
        closes=[10.0, 10.0, 10.0, 10.0, 10.0, 10.0],
    )
    strat = _EventStrategy(buy_dates=["2024-01-03"])  # 2024-01-03 收盘后产生 BUY 订单
    cfg = _config()

    base = run_backtest(market.frame(), strat, cfg)

    # 扰动 2024-01-05 和 2024-01-06（都晚于信号日）的收盘价
    perturbed = market.copy_with_close(4, 500.0).copy_with_close(5, 500.0).frame()
    altered = run_backtest(perturbed, strat, cfg)

    # 信号日在 2024-01-03，订单 order_date 必须是 2024-01-03
    assert base.orders[0].order_date == pd.Timestamp("2024-01-03")
    # 未来数据改变，订单列表在时间 ≤ 信号日的都不得变化
    for o_base, o_altered in zip(base.orders, altered.orders):
        assert o_base.signal_date == o_altered.signal_date
        assert o_base.planned_exec_date == o_altered.planned_exec_date
        assert o_base.quantity == o_altered.quantity
        assert o_base.status == o_altered.status


def test_future_open_does_not_change_past_fills() -> None:
    """未来（T+2）开盘价改变 → T+1 已成交的 Fill 不变。"""
    market = _DailyMarket(
        dates=pd.date_range("2024-01-02", periods=5, freq="B"),
        opens=[10.0, 10.0, 10.0, 10.0, 10.0],
        closes=[10.0, 10.0, 10.0, 10.0, 10.0],
    )
    strat = _EventStrategy(buy_dates=["2024-01-02"], sell_dates=["2024-01-03"])
    cfg = _config()

    base = run_backtest(market.frame(), strat, cfg)
    # 信号 1/2 收盘 → BUY order_date=1/2 planned=1/3，fill_date=1/3
    # 信号 1/3 收盘 → SELL order_date=1/3 planned=1/4，fill_date=1/4
    assert len(base.fills) == 2
    assert base.fills[0].fill_date == pd.Timestamp("2024-01-03")
    assert base.fills[1].fill_date == pd.Timestamp("2024-01-04")

    # 扰动 1/5 的开盘价（晚于最后一个成交日）
    perturbed = market.copy_with_open(4, 500.0).frame()
    altered = run_backtest(perturbed, strat, cfg)

    assert len(altered.fills) == len(base.fills)
    for fb, fa in zip(base.fills, altered.fills):
        assert fb.fill_date == fa.fill_date
        assert fb.side == fa.side
        assert fb.quantity == fa.quantity
        assert fb.price == fa.price


def test_adding_future_data_does_not_change_past_signals_and_fills() -> None:
    """在尾部追加更多行情 → 历史日期的订单/成交完全一致。"""
    market = _DailyMarket(
        dates=pd.date_range("2024-01-02", periods=5, freq="B"),
        opens=[10.0] * 5,
        closes=[10.0] * 5,
    )
    strat = _EventStrategy(buy_dates=["2024-01-02"])
    cfg = _config()

    base = run_backtest(market.frame(), strat, cfg)

    extended = pd.concat(
        [
            market.frame(),
            pd.DataFrame(
                {
                    "open": [10.0, 10.0, 10.0],
                    "high": [10.0, 10.0, 10.0],
                    "low": [10.0, 10.0, 10.0],
                    "close": [10.0, 10.0, 10.0],
                    "volume": [1000, 1000, 1000],
                },
                index=pd.date_range("2024-01-09", periods=3, freq="B"),
            ),
        ]
    )
    altered = run_backtest(extended, strat, cfg)

    # 历史区间内订单和成交的数量与字段必须一致
    past_orders_base = [o for o in base.orders if o.order_date <= pd.Timestamp("2024-01-06")]
    past_orders_alt = [o for o in altered.orders if o.order_date <= pd.Timestamp("2024-01-06")]
    assert len(past_orders_base) == len(past_orders_alt)
    for ob, oa in zip(past_orders_base, past_orders_alt):
        assert ob.quantity == oa.quantity
        assert ob.side == oa.side
        assert ob.status == oa.status
        assert ob.planned_exec_date == oa.planned_exec_date


def test_past_order_is_not_affected_by_future_t2_pending() -> None:
    """T 日 BUY 已在 T+1 成交 → T+2 后续 SELL 信号的订单不得回看 T+2 之后的数据。"""
    dates = pd.to_datetime(
        ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]
    )

    def run(opens: list, closes: list):
        frame = pd.DataFrame(
            {
                "open": opens,
                "high": [max(o, c) for o, c in zip(opens, closes)],
                "low": [min(o, c) for o, c in zip(opens, closes)],
                "close": closes,
                "volume": [1000] * 5,
            },
            index=dates,
        )
        strat = _EventStrategy(buy_dates=["2024-01-02"], sell_dates=["2024-01-04"])
        return run_backtest(frame, strat, _config())

    # 基准
    base = run([10.0] * 5, [10.0] * 5)
    # 扰动 1/8（最后一天）的收盘 → 不得影响 1/4 收盘产生的 SELL 订单
    alt = run([10.0] * 5, [10.0, 10.0, 10.0, 10.0, 999.0])

    # 两份的第二个订单（SELL）的 order_date / planned_exec_date / quantity / side 完全相同
    assert len(base.orders) == 2 == len(alt.orders)
    o_base_sell = base.orders[1]
    o_alt_sell = alt.orders[1]
    assert o_base_sell.order_date == o_alt_sell.order_date
    assert o_base_sell.planned_exec_date == o_alt_sell.planned_exec_date
    assert o_base_sell.quantity == o_alt_sell.quantity


# ===========================================================================
# F2. 区间外数据隔离：start_date / end_date 裁剪后，区间内的信号/成交
#     必须与「只给区间内数据」的结果一致。
# ===========================================================================


def test_date_range_cutoff_is_same_as_pure_frame() -> None:
    """带 start_date 裁剪的结果 = 只给裁剪后行情的结果（绝不偷看区间外）。"""
    dates = pd.date_range("2024-01-02", periods=10, freq="B")
    opens = closes = [10.0] * 10
    frame = pd.DataFrame(
        {
            "open": opens,
            "high": [max(o, c) for o, c in zip(opens, closes)],
            "low": [min(o, c) for o, c in zip(opens, closes)],
            "close": closes,
            "volume": [1000] * 10,
        },
        index=dates,
    )

    strat = _EventStrategy(buy_dates=["2024-01-05"])
    start = pd.Timestamp("2024-01-05")
    end = pd.Timestamp("2024-01-09")

    full_with_range = run_backtest(frame, strat, _config(start_date=start, end_date=end))
    sliced = run_backtest(
        frame.loc[start:end].copy(), strat, _config(start_date=None, end_date=None)
    )

    # 订单数量、planned_exec_date、成交数量/日期必须一致
    assert len(full_with_range.orders) == len(sliced.orders)
    for a, b in zip(full_with_range.orders, sliced.orders):
        assert a.signal_date == b.signal_date
        assert a.planned_exec_date == b.planned_exec_date
        assert a.quantity == b.quantity
    assert len(full_with_range.fills) == len(sliced.fills)
    for a, b in zip(full_with_range.fills, sliced.fills):
        assert a.fill_date == b.fill_date
        assert a.price == b.price
        assert a.quantity == b.quantity


# ===========================================================================
# E1. 执行时序：信号(T) → planned_exec_date(T+1)，且成交基准价是 T+1 的开盘价
#     （不是 T 的收盘价）。
# ===========================================================================


def test_fill_price_is_next_open_not_same_close() -> None:
    """信号 T 日收盘，最早 T+1 成交；成交价 = T+1 开盘价（经滑点），绝不是 T 日收盘。"""
    market = _DailyMarket(
        dates=pd.date_range("2024-01-02", periods=4, freq="B"),
        opens=[99.0, 101.0, 99.0, 101.0],  # T+1 开盘明显不同于 T 收盘
        closes=[100.0, 102.0, 100.0, 102.0],  # T 收盘 = 100，T+1 开盘 = 101
    )
    strat = _EventStrategy(buy_dates=["2024-01-02"])  # 信号 T = 2024-01-02
    cfg = _config(slippage_buy=0.0, slippage_sell=0.0)  # 关掉滑点，便于断言

    result = run_backtest(market.frame(), strat, cfg)
    assert result.fills[0].fill_date == pd.Timestamp("2024-01-03")
    # 成交价必须是 T+1 的 open = 101.0，不是 T 的 close = 100.0
    assert math.isclose(result.fills[0].price, 101.0)
    assert not math.isclose(result.fills[0].price, 100.0)


def test_order_date_equals_signal_date_and_planned_exec_date_is_next_day() -> None:
    """订单时序不变量：signal_date == order_date，planned_exec_date > order_date。"""
    market = _DailyMarket(
        dates=pd.date_range("2024-01-02", periods=5, freq="B"),
        opens=[10.0] * 5,
        closes=[10.0] * 5,
    )
    strat = _EventStrategy(buy_dates=["2024-01-02"], sell_dates=["2024-01-04"])
    result = run_backtest(market.frame(), strat, _config())

    for order in result.orders:
        assert order.order_date == order.signal_date
        assert order.planned_exec_date > order.order_date
        assert order.planned_exec_date == pd.Timestamp(order.order_date) + pd.Timedelta(days=1)


# ===========================================================================
# E2. T+1、现金不足、持仓不足（已覆盖过，但加一条：买单执行失败时，
#     第二天不会被系统自动重试 —— 订单状态保持 rejected。）
# ===========================================================================


def test_rejected_order_is_not_retried_next_day() -> None:
    """T+1 因开盘价缺失被拒绝 → 订单保持 REJECTED，后续不再尝试。"""
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    opens = [10.0, float("nan"), 10.0, 10.0]  # 1/3 开盘价缺失
    closes = [10.0, 10.0, 10.0, 10.0]
    frame = pd.DataFrame(
        {
            "open": opens,
            "high": [max(o if math.isfinite(o) else 0, c) for o, c in zip(opens, closes)],
            "low": [min(o if math.isfinite(o) else 0, c) for o, c in zip(opens, closes)],
            "close": closes,
            "volume": [1000] * 4,
        },
        index=dates,
    )
    strat = _EventStrategy(buy_dates=["2024-01-02"])
    result = run_backtest(frame, strat, _config())

    assert len(result.orders) == 1
    order = result.orders[0]
    assert order.planned_exec_date == pd.Timestamp("2024-01-03")
    assert order.status == OrderStatus.REJECTED
    # 不会在 1/4 再追加一条 BUY 订单（订单数仍为 1）
    assert len(result.orders) == 1
    assert len(result.fills) == 0


# ===========================================================================
# M1. 费用与滑点的方向 / 最低佣金边界
# ===========================================================================


def test_buy_slippage_increases_execution_price() -> None:
    """买入滑点为正 → 实际成交价 > 当日 open（基准价）。"""
    market = _DailyMarket(
        dates=pd.date_range("2024-01-02", periods=3, freq="B"),
        opens=[10.0, 100.0, 10.0],
        closes=[10.0, 10.0, 10.0],
    )
    strat = _EventStrategy(buy_dates=["2024-01-02"])
    cfg = _config(slippage_buy=0.01, slippage_sell=0.0)

    result = run_backtest(market.frame(), strat, cfg)
    fill = result.fills[0]
    # 基准价 = 100，滑点 1% → 至少 101.00
    assert fill.price > 100.0
    assert fill.price >= 100 * 1.01  # 不严格等于，因为会按 tick 取整


def test_sell_slippage_reduces_execution_price() -> None:
    """卖出滑点为正 → 实际成交价 < 当日 open。"""
    market = _DailyMarket(
        dates=pd.date_range("2024-01-02", periods=4, freq="B"),
        opens=[10.0, 10.0, 80.0, 10.0],  # T+1 open = 80
        closes=[10.0, 10.0, 10.0, 10.0],
    )
    strat = _EventStrategy(buy_dates=["2024-01-02"], sell_dates=["2024-01-03"])
    cfg = _config(slippage_buy=0.0, slippage_sell=0.05)

    result = run_backtest(market.frame(), strat, cfg)
    # 卖出 fill 的基准价 = 80，滑点 5% → 成交价 ≤ 80 * 0.95
    sell = [f for f in result.fills if f.side == Side.SELL][0]
    assert sell.price < 80.0
    assert sell.price <= 80 * 0.95


def test_min_commission_is_applied_on_tiny_trade() -> None:
    """成交额极小（比例佣金 < min_commission）时按最低佣金执行。

    这里用 close ≈ open 的平稳行情（T 收盘 ≈ T+1 开盘），避免 target 估算偏差；
    成交额小到比例佣金 4.0 < 最低 5.0。
    """
    market = _DailyMarket(
        dates=pd.date_range("2024-01-02", periods=3, freq="B"),
        opens=[10.0, 10.0, 10.0],
        closes=[10.0, 10.0, 10.0],
    )
    strat = _EventStrategy(buy_dates=["2024-01-02"])
    # 初始现金极小，只够买 1 手 100 股 → 成交额 = 1000；比例佣金 0.4 < 最低 5.0
    cfg = _config(
        initial_cash=1010.0,
        commission_rate=0.0004,
        min_commission=5.0,
        slippage_buy=0.0,
    )

    result = run_backtest(market.frame(), strat, cfg)
    fill = result.fills[0]
    # 数量 = 1 手 = 100 股；比例佣金 ≈ 0.4 → 最低佣金 5.0
    assert fill.quantity == 100
    assert fill.fees.commission == 5.0


def test_target_buy_quantity_is_based_on_signal_day_close_not_next_open() -> None:
    """买单数量 target 估算基于信号日收盘价（不是未来成交价）。

    验证策略：把 T+1 开盘价改得极端，若估算偷看了未来 → 订单.quantity 会变；
    若估算只看 T 日收盘 → 订单.quantity 应保持不变（执行时逐手回退可以不同，
    但那是「已知价格」下的决策，不属未来函数）。
    """
    market = _DailyMarket(
        dates=pd.date_range("2024-01-02", periods=4, freq="B"),
        opens=[10.0, 10.0, 10.0, 10.0],
        closes=[10.0, 10.0, 10.0, 10.0],
    )
    strat = _EventStrategy(buy_dates=["2024-01-02"])
    cfg = _config(slippage_buy=0.0, initial_cash=1000000.0)

    base = run_backtest(market.frame(), strat, cfg)
    order_qty_base = base.orders[0].quantity

    # 把 T+1 开盘改得极端低（5.0），但 T 日收盘仍为 10
    alt = run_backtest(market.copy_with_open(1, 5.0).frame(), strat, cfg)
    order_qty_alt = alt.orders[0].quantity

    # 订单 quantity（target）必须只看信号日收盘价，不随 T+1 开盘变化
    assert order_qty_alt == order_qty_base
    # 成交 quantity 可以不同（执行时已知价格，逐手回退逻辑正常工作）
    assert alt.fills[0].quantity != base.fills[0].quantity


def test_stamp_duty_only_on_sell() -> None:
    """印花税只在卖出侧收取；买入侧为 0。"""
    market = _DailyMarket(
        dates=pd.date_range("2024-01-02", periods=4, freq="B"),
        opens=[10.0] * 4,
        closes=[10.0] * 4,
    )
    strat = _EventStrategy(buy_dates=["2024-01-02"], sell_dates=["2024-01-03"])
    cfg = _config(stamp_duty_rate=0.001, slippage_buy=0.0, slippage_sell=0.0)

    result = run_backtest(market.frame(), strat, cfg)
    buy = [f for f in result.fills if f.side == Side.BUY][0]
    sell = [f for f in result.fills if f.side == Side.SELL][0]
    assert buy.fees.stamp_duty == 0.0
    assert sell.fees.stamp_duty > 0


# ===========================================================================
# P1. 绩效边界：无交易、权益缺失、单交易日
# ===========================================================================


def test_no_trade_performance_is_pure_cash() -> None:
    """全程无交易 → 权益 = initial_cash；收益率曲线全为 0（或首日为 None）。"""
    market = _DailyMarket(
        dates=pd.date_range("2024-01-02", periods=5, freq="B"),
        opens=[10.0] * 5,
        closes=[10.0] * 5,
    )
    strat = _EventStrategy()  # 无任何信号
    result = run_backtest(market.frame(), strat, _config())

    perf = result.performance
    assert perf.final_equity == 100000.0
    assert perf.cumulative_return == 0.0
    assert perf.trade_stats.n_closed_trades == 0
    assert perf.trade_stats.win_rate is None
    # 每个 AccountState.market_value 应为 0（position.quantity == 0）
    assert all(s.market_value == 0.0 for s in result.account_states)


def test_missing_close_does_not_inflate_equity() -> None:
    """收盘价缺失的日期不把持仓市值记为 0 也不虚构权益，result 标记不完整。"""
    dates = pd.date_range("2024-01-02", periods=4, freq="B")
    opens = [10.0, 10.0, 10.0, 10.0]
    closes = [10.0, 10.0, float("nan"), 10.0]  # 第三天收盘缺失
    frame = pd.DataFrame(
        {
            "open": opens,
            "high": [10.0] * 4,
            "low": [10.0] * 4,
            "close": closes,
            "volume": [1000] * 4,
        },
        index=dates,
    )
    strat = _EventStrategy(buy_dates=["2024-01-02"])
    result = run_backtest(frame, strat, _config(slippage_buy=0.0))

    # 第三天 AccountState 有 position 但 market_value=None, total_equity=None
    state_missing = result.account_states[2]
    assert state_missing.position.quantity > 0
    assert state_missing.market_value is None
    assert state_missing.total_equity is None


def test_single_day_returns_volatility_unavailable() -> None:
    """仅 1 个交易日 → 年化波动率不可计算（需要至少 2 个日收益率）。"""
    market = _DailyMarket(
        dates=pd.date_range("2024-01-02", periods=1, freq="B"),
        opens=[10.0],
        closes=[10.0],
    )
    strat = _EventStrategy()
    result = run_backtest(market.frame(), strat, _config())

    perf = result.performance
    assert perf.annualized_volatility is None
    # 至少有 1 个 DailyReturn（初始权益 → 初始权益 = 0.0）
    assert len(perf.daily_returns) == 1


def test_performance_compute_performance_is_read_only() -> None:
    """compute_performance 不得修改 BacktestResult 的字段。"""
    market = _DailyMarket(
        dates=pd.date_range("2024-01-02", periods=3, freq="B"),
        opens=[10.0] * 3,
        closes=[10.0] * 3,
    )
    strat = _EventStrategy(buy_dates=["2024-01-02"])
    result = run_backtest(market.frame(), strat, _config())

    snapshot_fills = [f.quantity for f in result.fills]
    snapshot_cash = result.account_states[0].cash
    compute_performance(result)

    assert [f.quantity for f in result.fills] == snapshot_fills
    assert result.account_states[0].cash == snapshot_cash
