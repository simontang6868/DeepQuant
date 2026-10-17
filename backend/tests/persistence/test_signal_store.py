"""Step 8.2' 信号级持久化的测试。

覆盖：无损往返（日期 / 方向 / 强度 / 有效期 / metadata）、
按 batch / source / 日期区间过滤、计数、limit、空库读取、参数校验。

所有用例都用 `tmp_path` 里的独立数据库，不触碰项目默认数据库。
"""

from __future__ import annotations

import pandas as pd
import pytest

from deepquant.persistence import (
    count_signal_events,
    list_signal_events,
    save_signal_events,
)
from deepquant.persistence.store import PersistenceError
from deepquant.strategies.base import Signal
from deepquant.strategies.signals import SignalEvent


def _event(
    day: str,
    *,
    direction: Signal = Signal.BUY,
    strength: float = 1.0,
    source: str = "ma_cross",
    valid_until: str | None = None,
    metadata: dict | None = None,
) -> SignalEvent:
    return SignalEvent(
        date=pd.Timestamp(day),
        direction=direction,
        strength=strength,
        source=source,
        valid_until=pd.Timestamp(valid_until) if valid_until else None,
        metadata=dict(metadata) if metadata else {},
    )


def _db(tmp_path) -> "object":
    return tmp_path / "signals.db"


# ---------------------------------------------------------------- 保存


def test_save_returns_generated_batch_id(tmp_path):
    batch = save_signal_events([_event("2024-01-02")], db_path=_db(tmp_path))

    assert isinstance(batch, str)
    assert len(batch) > 0


def test_save_uses_explicit_batch_id(tmp_path):
    batch = save_signal_events(
        [_event("2024-01-02")], batch_id="batch-A", db_path=_db(tmp_path)
    )

    assert batch == "batch-A"


def test_save_empty_events_raises(tmp_path):
    with pytest.raises(ValueError, match="events"):
        save_signal_events([], db_path=_db(tmp_path))


def test_save_rejects_non_signal_event(tmp_path):
    with pytest.raises(TypeError, match="SignalEvent"):
        save_signal_events([_event("2024-01-02"), "not-an-event"], db_path=_db(tmp_path))


def test_save_rejects_empty_batch_id(tmp_path):
    with pytest.raises(ValueError, match="batch_id"):
        save_signal_events([_event("2024-01-02")], batch_id="", db_path=_db(tmp_path))


def test_save_rejects_unserializable_metadata(tmp_path):
    event = _event("2024-01-02", metadata={"bad": {1, 2}})

    with pytest.raises(ValueError, match="metadata"):
        save_signal_events([event], db_path=_db(tmp_path))


# ---------------------------------------------------------------- 无损往返


def test_roundtrip_preserves_all_fields(tmp_path):
    event = _event(
        "2024-01-02",
        direction=Signal.SELL,
        strength=0.42,
        source="mr_zscore",
        valid_until="2024-01-03",
        metadata={"z": -1.75, "tag": "测试", "nested": {"a": [1, 2, 3]}},
    )

    save_signal_events([event], batch_id="b1", db_path=_db(tmp_path))
    (restored,) = list_signal_events(batch_id="b1", db_path=_db(tmp_path))

    assert restored.date == event.date
    assert int(restored.direction) == int(event.direction)
    assert restored.strength == pytest.approx(event.strength)
    assert restored.source == event.source
    assert restored.valid_until == event.valid_until
    assert restored.metadata == event.metadata


def test_direction_restored_as_signal_enum(tmp_path):
    save_signal_events(
        [_event("2024-01-02", direction=Signal.SELL)],
        batch_id="b1",
        db_path=_db(tmp_path),
    )
    (restored,) = list_signal_events(db_path=_db(tmp_path))

    assert isinstance(restored.direction, Signal)
    assert restored.direction is Signal.SELL


def test_hold_direction_persisted(tmp_path):
    save_signal_events(
        [_event("2024-01-02", direction=Signal.HOLD)],
        batch_id="b1",
        db_path=_db(tmp_path),
    )
    (restored,) = list_signal_events(db_path=_db(tmp_path))

    assert restored.direction is Signal.HOLD
    assert restored.is_actionable is False


def test_valid_until_none_roundtrip(tmp_path):
    save_signal_events(
        [_event("2024-01-02", valid_until=None)], batch_id="b1", db_path=_db(tmp_path)
    )
    (restored,) = list_signal_events(db_path=_db(tmp_path))

    assert restored.valid_until is None


# ---------------------------------------------------------------- 检索与过滤


def test_list_returns_empty_on_fresh_db(tmp_path):
    assert list_signal_events(db_path=_db(tmp_path)) == []


def test_count_zero_on_fresh_db(tmp_path):
    assert count_signal_events(db_path=_db(tmp_path)) == 0


def test_count_matches_saved(tmp_path):
    save_signal_events(
        [_event("2024-01-02"), _event("2024-01-03"), _event("2024-01-04")],
        batch_id="b1",
        db_path=_db(tmp_path),
    )

    assert count_signal_events(db_path=_db(tmp_path)) == 3


def test_filter_by_batch_id(tmp_path):
    save_signal_events([_event("2024-01-02")], batch_id="b1", db_path=_db(tmp_path))
    save_signal_events([_event("2024-01-03")], batch_id="b2", db_path=_db(tmp_path))

    assert count_signal_events(batch_id="b1", db_path=_db(tmp_path)) == 1
    (got,) = list_signal_events(batch_id="b2", db_path=_db(tmp_path))
    assert got.date == pd.Timestamp("2024-01-03")


def test_filter_by_source(tmp_path):
    save_signal_events(
        [
            _event("2024-01-02", source="ma_cross"),
            _event("2024-01-03", source="mr_zscore"),
            _event("2024-01-04", source="ma_cross"),
        ],
        batch_id="b1",
        db_path=_db(tmp_path),
    )

    assert count_signal_events(source="ma_cross", db_path=_db(tmp_path)) == 2
    assert all(e.source == "mr_zscore"
               for e in list_signal_events(source="mr_zscore", db_path=_db(tmp_path)))


def test_filter_by_date_range_is_inclusive(tmp_path):
    save_signal_events(
        [
            _event("2024-01-02"),
            _event("2024-01-03"),
            _event("2024-01-04"),
            _event("2024-01-05"),
        ],
        batch_id="b1",
        db_path=_db(tmp_path),
    )
    db = _db(tmp_path)

    got = list_signal_events(
        start=pd.Timestamp("2024-01-03"),
        end=pd.Timestamp("2024-01-04"),
        db_path=db,
    )

    assert [str(e.date.date()) for e in got] == ["2024-01-03", "2024-01-04"]


def test_results_ordered_by_date(tmp_path):
    save_signal_events(
        [_event("2024-01-05"), _event("2024-01-02"), _event("2024-01-04")],
        batch_id="b1",
        db_path=_db(tmp_path),
    )

    got = list_signal_events(db_path=_db(tmp_path))

    assert [str(e.date.date()) for e in got] == ["2024-01-02", "2024-01-04", "2024-01-05"]


def test_limit_caps_results(tmp_path):
    save_signal_events(
        [_event("2024-01-0%d" % d) for d in range(2, 6)],
        batch_id="b1",
        db_path=_db(tmp_path),
    )

    assert len(list_signal_events(limit=2, db_path=_db(tmp_path))) == 2


def test_limit_must_be_positive_int(tmp_path):
    db = _db(tmp_path)
    save_signal_events([_event("2024-01-02")], db_path=db)

    with pytest.raises(TypeError, match="limit"):
        list_signal_events(limit="2", db_path=db)
    with pytest.raises(ValueError, match="limit"):
        list_signal_events(limit=0, db_path=db)


def test_multiple_batches_independent(tmp_path):
    save_signal_events([_event("2024-01-02")], batch_id="b1", db_path=_db(tmp_path))
    save_signal_events([_event("2024-01-03")], batch_id="b1", db_path=_db(tmp_path))

    assert count_signal_events(batch_id="b1", db_path=_db(tmp_path)) == 2
