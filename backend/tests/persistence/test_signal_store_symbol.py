"""Step 8.2' 检索增强测试：按 `metadata["symbol"]` 过滤（纯加法）。

覆盖：symbol 精确匹配、与日期窗组合、与 limit 组合、与 batch_id 组合、
count 按 symbol 统计、symbol 缺失返回空、symbol=None 保留旧行为（不过滤）。

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
from deepquant.strategies.base import Signal
from deepquant.strategies.signals import SignalEvent


def _event(
    day: str,
    *,
    direction: Signal = Signal.BUY,
    source: str = "ma_cross",
    symbol: str | None = None,
) -> SignalEvent:
    metadata = {"symbol": symbol} if symbol is not None else {}
    return SignalEvent(
        date=pd.Timestamp(day),
        direction=direction,
        strength=1.0,
        source=source,
        metadata=metadata,
    )


def _db(tmp_path) -> "object":
    return tmp_path / "signals.db"


def test_filter_by_symbol_exact_match(tmp_path):
    save_signal_events(
        [
            _event("2024-01-02", symbol="600519"),
            _event("2024-01-03", symbol="000001"),
            _event("2024-01-04", symbol="600519"),
        ],
        batch_id="b1",
        db_path=_db(tmp_path),
    )

    got = list_signal_events(symbol="600519", db_path=_db(tmp_path))

    assert {str(e.date.date()) for e in got} == {"2024-01-02", "2024-01-04"}
    assert all(e.metadata.get("symbol") == "600519" for e in got)


def test_filter_by_symbol_missing_returns_empty(tmp_path):
    save_signal_events(
        [_event("2024-01-02", symbol="600519")],
        batch_id="b1",
        db_path=_db(tmp_path),
    )

    assert list_signal_events(symbol="000001", db_path=_db(tmp_path)) == []


def test_symbol_none_keeps_legacy_behavior(tmp_path):
    save_signal_events(
        [
            _event("2024-01-02", symbol="600519"),
            _event("2024-01-03", symbol="000001"),
        ],
        batch_id="b1",
        db_path=_db(tmp_path),
    )

    got = list_signal_events(db_path=_db(tmp_path))

    assert len(got) == 2  # 不过滤 = 旧行为


def test_symbol_combined_with_date_window(tmp_path):
    db = _db(tmp_path)
    save_signal_events(
        [
            _event("2024-01-02", symbol="600519"),
            _event("2024-01-03", symbol="600519"),  # 在窗内
            _event("2024-01-04", symbol="600519"),
            _event("2024-01-03", symbol="000001"),
        ],
        batch_id="b1",
        db_path=db,
    )

    got = list_signal_events(
        symbol="600519",
        start=pd.Timestamp("2024-01-03"),
        end=pd.Timestamp("2024-01-03"),
        db_path=db,
    )

    assert [str(e.date.date()) for e in got] == ["2024-01-03"]


def test_symbol_combined_with_limit(tmp_path):
    db = _db(tmp_path)
    save_signal_events(
        [
            _event("2024-01-02", symbol="600519"),
            _event("2024-01-03", symbol="600519"),
            _event("2024-01-04", symbol="600519"),
        ],
        batch_id="b1",
        db_path=db,
    )

    got = list_signal_events(symbol="600519", limit=2, db_path=db)

    assert len(got) == 2
    assert [str(e.date.date()) for e in got] == ["2024-01-02", "2024-01-03"]


def test_symbol_combined_with_batch_id(tmp_path):
    db = _db(tmp_path)
    save_signal_events(
        [_event("2024-01-02", symbol="600519")], batch_id="b1", db_path=db
    )
    save_signal_events(
        [_event("2024-01-03", symbol="600519")], batch_id="b2", db_path=db
    )

    got = list_signal_events(symbol="600519", batch_id="b1", db_path=db)

    assert len(got) == 1
    assert str(got[0].date.date()) == "2024-01-02"


def test_count_by_symbol(tmp_path):
    save_signal_events(
        [
            _event("2024-01-02", symbol="600519"),
            _event("2024-01-03", symbol="600519"),
            _event("2024-01-04", symbol="000001"),
        ],
        batch_id="b1",
        db_path=_db(tmp_path),
    )

    assert count_signal_events(symbol="600519", db_path=_db(tmp_path)) == 2
    assert count_signal_events(symbol="000001", db_path=_db(tmp_path)) == 1
    assert count_signal_events(symbol="NOSUCH", db_path=_db(tmp_path)) == 0
    # 无 symbol 过滤 → 全量（旧路径）
    assert count_signal_events(db_path=_db(tmp_path)) == 3


def test_count_by_symbol_with_date_window(tmp_path):
    db = _db(tmp_path)
    save_signal_events(
        [
            _event("2024-01-02", symbol="600519"),
            _event("2024-01-03", symbol="600519"),
            _event("2024-01-04", symbol="600519"),
        ],
        batch_id="b1",
        db_path=db,
    )

    assert (
        count_signal_events(
            symbol="600519",
            start=pd.Timestamp("2024-01-03"),
            db_path=db,
        )
        == 2
    )


def test_event_without_symbol_ignored_when_filtering(tmp_path):
    db = _db(tmp_path)
    save_signal_events(
        [
            _event("2024-01-02", symbol=None),  # 无 symbol 元数据
            _event("2024-01-03", symbol="600519"),
        ],
        batch_id="b1",
        db_path=db,
    )

    got = list_signal_events(symbol="600519", db_path=db)

    assert len(got) == 1
    assert str(got[0].date.date()) == "2024-01-03"
