"""降级链测试：优先级顺序、异常不外溢、切换与回切事件、锁定、延迟记录。

验收标准里的「切换成功且业务无感知中断」有两个必须分开验证的点：
1. 主源失败时**返回值照常拿到**（而不是异常被吞掉后返回空）；
2. 只有**全部通道失败**才抛异常，且异常里带着每次尝试的耗时与原因。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from deepquant.deps.chain import (
    ChainState,
    DependencyUnavailableError,
    execute_chain,
)
from deepquant.deps.events import EventLog
from deepquant.deps.latency import LatencyArchive
from deepquant.deps.registry import Dependency, DependencyConfigError, Registry, build_registry


@pytest.fixture
def runtime(tmp_path: Path):
    """一套隔离的运行期对象（状态、事件、延迟档案均独立）。"""
    return {
        "registry": build_registry(lock={}),
        "archive": LatencyArchive(tmp_path / "deps", min_interval_s=0),
        "events": EventLog(tmp_path / "deps"),
        "state": ChainState(),
    }


def _call_fail_first(state_box: dict):
    """东财抛错、腾讯成功。``state_box`` 用于断言"失败也被记录了延迟"。"""

    def _call(dependency):
        if dependency.name == "eastmoney":
            raise ConnectionError("proxy blocked")
        return f"data-from-{dependency.name}"

    return _call


def test_primary_success_returns_without_event(runtime) -> None:
    result = execute_chain(kind="market_data", call=lambda d: f"ok-{d.name}", **runtime)
    assert result.channel == "eastmoney"
    assert result.value == "ok-eastmoney"
    assert result.event is None
    assert result.degraded is False
    assert result.pinned is False
    assert [a.channel for a in result.attempts] == ["eastmoney"]


def test_failure_falls_back_and_returns_value(runtime) -> None:
    """主源失败时调用方**拿到正常值**，而不是异常。"""
    result = execute_chain(kind="market_data", call=_call_fail_first({}), **runtime)
    assert result.channel == "tencent"
    assert result.value == "data-from-tencent"
    assert result.degraded is True
    assert [a.ok for a in result.attempts] == [False, True]


def test_fallback_records_switch_event(runtime) -> None:
    result = execute_chain(kind="market_data", call=_call_fail_first({}), **runtime)
    assert result.event is not None
    event = result.event
    assert (event.src, event.dst) == ("eastmoney", "tencent")
    assert event.direction == "failover"
    assert event.reason == "primary_failed_fallback"
    assert event.persisted is True

    stored = runtime["events"].read_file()
    assert len(stored) == 1
    assert stored[0]["dst"] == "tencent"
    assert stored[0]["attempts"][0]["ok"] is False


def test_first_call_fallback_still_emits_event(runtime) -> None:
    """进程内第一次调用就降级时，没有"上次成功通道"可对比——仍必须留痕。

    否则新启动的进程在完全降级状态下运行，事件日志会是空的，
    事后完全看不出"那天数据其实来自备用源"。
    """
    result = execute_chain(kind="market_data", call=_call_fail_first({}), **runtime)
    assert result.event is not None


def test_recovery_emits_recover_event(runtime) -> None:
    """主源恢复后自动回切，并记录方向为 recover。"""
    execute_chain(kind="market_data", call=_call_fail_first({}), **runtime)
    second = execute_chain(kind="market_data", call=lambda d: f"ok-{d.name}", **runtime)

    assert second.channel == "eastmoney"
    assert second.event is not None
    assert second.event.direction == "recover"
    assert (second.event.src, second.event.dst) == ("tencent", "eastmoney")


def test_all_fail_raises_with_attempts(runtime) -> None:
    def _call(dependency):
        raise TimeoutError(f"{dependency.name} timeout")

    with pytest.raises(DependencyUnavailableError) as excinfo:
        execute_chain(kind="market_data", call=_call, **runtime)

    error = excinfo.value
    assert len(error.attempts) == 2
    assert [a.channel for a in error.attempts] == ["eastmoney", "tencent"]
    assert all(not a.ok for a in error.attempts)
    assert "eastmoney" in str(error) and "tencent" in str(error)


def test_all_fail_does_not_return_empty_value(runtime) -> None:
    """全部失败时绝不返回"空数据"冒充"这段时间没数据"。"""
    with pytest.raises(DependencyUnavailableError):
        execute_chain(kind="market_data", call=lambda d: (_ for _ in ()).throw(OSError("x")), **runtime)


def test_non_retryable_error_is_raised_immediately(runtime) -> None:
    """参数错误换通道也没用：应原样抛出，而不是拖成两次尝试后变成"全部不可用"。"""

    class BadParam(Exception):
        pass

    calls: list[str] = []

    def _call(dependency):
        calls.append(dependency.name)
        raise BadParam("symbol 非法")

    with pytest.raises(BadParam):
        execute_chain(
            kind="market_data",
            call=_call,
            is_retryable=lambda exc: not isinstance(exc, BadParam),
            **runtime,
        )
    assert calls == ["eastmoney"]


def test_pinned_channel_skips_fallback(runtime) -> None:
    """锁定到备用通道：只用它，且不产出切换事件（没有发生切换）。"""
    result = execute_chain(
        kind="market_data",
        call=lambda d: f"ok-{d.name}",
        pinned="tencent",
        **runtime,
    )
    assert result.channel == "tencent"
    assert result.pinned is True
    assert result.event is None
    assert [a.channel for a in result.attempts] == ["tencent"]


def test_registry_lock_is_honoured(runtime) -> None:
    registry = build_registry(lock={"market_data": "tencent"})
    kwargs = {k: v for k, v in runtime.items() if k != "registry"}
    result = execute_chain(kind="market_data", call=lambda d: d.name, registry=registry, **kwargs)
    assert result.channel == "tencent"
    assert result.pinned is True


def test_pinned_unknown_channel_raises(runtime) -> None:
    with pytest.raises(DependencyConfigError, match="锁定通道不存在"):
        execute_chain(kind="market_data", call=lambda d: d.name, pinned="sina", **runtime)


def test_unknown_kind_raises(runtime) -> None:
    with pytest.raises(DependencyConfigError):
        execute_chain(kind="notification", call=lambda d: d.name, **runtime)


def test_latency_recorded_for_success_and_failure(runtime) -> None:
    execute_chain(kind="market_data", call=_call_fail_first({}), **runtime)
    samples = runtime["archive"].samples()
    by_channel = {s["channel"]: s for s in samples}
    assert set(by_channel) == {"eastmoney", "tencent"}
    assert by_channel["eastmoney"]["ok"] is False
    assert by_channel["tencent"]["ok"] is True
    assert by_channel["eastmoney"]["latency_ms"] >= 0


def test_state_is_per_instance(tmp_path: Path) -> None:
    """两个独立状态互不影响：一个的降级不应让另一个产出"回切"事件。"""
    registry = build_registry(lock={})
    archive = LatencyArchive(None)
    events_a = EventLog(None)
    events_b = EventLog(None)

    execute_chain(kind="market_data", call=_call_fail_first({}), registry=registry,
                  archive=archive, events=events_a, state=ChainState())
    result_b = execute_chain(kind="market_data", call=lambda d: d.name, registry=registry,
                             archive=archive, events=events_b, state=ChainState())
    assert result_b.event is None


def test_custom_dependency_order_is_respected(tmp_path: Path) -> None:
    deps = (
        Dependency(kind="market_data", name="backup", priority=1),
        Dependency(kind="market_data", name="primary", priority=2),
    )
    registry = Registry(dependencies=deps, lock={})
    result = execute_chain(
        kind="market_data",
        call=lambda d: d.name,
        registry=registry,
        archive=LatencyArchive(None),
        events=EventLog(None),
        state=ChainState(),
    )
    assert result.channel == "backup", "顺序由 priority 决定，不由声明顺序决定"
