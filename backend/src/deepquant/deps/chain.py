"""降级链执行器：按优先级尝试通道，成功即返回，切换必留痕。

「业务无感知中断」到底怎么保证
------------------------------
计划 T3 的验收标准是「模拟主源故障时切换成功且业务无感知中断」。
关键在于**异常不该走到调用方**：调用方写的是 ``df = fetch(...)``，不应该被迫
处理"东财挂了但腾讯可用"这种运维细节。因此本模块只在**全部通道都失败**时抛
``DependencyUnavailableError``，任何一次成功都返回正常值。

极端情况必须如实说明：如果全部通道都失败，本模块**不会**返回空 DataFrame
冒充"这段时间没数据"——那是本项目反复强调的红线（"没检出 ≠ 没问题"）。

恢复回切是自动的，但要记事件
----------------------------
通道顺序是固定的优先级序列，每次调用都从最高优先级开始尝试。主源恢复后，
下一次调用自然回到主源——这就是"恢复回切"，不需要额外的定时探活。
但**回到主源这件事本身也要留痕**：否则从事件日志看，只有降级没有恢复，
事后无法判断某天的数据到底来自哪条通道。

人工锁定
--------
``registry.lock`` 或调用方 ``pinned`` 可把某类别钉死在某条通道上。锁定时不走
降级链（否则"锁定"名不副实），也不产出切换事件（没有发生切换），
但会在结果里标记 ``pinned=True``，让调用方知道"这次的数据源是被指定的"。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, Mapping, TypeVar

from deepquant.deps.events import EventLog, SwitchEvent, now_iso
from deepquant.deps.latency import LatencyArchive
from deepquant.deps.registry import Dependency, DependencyConfigError, Registry, build_registry

logger = logging.getLogger(__name__)

T = TypeVar("T")


class DependencyUnavailableError(RuntimeError):
    """该类别下全部通道均失败。``attempts`` 保留每次尝试的耗时与错误。"""

    def __init__(self, kind: str, attempts: tuple["Attempt", ...]) -> None:
        self.kind = kind
        self.attempts = attempts
        detail = " | ".join(
            f"[{a.channel}] {'ok' if a.ok else a.error}" for a in attempts
        )
        super().__init__(f"{kind} 全部通道失败（{len(attempts)} 次尝试）：{detail}")


@dataclass(frozen=True)
class Attempt:
    channel: str
    ok: bool
    latency_ms: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "error": self.error,
        }


@dataclass(frozen=True)
class ChainResult(Generic[T]):
    value: T
    channel: str
    attempts: tuple[Attempt, ...]
    event: SwitchEvent | None = None
    pinned: bool = False

    @property
    def switched(self) -> bool:
        return self.event is not None

    @property
    def degraded(self) -> bool:
        """是否发生了降级（首个通道失败后才成功）。"""
        return any(not a.ok for a in self.attempts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "pinned": self.pinned,
            "degraded": self.degraded,
            "attempts": [a.to_dict() for a in self.attempts],
            "event": None if self.event is None else self.event.to_dict(),
        }


@dataclass
class ChainState:
    """跨调用的状态：记住每个类别上一次**成功**的通道，用于判定切换方向。"""

    last_success: dict[str, str] = field(default_factory=dict)

    def reset(self) -> None:
        self.last_success.clear()


_DEFAULT_STATE = ChainState()


def execute_chain(
    *,
    kind: str,
    call: Callable[[Dependency], T],
    registry: Registry | None = None,
    pinned: str | None = None,
    archive: LatencyArchive | None = None,
    events: EventLog | None = None,
    state: ChainState | None = None,
    is_retryable: Callable[[Exception], bool] | None = None,
) -> ChainResult[T]:
    """按优先级尝试 ``kind`` 类别的通道，返回第一个成功的结果。

    ``is_retryable``：给定异常是否值得换下一条通道。默认全部可重试。
    调用方一旦区分得清"网络不可达"与"参数错了"，就应传入该判断——
    参数错误换通道也没用，只会把一个明确错误拖成 N 次调用加一条含糊的聚合错误。
    """
    registry = registry or build_registry()
    state = state if state is not None else _DEFAULT_STATE
    archive = archive if archive is not None else LatencyArchive(None)
    events = events if events is not None else EventLog(None)

    channels = registry.channels(kind)
    if not channels:
        raise DependencyConfigError(f"{kind} 类别下没有任何可用通道")

    locked = pinned or registry.locked_channel(kind)
    if locked is not None:
        matched = tuple(c for c in channels if c.name == locked)
        if not matched:
            raise DependencyConfigError(
                f"锁定通道不存在：{kind}={locked}（可选 {[c.name for c in channels]}）"
            )
        channels = matched

    primary = registry.primary(kind)
    attempts: list[Attempt] = []

    for channel in channels:
        started = time.perf_counter()
        try:
            value = call(channel)
        except Exception as exc:  # noqa: BLE001 - 任何异常都视为该通道不可用
            latency_ms = round((time.perf_counter() - started) * 1000, 3)
            attempts.append(
                Attempt(channel=channel.name, ok=False, latency_ms=latency_ms, error=f"{type(exc).__name__}: {exc}")
            )
            archive.record(kind=kind, channel=channel.name, latency_ms=latency_ms, ok=False)
            logger.warning("依赖通道 %s 调用失败（%.0fms）：%s", channel.key, latency_ms, exc)
            if is_retryable is not None and not is_retryable(exc):
                # 不可重试：原样抛出，不伪装成"所有通道都不可用"
                raise
            continue

        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        attempts.append(Attempt(channel=channel.name, ok=True, latency_ms=latency_ms))
        archive.record(kind=kind, channel=channel.name, latency_ms=latency_ms, ok=True)

        event = None
        if locked is None:
            previous = state.last_success.get(kind)
            first_failed = bool(attempts) and not attempts[0].ok
            src: str | None = None
            reason = ""
            recovered = False

            if previous is not None and previous != channel.name:
                # 与上次成功的通道不同：方向取决于是否回到了最高优先级通道
                src = previous
                recovered = primary is not None and channel.name == primary.name
                reason = "primary_recovered" if recovered else "primary_failed_fallback"
            elif first_failed:
                # 进程内第一次调用就发生降级（还没有 last_success 可对比）。
                # 这种情况同样是一次真实切换，不能因为它"没有历史"就不留痕——
                # 否则一个新启动的进程在完全降级的状态下运行时，事件日志会是空的。
                src = attempts[0].channel
                reason = "primary_failed_fallback"

            if src is not None:
                event = events.record(
                    SwitchEvent(
                        kind=kind,
                        src=src,
                        dst=channel.name,
                        reason=reason,
                        occurred_at=now_iso(),
                        recovered=recovered,
                        attempts=tuple(a.to_dict() for a in attempts),
                    )
                )
        state.last_success[kind] = channel.name

        return ChainResult(
            value=value,
            channel=channel.name,
            attempts=tuple(attempts),
            event=event,
            pinned=locked is not None,
        )

    raise DependencyUnavailableError(kind, tuple(attempts))


def strategy_for(registry: Registry, kind: str) -> tuple[str, ...]:
    """该类别当前的尝试顺序（供报告/日志使用）。"""
    return tuple(c.name for c in registry.channels(kind))


__all__ = [
    "Attempt",
    "ChainResult",
    "ChainState",
    "DependencyUnavailableError",
    "execute_chain",
    "strategy_for",
]
