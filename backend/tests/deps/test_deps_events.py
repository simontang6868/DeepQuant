"""切换事件日志测试：每次切换必须留痕，落盘失败也不影响业务但要标明。

验收标准「每次切换生成事件记录」有一个隐蔽的失败态：文件写不进去时抛错，
把一次可恢复的降级变成业务中断。本文件的处理方式是**降级但标记**
（``persisted=False``），既不中断业务，也不假装记录成功。
"""

from __future__ import annotations

import json
from pathlib import Path

from deepquant.deps.events import EVENTS_FILE, EventLog, SwitchEvent


def _event(**kwargs) -> SwitchEvent:
    base = dict(
        kind="market_data",
        src="eastmoney",
        dst="tencent",
        reason="primary_failed_fallback",
        occurred_at="2026-10-27T01:00:00+00:00",
        attempts=({"channel": "eastmoney", "ok": False},),
    )
    base.update(kwargs)
    return SwitchEvent(**base)


def test_record_writes_memory_and_file(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    recorded = log.record(_event())

    assert recorded.persisted is True
    assert log.count() == 1

    path = tmp_path / EVENTS_FILE
    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["src"] == "eastmoney"
    assert payload["dst"] == "tencent"
    assert payload["direction"] == "failover"
    assert payload["persisted"] is True


def test_file_is_lf_only(tmp_path: Path) -> None:
    EventLog(tmp_path).record(_event())
    raw = (tmp_path / EVENTS_FILE).read_bytes()
    assert b"\r\n" not in raw
    assert raw.endswith(b"\n")


def test_direction_reflects_recovery(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    recovered = log.record(_event(src="tencent", dst="eastmoney", recovered=True))
    assert recovered.direction == "recover"
    assert log.recent()[0]["direction"] == "recover"


def test_recent_filters_by_kind(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    log.record(_event(kind="market_data"))
    log.record(_event(kind="notification", src="a", dst="b"))
    assert len(log.recent(kind="market_data")) == 1
    assert log.count(kind="notification") == 1


def test_memory_only_when_no_root() -> None:
    log = EventLog(None)
    recorded = log.record(_event())
    assert recorded.persisted is True, "没有目录 ≠ 落盘失败，不应标记为未落盘"
    assert log.path is None
    assert log.count() == 1


def test_unwritable_root_degrades_without_raising(tmp_path: Path) -> None:
    """目录不可写时：不抛异常，但事件被标记为未落盘。"""
    blocked = tmp_path / "blocked"
    blocked.write_text("我是文件，不是目录", encoding="utf-8", newline="\n")

    log = EventLog(blocked)
    recorded = log.record(_event())
    assert recorded.persisted is False
    assert log.recent()[-1]["persisted"] is False
    assert log.count() == 1, "内存里仍要有记录，否则事后连「发生过切换」都不知道"


def test_read_file_skips_corrupt_lines(tmp_path: Path) -> None:
    path = tmp_path / EVENTS_FILE
    path.write_text(
        json.dumps({"kind": "market_data", "src": "a", "dst": "b"}) + "\n{broken\n",
        encoding="utf-8",
        newline="\n",
    )
    log = EventLog(tmp_path)
    items = log.read_file()
    assert len(items) == 1
    assert items[0]["src"] == "a"


def test_read_file_limit_returns_latest(tmp_path: Path) -> None:
    log = EventLog(tmp_path)
    for i in range(5):
        log.record(_event(reason=f"r{i}"))
    latest = log.read_file(limit=2)
    assert [e["reason"] for e in latest] == ["r3", "r4"]


def test_attempts_are_preserved_verbatim(tmp_path: Path) -> None:
    """事件必须保留每次尝试的耗时与错误——只记"换到腾讯了"无法定位主源为什么挂。"""
    log = EventLog(tmp_path)
    attempts = (
        {"channel": "eastmoney", "ok": False, "latency_ms": 30012.0, "error": "ProxyError"},
        {"channel": "tencent", "ok": True, "latency_ms": 1300.0, "error": None},
    )
    log.record(_event(attempts=attempts))
    stored = log.read_file()[0]
    assert stored["attempts"] == list(attempts)
