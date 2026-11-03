"""外部依赖注册表：把「有哪些外部依赖、谁优先、谁可替换」写成可枚举的数据。

为什么必须有注册表
------------------
计划 T3 的验收标准第一条是「核心外部依赖 100% 纳入注册表」。这句话只有在注册表
**先于实现存在**时才有意义：否则"核心依赖"会被定义成"当前代码恰好写死的那些"，
覆盖率自动 100%，而真正的缺口（比如某个脚本里硬编码的另一个数据源）永远不会出现。

因此本模块把依赖声明成 ``Dependency`` 常量，``DEFAULT_REGISTRY`` 据其构建。
新增通道只加一行，``chain`` / ``latency`` / 事件日志都不需要改。

本仓库的实际范围（必须如实说明，不要伪造）
------------------------------------------
- ``market_data``：两个真实可切换通道（eastmoney / tencent），是 T3 的主战场；
- **通讯通道**：本仓库没有任何对外通知/消息通道（无邮件、无 IM、无下游投递），
  因此该类为空，不用空壳占位；
- **下游系统**：本仓库是单体进程，不向下游系统投递，该类同样为空；
- 脚本层取数（``scripts/`` 下直接调 akshare 的 dividend/capital 抓取）**不经过本注册表**，
  它们不在 API 进程内，属已知限制（见验收文档 §7）。

登记为空类别不是遗漏，而是把"没有"和"没做"分开：等真有通知通道时，
在 ``Dependencies`` 里加一条并补 ``kind`` 即可。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Iterable, Mapping

#: 强制锁定通道的环境变量，格式 ``kind=channel``（如 ``market_data=tencent``）。
#: 用于计划要求的「支持人工强制锁定某一通道」：排查可疑数据时先锁死，避免
#: 观测到的样本在不同通道之间漂移。
DEP_LOCK_ENV = "DEEPQUANT_DEP_LOCK"

#: 依赖状态目录（事件与延迟档案）。默认放在 ``backend/data/deps`` —— 与
#: ``backtests.db`` 同级的运行时目录，已被 .gitignore 覆盖。
DEPS_DIR_ENV = "DEEPQUANT_DEPS_DIR"

#: 计划点名的三类依赖。用于覆盖率核算（分母固定）。
REQUIRED_KINDS: tuple[str, ...] = ("market_data", "notification", "downstream")


class DependencyConfigError(ValueError):
    """注册表本身不自洽（重名、优先级重复、kind 未知等）。"""


@dataclass(frozen=True)
class Dependency:
    """一个外部依赖通道。"""

    kind: str
    name: str
    priority: int
    #: 说明该通道实际用什么接口/地址，便于排查时不必翻代码。
    endpoint: str = ""
    #: 该通道是否可被降级链使用（False 表示仅保留条目，不参与自动切换）。
    enabled: bool = True
    note: str = ""

    @property
    def key(self) -> str:
        return f"{self.kind}.{self.name}"

    def to_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "name": self.name,
            "key": self.key,
            "priority": self.priority,
            "endpoint": self.endpoint,
            "enabled": self.enabled,
            "note": self.note,
        }


#: 本仓库的依赖清单。``priority`` 越小越优先。
DEFAULT_DEPENDENCIES: tuple[Dependency, ...] = (
    Dependency(
        kind="market_data",
        name="eastmoney",
        priority=10,
        endpoint="akshare.stock_zh_a_hist",
        note="中文列名；2026-10-17 实测曾被云端出口 proxy 拦截，故必须保留备用通道",
    ),
    Dependency(
        kind="market_data",
        name="tencent",
        priority=20,
        endpoint="akshare.stock_zh_a_hist_tx",
        note="英文列名、需 sh/sz/bj 市场前缀；东财不可达时的主力备用源",
    ),
)


def deps_dir() -> str:
    """依赖状态目录。空串表示不落盘（仅内存留痕）。"""
    raw = os.environ.get(DEPS_DIR_ENV, "").strip()
    return raw


def resolve_lock() -> dict[str, str]:
    """解析人工锁定配置。

    格式 ``kind=channel``，多个用分号或逗号分隔：``market_data=tencent``。
    解析不出来直接报错——锁定配置写错却静默忽略，会让"我以为锁了"变成新的排查陷阱。
    """
    raw = os.environ.get(DEP_LOCK_ENV, "").strip()
    if not raw:
        return {}
    out: dict[str, str] = {}
    for chunk in raw.replace(",", ";").split(";"):
        item = chunk.strip()
        if not item:
            continue
        if "=" not in item:
            raise DependencyConfigError(
                f"{DEP_LOCK_ENV} 格式应为 kind=channel，收到 {item!r}"
            )
        kind, channel = (x.strip() for x in item.split("=", 1))
        if not kind or not channel:
            raise DependencyConfigError(f"{DEP_LOCK_ENV} 存在空值：{item!r}")
        out[kind] = channel
    return out


@dataclass
class Registry:
    """依赖注册表：按 kind 分组、按 priority 排序、支持人工锁定。"""

    dependencies: tuple[Dependency, ...] = DEFAULT_DEPENDENCIES
    lock: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._validate()

    def _validate(self) -> None:
        kinds = {d.kind for d in self.dependencies}
        unknown = kinds - set(REQUIRED_KINDS)
        if unknown:
            raise DependencyConfigError(f"未知的依赖类别：{sorted(unknown)}")

        keys = [d.key for d in self.dependencies]
        if len(keys) != len(set(keys)):
            dup = [k for k in keys if keys.count(k) > 1]
            raise DependencyConfigError(f"依赖通道重名：{sorted(set(dup))}")

        by_kind: dict[str, list[int]] = {}
        for d in self.dependencies:
            by_kind.setdefault(d.kind, []).append(d.priority)
        for kind, prios in by_kind.items():
            if len(prios) != len(set(prios)):
                raise DependencyConfigError(f"{kind} 内存在重复优先级：{sorted(prios)}")

        for kind, channel in self.lock.items():
            available = {d.name for d in self.dependencies if d.kind == kind}
            if channel not in available:
                raise DependencyConfigError(
                    f"锁定配置指向不存在的通道：{kind}={channel}（可选 {sorted(available)}）"
                )

    def channels(self, kind: str, *, include_disabled: bool = False) -> tuple[Dependency, ...]:
        """按优先级返回该类别下的通道。"""
        items = [
            d
            for d in self.dependencies
            if d.kind == kind and (include_disabled or d.enabled)
        ]
        return tuple(sorted(items, key=lambda d: d.priority))

    def primary(self, kind: str) -> Dependency | None:
        items = self.channels(kind)
        return items[0] if items else None

    def locked_channel(self, kind: str) -> str | None:
        return self.lock.get(kind)

    def kinds(self) -> tuple[str, ...]:
        return tuple(k for k in REQUIRED_KINDS if any(d.kind == k for d in self.dependencies))

    def describe(self) -> list[dict[str, object]]:
        return [d.to_dict() for d in sorted(self.dependencies, key=lambda d: (d.kind, d.priority))]


def build_registry(
    dependencies: Iterable[Dependency] | None = None,
    *,
    lock: Mapping[str, str] | None = None,
) -> Registry:
    return Registry(
        dependencies=tuple(dependencies) if dependencies is not None else DEFAULT_DEPENDENCIES,
        lock=dict(lock if lock is not None else resolve_lock()),
    )


def coverage(registry: Registry) -> dict[str, object]:
    """覆盖率核算：三类依赖各自登记了什么。

    分母是计划点名的三类。``notification`` / ``downstream`` 在本仓库为空，
    这是**如实为空**（不是漏登记）：报告里给出 0 而不是伪造一个占位通道。
    """
    by_kind = {kind: [d.key for d in registry.channels(kind)] for kind in REQUIRED_KINDS}
    with_market = len(by_kind["market_data"])
    return {
        "required_kinds": list(REQUIRED_KINDS),
        "by_kind": by_kind,
        "market_data_channels": with_market,
        "market_data_has_fallback": with_market >= 2,
        "empty_kinds": [k for k in REQUIRED_KINDS if not by_kind[k]],
        "total_channels": len(registry.dependencies),
        "lock": dict(registry.lock),
    }


__all__ = [
    "DEFAULT_DEPENDENCIES",
    "DEP_LOCK_ENV",
    "DEPS_DIR_ENV",
    "Dependency",
    "DependencyConfigError",
    "REQUIRED_KINDS",
    "Registry",
    "build_registry",
    "coverage",
    "deps_dir",
    "resolve_lock",
]
