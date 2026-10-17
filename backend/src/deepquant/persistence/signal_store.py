"""信号级持久化（Step 8.2'）—— 把 `SignalEvent` 存入 SQLite 并提供只读检索。

设计约束（与既有持久化层保持一致，且严格纯加法）：

- **不改动既有 schema。** 既有表由 `connection.init_schema()` 创建；
  本模块自建 `signal_events` 表（`CREATE TABLE IF NOT EXISTS`，幂等），
  不修改 `connection.py` 的 `_SCHEMA_SQL`，避免影响既有的回测存储路径。
- **不参与参数选择。** 这里只做「存下来、能查回来」，
  不评价、不排序择优、不聚合出任何用于挑参的指标（红线 R2 / R11）。
- **不修改事件内容。** 存取必须是无损往返：日期、方向、强度、有效期、
  metadata 全部原样还原，不做任何隐含的补齐或改写。
- 复用既有 `get_connection()` / `PersistenceError`，保持一致的异常与连接语义。

诚实性约定：
- 本模块持久化的是**研究层的结构化信号**，不是成交记录；
  它不证明信号可执行、也不证明有 alpha。
- `metadata` 以 JSON 文本存储，仅支持 JSON 可序列化的内容；
  非 JSON 可序列化的值会明确报错，不静默丢弃。
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from deepquant.persistence.connection import get_connection
from deepquant.persistence.store import PersistenceError
from deepquant.strategies.base import Signal
from deepquant.strategies.signals import SignalEvent

__all__ = [
    "SIGNAL_EVENTS_TABLE",
    "count_signal_events",
    "list_signal_events",
    "save_signal_events",
]

SIGNAL_EVENTS_TABLE = "signal_events"

_SIGNAL_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS signal_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id TEXT NOT NULL,
    event_date TEXT NOT NULL,
    direction INTEGER NOT NULL,
    strength REAL NOT NULL,
    source TEXT NOT NULL,
    valid_until TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signal_events_batch ON signal_events(batch_id);
CREATE INDEX IF NOT EXISTS idx_signal_events_source ON signal_events(source);
CREATE INDEX IF NOT EXISTS idx_signal_events_date ON signal_events(event_date);
"""


@contextmanager
def _open(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """打开连接并确保 `signal_events` 表存在；退出时关闭连接。

    `get_connection()` 已包含既有 schema 的初始化（幂等），
    这里再执行一次本模块自己的建表语句（同样幂等）。
    """
    conn = get_connection(db_path)
    try:
        with conn:
            conn.executescript(_SIGNAL_EVENTS_SQL)
        yield conn
    finally:
        conn.close()


def _to_iso(value: pd.Timestamp | None) -> str | None:
    if value is None:
        return None
    return pd.Timestamp(value).isoformat()


def _row_to_event(row: sqlite3.Row) -> SignalEvent:
    """把一行记录还原为 `SignalEvent`（无损往返）。"""
    metadata_raw = row["metadata_json"]
    metadata = json.loads(metadata_raw) if metadata_raw else {}
    valid_until_raw = row["valid_until"]

    return SignalEvent(
        date=pd.Timestamp(row["event_date"]),
        direction=Signal(int(row["direction"])),
        strength=float(row["strength"]),
        source=str(row["source"]),
        valid_until=(
            pd.Timestamp(valid_until_raw) if valid_until_raw is not None else None
        ),
        metadata=metadata,
    )


def save_signal_events(
    events: Sequence[SignalEvent],
    *,
    batch_id: str | None = None,
    db_path: Path | None = None,
) -> str:
    """把一批 `SignalEvent` 存入数据库，返回这批记录的 `batch_id`。

    Args:
        events: 待保存的事件序列，元素必须是 `SignalEvent`。
        batch_id: 批次标识；为 None 时自动生成（uuid4 hex）。
            同一批事件可用 `batch_id` 一次性检索回来。
        db_path: 数据库路径；None 表示使用 `get_db_path()` 的默认解析结果。

    Raises:
        TypeError: 元素不是 `SignalEvent`。
        ValueError: `events` 为空（无意义的一次保存）。
        PersistenceError: 数据库不可写 / 事务失败。

    注意：
        本函数只做持久化，不做任何评价或择优（R2 / R11）。
    """
    if events is None or len(events) == 0:
        raise ValueError("events 为空：没有可保存的信号事件")

    for i, event in enumerate(events):
        if not isinstance(event, SignalEvent):
            raise TypeError(
                f"events[{i}] 必须是 SignalEvent，收到 {type(event)!r}"
            )

    resolved_batch = batch_id if batch_id is not None else uuid.uuid4().hex
    if not isinstance(resolved_batch, str) or not resolved_batch:
        raise ValueError("batch_id 必须是非空字符串")

    created_at = datetime.now(UTC).isoformat()
    rows = []
    for event in events:
        try:
            metadata_json = json.dumps(event.metadata, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"事件的 metadata 无法 JSON 序列化：{exc!r}"
            ) from exc
        rows.append(
            (
                resolved_batch,
                _to_iso(event.date),
                int(event.direction),
                float(event.strength),
                event.source,
                _to_iso(event.valid_until),
                metadata_json,
                created_at,
            )
        )

    try:
        with _open(db_path) as conn:
            with conn:
                conn.executemany(
                    "INSERT INTO signal_events "
                    "(batch_id, event_date, direction, strength, source, "
                    " valid_until, metadata_json, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    rows,
                )
    except sqlite3.Error as exc:
        raise PersistenceError(f"保存信号事件失败：{exc!r}") from exc

    return resolved_batch


def _build_filters(
    *,
    batch_id: str | None,
    source: str | None,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
) -> tuple[str, list[object]]:
    clauses: list[str] = []
    params: list[object] = []

    if batch_id is not None:
        clauses.append("batch_id = ?")
        params.append(batch_id)
    if source is not None:
        clauses.append("source = ?")
        params.append(source)
    if start is not None:
        clauses.append("event_date >= ?")
        params.append(_to_iso(start))
    if end is not None:
        clauses.append("event_date <= ?")
        params.append(_to_iso(end))

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    return where, params


def _query_events(
    *,
    batch_id: str | None,
    source: str | None,
    start: pd.Timestamp | None,
    end: pd.Timestamp | None,
    symbol: str | None,
    db_path: Path | None,
) -> list[SignalEvent]:
    """按 SQL 可表达的过滤条件取出事件，再在 Python 侧按 `symbol` 过滤。

    时间窗（start/end）、batch、source 走 SQL（高效、可下推）；
    `symbol` 存放在 `metadata_json` 里，无法在 SQL 层可靠过滤，统一在取回后
    按 `metadata["symbol"]` 精确匹配。这样**无需改动既有 schema**，也不影响
    旧库的读取（纯加法，R6/R7 友好）。`symbol=None` 表示不过滤（保留旧行为）。
    """
    where, params = _build_filters(
        batch_id=batch_id, source=source, start=start, end=end
    )
    sql = (
        "SELECT batch_id, event_date, direction, strength, source, "
        "valid_until, metadata_json, created_at "
        f"FROM signal_events{where} ORDER BY event_date ASC, id ASC"
    )
    try:
        with _open(db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error as exc:
        raise PersistenceError(f"读取信号事件失败：{exc!r}") from exc

    events = [_row_to_event(row) for row in rows]
    if symbol is not None:
        events = [e for e in events if e.metadata.get("symbol") == symbol]
    return events


def list_signal_events(
    *,
    batch_id: str | None = None,
    source: str | None = None,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    symbol: str | None = None,
    limit: int | None = None,
    db_path: Path | None = None,
) -> list[SignalEvent]:
    """只读检索已保存的信号事件（不修改任何数据）。

    Args:
        batch_id / source: 精确匹配过滤。
        start / end: 按信号日闭区间过滤（含端点）；即「时间窗」过滤（已支持）。
        symbol: 按 `metadata["symbol"]` 精确匹配过滤（新增，纯加法）。
            为 None 时不按 symbol 过滤（保留旧行为）。
        limit: 最多返回条数；None 表示不限制。必须为正整数。
        db_path: 数据库路径；None 表示默认解析结果。

    Returns:
        按 `event_date`、`id` 升序排列的 `SignalEvent` 列表；无匹配时返回空列表。

    Raises:
        TypeError: `limit` 不是 int。
        ValueError: `limit` 不是正整数。
        PersistenceError: 数据库读取失败。
    """
    if limit is not None:
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise TypeError(f"limit 必须是 int，收到 {type(limit)!r}")
        if limit <= 0:
            raise ValueError(f"limit 必须是正整数，收到 {limit}")

    # 当按 symbol 过滤时，LIMIT 必须在 symbol 过滤之后生效，
    # 否则会先截断再筛 symbol，导致结果数量不可预期（纯加法、语义明确）。
    events = _query_events(
        batch_id=batch_id,
        source=source,
        start=start,
        end=end,
        symbol=symbol,
        db_path=db_path,
    )
    if limit is not None:
        events = events[:limit]
    return events


def count_signal_events(
    *,
    batch_id: str | None = None,
    source: str | None = None,
    start: pd.Timestamp | None = None,
    end: pd.Timestamp | None = None,
    symbol: str | None = None,
    db_path: Path | None = None,
) -> int:
    """只读取数：统计满足过滤条件的信号事件条数。

    参数语义同 `list_signal_events`（不含 `limit`）。无匹配时返回 0。
    """
    if symbol is None:
        # 无 symbol 过滤时走 SQL COUNT，保持原有高效路径（不回归）。
        where, params = _build_filters(
            batch_id=batch_id, source=source, start=start, end=end
        )
        sql = f"SELECT COUNT(*) AS n FROM signal_events{where}"
        try:
            with _open(db_path) as conn:
                row = conn.execute(sql, params).fetchone()
        except sqlite3.Error as exc:
            raise PersistenceError(f"统计信号事件失败：{exc!r}") from exc
        return int(row["n"]) if row is not None else 0

    # 含 symbol 过滤时，材料化后统计（与 `list_signal_events` 语义严格一致）。
    return len(
        _query_events(
            batch_id=batch_id,
            source=source,
            start=start,
            end=end,
            symbol=symbol,
            db_path=db_path,
        )
    )
