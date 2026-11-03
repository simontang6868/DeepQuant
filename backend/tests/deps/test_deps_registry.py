"""依赖注册表测试：顺序、校验、锁定、覆盖率。

覆盖《推进计划》T3 的第一条验收标准「核心外部依赖 100% 纳入注册表」——
注意这不是"注册表里有几条"，而是"三类依赖各自如实登记了什么"：
本仓库没有通知通道与下游系统，就必须报告为空，而不是塞占位条目。
"""

from __future__ import annotations

import pytest

from deepquant.deps.registry import (
    DEFAULT_DEPENDENCIES,
    DEP_LOCK_ENV,
    REQUIRED_KINDS,
    Dependency,
    DependencyConfigError,
    Registry,
    build_registry,
    coverage,
    resolve_lock,
)


def test_default_registry_has_ordered_market_channels() -> None:
    registry = build_registry(lock={})
    channels = registry.channels("market_data")
    assert [c.name for c in channels] == ["eastmoney", "tencent"]
    assert registry.primary("market_data").name == "eastmoney"
    assert channels[0].priority < channels[1].priority


def test_channels_exclude_disabled_by_default() -> None:
    deps = (
        Dependency(kind="market_data", name="a", priority=10),
        Dependency(kind="market_data", name="b", priority=20, enabled=False),
    )
    registry = build_registry(deps, lock={})
    assert [c.name for c in registry.channels("market_data")] == ["a"]
    assert [c.name for c in registry.channels("market_data", include_disabled=True)] == ["a", "b"]


def test_duplicate_channel_raises() -> None:
    deps = (
        Dependency(kind="market_data", name="a", priority=10),
        Dependency(kind="market_data", name="a", priority=20),
    )
    with pytest.raises(DependencyConfigError, match="重名"):
        Registry(dependencies=deps, lock={})


def test_duplicate_priority_raises() -> None:
    """优先级重复会让"按顺序尝试"变成不确定行为，必须拒绝。"""
    deps = (
        Dependency(kind="market_data", name="a", priority=10),
        Dependency(kind="market_data", name="b", priority=10),
    )
    with pytest.raises(DependencyConfigError, match="优先级"):
        Registry(dependencies=deps, lock={})


def test_unknown_kind_raises() -> None:
    deps = (Dependency(kind="magic", name="a", priority=10),)
    with pytest.raises(DependencyConfigError, match="未知的依赖类别"):
        Registry(dependencies=deps, lock={})


def test_lock_to_unknown_channel_raises() -> None:
    """锁定指向不存在的通道时必须报错，而不是静默忽略（"我以为锁了"更危险）。"""
    with pytest.raises(DependencyConfigError, match="锁定配置指向不存在的通道"):
        Registry(dependencies=DEFAULT_DEPENDENCIES, lock={"market_data": "sina"})


def test_lock_parsing_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEP_LOCK_ENV, raising=False)
    assert resolve_lock() == {}

    monkeypatch.setenv(DEP_LOCK_ENV, "market_data=tencent")
    assert resolve_lock() == {"market_data": "tencent"}

    monkeypatch.setenv(DEP_LOCK_ENV, "market_data=tencent; upstream=primary")
    assert resolve_lock() == {"market_data": "tencent", "upstream": "primary"}


@pytest.mark.parametrize("raw", ["market_data", "=tencent", "market_data="])
def test_bad_lock_format_raises(raw: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DEP_LOCK_ENV, raw)
    with pytest.raises(DependencyConfigError):
        resolve_lock()


def test_coverage_reports_empty_kinds_honestly() -> None:
    """通知与下游在本仓库确实不存在：报告要显示为空，而不是编一个占位通道。"""
    report = coverage(build_registry(lock={}))
    assert report["market_data_channels"] == 2
    assert report["market_data_has_fallback"] is True
    assert set(report["empty_kinds"]) == {"notification", "downstream"}
    assert set(report["required_kinds"]) == set(REQUIRED_KINDS)
    assert report["total_channels"] == 2


def test_describe_is_serialisable_and_sorted() -> None:
    entries = build_registry(lock={}).describe()
    assert [e["name"] for e in entries] == ["eastmoney", "tencent"]
    assert all(e["kind"] == "market_data" for e in entries)
    assert all(e["endpoint"] for e in entries), "每条通道都要写明实际接口，便于排查"
