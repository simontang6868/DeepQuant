"""能力注册表：清单自洽、查询语义、重复登记检测。"""

from __future__ import annotations

import pytest

from deepquant.capabilities.registry import (
    CAPABILITIES,
    all_capabilities,
    by_operation,
    by_route,
    get,
    http_capabilities,
    service_capabilities,
    validate_registry,
)
from deepquant.capabilities.spec import CapabilityContractError, CapabilitySpec


def _spec(**overrides) -> CapabilitySpec:
    base = dict(
        cap_id="demo.run",
        title="演示",
        version="1.0.0",
        summary="演示能力",
        transport="http",
        operation_id="demo.run",
        method="POST",
        path="/demo",
        preconditions=("identity",),
        risk_tags=("mutating",),
    )
    base.update(overrides)
    return CapabilitySpec(**base)  # type: ignore[arg-type]


def test_registry_is_self_consistent() -> None:
    validate_registry()


def test_registry_covers_six_core_capabilities() -> None:
    ids = [c.cap_id for c in all_capabilities()]
    assert ids == [
        "audit.replay",
        "backtest.compare",
        "backtest.run",
        "dataset.list",
        "portfolio.backtest",
        "strategy.list",
    ]


def test_sorted_and_deterministic() -> None:
    assert [c.cap_id for c in all_capabilities()] == sorted(c.cap_id for c in CAPABILITIES)


def test_http_and_service_split() -> None:
    assert len(http_capabilities()) == 5
    assert [c.cap_id for c in service_capabilities()] == ["audit.replay"]


def test_lookup_by_id() -> None:
    assert get("backtest.run") is not None
    assert get("nope.nope") is None


def test_lookup_by_route_is_exact() -> None:
    assert by_route("POST", "/backtests").cap_id == "backtest.run"  # type: ignore[union-attr]
    assert by_route("post", "/backtests/").cap_id == "backtest.run"  # type: ignore[union-attr]
    # 带路径参数的接口不得被前缀匹配成"执行回测"：
    # 把"查询历史"记成"执行回测"，工件与授权都会跟着错
    assert by_route("GET", "/backtests/{run_id}") is None
    assert by_route("POST", "/backtests/compare").cap_id == "backtest.compare"  # type: ignore[union-attr]


def test_lookup_by_route_rejects_method_mismatch() -> None:
    assert by_route("GET", "/backtests") is None


def test_lookup_by_operation() -> None:
    assert by_operation("portfolio.backtest").cap_id == "portfolio.backtest"  # type: ignore[union-attr]
    assert by_operation("order.cancel") is None


# --------------------------------------------------------------------------- #
# 重复登记：后写入者会静默覆盖前一条的授权与禁用状态
# --------------------------------------------------------------------------- #


def test_duplicate_cap_id_rejected() -> None:
    with pytest.raises(CapabilityContractError, match="cap_id 重复"):
        validate_registry((_spec(cap_id="a.b"), _spec(cap_id="a.b", path="/other")))


def test_duplicate_route_rejected() -> None:
    with pytest.raises(CapabilityContractError, match="路由重复"):
        validate_registry((_spec(cap_id="a.b"), _spec(cap_id="c.d", operation_id="c.d")))


def test_duplicate_operation_rejected() -> None:
    with pytest.raises(CapabilityContractError, match="operation_id 重复"):
        validate_registry((_spec(cap_id="a.b"), _spec(cap_id="c.d", path="/other")))


def test_missing_successor_rejected() -> None:
    with pytest.raises(CapabilityContractError, match="successor"):
        validate_registry((_spec(deprecated=True, successor="ghost.cap"),))


def test_invalid_entry_propagates() -> None:
    with pytest.raises(CapabilityContractError, match="cap_id"):
        validate_registry((_spec(cap_id="Bad"),))
