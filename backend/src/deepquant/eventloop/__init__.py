"""事件驱动执行内核（单市场、日频起）。

定位
----
这是 L1b（事件驱动精验）的载体，用于回答向量化粗筛回答不了的问题：
**订单能不能成交、成交了多少、成交价偏了多少**。

能力边界（必须与结论一起引用，不得省略）
----------------------------------------
- 有日线数据（``data/exec_all`` / ``data/qfq_all``），**无分钟、无逐笔、无 L2 订单簿**；
- 因此"事件"是**交易日内的四个相位**（结算 / 开盘 / 收盘 / 估值），
  不是逐笔事件流；
- 成交细节由**参数化模型**给出（参与率、排队位置、冲击），
  不是对真实撮合的复现。任何"保真"表述都必须带上这一句。

模块划分：``events`` 事件与优先级、``clock`` 时钟与事件堆、``orders`` 订单状态机、
``account`` 账户记账、``matching`` 撮合、``impact`` 冲击与滑点、``kernel`` 主循环。
"""

from deepquant.eventloop.account import (
    Account,
    AccountError,
    Holding,
    InsufficientCash,
    InsufficientPosition,
    MissingPriceError,
)
from deepquant.eventloop.clock import ClockNotStartedError, EpochClock, TimeTravelError
from deepquant.eventloop.events import PRIORITY, SESSION_TIMES, Event, EventType, session_events
from deepquant.eventloop.impact import (
    FRICTIONLESS,
    SlippageMode,
    SlippageModel,
    SlippageModelError,
    sqrt_impact,
)
from deepquant.eventloop.kernel import KernelError, KernelResult, EventKernel, affordable_quantity
from deepquant.eventloop.matching import (
    BLOCKING_REASONS,
    Bar,
    LiquidityLedger,
    MatchResult,
    MatchingError,
    MatchingParams,
    match_on_close,
    match_on_open,
    new_ledger,
)
from deepquant.eventloop.orders import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    Order,
    OrderStateError,
    OrderStatus,
    OrderType,
    Transition,
)

__all__ = [
    "ALLOWED_TRANSITIONS",
    "BLOCKING_REASONS",
    "FRICTIONLESS",
    "PRIORITY",
    "SESSION_TIMES",
    "TERMINAL_STATUSES",
    "Account",
    "AccountError",
    "Bar",
    "ClockNotStartedError",
    "EpochClock",
    "Event",
    "EventKernel",
    "EventType",
    "Holding",
    "InsufficientCash",
    "InsufficientPosition",
    "KernelError",
    "KernelResult",
    "LiquidityLedger",
    "MatchResult",
    "MatchingError",
    "MatchingParams",
    "MissingPriceError",
    "Order",
    "OrderStateError",
    "OrderStatus",
    "OrderType",
    "SlippageMode",
    "SlippageModel",
    "SlippageModelError",
    "TimeTravelError",
    "Transition",
    "affordable_quantity",
    "match_on_close",
    "match_on_open",
    "new_ledger",
    "session_events",
    "sqrt_impact",
]
