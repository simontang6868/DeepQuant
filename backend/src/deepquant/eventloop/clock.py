"""模拟时钟与确定性事件堆。

核心不变量：**时间只能前进**。

``push()`` 拒绝投递早于当前时刻的事件（``TimeTravelError``）。这不是防御性编程的洁癖——
"事件倒流"正是最典型的未来函数形态：在 t 日落盘的事件里塞一个 t-1 的逻辑，
结果看起来没错，但因果已经反了。把它变成硬错误，就不可能悄悄发生。

同刻多事件的顺序由 ``events.PRIORITY`` 决定，其次按入队序号（FIFO），
因此**同一输入必然产生同一处理序列**——回测可复现的前提。
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field

import pandas as pd

from deepquant.eventloop.events import Event, EventType


class TimeTravelError(RuntimeError):
    """试图向过去投递事件，或让时钟倒退。"""


class ClockNotStartedError(RuntimeError):
    """在尚未处理任何事件时读取当前时间。"""


@dataclass(order=True)
class _Entry:
    """事件堆条目：排序键依次为 (时间, 优先级, 入队序号)。"""

    ts: pd.Timestamp
    priority: int
    seq: int
    event: Event = field(compare=False)


class EpochClock:
    """事件堆 + 当前时间。"""

    def __init__(self, start: pd.Timestamp | None = None) -> None:
        if start is not None:
            start = pd.Timestamp(start)
            if start.tzinfo is not None:
                raise ValueError("起始时间必须无时区（tz-naive）")
        self._heap: list[_Entry] = []
        self._seq = 0
        self._now: pd.Timestamp | None = start
        self._processed = 0
        self._by_type: dict[str, int] = {}

    # ---------------- 状态 ---------------- #
    @property
    def now(self) -> pd.Timestamp:
        if self._now is None:
            raise ClockNotStartedError("尚未处理任何事件，当前时间未定义")
        return self._now

    @property
    def has_now(self) -> bool:
        return self._now is not None

    @property
    def processed(self) -> int:
        return self._processed

    def count(self, event_type: EventType) -> int:
        """已处理某类事件的条数（用于自检与报告）。"""
        return self._by_type.get(event_type.value, 0)

    def counts(self) -> dict[str, int]:
        return dict(sorted(self._by_type.items()))

    def __len__(self) -> int:
        return len(self._heap)

    # ---------------- 投递与取出 ---------------- #
    def push(self, event: Event) -> None:
        if self._now is not None and event.ts < self._now:
            raise TimeTravelError(
                f"事件时间 {event.ts} 早于当前时刻 {self._now}，"
                f"拒绝回填历史（事件类型 {event.type.value}）"
            )
        self._seq += 1
        heapq.heappush(self._heap, _Entry(event.ts, event.priority, self._seq, event))

    def push_many(self, events: list[Event]) -> None:
        for event in events:
            self.push(event)

    def peek(self) -> Event | None:
        return self._heap[0].event if self._heap else None

    def pop(self) -> Event:
        if not self._heap:
            raise IndexError("事件堆为空")
        entry = heapq.heappop(self._heap)
        if self._now is None or entry.ts > self._now:
            self._now = entry.ts
        self._processed += 1
        key = entry.event.type.value
        self._by_type[key] = self._by_type.get(key, 0) + 1
        return entry.event

    def advance_to(self, ts: pd.Timestamp) -> None:
        """把时钟推到指定时刻（不得倒退）。用于"跳过无事件的空白期"。"""
        ts = pd.Timestamp(ts)
        if self._now is not None and ts < self._now:
            raise TimeTravelError(f"时钟不能倒退：当前 {self._now}，目标 {ts}")
        self._now = ts

    def pending_ts(self) -> list[pd.Timestamp]:
        """堆内所有事件的时刻（升序），用于断言"没有残留订单被遗忘"。"""
        return sorted(entry.ts for entry in self._heap)
