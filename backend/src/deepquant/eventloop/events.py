"""事件类型、同刻优先级与事件对象（纯数据，无 IO、无副作用）。

为什么要有"同刻优先级"
----------------------
事件驱动引擎的正确性很大程度上取决于**同一时间戳内的处理顺序**。这不是美学问题：

- ``SETTLE`` 必须先于撮合，否则当日买入的数量还没结算成"可卖"，T+1 会变成 T+0；
- ``BAR_OPEN`` 必须先于 ``BAR_CLOSE``，否则开盘价与收盘价会被混用，
  限价单可能在"当日最高价"上成交却在"开盘价"上记账；
- ``MARK`` 必须在最后，否则日终估值会把"尚未成交的订单"也算进权益。

顺序写进常量而不是散落在调用处，是为了让"改了顺序"这件事显式且可被测到。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping

import pandas as pd


class EventType(str, Enum):
    """内核处理的事件类型。"""

    # ---- 每日节律（由 feed 生成） ----
    SETTLE = "settle"  # 开盘前结算：T+1 释放、可卖数量刷新
    BAR_OPEN = "bar_open"  # 开盘：撮合"开盘即可成交"的订单
    BAR_CLOSE = "bar_close"  # 收盘：撮合"盘中触价"的限价单；随后策略出信号
    MARK = "mark"  # 日终估值快照

    # ---- 交易链路 ----
    SIGNAL = "signal"  # 策略产出的意图（尚未成为订单）
    ORDER_SUBMITTED = "order_submitted"  # 订单提交
    ORDER_ACCEPTED = "order_accepted"  # 订单被接受（通过前置校验）
    ORDER_REJECTED = "order_rejected"  # 订单被拒绝
    ORDER_CANCELLED = "order_cancelled"  # 订单撤销
    FILL = "fill"  # 成交回报


#: 同一时间戳内的事件处理顺序（数值小者先）。
PRIORITY: dict[EventType, int] = {
    EventType.SETTLE: 10,
    EventType.BAR_OPEN: 20,
    EventType.ORDER_SUBMITTED: 30,
    EventType.ORDER_ACCEPTED: 31,
    EventType.FILL: 32,
    EventType.ORDER_REJECTED: 33,
    EventType.ORDER_CANCELLED: 34,
    EventType.BAR_CLOSE: 40,
    EventType.SIGNAL: 41,
    EventType.MARK: 90,
}


@dataclass(frozen=True)
class Event:
    """一条事件。``payload`` 只读使用，内核不修改其中的容器。"""

    ts: pd.Timestamp
    type: EventType
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.ts, pd.Timestamp):
            raise TypeError(f"事件时间戳必须是 pd.Timestamp，收到 {type(self.ts)!r}")
        if pd.isna(self.ts):
            raise ValueError("事件时间戳不能为 NaT")
        if self.ts.tzinfo is not None:
            raise ValueError(f"事件时间戳必须无时区（tz-naive），收到 {self.ts}")
        if not isinstance(self.type, EventType):
            raise ValueError(f"事件类型必须是 EventType，收到 {self.type!r}")

    @property
    def priority(self) -> int:
        return PRIORITY[self.type]

    def describe(self) -> str:
        return f"{self.ts:%Y-%m-%d %H:%M} {self.type.value}"


#: 日频 bar 的时点表（仅用于把交易日展开成事件序列，不代表真实交易时段）。
#: SIGNAL 与 BAR_CLOSE 同刻：靠 PRIORITY 保证「先撮合、后出信号」，
#: 若给 SIGNAL 单独的时间戳，会让"同一时刻"变成"晚一分钟"，掩盖顺序依赖。
SESSION_TIMES: tuple[tuple[str, EventType], ...] = (
    ("09:00", EventType.SETTLE),
    ("09:30", EventType.BAR_OPEN),
    ("15:00", EventType.BAR_CLOSE),
    ("15:00", EventType.SIGNAL),
    ("15:30", EventType.MARK),
)


def session_events(trading_day: pd.Timestamp) -> list[Event]:
    """把一个交易日展开为 4 个事件（时间戳 = 交易日 + 时点）。

    只展开事件骨架，具体数据由调用方在处理时按 ``trading_day`` 取。
    """
    day = pd.Timestamp(trading_day).normalize()
    events = []
    for clock, kind in SESSION_TIMES:
        hour, minute = (int(part) for part in clock.split(":"))
        events.append(Event(ts=day + pd.Timedelta(hours=hour, minutes=minute), type=kind))
    return events
