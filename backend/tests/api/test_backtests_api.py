"""POST /backtests 接口测试。

覆盖：合法请求、非法字段、数据集/策略不存在、策略参数非法、日期范围非法、
数据质量错误、JSON 可解析且无 NaN/Infinity、绩效与可复现性、确定性、
不修改原始数据、错误响应不泄露绝对路径。
"""

from __future__ import annotations

import hashlib
import json

import pandas as pd

from conftest import ingest_dataset

RESULT_KEYS = {
    "schema_version",
    "engine_version",
    "config",
    "period",
    "strategy",
    "dataset",
    "orders",
    "fills",
    "account_states",
    "data_quality_issues",
    "limitations",
    "is_complete",
    "incomplete_reasons",
    "performance",
    "reproducibility",
}


def test_valid_request_returns_result(client_with_dataset, valid_backtest_payload) -> None:
    """合法请求：200，外层 api 元信息 + 既有结果 JSON 契约完整。"""
    response = client_with_dataset.post("/backtests", json=valid_backtest_payload)
    assert response.status_code == 200
    body = response.json()

    assert body["api"]["name"] == "deepquant-api"
    assert isinstance(body["api"]["version"], str)

    result = body["result"]
    assert RESULT_KEYS <= set(result.keys())
    assert result["schema_version"] == "0.1.0"
    assert result["config"]["symbol"] == "TEST01"
    assert result["config"]["initial_cash"] == 100000
    assert result["strategy"]["name"] == "ma_cross"
    assert result["strategy"]["params"] == {"fast_window": 5, "slow_window": 20}
    assert result["dataset"]["digest"].startswith("sha256:")
    assert isinstance(result["orders"], list)
    assert isinstance(result["account_states"], list) and len(result["account_states"]) == 60
    assert result["performance"] is not None
    assert result["reproducibility"]["inputs_identifiable"] is True


def test_fee_source_marked_as_simulation(client_with_dataset, valid_backtest_payload) -> None:
    """响应必须标明费用参数来源为模拟占位，不得描述为真实费率。"""
    body = client_with_dataset.post("/backtests", json=valid_backtest_payload).json()
    assert body["result"]["config"]["fee_source"] == "simulation_placeholder"
    assert any("模拟" in a for a in body["result"]["config"]["assumptions"])


def test_response_is_standard_json_without_nan_or_inf(
    client_with_dataset, valid_backtest_payload
) -> None:
    """响应可被标准 JSON 解析，且不含 NaN / Infinity 字面量。

    注意：结果文档的 canonicalization 说明里存在字符串 "NaN"（缺失值表示约定），
    这是合法的字符串值；此处用 parse_constant 精确拦截非标准 JSON 数值字面量。
    """
    def _reject_constant(value: str) -> None:
        raise AssertionError(f"响应包含非标准 JSON 常量：{value}")

    response = client_with_dataset.post("/backtests", json=valid_backtest_payload)
    assert response.status_code == 200
    json.loads(response.text, parse_constant=_reject_constant)


def test_default_strategy_params_used_when_omitted(
    client_with_dataset, valid_backtest_payload
) -> None:
    """省略 strategy_params：使用注册表默认参数。"""
    payload = dict(valid_backtest_payload)
    payload.pop("strategy_params")
    body = client_with_dataset.post("/backtests", json=payload).json()
    assert body["result"]["strategy"]["params"] == {"fast_window": 5, "slow_window": 20}


def test_unknown_request_field_rejected(client_with_dataset, valid_backtest_payload) -> None:
    """未知请求字段：422（extra=forbid）。"""
    payload = {**valid_backtest_payload, "unknown_field": 1}
    response = client_with_dataset.post("/backtests", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_invalid_initial_cash_rejected(client_with_dataset, valid_backtest_payload) -> None:
    """非法初始资金：0 / 负数 / 非数值一律 422。"""
    for bad in (0, -100, "abc"):
        payload = {**valid_backtest_payload, "initial_cash": bad}
        response = client_with_dataset.post("/backtests", json=payload)
        assert response.status_code == 422, f"initial_cash={bad!r} 应被拒绝"


def test_inverted_date_range_rejected(client_with_dataset, valid_backtest_payload) -> None:
    """start_date 晚于 end_date：422。"""
    payload = {
        **valid_backtest_payload,
        "start_date": "2020-02-01",
        "end_date": "2020-01-01",
    }
    response = client_with_dataset.post("/backtests", json=payload)
    assert response.status_code == 422


def test_negative_fee_rejected(client_with_dataset, valid_backtest_payload) -> None:
    """非法费率（负数）：422。"""
    payload = {**valid_backtest_payload, "commission_rate": -0.001}
    response = client_with_dataset.post("/backtests", json=payload)
    assert response.status_code == 422


def test_dataset_not_found(client_with_dataset, valid_backtest_payload) -> None:
    """数据集不存在：404，不静默回退到其他数据集。"""
    payload = {**valid_backtest_payload, "dataset_symbol": "NOPE"}
    response = client_with_dataset.post("/backtests", json=payload)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dataset_not_found"


def test_strategy_param_constraint_violation(client_with_dataset, valid_backtest_payload) -> None:
    """slow_window <= fast_window：422（由策略自身校验，不经 API 绕过）。"""
    payload = {
        **valid_backtest_payload,
        "strategy_params": {"fast_window": 20, "slow_window": 20},
    }
    response = client_with_dataset.post("/backtests", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_strategy_params"


def test_strategy_param_wrong_type(client_with_dataset, valid_backtest_payload) -> None:
    """策略参数类型错误（字符串 / 布尔）：422。"""
    for bad_params in ({"fast_window": "5"}, {"fast_window": True}):
        payload = {**valid_backtest_payload, "strategy_params": bad_params}
        response = client_with_dataset.post("/backtests", json=payload)
        assert response.status_code == 422, f"params={bad_params!r} 应被拒绝"
        assert response.json()["error"]["code"] == "invalid_strategy_params"


def test_unknown_strategy_param_rejected(client_with_dataset, valid_backtest_payload) -> None:
    """策略不支持的参数名：422，不静默忽略。"""
    payload = {
        **valid_backtest_payload,
        "strategy_params": {"fast_window": 5, "typo_window": 10},
    }
    response = client_with_dataset.post("/backtests", json=payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_strategy_params"


def test_empty_backtest_range_returns_400(client_with_dataset, valid_backtest_payload) -> None:
    """日期范围合法但区间内无行情数据：400 backtest_rejected。"""
    payload = {
        **valid_backtest_payload,
        "start_date": "2030-01-01",
        "end_date": "2030-12-31",
    }
    response = client_with_dataset.post("/backtests", json=payload)
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "backtest_rejected"


def test_data_quality_error_returns_400(client_with_dataset, data_root) -> None:
    """已登记数据文件被损坏（非法价格）：400 data_validation_failed。"""
    processed = data_root / "processed" / "TEST01.daily.parquet"
    df = pd.read_parquet(processed)
    df.loc[5, "close"] = -1.0  # 注入 ERROR 级数据质量问题
    df.to_parquet(processed, index=False)

    response = client_with_dataset.post(
        "/backtests",
        json={
            "dataset_symbol": "TEST01",
            "strategy_id": "ma_cross",
            "initial_cash": 100000,
        },
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "data_validation_failed"


def test_deterministic_repeat_execution(client_with_dataset, valid_backtest_payload) -> None:
    """相同输入重复执行：响应完全一致（确定性）。"""
    first = client_with_dataset.post("/backtests", json=valid_backtest_payload)
    second = client_with_dataset.post("/backtests", json=valid_backtest_payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()


def test_api_does_not_modify_dataset(client_with_dataset, data_root, valid_backtest_payload) -> None:
    """API 执行不得修改或持久化改写原始数据文件。"""
    processed = data_root / "processed" / "TEST01.daily.parquet"
    raw = data_root / "raw" / "TEST01.raw.csv"
    before = (hashlib.sha256(processed.read_bytes()).hexdigest(),
              hashlib.sha256(raw.read_bytes()).hexdigest())

    response = client_with_dataset.post("/backtests", json=valid_backtest_payload)
    assert response.status_code == 200

    after = (hashlib.sha256(processed.read_bytes()).hexdigest(),
             hashlib.sha256(raw.read_bytes()).hexdigest())
    assert before == after


def test_error_response_does_not_leak_absolute_path(
    client_with_dataset, data_root, valid_backtest_payload
) -> None:
    """错误响应（含数据损坏场景）不得包含本机绝对路径。"""
    processed = data_root / "processed" / "TEST01.daily.parquet"
    df = pd.read_parquet(processed)
    df.loc[5, "close"] = -1.0
    df.to_parquet(processed, index=False)

    response = client_with_dataset.post("/backtests", json=valid_backtest_payload)
    assert response.status_code == 400
    assert str(data_root) not in response.text


def test_unavailable_dataset_returns_404(client, data_root, tmp_path, valid_backtest_payload) -> None:
    """元数据存在但数据文件缺失：404 dataset_unavailable。"""
    ingest_dataset(data_root, tmp_path, "TEST01")
    (data_root / "processed" / "TEST01.daily.parquet").unlink()

    response = client.post("/backtests", json=valid_backtest_payload)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "dataset_unavailable"
