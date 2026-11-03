"""券商适配层：``BrokerAdapter`` 抽象、数据模型、可注入故障的 ``MockBroker``。

本包的边界（重要）
------------------
**只做"与券商交互"**，不做撮合、不做风控判定、不做订单规划。

* 撮合 → ``deepquant.eventloop.matching``（回测口径，bar 内可及性/参与率/队列）
* 风控 → ``deepquant.execution.risk_gate.RiskGate``（交易风控闸门）
  ⚠️ 不是 ``deepquant.risk.gateway``（那是 API 层检查点网关）
* 规划 → ``deepquant.execution.planner``

把这几件事混在一起时，最先坏掉的是"谁是事实来源"：券商说成交了、撮合模型说
没成交、风控说该拒——三个答案都"有道理"，但只有一个能进台账。
"""

from deepquant.broker.base import BrokerAdapter
from deepquant.broker.errors import (
    BrokerError,
    BrokerTimeout,
    DuplicateOrder,
    NotConnected,
    OrderRejected,
    RejectCode,
    UnknownOrderState,
)
from deepquant.broker.mock import FaultScript, MockBroker
from deepquant.broker.models import (
    TERMINAL_STATUSES,
    AccountSnapshot,
    BrokerOrder,
    BrokerOrderStatus,
    Cash,
    Fill,
    OrderRequest,
    Position,
    Quote,
    TradingCalendar,
)

__all__ = [
    "BrokerAdapter",
    "BrokerError",
    "BrokerTimeout",
    "DuplicateOrder",
    "NotConnected",
    "OrderRejected",
    "RejectCode",
    "UnknownOrderState",
    "FaultScript",
    "MockBroker",
    "TERMINAL_STATUSES",
    "AccountSnapshot",
    "BrokerOrder",
    "BrokerOrderStatus",
    "Cash",
    "Fill",
    "OrderRequest",
    "Position",
    "Quote",
    "TradingCalendar",
]
