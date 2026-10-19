"""A16#12 —— signal_store.py 的「数据库故障 → PersistenceError」契约测试。

目标缺口（重测后 94%，6 行未覆盖）：
    signal_store.py 173-174 / 233-234 / 313-314
    分别是 save_signal_events / _query_events（被 list_signal_events 复用）/
    count_signal_events（symbol=None 走 SQL COUNT 分支）中，把底层
    sqlite3.Error 统一包装为 PersistenceError 的守卫分支。

设计原则（与 A16#10 store.py 边界契约测试一致）：
- 纯加法：只新增本测试文件，不改 signal_store.py 一行；
- 用 monkeypatch 把模块内 `get_connection` 替换为「直接抛 sqlite3.Error」的桩，
  精确命中三个守卫分支，并断言异常类型与来源链（from sqlite3.Error）；
- 附带一个无损往返契约，锚定「正常路径下 save/list/count 行为正确」，
  防止 monkeypatch 泄漏或回归；
- 所有用例都用 `tmp_path` 里的独立数据库，不触碰项目默认数据库。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd
import pytest

from deepquant.persistence import (
    count_signal_events,
    list_signal_events,
    save_signal_events,
    signal_store,
)
from deepquant.persistence.store import PersistenceError
from deepquant.strategies.base import Signal
from deepquant.strategies.signals import SignalEvent


def _valid_event() -> SignalEvent:
    return SignalEvent(
        date=pd.Timestamp("2024-01-02"),
        direction=Signal.BUY,
        strength=0.8,
        source="strat-A",
        valid_until=pd.Timestamp("2024-01-05"),
        metadata={"symbol": "600000", "z": 1.5},
    )


def _patch_connection_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """把 signal_store.get_connection 替换成「进入即抛 sqlite3.Error」的桩。

    `_open` 在 `conn = get_connection(db_path)` 处抛错，异常冒泡到各函数内的
    `except sqlite3.Error`，从而命中 173-174 / 233-234 / 313-314 三个守卫分支。
    """

    def _raise(*_args, **_kwargs):
        raise sqlite3.Error("forced connection failure for contract test")

    monkeypatch.setattr(signal_store, "get_connection", _raise)


def test_save_signal_events_db_failure_wraps_persistence_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """save 阶段底层 sqlite 故障 → 统一抛 PersistenceError（来源链为 sqlite3.Error）。"""
    _patch_connection_raises(monkeypatch)
    with pytest.raises(PersistenceError) as excinfo:
        save_signal_events([_valid_event()], db_path=tmp_path / "x.db")
    assert isinstance(excinfo.value.__cause__, sqlite3.Error)


def test_list_signal_events_db_failure_wraps_persistence_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """list 阶段底层 sqlite 故障 → 统一抛 PersistenceError（来源链为 sqlite3.Error）。"""
    _patch_connection_raises(monkeypatch)
    with pytest.raises(PersistenceError) as excinfo:
        list_signal_events(db_path=tmp_path / "x.db")
    assert isinstance(excinfo.value.__cause__, sqlite3.Error)


def test_count_signal_events_db_failure_wraps_persistence_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """count（symbol=None 走 SQL COUNT 分支）底层 sqlite 故障 → 统一抛 PersistenceError。"""
    _patch_connection_raises(monkeypatch)
    with pytest.raises(PersistenceError) as excinfo:
        count_signal_events(symbol=None, db_path=tmp_path / "x.db")
    assert isinstance(excinfo.value.__cause__, sqlite3.Error)


def test_signal_store_lossless_round_trip(tmp_path: Path) -> None:
    """正常路径：save→list→count 无损往返，锚定模块核心契约（防回归）。"""
    db = tmp_path / "sig.db"
    ev = _valid_event()
    batch = save_signal_events([ev], db_path=db)

    got = list_signal_events(batch_id=batch, db_path=db)
    assert len(got) == 1
    g = got[0]
    assert g.date == ev.date
    assert g.direction == ev.direction
    assert g.strength == ev.strength
    assert g.source == ev.source
    assert g.valid_until == ev.valid_until
    assert g.metadata == ev.metadata

    assert count_signal_events(db_path=db) == 1

    # valid_until=None 与空 metadata 也应无损还原
    ev2 = SignalEvent(
        date=pd.Timestamp("2024-01-03"),
        direction=Signal.HOLD,
        strength=0.0,
        source="strat-B",
    )
    save_signal_events([ev2], batch_id="B2", db_path=db)
    got2 = list_signal_events(batch_id="B2", db_path=db)
    assert len(got2) == 1
    assert got2[0].valid_until is None
    assert got2[0].metadata == {}
