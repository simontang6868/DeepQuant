"""回测核心数据结构（订单 / 成交 / 持仓 / 账户状态 / 回测结果）。

职责边界：
- 本模块只描述对象与**不变量**（非法数量、价格、标识、时序、估值状态等）；
- 不实现撮合、现金扣减顺序、拒绝策略等业务规则（属于 backtest.engine）；
- 复用 market 模块的 Side 枚举、sellable_quantity 等既有定义，不重复造概念。

时序约定（v1）：
- 策略在 T 日收盘后产生信号 → signal_date = T；
- 订单在此时创建 → order_date = T；
- 计划执行日 = 下一交易日 → planned_exec_date（必须晚于 signal_date）；
- 实际成交日 = Fill.fill_date。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

import pandas as pd

from deepquant.backtest.config import (
    BacktestConfig,
    require_naive_timestamp,
    require_non_negative,
    require_positive,
)
from deepquant.market.ashare import sellable_quantity
from deepquant.market.rules import MarketRules, Side
from deepquant.version import ENGINE_VERSION, RESULT_SCHEMA_VERSION

if TYPE_CHECKING:  # 仅用于类型标注，避免与 performance 模块产生运行时循环依赖
    from deepquant.performance.metrics import PerformanceMetrics


class OrderStatus(str, Enum):
    """订单状态（v1）。"""

    PENDING = "pending"  # 已创建，执行结果待定
    FILLED = "filled"  # 全部成交
    REJECTED = "rejected"  # 被拒绝（资金/持仓/价格/涨跌停等原因）
    EXPIRED = "expired"  # 在计划执行日未能执行而失效


class ValuationStatus(str, Enum):
    """每日账户估值状态。"""

    OK = "ok"  # 持仓可被可靠估值（或无持仓）
    MISSING_CLOSE = "missing_close"  # 持仓存在但收盘价缺失，无法可靠估值


@dataclass(frozen=True)
class Fees:
    """成交费用明细（金额，>= 0）。具体费率由 backtest.costs 计算。"""

    commission: float = 0.0
    stamp_duty: float = 0.0
    transfer_fee: float = 0.0

    def __post_init__(self) -> None:
        for name in ("commission", "stamp_duty", "transfer_fee"):
            require_non_negative(getattr(self, name), f"fees.{name}")

    @property
    def total(self) -> float:
        return self.commission + self.stamp_duty + self.transfer_fee


def _require_id(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} 必须是非空字符串，收到 {value!r}")
    return value


def _require_symbol(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"symbol 必须是非空字符串，收到 {value!r}")
    return value


def _require_positive_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} 必须是正整数，收到 {value!r}")
    return value


def _require_non_negative_int(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} 必须是非负整数，收到 {value!r}")
    return value


@dataclass
class Order:
    """订单（交易意图）。

    quantity 表示**请求**成交数量；实际成交数量见对应 Fill.quantity。
    """

    order_id: str
    symbol: str
    side: Side
    signal_date: pd.Timestamp
    order_date: pd.Timestamp
    planned_exec_date: pd.Timestamp
    quantity: int
    status: OrderStatus = OrderStatus.PENDING
    reason: str | None = None
    fill_id: str | None = None

    def __post_init__(self) -> None:
        self.order_id = _require_id(self.order_id, "order_id")
        self.symbol = _require_symbol(self.symbol)
        if not isinstance(self.side, Side):
            raise ValueError(f"side 必须是 Side，收到 {self.side!r}")
        self.signal_date = require_naive_timestamp(self.signal_date, "signal_date")
        self.order_date = require_naive_timestamp(self.order_date, "order_date")
        self.planned_exec_date = require_naive_timestamp(
            self.planned_exec_date, "planned_exec_date"
        )
        self.quantity = _require_positive_int(self.quantity, "quantity")
        if not isinstance(self.status, OrderStatus):
            raise ValueError(f"status 必须是 OrderStatus，收到 {self.status!r}")
        if self.fill_id is not None:
            self.fill_id = _require_id(self.fill_id, "fill_id")
        if self.reason is not None and not isinstance(self.reason, str):
            raise ValueError(f"reason 必须是字符串或 None，收到 {self.reason!r}")

        # 时序不变量：不得用 T 日或更早的价格成交
        if self.planned_exec_date <= self.signal_date:
            raise ValueError(
                f"planned_exec_date({self.planned_exec_date}) 必须晚于 signal_date({self.signal_date})"
            )
        if self.planned_exec_date < self.order_date:
            raise ValueError(
                f"planned_exec_date({self.planned_exec_date}) 不能早于 order_date({self.order_date})"
            )


@dataclass
class Fill:
    """成交记录（成交事实）。"""

    fill_id: str
    order_id: str
    symbol: str
    side: Side
    fill_date: pd.Timestamp
    price: float
    quantity: int
    fees: Fees = field(default_factory=Fees)

    def __post_init__(self) -> None:
        self.fill_id = _require_id(self.fill_id, "fill_id")
        self.order_id = _require_id(self.order_id, "order_id")
        self.symbol = _require_symbol(self.symbol)
        if not isinstance(self.side, Side):
            raise ValueError(f"side 必须是 Side，收到 {self.side!r}")
        self.fill_date = require_naive_timestamp(self.fill_date, "fill_date")
        self.price = require_positive(self.price, "price")
        self.quantity = _require_positive_int(self.quantity, "quantity")
        if not isinstance(self.fees, Fees):
            raise ValueError(f"fees 必须是 Fees，收到 {self.fees!r}")

    @property
    def gross_amount(self) -> float:
        """成交金额（不含费用）。"""
        return self.price * self.quantity


@dataclass
class Position:
    """单标的持仓。

    quantity: 总持仓数量；bought_today: 当日买入数量（T+1 下当日不可卖）。
    """

    symbol: str
    quantity: int = 0
    bought_today: int = 0
    avg_cost: float | None = None

    def __post_init__(self) -> None:
        self.symbol = _require_symbol(self.symbol)
        self.quantity = _require_non_negative_int(self.quantity, "quantity")
        self.bought_today = _require_non_negative_int(self.bought_today, "bought_today")
        if self.bought_today > self.quantity:
            raise ValueError(
                f"bought_today({self.bought_today}) 不能大于 quantity({self.quantity})"
            )
        if self.avg_cost is not None:
            self.avg_cost = require_non_negative(self.avg_cost, "avg_cost")

    def sellable(self, rules: MarketRules) -> int:
        """按市场规则计算可卖数量（复用 market.sellable_quantity）。"""
        return sellable_quantity(self.quantity, self.bought_today, rules)


@dataclass
class AccountState:
    """每日账户状态（收盘后快照）。

    估值不变量（对应 D4）：
    - 无持仓（quantity == 0）：估值状态必须为 OK，market_value == 0，total_equity == cash；
    - 有持仓且 OK：close 必须有效，market_value == quantity * close，total_equity == cash + market_value；
    - 有持仓且 MISSING_CLOSE：market_value 与 total_equity 必须为 None（**不得记为 0**），
      表示该日权益不可完整计算。
    """

    date: pd.Timestamp
    cash: float
    position: Position
    close: float | None
    market_value: float | None
    total_equity: float | None
    valuation_status: ValuationStatus

    def __post_init__(self) -> None:
        self.date = require_naive_timestamp(self.date, "date")
        self.cash = require_non_negative(self.cash, "cash")
        if not isinstance(self.position, Position):
            raise ValueError(f"position 必须是 Position，收到 {self.position!r}")
        if not isinstance(self.valuation_status, ValuationStatus):
            raise ValueError(
                f"valuation_status 必须是 ValuationStatus，收到 {self.valuation_status!r}"
            )
        self._check_valuation()

    def _check_valuation(self) -> None:
        qty = self.position.quantity

        if qty == 0:
            if self.valuation_status is not ValuationStatus.OK:
                raise ValueError("无持仓时估值状态必须为 OK（权益 = 现金，可完整计算）")
            if self.market_value != 0.0:
                raise ValueError(f"无持仓时 market_value 必须为 0，收到 {self.market_value!r}")
            if self.total_equity != self.cash:
                raise ValueError(
                    f"无持仓时 total_equity 必须等于 cash，收到 {self.total_equity!r} != {self.cash!r}"
                )
            return

        # 有持仓
        if self.valuation_status is ValuationStatus.OK:
            if self.close is None or not isinstance(self.close, (int, float)):
                raise ValueError("估值状态为 OK 且有持仓时，close 必须有效")
            close = require_positive(self.close, "close")
            expected_mv = qty * close
            if self.market_value is None or abs(self.market_value - expected_mv) > 1e-6:
                raise ValueError(
                    f"market_value({self.market_value!r}) 必须等于 quantity*close({expected_mv})"
                )
            expected_eq = self.cash + expected_mv
            if self.total_equity is None or abs(self.total_equity - expected_eq) > 1e-6:
                raise ValueError(
                    f"total_equity({self.total_equity!r}) 必须等于 cash + market_value({expected_eq})"
                )
            return

        # 有持仓但收盘价缺失
        if self.close is not None:
            raise ValueError("估值状态为 MISSING_CLOSE 时 close 必须为 None")
        if self.market_value is not None:
            raise ValueError("估值状态为 MISSING_CLOSE 时 market_value 必须为 None（不得记为 0）")
        if self.total_equity is not None:
            raise ValueError("估值状态为 MISSING_CLOSE 时 total_equity 必须为 None（不得虚构权益）")


def find_consistency_issues(orders: Sequence[Order], fills: Sequence[Fill]) -> list[str]:
    """检查订单与成交之间的一致性，返回问题描述列表（空列表表示一致）。

    覆盖：重复标识、成交引用不存在的订单、成交数量超过订单数量、
    证券代码/方向不一致、订单状态与成交关联不一致、一个订单对应多个成交。
    """
    issues: list[str] = []

    order_by_id: dict[str, Order] = {}
    for order in orders:
        if order.order_id in order_by_id:
            issues.append(f"重复的 order_id: {order.order_id}")
        order_by_id[order.order_id] = order

    fill_by_id: dict[str, Fill] = {}
    for fill in fills:
        if fill.fill_id in fill_by_id:
            issues.append(f"重复的 fill_id: {fill.fill_id}")
        fill_by_id[fill.fill_id] = fill

    fill_by_order: dict[str, Fill] = {}
    for fill in fills:
        order = order_by_id.get(fill.order_id)
        if order is None:
            issues.append(f"成交 {fill.fill_id} 引用了不存在的订单 {fill.order_id}")
            continue
        if fill.order_id in fill_by_order:
            issues.append(f"订单 {fill.order_id} 对应多个成交（v1 不支持部分成交/多次成交）")
            continue
        fill_by_order[fill.order_id] = fill
        if fill.symbol != order.symbol:
            issues.append(
                f"成交 {fill.fill_id} 与订单 {order.order_id} 的证券代码不一致"
                f"（{fill.symbol} != {order.symbol}）"
            )
        if fill.side != order.side:
            issues.append(
                f"成交 {fill.fill_id} 与订单 {order.order_id} 的方向不一致"
                f"（{fill.side.value} != {order.side.value}）"
            )
        if fill.quantity > order.quantity:
            issues.append(
                f"成交数量({fill.quantity}) 超过订单剩余数量({order.quantity})：{fill.fill_id}"
            )

    for order in orders:
        matching = fill_by_order.get(order.order_id)
        if order.status is OrderStatus.FILLED:
            if matching is None:
                issues.append(f"订单 {order.order_id} 标记为 FILLED 但没有对应成交")
            elif order.fill_id is not None and order.fill_id != matching.fill_id:
                issues.append(
                    f"订单 {order.order_id} 的 fill_id({order.fill_id}) 与实际成交({matching.fill_id}) 不一致"
                )
        else:
            if order.fill_id is not None:
                issues.append(
                    f"订单 {order.order_id} 状态为 {order.status.value} 却关联了 fill_id({order.fill_id})"
                )

    return issues


def assess_completeness(
    account_states: Sequence[AccountState],
) -> tuple[bool, list[str]]:
    """评估权益序列是否可完整计算。

    返回 (is_complete, reasons)。只要存在任何一天 total_equity 为 None，
    即视为不完整并给出原因（对应「不得将权益不可完整计算的结果标记为完整」）。
    没有任何每日状态记录时同样视为不完整。
    """
    if not account_states:
        return False, ["无每日账户状态记录，无法判断权益完整性"]

    reasons: list[str] = []
    for state in account_states:
        if state.total_equity is None:
            reasons.append(
                f"{state.date.date()} 权益不可完整计算（估值状态 {state.valuation_status.value}）"
            )
    return (len(reasons) == 0), reasons


@dataclass
class BacktestResult:
    """回测结果汇总。

    is_complete 默认为 False：在未明确评估前，不得默认宣称结果完整有效。
    评估可使用 assess_completeness(account_states)。

    可复现性与绩效（本阶段补充，均为可选，保持向后兼容）：
    - strategy：策略稳定标识与参数（由 performance.reproducibility 捕获）；
    - dataset：数据集标识与内容摘要（digest）；
    - performance：绩效指标（由 performance.metrics 计算；run_backtest 结束时会
      自动填充，作为只读汇总，不重新撮合或改变账户状态）；
    - engine_version：引擎/项目版本标识。
    """

    config: BacktestConfig
    period_start: pd.Timestamp | None = None
    period_end: pd.Timestamp | None = None
    orders: list[Order] = field(default_factory=list)
    fills: list[Fill] = field(default_factory=list)
    account_states: list[AccountState] = field(default_factory=list)
    data_quality_issues: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    is_complete: bool = False
    incomplete_reasons: list[str] = field(default_factory=list)
    schema_version: str = RESULT_SCHEMA_VERSION
    engine_version: str = ENGINE_VERSION
    strategy: dict | None = None
    dataset: dict | None = None
    performance: "PerformanceMetrics | None" = None

    def __post_init__(self) -> None:
        if not isinstance(self.config, BacktestConfig):
            raise ValueError("config 必须是 BacktestConfig 实例")
        if not isinstance(self.schema_version, str) or not self.schema_version.strip():
            raise ValueError("schema_version 必须是非空字符串")
        if not isinstance(self.engine_version, str) or not self.engine_version.strip():
            raise ValueError("engine_version 必须是非空字符串")
        for name in ("strategy", "dataset"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, dict):
                raise ValueError(f"{name} 必须是 dict 或 None，收到 {value!r}")
        if self.period_start is not None:
            self.period_start = require_naive_timestamp(self.period_start, "period_start")
        if self.period_end is not None:
            self.period_end = require_naive_timestamp(self.period_end, "period_end")
        if (
            self.period_start is not None
            and self.period_end is not None
            and self.period_start > self.period_end
        ):
            raise ValueError(
                f"period_start({self.period_start}) 不能晚于 period_end({self.period_end})"
            )

    def consistency_issues(self) -> list[str]:
        """返回订单与成交之间的一致性问题列表。"""
        return find_consistency_issues(self.orders, self.fills)
