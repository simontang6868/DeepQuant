"""时钟与事件堆：确定性、不可回填、同刻顺序。"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.eventloop.clock import ClockNotStartedError, EpochClock, TimeTravelError
from deepquant.eventloop.events import Event, EventType, session_events


def _ev(ts: str, kind: EventType, **payload) -> Event:
    return Event(ts=pd.Timestamp(ts), type=kind, payload=payload)


def test_clock_rejects_backdated_event() -> None:
    clock = EpochClock()
    clock.push(_ev("2024-01-02 15:00", EventType.BAR_CLOSE))
    clock.pop()
    with pytest.raises(TimeTravelError, match="拒绝回填"):
        clock.push(_ev("2024-01-02 09:30", EventType.BAR_OPEN))


def test_clock_rejects_backwards_advance() -> None:
    clock = EpochClock(pd.Timestamp("2024-01-03"))
    with pytest.raises(TimeTravelError, match="不能倒退"):
        clock.advance_to(pd.Timestamp("2024-01-02"))


def test_same_timestamp_ordered_by_priority_not_insertion() -> None:
    """先投 MARK 再投 BAR_OPEN：同刻仍应先处理 BAR_OPEN。"""
    clock = EpochClock()
    clock.push(_ev("2024-01-02 15:00", EventType.MARK))
    clock.push(_ev("2024-01-02 15:00", EventType.BAR_OPEN))
    order = [clock.pop().type for _ in range(2)]
    assert order == [EventType.BAR_OPEN, EventType.MARK]


def test_same_priority_is_fifo() -> None:
    clock = EpochClock()
    clock.push(_ev("2024-01-02 09:30", EventType.BAR_OPEN, tag="a"))
    clock.push(_ev("2024-01-02 09:30", EventType.BAR_OPEN, tag="b"))
    first, second = clock.pop(), clock.pop()
    assert (first.payload["tag"], second.payload["tag"]) == ("a", "b")


def test_settle_before_fill_before_mark_at_same_instant() -> None:
    """结算必须早于撮合：否则当日买入会立刻变成可卖（T+1 失效）。"""
    clock = EpochClock()
    for kind in (EventType.MARK, EventType.FILL, EventType.SETTLE):
        clock.push(_ev("2024-01-02 09:00", kind))
    assert [clock.pop().type for _ in range(3)] == [
        EventType.SETTLE,
        EventType.FILL,
        EventType.MARK,
    ]


def test_now_is_undefined_until_first_pop() -> None:
    clock = EpochClock()
    with pytest.raises(ClockNotStartedError):
        _ = clock.now
    clock.push(_ev("2024-01-02 09:00", EventType.SETTLE))
    clock.pop()
    assert clock.now == pd.Timestamp("2024-01-02 09:00")


def test_processed_counter_and_type_counts() -> None:
    clock = EpochClock()
    clock.push(_ev("2024-01-02 09:00", EventType.SETTLE))
    clock.push(_ev("2024-01-02 15:00", EventType.BAR_CLOSE))
    while len(clock):
        clock.pop()
    assert clock.processed == 2
    assert clock.counts() == {"bar_close": 1, "settle": 1}


def test_session_events_expand_one_day_into_phases() -> None:
    events = session_events(pd.Timestamp("2024-01-02"))
    assert [item.type for item in events] == [
        EventType.SETTLE,
        EventType.BAR_OPEN,
        EventType.BAR_CLOSE,
        EventType.SIGNAL,
        EventType.MARK,
    ]
    assert [f"{item.ts:%H:%M}" for item in events] == [
        "09:00",
        "09:30",
        "15:00",
        "15:00",
        "15:30",
    ]
    assert all(item.ts.normalize() == pd.Timestamp("2024-01-02") for item in events)


def test_event_rejects_timezone_aware_timestamp() -> None:
    with pytest.raises(ValueError, match="无时区"):
        Event(ts=pd.Timestamp("2024-01-02 09:30", tz="Asia/Shanghai"), type=EventType.BAR_OPEN)


def test_pending_ts_reports_unprocessed_events() -> None:
    clock = EpochClock()
    clock.push(_ev("2024-01-02 15:00", EventType.BAR_CLOSE))
    clock.push(_ev("2024-01-02 09:00", EventType.SETTLE))
    assert clock.pending_ts() == [pd.Timestamp("2024-01-02 09:00"), pd.Timestamp("2024-01-02 15:00")]
