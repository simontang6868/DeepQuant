"""行情依赖的运行期装配：注册表 + 延迟档案 + 事件日志 + 探活器。

为什么集中在一处装配
--------------------
降级链、延迟档案、事件日志三者共享同一个状态目录，且都要能被环境变量覆盖
（``DEEPQUANT_DEPS_DIR`` / ``DEEPQUANT_DEP_LOCK``）。如果每个调用点各自
``LatencyArchive(...)``，就会出现"三处各写一个目录、报告里三份数字对不上"。

因此这里给出唯一入口 ``runtime()``：按当前环境构建一次并缓存；环境变了就重建
（测试里 monkeypatch 环境变量后无需手动清理）。

默认目录
--------
``backend/data/deps``（与 ``backtests.db`` 同级，已被 .gitignore 覆盖）。
切换事件量很低，默认落盘；延迟采样按秒限流，避免批量抓取时写爆磁盘。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from deepquant.deps.chain import ChainState
from deepquant.deps.events import EventLog
from deepquant.deps.health import HealthTracker
from deepquant.deps.latency import DEFAULT_MIN_INTERVAL_S, LatencyArchive
from deepquant.deps.registry import (
    DEP_LOCK_ENV,
    DEPS_DIR_ENV,
    Registry,
    build_registry,
    deps_dir,
    resolve_lock,
)

#: 仓库内默认依赖状态目录：backend/data/deps（由本文件位置锚定，不依赖启动目录）。
DEFAULT_DEPS_DIR = Path(__file__).resolve().parents[3] / "data" / "deps"


@dataclass(frozen=True)
class Runtime:
    """一次装配出的运行期对象集合。"""

    registry: Registry
    archive: LatencyArchive
    events: EventLog
    health: HealthTracker
    #: 切换方向判定所需的"上次成功通道"。与事件日志同寿命：
    #: 状态与它解释的事件放在一起，避免出现"事件说回切了、状态却不知道"。
    state: ChainState

    def describe(self) -> dict[str, Any]:
        return {
            "registry": self.registry.describe(),
            "lock": dict(self.registry.lock),
            "events_path": None if self.events.path is None else str(self.events.path),
            "latency_root": None if self.archive.root is None else str(self.archive.root),
        }


_CACHE: dict[tuple[str, tuple[tuple[str, str], ...]], Runtime] = {}


def _cache_key() -> tuple[str, tuple[tuple[str, str], ...]]:
    directory = deps_dir() or str(DEFAULT_DEPS_DIR)
    lock = tuple(sorted(resolve_lock().items()))
    return directory, lock


def build_runtime(
    *,
    directory: str | Path | None = None,
    lock: Mapping[str, str] | None = None,
    min_interval_s: float | None = None,
) -> Runtime:
    """显式装配（测试与脚本用），不读缓存。"""
    if directory is None:
        raw = deps_dir()
        directory = raw if raw else DEFAULT_DEPS_DIR
    root = Path(directory) if str(directory).strip() else None
    registry = build_registry(lock=lock if lock is not None else resolve_lock())
    # 未指定间隔时用模块默认值（1 秒/通道）；显式传 0 表示不限流（测试与专项采样用）
    interval = (
        min_interval_s
        if min_interval_s is not None
        else (DEFAULT_MIN_INTERVAL_S if root is not None else 0.0)
    )
    archive = LatencyArchive(root, min_interval_s=interval)
    return Runtime(
        registry=registry,
        archive=archive,
        events=EventLog(root),
        health=HealthTracker(),
        state=ChainState(),
    )


def runtime() -> Runtime:
    """按当前环境取（并缓存）运行期对象。

    缓存键包含目录与锁定配置：测试里 monkeypatch 环境变量后自动得到新实例，
    不会出现"上一个用例的锁定配置还在生效"这种跨用例污染。
    """
    key = _cache_key()
    cached = _CACHE.get(key)
    if cached is None:
        cached = build_runtime()
        _CACHE[key] = cached
    return cached


def reset_runtime() -> None:
    """清空缓存（测试与长驻进程重载配置时用）。"""
    _CACHE.clear()


def channel_probes() -> dict[str, Callable[[], Any]]:
    """行情通道的探活器：各拉一小段真实行情，失败即抛。

    懒导入 ``akshare_loader``：本模块与加载器互相引用，顶层导入会成环；
    且 akshare 只在真正探测时才需要（未安装时应报可读错误，而不是 import 阶段炸掉）。
    """

    def _probe(channel: str) -> Callable[[], Any]:
        def _run() -> None:
            from deepquant.data.loaders import akshare_loader

            akshare_loader.probe_channel(channel)

        _run.__name__ = f"probe_{channel}"
        return _run

    return {name: _probe(name) for name in ("eastmoney", "tencent")}


__all__ = [
    "DEFAULT_DEPS_DIR",
    "DEP_LOCK_ENV",
    "DEPS_DIR_ENV",
    "Runtime",
    "build_runtime",
    "channel_probes",
    "reset_runtime",
    "runtime",
]
