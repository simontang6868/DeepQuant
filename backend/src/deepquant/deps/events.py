"""切换事件记录：每次"换了一条通道"都必须留下可检索的记录。

为什么事件要独立于 T1 审计工件
------------------------------
T1 的工件是**接口操作**级（一次请求一张卡），而切换发生在**一次调用内部**，
可能一次请求里切换两次（东财失败 → 腾讯成功 → 下一个标的再失败）。
把切换塞进请求级工件会被折叠成一条"这条请求里发生过切换"，细节丢失。

因此事件单独记录，两者用时间戳与通道名关联：
- 事件：原子事实（谁换到谁、为什么、几次尝试）；
- 工件：请求级归档（这次操作是否受影响）。

落盘策略（为什么默认会写文件、但不写每条延迟）
----------------------------------------------
计划要求「每次切换生成事件记录」，所以事件**默认落盘**（目录见
``registry.deps_dir``）。切换是低频事件（正常运行时一天可能 0 条），
写盘成本可忽略；而延迟采样是每次调用都产生，必须限流（见 ``latency.py``）。

目录不可写时降级为「只留内存 + 记错误日志」，**不抛给业务**：
切换本身已经是一次故障，不能因为记不下日志把一次可恢复的降级变成业务中断。
但降级事实会写进事件的 ``persisted`` 字段，避免出现"以为有记录其实没有"。
"""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

logger = logging.getLogger(__name__)

#: 事件文件名（append-only JSONL）。
EVENTS_FILE = "events.jsonl"

#: 内存环形缓冲容量：不落盘时也能查出最近发生过什么。
DEFAULT_CAPACITY = 500


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class SwitchEvent:
    """一次通道切换。"""

    kind: str
    src: str
    dst: str
    reason: str
    occurred_at: str
    #: True 表示回到了优先级更高的通道（恢复回切），False 表示降级切换。
    recovered: bool = False
    attempts: tuple[dict[str, Any], ...] = ()
    persisted: bool = True

    @property
    def direction(self) -> str:
        return "recover" if self.recovered else "failover"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "src": self.src,
            "dst": self.dst,
            "direction": self.direction,
            "recovered": self.recovered,
            "reason": self.reason,
            "occurred_at": self.occurred_at,
            "attempts": list(self.attempts),
            "persisted": self.persisted,
        }


@dataclass
class EventLog:
    """事件日志：内存环形缓冲 + 可选 append-only 文件。"""

    root: str | Path | None = None
    capacity: int = DEFAULT_CAPACITY
    _buffer: deque[dict[str, Any]] = field(default_factory=lambda: deque(maxlen=DEFAULT_CAPACITY))

    def __post_init__(self) -> None:
        self._buffer = deque(maxlen=self.capacity)

    @property
    def path(self) -> Path | None:
        if self.root is None or str(self.root).strip() == "":
            return None
        return Path(self.root) / EVENTS_FILE

    def record(self, event: SwitchEvent) -> SwitchEvent:
        """记录一条事件，返回可能被标记为"未落盘"的副本。"""
        payload = event.to_dict()
        persisted = True
        path = self.path
        if path is not None:
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                line = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                with path.open("a", encoding="utf-8", newline="\n") as fh:
                    fh.write(line + "\n")
                    fh.flush()
            except OSError as exc:  # 目录不可写 / 磁盘满
                persisted = False
                logger.error("切换事件落盘失败（已降级为内存记录）：%s", exc)
        if not persisted:
            payload["persisted"] = False
            event = SwitchEvent(
                kind=event.kind,
                src=event.src,
                dst=event.dst,
                reason=event.reason,
                occurred_at=event.occurred_at,
                recovered=event.recovered,
                attempts=event.attempts,
                persisted=False,
            )
        self._buffer.append(payload)
        return event

    def recent(self, *, kind: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """最近的事件（内存）。"""
        items = [e for e in self._buffer if kind is None or e.get("kind") == kind]
        return list(items)[-limit:]

    def read_file(self, *, kind: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        """从磁盘读取事件（跨进程、跨重启）。文件不存在返回空列表。"""
        path = self.path
        if path is None or not path.exists():
            return []
        out: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8", newline="") as fh:
            for line_no, line in enumerate(fh, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    item = json.loads(text)
                except json.JSONDecodeError:
                    # 不静默跳过：损坏的事件行本身就是需要知道的故障
                    logger.error("事件文件第 %d 行无法解析，已跳过：%s", line_no, text[:120])
                    continue
                if kind is None or item.get("kind") == kind:
                    out.append(item)
        return out[-limit:] if limit else out

    def count(self, *, kind: str | None = None) -> int:
        return sum(1 for e in self._buffer if kind is None or e.get("kind") == kind)


def build_event_log(root: str | Path | None, *, capacity: int = DEFAULT_CAPACITY) -> EventLog:
    return EventLog(root=root, capacity=capacity)


__all__ = ["DEFAULT_CAPACITY", "EVENTS_FILE", "EventLog", "SwitchEvent", "build_event_log", "now_iso"]
