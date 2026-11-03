"""健康探测：主动问一次"这条通道现在能用吗"。

被动健康 vs 主动探测，为什么两个都要
------------------------------------
降级链记录的是**被动健康**（刚才那次调用成没成）。它零成本但滞后：主源恢复后
要等下一次业务调用才知道。主动探测补上这一点，代价是要真的发请求。

因此本模块的定位是**按需工具**，不是每次请求都跑：

- 运维/巡检脚本调 ``probe_all`` 得到一份"当前哪条通道活着"的快照；
- 业务链路**不**在请求里探活（那会把调用成本翻倍，还引入"探活成功但业务失败"的
  假阳性）——降级链靠真实调用的成功/失败自行收敛。

超时的真相必须说清楚
--------------------
``timeout_s`` 用线程 ``join`` 实现。Python 无法强杀线程，所以**超时只是"不再等"，
被探测的请求可能仍在后台跑完**。这让探活适合轻量接口（拉一行行情），
不适合重查询。本模块把这个限制写进文档而不是假装能中断。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Iterable, Mapping

from deepquant.deps.registry import Dependency, Registry


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")


@dataclass(frozen=True)
class ProbeResult:
    kind: str
    channel: str
    ok: bool
    latency_ms: float
    detail: str = ""
    timed_out: bool = False
    checked_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "channel": self.channel,
            "ok": self.ok,
            "latency_ms": self.latency_ms,
            "detail": self.detail,
            "timed_out": self.timed_out,
            "checked_at": self.checked_at,
        }


@dataclass(frozen=True)
class HealthReport:
    results: tuple[ProbeResult, ...]

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def n_ok(self) -> int:
        return sum(1 for r in self.results if r.ok)

    def by_channel(self) -> dict[str, bool]:
        return {f"{r.kind}.{r.channel}": r.ok for r in self.results}

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at": _now_iso(),
            "n_probed": len(self.results),
            "n_ok": self.n_ok,
            "all_ok": self.ok,
            "healthy": self.by_channel(),
            "results": [r.to_dict() for r in self.results],
        }


def probe_one(
    dependency: Dependency,
    probe: Callable[[], Any],
    *,
    timeout_s: float = 5.0,
) -> ProbeResult:
    """探测单条通道。任何异常（含超时）都记为不健康，不向上抛。"""
    box: dict[str, Any] = {}

    def _run() -> None:
        started = time.perf_counter()
        try:
            probe()
        except BaseException as exc:  # noqa: BLE001 - 探活不允许把异常抛给调用方
            box["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            box["latency_ms"] = round((time.perf_counter() - started) * 1000, 3)

    thread = threading.Thread(target=_run, name=f"probe-{dependency.key}", daemon=True)
    thread.start()
    thread.join(timeout_s)

    if thread.is_alive():
        # 无法强杀线程：只能不再等，并如实标记「超时」而非「失败原因未知」
        return ProbeResult(
            kind=dependency.kind,
            channel=dependency.name,
            ok=False,
            latency_ms=round(timeout_s * 1000, 3),
            detail=f"探测超过 {timeout_s:.1f}s 未返回（线程仍在后台运行，无法中断）",
            timed_out=True,
        )

    latency_ms = float(box.get("latency_ms", 0.0))
    error = box.get("error")
    return ProbeResult(
        kind=dependency.kind,
        channel=dependency.name,
        ok=error is None,
        latency_ms=latency_ms,
        detail="" if error is None else str(error),
    )


def probe_all(
    registry: Registry,
    probes: Mapping[str, Callable[[], Any]],
    *,
    kind: str | None = None,
    timeout_s: float = 5.0,
) -> HealthReport:
    """探测注册表里的通道。

    ``probes`` 的键为通道名；缺少探测器的通道**不参与**报告（而不是记为健康）——
    "没探测"和"探测通过"必须分开，否则健康报告会系统性偏乐观。
    """
    results: list[ProbeResult] = []
    kinds: Iterable[str] = [kind] if kind else registry.kinds()
    for k in kinds:
        for dependency in registry.channels(k):
            probe = probes.get(dependency.name)
            if probe is None:
                continue
            results.append(probe_one(dependency, probe, timeout_s=timeout_s))
    return HealthReport(results=tuple(results))


class HealthTracker:
    """被动健康状态：连续失败计数与最近一次结果。

    **刻意不用于跳过通道**。原因：一次失败就把主源跳过，会让"偶发超时"演变成
    "连续降级"，把可恢复的抖动放大成不可用；而且状态一旦陈旧（主源已恢复但计数
    还在），跳过会掩盖恢复。当前策略是"每次都按优先级真实尝试"，用真实调用结果收敛。
    计数只用于运维观察与将来引入熔断时提供依据。
    """

    def __init__(self) -> None:
        self._failures: dict[str, int] = {}
        self._last_ok: dict[str, bool] = {}
        self._last_latency: dict[str, float] = {}

    def record(self, kind: str, channel: str, *, ok: bool, latency_ms: float) -> None:
        key = f"{kind}.{channel}"
        self._last_ok[key] = ok
        self._last_latency[key] = latency_ms
        if ok:
            self._failures[key] = 0
        else:
            self._failures[key] = self._failures.get(key, 0) + 1

    def snapshot(self) -> dict[str, dict[str, Any]]:
        return {
            key: {
                "consecutive_failures": self._failures.get(key, 0),
                "last_ok": self._last_ok.get(key),
                "last_latency_ms": self._last_latency.get(key),
            }
            for key in sorted(set(self._failures) | set(self._last_ok))
        }

    def reset(self) -> None:
        self._failures.clear()
        self._last_ok.clear()
        self._last_latency.clear()


__all__ = ["HealthReport", "HealthTracker", "ProbeResult", "probe_all", "probe_one"]
