"""JSON 序列化 / 反序列化测试：往返、类型恢复、标准 JSON、非标准值处理。"""

from __future__ import annotations

import json
from decimal import Decimal

import pandas as pd
import pytest

from deepquant.backtest.config import ExecutionPrice
from deepquant.backtest.models import Fees, OrderStatus, Position, ValuationStatus
from deepquant.market.rules import MarketRules, Side
from deepquant.performance.json_encoding import (
    SerializationError,
    from_jsonable,
    to_jsonable,
)
from deepquant.performance.metrics import PerformanceMetrics, compute_performance
from deepquant.performance.serialize import (
    result_from_dict,
    result_from_json,
    result_to_dict,
    result_to_json,
)
from helpers import build_result, engine_result, flat_states, make_state, missing_state


def _with_metrics(result):
    result.performance = compute_performance(result)
    return result


# ===================================================================== #
# 往返序列化
# ===================================================================== #


def test_round_trip_full_result_is_stable():
    result = _with_metrics(engine_result())
    restored = result_from_json(result_to_json(result))
    assert result_to_dict(restored) == result_to_dict(result)
    assert restored.orders == result.orders
    assert restored.fills == result.fills
    assert restored.account_states == result.account_states
    assert restored.performance == result.performance
    assert restored.is_complete == result.is_complete
    assert restored.incomplete_reasons == result.incomplete_reasons


def test_round_trip_restores_types():
    result = _with_metrics(engine_result())
    restored = result_from_json(result_to_json(result))
    assert isinstance(restored.config.exec_price_rule, ExecutionPrice)
    assert isinstance(restored.config.market_rules, MarketRules)
    assert isinstance(restored.orders[0].side, Side)
    assert isinstance(restored.orders[0].status, OrderStatus)
    assert isinstance(restored.orders[0].signal_date, pd.Timestamp)
    assert isinstance(restored.fills[0].fees, Fees)
    assert isinstance(restored.account_states[0].position, Position)
    assert isinstance(restored.account_states[0].valuation_status, ValuationStatus)
    assert isinstance(restored.performance, PerformanceMetrics)
    assert restored.schema_version == result.schema_version == "0.1.0"
    assert restored.engine_version == result.engine_version


def test_round_trip_preserves_configured_market_rules():
    result = _with_metrics(
        build_result(states=flat_states([1000.0, 1000.0]), initial=1000.0)
    )
    restored = result_from_json(result_to_json(result))
    assert restored.config.market_rules.price_limit is None
    assert restored.config.initial_cash == 1000.0


# ===================================================================== #
# 标准 JSON 与非标准数值
# ===================================================================== #


def test_json_is_standard_parseable_without_nonfinite():
    text = result_to_json(engine_result())
    parsed = json.loads(text)
    assert parsed["schema_version"] == "0.1.0"
    # 标准 JSON 解析器可读取；且文本中不含 NaN / Infinity / -Infinity 等
    # 非标准「数值字面量」（parse_constant 在这些常量出现时报错）。
    def _reject_constant(token: str):
        raise AssertionError(f"出现非标准 JSON 数值常量：{token}")

    reparsed = json.loads(text, parse_constant=_reject_constant)
    assert reparsed["schema_version"] == "0.1.0"


def test_enums_serialized_as_stable_values():
    data = result_to_dict(engine_result())
    assert data["config"]["exec_price_rule"] == "next_open"
    assert data["orders"][0]["side"] in {"BUY", "SELL"}
    assert data["orders"][0]["status"] in {"filled", "rejected"}


def test_timestamps_serialized_as_iso_strings():
    data = result_to_dict(engine_result())
    assert data["orders"][0]["signal_date"].startswith("2024-01-")
    assert data["period"]["start"].startswith("2024-01-")


def test_nested_fees_structure():
    data = result_to_dict(engine_result())
    fees = data["fills"][0]["fees"]
    assert set(fees) == {"commission", "stamp_duty", "transfer_fee"}


def test_none_serialized_as_null():
    result = build_result(
        states=[
            make_state("2024-01-02", 1000.0),
            missing_state("2024-01-03", cash=500.0, quantity=100),
        ],
        initial=1000.0,
    )
    _with_metrics(result)
    data = result_to_dict(result)
    assert data["account_states"][1]["total_equity"] is None
    assert data["performance"]["daily_returns"][1]["value"] is None
    text = result_to_json(result)
    assert json.loads(text)["account_states"][1]["total_equity"] is None


def test_nan_and_infinity_rejected():
    with pytest.raises(SerializationError, match="非标准"):
        to_jsonable(float("nan"))
    with pytest.raises(SerializationError, match="非标准"):
        to_jsonable(float("inf"))
    with pytest.raises(SerializationError):
        to_jsonable(float("-inf"))


def test_unsupported_type_rejected():
    with pytest.raises(SerializationError, match="不支持的类型"):
        to_jsonable(object())


def test_non_string_dict_key_rejected():
    with pytest.raises(SerializationError, match="字典键"):
        to_jsonable({1: "a"})


# ===================================================================== #
# Decimal / 泛型编解码
# ===================================================================== #


def test_decimal_is_tagged_and_lossless():
    encoded = to_jsonable(Decimal("123.45"))
    assert encoded == {"__type__": "decimal", "value": "123.45"}
    assert from_jsonable(encoded) == Decimal("123.45")


def test_decimal_inside_nested_structure():
    encoded = to_jsonable({"amount": Decimal("0.10"), "items": [Decimal("1.005")]})
    assert encoded["amount"] == {"__type__": "decimal", "value": "0.10"}
    restored = from_jsonable(encoded)
    assert restored["amount"] == Decimal("0.10")
    assert restored["items"][0] == Decimal("1.005")


# ===================================================================== #
# 错误报告（字段路径 / 版本 / 枚举）
# ===================================================================== #


def test_missing_field_reports_path():
    data = result_to_dict(engine_result())
    del data["config"]["symbol"]
    with pytest.raises(SerializationError, match="symbol"):
        result_from_dict(data)


def test_unsupported_schema_version_rejected():
    data = result_to_dict(engine_result())
    data["schema_version"] = "9.9.9"
    with pytest.raises(SerializationError, match="schema_version"):
        result_from_dict(data)


def test_invalid_enum_value_rejected():
    data = result_to_dict(engine_result())
    data["orders"][0]["side"] = "HOLD"
    with pytest.raises(SerializationError, match="side"):
        result_from_dict(data)


def test_invalid_json_text_rejected():
    with pytest.raises(SerializationError, match="无法解析 JSON"):
        result_from_json("{not json}")
