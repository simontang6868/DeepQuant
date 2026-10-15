"""GET /strategies 接口测试。

覆盖：正常策略列表、返回结构稳定、参数描述与真实策略一致、
不暴露内部/测试策略、未注册策略不能被执行。
"""

from __future__ import annotations

from deepquant.api.registry import STRATEGY_REGISTRY
from deepquant.strategies.ma_cross import MaCrossStrategy


def test_returns_ma_cross(client) -> None:
    """策略列表包含且仅包含注册表中的策略。"""
    response = client.get("/strategies")
    assert response.status_code == 200
    strategies = response.json()["strategies"]
    assert [s["strategy_id"] for s in strategies] == ["ma_cross"]


def test_structure_stable(client) -> None:
    """返回结构稳定：字段集合固定，排序确定。"""
    body = client.get("/strategies").json()
    assert set(body.keys()) == {"strategies"}
    entry = body["strategies"][0]
    assert set(entry.keys()) == {
        "strategy_id",
        "display_name",
        "version",
        "description",
        "params",
    }
    for param in entry["params"].values():
        assert set(param.keys()) == {"type", "default", "description", "minimum"}
    # 再次请求结构一致
    assert client.get("/strategies").json() == body


def test_params_match_real_strategy(client) -> None:
    """参数描述与真实策略的默认值、版本一致（不得臆造）。"""
    entry = client.get("/strategies").json()["strategies"][0]
    real = MaCrossStrategy()
    assert entry["version"] == real.version
    assert entry["strategy_id"] == real.name
    assert entry["params"]["fast_window"]["type"] == "integer"
    assert entry["params"]["fast_window"]["default"] == real.params["fast_window"]
    assert entry["params"]["slow_window"]["default"] == real.params["slow_window"]
    # 约束描述与策略真实校验规则一致
    assert entry["params"]["fast_window"]["minimum"] == 1
    assert "大于 fast_window" in entry["params"]["slow_window"]["description"]


def test_no_internal_or_test_strategies_exposed(client) -> None:
    """不会暴露注册表之外的策略（测试策略 / 内部实现类）。"""
    strategies = client.get("/strategies").json()["strategies"]
    assert len(strategies) == len(STRATEGY_REGISTRY)
    for s in strategies:
        assert s["strategy_id"] in STRATEGY_REGISTRY
        assert "test" not in s["strategy_id"].lower()


def test_unregistered_strategy_cannot_be_executed(client_with_dataset, valid_backtest_payload) -> None:
    """未注册策略：POST /backtests 返回 404，不会反射或动态导入。"""
    payload = {**valid_backtest_payload, "strategy_id": "rsi_not_registered"}
    response = client_with_dataset.post("/backtests", json=payload)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "strategy_not_found"


def test_module_path_cannot_be_used_as_strategy(client_with_dataset, valid_backtest_payload) -> None:
    """客户端传 Python 模块路径/类名：仅被视为未注册标识符，返回 404。"""
    payload = {
        **valid_backtest_payload,
        "strategy_id": "deepquant.strategies.ma_cross.MaCrossStrategy",
    }
    response = client_with_dataset.post("/backtests", json=payload)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "strategy_not_found"
