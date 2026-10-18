"""backtest/models.py 契约与不变量补充测试（A16#2）。

既有 `test_models.py` 已覆盖创建、正常估值、多数一致性分支，但仍有 **17 行契约守护分支**
未被触达（coverage 实测 242 stmts / 17 miss）。本文件只补这些**守护分支**，互为补充、不重复：

- Order：status / reason 非法（__post_init__ 守卫）；planned_exec_date < order_date 时序不变量；
- Fill：side 非法；
- AccountState：position / valuation_status 非法（类型守卫）；无持仓却 market_value≠0 /
  total_equity≠cash；OK 下 close 非法；OK 下 total_equity 与 cash+mv 不符；
  MISSING_CLOSE 下 close 非空；
- find_consistency_issues：订单标记 FILLED 但记录的 fill_id 与实际成交不一致；
- BacktestResult：config / schema_version / engine_version 非法；strategy / dataset 非 dict。

全部为**语义断言**：除「抛 ValueError」外，还校验异常说明命中具体不变量
（如「无持仓时 market_value 必须为 0」「total_equity 必须等于 cash + market_value」）。

纯加法测试，未改 src/ 一行（R7 只增不减），不碰 backtest/engine.py。
"""

import pandas as pd
import pytest

from deepquant.backtest.config import BacktestConfig
from deepquant.backtest.models import (
    AccountState,
    BacktestResult,
    Fill,
    Order,
    OrderStatus,
    Position,
    ValuationStatus,
    find_consistency_issues,
)
from deepquant.market.rules import Side

D1 = pd.Timestamp("2024-01-02")
D2 = pd.Timestamp("2024-01-03")
D3 = pd.Timestamp("2024-01-04")


def _order(**overrides) -> Order:
    kwargs = dict(
        order_id="o1",
        symbol="600000",
        side=Side.BUY,
        signal_date=D1,
        order_date=D1,
        planned_exec_date=D2,
        quantity=100,
    )
    kwargs.update(overrides)
    return Order(**kwargs)


def _fill(**overrides) -> Fill:
    kwargs = dict(
        fill_id="f1",
        order_id="o1",
        symbol="600000",
        side=Side.BUY,
        fill_date=D2,
        price=10.0,
        quantity=100,
    )
    kwargs.update(overrides)
    return Fill(**kwargs)


def _config() -> BacktestConfig:
    return BacktestConfig(symbol="600000", initial_cash=100000.0)


# ---------- Order 类型守卫 ----------


@pytest.mark.parametrize("bad_symbol", ["", "   ", None, 7, 600000])
def test_order_rejects_non_str_symbol(bad_symbol):
    # models.py:79 —— symbol 必须是非空字符串
    with pytest.raises(ValueError, match="symbol 必须是非空字符串"):
        _order(symbol=bad_symbol)


@pytest.mark.parametrize("bad_status", ["filled", 1, None, OrderStatus])
def test_order_rejects_non_orderstatus(bad_status):
    # models.py:125 —— status 必须是 OrderStatus 实例
    with pytest.raises(ValueError, match="status 必须是 OrderStatus"):
        _order(status=bad_status)


@pytest.mark.parametrize("bad_reason", [0, [], {}])
def test_order_rejects_non_str_reason(bad_reason):
    # models.py:129 —— reason 必须是字符串或 None
    with pytest.raises(ValueError, match="reason 必须是字符串或 None"):
        _order(reason=bad_reason)


def test_order_rejects_planned_before_order_date():
    # models.py:137 —— 时序不变量：不得用比下单日更早的价成交。
    # 构造 planned > signal 但 < order_date，确保只命中 137 这一支。
    with pytest.raises(ValueError, match="不能早于 order_date"):
        _order(signal_date=D1, order_date=D3, planned_exec_date=D2)


# ---------- Fill 类型守卫 ----------


def test_fill_rejects_non_side_value():
    # models.py:160 —— side 必须是 Side
    with pytest.raises(ValueError, match="side 必须是 Side"):
        _fill(side="SELL")


# ---------- AccountState 类型守卫 ----------


def test_account_state_rejects_non_position():
    # models.py:224 —— position 必须是 Position 实例
    with pytest.raises(ValueError, match="position 必须是 Position"):
        AccountState(
            date=D1,
            cash=5000.0,
            position={"symbol": "600000"},  # 不是 Position
            close=None,
            market_value=0.0,
            total_equity=5000.0,
            valuation_status=ValuationStatus.OK,
        )


def test_account_state_rejects_non_valuation_status():
    # models.py:226 —— valuation_status 必须是 ValuationStatus
    with pytest.raises(ValueError, match="valuation_status 必须是 ValuationStatus"):
        AccountState(
            date=D1,
            cash=5000.0,
            position=Position(symbol="600000", quantity=0),
            close=None,
            market_value=0.0,
            total_equity=5000.0,
            valuation_status="ok",
        )


# ---------- AccountState 无持仓估值不变量（D4 子类） ----------


def test_account_state_empty_position_rejects_nonzero_market_value():
    # models.py:238 —— 无持仓时 market_value 必须为 0
    with pytest.raises(ValueError, match="无持仓时 market_value 必须为 0"):
        AccountState(
            date=D1,
            cash=5000.0,
            position=Position(symbol="600000", quantity=0),
            close=None,
            market_value=1.0,
            total_equity=5000.0,
            valuation_status=ValuationStatus.OK,
        )


def test_account_state_empty_position_rejects_total_equity_mismatch():
    # models.py:240 —— 无持仓时 total_equity 必须等于 cash
    with pytest.raises(ValueError, match="无持仓时 total_equity 必须等于 cash"):
        AccountState(
            date=D1,
            cash=5000.0,
            position=Position(symbol="600000", quantity=0),
            close=None,
            market_value=0.0,
            total_equity=4999.0,
            valuation_status=ValuationStatus.OK,
        )


# ---------- AccountState 有持仓 OK 估值不变量 ----------


def test_account_state_ok_rejects_missing_close():
    # models.py:248 —— OK 且有持仓时 close 必须有效（不得为 None / 非数值）
    with pytest.raises(ValueError, match="估值状态为 OK 且有持仓时，close 必须有效"):
        AccountState(
            date=D1,
            cash=4000.0,
            position=Position(symbol="600000", quantity=100),
            close=None,
            market_value=None,
            total_equity=None,
            valuation_status=ValuationStatus.OK,
        )


def test_account_state_ok_rejects_total_equity_mismatch():
    # models.py:257 —— OK 下 total_equity 必须等于 cash + market_value
    # market_value 正确（100*10），但 total_equity 故意写错，专门命中 257。
    with pytest.raises(ValueError, match="total_equity.*必须等于 cash"):
        AccountState(
            date=D1,
            cash=4000.0,
            position=Position(symbol="600000", quantity=100),
            close=10.0,
            market_value=1000.0,
            total_equity=4999.0,
            valuation_status=ValuationStatus.OK,
        )


# ---------- AccountState 有持仓 MISSING_CLOSE 估值不变量 ----------


def test_account_state_missing_close_rejects_non_null_close():
    # models.py:264 —— MISSING_CLOSE 下 close 必须为 None（不得补一个价）
    with pytest.raises(ValueError, match="估值状态为 MISSING_CLOSE 时 close 必须为 None"):
        AccountState(
            date=D1,
            cash=4000.0,
            position=Position(symbol="600000", quantity=100),
            close=10.0,
            market_value=None,
            total_equity=None,
            valuation_status=ValuationStatus.MISSING_CLOSE,
        )


# ---------- 订单 / 成交 一致性：fill_id 不一致 ----------


def test_consistency_detects_filled_order_fill_id_mismatch():
    # models.py:322 —— 订单标 FILLED 且记录了 fill_id，但实际成交的 fill_id 与之不符。
    order = _order(status=OrderStatus.FILLED, fill_id="f1")
    fill = _fill(fill_id="f9")  # 实际成交 fill_id 与订单记录不一致
    issues = find_consistency_issues([order], [fill])
    assert any("fill_id" in issue and "不一致" in issue for issue in issues)


# ---------- BacktestResult 类型守卫 ----------


def test_backtest_result_rejects_non_config():
    # models.py:388 —— config 必须是 BacktestConfig 实例
    with pytest.raises(ValueError, match="config 必须是 BacktestConfig 实例"):
        BacktestResult(config="not-a-config")


@pytest.mark.parametrize("bad", ["", "   ", 1, None])
def test_backtest_result_rejects_bad_schema_version(bad):
    # models.py:390 —— schema_version 必须是非空字符串
    with pytest.raises(ValueError, match="schema_version 必须是非空字符串"):
        BacktestResult(config=_config(), schema_version=bad)


@pytest.mark.parametrize("bad", ["", "   ", 1, None])
def test_backtest_result_rejects_bad_engine_version(bad):
    # models.py:392 —— engine_version 必须是非空字符串
    with pytest.raises(ValueError, match="engine_version 必须是非空字符串"):
        BacktestResult(config=_config(), engine_version=bad)


@pytest.mark.parametrize("name", ["strategy", "dataset"])
def test_backtest_result_rejects_non_dict_meta(name):
    # models.py:396 —— strategy / dataset 必须是 dict 或 None
    with pytest.raises(ValueError, match=f"{name} 必须是 dict 或 None"):
        BacktestResult(config=_config(), **{name: "not-a-dict"})
