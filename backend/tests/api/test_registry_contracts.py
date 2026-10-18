"""api/registry.py 契约与不变量测试（A16#3）。

补充 HTTP 级 ``test_strategies_api.py`` 未覆盖的守卫分支。覆盖 git 基线缺失的 10 行：
``_check_param_type`` 的 number / boolean / string 类型守护（85–93），
以及 ``build_strategy`` 的 params 非 dict 守卫（107）。

所有断言均为**语义级**：不仅断言「抛了异常」，还断言——
1. 异常类型精确为 ``StrategyParamsError``（422 域错误，不得泄漏底层 ValueError/TypeError）；
2. 错误消息精确点名被违反的**具体类型契约**（数值 / 布尔值 / 字符串 / 必须是对象）。
另含对照项：合法 dict 参数仍能正常实例化，证明守卫不会被误伤（不变量不被过度收紧）。

纯加法，未改 ``src/`` 一行。
"""

from __future__ import annotations

import pytest

from deepquant.api.errors import StrategyParamsError
from deepquant.api.registry import (
    StrategyParamSpec,
    _check_param_type,
    build_strategy,
)


# ---- _check_param_type：number 类型契约（lines 85–87）----

def test_number_rejects_string() -> None:
    spec = StrategyParamSpec(type="number", default=0.0, description="x")
    with pytest.raises(StrategyParamsError) as exc:
        _check_param_type(spec, "3.5", "x")
    assert exc.value.status_code == 422
    assert "数值" in str(exc.value)


def test_number_rejects_bool() -> None:
    # bool 在 Python 中是 int 的子类，必须被显式排除，否则 True 会被当数值
    spec = StrategyParamSpec(type="number", default=0.0, description="x")
    with pytest.raises(StrategyParamsError) as exc:
        _check_param_type(spec, True, "x")
    assert "数值" in str(exc.value)


def test_number_accepts_int_and_float() -> None:
    spec = StrategyParamSpec(type="number", default=0.0, description="x")
    _check_param_type(spec, 3, "x")    # 整数也算数值
    _check_param_type(spec, 3.5, "x")  # 浮点正常通过


# ---- _check_param_type：boolean 类型契约（lines 88–90）----

def test_boolean_rejects_int() -> None:
    spec = StrategyParamSpec(type="boolean", default=True, description="x")
    with pytest.raises(StrategyParamsError) as exc:
        _check_param_type(spec, 1, "x")
    assert "布尔值" in str(exc.value)


def test_boolean_accepts_bool() -> None:
    spec = StrategyParamSpec(type="boolean", default=True, description="x")
    _check_param_type(spec, True, "x")
    _check_param_type(spec, False, "x")


# ---- _check_param_type：string 类型契约（lines 91–93）----

def test_string_rejects_int() -> None:
    spec = StrategyParamSpec(type="string", default="", description="x")
    with pytest.raises(StrategyParamsError) as exc:
        _check_param_type(spec, 123, "x")
    assert "字符串" in str(exc.value)


def test_string_accepts_str() -> None:
    spec = StrategyParamSpec(type="string", default="", description="x")
    _check_param_type(spec, "ma_cross", "x")


# ---- build_strategy：params 必须为 dict（line 107）----

def test_build_strategy_rejects_string_params() -> None:
    with pytest.raises(StrategyParamsError) as exc:
        build_strategy("ma_cross", "not_a_dict")
    assert exc.value.status_code == 422
    assert "必须是对象" in str(exc.value)


def test_build_strategy_rejects_int_params() -> None:
    with pytest.raises(StrategyParamsError) as exc:
        build_strategy("ma_cross", 123)
    assert "必须是对象" in str(exc.value)


# ---- 对照：合法 dict 参数仍能正常实例化（守卫不被误伤）----

def test_build_strategy_accepts_valid_dict() -> None:
    strategy = build_strategy("ma_cross", {"fast_window": 5, "slow_window": 20})
    assert strategy is not None
    assert strategy.params["fast_window"] == 5
    assert strategy.params["slow_window"] == 20
