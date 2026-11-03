"""事件驱动内核主循环。

与既有日线引擎（``backtest.engine``）的关系
--------------------------------------------
两者**接口相同、费率口径相同、成交假设不同**：

- 相同：都用 ``Strategy.generate_signals`` 出信号、都用 ``BacktestConfig`` 的费率、
  都是"T 日收盘信号 → 最早 T+1 开盘成交"、都做整手与 T+1、买入数量都按整手逐手回退；
- 不同：本内核多了参与率额度、排队位置、部分成交、可选的涨跌停与冲击滑点。
  把这些关掉（``participation_limit=1.0, queue_position=0, slippage=NONE``，
  且不提供涨跌停表）就退化为与日线引擎等价的无摩擦配置——这是 L1b 对齐验收的做法。

一处刻意的不一致（也是本内核的主要价值）
----------------------------------------
日线引擎在**完整样本**上一次算完信号再裁剪；本内核在**每个交易日**用
"截至当日"的截断数据重新调用策略，只取最后一个信号。

代价是 O(n²) 的重复计算；收益是**不可能偷看未来**——策略物理上拿不到未来的行。
日线引擎依赖"策略实现是因果的"这一约定（由 L1a 的静态+动态泄漏检测兜底），
本内核把这件事变成结构性保证。

订单生命周期
------------
订单在 T 日收盘后创建，``planned_exec_day = T+1``；只在执行日当天有效，
日终未成交即撤销（``expired``）。与日线引擎"一次执行机会"一致，
避免订单跨日累积让成交量凭空增长。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.costs import compute_fees, round_money
from deepquant.backtest.engine import normalize_daily_data
from deepquant.eventloop.account import Account, MissingPriceError
from deepquant.eventloop.clock import EpochClock
from deepquant.eventloop.events import EventType, session_events
from deepquant.eventloop.impact import FRICTIONLESS, SlippageModel
from deepquant.eventloop.matching import (
    Bar,
    LiquidityLedger,
    MatchResult,
    MatchingParams,
    match_on_close,
    match_on_open,
    new_ledger,
)
from deepquant.eventloop.orders import Order, OrderStatus, OrderType
from deepquant.market.ashare import affordable_lot_quantity, round_down_to_lot
from deepquant.market.rules import Side
from deepquant.strategies.base import Signal, Strategy

EXPIRED = "expired"
INSUFFICIENT_CASH = "insufficient_cash"

#: 随结果返回的口径限制。写进返回值而不是只写进文档——否则引用时一定会漏。
BASE_LIMITATIONS: tuple[str, ...] = (
    "日线数据下的事件是「交易日相位」（结算/开盘/收盘/策略决策/估值），不是逐笔事件流；"
    "不重建订单簿，排队位置是参数不是观测值。",
    "限价单只判「价格是否被 bar 的 [low, high] 覆盖」，不判盘中路径；"
    "触价成交按限价成交，不占用 bar 的更优价。",
    "部分成交由参与率额度决定，不是排队消耗的结果；额度在当日所有订单间共享。",
    "订单只在执行日（T+1）当天有效，日终未成交即撤销，不跨日累积。",
)


class KernelError(RuntimeError):
    """内核运行期的不可恢复错误（数据自相矛盾、账户不变量被破坏）。"""


@dataclass
class KernelResult:
    symbol: str
    days: list[str] = field(default_factory=list)
    equity_curve: list[dict[str, Any]] = field(default_factory=list)
    orders: list[dict[str, Any]] = field(default_factory=list)
    fills: list[dict[str, Any]] = field(default_factory=list)
    rejections: list[dict[str, Any]] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    limitations: list[str] = field(default_factory=list)
    data_quality: list[str] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)

    @property
    def equity_series(self) -> pd.Series:
        """权益曲线（剔除估值缺失日；缺失日必须单独报，不能补 0）。"""
        rows = [(row["day"], row["equity"]) for row in self.equity_curve if row["equity"] is not None]
        if not rows:
            return pd.Series(dtype=float)
        index, values = zip(*rows)
        return pd.Series(list(values), index=pd.to_datetime(list(index)), dtype=float)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "days": len(self.days),
            "stats": self.stats,
            "counters": self.counters,
            "limitations": self.limitations,
            "data_quality": self.data_quality,
            "orders": self.orders,
            "fills": self.fills,
            "rejections": self.rejections,
            "equity_curve": self.equity_curve,
        }


def affordable_quantity(cash: float, price: float, target: int, lot: int, config: BacktestConfig) -> int:
    """从 target 起按整手逐手回退，返回「成交金额 + 费用」不超过现金的最大整手数。

    与 ``backtest.engine._affordable_buy_quantity`` 同口径（含费用），
    因此无摩擦配置下两引擎的买入数量应逐笔一致。
    """
    if target < lot or price <= 0:
        return 0
    for lots in range(target // lot, 0, -1):
        quantity = lots * lot
        gross = round_money(price * quantity)
        fees = compute_fees(Side.BUY, price, quantity, config)
        if gross + fees.total <= cash + 1e-9:
            return quantity
    return 0


def _round_half_up(series: pd.Series, decimals: int = 2) -> pd.Series:
    """四舍五入到分（HALF_UP），不用 numpy 的银行家舍入——涨跌停价按官方口径进位。"""
    factor = 10**decimals
    return (series * factor + 0.5).floordiv(1) / factor


def limits_from_trading_frame(
    trading: pd.DataFrame,
    *,
    price_limit_rate: float = 0.10,
) -> pd.DataFrame:
    """从**交易层（不复权）**日线也算出封板与停牌标记。

    为什么必须用交易层
    ------------------
    涨跌停价 = 前收盘 × (1 ± 比例)，这个公式只在**不复权**价格上成立。
    用复权价算会把除权日的价格跳变误判成跌停/涨停——项目已有"除权只可能让价格下跌"的教训，
    这里的错误方向恰好会让大量除权日变成"跌停不可卖"。

    输出列：``limit_up_hit`` / ``limit_down_hit`` / ``tradable``（均为布尔或缺失）。
    首行没有前收盘，标记为缺失（不做判定），而不是默认"没封板"。
    """
    if not isinstance(trading, pd.DataFrame):
        raise KernelError(f"trading 必须是 DataFrame，收到 {type(trading)!r}")
    table = trading.copy()
    if "date" in table.columns:
        table["date"] = pd.to_datetime(table["date"], errors="coerce")
        table = table.set_index("date")
    table.index = pd.DatetimeIndex(table.index)
    table = table.sort_index()
    if table.index.has_duplicates:
        raise KernelError("交易层数据存在重复日期")
    required = {"open", "high", "low", "close", "volume"}
    missing = required - set(table.columns)
    if missing:
        raise KernelError(f"交易层数据缺少列：{sorted(missing)}")
    if not 0 < price_limit_rate < 1:
        raise KernelError(f"price_limit_rate 必须在 (0,1)，收到 {price_limit_rate}")

    prev_close = table["close"].shift(1)
    valid_prev = prev_close.notna() & (prev_close > 0)
    upper = _round_half_up(prev_close * (1.0 + price_limit_rate))
    lower = _round_half_up(prev_close * (1.0 - price_limit_rate))
    open_px = table["open"]

    up_hit = (open_px >= upper - 0.005) & valid_prev & open_px.notna()
    down_hit = (open_px <= lower + 0.005) & valid_prev & open_px.notna()
    out = pd.DataFrame(
        {
            "limit_up_hit": up_hit.astype(object).where(valid_prev & open_px.notna(), None),
            "limit_down_hit": down_hit.astype(object).where(valid_prev & open_px.notna(), None),
            "tradable": (table["volume"].fillna(0) > 0) & (open_px > 0),
        },
        index=table.index,
    )
    out["limit_up"] = upper.where(valid_prev, None)
    out["limit_down"] = lower.where(valid_prev, None)
    return out


class EventKernel:
    """单标的、日频的事件驱动执行内核。"""

    def __init__(
        self,
        *,
        strategy: Strategy,
        frame: pd.DataFrame,
        config: BacktestConfig,
        slippage: SlippageModel = FRICTIONLESS,
        matching: MatchingParams | None = None,
        limits: pd.DataFrame | None = None,
        volume_multiplier: float = 100.0,
    ) -> None:
        if not isinstance(strategy, Strategy):
            raise KernelError(f"strategy 必须是 Strategy，收到 {type(strategy)!r}")
        if not isinstance(config, BacktestConfig):
            raise KernelError(f"config 必须是 BacktestConfig，收到 {type(config)!r}")
        if not math.isfinite(volume_multiplier) or volume_multiplier <= 0:
            raise KernelError(f"volume_multiplier 必须为正，收到 {volume_multiplier!r}")

        self.strategy = strategy
        self.config = config
        self.slippage = slippage
        self.matching = matching or MatchingParams()
        self.volume_multiplier = volume_multiplier

        self._frame = normalize_daily_data(frame)
        days = list(self._frame.index)
        if config.start_date is not None:
            days = [day for day in days if day >= config.start_date]
        if config.end_date is not None:
            days = [day for day in days if day <= config.end_date]
        if not days:
            raise KernelError("回测区间内没有可用交易日")
        self._days = days
        self._limits = self._index_limits(limits)
        self._registry: list[Order] = []
        self._cash_limited: set[str] = set()

    # ---------------- 输入整理 ---------------- #
    @staticmethod
    def _index_limits(
        limits: pd.DataFrame | None,
    ) -> dict[pd.Timestamp, tuple[float | None, float | None, bool | None, bool | None, bool]]:
        if limits is None:
            return {}
        table = limits.copy()
        if "date" in table.columns:
            table["date"] = pd.to_datetime(table["date"], errors="coerce")
            table = table.set_index("date")
        table.index = pd.DatetimeIndex(table.index)
        if table.index.has_duplicates:
            raise KernelError("涨跌停表存在重复日期，拒绝静默去重")

        def opt_float(value: Any) -> float | None:
            return None if value is None or pd.isna(value) else float(value)

        def opt_bool(value: Any) -> bool | None:
            return None if value is None or pd.isna(value) else bool(value)

        out: dict[pd.Timestamp, tuple[float | None, float | None, bool | None, bool | None, bool]] = {}
        for day, row in table.iterrows():
            out[pd.Timestamp(day)] = (
                opt_float(row.get("limit_up")),
                opt_float(row.get("limit_down")),
                opt_bool(row.get("limit_up_hit")),
                opt_bool(row.get("limit_down_hit")),
                bool(row.get("tradable", True)),
            )
        return out

    def _bar(self, day: pd.Timestamp) -> Bar:
        row = self._frame.loc[day]
        if isinstance(row, pd.DataFrame):
            raise KernelError(f"{day.date()} 存在重复行情行")
        up, down, up_hit, down_hit, tradable = self._limits.get(
            day, (None, None, None, None, True)
        )
        note = "该日无涨跌停数据" if (self._limits and day not in self._limits) else ""
        return Bar(
            day=day,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]) * self.volume_multiplier,
            limit_up=up,
            limit_down=down,
            limit_up_hit=up_hit,
            limit_down_hit=down_hit,
            tradable=tradable,
            note=note,
        )

    def _history(self, day: pd.Timestamp) -> pd.DataFrame:
        """截至当日（含）的截断数据——策略能看到的全部历史。"""
        return self._frame.loc[:day]

    # ---------------- 主循环 ---------------- #
    def run(self) -> KernelResult:
        config = self.config
        symbol = config.symbol
        lot = config.market_rules.lot_size
        account = Account(cash=config.initial_cash, config=config)
        clock = EpochClock()

        result = KernelResult(symbol=symbol)
        result.limitations.extend(BASE_LIMITATIONS)
        if not self._limits:
            result.limitations.append(
                "未提供涨跌停/停牌数据：本次运行**未执行涨跌停判定**，"
                "成交结果乐观于真实市场（不得表述为『已通过涨跌停约束』）。"
            )
        if self.slippage.is_frictionless:
            result.limitations.append("滑点模型 = 无摩擦（frictionless），成交价不含冲击成本。")

        self._registry = []
        self._cash_limited = set()
        open_orders: list[Order] = []
        order_seq = 0
        reason_counter: dict[str, int] = {}
        states: list[dict[str, Any]] = []

        for index, day in enumerate(self._days):
            clock.push_many(session_events(day))
            bar = self._bar(day)
            ledger = new_ledger(bar, self.matching)

            while True:
                peeked = clock.peek()
                if peeked is None or peeked.ts.normalize() > day:
                    break
                event = clock.pop()

                if event.type is EventType.SETTLE:
                    account.settle()

                elif event.type is EventType.BAR_OPEN:
                    for order in [item for item in open_orders if item.planned_exec_day == day]:
                        self._execute(
                            match_on_open(order, bar, self.matching, ledger, self.slippage),
                            order,
                            day,
                            account,
                            ledger,
                            result,
                            reason_counter,
                        )

                elif event.type is EventType.BAR_CLOSE:
                    # 只处理限价单：市价单在开盘未成交就是没成交，
                    # 收盘再试等于凭空制造流动性（日线模型下"当天没成交"就是没成交）。
                    for order in [
                        item
                        for item in open_orders
                        if item.planned_exec_day == day
                        and not item.is_terminal
                        and item.order_type is OrderType.LIMIT
                    ]:
                        self._execute(
                            match_on_close(order, bar, self.matching, ledger, self.slippage),
                            order,
                            day,
                            account,
                            ledger,
                            result,
                            reason_counter,
                        )

                elif event.type is EventType.SIGNAL:
                    for order in open_orders:
                        if order.planned_exec_day is not None and order.planned_exec_day <= day:
                            if not order.is_terminal:
                                # 原因要分开：资金不足缩量不是"订单过期"，归到过期会污染归因
                                reason = (
                                    INSUFFICIENT_CASH
                                    if order.order_id in self._cash_limited
                                    else EXPIRED
                                )
                                order.cancel(event.ts, reason)
                                reason_counter[reason] = reason_counter.get(reason, 0) + 1
                    open_orders = [item for item in open_orders if not item.is_terminal]

                    if index + 1 < len(self._days):
                        signal = self._signal_for(day)
                        next_day = self._days[index + 1]
                        if signal is Signal.BUY and account.quantity(symbol) == 0:
                            order_seq += 1
                            price_ref = float(self._frame.loc[day, "close"])
                            target = affordable_lot_quantity(account.cash, price_ref, lot)
                            target = target if target >= lot else lot
                            order = Order(
                                order_id=f"O{order_seq}",
                                symbol=symbol,
                                side=Side.BUY,
                                order_type=OrderType.MARKET,
                                quantity=int(target),
                                created_at=event.ts,
                                planned_exec_day=next_day,
                            )
                            self._registry.append(order)
                            open_orders.append(order)
                        elif signal is Signal.SELL and account.quantity(symbol) > 0:
                            order_seq += 1
                            sellable = account.available(symbol) or account.quantity(symbol)
                            order = Order(
                                order_id=f"O{order_seq}",
                                symbol=symbol,
                                side=Side.SELL,
                                order_type=OrderType.MARKET,
                                quantity=int(sellable),
                                created_at=event.ts,
                                planned_exec_day=next_day,
                            )
                            self._registry.append(order)
                            open_orders.append(order)

                elif event.type is EventType.MARK:
                    states.append(self._mark(day, bar, account, result))

            problems = account.check_invariants()
            if problems:
                raise KernelError(f"{day.date()} 账户不变量被破坏：{problems}")

        result.days = [f"{day:%Y-%m-%d}" for day in self._days]
        result.equity_curve = states
        result.orders = [order.to_dict() for order in self._registry]
        result.rejections = [
            {"order_id": item["order_id"], "reason": item["reason"], "day": item["created_at"]}
            for item in result.orders
            if item["status"] == OrderStatus.REJECTED.value
        ]
        result.counters = clock.counts()
        result.stats = self._stats(result, reason_counter)
        result.stats["cash_limited_orders"] = sorted(self._cash_limited)
        return result

    # ---------------- 撮合落地 ---------------- #
    def _execute(
        self,
        outcome: MatchResult,
        order: Order,
        day: pd.Timestamp,
        account: Account,
        ledger: LiquidityLedger,
        result: KernelResult,
        reason_counter: dict[str, int],
    ) -> None:
        stage = outcome.detail.get("stage", "open")
        at = pd.Timestamp(day) + (
            pd.Timedelta(hours=9, minutes=30) if stage == "open" else pd.Timedelta(hours=15, minutes=0)
        )

        if not outcome.filled:
            reason_counter[outcome.reason] = reason_counter.get(outcome.reason, 0) + 1
            if outcome.blocked:
                order.reject(at, outcome.reason)
            return

        price = float(outcome.fill_price)
        quantity = outcome.filled_quantity

        if order.side is Side.BUY:
            affordable = affordable_quantity(
                account.cash, price, quantity, self.config.market_rules.lot_size, self.config
            )
            if affordable < quantity:
                # 资金不足：退还未使用的额度，避免流动性被"没成交的部分"占掉
                ledger.consumed = max(0.0, ledger.consumed - (quantity - affordable))
                # 单列标记：这不是"市场没给成交"，而是"钱不够买那么多"。
                # 混进成交率会让 L1b 把资金约束误判成流动性衰减。
                self._cash_limited.add(order.order_id)
                quantity = affordable
            if quantity < self.config.market_rules.lot_size:
                reason_counter[INSUFFICIENT_CASH] = reason_counter.get(INSUFFICIENT_CASH, 0) + 1
                order.reject(at, INSUFFICIENT_CASH)
                return

        if order.status is OrderStatus.PENDING_NEW:
            order.accept(at)
        order.record_fill(quantity, price, at)
        fees = (
            account.buy(order.symbol, quantity, price)
            if order.side is Side.BUY
            else account.sell(order.symbol, quantity, price)
        )

        slip = outcome.detail.get("slippage") or {}
        result.fills.append(
            {
                "fill_id": f"F{len(result.fills) + 1}",
                "order_id": order.order_id,
                "day": f"{day:%Y-%m-%d}",
                "side": order.side.value,
                "quantity": quantity,
                "price": price,
                "fees": {
                    "commission": fees.commission,
                    "stamp_duty": fees.stamp_duty,
                    "transfer_fee": fees.transfer_fee,
                    "total": fees.total,
                },
                "stage": stage,
                "touch": bool(outcome.detail.get("touch", False)),
                "slippage_mode": slip.get("mode"),
                "unit_impact": slip.get("unit_impact"),
                "partial": order.status is OrderStatus.PARTIALLY_FILLED,
            }
        )

    # ---------------- 策略与估值 ---------------- #
    def _signal_for(self, day: pd.Timestamp) -> Signal:
        history = self._history(day)
        signals = self.strategy.generate_signals(history)
        if not isinstance(signals, pd.Series):
            raise KernelError(f"策略 {self.strategy.name} 返回的不是 Series")
        if len(signals) != len(history):
            raise KernelError(
                f"策略 {self.strategy.name} 信号长度 {len(signals)} 与历史长度 {len(history)} 不一致"
            )
        value = int(signals.iloc[-1])
        if value not in {int(Signal.BUY), int(Signal.SELL), int(Signal.HOLD)}:
            raise KernelError(f"非法的信号取值：{value}")
        # 必须返回 Signal 枚举：与 int 用 ``is`` 比较恒为 False（曾因此让内核一笔不下单）
        return Signal(value)

    def _mark(self, day: pd.Timestamp, bar: Bar, account: Account, result: KernelResult) -> dict[str, Any]:
        record: dict[str, Any] = {"day": f"{day:%Y-%m-%d}"}
        if account.quantity(self.config.symbol) == 0:
            record.update(
                {
                    "cash": account.cash,
                    "market_value": 0.0,
                    "equity": account.cash,
                    "position": 0,
                    "valuation_status": "ok",
                }
            )
            return record
        try:
            market_value = account.market_value({self.config.symbol: bar.close})
        except MissingPriceError as exc:
            result.data_quality.append(
                f"{day:%Y-%m-%d} 收盘价缺失，持仓权益无法估值（不记为 0）：{exc}"
            )
            record.update(
                {
                    "cash": account.cash,
                    "market_value": None,
                    "equity": None,
                    "position": account.quantity(self.config.symbol),
                    "valuation_status": "missing_close",
                }
            )
            return record
        record.update(
            {
                "cash": account.cash,
                "market_value": market_value,
                "equity": round_money(account.cash + market_value),
                "position": account.quantity(self.config.symbol),
                "valuation_status": "ok",
            }
        )
        return record

    # ---------------- 统计 ---------------- #
    def _stats(self, result: KernelResult, reason_counter: dict[str, int]) -> dict[str, Any]:
        orders = result.orders
        total = len(orders)
        filled = sum(1 for item in orders if item["status"] == OrderStatus.FILLED.value)
        partial = sum(1 for item in orders if item["status"] == OrderStatus.PARTIALLY_FILLED.value)
        cancelled = sum(1 for item in orders if item["status"] == OrderStatus.CANCELLED.value)
        rejected = sum(1 for item in orders if item["status"] == OrderStatus.REJECTED.value)
        submitted = sum(int(item["quantity"]) for item in orders)
        filled_qty = sum(int(item["filled_quantity"]) for item in orders)
        # 订单级成交率的分母剔除「资金不足缩量」：那不是市场不给成交
        market_orders = [item for item in orders if item["order_id"] not in self._cash_limited]
        market_total = len(market_orders)
        market_filled = sum(
            1 for item in market_orders if item["status"] == OrderStatus.FILLED.value
        )
        return {
            "orders_total": total,
            "orders_filled": filled,
            "orders_partial": partial,
            "orders_cancelled": cancelled,
            "orders_rejected": rejected,
            "fill_rate": (filled / total) if total else None,
            # 市场性成交率：分母剔除资金不足缩量的订单，用于 L1b 判定
            "fill_rate_market": (market_filled / market_total) if market_total else None,
            "market_orders": market_total,
            "cash_limited_count": len(self._cash_limited),
            "quantity_fill_rate": (filled_qty / submitted) if submitted else None,
            "submitted_quantity": submitted,
            "filled_quantity": filled_qty,
            "fills": len(result.fills),
            "reasons": dict(sorted(reason_counter.items(), key=lambda kv: (-kv[1], kv[0]))),
            "days_with_position": sum(1 for row in result.equity_curve if row["position"]),
            "final_equity": result.equity_curve[-1]["equity"] if result.equity_curve else None,
            "valuation_missing_days": sum(
                1 for row in result.equity_curve if row["valuation_status"] != "ok"
            ),
        }
