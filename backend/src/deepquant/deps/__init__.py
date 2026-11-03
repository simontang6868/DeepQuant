"""外部依赖降级链（T3）：注册表、健康探测、自动切换与延迟分布档案。

模块分工：

- :mod:`deepquant.deps.registry`：依赖清单（类别 / 优先级 / 端点）与人工锁定；
- :mod:`deepquant.deps.chain`：按优先级尝试通道，成功即返回，切换必留痕；
- :mod:`deepquant.deps.events`：切换事件（内存环形 + append-only 文件）；
- :mod:`deepquant.deps.latency`：分通道延迟采样与 P50/P90/P99 导出；
- :mod:`deepquant.deps.health`：按需主动探测（不在请求链路里跑）；
- :mod:`deepquant.deps.market_data`：运行期装配（唯一入口 ``runtime()``）。

三条贯穿设计：

1. **切换不影响调用方**：只有全部通道失败才抛异常；任一次成功都返回正常值。
2. **全部失败不返回空数据**：不拿空 DataFrame 冒充"这段时间没数据"。
3. **状态收敛靠真实调用**：不在请求里探活、不做"一次失败就跳过主源"的熔断——
   那会把偶发抖动放大成持续降级，也会让陈旧状态掩盖恢复。
"""

from __future__ import annotations

from deepquant.deps.chain import (
    Attempt,
    ChainResult,
    ChainState,
    DependencyUnavailableError,
    execute_chain,
    strategy_for,
)
from deepquant.deps.events import EVENTS_FILE, EventLog, SwitchEvent
from deepquant.deps.health import (
    HealthReport,
    HealthTracker,
    ProbeResult,
    probe_all,
    probe_one,
)
from deepquant.deps.latency import (
    LATENCY_DIR,
    LatencyArchive,
    LatencySample,
    percentile,
)
from deepquant.deps.market_data import (
    DEFAULT_DEPS_DIR,
    Runtime,
    build_runtime,
    channel_probes,
    reset_runtime,
    runtime,
)
from deepquant.deps.registry import (
    DEFAULT_DEPENDENCIES,
    DEP_LOCK_ENV,
    DEPS_DIR_ENV,
    REQUIRED_KINDS,
    Dependency,
    DependencyConfigError,
    Registry,
    build_registry,
    coverage,
    deps_dir,
    resolve_lock,
)

__all__ = [
    "DEFAULT_DEPENDENCIES",
    "DEFAULT_DEPS_DIR",
    "DEP_LOCK_ENV",
    "DEPS_DIR_ENV",
    "EVENTS_FILE",
    "LATENCY_DIR",
    "REQUIRED_KINDS",
    "Attempt",
    "ChainResult",
    "ChainState",
    "Dependency",
    "DependencyConfigError",
    "DependencyUnavailableError",
    "EventLog",
    "HealthReport",
    "HealthTracker",
    "LatencyArchive",
    "LatencySample",
    "ProbeResult",
    "Registry",
    "Runtime",
    "SwitchEvent",
    "build_registry",
    "build_runtime",
    "channel_probes",
    "coverage",
    "deps_dir",
    "execute_chain",
    "percentile",
    "probe_all",
    "probe_one",
    "reset_runtime",
    "resolve_lock",
    "runtime",
    "strategy_for",
]
