"""订单与订单状态机。

为什么要有显式状态机
--------------------
订单的状态转移如果只是"随手改个字段"，最容易出的错是**从终态又被改回来**：
一笔已经全部成交的订单，被后续回报改成"部分成交"，持仓就会多算一次。
这类错误在日志里看不出来（每一步都"合法"），只有把转移表写成常量、
让非法转移直接抛异常，才在源头挡住。

时间约束同理：任何一次状态转移的时间不得早于上一次——回报不可能比事件本身更早。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import pandas as pd

from deepquant.market.rules import Side


class OrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"


class OrderStatus(str, Enum):
    PENDING_NEW = "pending_new"  # 已提交，尚未通过校验
    ACCEPTED = "accepted"  # 已接受，等待撮合
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


#: 终态：不可再转移。
TERMINAL_STATUSES: frozenset[OrderStatus] = frozenset(
    {OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED}
)

#: 合法转移表。PARTIALLY_FILLED → PARTIALLY_FILLED 允许（多次部分成交）。
ALLOWED_TRANSITIONS: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING_NEW: frozenset(
        {OrderStatus.ACCEPTED, OrderStatus.REJECTED, OrderStatus.CANCELLED}
    ),
    OrderStatus.ACCEPTED: frozenset(
        {
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        }
    ),
    OrderStatus.PARTIALLY_FILLED: frozenset(
        {OrderStatus.PARTIALLY_FILLED, OrderStatus.FILLED, OrderStatus.CANCELLED}
    ),
    OrderStatus.FILLED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
    OrderStatus.REJECTED: frozenset(),
}


class OrderStateError(RuntimeError):
    """非法状态转移、时间倒退或数量越界。"""


@dataclass(frozen=True)
class Transition:
    at: pd.Timestamp
    status: OrderStatus
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"at": f"{self.at:%Y-%m-%d %H:%M}", "status": self.status.value, "reason": self.reason}


@dataclass
class Order:
    """一笔订单。数量单位为**股**（不是手）。"""

    order_id: str
    symbol: str
    side: Side
    order_type: OrderType
    quantity: int
    created_at: pd.Timestamp
    limit_price: float | None = None
    #: 计划执行日。订单只在执行日当天有效，日终未成交即撤销（与日线引擎同语义）。
    planned_exec_day: pd.Timestamp | None = None
    status: OrderStatus = OrderStatus.PENDING_NEW
    filled_quantity: int = 0
    filled_notional: float = 0.0
    reason: str = ""
    history: list[Transition] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not isinstance(self.order_id, str) or not self.order_id.strip():
            raise ValueError("order_id 必须是非空字符串")
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol 必须是非空字符串")
        if not isinstance(self.side, Side):
            raise ValueError(f"side 必须是 Side，收到 {self.side!r}")
        if not isinstance(self.order_type, OrderType):
            raise ValueError(f"order_type 必须是 OrderType，收到 {self.order_type!r}")
        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity <= 0:
            raise ValueError(f"quantity 必须是正整数（股），收到 {self.quantity!r}")
        if self.order_type is OrderType.LIMIT:
            if self.limit_price is None:
                raise ValueError("限价单必须给出 limit_price")
            if not math.isfinite(self.limit_price) or self.limit_price <= 0:
                raise ValueError(f"limit_price 必须是有限正数，收到 {self.limit_price!r}")
        elif self.limit_price is not None:
            raise ValueError("市价单不应携带 limit_price")
        if not self.history:
            self.history.append(Transition(pd.Timestamp(self.created_at), self.status, "created"))

    # ---------------- 派生量 ---------------- #
    @property
    def remaining(self) -> int:
        return self.quantity - self.filled_quantity

    @property
    def avg_fill_price(self) -> float | None:
        if self.filled_quantity <= 0:
            return None
        return self.filled_notional / self.filled_quantity

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    @property
    def last_transition_at(self) -> pd.Timestamp:
        return self.history[-1].at

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "order_type": self.order_type.value,
            "quantity": self.quantity,
            "limit_price": self.limit_price,
            "planned_exec_day": f"{self.planned_exec_day:%Y-%m-%d}" if self.planned_exec_day is not None else None,
            "status": self.status.value,
            "filled_quantity": self.filled_quantity,
            "avg_fill_price": self.avg_fill_price,
            "created_at": f"{self.created_at:%Y-%m-%d %H:%M}",
            "reason": self.reason,
            "history": [item.to_dict() for item in self.history],
        }

    # ---------------- 状态转移 ---------------- #
    def transition(self, to: OrderStatus, at: pd.Timestamp, reason: str = "") -> None:
        """执行一次状态转移；非法转移或时间倒退抛 ``OrderStateError``。"""
        at = pd.Timestamp(at)
        if at.tzinfo is not None:
            raise OrderStateError("状态转移时间必须无时区（tz-naive）")
        if at < self.last_transition_at:
            raise OrderStateError(
                f"状态转移时间倒退：{self.order_id} 上一步 {self.last_transition_at}，本次 {at}"
            )
        allowed = ALLOWED_TRANSITIONS[self.status]
        if to not in allowed:
            raise OrderStateError(
                f"非法状态转移：{self.order_id} {self.status.value} → {to.value}"
                f"（允许：{sorted(item.value for item in allowed) or '无，已是终态'}）"
            )
        self.status = to
        self.reason = reason or self.reason
        self.history.append(Transition(at, to, reason))

    def accept(self, at: pd.Timestamp, reason: str = "") -> None:
        self.transition(OrderStatus.ACCEPTED, at, reason)

    def reject(self, at: pd.Timestamp, reason: str) -> None:
        self.transition(OrderStatus.REJECTED, at, reason)

    def cancel(self, at: pd.Timestamp, reason: str = "") -> None:
        self.transition(OrderStatus.CANCELLED, at, reason)

    def record_fill(self, quantity: int, price: float, at: pd.Timestamp) -> None:
        """登记一次成交：数量累加、均价重算、状态自动推进到部分成交或全部成交。"""
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise OrderStateError(f"成交数量必须是正整数，收到 {quantity!r}")
        if not math.isfinite(price) or price <= 0:
            raise OrderStateError(f"成交价必须是有限正数，收到 {price!r}")
        if quantity > self.remaining:
            raise OrderStateError(
                f"成交数量 {quantity} 超过剩余 {self.remaining}（订单 {self.order_id}）"
            )
        if self.status is not OrderStatus.ACCEPTED and self.status is not OrderStatus.PARTIALLY_FILLED:
            raise OrderStateError(
                f"订单当前状态 {self.status.value} 不可成交（{self.order_id}）"
            )
        self.filled_quantity += quantity
        self.filled_notional += quantity * price
        target = (
            OrderStatus.FILLED if self.filled_quantity == self.quantity else OrderStatus.PARTIALLY_FILLED
        )
        self.transition(target, at, f"fill {quantity}@{price}")
